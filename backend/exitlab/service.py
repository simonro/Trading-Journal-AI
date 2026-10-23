import json
from .bars import cached
from .assignments import journal_rows
from .calendar import regular_windows
from .stops import journal_stop
from .eligibility import from_row, reasons
from .scaling import scaling_details


def load_entries(conn, account_id, feed):
    rows = journal_rows(conn, account_id)
    entries = []
    for row in rows:
        entry = {"id": row["id"], "trade": None, "bars": [], "journal_trade": dict(row)}
        try:
            stop = journal_stop(conn, row)
            trade = from_row(row, stop)
            windows = regular_windows(trade)
            bars = [b for start, end in windows for b in cached(conn, trade.symbol, start, end, feed)]
            spy = [b for start, end in windows for b in cached(conn, "SPY", start, end, feed)]
            entry.update(trade=trade, bars=bars, spy_bars=spy,
                         reasons=reasons(trade, bars, simulation=True))
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            entry["reasons"] = [str(exc)]
        entries.append(entry)
    return entries


def readiness(conn, account_id, feed):
    entries = load_entries(conn, account_id, feed)
    rows = []
    for e in entries:
        t = e.get("trade")
        rows.append({"trade_id": e["id"], "eligible": not e["reasons"], "reasons": e["reasons"],
                     "excursion_reasons": reasons(t, e["bars"]) if t else e["reasons"],
                     "source": t.source if t else e["journal_trade"].get("source"),
                     "stop_price": t.stop if t else None, "journal_trade": e["journal_trade"],
                     "scaled_entry": scaling_details(t, e["bars"]) if t else None})
    return {"feed": feed, "eligible": sum(r["eligible"] for r in rows),
            "excluded": sum(not r["eligible"] for r in rows),
            "missing_stops": sum(r["stop_price"] is None for r in rows),
            "trades": rows, "note": "Rule-specific warm-up is checked during replay"}
