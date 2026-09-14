"""Tax-lot matching: FIFO / LIFO cost-basis assignment per position.

Regulatory note: lot-matching method (FIFO, LIFO, or "specific identification")
is an IRS concept, not an SEC one (IRS Pub. 550 / Treas. Reg. Sec. 1.1012-1).
Brokers default to FIFO unless you specifically identify the lot being closed
at the time of the trade; LIFO and per-trade overrides here model that
"specific identification" choice so your 1099-B can be reconciled lot for lot.

A trade's realized gross/net P&L (the whole round trip) never changes with the
method chosen — only which opening lot(s) are considered closed by which
closing fill changes, along with the resulting holding period (short-term vs.
long-term, the >365-day rule under IRC Sec. 1223) per lot. That per-lot detail
is what this module computes and stores.
"""
import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from database import get_db

router = APIRouter(prefix="/api")

METHODS = ("FIFO", "LIFO")
LOT_METHOD_SETTING_KEY = "lot_matching_method"
DEFAULT_METHOD = "FIFO"


def get_connection():
    conn = get_db()
    try:
        yield conn
    finally:
        conn.close()


def init_lot_tables(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trade_lots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
            trade_group TEXT NOT NULL,
            method TEXT NOT NULL CHECK(method IN ('FIFO','LIFO')),
            open_date TEXT NOT NULL,
            close_date TEXT NOT NULL,
            qty REAL NOT NULL,
            open_price REAL NOT NULL,
            close_price REAL NOT NULL,
            commission REAL NOT NULL DEFAULT 0,
            realized_pnl REAL NOT NULL,
            holding_days INTEGER NOT NULL,
            term TEXT NOT NULL CHECK(term IN ('short_term','long_term')),
            side TEXT NOT NULL CHECK(side IN ('LONG','SHORT'))
        );
        CREATE INDEX IF NOT EXISTS idx_trade_lots_trade ON trade_lots(trade_id);
    """)
    conn.commit()
    try:
        conn.execute("ALTER TABLE trades ADD COLUMN lot_method TEXT")
        conn.commit()
    except Exception:
        pass


def get_global_method(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT value FROM settings WHERE account_id = 0 AND key = ?",
        (LOT_METHOD_SETTING_KEY,),
    ).fetchone()
    return row["value"] if row else DEFAULT_METHOD


def set_global_method(conn: sqlite3.Connection, method: str):
    conn.execute(
        """INSERT INTO settings (account_id, key, value) VALUES (0, ?, ?)
           ON CONFLICT(account_id, key) DO UPDATE SET value = excluded.value""",
        (LOT_METHOD_SETTING_KEY, method),
    )
    conn.commit()


def _days_between(open_date: str, close_date: str) -> int:
    from datetime import date
    try:
        o = date.fromisoformat(open_date[:10])
        c = date.fromisoformat(close_date[:10])
        return (c - o).days
    except Exception:
        return 0


def match_lots(executions: list[dict], method: str, multiplier: float = 1.0) -> list[dict]:
    """Match closing fills against opening fills FIFO or LIFO.

    `executions` is the trade's stored fill list: dicts with action
    (BOT/SOLD), qty, price, date/iso_date, commission. Fills are consumed in
    the chronological order they're stored in (callers already sort them).
    Returns a list of closed-lot dicts (qty, prices, dates, realized_pnl,
    holding_days, term, side). Any still-open remainder (unclosed position) is
    not returned as a lot.
    """
    if method not in METHODS:
        method = DEFAULT_METHOD

    # Each queue entry: [remaining_qty, price, date, commission_per_unit]
    queue: list[list] = []
    queue_side = None  # 'LONG' (queue holds BOT opens) or 'SHORT' (queue holds SOLD opens)
    lots = []

    for ex in executions:
        action = ex.get("action")
        qty = float(ex.get("qty") or 0)
        price = float(ex.get("price") or 0)
        ex_date = ex.get("iso_date") or ex.get("date") or ""
        commission = float(ex.get("commission") or 0)
        commission_per_unit = commission / qty if qty else 0.0
        remaining = qty
        this_side = "LONG" if action == "BOT" else "SHORT"

        # Closing fills are the opposite side of whatever the queue currently holds.
        is_closing = queue and queue_side is not None and this_side != queue_side

        if is_closing:
            while remaining > 1e-9 and queue:
                idx = 0 if method == "FIFO" else -1
                lot_qty, lot_price, lot_date, lot_comm_unit = queue[idx]
                matched = min(lot_qty, remaining)
                open_side = queue_side
                if open_side == "LONG":
                    open_price, close_price = lot_price, price
                else:
                    open_price, close_price = price, lot_price
                pnl = (close_price - open_price) * matched * multiplier
                pnl -= (lot_comm_unit + commission_per_unit) * matched
                open_dt, close_dt = (lot_date, ex_date) if open_side == "LONG" else (lot_date, ex_date)
                holding_days = _days_between(open_dt, close_dt)
                lots.append({
                    "qty": round(matched, 6),
                    "open_price": round(open_price, 6),
                    "close_price": round(close_price, 6),
                    "open_date": open_dt,
                    "close_date": close_dt,
                    "commission": round((lot_comm_unit + commission_per_unit) * matched, 4),
                    "realized_pnl": round(pnl, 4),
                    "holding_days": holding_days,
                    "term": "long_term" if holding_days > 365 else "short_term",
                    "side": open_side,
                })
                remaining -= matched
                new_lot_qty = lot_qty - matched
                if new_lot_qty <= 1e-9:
                    queue.pop(idx)
                else:
                    queue[idx][0] = new_lot_qty
            if not queue:
                queue_side = None

        # Any leftover qty (fill bigger than what was open, i.e. a flip) opens a new lot.
        if remaining > 1e-9:
            if queue and queue_side != this_side:
                # Shouldn't happen (queue would have been drained above), but guard anyway.
                queue = []
            queue.append([remaining, price, ex_date, commission_per_unit])
            queue_side = this_side

    return lots


def _multiplier_for(trade_row: dict) -> float:
    return 100.0 if trade_row.get("instrument_type") == "OPTION" else 1.0


def recalc_trade_lots(conn: sqlite3.Connection, trade_row: dict, global_method: str):
    """Recompute and persist trade_lots for a single trade row (a sqlite3.Row or dict)."""
    method = trade_row.get("lot_method") or global_method
    executions = json.loads(trade_row["executions"] or "[]")
    executions_sorted = sorted(executions, key=lambda e: (e.get("iso_date", e.get("date", "")), e.get("time", "")))
    lots = match_lots(executions_sorted, method, _multiplier_for(trade_row))

    conn.execute("DELETE FROM trade_lots WHERE trade_id = ?", (trade_row["id"],))
    for lot in lots:
        conn.execute(
            """INSERT INTO trade_lots
               (trade_id, trade_group, method, open_date, close_date, qty, open_price,
                close_price, commission, realized_pnl, holding_days, term, side)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (trade_row["id"], trade_row["trade_group"], method, lot["open_date"], lot["close_date"],
             lot["qty"], lot["open_price"], lot["close_price"], lot["commission"], lot["realized_pnl"],
             lot["holding_days"], lot["term"], lot["side"]),
        )
    return lots


