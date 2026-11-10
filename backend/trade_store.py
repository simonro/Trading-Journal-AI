"""Storing built trades: shared by the CSV import and the Edge Trader sync.

IMPORT_LOCK serialises every path that adds or moves fills (CSV import, sync Apply, sync dry
run, moving a trade), so two of them never read the same stored fills and both decide a fill
is new.
"""
import json
import threading

IMPORT_LOCK = threading.Lock()

# Tables of the Edge Trader sync (edge_sync.py) that carry a trade_group and follow a regroup.
_SYNC_TABLES = ("sync_annotations", "trade_events", "sync_tags", "sync_field_state")


def _has_table(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def replace_regrouped_trades(conn, account_id: int, trades: list[dict]) -> None:
    """Clear stored trades that an import regrouped with new fills (see overlapping_db_fills).

    The review data (analysis and tags) of each old trade follows its fills to the new trade
    holding most of them, even when the import reuses the old name for a different trade
    (an earlier round trip imported later becomes _1 and pushes the old _1 to _2). A target
    that already has review data of its own keeps it. Edge Trader sync rows (annotations,
    timeline events and their bookkeeping) always follow the fills.
    """
    old_groups = {g for t in trades for g in t.get('replaces', [])}
    if not old_groups:
        return
    new_keys = {t['trade_group'] for t in trades}

    def fill_key(e):
        # split_part tells the closing half of a reversal from the opening half
        return (e.get('date'), e.get('time'), e.get('action'), e.get('price'), e.get('split_part'))

    moves = {}
    for g in old_groups:
        holders = [t for t in trades if g in t.get('replaces', [])]
        row = conn.execute("SELECT executions FROM trades WHERE trade_group=? AND account_id=?",
                           (g, account_id)).fetchone()
        fps = {fill_key(e) for e in json.loads((row[0] if row else None) or '[]')}
        best = max(holders, key=lambda t: sum(fill_key(e) in fps for e in json.loads(t['executions'])))
        if best['trade_group'] != g:
            moves[g] = best['trade_group']
    for g in old_groups - new_keys:
        conn.execute("DELETE FROM trades WHERE trade_group=? AND account_id=?", (g, account_id))
    if not moves:
        return
    sync_tables = [t for t in _SYNC_TABLES if _has_table(conn, t)]
    # Two steps through temporary names, so swapped names (_1 <-> _2) never collide.
    for g in moves:
        for table in ("trade_analysis", "trade_tags", *sync_tables):
            conn.execute(f"UPDATE {table} SET trade_group=? WHERE trade_group=? AND account_id=?",
                         (f"__moving__{g}", g, account_id))
    for g, target in moves.items():
        temp = f"__moving__{g}"
        for table in ("trade_tags", *sync_tables):
            conn.execute(f"UPDATE {table} SET trade_group=? WHERE trade_group=? AND account_id=?",
                         (target, temp, account_id))
        if conn.execute("SELECT 1 FROM trade_analysis WHERE trade_group=? AND account_id=?",
                        (target, account_id)).fetchone():
            merge_review(conn, account_id, temp, target)
        else:
            conn.execute("UPDATE trade_analysis SET trade_group=? WHERE trade_group=? AND account_id=?",
                         (target, temp, account_id))


_REVIEW_TEXT = ("entry_reason", "exit_reason", "mistakes", "notes", "ai_feedback", "match_notes")


def merge_review(conn, account_id: int, source: str, target: str) -> None:
    """Two reviewed trades became one: keep the target's values, fill its blanks from the
    source, and keep both texts, so nothing written is lost."""
    src = conn.execute("SELECT * FROM trade_analysis WHERE trade_group=? AND account_id=?",
                       (source, account_id)).fetchone()
    dst = conn.execute("SELECT * FROM trade_analysis WHERE trade_group=? AND account_id=?",
                       (target, account_id)).fetchone()
    if not src or not dst:
        return
    updates = {}
    for col in src.keys():
        if col in ("id", "account_id", "trade_group", "ticker", "date"):
            continue
        a, b = dst[col], src[col]
        if b in (None, '') or b == a:
            continue
        if col in _REVIEW_TEXT and a not in (None, ''):
            updates[col] = f"{a}\n\n{b}"   # keep both texts
        elif a in (None, ''):
            updates[col] = b                # fill the target's blanks; its own values win
    if updates:
        sets = ", ".join(f"{c}=?" for c in updates)
        conn.execute(f"UPDATE trade_analysis SET {sets} WHERE id=?", (*updates.values(), dst["id"]))
    conn.execute("DELETE FROM trade_analysis WHERE id=?", (src["id"],))


def insert_trade(conn, trade: dict) -> None:
    conn.execute("""
        INSERT INTO trades
            (account_id, trade_group, date, ticker, instrument_type, side,
             gross_pnl, net_pnl, commissions, executions,
             option_expiry, option_strike, option_type, source)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(trade_group, account_id) DO UPDATE SET
            date=excluded.date,
            side=excluded.side,
            gross_pnl=excluded.gross_pnl,
            net_pnl=excluded.net_pnl,
            commissions=excluded.commissions,
            executions=excluded.executions,
            imported_at=datetime('now')
    """, (
        trade['account_id'], trade['trade_group'], trade['date'],
        trade['ticker'], trade['instrument_type'], trade['side'],
        trade['gross_pnl'], trade['net_pnl'], trade['commissions'],
        trade['executions'], trade['option_expiry'],
        trade['option_strike'], trade['option_type'], trade['source'],
    ))


def rebuilt_names(trades: list[dict]) -> set:
    """Stored trades a batch rebuilds from their fills (every built trade's `replaces`)."""
    return {g for t in trades for g in (t.get("replaces") or [])}


def free_name(conn, account_id: int, trade: dict, taken: set, rebuilt: set | None = None) -> str:
    """The name a new trade may be stored under. A built trade whose name already belongs to a
    stored trade this batch did not rebuild (not in `rebuilt`, see rebuilt_names: a vertical or a
    trade edited in the journal, which overlapping_db_fills leaves alone) would overwrite that
    trade's executions, so it takes the next free number instead. A rebuilt name is free even for
    another trade of the batch: an earlier round trip imported later takes _1 and the old _1 moves
    to _2. `taken` holds names given out in this batch."""
    name = trade["trade_group"]
    replaces = set(trade.get("replaces") or []) | set(rebuilt or ())

    def free(n):
        if n in taken:
            return False
        if n in replaces:
            return True
        return conn.execute("SELECT 1 FROM trades WHERE trade_group=? AND account_id=?",
                            (n, account_id)).fetchone() is None
    if not free(name):
        base, _, seq = name.rpartition("_")
        n = int(seq) if seq.isdigit() else 1
        while True:
            n += 1
            candidate = f"{base}_{n}"
            if free(candidate):
                name = candidate
                break
    taken.add(name)
    return name
