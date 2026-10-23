"""Single-state minute replay with protective stops before competing exits."""
from dataclasses import dataclass, replace, asdict
from datetime import timedelta
from .models import MINUTE, NY, iso
from .calendar import trade_session
from .eligibility import reasons
from .indicators import prepare
from .rules import Costs
from .scaling import scaling_details

EPS = 1e-9


@dataclass(frozen=True)
class Fill:
    ts: str
    price: float
    quantity: float
    fee: float
    reason: str
    timing: str


@dataclass(frozen=True)
class State:
    remaining: float
    stop: float
    done: tuple = ()
    steps: tuple = ()
    pnl: float = 0
    fills: tuple = ()
    activated_at: str | None = None


def fill(state, trade, costs, qty, price, reason, ts, market=True, timing="within_minute"):
    qty = min(qty, state.remaining)
    if qty <= EPS:
        return state
    price = price*(1-trade.direction*costs.slippage_bps/10000) if market else price
    fee = costs.per_fill + costs.per_share*qty
    event = Fill(iso(ts), price, qty, fee, reason, timing)
    return replace(state, remaining=max(0, state.remaining-qty),
                   pnl=state.pnl+trade.direction*(price-trade.entry)*qty-fee,
                   fills=state.fills+(event,))


def tighten(state, candidate, direction):
    return replace(state, stop=max(state.stop, candidate) if direction == 1 else min(state.stop, candidate))


def quantity(trade, rule, i):
    # Integer positions use whole shares; the final tranche owns the residual.
    sizes = [trade.quantity*t.fraction for t in rule.tranches[:-1]]
    if float(trade.quantity).is_integer():
        sizes = [int(q + EPS) for q in sizes]
    sizes.append(trade.quantity-sum(sizes))
    return sizes[i]


def bar_result(state, bar, trade, rule, costs, partial, vwap_target):
    d = trade.direction
    start = trade.boundary_price if partial else bar.open
    adverse = bar.low if d == 1 else bar.high
    favorable = bar.high if d == 1 else bar.low
    # Boundary extrema can precede the fill: count adverse touches, but only
    # credit favorable movement established by the fill or closing price.
    if partial:
        favorable = max(start, bar.close) if d == 1 else min(start, bar.close)
    if d * (start - state.stop) <= EPS:
        return fill(state, trade, costs, state.remaining, start, "stop",
                    trade.replay_at if partial else bar.ts, timing="open" if not partial else "within_minute")
    if d * (adverse - state.stop) <= EPS:
        return fill(state, trade, costs, state.remaining, state.stop, "stop", bar.ts)
    # A threshold and its tightened stop in the same minute resolve at the stop.
    # Check steps in order so a lower protective stop can fill before a later step.
    for i, step in enumerate(rule.r_steps):
        if i not in state.steps and d * (favorable - trade.entry) >= step.at_r * trade.risk - EPS:
            state = tighten(state, trade.entry + d * step.stop_r * trade.risk, d)
            state = replace(state, steps=state.steps + (i,),
                            activated_at=state.activated_at or iso(bar.ts))
            if d * (adverse - state.stop) <= EPS:
                return fill(state, trade, costs, state.remaining, state.stop, "stop", bar.ts)
    targets = []
    for i, tranche in enumerate(rule.tranches):
        if i in state.done or tranche.kind not in ("target", "vwap_touch"):
            continue
        target = vwap_target if tranche.kind == "vwap_touch" else trade.entry + d * tranche.r * trade.risk
        if target is not None:
            targets.append((d * target, i, target, tranche.kind))
    for _, i, target, kind in sorted(targets):
        if d * (favorable - target) >= rule.tick_size - EPS:
            at_open = not partial and d * (start - target) >= rule.tick_size - EPS
            state = fill(state, trade, costs, quantity(trade, rule, i), target, kind, bar.ts,
                         market=False, timing="open" if at_open else "within_minute")
            state = replace(state, done=state.done + (i,))
    return state


