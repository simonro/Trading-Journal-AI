import os
import re
import json
import uuid
import sqlite3
import threading
import aiofiles
from pathlib import Path
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel
from dotenv import load_dotenv

import httpx

from database import init_db, get_db, row_to_dict, in_marks, DB_PATH
from csv_parser import parse_broker_csv, detect_broker, FUTURES_MULTIPLIERS
from trade_store import (IMPORT_LOCK, free_name, insert_trade, rebuilt_names,
                         replace_regrouped_trades as _replace_regrouped_trades)
from broker_formats import SourceAccountsError, DEFAULT_TIMEZONE, SPEC_BY_KEY
from zoneinfo import ZoneInfo
from ai_analysis import (
    analyze_diary_entry,
    analyze_diary_text,
    save_analysis_to_db,
    build_trades_context,
    generate_insights,
    build_brain_context,
    generate_brain_response,
    generate_weekly_summary,
    explain_leaks,
)
import leaks
from daily_summary import build_daily_context, generate_daily_summary
from library import router as library_router, init_library_tables, apply_aliases, library_names
from exitlab.api import router as exitlab_router
import edge_sync
import update_check

load_dotenv()

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _conn = get_db()
    try:
        init_library_tables(_conn)
        edge_sync.init_sync_tables(_conn)
    finally:
        _conn.close()
    Path(UPLOAD_DIR).mkdir(exist_ok=True)
    update_check.start_background_check()
    yield


app = FastAPI(title="Trading Journal AI API", lifespan=lifespan)

# This runs on your own machine, so any localhost port is accepted: when 3010 is
# busy the dev server offers 3011, and the app should still work. FRONTEND_ORIGINS
# (comma separated) adds non-localhost origins, e.g. another machine on your LAN.
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("FRONTEND_ORIGINS", "").split(",") if o.strip()]
LOCALHOST_ANY_PORT = r"^http://(localhost|127\.0\.0\.1)(:\d+)?$"

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=LOCALHOST_ANY_PORT,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve uploaded diary screenshots (create the folder on first run)
Path(UPLOAD_DIR).mkdir(exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

# Settings > Library (strategies, sources, tags)
app.include_router(library_router)
app.include_router(exitlab_router)
# Edge Trader for Schwab sync: acts only on a POST from Edge Trader's Apply (backend/edge_sync.py)
app.include_router(edge_sync.router)


@app.get("/api/update")
def get_update_status():
    """Newer release on GitHub? Answered from the once-a-day startup check, never fetched here."""
    return update_check.status()


# ── Dependency ─────────────────────────────────────────────────────────────────

def get_connection():
    conn = get_db()
    try:
        yield conn
    finally:
        conn.close()


def account_ids(account_id: str | None = Query(None)) -> list[int] | None:
    """?account_id=1 or ?account_id=1,4. None means all accounts."""
    if account_id is None or not account_id.strip():
        return None
    try:
        return sorted({int(p) for p in account_id.split(",") if p.strip()}) or None
    except ValueError:
        raise HTTPException(status_code=422, detail="account_id must be an id or a comma-separated list of ids")


def _summary_cache_key(key: str, account_id: list[int] | None) -> tuple[str, int | None]:
    """(summary_date, account_id) an AI summary is cached under. A set of several
    accounts has no account row of its own, so its ids go into the key."""
    if account_id and len(account_id) > 1:
        return f"{key}|accounts={','.join(map(str, account_id))}", None
    return key, account_id[0] if account_id else None


# ── Exception handlers ─────────────────────────────────────────────────────────

@app.exception_handler(SourceAccountsError)
async def source_accounts_handler(request, exc):
    # 409 with the accounts, so the Import page can ask which one to bring in.
    return JSONResponse(status_code=409, content={"error": str(exc), "source_accounts": exc.accounts})


@app.exception_handler(ValueError)
async def value_error_handler(request, exc):
    return JSONResponse(status_code=400, content={"error": str(exc)})


@app.exception_handler(FileNotFoundError)
async def not_found_handler(request, exc):
    return JSONResponse(status_code=404, content={"error": str(exc)})


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    return JSONResponse(
        status_code=500,
        content={"error": str(exc), "type": type(exc).__name__}
    )


# ── Health check ───────────────────────────────────────────────────────────────

@app.get("/")
def health():
    return {"status": "ok"}


# ── Goals ──────────────────────────────────────────────────────────────────────

GOAL_DEFAULTS = {
    "win_rate": 65.0,
    "profit_factor": 1.5,
    "day_win_rate": 75.0,
    "expectancy": 50.0,
    "avg_win_loss_ratio": 1.5,
    "exit_efficiency": 50.0,
}


class GoalsBody(BaseModel):
    account_id: int | None = None
    win_rate: float = 65.0
    profit_factor: float = 1.5
    day_win_rate: float = 75.0
    expectancy: float = 50.0
    avg_win_loss_ratio: float = 1.5
    exit_efficiency: float = 50.0


APP_SETTINGS_DEFAULTS = {
    # The local timezone Thinkorswim's desktop app was running in when it
    # wrote a CSV export, used to convert fill times to exchange (Eastern)
    # time on import. See csv_parser.IMPORT_LOCAL_TZ for the parsing side.
    "import_timezone": "America/New_York",
}


class AppSettingsBody(BaseModel):
    import_timezone: str


@app.get("/api/app-settings")
def get_app_settings(conn: sqlite3.Connection = Depends(get_connection)):
    row = conn.execute(
        "SELECT value FROM settings WHERE account_id = 0 AND key = 'app_settings'",
    ).fetchone()
    if row:
        return {**APP_SETTINGS_DEFAULTS, **json.loads(row["value"])}
    return APP_SETTINGS_DEFAULTS


@app.put("/api/app-settings")
def put_app_settings(
    body: AppSettingsBody,
    conn: sqlite3.Connection = Depends(get_connection),
):
    payload = json.dumps({"import_timezone": body.import_timezone})
    conn.execute(
        """INSERT INTO settings (account_id, key, value) VALUES (0, 'app_settings', ?)
           ON CONFLICT(account_id, key) DO UPDATE SET value = excluded.value""",
        (payload,),
    )
    conn.commit()
    return json.loads(payload)


@app.get("/api/goals")
def get_goals(
    account_id: int | None = Query(None),
    conn: sqlite3.Connection = Depends(get_connection),
):
    acct_key = account_id if account_id is not None else 0
    row = conn.execute(
        "SELECT value FROM settings WHERE account_id = ? AND key = 'goals'",
        (acct_key,),
    ).fetchone()
    if row:
        return json.loads(row["value"])
    # If account-specific not found, try global (0)
    if acct_key != 0:
        row = conn.execute(
            "SELECT value FROM settings WHERE account_id = 0 AND key = 'goals'",
        ).fetchone()
        if row:
            return json.loads(row["value"])
    return GOAL_DEFAULTS


@app.put("/api/goals")
def put_goals(
    body: GoalsBody,
    conn: sqlite3.Connection = Depends(get_connection),
):
    acct_key = body.account_id if body.account_id is not None else 0
    payload = json.dumps({
        "win_rate": body.win_rate,
        "profit_factor": body.profit_factor,
        "day_win_rate": body.day_win_rate,
        "expectancy": body.expectancy,
        "avg_win_loss_ratio": body.avg_win_loss_ratio,
        "exit_efficiency": body.exit_efficiency,
    })
    conn.execute(
        """INSERT INTO settings (account_id, key, value) VALUES (?, 'goals', ?)
           ON CONFLICT(account_id, key) DO UPDATE SET value = excluded.value""",
        (acct_key, payload),
    )
    conn.commit()
    return json.loads(payload)


# ── Accounts ───────────────────────────────────────────────────────────────────

class AccountCreate(BaseModel):
    name: str
    type: str
    color: str = "#6366f1"
    broker: str | None = None


@app.get("/api/accounts")
def list_accounts(conn: sqlite3.Connection = Depends(get_connection)):
    rows = conn.execute("SELECT * FROM accounts ORDER BY created_at").fetchall()
    return [row_to_dict(r) for r in rows]


class AccountUpdate(BaseModel):
    name: str | None = None
    type: str | None = None
    color: str | None = None
    broker: str | None = None


@app.put("/api/accounts/{account_id}")
def update_account(account_id: int, data: AccountUpdate, conn: sqlite3.Connection = Depends(get_connection)):
    row = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Account not found")

    updates = {k: v for k, v in data.model_dump().items() if v is not None}
    if not updates:
        return row_to_dict(row)

    if 'type' in updates:
        valid_types = {'day_trading', 'swing_trading', 'investment'}
        if updates['type'] not in valid_types:
            raise ValueError(f"type must be one of {valid_types}")

    set_clause = ', '.join(f"{k}=?" for k in updates)
    conn.execute(f"UPDATE accounts SET {set_clause} WHERE id=?", list(updates.values()) + [account_id])
    conn.commit()

    row = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
    return row_to_dict(row)


@app.get("/api/accounts/{account_id}/usage")
def account_usage(account_id: int, conn: sqlite3.Connection = Depends(get_connection)):
    """What deleting this account would remove, for the confirmation dialog."""
    if not conn.execute("SELECT 1 FROM accounts WHERE id=?", (account_id,)).fetchone():
        raise HTTPException(status_code=404, detail="Account not found")
    t = conn.execute("SELECT COUNT(*), MIN(date), MAX(date) FROM trades WHERE account_id=?",
                     (account_id,)).fetchone()
    diary = conn.execute("SELECT COUNT(*) FROM diary_entries WHERE account_id=?", (account_id,)).fetchone()[0]
    return {"trades": t[0], "first_date": t[1], "last_date": t[2], "diary_entries": diary}


@app.delete("/api/accounts/{account_id}")
def delete_account(account_id: int, conn: sqlite3.Connection = Depends(get_connection)):
    """Delete an account and everything recorded under it. The database is
    backed up first, so a delete can be undone by restoring that file."""
    if not conn.execute("SELECT 1 FROM accounts WHERE id=?", (account_id,)).fetchone():
        raise HTTPException(status_code=404, detail="Account not found")
    if conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] <= 1:
        raise ValueError("This is the only account. Create another one before deleting it.")

    db = Path(DB_PATH)
    backup = db.with_name(f"{db.stem}.backup-{datetime.now():%Y%m%d-%H%M%S}-predelete-account{account_id}.db")
    dst = sqlite3.connect(backup)
    try:
        conn.backup(dst)
    finally:
        dst.close()

    try:
        trades = conn.execute("DELETE FROM trades WHERE account_id=?", (account_id,)).rowcount
        # Notes and tags are keyed by (trade_group, account_id): another account's
        # trade with the same name keeps its own.
        notes = conn.execute("DELETE FROM trade_analysis WHERE account_id=?", (account_id,)).rowcount
        conn.execute("DELETE FROM trade_tags WHERE account_id=?", (account_id,))
        conn.execute("UPDATE trade_analysis SET diary_entry_id = NULL WHERE diary_entry_id IN "
                     "(SELECT id FROM diary_entries WHERE account_id=?)", (account_id,))
        diary = conn.execute("DELETE FROM diary_entries WHERE account_id=?", (account_id,)).rowcount
        conn.execute("DELETE FROM daily_summaries WHERE account_id=?", (account_id,))
        # AI summaries cached for a set of accounts that included this one (see _summary_cache_key)
        for sid, key in conn.execute("SELECT id, summary_date FROM daily_summaries "
                                     "WHERE account_id IS NULL AND summary_date LIKE '%|accounts=%'").fetchall():
            if str(account_id) in key.rsplit("|accounts=", 1)[1].split(","):
                conn.execute("DELETE FROM daily_summaries WHERE id=?", (sid,))
        conn.execute("DELETE FROM settings WHERE account_id=?", (account_id,))
        edge_sync.forget_account(conn, account_id)
        conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"ok": True, "trades": trades, "trade_notes": notes, "diary_entries": diary,
            "backup": backup.name}


