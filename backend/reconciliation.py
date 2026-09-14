"""Reconciliation: match the journal's estimated fees against your broker's
actual fee statement (brokers often omit fees from CSV exports, so the
journal estimates them), and record dividends the CSV import doesn't carry.
"""
import csv
import io
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel

from database import get_db

router = APIRouter(prefix="/api/reconciliation")


def get_connection():
    conn = get_db()
    try:
        yield conn
    finally:
        conn.close()


def init_reconciliation_tables(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS dividends (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL REFERENCES accounts(id),
            date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            amount REAL NOT NULL,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS fee_reconciliations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER NOT NULL UNIQUE REFERENCES trades(id) ON DELETE CASCADE,
            trade_group TEXT NOT NULL,
            broker_actual_fee REAL NOT NULL,
            notes TEXT,
            reconciled_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_dividends_account_date ON dividends(account_id, date);
    """)
    conn.commit()


# ── Dividends ──────────────────────────────────────────────────────────────

class DividendBody(BaseModel):
    account_id: int
    date: str
    ticker: str
    amount: float
    notes: str | None = None


@router.get("/dividends")
def list_dividends(account_id: int | None = None, conn: sqlite3.Connection = Depends(get_connection)):
    if account_id is not None:
        rows = conn.execute("SELECT * FROM dividends WHERE account_id=? ORDER BY date DESC", (account_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM dividends ORDER BY date DESC").fetchall()
    return [dict(r) for r in rows]


@router.post("/dividends", status_code=201)
def create_dividend(body: DividendBody, conn: sqlite3.Connection = Depends(get_connection)):
    cur = conn.execute(
        "INSERT INTO dividends (account_id, date, ticker, amount, notes) VALUES (?,?,?,?,?)",
        (body.account_id, body.date, body.ticker.upper(), body.amount, body.notes),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM dividends WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)


@router.put("/dividends/{dividend_id}")
def update_dividend(dividend_id: int, body: DividendBody, conn: sqlite3.Connection = Depends(get_connection)):
    row = conn.execute("SELECT * FROM dividends WHERE id=?", (dividend_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Dividend not found")
    conn.execute(
        "UPDATE dividends SET account_id=?, date=?, ticker=?, amount=?, notes=? WHERE id=?",
        (body.account_id, body.date, body.ticker.upper(), body.amount, body.notes, dividend_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM dividends WHERE id=?", (dividend_id,)).fetchone()
    return dict(row)


@router.delete("/dividends/{dividend_id}")
def delete_dividend(dividend_id: int, conn: sqlite3.Connection = Depends(get_connection)):
    conn.execute("DELETE FROM dividends WHERE id=?", (dividend_id,))
    conn.commit()
    return {"deleted": dividend_id}


# ── Fee reconciliation ────────────────────────────────────────────────────────

class FeeReconcileBody(BaseModel):
    broker_actual_fee: float
    notes: str | None = None


def _fee_row(conn: sqlite3.Connection, trade_row: dict) -> dict:
    recon = conn.execute("SELECT * FROM fee_reconciliations WHERE trade_id=?", (trade_row["id"],)).fetchone()
    actual = recon["broker_actual_fee"] if recon else None
    estimated = trade_row["commissions"] or 0.0
    return {
        "trade_id": trade_row["id"],
        "trade_group": trade_row["trade_group"],
        "date": trade_row["date"],
        "ticker": trade_row["ticker"],
        "estimated_fee": round(estimated, 4),
        "broker_actual_fee": actual,
        "variance": round(actual - estimated, 4) if actual is not None else None,
        "notes": recon["notes"] if recon else None,
        "reconciled": recon is not None,
    }


@router.get("/trades")
def reconciliation_trades(
    account_id: int | None = None,
    start: str | None = None,
    end: str | None = None,
    unreconciled_only: bool = False,
    conn: sqlite3.Connection = Depends(get_connection),
):
    query = "SELECT * FROM trades WHERE 1=1"
    params: list = []
    if account_id is not None:
        query += " AND account_id=?"
        params.append(account_id)
    if start:
        query += " AND date>=?"
        params.append(start)
    if end:
        query += " AND date<=?"
        params.append(end)
    query += " ORDER BY date DESC"
    rows = conn.execute(query, params).fetchall()
    results = [_fee_row(conn, dict(r)) for r in rows]
    if unreconciled_only:
        results = [r for r in results if not r["reconciled"]]
    return results


@router.post("/trades/{trade_id}")
def reconcile_trade(trade_id: int, body: FeeReconcileBody, conn: sqlite3.Connection = Depends(get_connection)):
    row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Trade {trade_id} not found")
    conn.execute(
        """INSERT INTO fee_reconciliations (trade_id, trade_group, broker_actual_fee, notes)
           VALUES (?,?,?,?)
           ON CONFLICT(trade_id) DO UPDATE SET broker_actual_fee=excluded.broker_actual_fee,
             notes=excluded.notes, reconciled_at=datetime('now')""",
        (trade_id, row["trade_group"], body.broker_actual_fee, body.notes),
    )
    conn.commit()
    return _fee_row(conn, dict(row))


@router.delete("/trades/{trade_id}")
def unreconcile_trade(trade_id: int, conn: sqlite3.Connection = Depends(get_connection)):
    conn.execute("DELETE FROM fee_reconciliations WHERE trade_id=?", (trade_id,))
    conn.commit()
    return {"trade_id": trade_id, "reconciled": False}


@router.post("/import")
async def import_fee_statement(
    account_id: int,
    file: UploadFile = File(...),
    conn: sqlite3.Connection = Depends(get_connection),
):
    """Bulk-reconcile from a broker fee export CSV: columns date, ticker, fee
    (any of trade_group/symbol/ticker and fee/commission/actual_fee are accepted)."""
    content = (await file.read()).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="Empty or unreadable CSV")
    headers = {h.strip().lower(): h for h in reader.fieldnames}

    def pick(row, *names):
        for n in names:
            if n in headers and row.get(headers[n], "").strip() != "":
                return row[headers[n]].strip()
        return None

    matched, unmatched = 0, []
    trades = conn.execute("SELECT * FROM trades WHERE account_id=?", (account_id,)).fetchall()
    by_group = {t["trade_group"]: dict(t) for t in trades}
    by_ticker_date = {}
    for t in trades:
        by_ticker_date.setdefault((t["ticker"], t["date"]), []).append(dict(t))

    for row in reader:
        group = pick(row, "trade_group", "group")
        ticker = pick(row, "ticker", "symbol")
        date = pick(row, "date")
        fee_str = pick(row, "fee", "commission", "actual_fee", "broker_actual_fee")
        if fee_str is None:
            continue
        try:
            fee = float(fee_str.replace("$", "").replace(",", ""))
        except ValueError:
            continue

        trade = None
        if group and group in by_group:
            trade = by_group[group]
        elif ticker and date:
            candidates = by_ticker_date.get((ticker.upper(), date))
            if candidates and len(candidates) == 1:
                trade = candidates[0]

        if not trade:
            unmatched.append({"trade_group": group, "ticker": ticker, "date": date, "fee": fee})
            continue

        conn.execute(
            """INSERT INTO fee_reconciliations (trade_id, trade_group, broker_actual_fee, notes)
               VALUES (?,?,?,'imported')
               ON CONFLICT(trade_id) DO UPDATE SET broker_actual_fee=excluded.broker_actual_fee,
                 reconciled_at=datetime('now')""",
            (trade["id"], trade["trade_group"], fee),
        )
        matched += 1
    conn.commit()
    return {"matched": matched, "unmatched": unmatched}


@router.get("/summary")
def reconciliation_summary(account_id: int | None = None, conn: sqlite3.Connection = Depends(get_connection)):
    rows = reconciliation_trades(account_id=account_id, conn=conn)
    reconciled = [r for r in rows if r["reconciled"]]
    total_estimated = sum(r["estimated_fee"] for r in reconciled)
    total_actual = sum(r["broker_actual_fee"] for r in reconciled)
    return {
        "total_trades": len(rows),
        "reconciled_trades": len(reconciled),
        "unreconciled_trades": len(rows) - len(reconciled),
        "total_estimated_fees": round(total_estimated, 4),
        "total_broker_actual_fees": round(total_actual, 4),
        "total_variance": round(total_actual - total_estimated, 4),
    }
