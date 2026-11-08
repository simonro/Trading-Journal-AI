"""Read and edit the journal's stop loss, without a separate stop history."""
from .assignments import columns


def journal_stop(conn, row):
    row = dict(row)
    analysis_cols = columns(conn, "trade_analysis")
    if {"stop_loss", "trade_group"} <= analysis_cols:
        if "account_id" in analysis_cols and row.get("account_id") is not None:
            analysis = conn.execute("SELECT stop_loss FROM trade_analysis WHERE trade_group=? AND account_id=?",
                                    (row["trade_group"], row["account_id"])).fetchone()
        else:
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
    # Keyed by (trade_group, account_id): names repeat across accounts.
    with conn:
        conn.execute("""INSERT INTO trade_analysis(trade_group,account_id,ticker,date,stop_loss)
            VALUES(?,?,?,?,?) ON CONFLICT(trade_group,account_id) DO UPDATE SET stop_loss=excluded.stop_loss""",
            (row["trade_group"], row["account_id"], row["ticker"], row["date"], price))
