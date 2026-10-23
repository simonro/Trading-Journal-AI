"""Average-entry metadata and advisory build-window touches, never rule state."""
from .models import MINUTE, iso

BUILD_WARNING = "Touched the stop/target while scaling in; not counted"


def scaling_details(trade, bars, rule=None, indicators=None):
    if not trade.scaled_entry:
        return None
    action = "BOT" if trade.direction == 1 else "SOLD"
    fills = [{"ts": iso(f[0]), "quantity": f[2], "price": f[3]}
             for f in trade.fills if f[1] == action]
    start = trade.entered_at.replace(second=0, microsecond=0)
    boundary = trade.replay_at.replace(second=0, microsecond=0)
    # Boundary extrema cannot be placed before/after the fill using minute bars.
    # Flag build-window touches independently of the stop-first replay.
    window = [b for b in bars if start <= b.ts < trade.replay_at]
    levels = []
    if trade.stop is not None and trade.risk:
        levels.append(("stop", trade.stop))
        multiples = [t.r for t in rule.tranches if t.kind == "target"] if rule else [1, 2]
        levels.extend((f"{r:g}R", trade.entry + trade.direction*r*trade.risk) for r in multiples)
    touches = []
    for bar in window:
        targets = list(levels)
        if rule and any(t.kind == "vwap_touch" for t in rule.tranches):
            value = (indicators or {}).get("vwap_targets", {}).get(iso(bar.ts))
            if value is not None:
                targets.append(("VWAP", value))
        for label, price in targets:
            if label == "stop":
                touched = bar.low <= price if trade.direction == 1 else bar.high >= price
            else:
                touched = bar.high >= price if trade.direction == 1 else bar.low <= price
            if touched and not any(t["level"] == label for t in touches):
                touches.append({"level": label, "price": price, "ts": iso(bar.ts),
                                "possible": bar.ts < trade.entered_at or bar.ts == boundary})
    return {"fills": fills, "fill_count": len(fills), "first_fill_at": iso(trade.entered_at),
            "last_fill_at": iso(trade.replay_at), "average_entry": trade.entry,
            "quantity": trade.quantity, "initial_risk_dollars": trade.risk*trade.quantity if trade.risk else None,
            "full_bar_start": iso(boundary + MINUTE), "build_touches": touches,
            "warning": BUILD_WARNING if touches else None}