def simulate(trade, bars, rule, costs=None, spy_bars=None):
    costs = Costs()  # Ignore even legacy model_copy values at the engine boundary.
    errors = reasons(trade, bars, simulation=True, tick=rule.tick_size)
    if errors:
        if ("incomplete_minute_coverage_through_close" in errors
                and "not_completed_same_regular_session" not in errors
                and rule.indicator and rule.indicator.kind == "rs_rw"):
            _, _, _, missing = prepare(trade, bars, rule, spy_bars)
            if missing and missing.startswith("regular_minute_missing|"):
                errors = [missing if e == "incomplete_minute_coverage_through_close" else e for e in errors]
        return {"eligible": False, "reasons": errors, "pnl_r": None, "pnl": None, "exit_price": None}
    exits, trailing, indicators, error = prepare(trade, bars, rule, spy_bars)
    if error:
        return {"eligible": False, "reasons": [error], "pnl_r": None, "pnl": None, "exit_price": None}
    _, closing = trade_session(trade)
    initial_fee = costs.per_fill + trade.quantity*costs.per_share
    state = State(trade.quantity, trade.stop, pnl=-initial_fee)
    for b in bars:
        if not trade.replay_at.replace(second=0, microsecond=0) <= b.ts < closing:
            continue
        if state.remaining <= EPS:
            break
        vwap_target = indicators.get("vwap_targets", {}).get(b.ts.isoformat())
        partial = b.ts <= trade.replay_at < b.ts+MINUTE and (
            trade.scaled_entry or trade.replay_at != b.ts or abs(trade.entry-b.open) > EPS or
            (trade.opening_last_at is not None and trade.opening_last_at > b.ts))
        if b.ts in trailing:
            state = tighten(state, trailing[b.ts], trade.direction)
        state = bar_result(state, b, trade, rule, costs, partial, vwap_target)
        if not partial and b.ts in exits:
            state = fill(state, trade, costs, state.remaining, b.open, exits[b.ts], b.ts, timing="open")
        for i, tranche in enumerate(rule.tranches):
            due = ((tranche.kind == "minutes" and b.ts >= trade.replay_at+timedelta(minutes=tranche.minutes)) or
                   (tranche.kind == "clock" and b.ts.astimezone(NY).strftime("%H:%M") >= tranche.clock))
            if due and not partial and i not in state.done:
                state = fill(state, trade, costs, quantity(trade, rule, i), b.open, tranche.kind, b.ts, timing="open")
                state = replace(state, done=state.done+(i,))
        if b.ts+MINUTE == closing:
            fallback = any(t.kind == "clock" and i not in state.done for i, t in enumerate(rule.tranches))
            state = fill(state, trade, costs, state.remaining, b.close,
                         "session_close_clock_fallback" if fallback else "session_close", closing, timing="close")
    if state.remaining > EPS:
        return {"eligible": False, "reasons": ["unresolved_position"], "pnl_r": None, "pnl": None, "exit_price": None}
    denom = trade.risk*trade.quantity
    actual_fees = (len(trade.fills) or 2)*costs.per_fill + 2*costs.per_share*trade.quantity
    actual_pnl = trade.direction*(trade.exit-trade.entry)*trade.quantity-actual_fees
    return {"eligible": True, "reasons": [], "pnl": state.pnl,
            "pnl_r": state.pnl/denom, "delta_r": (state.pnl-actual_pnl)/denom,
            "actual_pnl_modeled_costs": actual_pnl,
            "exit_price": sum(f.price*f.quantity for f in state.fills)/trade.quantity,
            "fills": [asdict(f) for f in state.fills], "activated_at": state.activated_at,
            "scaled_entry": scaling_details(trade, bars, rule, indicators),
            "initial_fee": initial_fee, "costs": costs.model_dump(), "cost_label": costs.label,
            "indicator_values": indicators, "feed": bars[0].feed}
