from .models import MINUTE
from .eligibility import reasons


def excursions(trade, bars):
    errors = reasons(trade, bars)
    if errors:
        return {"eligible": False, "reasons": errors, "mfe": None, "mae": None,
                "mfe_pct": None, "mae_pct": None, "exit_efficiency": None}
    realized = trade.direction * (trade.exit - trade.entry)
    known = [0.0, realized]
    if trade.scaled_entry:
        known.append(trade.direction * (trade.boundary_price - trade.entry))
    possible = known.copy()
    for bar in bars:
        end = bar.ts + MINUTE
        if bar.ts >= trade.exited_at or end <= trade.replay_at:
            continue
        extrema = [trade.direction*(p-trade.entry) for p in (bar.high, bar.low)]
        possible.extend(extrema)
        boundary = trade.scaled_entry and bar.ts == trade.replay_at.replace(second=0, microsecond=0)
        if not boundary and (trade.opening_last_at or trade.replay_at) <= bar.ts and end <= (trade.closing_first_at or trade.exited_at):
            known.extend(extrema)
        else:
            if not boundary and trade.replay_at <= bar.ts < trade.exited_at:
                known.append(trade.direction*(bar.open-trade.entry))
            if trade.replay_at < end <= trade.exited_at:
                known.append(trade.direction*(bar.close-trade.entry))
    mfe = max(known)
    mae = min(possible)
    efficiency = None if mfe <= 0 else 100*realized/(max(possible) if realized >= 0 else mfe)
    return {"eligible": True, "reasons": [], "mfe": mfe, "mae": mae,
            "mfe_pct": 100*mfe/trade.entry,
            "mae_pct": 100*mae/trade.entry, "exit_efficiency": efficiency,
            "measured_from": trade.replay_at.isoformat(),
            "label": (("Synthetic trades, historical market prices; " if trade.source == "demo" else "") +
                      "Scaled entry: average price, hold after the last fill" if trade.scaled_entry else
                      "Synthetic trades, historical market prices" if trade.source == "demo" else "computed from bars")}


def persist(conn, trade, result):
    row = conn.execute("SELECT excursion_source FROM trades WHERE id=?", (trade.id,)).fetchone()
    if not result["eligible"] or trade.source == "demo" or (row and row[0] in ("legacy_synthetic", "demo")):
        return
    conn.execute("""UPDATE trades SET mfe_pct=?,mae_pct=?,exit_efficiency=?,excursion_source='computed'
        WHERE id=?""", (result["mfe_pct"], result["mae_pct"], result["exit_efficiency"], trade.id))