def recalc_all_lots(conn: sqlite3.Connection) -> int:
    """Recompute lots for every trade that has no per-trade override (uses the new global method)."""
    global_method = get_global_method(conn)
    rows = conn.execute(
        "SELECT id, trade_group, instrument_type, executions, lot_method FROM trades"
    ).fetchall()
    count = 0
    for row in rows:
        recalc_trade_lots(conn, dict(row), global_method)
        count += 1
    conn.commit()
    return count


# ── API ─────────────────────────────────────────────────────────────────────

class LotMethodBody(BaseModel):
    method: str


@router.get("/settings/lot-method")
def get_lot_method(conn: sqlite3.Connection = Depends(get_connection)):
    return {"method": get_global_method(conn)}


@router.put("/settings/lot-method")
def put_lot_method(body: LotMethodBody, conn: sqlite3.Connection = Depends(get_connection)):
    if body.method not in METHODS:
        raise HTTPException(status_code=400, detail=f"method must be one of {METHODS}")
    set_global_method(conn, body.method)
    recalculated = recalc_all_lots(conn)
    return {"method": body.method, "recalculated_trades": recalculated}


@router.post("/lots/recalculate-all")
def force_recalc(conn: sqlite3.Connection = Depends(get_connection)):
    return {"recalculated_trades": recalc_all_lots(conn)}


class TradeLotMethodBody(BaseModel):
    method: str | None = None  # FIFO | LIFO | None to clear override (use global default)


@router.patch("/trades/{trade_id}/lot-method")
def patch_trade_lot_method(trade_id: int, body: TradeLotMethodBody, conn: sqlite3.Connection = Depends(get_connection)):
    if body.method is not None and body.method not in METHODS:
        raise HTTPException(status_code=400, detail=f"method must be one of {METHODS} or null")
    row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Trade {trade_id} not found")
    conn.execute("UPDATE trades SET lot_method=? WHERE id=?", (body.method, trade_id))
    conn.commit()
    row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
    global_method = get_global_method(conn)
    lots = recalc_trade_lots(conn, dict(row), global_method)
    conn.commit()
    return {"trade_id": trade_id, "method": body.method or global_method, "override": body.method is not None, "lots": lots}


@router.get("/trades/{trade_id}/lots")
def get_trade_lots(trade_id: int, conn: sqlite3.Connection = Depends(get_connection)):
    row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Trade {trade_id} not found")
    lot_rows = conn.execute(
        "SELECT * FROM trade_lots WHERE trade_id=? ORDER BY close_date, open_date", (trade_id,)
    ).fetchall()
    global_method = get_global_method(conn)
    return {
        "trade_id": trade_id,
        "effective_method": row["lot_method"] or global_method,
        "override": row["lot_method"],
        "global_method": global_method,
        "lots": [dict(r) for r in lot_rows],
    }
