import json
import math
from datetime import datetime
from .models import Trade, NY, UTC, MINUTE, continuous
from .calendar import trade_session

MISSING_PRICES = "incomplete_minute_coverage_through_close"


def from_row(row, stop=None):
    row = dict(row)
    if row["instrument_type"] != "STOCK":
        raise ValueError("not_stock")
    executions = json.loads(row["executions"])
    fills = []
    for e in executions:
        # Existing journal stores naive exchange-local date/time, not UTC.
        try:
            date = str(e["date"])
            if "/" in date:
                date = datetime.strptime(date, "%m/%d/%y").date().isoformat()
            dt = datetime.fromisoformat(date + "T" + e["time"])
            dt = (dt.replace(tzinfo=NY) if dt.tzinfo is None else dt).astimezone(UTC)
            action = e["action"].upper()
            action = {"BUY": "BOT", "SELL": "SOLD"}.get(action, action)
            qty, price = float(e["qty"]), float(e["price"])
            if action not in ("BOT", "SOLD") or not all(math.isfinite(x) and x > 0 for x in (qty, price)):
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise ValueError("invalid_execution") from None
        fills.append((dt, action, qty, price))
    # Preserve the existing newest-first, single-timestamp scalp convention.
    # Otherwise keep equal-time import ordering: never move an add before an exit.
    opening_action = "BOT" if row["side"] == "LONG" else "SOLD"
    if len({f[0] for f in fills}) == 1:
        fills.sort(key=lambda f: f[1] != opening_action)
    else:
        fills.sort(key=lambda f: f[0])
    groups = []
    for f in fills:
        if not groups or groups[-1][0][1] != f[1]:
            groups.append([])
        groups[-1].append(f)
    if len(groups) != 2:
        raise ValueError("requires_one_open_and_one_close_fill_group")
    opened, closed = groups
    start, a = opened[0][:2]
    end, b = closed[-1][:2]
    q, amount = sum(f[2] for f in opened), sum(f[2]*f[3] for f in opened)
    q2, amount2 = sum(f[2] for f in closed), sum(f[2]*f[3] for f in closed)
    direction = 1 if row["side"] == "LONG" else -1
    if a != ("BOT" if direction == 1 else "SOLD") or b == a or abs(q-q2) > 1e-8:
        raise ValueError("not_completed_constant_quantity")
    source = "demo" if row.get("excursion_source") in ("legacy_synthetic", "demo") else row.get("source", "imported")
    return Trade(row["id"], row["ticker"], direction, q, amount/q, amount2/q2,
                 start, end, stop, source, tuple(fills), opened[-1][0], closed[0][0])


def reasons(trade, bars, simulation=False, tick=0.01):
    errors = []
    if trade.direction not in (-1, 1) or not all(math.isfinite(x) and x > 0 for x in
                                               (trade.quantity, trade.entry, trade.exit)):
        return ["invalid_trade"]
    if trade.entered_at.tzinfo is None or trade.exited_at.tzinfo is None:
        return ["invalid_timestamps"]
    try:
        opening, closing = trade_session(trade)
    except ValueError as exc:
        return [str(exc)]
    if not opening <= trade.entered_at < trade.exited_at <= closing:
        errors.append("not_completed_same_regular_session")
    first = trade.entered_at.replace(second=0, microsecond=0)
    if not continuous(bars, first, closing):
        errors.append(MISSING_PRICES)
    if len({b.feed for b in bars}) > 1:
        errors.append("mixed_feeds")
    by_time = {b.ts: b for b in bars}
    # Validate every actual print, not a weighted price against the last minute.
    prints = [(f[0], f[3]) for f in trade.fills] or [(trade.entered_at, trade.entry), (trade.exited_at, trade.exit)]
    for dt, price in prints:
        stamp = dt.replace(second=0, microsecond=0)
        if dt == closing:
            stamp -= MINUTE
        bar = by_time.get(stamp)
        if bar and not bar.low-tick-1e-9 <= price <= bar.high+tick+1e-9:
            errors.append("fill_outside_bar_range")
            break
    if simulation:
        if trade.stop is None:
            errors.append("missing_initial_stop")
        elif not math.isfinite(trade.stop) or trade.stop <= 0 or trade.direction * (trade.entry-trade.stop) <= 0:
            errors.append("initial_stop_not_on_loss_side")
        elif trade.risk < 2*tick-1e-9:
            errors.append("initial_risk_under_two_ticks")
    return errors