@app.post("/api/accounts", status_code=201)
def create_account(data: AccountCreate, conn: sqlite3.Connection = Depends(get_connection)):
    valid_types = {'day_trading', 'swing_trading', 'investment'}
    if data.type not in valid_types:
        raise ValueError(f"type must be one of {valid_types}")

    cursor = conn.execute(
        "INSERT INTO accounts (name, type, color, broker) VALUES (?,?,?,?)",
        (data.name, data.type, data.color, data.broker)
    )
    conn.commit()

    row = conn.execute("SELECT * FROM accounts WHERE id=?", (cursor.lastrowid,)).fetchone()
    return row_to_dict(row)


# ── CSV Import ─────────────────────────────────────────────────────────────────

class SetupOverride(BaseModel):
    setup: str | None = None      # a playbook setup name, 'NONE', or None to clear
    note: str | None = None


@app.patch("/api/trades/{trade_id}/setup")
def override_setup(
    trade_id: int,
    body: SetupOverride,
    conn: sqlite3.Connection = Depends(get_connection),
):
    """Tag a trade with one of your playbook setups (or clear the tag)."""
    row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Trade {trade_id} not found")

    if body.setup is None:
        conn.execute(
            "UPDATE trades SET setup=NULL, setup_notes=NULL, setup_source='manual' "
            "WHERE id=?", (trade_id,))
        conn.commit()
        return {"id": trade_id, "setup": None, "setup_grade": row['setup_grade'],
                "setup_source": "manual", "message": "Setup tag cleared."}

    if body.setup != 'NONE':
        known = conn.execute(
            "SELECT 1 FROM custom_setups WHERE name=? AND active=1", (body.setup,)
        ).fetchone()
        if not known:
            raise HTTPException(
                status_code=400,
                detail=f"'{body.setup}' is not in your playbook. "
                       f"Add it first (POST /api/setups/custom or the + option in the UI).")

    notes = {
        "manual": True,
        "note": body.note,
        "notes": [f"Manually set to {body.setup}"],
        "violations": [],
    }
    conn.execute(
        "UPDATE trades SET setup=?, setup_notes=?, setup_source='manual' WHERE id=?",
        (body.setup, json.dumps(notes), trade_id))
    conn.commit()
    return {"id": trade_id, "setup": body.setup, "setup_grade": row['setup_grade'],
            "setup_source": "manual", "message": f"Setup set to {body.setup}."}



# ── Playbook setups ───────────────────────────────────────────────────────────
# The playbook is the trader's own list of named setups. Trades are tagged with
# one of them by hand, so tags always carry setup_source='manual'.

class CustomSetupBody(BaseModel):
    name: str
    side: str | None = None      # LONG | SHORT | None (either)
    notes: str | None = None


