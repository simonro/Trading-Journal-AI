"""Edge Trader for Schwab -> journal sync.

Edge Trader sends, on the trader's click, the raw Schwab fills since its last sync plus the
trader's journal fields, notes and timestamped events. One endpoint does both halves:

    POST /api/sync/edge-trader  {"dry_run": true, ...}   -> preview + state_token
    POST /api/sync/edge-trader  {"dry_run": false, "state_token": ..., ...}  -> applied

A dry run runs the whole Apply inside one transaction and rolls it back, so the preview is
exactly what Apply would do. Apply refuses (409, with a fresh preview) when the journal
changed after the preview. Apply backs the database up first and commits once.

Fills: `schwab_adapter` turns Schwab transactions into the Thinkorswim statement parser's
shape. A fill already stored in any of the trader's real accounts (matched exactly: date, time,
symbol, contract, side, qty, price) is not added again; a fill covered by a stored statement fill
that merged it with others (same day, symbol, side, price, quantity sum) is not added either. A
stored fill the journal split at a reversal counts once, at its original size.

Annotations (the trader's picks), events and notes are anchored to Schwab fill keys, never to
trade names: on every sync they are resolved to the trade that holds those fills now.

A journal edit wins: a field is written only when the journal value is empty, equal, or still
the value this sync (or the journal's AI) wrote last time. Anything else is a conflict shown in
the preview and left alone.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import sqlite3
import threading
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

import schwab_adapter as sa
import update_check
from csv_parser import _contract_id, _is_vertical_group, _vertical_second_strike, build_trades_from_executions
from database import DB_PATH, get_db, row_to_dict
from library import TAG_TYPES
from trade_store import IMPORT_LOCK, free_name, insert_trade, rebuilt_names, replace_regrouped_trades

SYNC_API_VERSION = 1
SOURCE = "edge-trader"
BACKUPS_KEPT = 10

TEXT_FIELDS = ("strategy", "idea_source", "emotional_state", "mistakes", "entry_reason", "exit_reason")
NUMBER_FIELDS = ("stop_loss", "target_price", "risk_per_trade", "risk_reward", "r_multiple")
FIELDS = TEXT_FIELDS + NUMBER_FIELDS
INSTRUMENTS = ("STOCK", "OPTION", "FUTURE")
EVENT_KINDS = ("entry", "exit", "partial", "stop_set", "stop_moved", "breakeven", "trail",
               "target_set", "target_moved", "target_cancelled", "stop_cancelled", "close", "note", "other")

router = APIRouter(prefix="/api/sync", tags=["sync"])


# ── Schema ─────────────────────────────────────────────────────────────────────

def init_sync_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        -- Every Schwab fill a sync has seen, and where it lives in the journal.
        CREATE TABLE IF NOT EXISTS sync_fills (
            fill_key TEXT PRIMARY KEY,
            account_id INTEGER,
            match_key TEXT NOT NULL,      -- exact key of the stored execution it is
            agg_key TEXT NOT NULL,        -- same without time and qty (merged statement fills)
            status TEXT NOT NULL,         -- new | already | covered
            payload_hash TEXT NOT NULL,
            qty REAL,                     -- the fill's size
            lead_key TEXT,                -- first fill key of its execution (a vertical has two)
            first_synced TEXT NOT NULL DEFAULT (datetime('now')),
            last_synced TEXT NOT NULL DEFAULT (datetime('now'))
        );
        -- One trader annotation (picks for one Edge Trader trade), anchored by fill keys.
        CREATE TABLE IF NOT EXISTS sync_annotations (
            id TEXT PRIMARY KEY,
            fill_keys TEXT NOT NULL,      -- JSON list
            account_id INTEGER,
            trade_group TEXT,             -- resolved on the last sync, follows regroups
            status TEXT,                  -- ok | pending | split | merge
            updated_at TEXT,
            synced_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        -- Who wrote each field last: 'sync' (an Edge Trader pick) or 'ai'.
        CREATE TABLE IF NOT EXISTS sync_field_state (
            annotation_id TEXT NOT NULL,
            field TEXT NOT NULL,
            value TEXT,                   -- JSON
            writer TEXT NOT NULL CHECK(writer IN ('sync','ai')),
            account_id INTEGER,
            trade_group TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (annotation_id, field)
        );
        -- Tags a sync added (only these are ever removed by a sync).
        CREATE TABLE IF NOT EXISTS sync_tags (
            annotation_id TEXT NOT NULL,
            tag_id INTEGER NOT NULL,
            account_id INTEGER,
            trade_group TEXT,
            tag_type TEXT NOT NULL,
            tag_value TEXT NOT NULL,
            PRIMARY KEY (annotation_id, tag_id)
        );
        -- Timestamped trade events and notes (stop moves, partials, notes ...).
        CREATE TABLE IF NOT EXISTS trade_events (
            id TEXT PRIMARY KEY,
            annotation_id TEXT,
            account_id INTEGER,
            trade_group TEXT,
            ts TEXT NOT NULL,
            kind TEXT NOT NULL,
            price REAL,
            qty REAL,
            text TEXT,
            origin TEXT NOT NULL DEFAULT 'edge',
            payload_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_events_trade ON trade_events(account_id, trade_group);
        -- The Edge Trader day note a sync last wrote, so a note deleted in the journal stays deleted.
        CREATE TABLE IF NOT EXISTS sync_day_notes (
            account_id INTEGER NOT NULL,
            entry_date TEXT NOT NULL,
            text TEXT NOT NULL,
            PRIMARY KEY (account_id, entry_date)
        );
        CREATE INDEX IF NOT EXISTS idx_annot_trade ON sync_annotations(account_id, trade_group);
    """)
    fill_cols = {r[1] for r in conn.execute("PRAGMA table_info(sync_fills)")}
    for col, kind in (("qty", "REAL"), ("lead_key", "TEXT")):
        if col not in fill_cols:
            conn.execute(f"ALTER TABLE sync_fills ADD COLUMN {col} {kind}")
    _backfill_sync_fills(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(diary_entries)")}
    if "source" not in cols:
        conn.execute("ALTER TABLE diary_entries ADD COLUMN source TEXT")
    if "updated_at" not in cols:
        conn.execute("ALTER TABLE diary_entries ADD COLUMN updated_at TEXT")
    conn.commit()


def _backfill_sync_fills(conn) -> None:
    """Rows synced before qty and lead_key existed. A new or already-stored row is one Schwab fill:
    its qty is read back from the exact key (second-to-last part) and it leads itself (for a
    vertical's two rows that counts the execution twice where it is recorded as synced, which only
    keeps it out of the merged-fill pool, never lets it cover another fill). A covered row's share of
    a merged statement fill cannot be recovered: it stays unknown, and _sync_fills never lets that
    day's merged pool cover a new fill."""
    for fill_key, match_key in conn.execute("SELECT fill_key, match_key FROM sync_fills WHERE status IN "
                                            "('new', 'already') AND (qty IS NULL OR lead_key IS NULL)").fetchall():
        try:
            qty = float(match_key.split("|")[-2])
        except (ValueError, IndexError):
            qty = None
        conn.execute("UPDATE sync_fills SET qty=COALESCE(qty, ?), lead_key=COALESCE(lead_key, ?) WHERE fill_key=?",
                     (qty, fill_key, fill_key))


def move_fills(conn, src: int, dst: int, executions_json: str, trade_row) -> None:
    """A trade moved to another account: the sync's record of its fills moves with it, so they keep
    counting as synced fills there (never as a merged statement fill that could cover a later fill)."""
    try:
        execs = json.loads(executions_json or "[]")
    except ValueError:
        return
    # A fill the journal split at a reversal is represented by its first half (StoredFills skips the
    # second): its record follows the trade holding the first half, so moving the trade with the
    # second half alone leaves it where the first half still is.
    keys = {_key_of_stored(trade_row, e) for e in execs if e.get("split_part") in (None, 1)}
    for k in keys:
        conn.execute("UPDATE sync_fills SET account_id=? WHERE account_id=? AND match_key=?", (dst, src, k))


def forget_trade(conn, account_id: int, trade_group: str) -> None:
    """A trade was deleted: its sync rows lose the anchor (the next sync re-resolves them)."""
    for table in ("sync_annotations", "trade_events", "sync_field_state"):
        conn.execute(f"UPDATE {table} SET trade_group=NULL WHERE account_id=? AND trade_group=?",
                     (account_id, trade_group))
    conn.execute("DELETE FROM sync_tags WHERE account_id=? AND trade_group=?", (account_id, trade_group))


def forget_account(conn, account_id: int) -> None:
    for table in ("sync_annotations", "trade_events", "sync_field_state", "sync_tags", "sync_fills",
                  "sync_day_notes"):
        conn.execute(f"DELETE FROM {table} WHERE account_id=?", (account_id,))


# ── Request ────────────────────────────────────────────────────────────────────

class Annotation(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    fill_keys: list[Annotated[str, Field(min_length=1, max_length=40)]] = Field(default_factory=list, max_length=500)
    fields: dict[str, Any] = Field(default_factory=dict)
    tags: dict[str, list[Annotated[str, Field(max_length=100)]]] | None = None   # None: leave tags alone
    updated_at: str | None = Field(default=None, max_length=40)

    @field_validator("fields")
    @classmethod
    def _field_sizes(cls, v):
        for k, val in v.items():
            if isinstance(val, str) and len(val) > 5000:
                raise ValueError(f"field {k} is longer than 5000 characters")
            if val is not None and not isinstance(val, (str, int, float)):
                raise ValueError(f"field {k} must be text or a number")
        return v


class Event(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    annotation_id: str | None = Field(default=None, max_length=100)
    ts: str = Field(min_length=10, max_length=40)
    kind: str
    price: float | None = None
    qty: float | None = None
    text: str | None = Field(default=None, max_length=10000)
    origin: Literal["edge", "schwab"] = "edge"


class DayNote(BaseModel):
    date: str = Field(max_length=10)
    account_id: int
    text: str = Field(max_length=50000)
    updated_at: str | None = Field(default=None, max_length=40)


class SyncRequest(BaseModel):
    dry_run: bool = True
    state_token: str | None = None
    routes: dict[str, int]                      # STOCK / OPTION / FUTURE -> journal account id
    # Per-fill account picked in the preview (fill key -> journal account id); wins over routes.
    account_overrides: dict[Annotated[str, Field(max_length=100)], int] = Field(default_factory=dict, max_length=20000)
    dedup_accounts: list[int] = Field(default_factory=list)   # real accounts to look for stored fills
    exclude_symbols: list[str] = Field(default_factory=list)
    transactions: list[dict] = Field(default_factory=list, max_length=20000)
    annotations: list[Annotation] = Field(default_factory=list, max_length=5000)
    events: list[Event] = Field(default_factory=list, max_length=50000)
    day_notes: list[DayNote] = Field(default_factory=list, max_length=1000)
    feedback: bool = True                       # AI feedback on trades with new notes or picks
    deleted_events: list[Annotated[str, Field(max_length=100)]] = Field(default_factory=list, max_length=5000)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def _num(v):
    try:
        return round(float(v), 6)
    except (TypeError, ValueError):
        return None


def _exec_key(ticker, contract, iso_date, time, action, qty, price) -> str:
    return "|".join(str(x) for x in (iso_date, time, ticker, contract, action, _num(qty), _num(price)))


def _agg_key(ticker, contract, iso_date, action, price) -> str:
    return "|".join(str(x) for x in (iso_date, ticker, contract, action, _num(price)))


def _fill_keys_of(ex: dict) -> tuple[str, str]:
    """(exact key, merged key) of a new fill. The contract carries a vertical's second strike,
    like the import's duplicate fingerprints, so two verticals sharing one strike stay apart."""
    args = (ex["ticker"], _contract_id(ex["instrument_type"], ex.get("option_expiry"), ex.get("option_strike"),
                                       ex.get("option_type"), ex.get("option_strike2")))
    return (_exec_key(*args, ex["iso_date"], ex["time"], ex["action"], ex["qty"], ex["price"]),
            _agg_key(*args, ex["iso_date"], ex["action"], ex["price"]))


def _row_args(r) -> tuple:
    """(ticker, contract) of a stored trade row; a vertical keeps its second strike in its name."""
    return (r["ticker"], _contract_id(r["instrument_type"], r["option_expiry"], r["option_strike"],
                                      r["option_type"], _vertical_second_strike(r["trade_group"])))


def _stored_keys(args, e: dict) -> tuple[str, str, int | None]:
    """(exact key, merged key, split part) of a stored execution. A fill the journal split at a
    reversal is stored as two parts that both remember the original size: their keys use it, so
    they match the one Schwab fill they came from."""
    qty, part = e.get("qty", 0), None
    if e.get("split_qty") is not None:
        qty, part = e["split_qty"], e.get("split_part")
    return (_exec_key(*args, e.get("date", ""), e.get("time", ""), e.get("action", ""), qty, e.get("price", 0)),
            _agg_key(*args, e.get("date", ""), e.get("action", ""), e.get("price", 0)), part)


class StoredFills:
    """Executions stored in the given accounts, indexed by exact and merged keys.
    Each stored execution is one item [account, group, qty_left]; consuming it by an exact
    match also removes it from the merged-fill pool.

    Only an execution no sync has accounted for can cover Schwab fills as a merged statement fill:
    one a sync stored or matched (`synced`: (account, exact key) -> count) is exactly one Schwab
    fill and never covers another fill at the same price that day."""

    def __init__(self, conn, account_ids, synced: Counter | None = None):
        self.exact: dict[str, list[list]] = defaultdict(list)
        self.agg: dict[str, list[list]] = defaultdict(list)
        synced = Counter(synced or {})
        ids = sorted(set(account_ids))
        if not ids:
            return
        rows = conn.execute(
            f"SELECT account_id, trade_group, ticker, instrument_type, option_expiry, option_strike, "
            f"option_type, executions FROM trades WHERE account_id IN ({','.join('?' * len(ids))})", ids)
        for r in rows:
            try:
                execs = json.loads(r["executions"] or "[]")
            except ValueError:
                continue
            args = _row_args(r)
            for e in execs:
                exact, agg, part = _stored_keys(args, e)
                if part not in (None, 1):
                    continue     # the second half of a split fill: its first half stands for both
                qty = e["split_qty"] if part else e.get("qty", 0)
                item = [r["account_id"], r["trade_group"], float(qty or 0)]
                self.exact[exact].append(item)
                if synced[(r["account_id"], exact)] > 0:
                    synced[(r["account_id"], exact)] -= 1
                    continue
                self.agg[agg].append(item)

    def take_exact(self, key):
        """Consume one stored copy of an exact execution; return (account, group) or None."""
        for item in self.exact.get(key, []):
            if item[2] > 0:
                item[2] = 0.0
                return item[0], item[1]
        return None

    def leftover(self, agg_key) -> list:
        return [i for i in self.agg.get(agg_key, []) if i[2] > 0]

    def consume(self, agg_key, account, qty) -> None:
        """Take what an earlier sync's covered fill used from the merged pool (its own account first)."""
        items = sorted(self.leftover(agg_key), key=lambda i: i[0] != account)
        for i in items:
            use = min(i[2], qty)
            i[2] -= use
            qty -= use
            if qty <= 1e-9:
                break


def _canonical(conn, kind: str, tag_type: str, name):
    if not isinstance(name, str):
        return name
    row = conn.execute("SELECT canonical FROM library_aliases WHERE kind=? AND tag_type=? AND LOWER(alias)=LOWER(?)",
                       (kind, tag_type, name)).fetchone()
    return row[0] if row else name


def _same(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    na, nb = _num(a), _num(b)
    if na is not None and nb is not None:
        return abs(na - nb) < 1e-6
    return str(a).strip().lower() == str(b).strip().lower()


def _utc(ts: str) -> str | None:
    """ISO time in UTC with seconds, so stored events sort as text in time order."""
    try:
        dt = datetime.fromisoformat(ts.strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.strptime(ts.strip(), "%Y-%m-%dT%H:%M:%S%z")
        except ValueError:
            return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _finite(v) -> bool:
    """None, or a number (or numeric text) that is neither NaN nor infinite."""
    if v is None:
        return True
    if isinstance(v, bool):
        return False
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def _empty(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


# ── The sync itself ────────────────────────────────────────────────────────────

class Plan:
    def __init__(self):
        self.fills = Counter()
        self.problems: list[dict] = []
        self.trades: list[dict] = []
        self.annotations: list[dict] = []
        self.events = Counter()
        self.day_notes: list[dict] = []
        self.changed_fills: list[dict] = []
        self.feedback_targets: set = set()   # (annotation_id, account_id, trade_group)

    def result(self) -> dict:
        return {
            "fills": dict(self.fills),
            "problems": self.problems,
            "changed_fills": self.changed_fills,
            "trades": sorted(self.trades, key=lambda t: (t["date"], t["account_id"], t["trade_group"])),
            "annotations": self.annotations,
            "events": dict(self.events),
            "day_notes": self.day_notes,
        }


def _validate(conn, req: SyncRequest) -> None:
    accounts = {r[0] for r in conn.execute("SELECT id FROM accounts")}
    for instr, acct in req.routes.items():
        if instr not in INSTRUMENTS:
            raise HTTPException(422, f"Unknown instrument '{instr}' in routes.")
        if acct not in accounts:
            raise HTTPException(422, f"Journal account {acct} does not exist.")
    for acct in req.dedup_accounts:
        if acct not in accounts:
            raise HTTPException(422, f"Journal account {acct} does not exist.")
    for n in req.day_notes:
        if n.account_id not in accounts:
            raise HTTPException(422, f"Journal account {n.account_id} does not exist.")
        try:
            datetime.strptime(n.date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(422, f"Day note date '{n.date}' is not YYYY-MM-DD.")
    for e in req.events:
        if e.kind not in EVENT_KINDS:
            raise HTTPException(422, f"Unknown event kind '{e.kind}'.")
    # Not-a-number and infinite values are refused before anything runs: stored, they break every
    # later answer that holds them. Checked here, not by the model, so the refusal never has to echo
    # the value back (JSON cannot carry it).
    for e in req.events:
        for name in ("price", "qty"):
            if not _finite(getattr(e, name)):
                raise HTTPException(422, f"Event {e.id}: {name} is not a finite number.")
    for a in req.annotations:
        for k, val in a.fields.items():
            if (isinstance(val, float) or (k in NUMBER_FIELDS and not _empty(val) and _num(val) is not None))                     and not _finite(val):
                raise HTTPException(422, f"Annotation {a.id}: {k} is not a finite number.")
    for t in req.transactions:
        items = t.get("transferItems") if isinstance(t, dict) else None
        for item in items if isinstance(items, list) else []:
            for key in ("amount", "price", "cost"):
                if isinstance(item, dict) and not _finite(item.get(key)):
                    raise HTTPException(422, f"Transaction {t.get('activityId')}: {key} is not a finite number.")
    by_id: dict[str, str] = {}
    for t in req.transactions:
        if t.get("activityId") is None:
            continue
        aid, h = str(t["activityId"]), _hash(t)
        if by_id.setdefault(aid, h) != h:
            raise HTTPException(422, f"Transaction {aid} appears twice with different contents.")
    for a in req.annotations:
        unknown = set(a.fields) - set(FIELDS)
        if unknown:
            raise HTTPException(422, f"Unknown field(s) {sorted(unknown)}.")
        for t in (a.tags or {}):
            if t not in TAG_TYPES:
                raise HTTPException(422, f"Unknown tag type '{t}'.")


def _group_strikes(trade_group: str) -> set:
    """Both strikes of a vertical from its name ('..._600-595_PUT_1')."""
    out = set()
    for part in trade_group.split("_")[-3].split("-"):
        try:
            out.add(round(float(part), 6))
        except ValueError:
            pass
    return out


def _open_single_legs(conn, account, ex) -> bool:
    """Does the account this vertical goes to hold either of its contracts as an open single-leg
    position, and no open vertical on the same two contracts (which the fill then belongs to)?"""
    if account is None:
        return False
    rows = conn.execute(
        "SELECT trade_group, option_strike, executions FROM trades WHERE account_id=? AND instrument_type='OPTION' "
        "AND ticker=? AND option_expiry=? AND option_type=?",
        (account, ex["ticker"], ex["option_expiry"], ex["option_type"])).fetchall()
    strikes = {round(float(ex["option_strike"]), 6), round(float(ex["option_strike2"]), 6)}
    single = False
    for r in rows:
        try:
            execs = json.loads(r["executions"] or "[]")
        except ValueError:
            continue
        held = sum((e.get("qty", 0) or 0) * (1 if e.get("action") == "BOT" else -1) for e in execs)
        if abs(held) <= 1e-6:
            continue
        if _is_vertical_group(r["trade_group"]):
            if _group_strikes(r["trade_group"]) == strikes:
                return False
        elif r["option_strike"] is not None and round(float(r["option_strike"]), 6) in strikes:
            single = True
    return single


def _keep_known_legs(conn, req: SyncRequest, execs: list[dict], kept: list[dict], seen: dict, plan: Plan) -> list[dict]:
    """A vertical stays as single legs when an earlier sync already stored one of its legs on its own
    (it saw that leg before the other one was listed), so the stored leg is recognised and not booked
    again, and when the journal holds either contract as an open single-leg position, so a closing
    vertical closes those legs instead of opening a vertical beside them. That is decided in the account
    the fill goes to (its pick, else the option route), and an open vertical there on the same two
    contracts keeps it a vertical."""
    by_key = {}
    for t in kept:
        for i in range(max(1, len(sa._legs(t)))):
            try:
                by_key[sa.fill_key(t, i)] = t
            except sa.AdapterError:
                pass
    out = []
    for ex in execs:
        mk = _fill_keys_of(ex)[0]
        known = [seen.get(fk) for fk in ex["_fill_keys"]]
        if not ex.get("option_strike2"):
            out.append(ex)
            continue
        if any(k and k["status"] in ("new", "already", "covered") and k["match_key"] != mk for k in known):
            why = "an earlier sync already stored one leg on its own"
        elif _open_single_legs(conn, next((req.account_overrides[fk] for fk in ex["_fill_keys"]
                                           if fk in req.account_overrides), req.routes.get("OPTION")), ex):
            why = "the journal holds its contracts as single-leg positions"
        else:
            out.append(ex)
            continue
        txns = [by_key[fk] for fk in ex["_fill_keys"] if fk in by_key]
        legs, problems = sa.transactions_to_executions(txns[:1])
        for t in txns[1:]:
            more, more_problems = sa.transactions_to_executions([t])
            legs += more
            problems += more_problems
        plan.problems.extend(problems)
        out.extend(legs)
        plan.problems.append({"activityId": ex["_fill_keys"][0],
                              "reason": f"{ex['ticker']} {ex['option_strike']:g}/{ex['option_strike2']:g} "
                                        f"{ex['option_type'].lower()} vertical on {ex['iso_date']} kept as separate "
                                        f"legs: {why}"})
    return out


def _sync_fills(conn, req: SyncRequest, plan: Plan) -> None:
    excluded = {s.strip().upper() for s in req.exclude_symbols if s.strip()}
    seen = {r["fill_key"]: dict(r) for r in conn.execute("SELECT * FROM sync_fills")}
    kept, payload_by_key, busted = [], {}, []
    once: set = set()
    for t in req.transactions:
        if t.get("type") != "TRADE":
            continue
        if t.get("activityId") is not None:
            # Overlapping pages repeat a transaction: the same one counts once (_validate refused
            # two different transactions under one id).
            if str(t["activityId"]) in once:
                continue
            once.add(str(t["activityId"]))
        sym = sa.symbol_of(t)
        if sym and any(sym == x or (x.startswith("/") and sym.startswith(x)) for x in excluded):
            plan.fills["excluded"] += len(sa._legs(t)) or 1
            continue
        keys = []
        for i in range(max(1, len(sa._legs(t)))):
            try:
                keys.append(sa.fill_key(t, i))
            except sa.AdapterError:
                pass
        if t.get("status") not in (None, "VALID"):
            # Busted or cancelled at Schwab: report a fill the journal already holds, once.
            for fk in keys:
                prev = seen.get(fk)
                if prev and prev["status"] != "busted":
                    plan.changed_fills.append({"fill_key": fk, "reason": f"Schwab now reports it {t.get('status')}",
                                               "time": t.get("time")})
                    busted.append((fk, prev))
            continue
        kept.append(t)
        for fk in keys:
            payload_by_key[fk] = _hash(t)
    execs, problems = sa.transactions_to_executions(kept)
    plan.problems.extend(problems)
    execs = _keep_known_legs(conn, req, execs, kept, seen, plan)

    # A pick for an account that no longer exists falls back to the route and is reported, so an old
    # pick can never block every sync.
    accounts = {r[0] for r in conn.execute("SELECT id FROM accounts")}
    picks = {k: v for k, v in req.account_overrides.items() if v in accounts}
    for acct in sorted(set(req.account_overrides.values()) - accounts):
        plan.problems.append({"reason": f"a trade was picked for journal account {acct}, which no longer exists; "
                                        f"its fills go to their default account"})
    synced = Counter((r["account_id"], r["match_key"]) for r in seen.values()
                     if r["status"] in ("new", "already") and r["lead_key"] in (None, r["fill_key"]))
    stored = StoredFills(conn, set(req.dedup_accounts) | set(req.routes.values()) | set(picks.values()), synced)
    in_request = {fk for ex in execs for fk in ex["_fill_keys"]}
    for r in seen.values():
        # A fill an earlier sync found covered by a merged statement fill keeps its share of it.
        if (r["status"] == "covered" and r["qty"] and r["lead_key"] in (None, r["fill_key"])
                and r["fill_key"] not in in_request):
            stored.consume(r["agg_key"], r["account_id"], float(r["qty"]))

    candidates = []
    records = []   # (fill_keys, account, match_key, agg_key, status, qty)
    for ex in execs:
        account = next((picks[fk] for fk in ex["_fill_keys"] if fk in picks),
                       req.routes.get(ex["instrument_type"]))
        if account is None:
            plan.fills["no_route"] += 1
            plan.problems.append({"activityId": ex["_fill_keys"][0],
                                  "reason": f"no journal account chosen for {ex['instrument_type'].lower()}"})
            continue
        mk, ak = _fill_keys_of(ex)
        prevs = [seen.get(fk) for fk in ex["_fill_keys"]]
        for fk, prev in zip(ex["_fill_keys"], prevs):
            if prev and prev["payload_hash"] and prev["payload_hash"] != payload_by_key.get(fk, prev["payload_hash"]):
                plan.changed_fills.append({"fill_key": fk, "symbol": ex["ticker"], "date": ex["iso_date"],
                                           "time": ex["time"], "reason": "Schwab changed this fill after it was "
                                           "synced; the journal keeps what it has"})
        hit = stored.take_exact(mk)
        if hit:
            plan.fills["already"] += 1
            records.append((ex["_fill_keys"], hit[0], mk, ak, "already", ex["qty"]))
        elif all(p and p["status"] in ("new", "already") for p in prevs):
            # Synced before and no longer in the journal: the trader deleted or edited it there.
            plan.fills["removed_in_journal"] += 1
            plan.problems.append({"activityId": ex["_fill_keys"][0],
                                  "reason": f"{ex['ticker']} {ex['action']} {ex['qty']} @ {ex['price']} on "
                                            f"{ex['iso_date']} {ex['time']} was removed or edited in the journal; "
                                            f"not added again"})
        else:
            candidates.append((ex, account, mk, ak))

    # Statement fills that merged several Schwab fills (an early export's Trade History): their
    # quantity covers Schwab fills in time order; what is left over is new.
    by_agg = defaultdict(list)
    for c in candidates:
        by_agg[c[3]].append(c)
    # Covered by a merged statement fill in a sync before this version: how much of it they used is
    # unknown, so that day's merged pool covers no new fill (each is added as new and named).
    unknown = {r["agg_key"] for r in seen.values() if r["status"] == "covered" and r["fill_key"] not in in_request
               and (r["qty"] is None or r["lead_key"] is None)}
    new_by_account = defaultdict(list)
    for ak, group in by_agg.items():
        left = stored.leftover(ak)
        for ex, account, mk, ak2 in sorted(group, key=lambda c: (c[0]["iso_date"], c[0]["time"])):
            qty = float(ex["qty"])
            if ak in unknown:
                prevs = [seen.get(fk) for fk in ex["_fill_keys"]]
                if all(p and p["status"] == "covered" for p in prevs):
                    plan.fills["covered"] += 1
                    records.append((ex["_fill_keys"], prevs[0]["account_id"], mk, ak2, "covered", ex["qty"]))
                    continue
                plan.fills["new"] += 1
                new_by_account[account].append(ex)
                records.append((ex["_fill_keys"], account, mk, ak2, "new", ex["qty"]))
                plan.problems.append({"activityId": ex["_fill_keys"][0],
                                      "reason": f"{ex['ticker']} {ex['action']} {ex['qty']} @ {ex['price']} on "
                                                f"{ex['iso_date']} {ex['time']} added as new: a merged statement fill "
                                                f"at this price was matched by an older sync, so how much of it is "
                                                f"left is unknown. Check this day in the journal for a duplicate."})
                continue
            avail = sum(i[2] for i in left)
            if avail + 1e-9 >= qty:
                need = qty
                owners = set()
                for i in left:
                    use = min(i[2], need)
                    if use > 0:
                        owners.add(i[0])
                    i[2] -= use
                    need -= use
                    if need <= 1e-9:
                        break
                left = [i for i in left if i[2] > 1e-9]
                plan.fills["covered"] += 1
                # The account holding the statement fill, not the route; none when it spans accounts.
                owner = next(iter(owners)) if len(owners) == 1 else None
                records.append((ex["_fill_keys"], owner, mk, ak2, "covered", ex["qty"]))
            elif avail > 1e-9:
                for i in left:
                    i[2] = 0.0
                left = []
                plan.fills["partly_covered"] += 1
                plan.problems.append({"activityId": ex["_fill_keys"][0],
                                      "reason": f"{ex['ticker']} {ex['action']} {ex['qty']} @ {ex['price']} on "
                                                f"{ex['iso_date']} is partly covered by a merged statement fill "
                                                f"({avail:g} of {qty:g}); not added. Check this day in the journal."})
            else:
                plan.fills["new"] += 1
                new_by_account[account].append(ex)
                records.append((ex["_fill_keys"], account, mk, ak2, "new", ex["qty"]))

    for account, fills in new_by_account.items():
        keys_of = defaultdict(list)       # exact execution key -> the new fills' keys, so the preview can name them
        for f in fills:
            keys_of[_fill_keys_of(f)[0]].extend(f["_fill_keys"])
        clean = [{k: v for k, v in f.items() if not k.startswith("_")} for f in fills]
        before = {r["trade_group"]: r["executions"] for r in conn.execute(
            "SELECT trade_group, executions FROM trades WHERE account_id=?", (account,))}
        trades, _ = build_trades_from_executions(clean, account, conn, commit=False, dedup=False)
        replace_regrouped_trades(conn, account, trades)
        taken: set = set()
        rebuilt = rebuilt_names(trades)
        for t in trades:
            original = t["trade_group"]
            t["trade_group"] = free_name(conn, account, t, taken, rebuilt)
            if t["trade_group"] != original:
                plan.problems.append({"trade_group": original,
                                      "reason": f"kept apart from the stored trade {original} (not rebuilt: a "
                                                f"vertical or a trade edited in the journal); saved as "
                                                f"{t['trade_group']}"})
            existed = conn.execute("SELECT 1 FROM trades WHERE trade_group=? AND account_id=?",
                                   (t["trade_group"], account)).fetchone() is not None
            insert_trade(conn, t)
            args = _row_args(t)
            try:
                execs = json.loads(t.get("executions") or "[]")
            except ValueError:
                execs = []
            fks = sorted({fk for e in execs for fk in keys_of.get(_stored_keys(args, e)[0], [])})
            plan.trades.append({"account_id": account, "trade_group": t["trade_group"], "ticker": t["ticker"],
                                "date": t["date"], "instrument_type": t["instrument_type"], "side": t["side"],
                                "status": "updated" if existed or t.get("replaces") else "new",
                                "net_pnl": t["net_pnl"], "fill_keys": fks})
        # The builder also closes a stored open vertical in place (no built trade for it): every trade
        # this Apply changes is in the preview.
        listed = {t["trade_group"] for t in trades}
        for r in conn.execute("SELECT * FROM trades WHERE account_id=?", (account,)).fetchall():
            if r["trade_group"] in listed or before.get(r["trade_group"]) == r["executions"]:
                continue
            try:
                execs = json.loads(r["executions"] or "[]")
            except ValueError:
                execs = []
            args = _row_args(r)
            fks = sorted({fk for e in execs for fk in keys_of.get(_stored_keys(args, e)[0], [])})
            plan.trades.append({"account_id": account, "trade_group": r["trade_group"], "ticker": r["ticker"],
                                "date": r["date"], "instrument_type": r["instrument_type"], "side": r["side"],
                                "status": "updated" if r["trade_group"] in before else "new",
                                "net_pnl": r["net_pnl"], "fill_keys": fks})

    for keys, account, mk, ak, status, qty in records:
        for fk in keys:
            conn.execute("""
                INSERT INTO sync_fills (fill_key, account_id, match_key, agg_key, status, payload_hash, qty, lead_key)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(fill_key) DO UPDATE SET account_id=excluded.account_id, match_key=excluded.match_key,
                    agg_key=excluded.agg_key, payload_hash=excluded.payload_hash, qty=excluded.qty,
                    lead_key=excluded.lead_key, last_synced=datetime('now')
            """, (fk, account, mk, ak, status, payload_by_key.get(fk, ""), qty, keys[0]))
    for fk, _prev in busted:
        conn.execute("UPDATE sync_fills SET status='busted', last_synced=datetime('now') WHERE fill_key=?", (fk,))


def _key_of_stored(row, e) -> str:
    return _stored_keys(_row_args(row), e)[0]


class TradeIndex:
    """Find the trade that holds a synced fill now (after any regroup)."""

    def __init__(self, conn):
        self.conn = conn
        self.cache: dict[int, tuple[dict, dict, dict]] = {}

    def _load(self, account_id):
        if account_id not in self.cache:
            exact, split, agg = {}, {}, {}
            for r in self.conn.execute("SELECT * FROM trades WHERE account_id=?", (account_id,)):
                try:
                    execs = json.loads(r["executions"] or "[]")
                except ValueError:
                    continue
                args = _row_args(r)
                for e in execs:
                    k, ak, part = _stored_keys(args, e)
                    if part is None:
                        exact.setdefault(k, r["trade_group"])
                    else:        # a reversal fill: [the trade it closed, the trade it opened]
                        split.setdefault(k, [None, None])[0 if part == 1 else 1] = r["trade_group"]
                    agg.setdefault(ak, r["trade_group"])
            self.cache[account_id] = (exact, split, agg)
        return self.cache[account_id]

    def trades_of(self, fill_row) -> list[tuple[int, str]]:
        """The trade holding the fill; two for a fill the journal split at a reversal (the trade
        it closed first, then the one it opened)."""
        if not fill_row or fill_row["account_id"] is None:
            return []
        exact, split, agg = self._load(fill_row["account_id"])
        acct = fill_row["account_id"]
        g = exact.get(fill_row["match_key"])
        if g:
            return [(acct, g)]
        parts = [x for x in split.get(fill_row["match_key"]) or [] if x]
        if parts:
            return [(acct, x) for x in dict.fromkeys(parts)]
        g = agg.get(fill_row["agg_key"])
        return [(acct, g)] if g else []


def _resolve_annotations(conn, req: SyncRequest, plan: Plan):
    """Returns (target for fields and tags, anchor for events and notes). They differ only for the
    losers of a merge: their picks are not applied, but their events still belong to the trade."""
    fills = {r["fill_key"]: r for r in conn.execute("SELECT * FROM sync_fills")}
    index = TradeIndex(conn)
    resolved: dict[str, tuple[int, str] | None] = {}
    info: dict[str, dict] = {}
    for a in req.annotations:
        trades, shared, missing = [], [], []
        for fk in a.fill_keys:
            held = index.trades_of(fills.get(fk))
            if not held:
                missing.append(fk)
            elif len(held) > 1:
                shared.append(held)
            elif held[0] not in trades:
                trades.append(held[0])
        if not trades and shared:
            # Only reversal fills anchor it: the trade they all share, else the one the first opened
            # (the annotation of the trade a reversal closed also holds that trade's earlier fills).
            common = [t for t in shared[0] if all(t in s for s in shared[1:])]
            trades = [common[-1] if common else shared[0][-1]]
        if not trades:
            status = "pending"
            target = None
        else:
            status = "split" if len(trades) > 1 else "ok"
            target = trades[0]   # the trade holding the earliest anchored fill
        resolved[a.id] = target
        info[a.id] = {"id": a.id, "status": status, "missing_fills": len(missing),
                      "trades": [{"account_id": x[0], "trade_group": x[1]} for x in trades]}
    anchor = dict(resolved)
    # Two annotations on one journal trade (a flip the journal keeps as one trade): the most
    # recently edited one's picks are applied, the others' picks are reported and left out.
    by_trade = defaultdict(list)
    for a in req.annotations:
        if resolved.get(a.id):
            by_trade[resolved[a.id]].append(a)
    for trade, group in by_trade.items():
        if len(group) > 1:
            group.sort(key=lambda a: a.updated_at or "", reverse=True)
            for loser in group[1:]:
                info[loser.id]["status"] = "merge"
                info[loser.id]["applied_instead"] = group[0].id
                resolved[loser.id] = None
    for a in req.annotations:
        target = anchor.get(a.id)
        known = conn.execute("SELECT 1 FROM sync_annotations WHERE id=?", (a.id,)).fetchone()
        values = (json.dumps(a.fill_keys), target[0] if target else None, target[1] if target else None,
                  info[a.id]["status"], a.updated_at)
        if known:
            conn.execute("UPDATE sync_annotations SET fill_keys=?, account_id=?, trade_group=?, status=?, "
                         "updated_at=?, synced_at=datetime('now') WHERE id=?", values + (a.id,))
        else:
            conn.execute("INSERT INTO sync_annotations (fill_keys, account_id, trade_group, status, updated_at, id) "
                         "VALUES (?,?,?,?,?,?)", values + (a.id,))
    plan.annotations = [info[a.id] for a in req.annotations]
    return resolved, anchor


def _apply_fields(conn, req: SyncRequest, resolved, plan: Plan) -> None:
    info = {i["id"]: i for i in plan.annotations}
    for a in req.annotations:
        target = resolved.get(a.id)
        out = info[a.id]
        out["fields"], out["tags"] = [], {"add": [], "remove": [], "kept": []}
        if not target:
            continue
        account_id, group = target
        trade = conn.execute("SELECT ticker, date FROM trades WHERE trade_group=? AND account_id=?",
                             (group, account_id)).fetchone()
        if not trade:
            continue
        row = conn.execute("SELECT * FROM trade_analysis WHERE trade_group=? AND account_id=?",
                           (group, account_id)).fetchone()
        current = dict(row) if row else {}
        writes = {}
        for field, edge_value in a.fields.items():
            if _empty(edge_value):
                continue   # a blank pick never clears a journal value
            if field in NUMBER_FIELDS:
                if _num(edge_value) is None:
                    raise HTTPException(422, f"Field {field} must be a number.")
                edge_value = _num(edge_value)
            kind = {"strategy": "strategy", "idea_source": "source"}.get(field)
            if kind:
                edge_value = _canonical(conn, kind, "", edge_value)
            journal = current.get(field)
            state = conn.execute("SELECT value, writer FROM sync_field_state WHERE annotation_id=? AND field=?",
                                 (a.id, field)).fetchone()
            last = json.loads(state["value"]) if state and state["value"] is not None else None
            if kind and last is not None:
                last = _canonical(conn, kind, "", last)
            if _same(journal, edge_value):
                status = "same"
            elif _empty(journal) and state and not _empty(last):
                status = "cleared_in_journal"     # the trader cleared what a sync (or the AI) wrote
            elif _empty(journal) or (state and _same(journal, last)):
                status = "write"
                writes[field] = edge_value
            else:
                status = "conflict"
            out["fields"].append({"field": field, "status": status, "journal": journal, "edge": edge_value})
            if status in ("write", "same"):
                conn.execute("""
                    INSERT INTO sync_field_state (annotation_id, field, value, writer, account_id, trade_group)
                    VALUES (?,?,?,'sync',?,?)
                    ON CONFLICT(annotation_id, field) DO UPDATE SET value=excluded.value, writer='sync',
                        account_id=excluded.account_id, trade_group=excluded.trade_group, updated_at=datetime('now')
                """, (a.id, field, json.dumps(edge_value), account_id, group))
        if writes:
            plan.feedback_targets.add((a.id, account_id, group))
            if not row:
                conn.execute("INSERT INTO trade_analysis (trade_group, account_id, ticker, date, match_confidence) "
                             "VALUES (?,?,?,?,'manual')", (group, account_id, trade["ticker"], trade["date"]))
            sets = ", ".join(f"{k}=?" for k in writes)
            conn.execute(f"UPDATE trade_analysis SET {sets} WHERE trade_group=? AND account_id=?",
                         list(writes.values()) + [group, account_id])
        if a.tags is not None:
            _apply_tags(conn, a, account_id, group, out["tags"])


def _apply_tags(conn, a: Annotation, account_id: int, group: str, out: dict) -> None:
    wanted = set()
    for tag_type, values in a.tags.items():
        for v in values:
            if isinstance(v, str) and v.strip():
                wanted.add((tag_type, _canonical(conn, "tag", tag_type, v.strip())))
    have = {(r["tag_type"], r["tag_value"].lower()): r["id"] for r in conn.execute(
        "SELECT id, tag_type, tag_value FROM trade_tags WHERE trade_group=? AND account_id=?", (group, account_id))}
    ours = {r["tag_id"]: (r["tag_type"], r["tag_value"]) for r in conn.execute(
        "SELECT tag_id, tag_type, tag_value FROM sync_tags WHERE annotation_id=?", (a.id,))}
    alive = {r[0] for r in conn.execute("SELECT id FROM trade_tags WHERE id IN (%s)" % ",".join("?" * len(ours)),
                                        list(ours))} if ours else set()
    removed_by_trader = {(t, v.lower()) for tid, (t, v) in ours.items() if tid not in alive}
    out.setdefault("removed_in_journal", [])
    for tag_type, value in sorted(wanted):
        tag_id = have.get((tag_type, value.lower()))
        if tag_id is None and (tag_type, value.lower()) in removed_by_trader:
            out["removed_in_journal"].append({"type": tag_type, "value": value})
            continue
        if tag_id is None:
            tag_id = conn.execute("INSERT INTO trade_tags (trade_group, account_id, tag_type, tag_value, source) "
                                  "VALUES (?,?,?,?,'manual')", (group, account_id, tag_type, value)).lastrowid
            conn.execute("INSERT OR REPLACE INTO sync_tags (annotation_id, tag_id, account_id, trade_group, tag_type, "
                         "tag_value) VALUES (?,?,?,?,?,?)", (a.id, tag_id, account_id, group, tag_type, value))
            out["add"].append({"type": tag_type, "value": value})
        else:
            out["kept"].append({"type": tag_type, "value": value})
    wanted_lower = {(t, v.lower()) for t, v in wanted}
    for tag_id, (tag_type, value) in ours.items():
        if (tag_type, value.lower()) in wanted_lower:
            continue
        gone = conn.execute("DELETE FROM trade_tags WHERE id=?", (tag_id,)).rowcount if tag_id in alive else 0
        conn.execute("DELETE FROM sync_tags WHERE annotation_id=? AND tag_id=?", (a.id, tag_id))
        if gone:
            out["remove"].append({"type": tag_type, "value": value})


def _apply_events(conn, req: SyncRequest, resolved, plan: Plan) -> None:
    for event_id in req.deleted_events:
        # Only notes Edge Trader wrote: a note the trader deleted there.
        if conn.execute("DELETE FROM trade_events WHERE id=? AND origin='edge' AND kind='note'", (event_id,)).rowcount:
            plan.events["deleted"] += 1
    for e in req.events:
        target = resolved.get(e.annotation_id) if e.annotation_id else None
        if e.annotation_id and target is None:
            row = conn.execute("SELECT account_id, trade_group FROM sync_annotations WHERE id=?",
                               (e.annotation_id,)).fetchone()
            target = (row["account_id"], row["trade_group"]) if row and row["trade_group"] else None
        ts = _utc(e.ts)
        if ts is None:
            raise HTTPException(422, f"Event {e.id} has an unreadable time '{e.ts}'.")
        e.ts = ts
        payload = e.model_dump()
        h = _hash(payload)
        prev = conn.execute("SELECT payload_hash FROM trade_events WHERE id=?", (e.id,)).fetchone()
        acct, group = (target if target else (None, None))
        if prev and prev["payload_hash"] == h:
            if target is not None:      # never detach an event whose trade is just unknown right now
                conn.execute("UPDATE trade_events SET account_id=?, trade_group=? WHERE id=?", (acct, group, e.id))
            plan.events["same"] += 1
            continue
        conn.execute("""
            INSERT INTO trade_events (id, annotation_id, account_id, trade_group, ts, kind, price, qty, text, origin,
                                      payload_hash)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET annotation_id=excluded.annotation_id, account_id=excluded.account_id,
                trade_group=excluded.trade_group, ts=excluded.ts, kind=excluded.kind, price=excluded.price,
                qty=excluded.qty, text=excluded.text, origin=excluded.origin, payload_hash=excluded.payload_hash
        """, (e.id, e.annotation_id, acct, group, e.ts, e.kind, e.price, e.qty, e.text, e.origin, h))
        plan.events["updated" if prev else "new"] += 1
        if target is not None and e.kind == "note" and e.annotation_id:
            plan.feedback_targets.add((e.annotation_id, target[0], target[1]))
        if target is None:
            plan.events["pending"] += 1


def _apply_day_notes(conn, req: SyncRequest, plan: Plan) -> None:
    for n in req.day_notes:
        out = {"date": n.date, "account_id": n.account_id}
        row = conn.execute("SELECT id, raw_text FROM diary_entries WHERE account_id=? AND entry_date=? AND source=?",
                           (n.account_id, n.date, SOURCE)).fetchone()
        last = conn.execute("SELECT text FROM sync_day_notes WHERE account_id=? AND entry_date=?",
                            (n.account_id, n.date)).fetchone()
        if row and (row["raw_text"] or "") == n.text:
            out["status"] = "same"
        elif row:
            conn.execute("UPDATE diary_entries SET raw_text=?, updated_at=datetime('now') WHERE id=?",
                         (n.text, row["id"]))
            out["status"] = "updated"
        elif last and last["text"] == n.text:
            out["status"] = "removed_in_journal"       # deleted there, unchanged here: stays deleted
        elif n.text.strip():
            conn.execute("INSERT INTO diary_entries (account_id, entry_date, raw_text, source, updated_at) "
                         "VALUES (?,?,?,?,datetime('now'))", (n.account_id, n.date, n.text, SOURCE))
            out["status"] = "new"
        else:
            continue
        plan.day_notes.append(out)
        if out["status"] in ("same", "updated", "new"):
            conn.execute("INSERT INTO sync_day_notes (account_id, entry_date, text) VALUES (?,?,?) "
                         "ON CONFLICT(account_id, entry_date) DO UPDATE SET text=excluded.text",
                         (n.account_id, n.date, n.text))


def run_sync(conn, req: SyncRequest) -> dict:
    """The whole sync inside the caller's transaction. Returns the plan."""
    _validate(conn, req)
    plan = Plan()
    _sync_fills(conn, req, plan)
    resolved, anchor = _resolve_annotations(conn, req, plan)
    _apply_fields(conn, req, resolved, plan)
    _apply_events(conn, req, anchor, plan)
    _apply_day_notes(conn, req, plan)
    out = plan.result()
    out["_feedback_targets"] = sorted(plan.feedback_targets)
    return out


# Every table the plan reads or writes. Any change to them between a preview and its Apply refuses the
# Apply (409), whatever the summary shows.
STATE_TABLES = ("accounts", "trades", "trade_analysis", "trade_tags", "diary_entries", "library_aliases",
                "sync_fills", "sync_annotations", "sync_field_state", "sync_tags", "trade_events", "sync_day_notes")


def _state_digest(conn) -> str:
    h = hashlib.sha256()
    for table in STATE_TABLES:
        h.update(f"\n#{table}\n".encode())
        for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid"):
            h.update(repr(tuple(row)).encode())
            h.update(b"\n")
    return h.hexdigest()


def _request_digest(req: SyncRequest) -> str:
    """The request as sent, without dry_run and state_token: Apply must send exactly the preview's."""
    return _hash(req.model_dump(mode="json", exclude={"dry_run", "state_token"}))


def _backup(conn) -> str:
    db = Path(DB_PATH)
    folder = db.resolve().parent / "sync-backups"
    folder.mkdir(exist_ok=True)
    dst_path = folder / f"{db.stem}.presync-{datetime.now():%Y%m%d-%H%M%S-%f}.db"
    dst = sqlite3.connect(dst_path)
    try:
        conn.backup(dst)
    finally:
        dst.close()
    old = sorted(folder.glob(f"{db.stem}.presync-*.db"))
    for p in old[:-BACKUPS_KEPT]:
        try:
            p.unlink()
        except OSError:
            pass
    return dst_path.name


def _connection():
    conn = get_db()
    try:
        yield conn
    finally:
        conn.close()


# ── AI feedback (after an Apply, in the background) ────────────────────────────

AI_FILL_FIELDS = ("entry_reason", "exit_reason", "mistakes", "emotional_state")
FEEDBACK_BATCH = 15
log = logging.getLogger("edge_sync")

FEEDBACK_PROMPT = """You are a trading coach reviewing day trades a trader logged live in Edge Trader.
For each trade you get the fills, the stop and target moves, the trader's own notes and the fields
the trader already filled in. Give short, specific feedback (2 to 4 sentences) on entries, exits,
stop management and discipline, grounded only in the data given. Then, ONLY for the fields listed in
"blank_fields", suggest a value when the notes clearly support one; leave a field out otherwise.
Never contradict or rewrite what the trader filled in. Use plain language and never use em dashes.
Return ONLY JSON: {"trades": [{"key": "<key>", "ai_feedback": "...", "fill": {"<field>": "..."}}]}"""


def _feedback_context(conn, annotation_id, account_id, group):
    trade = conn.execute("SELECT * FROM trades WHERE trade_group=? AND account_id=?", (group, account_id)).fetchone()
    if not trade:
        return None
    analysis = conn.execute("SELECT * FROM trade_analysis WHERE trade_group=? AND account_id=?",
                            (group, account_id)).fetchone()
    analysis = dict(analysis) if analysis else {}
    events = [dict(r) for r in conn.execute(
        "SELECT ts, kind, price, text FROM trade_events WHERE account_id=? AND trade_group=? ORDER BY ts",
        (account_id, group))]
    filled = {k: analysis.get(k) for k in FIELDS if not _empty(analysis.get(k))}
    return {"key": f"{account_id}|{group}", "annotation_id": annotation_id, "_executions": trade["executions"],
            "ticker": trade["ticker"],
            "date": trade["date"], "side": trade["side"], "instrument": trade["instrument_type"],
            "net_pnl": trade["net_pnl"], "executions": json.loads(trade["executions"] or "[]"),
            "events": events, "filled_by_trader": filled,
            "blank_fields": [f for f in AI_FILL_FIELDS if f not in filled]}


def ask_ai(contexts: list[dict]) -> dict:
    """One model call for a batch of trades. Returns {key: {"ai_feedback", "fill"}}."""
    from ai_analysis import MODEL, get_client, response_text
    client = get_client()
    payload = [{k: v for k, v in c.items() if k not in ("annotation_id", "_executions")} for c in contexts]
    response = client.messages.create(model=MODEL, max_tokens=8000, system=FEEDBACK_PROMPT,
                                      messages=[{"role": "user", "content": json.dumps(payload, default=str)}])
    text = response_text(response).strip()
    data = json.loads(text[text.find("{"): text.rfind("}") + 1])
    return {t["key"]: t for t in data.get("trades") or [] if isinstance(t, dict) and t.get("key")}


def run_feedback(targets) -> int:
    """Write AI feedback and blank fields for the given trades. Returns how many trades got feedback."""
    conn = get_db()
    written = 0
    try:
        contexts = [c for c in (_feedback_context(conn, *t) for t in targets) if c]
        for i in range(0, len(contexts), FEEDBACK_BATCH):
            batch = contexts[i:i + FEEDBACK_BATCH]
            try:
                answers = ask_ai(batch)
            except Exception as exc:          # an AI outage never touches the synced data
                log.warning("AI feedback failed: %s", exc)
                continue
            with IMPORT_LOCK:
                for c in batch:
                    ans = answers.get(c["key"])
                    if not isinstance(ans, dict):
                        continue
                    account_id, group = c["key"].split("|", 1)
                    account_id = int(account_id)
                    trade = conn.execute("SELECT ticker, date, executions FROM trades WHERE trade_group=? "
                                         "AND account_id=?", (group, account_id)).fetchone()
                    if not trade or trade["executions"] != c["_executions"]:
                        continue      # regrouped while the AI was thinking: this name is another trade now
                    if not conn.execute("SELECT 1 FROM trade_analysis WHERE trade_group=? AND account_id=?",
                                        (group, account_id)).fetchone():
                        conn.execute("INSERT INTO trade_analysis (trade_group, account_id, ticker, date, "
                                     "match_confidence) VALUES (?,?,?,?,'manual')",
                                     (group, account_id, trade["ticker"], trade["date"]))
                    if isinstance(ans.get("ai_feedback"), str) and ans["ai_feedback"].strip():
                        conn.execute("UPDATE trade_analysis SET ai_feedback=? WHERE trade_group=? AND account_id=?",
                                     (ans["ai_feedback"].strip()[:4000], group, account_id))
                    for field, value in (ans.get("fill") or {}).items():
                        if field not in c["blank_fields"] or not isinstance(value, str) or not value.strip():
                            continue
                        # Only while still blank (one statement): the trader may have filled it meanwhile.
                        value = value.strip()[:2000]
                        done = conn.execute(f"UPDATE trade_analysis SET {field}=? WHERE trade_group=? AND "
                                            f"account_id=? AND ({field} IS NULL OR TRIM({field})='')",
                                            (value, group, account_id)).rowcount
                        if not done:
                            continue
                        conn.execute("""
                            INSERT INTO sync_field_state (annotation_id, field, value, writer, account_id, trade_group)
                            VALUES (?,?,?,'ai',?,?)
                            ON CONFLICT(annotation_id, field) DO UPDATE SET value=excluded.value, writer='ai',
                                account_id=excluded.account_id, trade_group=excluded.trade_group,
                                updated_at=datetime('now')
                        """, (c["annotation_id"], field, json.dumps(value), account_id, group))
                    written += 1
                conn.commit()
    finally:
        conn.close()
    return written


def start_feedback(targets) -> None:
    threading.Thread(target=run_feedback, args=(list(targets),), daemon=True, name="edge-sync-feedback").start()


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/info")
def sync_info():
    version = update_check.current_version()
    return {"api": "edge-trader-sync", "api_version": SYNC_API_VERSION, "journal_version": version,
            "fields": list(FIELDS), "tag_types": list(TAG_TYPES), "event_kinds": list(EVENT_KINDS)}


def _from_a_browser(request: Request) -> bool:
    """Edge Trader posts from its own server (urllib: no Origin, no Sec-Fetch headers). A web page
    always sends Origin on a POST, so any page, even one served from localhost, is turned away."""
    site = request.headers.get("sec-fetch-site")
    return request.headers.get("origin") is not None or (site is not None and site != "none")


@router.post("/edge-trader")
def sync_edge_trader(req: SyncRequest, request: Request, conn: sqlite3.Connection = Depends(_connection)):
    if _from_a_browser(request):
        raise HTTPException(403, "The sync accepts requests from Edge Trader's server only, not from a web page.")
    if not req.dry_run and not req.state_token:
        raise HTTPException(422, "Apply needs the state_token of a preview (send dry_run true first).")
    request_digest = _request_digest(req)

    def attempt():
        conn.execute("BEGIN IMMEDIATE")
        try:
            state = _state_digest(conn)
            result = run_sync(conn, req.model_copy(deep=True))
        except Exception:
            conn.rollback()
            raise
        targets = result.pop("_feedback_targets")
        return result, targets, _hash({"request": request_digest, "state": state, "result": result})

    with IMPORT_LOCK:
        result, targets, token = attempt()
        conn.rollback()
        if req.dry_run:
            return {"dry_run": True, "applied": False, "state_token": token, **result}
        if req.state_token != token:
            return JSONResponse(status_code=409, content={
                "detail": "The journal changed since the preview. Review the new preview.",
                "dry_run": True, "applied": False, "state_token": token, **result})
        backup = _backup(conn)
        try:
            result, targets, token = attempt()
            if req.state_token != token:     # changed in the last instant (a journal page edit)
                conn.rollback()
                return JSONResponse(status_code=409, content={
                    "detail": "The journal changed since the preview. Review the new preview.",
                    "dry_run": True, "applied": False, "state_token": token, **result})
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    feedback = {"queued": 0}
    if req.feedback and targets:
        if os.getenv("ANTHROPIC_API_KEY"):
            feedback = {"queued": len(targets)}
            start_feedback(targets)
        else:
            feedback = {"queued": 0, "skipped": "No AI key in the journal's .env: no AI feedback."}
    return {"dry_run": False, "applied": True, "state_token": token, "backup": backup, "feedback": feedback,
            **result}


@router.get("/trade-events")
def trade_events(trade_group: str = Query(...), account_id: int = Query(...),
                 conn: sqlite3.Connection = Depends(_connection)):
    """Timeline of one trade: events and notes Edge Trader synced, oldest first."""
    rows = conn.execute("SELECT id, ts, kind, price, qty, text, origin FROM trade_events "
                        "WHERE account_id=? AND trade_group=? ORDER BY ts, id", (account_id, trade_group)).fetchall()
    return {"trade_group": trade_group, "account_id": account_id, "events": [row_to_dict(r) for r in rows]}
