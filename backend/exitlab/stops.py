"""Read and edit the journal's stop loss, without a separate stop history."""
from .assignments import columns


def journal_stop(conn, row):
    row = dict(row)
    if {"stop_loss", "trade_group"} <= columns(conn, "trade_analysis"):
        analysis = conn.execute("SELECT stop_loss FROM trade_analysis WHERE trade_group=?",
                                (row["trade_group"],)).fetchone()
        if analysis and analysis[0] is not None:
            return analysis[0]
    for field in ("stop_loss", "stop"):
        if row.get(field) is not None:
            return row[field]
    return None


def save_stop(conn, row, price):
    # Same row and fields as the journal trade-analysis editor. Preserve other notes.
    with conn:
        conn.execute("""INSERT INTO trade_analysis(trade_group,ticker,date,stop_loss)
            VALUES(?,?,?,?) ON CONFLICT(trade_group) DO UPDATE SET stop_loss=excluded.stop_loss""",
            (row["trade_group"], row["ticker"], row["date"], price))