@app.get("/api/setups/custom")
def list_custom_setups(conn: sqlite3.Connection = Depends(get_connection)):
    rows = conn.execute(
        "SELECT cs.*, "
        " (SELECT COUNT(*) FROM trades t WHERE t.setup = cs.name) AS trade_count, "
        " (SELECT ROUND(SUM(t.net_pnl),2) FROM trades t WHERE t.setup = cs.name) AS net_pnl "
        "FROM custom_setups cs WHERE cs.active = 1 ORDER BY cs.name"
    ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/setups/custom")
def create_custom_setup(body: CustomSetupBody,
                        conn: sqlite3.Connection = Depends(get_connection)):
    name = (body.name or '').strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    if len(name) > 60:
        raise HTTPException(status_code=400, detail="Name must be 60 characters or fewer")
    if name.upper() == 'NONE':
        raise HTTPException(status_code=400, detail="'NONE' is reserved")
    side = (body.side or '').upper() or None
    if side not in (None, 'LONG', 'SHORT'):
        raise HTTPException(status_code=400, detail="side must be LONG, SHORT or empty")
    try:
        conn.execute(
            "INSERT INTO custom_setups (name, side, notes) VALUES (?,?,?)",
            (name, side, body.notes))
        conn.commit()
    except sqlite3.IntegrityError:
        # Already exists — reactivate rather than erroring, so re-adding is harmless.
        conn.execute("UPDATE custom_setups SET active=1 WHERE name=?", (name,))
        conn.commit()
    row = conn.execute("SELECT * FROM custom_setups WHERE name=?", (name,)).fetchone()
    return dict(row)


@app.delete("/api/setups/custom/{setup_id}")
def delete_custom_setup(setup_id: int,
                        conn: sqlite3.Connection = Depends(get_connection)):
    """Soft-delete: trades already tagged with it keep their label."""
    row = conn.execute("SELECT * FROM custom_setups WHERE id=?", (setup_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Setup not found")
    n = conn.execute("SELECT COUNT(*) c FROM trades WHERE setup=?", (row['name'],)).fetchone()['c']
    conn.execute("UPDATE custom_setups SET active=0 WHERE id=?", (setup_id,))
    conn.commit()
    return {"deleted": row['name'], "trades_keeping_label": n}


@app.get("/api/setups")
def setup_stats(
    account_id: int = Query(1),
    conn: sqlite3.Connection = Depends(get_connection),
):
    """Performance grouped by setup and by grade, for the Edge view."""
    def agg(group_col):
        rows = conn.execute(f"""
            SELECT {group_col} AS k,
                   COUNT(*) AS n,
                   SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                   ROUND(SUM(net_pnl), 2) AS total,
                   ROUND(AVG(net_pnl), 2) AS avg,
                   SUM(CASE WHEN net_pnl < -500 THEN 1 ELSE 0 END) AS big_losses
            FROM trades
            WHERE account_id = ? AND instrument_type='STOCK'
              AND net_pnl IS NOT NULL AND net_pnl != 0 AND {group_col} IS NOT NULL
            GROUP BY {group_col} ORDER BY total DESC
        """, (account_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d['win_rate'] = round(d['wins'] / d['n'] * 100, 1) if d['n'] else 0
            out.append(d)
        return out

    return {"by_setup": agg('setup'), "by_grade": agg('setup_grade'), "labels": {}}


@app.post("/api/import-csv")
async def import_csv(
    account_id: int = Form(...),
    file: UploadFile = File(...),
    broker: str = Form('auto'),   # 'thinkorswim' | 'ibkr' | 'generic' | 'tradovate' | 'ninjatrader' | 'topstepx' | 'webull' | 'auto'
    timezone: str = Form(''),      # IANA zone of the file's times (Tradovate, NinjaTrader, TopstepX, Webull)
    source_account: str = Form(''),  # which broker account to take from a file holding several
    conn: sqlite3.Connection = Depends(get_connection),
):
    if not file.filename.lower().endswith('.csv'):
        raise ValueError("Only .csv files are accepted")

    account = conn.execute("SELECT id, import_timezone FROM accounts WHERE id=?", (account_id,)).fetchone()
    if not account:
        raise ValueError(f"Account {account_id} not found")

    # The zone is kept per account: re-importing with another zone would shift every
    # time, so the same fills would no longer be recognised as duplicates.
    zone = timezone.strip() or account['import_timezone'] or DEFAULT_TIMEZONE
    try:
        ZoneInfo(zone)
    except Exception:
        raise ValueError(f"Unknown timezone '{zone}'.")

    raw = await file.read()
    try:
        content = raw.decode('utf-8-sig')  # strips BOM
    except UnicodeDecodeError:
        content = raw.decode('latin-1')

    warnings: list[str] = []
    # One import or sync at a time: both read the stored fills to decide what is new. In a worker
    # thread, so waiting for the lock (an Edge Trader sync in progress) never stalls the server.
    def locked_import():
        with IMPORT_LOCK:
            return _import_parsed(conn, content, broker, account_id, account, zone, timezone,
                                  source_account, warnings)
    skipped, imported, errors = await run_in_threadpool(locked_import)

    return {
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "warnings": warnings,
        "message": (f"Imported {imported} trade group(s). "
                    f"Skipped {skipped} duplicate execution(s)."),
    }


def _import_parsed(conn, content, broker, account_id, account, zone, timezone, source_account, warnings):
    trades, skipped = parse_broker_csv(content, broker, account_id, conn, timezone=zone,
                                       source_account=source_account.strip() or None,
                                       warnings=warnings,
                                       import_tz=get_app_settings(conn)["import_timezone"])

    # Only exports read with the zone save it: a Thinkorswim or IBKR file ignores it.
    fmt = (broker or 'auto').strip().lower()
    if fmt == 'auto':
        fmt = detect_broker(content)
    save_zone = fmt in SPEC_BY_KEY and timezone.strip() and zone != account['import_timezone']
    if save_zone and account['import_timezone']:
        warnings.append(f"The file timezone for this account changed from {account['import_timezone']} to "
                        f"{zone}. Fills imported earlier with the old zone are not matched as duplicates.")
    # Thinkorswim reads its own timezone from Settings > General; say so when the Import page
    # zone picked for this file was a different one, instead of ignoring it silently.
    tos_zone = get_app_settings(conn)["import_timezone"]
    if fmt == 'thinkorswim' and timezone.strip() and zone != tos_zone:
        warnings.append(f"Thinkorswim times were read as {tos_zone}, the timezone in Settings > General. "
                        f"The {zone} picked on the Import page applies only to the beta broker exports.")

    imported = 0
    errors = []

    try:
        _replace_regrouped_trades(conn, account_id, trades)
        taken: set = set()
        rebuilt = rebuilt_names(trades)
        for trade in trades:
            try:
                # Never overwrite a stored trade this import did not rebuild (a vertical or a
                # trade edited in the journal): it takes the next free number instead.
                trade['trade_group'] = free_name(conn, account_id, trade, taken, rebuilt)
                insert_trade(conn, trade)
                imported += 1
            except Exception as e:
                if trade.get('replaces'):
                    raise  # its old rows were cleared: undo the whole import instead
                errors.append({"trade_group": trade.get('trade_group'), "error": str(e)})

        if save_zone:
            conn.execute("UPDATE accounts SET import_timezone=? WHERE id=?", (zone, account_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return skipped, imported, errors


# ── Trades ─────────────────────────────────────────────────────────────────────

class TradeCreate(BaseModel):
    account_id: int
    date: str
    ticker: str
    instrument_type: str = "STOCK"
    side: str
    entry_price: float
    exit_price: float | None = None
    quantity: int = 1
    commissions: float = 0.0
    strategy: str | None = None
    stop_loss: float | None = None
    risk_per_trade: str | None = None
    notes: str | None = None
    option_expiry: str | None = None
    option_strike: float | None = None
    option_type: str | None = None
    time: str | None = None


def point_value(instrument: str | None, ticker: str | None) -> float:
    """Dollars per point per contract: 100 for an option, the futures table for a future
    (longest root first, so /MES is not read as /ES), 1 for a stock."""
    instrument = (instrument or 'STOCK').upper()
    if instrument == 'OPTION':
        return 100
    if instrument == 'FUTURE':
        t = (ticker or '').upper()
        if t and not t.startswith('/'):
            t = '/' + t
        return next((FUTURES_MULTIPLIERS[k] for k in sorted(FUTURES_MULTIPLIERS, key=len, reverse=True)
                     if t.startswith(k.upper())), 1)
    return 1


def compute_manual_pnl(side: str, entry: float, exit_price: float | None, qty: int, commissions: float,
                       multiplier: float = 1) -> tuple[float, float]:
    if exit_price is None:
        return 0.0, -commissions
    if side.upper() == 'LONG':
        gross = (exit_price - entry) * qty * multiplier
    else:
        gross = (entry - exit_price) * qty * multiplier
    return round(gross, 2), round(gross - commissions, 2)


@app.get("/api/point-values")
def get_point_values():
    """The futures point-value table, so the Add Trade preview uses the same numbers."""
    return {"option": 100, "futures": FUTURES_MULTIPLIERS}


def _is_open_position(trade: dict) -> bool:
    execs = trade.get('executions') or []
    side = (trade.get('side') or 'LONG').upper()
    entry_action = 'BOT' if side == 'LONG' else 'SOLD'
    exit_action  = 'SOLD' if side == 'LONG' else 'BOT'
    entry_qty = sum(e.get('qty', 0) for e in execs if e.get('action') == entry_action)
    exit_qty  = sum(e.get('qty', 0) for e in execs if e.get('action') == exit_action)
    return entry_qty > 0 and abs(entry_qty - exit_qty) >= 1e-6


def _closed_trades(trades: list[dict]) -> list[dict]:
    """Trades with a realised result. An open position has no outcome yet, so it must not
    count as a breakeven trade in win rate, expectancy or profit factor."""
    out = []
    for t in trades:
        execs = t.get('executions')
        if isinstance(execs, str):
            try:
                execs = json.loads(execs or '[]')
            except ValueError:
                execs = []
        realised = bool(t.get('gross_pnl'))  # a manual exit at $0 used to be stored with one fill
        if realised or not _is_open_position({**t, 'executions': execs or []}):
            out.append(t)
    return out


@app.get("/api/trades")
def list_trades(
    account_id: list[int] | None = Depends(account_ids),
    instrument_type: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    ticker: str | None = Query(None),
    open_only: bool = Query(False),
    limit: int | None = Query(None),
    conn: sqlite3.Connection = Depends(get_connection),
):
    sql = """
        SELECT t.*, ta.strategy, ta.stop_loss, ta.r_multiple, ta.match_confidence, ta.emotional_state,
               ta.entry_reason, ta.exit_reason, ta.ai_feedback, ta.mistakes, ta.notes as analysis_notes
        FROM trades t
        LEFT JOIN trade_analysis ta ON ta.trade_group = t.trade_group AND ta.account_id = t.account_id
        WHERE 1=1
    """
    params = []

    if account_id:
        sql += f" AND t.account_id IN ({in_marks(account_id)})"
        params += account_id
    if instrument_type:
        sql += " AND t.instrument_type = ?"
        params.append(instrument_type.upper())
    if date_from:
        sql += " AND t.date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND t.date <= ?"
        params.append(date_to)
    if ticker:
        sql += " AND t.ticker LIKE ?"
        params.append(f"%{ticker.upper()}%")

    sql += " ORDER BY t.date DESC, t.imported_at DESC"
    if limit is not None and not open_only:
        sql += f" LIMIT {int(limit)}"

    rows = conn.execute(sql, params).fetchall()
    result = []
    for row in rows:
        d = row_to_dict(row)
        try:
            d['executions'] = json.loads(d.get('executions') or '[]')
        except Exception:
            d['executions'] = []
        if open_only and not _is_open_position(d):
            continue
        result.append(d)

    if limit is not None and open_only:
        result = result[:limit]

    return result


@app.post("/api/trades", status_code=201)
def create_trade(data: TradeCreate, conn: sqlite3.Connection = Depends(get_connection)):
    account = conn.execute("SELECT id FROM accounts WHERE id=?", (data.account_id,)).fetchone()
    if not account:
        raise ValueError(f"Account {data.account_id} not found")

    gross_pnl, net_pnl = compute_manual_pnl(
        data.side, data.entry_price, data.exit_price, data.quantity, data.commissions,
        point_value(data.instrument_type, data.ticker),
    )

    # Build a manual trade group key
    trade_time = data.time or datetime.now().strftime("%H:%M:%S")
    trade_group = f"{data.date}_{data.ticker}_{data.instrument_type}_{trade_time.replace(':', '')}"

    execution = {
        'time': trade_time,
        'action': 'BOT' if data.side.upper() == 'LONG' else 'SOLD',
        'qty': data.quantity,
        'price': data.entry_price,
        'commission': data.commissions / 2,
    }
    if data.exit_price is not None:
        execution2 = {
            'time': trade_time,
            'action': 'SOLD' if data.side.upper() == 'LONG' else 'BOT',
            'qty': data.quantity,
            'price': data.exit_price,
            'commission': data.commissions / 2,
        }
        executions = json.dumps([execution, execution2])
    else:
        executions = json.dumps([execution])

    cursor = conn.execute("""
        INSERT INTO trades
            (account_id, trade_group, date, ticker, instrument_type, side,
             gross_pnl, net_pnl, commissions, executions,
             option_expiry, option_strike, option_type, source)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        data.account_id, trade_group, data.date, data.ticker.upper(),
        data.instrument_type.upper(), data.side.upper(),
        gross_pnl, net_pnl, data.commissions, executions,
        data.option_expiry, data.option_strike, data.option_type, 'manual'
    ))
    conn.commit()

    if data.strategy or data.stop_loss or data.notes:
        conn.execute("""
            INSERT INTO trade_analysis (trade_group, account_id, ticker, date, strategy, stop_loss, notes)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(trade_group, account_id) DO UPDATE SET
                strategy=excluded.strategy, stop_loss=excluded.stop_loss, notes=excluded.notes
        """, (trade_group, data.account_id, data.ticker.upper(), data.date, data.strategy,
              data.stop_loss, data.notes))
        conn.commit()

    row = conn.execute("SELECT * FROM trades WHERE id=?", (cursor.lastrowid,)).fetchone()
    return row_to_dict(row)


@app.put("/api/trades/{trade_id}")
def update_trade(trade_id: int, data: dict, conn: sqlite3.Connection = Depends(get_connection)):
    row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Trade not found")

    trade = row_to_dict(row)
    # Only update allowed fields
    allowed = {'ticker', 'side', 'gross_pnl', 'net_pnl', 'commissions', 'date',
               'instrument_type', 'option_expiry', 'option_strike', 'option_type'}
    updates = {k: v for k, v in data.items() if k in allowed}

    if trade.get('source') in ('imported', 'demo'):
        updates['source'] = 'edited'

    if updates:
        set_clause = ', '.join(f"{k}=?" for k in updates)
        conn.execute(
            f"UPDATE trades SET {set_clause} WHERE id=?",
            list(updates.values()) + [trade_id]
        )
        conn.commit()

    row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
    return row_to_dict(row)


def _recalculate_and_save(trade: dict, execs: list, conn, trade_id: int):
    """Recalculate P&L from executions and persist. Returns updated trade row dict."""
    side = trade['side']
    instrument = trade['instrument_type']
    ticker = trade['ticker']

    entry_fills = [e for e in execs if e['action'] == ('BOT' if side == 'LONG' else 'SOLD')]
    exit_fills  = [e for e in execs if e['action'] == ('SOLD' if side == 'LONG' else 'BOT')]

    entry_qty = sum(e['qty'] for e in entry_fills)
    exit_qty  = sum(e['qty'] for e in exit_fills)
    is_open   = abs(entry_qty - exit_qty) >= 1e-6 or exit_qty == 0

    if is_open:
        gross_pnl, net_pnl = 0.0, 0.0
    else:
        avg_entry = sum(e['qty'] * e['price'] for e in entry_fills) / entry_qty
        avg_exit  = sum(e['qty'] * e['price'] for e in exit_fills)  / exit_qty
        multiplier = point_value(instrument, ticker)
        gross_pnl = (avg_entry - avg_exit if side == 'SHORT' else avg_exit - avg_entry) * entry_qty * multiplier
        commissions_total = sum(e.get('commission', 0) for e in execs)
        net_pnl   = round(gross_pnl - commissions_total, 2)
        gross_pnl = round(gross_pnl, 2)

    commissions = round(sum(e.get('commission', 0) for e in execs), 2)

    # Attribute closed trade to the last exit fill's date
    trade_date = trade['date']
    if not is_open and exit_fills:
        sorted_exits = sorted(exit_fills, key=lambda e: (e.get('date', ''), e.get('time', '')))
        trade_date = sorted_exits[-1].get('date', trade['date'])

    conn.execute(
        "UPDATE trades SET executions=?, gross_pnl=?, net_pnl=?, commissions=?, date=? WHERE id=?",
        (json.dumps(execs), gross_pnl, net_pnl, commissions, trade_date, trade_id)
    )
    conn.commit()
    return row_to_dict(conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone())


def _parse_exec_body(body: dict, fallback_date: str, previous: dict | None = None) -> dict:
    qty = float(body['qty'])
    out = {
        'date': body.get('date', fallback_date),
        'time': body.get('time', ''),
        'action': body['action'].upper(),
        'qty': int(qty) if qty.is_integer() else round(qty, 6),
        'price': float(body['price']),
        'commission': float(body.get('commission', 0)),
    }
    # A fill split at a reversal keeps its link to the statement line while its identity
    # (day, time, side, size, price) is unchanged, so a re-import still recognises it.
    if previous and previous.get('split_qty') is not None and all(
            str(out[k]) == str(previous.get(k)) if k != 'price' and k != 'qty'
            else abs(float(out[k]) - float(previous.get(k) or 0)) < 1e-9
            for k in ('date', 'time', 'action', 'qty', 'price')):
        out['split_qty'] = previous['split_qty']
        out['split_part'] = previous.get('split_part')
    return out


@app.post("/api/trades/{trade_id}/executions")
def add_execution(trade_id: int, body: dict, conn: sqlite3.Connection = Depends(get_connection)):
    # Reads, changes and rewrites the whole fill list: under the import lock, so a sync or import
    # committing in between can never be overwritten by a list read before it.
    with IMPORT_LOCK:
        row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Trade not found")
        trade = row_to_dict(row)
        execs = json.loads(trade.get('executions') or '[]')
        execs.append(_parse_exec_body(body, trade['date']))
        return _recalculate_and_save(trade, execs, conn, trade_id)


@app.put("/api/trades/{trade_id}/executions/{exec_idx}")
def update_execution(trade_id: int, exec_idx: int, body: dict, conn: sqlite3.Connection = Depends(get_connection)):
    with IMPORT_LOCK:
        row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Trade not found")
        trade = row_to_dict(row)
        execs = json.loads(trade.get('executions') or '[]')
        if exec_idx < 0 or exec_idx >= len(execs):
            raise HTTPException(status_code=404, detail="Execution index out of range")
        execs[exec_idx] = _parse_exec_body(body, trade['date'], execs[exec_idx] if 0 <= exec_idx < len(execs) else None)
        return _recalculate_and_save(trade, execs, conn, trade_id)


@app.delete("/api/trades/{trade_id}/executions/{exec_idx}")
def delete_execution(trade_id: int, exec_idx: int, conn: sqlite3.Connection = Depends(get_connection)):
    with IMPORT_LOCK:
        row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Trade not found")
        trade = row_to_dict(row)
        execs = json.loads(trade.get('executions') or '[]')
        if exec_idx < 0 or exec_idx >= len(execs):
            raise HTTPException(status_code=404, detail="Execution index out of range")
        execs.pop(exec_idx)
        return _recalculate_and_save(trade, execs, conn, trade_id)


@app.delete("/api/trades/{trade_id}")
def delete_trade(trade_id: int, conn: sqlite3.Connection = Depends(get_connection)):
    row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Trade not found")

    trade = row_to_dict(row)
    key = (trade['trade_group'], trade['account_id'])

    edge_sync.forget_trade(conn, trade['account_id'], trade['trade_group'])
    conn.execute("DELETE FROM trade_tags WHERE trade_group=? AND account_id=?", key)
    conn.execute("DELETE FROM trade_analysis WHERE trade_group=? AND account_id=?", key)
    conn.execute("DELETE FROM trades WHERE id=?", (trade_id,))
    conn.commit()

    return {"deleted": True, "id": trade_id}


class TradeAccountMove(BaseModel):
    account_id: int


@app.patch("/api/trades/{trade_id}/account")
def move_trade(trade_id: int, body: TradeAccountMove, conn: sqlite3.Connection = Depends(get_connection)):
    """Move a trade to another account. Its notes and tags go with it, since both are keyed by
    (trade_group, account_id). A trade with the same name already in the target account is a
    clash: the move is refused and nothing changes.

    - Notes or tags in the target account under this name with no trade behind them (left by a
      re-import that renamed a trade) cannot be opened anywhere; they are cleared so the moved
      trade does not inherit them, and the reply counts them.
    - The notes lose their link to the old account's diary entry, as an account delete does.
    - Edge Trader sync rows are un-anchored, as a delete does; the next sync re-attaches them
      only if it covers the new account.
    - Holds IMPORT_LOCK so an import or sync cannot write to the old name mid-move."""
    with IMPORT_LOCK:
        return _move_trade(conn, trade_id, body.account_id)


def _move_trade(conn, trade_id: int, dst: int):
    row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Trade not found")
    trade = row_to_dict(row)
    src = trade['account_id']
    target = conn.execute("SELECT name FROM accounts WHERE id=?", (dst,)).fetchone()
    if not target:
        raise HTTPException(status_code=404, detail="Account not found")
    if dst == src:
        raise HTTPException(status_code=400, detail="The trade is already in that account.")

    group = trade['trade_group']
    clash = conn.execute("SELECT id, ticker, date FROM trades WHERE trade_group=? AND account_id=?",
                         (group, dst)).fetchone()
    if clash:
        raise HTTPException(
            status_code=409,
            detail=(f"{target['name']} already has a {clash['ticker']} trade on {clash['date']} with "
                    f"the same name ({group}). Move or delete that trade first."))

    try:
        cleared = 0
        for table in ("trade_analysis", "trade_tags"):
            cleared += conn.execute(f"DELETE FROM {table} WHERE trade_group=? AND account_id=?",
                                    (group, dst)).rowcount
        edge_sync.forget_trade(conn, src, group)
        edge_sync.move_fills(conn, src, dst, trade['executions'], row)
        conn.execute("UPDATE trade_analysis SET diary_entry_id=NULL WHERE trade_group=? AND account_id=?",
                     (group, src))
        for table in ("trade_analysis", "trade_tags"):
            conn.execute(f"UPDATE {table} SET account_id=? WHERE trade_group=? AND account_id=?",
                         (dst, group, src))
        conn.execute("UPDATE trades SET account_id=? WHERE id=?", (dst, trade_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"moved": True, "id": trade_id, "account_id": dst, "account_name": target['name'],
            "cleared_leftovers": cleared}


def _trade_for_group(conn, trade_group: str, account_id: int | None):
    """The trade a review endpoint is about. trade_group names are not unique across
    accounts, so callers pass account_id; without it the name must belong to one account."""
    if account_id is not None:
        return conn.execute("SELECT trade_group, account_id, ticker, date FROM trades "
                            "WHERE trade_group=? AND account_id=?", (trade_group, account_id)).fetchone()
    rows = conn.execute("SELECT trade_group, account_id, ticker, date FROM trades WHERE trade_group=?",
                        (trade_group,)).fetchall()
    if len(rows) > 1:
        raise HTTPException(status_code=409,
                            detail="This trade name exists in several accounts. Pass account_id.")
    return rows[0] if rows else None


@app.get("/api/trades/{trade_group:path}/analysis")
def get_trade_analysis(trade_group: str, account_id: int | None = Query(None),
                       conn: sqlite3.Connection = Depends(get_connection)):
    trade = _trade_for_group(conn, trade_group, account_id)
    if not trade:
        return {"analysis": None, "tags": []}
    key = (trade["trade_group"], trade["account_id"])
    analysis = conn.execute(
        "SELECT * FROM trade_analysis WHERE trade_group=? AND account_id=?", key
    ).fetchone()

    tags = conn.execute(
        "SELECT * FROM trade_tags WHERE trade_group=? AND account_id=?", key
    ).fetchall()

    return {
        "analysis": row_to_dict(analysis) if analysis else None,
        "tags": [row_to_dict(t) for t in tags],
    }


class AnalysisUpdate(BaseModel):
    strategy: str | None = None
    idea_source: str | None = None
    stop_loss: float | None = None
    target_price: float | None = None
    emotional_state: str | None = None
    entry_reason: str | None = None
    exit_reason: str | None = None
    mistakes: str | None = None
    notes: str | None = None


@app.patch("/api/trades/{trade_group:path}/analysis")
def update_trade_analysis(trade_group: str, data: AnalysisUpdate, account_id: int | None = Query(None),
                          conn: sqlite3.Connection = Depends(get_connection)):
    trade = _trade_for_group(conn, trade_group, account_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    key = (trade["trade_group"], trade["account_id"])

    updates = data.model_dump(exclude_unset=True)

    existing = conn.execute("SELECT id FROM trade_analysis WHERE trade_group=? AND account_id=?", key).fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO trade_analysis (trade_group, account_id, ticker, date) VALUES (?,?,?,?)",
            key + (trade["ticker"], trade["date"])
        )

    if updates:
        set_clause = ", ".join(f"{k}=?" for k in updates)
        conn.execute(
            f"UPDATE trade_analysis SET {set_clause} WHERE trade_group=? AND account_id=?",
            list(updates.values()) + list(key)
        )

    conn.commit()
    row = conn.execute("SELECT * FROM trade_analysis WHERE trade_group=? AND account_id=?", key).fetchone()
    return row_to_dict(row) if row else {}


class TagCreate(BaseModel):
    tag_type: str
    tag_value: str


@app.post("/api/trades/{trade_group:path}/tags", status_code=201)
def add_trade_tag(trade_group: str, data: TagCreate, account_id: int | None = Query(None),
                  conn: sqlite3.Connection = Depends(get_connection)):
    trade = _trade_for_group(conn, trade_group, account_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    cursor = conn.execute(
        "INSERT INTO trade_tags (trade_group, account_id, tag_type, tag_value, source) VALUES (?,?,?,?,'manual')",
        (trade["trade_group"], trade["account_id"], data.tag_type, data.tag_value)
    )
    conn.commit()
    row = conn.execute("SELECT * FROM trade_tags WHERE id=?", (cursor.lastrowid,)).fetchone()
    return row_to_dict(row)


@app.get("/api/analysis-options")
def get_analysis_options(conn: sqlite3.Connection = Depends(get_connection)):
    strategies = conn.execute(
        "SELECT DISTINCT strategy FROM trade_analysis WHERE strategy IS NOT NULL ORDER BY strategy"
    ).fetchall()
    idea_sources = conn.execute(
        "SELECT DISTINCT idea_source FROM trade_analysis WHERE idea_source IS NOT NULL ORDER BY idea_source"
    ).fetchall()
    used_strategies = [r["strategy"] for r in strategies]
    used_sources = [r["idea_source"] for r in idea_sources]
    return {
        "strategies": sorted(set(used_strategies) | set(library_names(conn, "strategy")), key=str.lower),
        "idea_sources": sorted(set(used_sources) | set(library_names(conn, "source")), key=str.lower),
    }


@app.delete("/api/trade-tags/{tag_id}")
def delete_trade_tag(tag_id: int, conn: sqlite3.Connection = Depends(get_connection)):
    row = conn.execute("SELECT id FROM trade_tags WHERE id=?", (tag_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Tag not found")
    conn.execute("DELETE FROM trade_tags WHERE id=?", (tag_id,))
    conn.commit()
    return {"deleted": True, "id": tag_id}


# ── KPIs ───────────────────────────────────────────────────────────────────────


def _excursion_kpis(conn, account_id=None, date_from=None, date_to=None) -> dict:
    """Aggregate trade-quality metrics: how much of the move was captured, and
    how much heat was taken to get it.

    exit_efficiency is averaged over WINNERS only — a loser has no favourable
    excursion to capture, so including them would measure something else.
    MAE is reported separately for winners and losers because the gap between
    them is what calibrates the stop.
    """
    sql = ("SELECT net_pnl, mfe_pct, mae_pct, exit_efficiency FROM trades "
           "WHERE instrument_type='STOCK' AND mfe_pct IS NOT NULL "
           "AND net_pnl IS NOT NULL AND net_pnl <> 0")
    params = []
    if account_id:
        sql += f" AND account_id IN ({in_marks(account_id)})"; params += account_id
    if date_from:
        sql += " AND date >= ?"; params.append(date_from)
    if date_to:
        sql += " AND date <= ?"; params.append(date_to)
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return {}
    wins = [r for r in rows if r['net_pnl'] > 0]
    losses = [r for r in rows if r['net_pnl'] <= 0]

    def avg(vals):
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    def med(vals):
        vals = sorted(v for v in vals if v is not None)
        if not vals:
            return None
        n = len(vals)
        return round(vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2, 2)

    return {
        "exit_efficiency": avg([r['exit_efficiency'] for r in wins]),
        "exit_efficiency_median": med([r['exit_efficiency'] for r in wins]),
        "avg_mfe": avg([r['mfe_pct'] for r in rows]),
        "avg_mae": avg([r['mae_pct'] for r in rows]),
        "avg_mae_win": avg([r['mae_pct'] for r in wins]),
        "avg_mae_loss": avg([r['mae_pct'] for r in losses]),
        "excursion_n": len(rows),
    }


@app.get("/api/kpis")
def get_kpis(
    account_id: list[int] | None = Depends(account_ids),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    conn: sqlite3.Connection = Depends(get_connection),
):
    sql = "SELECT * FROM trades WHERE 1=1"
    params = []

    if account_id:
        sql += f" AND account_id IN ({in_marks(account_id)})"
        params += account_id
    if date_from:
        sql += " AND date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND date <= ?"
        params.append(date_to)

    rows = conn.execute(sql + " ORDER BY date", params).fetchall()
    trades = [row_to_dict(r) for r in rows]

    total_net_pnl = sum(t.get('net_pnl') or 0 for t in trades)
    total_gross_pnl = sum(t.get('gross_pnl') or 0 for t in trades)
    total_commissions = sum(t.get('commissions') or 0 for t in trades)

    closed = _closed_trades(trades)
    open_trades = len(trades) - len(closed)
    winners = [t for t in closed if (t.get('net_pnl') or 0) > 0]
    losers = [t for t in closed if (t.get('net_pnl') or 0) < 0]
    total_trades = len(closed)
    win_rate = round(len(winners) / total_trades * 100, 2) if total_trades else 0

    avg_win = round(sum(t['net_pnl'] for t in winners) / len(winners), 2) if winners else 0
    avg_loss = round(sum(t['net_pnl'] for t in losers) / len(losers), 2) if losers else 0

    # Net on both sides: the same trades decide winner/loser and the sums (commissions in).
    net_wins = sum(t.get('net_pnl') or 0 for t in winners)
    net_losses = abs(sum(t.get('net_pnl') or 0 for t in losers))
    profit_factor = round(net_wins / net_losses, 2) if net_losses else None

    # Expectancy = win_rate * avg_win + loss_rate * avg_loss (avg_loss is negative)
    if total_trades > 0:
        expectancy = round(
            (len(winners) / total_trades) * avg_win + (len(losers) / total_trades) * avg_loss, 2
        )
    else:
        expectancy = 0.0

    # Daily P&L
    daily: dict[str, float] = {}
    for t in trades:
        d = t.get('date', '')
        daily[d] = daily.get(d, 0) + (t.get('net_pnl') or 0)

    trading_days = len(daily)
    positive_days = sum(1 for v in daily.values() if v > 0)
    day_win_rate = round(positive_days / trading_days * 100, 1) if trading_days else 0

    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    daily_pnl = []
    for date in sorted(daily.keys()):
        cumulative += daily[date]
        if cumulative > peak:
            peak = cumulative
        dd = cumulative - peak
        if dd < max_drawdown:
            max_drawdown = dd
        daily_pnl.append({
            "date": date,
            "net_pnl": round(daily[date], 2),
            "cumulative": round(cumulative, 2),
        })

    # By instrument type
    by_instrument: dict[str, dict] = {}
    for t in trades:
        inst = t.get('instrument_type', 'STOCK')
        if inst not in by_instrument:
            by_instrument[inst] = {'net_pnl': 0, 'count': 0, 'wins': 0}
        by_instrument[inst]['net_pnl'] += t.get('net_pnl') or 0
        by_instrument[inst]['count'] += 1
        if (t.get('net_pnl') or 0) > 0:
            by_instrument[inst]['wins'] += 1

    # By strategy (join with trade_analysis)
    # Strategy breakdown = playbook setup tag first, diary strategy as fallback.
    # The label is resolved in an inner query so GROUP BY cannot bind to the
    # underlying ta.strategy column instead of the resolved alias.
    strategy_sql = """
        SELECT label as strategy,
               COUNT(*) as count,
               SUM(net_pnl) as total_pnl,
               SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) as wins,
               AVG(r_multiple) as avg_r
        FROM (
            SELECT COALESCE(
                       CASE WHEN t.setup IS NOT NULL AND t.setup <> 'NONE'
                            THEN t.setup END,
                       ta.strategy
                   ) as label,
                   t.net_pnl as net_pnl,
                   ta.r_multiple as r_multiple,
                   t.account_id as account_id
            FROM trades t
            LEFT JOIN trade_analysis ta ON ta.trade_group = t.trade_group AND ta.account_id = t.account_id
            WHERE t.net_pnl IS NOT NULL AND t.net_pnl != 0
        ) sub
        WHERE label IS NOT NULL
    """
    strat_params = []
    if account_id:
        strategy_sql += f" AND account_id IN ({in_marks(account_id)})"
        strat_params += account_id
    strategy_sql += " GROUP BY label ORDER BY total_pnl DESC"

    strat_rows = conn.execute(strategy_sql, strat_params).fetchall()
    by_strategy = []
    for r in strat_rows:
        r = dict(r)
        count = r['count']
        by_strategy.append({
            "strategy": r['strategy'],
            "net_pnl": round(r['total_pnl'] or 0, 2),
            "win_rate": round(r['wins'] / count * 100, 1) if count else 0,
            "count": count,
            "avg_r": round(r['avg_r'] or 0, 2),
        })

    return {
        "total_net_pnl": round(total_net_pnl, 2),
        "total_gross_pnl": round(total_gross_pnl, 2),
        "total_commissions": round(total_commissions, 2),
        "total_trades": total_trades,
        "winning_trades": len(winners),
        "losing_trades": len(losers),
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "open_trades": open_trades,
        "trading_days": trading_days,
        "positive_days": positive_days,
        "day_win_rate": day_win_rate,
        "daily_pnl": daily_pnl,
        "by_instrument": by_instrument,
        "by_strategy": by_strategy,
        "expectancy": expectancy,
        "max_drawdown": round(max_drawdown, 2),
        **_excursion_kpis(conn, account_id, date_from, date_to),
    }


# ── Diary Upload ───────────────────────────────────────────────────────────────

# .heic/.heif are what an iPhone produces by default — a photo of handwritten
# notes taken on the phone lands here. They are converted to JPEG on upload
# because the vision API does not accept HEIC.
ALLOWED_IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.heic', '.heif'}
ALLOWED_TEXT_EXTENSIONS = {'.txt', '.csv'}
ALLOWED_DIARY_EXTENSIONS = ALLOWED_IMAGE_EXTENSIONS | ALLOWED_TEXT_EXTENSIONS


@app.post("/api/upload-diary")
async def upload_diary(
    date: str = Form(...),
    account_id: int = Form(...),
    file: UploadFile = File(...),
    conn: sqlite3.Connection = Depends(get_connection),
):
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_DIARY_EXTENSIONS:
        raise ValueError(f"File must be one of {ALLOWED_DIARY_EXTENSIONS}")

    account = conn.execute("SELECT id FROM accounts WHERE id=?", (account_id,)).fetchone()
    if not account:
        raise ValueError(f"Account {account_id} not found")

    # The date must be a plain YYYY-MM-DD and the name is made here, never taken from the
    # client as a path: nothing can land outside the uploads folder, and a second
    # "image.png" never overwrites the first one an older entry points at.
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise ValueError("date must be YYYY-MM-DD")
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(file.filename).stem)[:60].strip("._") or "upload"
    safe_name = f"{date}_{account_id}_{stem}_{uuid.uuid4().hex[:8]}{ext}"
    save_path = Path(UPLOAD_DIR) / safe_name
    if Path(UPLOAD_DIR).resolve() not in save_path.resolve().parents:
        raise ValueError("Invalid file name")

    raw = await file.read()

    # iPhone photos arrive as HEIC, which the vision API cannot read. Convert to
    # JPEG on the way in so a phone snap of handwritten notes just works.
    if ext in {'.heic', '.heif'}:
        try:
            import io
            import pillow_heif
            from PIL import Image as PILImage
            pillow_heif.register_heif_opener()
            img = PILImage.open(io.BytesIO(raw)).convert('RGB')
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=90)
            raw = buf.getvalue()
            ext = '.jpg'
            safe_name = str(Path(safe_name).with_suffix('.jpg'))
            save_path = Path(UPLOAD_DIR) / safe_name
        except Exception as exc:
            raise ValueError(
                "Could not convert this HEIC photo. On iPhone, Settings > Camera > "
                f"Formats > Most Compatible saves as JPEG instead. ({exc})")

    async with aiofiles.open(save_path, 'wb') as f:
        await f.write(raw)

    # Insert diary entry row
    cursor = conn.execute(
        "INSERT INTO diary_entries (account_id, entry_date, image_path) VALUES (?,?,?)",
        (account_id, date, safe_name)
    )
    conn.commit()
    diary_entry_id = cursor.lastrowid

    # Build trades context for Claude
    trades_context = build_trades_context(conn, date, account_id)

    # Call Claude — image vision or text depending on file type
    analysis_error = None
    analysis = None
    try:
        if ext in ALLOWED_TEXT_EXTENSIONS:
            text_content = raw.decode('utf-8', errors='replace')
            analysis = analyze_diary_text(text_content, date, trades_context)
        else:
            analysis = analyze_diary_entry(str(save_path.absolute()), date, trades_context)
        analysis = apply_aliases(conn, analysis)
        # Persist analysis
        conn.execute(
            "UPDATE diary_entries SET ai_analysis=? WHERE id=?",
            (json.dumps(analysis), diary_entry_id)
        )
        conn.commit()
        save_analysis_to_db(conn, diary_entry_id, analysis)
    except Exception as e:
        analysis_error = str(e)

    diary_row = conn.execute("SELECT * FROM diary_entries WHERE id=?", (diary_entry_id,)).fetchone()
    result = row_to_dict(diary_row)

    if analysis_error:
        result['analysis_error'] = analysis_error
    else:
        result['trade_count'] = len(analysis.get('trade_analyses', [])) if analysis else 0

    return result


# ── Diary List ─────────────────────────────────────────────────────────────────

@app.get("/api/diary")
def list_diary(
    account_id: list[int] | None = Depends(account_ids),
    conn: sqlite3.Connection = Depends(get_connection),
):
    sql = "SELECT * FROM diary_entries WHERE 1=1"
    params = []
    if account_id:
        sql += f" AND account_id IN ({in_marks(account_id)})"
        params += account_id
    sql += " ORDER BY entry_date DESC"

    rows = conn.execute(sql, params).fetchall()
    result = []
    for row in rows:
        d = row_to_dict(row)
        try:
            d['ai_analysis'] = json.loads(d['ai_analysis']) if d.get('ai_analysis') else None
        except Exception:
            d['ai_analysis'] = None
        result.append(d)

    return result


@app.delete("/api/diary/by-date/{date}")
def delete_diary_by_date(
    date: str,
    account_id: list[int] | None = Depends(account_ids),
    conn: sqlite3.Connection = Depends(get_connection),
):
    # The day note written in Edge Trader is the trader's own text, not an uploaded diary:
    # clearing the day's uploads leaves it (delete it by id to remove it).
    where = f"entry_date=? AND COALESCE(source, '') <> '{edge_sync.SOURCE}'"
    params: list = [date]
    if account_id:
        where += f" AND account_id IN ({in_marks(account_id)})"
        params += account_id
    # trade_analysis.diary_entry_id points back here, so unlink first: the
    # analysis (including anything edited by hand) stays on the trade.
    conn.execute(
        f"UPDATE trade_analysis SET diary_entry_id = NULL WHERE diary_entry_id IN "
        f"(SELECT id FROM diary_entries WHERE {where})", params)
    conn.execute(f"DELETE FROM diary_entries WHERE {where}", params)
    conn.commit()
    return {"ok": True}


@app.delete("/api/diary/{entry_id}")
def delete_diary_entry(entry_id: int, conn: sqlite3.Connection = Depends(get_connection)):
    # Unlink the analyses this entry produced, otherwise the foreign key blocks
    # the delete with a 500. The analysis stays on the trade.
    conn.execute("UPDATE trade_analysis SET diary_entry_id = NULL WHERE diary_entry_id = ?", (entry_id,))
    conn.execute("DELETE FROM diary_entries WHERE id=?", (entry_id,))
    conn.commit()
    return {"ok": True}


# ── Chart Proxy ────────────────────────────────────────────────────────────────

ALPACA_KEY = os.getenv("APCA_API_KEY_ID", "")
ALPACA_SECRET = os.getenv("APCA_API_SECRET_KEY", "")
# "iex" works on a free Alpaca account; "sip" needs a paid market-data subscription.
# Default to iex so the chart works out of the box, regardless of which tier the
# viewer's key is on. Override with ALPACA_DATA_FEED=sip if you have the subscription.
ALPACA_DATA_FEED = os.getenv("ALPACA_DATA_FEED", "iex")

FUTURES_CHART_MAP = {
    '/ES': 'SPY', '/MES': 'SPY',
    '/NQ': 'QQQ', '/MNQ': 'QQQ',
    '/YM': 'DIA', '/MYM': 'DIA',
    '/RTY': 'IWM', '/M2K': 'IWM',
}


ALLOWED_CHART_TIMEFRAMES = {
    "1Min", "3Min", "5Min", "10Min", "15Min", "30Min", "1Hour", "1Day", "1Week",
}
# Daily/Weekly are a wide-context view around the trade, not the single RTH session
# the intraday timeframes use, so they get their own start/end/limit below.
_WIDE_RANGE_TIMEFRAMES = {"1Day", "1Week"}


async def _fetch_alpaca_bars(client, url, base_params, headers, max_bars=5000):
    """Follow Alpaca's next_page_token until exhausted or max_bars is hit.

    A single page caps at 1000 bars — a multi-day intraday request (the chart's
    zoom-out lazy-load) can easily exceed that, and Alpaca returns bars oldest
    first, so an unpaginated request would silently drop the most recent bars.
    """
    bars = []
    page_token = None
    while True:
        params = dict(base_params)
        if page_token:
            params["page_token"] = page_token
        resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        bars.extend(data.get("bars", []))
        page_token = data.get("next_page_token")
        if not page_token or len(bars) >= max_bars:
            break
    return bars


_CHART_ET = ZoneInfo("America/New_York")


@app.get("/api/chart/{ticker}/{date}")
async def get_chart(
    ticker: str, date: str,
    timeframe: str = Query("5Min"),
    days_back: int = Query(1, ge=1),
):
    if not ALPACA_KEY or ALPACA_KEY == "your_alpaca_api_key_here":
        return {
            "ticker": ticker, "date": date, "bars": [],
            "warning": "Add APCA_API_KEY_ID and APCA_API_SECRET_KEY to backend/.env to enable price charts."
        }

    # Normalize ticker
    if ticker.upper().startswith('/'):
        # Map futures to proxy ETF for charting
        alpaca_ticker = FUTURES_CHART_MAP.get(ticker.upper(), 'SPY')
    else:
        alpaca_ticker = ticker.upper()

    tf = timeframe if timeframe in ALLOWED_CHART_TIMEFRAMES else "5Min"

    # Same knob as the intraday branch below (days_back widens the window when
    # the chart is zoomed out past what's loaded) — daily/weekly just start
    # from a much bigger default and cap much further out, since a decade of
    # daily bars is still only ~2500 rows.
    _WIDE_DAYS_BACK_CAP = {"1Day": 3650, "1Week": 5475}
    days_back = min(days_back, _WIDE_DAYS_BACK_CAP.get(tf, days_back)) if tf in _WIDE_RANGE_TIMEFRAMES else min(days_back, 90)

    url = f"https://data.alpaca.markets/v2/stocks/{alpaca_ticker}/bars"
    if tf in _WIDE_RANGE_TIMEFRAMES:
        trade_day = datetime.strptime(date, "%Y-%m-%d").date()
        start = trade_day - timedelta(days=days_back - 1)
        end = min(trade_day + timedelta(days=10), datetime.utcnow().date())
        params = {
            "timeframe": tf,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "limit": 1000,
            "feed": ALPACA_DATA_FEED,
            "adjustment": "raw",
        }
    else:
        # days_back widens the window backward (calendar days, weekends just come
        # back empty) so zooming out on the chart can load real prior sessions
        # instead of running off the edge of a single day's data.
        trade_day = datetime.strptime(date, "%Y-%m-%d").date()
        start_day = trade_day - timedelta(days=days_back - 1)
        params = {
            "timeframe": tf,
            # US Eastern with the real offset of each day (EDT -04:00, EST -05:00).
            "start": datetime(start_day.year, start_day.month, start_day.day, 9, 30, tzinfo=_CHART_ET).isoformat(),
            "end": datetime(trade_day.year, trade_day.month, trade_day.day, 16, 0, tzinfo=_CHART_ET).isoformat(),
            "limit": 1000,
            "feed": ALPACA_DATA_FEED,
            "adjustment": "raw",
        }
    headers = {
        "APCA-API-KEY-ID": ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            try:
                raw_bars = await _fetch_alpaca_bars(client, url, params, headers)
            except httpx.HTTPStatusError as e:
                # A 403 on a non-iex feed means the key's tier doesn't carry that
                # feed's subscription. Retry once on iex, which every Alpaca
                # account (free included) can read, instead of failing the chart.
                if e.response.status_code == 403 and params["feed"] != "iex":
                    fallback_params = dict(params, feed="iex")
                    raw_bars = await _fetch_alpaca_bars(client, url, fallback_params, headers)
                else:
                    raise

        bars = []
        for bar in raw_bars:
            bars.append({
                "t": bar.get("t", ""),
                "o": bar.get("o", 0),
                "h": bar.get("h", 0),
                "l": bar.get("l", 0),
                "c": bar.get("c", 0),
                "v": bar.get("v", 0),
                "vw": bar.get("vw"),
            })

        return {"ticker": alpaca_ticker, "original_ticker": ticker, "date": date, "bars": bars}

    except httpx.HTTPStatusError as e:
        detail = "subscription required for this feed" if e.response.status_code == 403 else str(e.response.status_code)
        return {
            "ticker": ticker, "date": date, "bars": [],
            "warning": f"Alpaca API error: {detail}"
        }
    except Exception as e:
        return {
            "ticker": ticker, "date": date, "bars": [],
            "warning": f"Chart unavailable: {str(e)}"
        }


# ── Calendar ───────────────────────────────────────────────────────────────────

@app.get("/api/calendar")
def get_calendar(
    account_id: list[int] | None = Depends(account_ids),
    year: int | None = Query(None),
    month: int | None = Query(None),
    conn: sqlite3.Connection = Depends(get_connection),
):
    sql = "SELECT date, net_pnl FROM trades WHERE 1=1"
    params = []

    if account_id:
        sql += f" AND account_id IN ({in_marks(account_id)})"
        params += account_id
    if year and month:
        date_from = f"{year:04d}-{month:02d}-01"
        next_month_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
        date_to = f"{next_month_year:04d}-{next_month:02d}-01"
        sql += " AND date >= ? AND date < ?"
        params.extend([date_from, date_to])

    rows = conn.execute(sql, params).fetchall()

    day_stats: dict[str, dict] = {}
    for row in rows:
        d = row['date']
        pnl = row['net_pnl'] or 0
        if d not in day_stats:
            day_stats[d] = {'net_pnl': 0.0, 'trade_count': 0, 'winners': 0, 'losers': 0}
        day_stats[d]['net_pnl'] += pnl
        day_stats[d]['trade_count'] += 1
        if pnl > 0:
            day_stats[d]['winners'] += 1
        elif pnl < 0:
            day_stats[d]['losers'] += 1

    # Which days have diary entries
    diary_sql = "SELECT entry_date FROM diary_entries WHERE 1=1"
    diary_params = []
    if account_id:
        diary_sql += f" AND account_id IN ({in_marks(account_id)})"
        diary_params += account_id
    diary_rows = conn.execute(diary_sql, diary_params).fetchall()
    diary_dates = {r['entry_date'] for r in diary_rows}

    result = []
    for date, stats in sorted(day_stats.items()):
        count = stats['trade_count']
        result.append({
            'date': date,
            'net_pnl': round(stats['net_pnl'], 2),
            'trade_count': count,
            'winners': stats['winners'],
            'losers': stats['losers'],
            'win_rate': round(stats['winners'] / count * 100, 1) if count else 0,
            'has_diary': date in diary_dates,
        })

    return result


# ── Yearly KPIs ────────────────────────────────────────────────────────────────

@app.get("/api/yearly-kpis")
def get_yearly_kpis(
    year: int = Query(...),
    account_id: list[int] | None = Depends(account_ids),
    conn: sqlite3.Connection = Depends(get_connection),
):
    sql = "SELECT date, net_pnl, gross_pnl, side, executions FROM trades WHERE strftime('%Y', date) = ?"
    params = [str(year)]
    if account_id:
        sql += f" AND account_id IN ({in_marks(account_id)})"
        params += account_id

    rows = _closed_trades([dict(r) for r in conn.execute(sql + " ORDER BY date", params).fetchall()])

    # Bucket trades by month
    from collections import defaultdict
    months: dict[int, list] = defaultdict(list)
    for row in rows:
        m = int(row["date"][5:7])
        months[m].append({"net_pnl": row["net_pnl"] or 0, "gross_pnl": row["gross_pnl"] or 0, "date": row["date"]})

    result = []
    for m in range(1, 13):
        trades = months.get(m, [])
        if not trades:
            result.append({"month": m, "has_data": False})
            continue

        winners = [t for t in trades if t["net_pnl"] > 0]
        losers  = [t for t in trades if t["net_pnl"] < 0]
        total   = len(trades)

        net_pnl       = sum(t["net_pnl"] for t in trades)
        avg_win        = sum(t["net_pnl"] for t in winners) / len(winners) if winners else 0
        avg_loss       = sum(t["net_pnl"] for t in losers)  / len(losers)  if losers  else 0
        net_wins       = sum(t["net_pnl"] for t in winners)
        net_losses     = abs(sum(t["net_pnl"] for t in losers))
        profit_factor  = net_wins / net_losses if net_losses else None
        win_rate       = len(winners) / total * 100 if total else 0
        trading_days   = len(set(t["date"] for t in trades))
        positive_days  = len({t["date"] for t in trades if t["net_pnl"] > 0})
        day_win_rate   = positive_days / trading_days * 100 if trading_days else 0

        result.append({
            "month": m,
            "has_data": True,
            "net_pnl": round(net_pnl, 2),
            "win_rate": round(win_rate, 1),
            "profit_factor": round(profit_factor, 2) if profit_factor is not None else None,
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "total_trades": total,
            "winning_trades": len(winners),
            "losing_trades": len(losers),
            "trading_days": trading_days,
            "day_win_rate": round(day_win_rate, 1),
        })

    return result


# ── Edge Report ────────────────────────────────────────────────────────────────

# ── Reports ───────────────────────────────────────────────────────────────────
# The standard breakdowns a trading journal is expected to answer: when do I
# trade well, what do I trade well, and how well do I execute. Every bucket
# returns the same shape so one frontend component renders all of them.

_DOW_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
_SETUP_LABEL_MAP = {'NONE': 'No setup'}
_HOLD_ORDER = ['0-5 min', '5-15 min', '15-30 min', '30-60 min', '1-2 hrs', '2+ hrs']
_SESSION_ORDER = ['09:30-09:45', '09:45-10:30', '10:30-11:00',
                  '11:00-13:30', '13:30-15:30', '15:30-16:00']


def _mins_of(t):
    """'09:45:12' -> minutes since midnight. None when unparseable."""
    if not t:
        return None
    try:
        p = str(t).split(':')
        return int(p[0]) * 60 + int(p[1])
    except Exception:
        return None


def _bucket_stats(rows, key_fn, label_fn=None):
    """Group rows by key_fn and compute the standard per-bucket stats.

    exit_efficiency is averaged over winners only — a loser has no favourable
    excursion to capture, so mixing them would measure something else.
    """
    from collections import defaultdict
    buckets = defaultdict(list)
    for r in rows:
        k = key_fn(r)
        if k is None or k == '':
            continue
        buckets[k].append(r)

    out = []
    for k, group in buckets.items():
        pnls = sorted(g['net_pnl'] for g in group)
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        effs = [g['exit_efficiency'] for g in group
                if g.get('exit_efficiency') is not None and g['net_pnl'] > 0]
        maes = [g['mae_pct'] for g in group if g.get('mae_pct') is not None]
        n = len(group)
        out.append({
            "key": str(k),
            "label": label_fn(k) if label_fn else str(k),
            "trades": n,
            "net_pnl": round(sum(pnls), 2),
            "avg_pnl": round(sum(pnls) / n, 2),
            "median_pnl": round(pnls[n // 2], 2),
            "win_rate": round(len(wins) / n * 100, 1),
            "wins": len(wins),
            "losses": len(losses),
            "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
            "profit_factor": (round(sum(wins) / abs(sum(losses)), 2)
                              if losses and sum(losses) != 0 else None),
            "big_losses": sum(1 for p in pnls if p < -500),
            "exit_efficiency": round(sum(effs) / len(effs), 1) if effs else None,
            "avg_mae": round(sum(maes) / len(maes), 2) if maes else None,
        })
    return sorted(out, key=lambda x: -x['net_pnl'])


def _ordered(buckets, order):
    idx = {k: i for i, k in enumerate(order)}
    return sorted(buckets, key=lambda b: idx.get(b['key'], 999))


@app.get("/api/reports")
def get_reports(
    account_id: list[int] | None = Depends(account_ids),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    conn: sqlite3.Connection = Depends(get_connection),
):
    from datetime import datetime as _dt
    from collections import OrderedDict

    sql = """
        SELECT t.id, t.account_id, t.trade_group, t.ticker, t.side, t.date, t.net_pnl,
               t.instrument_type, t.option_type, t.executions, t.setup, t.setup_grade,
               t.mfe_pct, t.mae_pct, t.exit_efficiency,
               ta.strategy, ta.r_multiple, ta.emotional_state, ta.mistakes,
               ta.idea_source
        FROM trades t
        LEFT JOIN trade_analysis ta ON ta.trade_group = t.trade_group AND ta.account_id = t.account_id
        WHERE t.net_pnl IS NOT NULL AND t.net_pnl <> 0
    """
    params: list = []
    if account_id:
        sql += f" AND t.account_id IN ({in_marks(account_id)})"
        params += account_id
    if date_from:
        sql += " AND t.date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND t.date <= ?"
        params.append(date_to)
    sql += " ORDER BY t.date, t.id"

    raw = [dict(r) for r in conn.execute(sql, params).fetchall()]
    if not raw:
        return {"has_data": False}

    # Derive entry time, hold duration and exit count once per trade.
    for r in raw:
        try:
            ex = json.loads(r['executions'] or '[]')
        except Exception:
            ex = []
        # same timing rules as Leak Finder, so the hold-time tables reconcile (overnight holds included)
        tt = leaks.trade_times(ex, r['side'], r['date'])
        r['entry_min'], r['hold_min'], r['n_exits'] = tt['entry_min'], tt['hold_min'], tt['n_exits']
        try:
            r['dow'] = _dt.strptime(r['date'], '%Y-%m-%d').weekday()
        except Exception:
            r['dow'] = None

    def hold_bucket(r):
        m = r['hold_min']
        if m is None or m < 0:
            return None
        if m < 5:
            return '0-5 min'
        if m < 15:
            return '5-15 min'
        if m < 30:
            return '15-30 min'
        if m < 60:
            return '30-60 min'
        if m < 120:
            return '1-2 hrs'
        return '2+ hrs'

    def session_bucket(r):
        t = r['entry_min']
        if t is None:
            return None
        if t < 9 * 60 + 45:
            return '09:30-09:45'
        if t < 10 * 60 + 30:
            return '09:45-10:30'
        if t < 11 * 60:
            return '10:30-11:00'
        if t < 13 * 60 + 30:
            return '11:00-13:30'
        if t < 15 * 60 + 30:
            return '13:30-15:30'
        return '15:30-16:00'

    def management_bucket(r):
        if r['n_exits'] > 1:
            return 'Scaled out'
        if r['n_exits'] == 1:
            return 'All-or-nothing'
        return None

    # Equity curve and drawdown, aggregated per trading day.
    by_day = OrderedDict()
    for r in raw:
        by_day[r['date']] = by_day.get(r['date'], 0.0) + r['net_pnl']

    equity, cum, peak, max_dd, max_dd_date = [], 0.0, 0.0, 0.0, None
    for d, p in by_day.items():
        cum += p
        peak = max(peak, cum)
        dd = cum - peak
        if dd < max_dd:
            max_dd, max_dd_date = dd, d
        equity.append({"date": d, "pnl": round(p, 2),
                       "cumulative": round(cum, 2), "drawdown": round(dd, 2)})

    # Streaks over trades in chronological order.
    cur = best_win = worst_loss = 0
    for r in raw:
        if r['net_pnl'] > 0:
            cur = cur + 1 if cur > 0 else 1
            best_win = max(best_win, cur)
        else:
            cur = cur - 1 if cur < 0 else -1
            worst_loss = min(worst_loss, cur)

    # Tags per trade (strategy and source tags mirror their fields, so they are left out).
    # A trade with several tags counts once under each of them.
    tags_by_group = {}
    for tr in conn.execute(
        "SELECT DISTINCT account_id, trade_group, tag_type, tag_value FROM trade_tags "
        "WHERE tag_type NOT IN ('strategy', 'source') AND TRIM(tag_value) <> ''"
    ).fetchall():
        tags_by_group.setdefault((tr['account_id'], tr['trade_group']), []).append((tr['tag_type'], tr['tag_value']))
    by_tag = {}
    for tag_type in ('setup', 'execution', 'mistake', 'emotion', 'outcome'):
        rows_for_type = [
            dict(r, _tag=value)
            for r in raw
            for (t, value) in tags_by_group.get((r['account_id'], r['trade_group']), [])
            if t == tag_type
        ]
        if rows_for_type:
            by_tag[tag_type] = _bucket_stats(rows_for_type, lambda r: r['_tag'])

    day_pnls = list(by_day.values())
    green = [p for p in day_pnls if p > 0]
    red = [p for p in day_pnls if p < 0]

    return {
        "has_data": True,
        "trade_count": len(raw),
        "equity_curve": equity,
        "summary": {
            "net_pnl": round(sum(r['net_pnl'] for r in raw), 2),
            "max_drawdown": round(max_dd, 2),
            "max_drawdown_date": max_dd_date,
            "best_day": round(max(day_pnls), 2) if day_pnls else 0,
            "worst_day": round(min(day_pnls), 2) if day_pnls else 0,
            "trading_days": len(by_day),
            "green_days": len(green),
            "red_days": len(red),
            "avg_green_day": round(sum(green) / len(green), 2) if green else 0,
            "avg_red_day": round(sum(red) / len(red), 2) if red else 0,
            "longest_win_streak": best_win,
            "longest_loss_streak": abs(worst_loss),
            "avg_trades_per_day": round(len(raw) / len(by_day), 1) if by_day else 0,
        },
        "by_day_of_week": _ordered(
            _bucket_stats(raw, lambda r: r['dow'], lambda k: _DOW_NAMES[int(k)]),
            [str(i) for i in range(7)]),
        "by_session": _ordered(_bucket_stats(raw, session_bucket), _SESSION_ORDER),
        "by_hold_time": _ordered(_bucket_stats(raw, hold_bucket), _HOLD_ORDER),
        "by_month": sorted(_bucket_stats(raw, lambda r: r['date'][:7]),
                           key=lambda b: b['key']),
        "by_setup": _bucket_stats(raw, lambda r: r['setup'],
                                  lambda k: _SETUP_LABEL_MAP.get(k, k)),
        "by_grade": _ordered(_bucket_stats(raw, lambda r: r['setup_grade']),
                             ['A++', 'A+', 'A', 'B', 'C', 'D', 'F']),
        "by_strategy": _bucket_stats(raw, lambda r: r['strategy']),
        "by_symbol": _bucket_stats(raw, lambda r: r['ticker'])[:40],
        # Direction, not opening action: short puts and put credit spreads count as long.
        "by_side": _bucket_stats(raw, leaks.trade_direction,
                                 lambda k: {'LONG': 'Long / bullish', 'SHORT': 'Short / bearish'}.get(k, k)),
        "by_instrument": _bucket_stats(raw, lambda r: r['instrument_type']),
        "by_management": _bucket_stats(raw, management_bucket),
        "by_emotion": _bucket_stats(raw, lambda r: r['emotional_state']),
        "by_source": _bucket_stats(raw, lambda r: r['idea_source']),
        "by_tag": by_tag,
    }


# ── Leak Finder ──────────────────────────────────────────────────────────────
# backend/leaks.py holds the rules. These endpoints load the same trades the Reports
# page does (net P&L not zero, same account and date filters), so the two reconcile.

_leak_explain_lock = threading.Lock()


def _leak_rows(conn, account_id, date_from, date_to):
    sql = """
        SELECT t.id, t.account_id, t.trade_group, t.ticker, t.side, t.date, t.net_pnl,
               t.gross_pnl, t.commissions, t.instrument_type, t.option_type, t.executions, t.setup,
               t.mfe_pct, t.mae_pct, t.exit_efficiency,
               ta.strategy, ta.emotional_state, ta.idea_source
        FROM trades t
        LEFT JOIN trade_analysis ta ON ta.trade_group = t.trade_group AND ta.account_id = t.account_id
        WHERE 1=1
    """
    params: list = []
    if account_id:
        sql += f" AND t.account_id IN ({in_marks(account_id)})"
        params += account_id
    if date_from:
        sql += " AND t.date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND t.date <= ?"
        params.append(date_to)
    sql += " ORDER BY t.date, t.id"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    tags_by_group = {}
    for tr in conn.execute(
        "SELECT DISTINCT account_id, trade_group, tag_type, tag_value FROM trade_tags "
        "WHERE tag_type IN ('mistake', 'emotion') AND TRIM(tag_value) <> ''"
    ).fetchall():
        tags_by_group.setdefault((tr['account_id'], tr['trade_group']), []).append((tr['tag_type'], tr['tag_value']))
    for r in rows:
        r['tags'] = tags_by_group.get((r['account_id'], r['trade_group']), [])
        # The playbook setup name the trader tagged ('NONE' reads as 'No setup'), as in by_setup.
        r['setup_label'] = _SETUP_LABEL_MAP.get(r['setup'], r['setup']) if r['setup'] else None
    # derive over every trade so breakeven and open ones still count in the sequence, then keep
    # the ones with P&L, the same trades the Reports page shows
    return [r for r in leaks.derive(rows) if r['net_pnl']]


@app.get("/api/leaks")
def get_leaks(
    account_id: list[int] | None = Depends(account_ids),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    conn: sqlite3.Connection = Depends(get_connection),
):
    rows = _leak_rows(conn, account_id, date_from, date_to)
    if not rows:
        return {"has_data": False}
    res = leaks.find_leaks(rows)
    res.update({
        "has_data": True,
        "trade_count": len(rows),
        "net_pnl": round(sum(r['net_pnl'] for r in rows), 2),
        "cache_key": leaks.cache_key(res),
        # Outcomes, not entry decisions: shown beside the leaks, never ranked as one.
        "management": {
            "by_hold_time": _ordered(_bucket_stats(rows, leaks.hold_bucket), _HOLD_ORDER),
            "by_management": _bucket_stats(rows, leaks.management_bucket),
            "by_mistake": _bucket_stats(leaks.mistake_rows(rows), lambda r: r['mistake_tag']),
        },
        "costs": leaks.costs(rows),
        "coverage": leaks.coverage(rows),
    })
    return res


@app.get("/api/leaks/explain")
def explain_leak_findings(
    account_id: list[int] | None = Depends(account_ids),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    conn: sqlite3.Connection = Depends(get_connection),
):
    """Claude's sentence for each finding, cached by a hash of the numbers."""
    rows = _leak_rows(conn, account_id, date_from, date_to)
    if not rows:
        return {"sentences": {}}
    res = leaks.find_leaks(rows)
    key = leaks.cache_key(res)

    def cached():
        row = conn.execute("SELECT content FROM leak_explanations WHERE cache_key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    hit = cached()
    if hit is not None:
        return {"cache_key": key, "cached": True, "sentences": hit}
    payload = leaks.explain_payload(res)
    if not payload:
        return {"cache_key": key, "cached": False, "sentences": {}}

    # One generation at a time, so a page opened twice does not pay for two calls.
    with _leak_explain_lock:
        hit = cached()
        if hit is not None:
            return {"cache_key": key, "cached": True, "sentences": hit}
        try:
            sentences = explain_leaks(payload)
        except Exception as e:
            return {"cache_key": key, "cached": False, "sentences": {}, "error": str(e)}
        # Cache only a complete answer, so a partial one is retried on the next visit.
        if not all(f["id"] in sentences for f in payload):
            return {"cache_key": key, "cached": False, "sentences": sentences}
        conn.execute(
            "INSERT OR REPLACE INTO leak_explanations (cache_key, content, generated_at) VALUES (?, ?, ?)",
            (key, json.dumps(sentences), datetime.now().isoformat()),
        )
        conn.commit()
    return {"cache_key": key, "cached": False, "sentences": sentences}


@app.get("/api/edge-report")
def get_edge_report(
    account_id: list[int] | None = Depends(account_ids),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    conn: sqlite3.Connection = Depends(get_connection),
):
    sql = """
        SELECT t.trade_group, t.ticker, t.side, t.net_pnl, t.date, t.executions,
               ta.r_multiple, ta.emotional_state, ta.mistakes
        FROM trades t
        LEFT JOIN trade_analysis ta ON ta.trade_group = t.trade_group AND ta.account_id = t.account_id
        WHERE 1=1
    """
    params: list = []
    if account_id:
        sql += f" AND t.account_id IN ({in_marks(account_id)})"
        params += account_id
    if date_from:
        sql += " AND t.date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND t.date <= ?"
        params.append(date_to)
    sql += " ORDER BY t.date"

    rows = conn.execute(sql, params).fetchall()
    trades = [row_to_dict(r) for r in rows]

    # Mistake frequency from trade_tags
    tag_sql = """
        SELECT tt.tag_value, COUNT(*) as cnt
        FROM trade_tags tt
        JOIN trades t ON t.trade_group = tt.trade_group AND t.account_id = tt.account_id
        WHERE tt.tag_type = 'mistake'
    """
    tag_params: list = []
    if account_id:
        tag_sql += f" AND t.account_id IN ({in_marks(account_id)})"
        tag_params += account_id
    if date_from:
        tag_sql += " AND t.date >= ?"
        tag_params.append(date_from)
    if date_to:
        tag_sql += " AND t.date <= ?"
        tag_params.append(date_to)
    tag_sql += " GROUP BY tt.tag_value ORDER BY cnt DESC LIMIT 8"

    tag_rows = conn.execute(tag_sql, tag_params).fetchall()
    mistake_counts: dict[str, int] = {r["tag_value"]: r["cnt"] for r in tag_rows}

    # Also mine free-text mistakes field
    for trade in trades:
        text = (trade.get("mistakes") or "").strip()
        if not text:
            continue
        parts = [p.strip() for p in text.replace("\n", ",").replace(";", ",").split(",") if p.strip()]
        for part in parts:
            key = part[:60]
            if key not in mistake_counts:
                mistake_counts[key] = 1
            else:
                mistake_counts[key] += 1

    mistake_freq = sorted(
        [{"mistake": k, "count": v} for k, v in mistake_counts.items()],
        key=lambda x: -x["count"],
    )[:8]

    # 30-min time buckets 9:30 -> 15:30
    BUCKETS: list[str] = []
    t_min = 9 * 60 + 30
    while t_min < 16 * 60:
        h, m = divmod(t_min, 60)
        BUCKETS.append(f"{h:02d}:{m:02d}")
        t_min += 30

    bucket_pnl: dict[str, float] = {b: 0.0 for b in BUCKETS}
    bucket_counts: dict[str, int] = {b: 0 for b in BUCKETS}

    DOW_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    dow_pnl: dict[str, float] = {d: 0.0 for d in DOW_ORDER}
    dow_counts: dict[str, int] = {d: 0 for d in DOW_ORDER}
    DOW_NAMES = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}

    winner_hold: list[float] = []
    loser_hold: list[float] = []

    r_bucket_counts: dict[float, int] = {}
    for i in range(-7, 8):
        r_bucket_counts[round(i * 0.5, 1)] = 0

    EMOTIONS = ["calm", "anxious", "overconfident", "disciplined", "frustrated", "revenge"]
    emo_data: dict[str, dict] = {
        e: {"count": 0, "wins": 0, "total_pnl": 0.0, "r_vals": []} for e in EMOTIONS
    }

    for trade in trades:
        pnl = trade.get("net_pnl") or 0.0
        date_str = trade.get("date", "")
        side = (trade.get("side") or "LONG").upper()

        try:
            execs = json.loads(trade.get("executions") or "[]")
        except Exception:
            execs = []

        all_times = sorted([e.get("time", "") for e in execs if e.get("time")])
        entry_action = "BOT" if side == "LONG" else "SOLD"
        entry_times = sorted([e.get("time", "") for e in execs if e.get("action") == entry_action and e.get("time")])

        # Time-of-day bucket (entry time)
        if entry_times:
            try:
                parts = entry_times[0].split(":")
                h, m = int(parts[0]), int(parts[1])
                entry_mins = h * 60 + m
                bucket_floor = ((entry_mins - 9 * 60 - 30) // 30) * 30 + 9 * 60 + 30
                bh, bm = divmod(bucket_floor, 60)
                bkey = f"{bh:02d}:{bm:02d}"
                if bkey in bucket_pnl:
                    bucket_pnl[bkey] += pnl
                    bucket_counts[bkey] += 1
            except Exception:
                pass

        # Day of week
        if date_str:
            try:
                d = datetime.strptime(date_str, "%Y-%m-%d")
                dow = d.weekday()
                if dow in DOW_NAMES:
                    day_name = DOW_NAMES[dow]
                    dow_pnl[day_name] += pnl
                    dow_counts[day_name] += 1
            except Exception:
                pass

        # Hold time
        if len(all_times) >= 2:
            try:
                def to_mins(t_str: str) -> float:
                    p = t_str.split(":")
                    return int(p[0]) * 60 + int(p[1]) + (int(p[2]) / 60 if len(p) == 3 else 0)
                hold = to_mins(all_times[-1]) - to_mins(all_times[0])
                if hold >= 0:
                    if pnl > 0:
                        winner_hold.append(hold)
                    elif pnl < 0:
                        loser_hold.append(hold)
            except Exception:
                pass

        # R-multiple distribution
        r = trade.get("r_multiple")
        if r is not None:
            r_clipped = max(-3.5, min(3.5, float(r)))
            bucket_key = round(round(r_clipped * 2) / 2, 1)
            if bucket_key in r_bucket_counts:
                r_bucket_counts[bucket_key] += 1
            else:
                closest = min(r_bucket_counts.keys(), key=lambda x: abs(x - bucket_key))
                r_bucket_counts[closest] += 1

        # Emotion outcomes
        emo = (trade.get("emotional_state") or "").lower().strip()
        if emo in emo_data:
            emo_data[emo]["count"] += 1
            emo_data[emo]["total_pnl"] += pnl
            if pnl > 0:
                emo_data[emo]["wins"] += 1
            if r is not None:
                emo_data[emo]["r_vals"].append(float(r))

    time_of_day = [
        {"bucket": b, "net_pnl": round(bucket_pnl[b], 2), "trade_count": bucket_counts[b]}
        for b in BUCKETS
    ]
    day_of_week = [
        {"day": day, "net_pnl": round(dow_pnl[day], 2), "trade_count": dow_counts[day]}
        for day in DOW_ORDER
    ]
    r_multiple_dist = [
        {"bucket": str(k), "count": v}
        for k, v in sorted(r_bucket_counts.items())
    ]
    emotion_outcomes = []
    for emo in EMOTIONS:
        d = emo_data[emo]
        if d["count"] == 0:
            continue
        r_vals = d["r_vals"]
        emotion_outcomes.append({
            "state": emo,
            "trade_count": d["count"],
            "win_rate": round(d["wins"] / d["count"] * 100, 1),
            "avg_pnl": round(d["total_pnl"] / d["count"], 2),
            "avg_r": round(sum(r_vals) / len(r_vals), 2) if r_vals else None,
        })
    hold_time = {
        "winners_avg_min": round(sum(winner_hold) / len(winner_hold), 1) if winner_hold else None,
        "losers_avg_min": round(sum(loser_hold) / len(loser_hold), 1) if loser_hold else None,
    }

    # Expectancy for edge report
    all_pnl = [t.get("net_pnl") or 0 for t in trades]
    wins_er = [p for p in all_pnl if p > 0]
    losses_er = [p for p in all_pnl if p < 0]
    total_er = len(all_pnl)
    if total_er > 0 and wins_er and losses_er:
        er_expectancy = round(
            (len(wins_er) / total_er) * (sum(wins_er) / len(wins_er))
            + (len(losses_er) / total_er) * (sum(losses_er) / len(losses_er)),
            2,
        )
    else:
        er_expectancy = 0.0

    return {
        "time_of_day": time_of_day,
        "day_of_week": day_of_week,
        "r_multiple_dist": r_multiple_dist,
        "emotion_outcomes": emotion_outcomes,
        "hold_time": hold_time,
        "mistake_frequency": mistake_freq,
        "expectancy": er_expectancy,
        "total_trades": total_er,
    }


# ── AI Insights ────────────────────────────────────────────────────────────────

@app.get("/api/insights")
def get_insights(
    account_id: list[int] | None = Depends(account_ids),
    conn: sqlite3.Connection = Depends(get_connection),
):
    # Reuse KPI data as input to insights. Pass explicit None for the date
    # filters: called as a plain function, get_kpis would otherwise receive
    # truthy Query() defaults and bind them into SQL.
    kpis = get_kpis(account_id=account_id, date_from=None, date_to=None, conn=conn)

    try:
        insights_text = generate_insights(kpis)
        return {"insights": insights_text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Weekly Summary ─────────────────────────────────────────────────────────────

@app.get("/api/weekly-summary")
def get_weekly_summary(
    date: str = Query(...),
    account_id: list[int] | None = Depends(account_ids),
    force: bool = Query(False),
    conn: sqlite3.Connection = Depends(get_connection),
):
    from datetime import timedelta
    d = datetime.strptime(date, "%Y-%m-%d")
    week_start = d - timedelta(days=d.weekday())
    week_end = week_start + timedelta(days=4)
    week_label = f"{week_start.strftime('%Y')}-W{week_start.strftime('%V')}"
    week_from = week_start.strftime("%Y-%m-%d")
    week_to = week_end.strftime("%Y-%m-%d")
    cache_key, cache_acct = _summary_cache_key(f"weekly_{week_label}", account_id)

    if not force and account_id:
        cached = conn.execute(
            "SELECT ai_content FROM daily_summaries WHERE summary_date = ? AND account_id IS ?",
            (cache_key, cache_acct),
        ).fetchone()
        if cached and cached[0]:
            try:
                return json.loads(cached[0])
            except Exception:
                pass

    sql = """
        SELECT t.trade_group, t.ticker, t.side, t.instrument_type, t.option_type, t.net_pnl, t.date,
               ta.strategy, ta.r_multiple, ta.emotional_state, ta.mistakes,
               ta.entry_reason, ta.exit_reason
        FROM trades t
        LEFT JOIN trade_analysis ta ON ta.trade_group = t.trade_group AND ta.account_id = t.account_id
        WHERE t.date >= ? AND t.date <= ?
    """
    params: list = [week_from, week_to]
    if account_id:
        sql += f" AND t.account_id IN ({in_marks(account_id)})"
        params += account_id
    sql += " ORDER BY t.date, t.id"

    rows = conn.execute(sql, params).fetchall()
    trades = [row_to_dict(r) for r in rows]

    if not trades:
        return {"error": "No trades found for this week", "week_label": week_label,
                "week_from": week_from, "week_to": week_to}

    week_context = {
        "trades": trades,
        "week_label": week_label,
        "week_from": week_from,
        "week_to": week_to,
    }

    try:
        result = generate_weekly_summary(week_context)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    result["week_label"] = week_label
    result["week_from"] = week_from
    result["week_to"] = week_to

    if account_id:
        try:
            if cache_acct is None:
                # NULL never conflicts on UNIQUE, so clear the old row by hand.
                conn.execute("DELETE FROM daily_summaries WHERE summary_date = ? AND account_id IS NULL",
                             (cache_key,))
            conn.execute(
                """INSERT OR REPLACE INTO daily_summaries
                   (account_id, summary_date, ai_content, generated_at)
                   VALUES (?, ?, ?, ?)""",
                (cache_acct, cache_key, json.dumps(result), datetime.now().isoformat()),
            )
            conn.commit()
        except Exception:
            pass

    return result


# ── Daily Summary ──────────────────────────────────────────────────────────────

@app.get("/api/daily-summary")
def get_daily_summary(
    date: str = Query(...),
    account_id: list[int] | None = Depends(account_ids),
    force: bool = Query(False),
    conn: sqlite3.Connection = Depends(get_connection),
):
    cache_key, cache_acct = _summary_cache_key(date, account_id)
    # Check cache first
    if not force:
        row = conn.execute(
            "SELECT ai_content, generated_at FROM daily_summaries WHERE summary_date = ? AND (account_id = ? OR (account_id IS NULL AND ? IS NULL))",
            (cache_key, cache_acct, cache_acct)
        ).fetchone()
        if row:
            try:
                content = json.loads(row['ai_content'])
                content['date'] = date
                content['cached'] = True
                content['generated_at'] = row['generated_at']
                return content
            except Exception:
                pass

    try:
        context = build_daily_context(conn, date, account_id)
        if not context['trades']:
            return {"date": date, "cached": False, "no_trades": True, "narrative": "No trades recorded for this date."}
        summary = generate_daily_summary(context)
    except Exception as e:
        # Without an API key this is the expected path, not a server fault.
        if not os.getenv("ANTHROPIC_API_KEY"):
            return {
                "date": date,
                "cached": False,
                "unavailable": True,
                "narrative": "Add ANTHROPIC_API_KEY to backend/.env to generate a review for this day.",
            }
        raise HTTPException(status_code=500, detail=str(e))

    if cache_acct is None:
        # NULL never conflicts on UNIQUE, so clear the old row by hand.
        conn.execute("DELETE FROM daily_summaries WHERE summary_date = ? AND account_id IS NULL", (cache_key,))
    conn.execute(
        "INSERT OR REPLACE INTO daily_summaries (summary_date, account_id, ai_content, generated_at) VALUES (?, ?, ?, datetime('now'))",
        (cache_key, cache_acct, json.dumps(summary))
    )
    conn.commit()

    summary['date'] = date
    summary['cached'] = False
    return summary


# ── Brain AI Chatbot ────────────────────────────────────────────────────────────

from fastapi import Request as FastAPIRequest

@app.post("/api/brain")
async def brain_chat(
    req: FastAPIRequest,
    conn: sqlite3.Connection = Depends(get_connection),
):
    body = await req.json()
    messages = body.get("messages", [])
    raw = body.get("account_id")
    account_id = account_ids(None if raw is None else str(raw))

    if not messages:
        raise HTTPException(status_code=400, detail="No messages provided")

    try:
        context = build_brain_context(conn, account_id)
        response_text = generate_brain_response(messages, context)
        return {"response": response_text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
