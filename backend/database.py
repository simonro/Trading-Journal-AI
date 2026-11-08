import sqlite3
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DATABASE_PATH", "trading_journal.db")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('day_trading','swing_trading','investment')),
            color TEXT NOT NULL DEFAULT '#6366f1',
            broker TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL REFERENCES accounts(id),
            trade_group TEXT NOT NULL,
            date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            instrument_type TEXT NOT NULL CHECK(instrument_type IN ('STOCK','OPTION','FUTURE')),
            side TEXT NOT NULL CHECK(side IN ('LONG','SHORT')),
            gross_pnl REAL,
            net_pnl REAL,
            commissions REAL DEFAULT 0,
            executions TEXT NOT NULL DEFAULT '[]',
            option_expiry TEXT,
            option_strike REAL,
            option_type TEXT CHECK(option_type IN ('CALL','PUT',NULL)),
            source TEXT NOT NULL DEFAULT 'imported',
            imported_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(trade_group, account_id)
        );

        CREATE TABLE IF NOT EXISTS diary_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL REFERENCES accounts(id),
            entry_date TEXT NOT NULL,
            image_path TEXT,
            raw_text TEXT,
            ai_analysis TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        -- Review data is keyed by (trade_group, account_id), like trades: the
        -- importer names groups without the account, so two accounts trading the
        -- same ticker on the same day share a trade_group name.
        CREATE TABLE IF NOT EXISTS trade_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER REFERENCES accounts(id),
            trade_group TEXT NOT NULL,
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            strategy TEXT,
            stop_loss REAL,
            risk_per_trade REAL,
            risk_reward REAL,
            r_multiple REAL,
            entry_reason TEXT,
            exit_reason TEXT,
            mistakes TEXT,
            emotional_state TEXT,
            notes TEXT,
            ai_feedback TEXT,
            match_confidence TEXT CHECK(match_confidence IN ('high','medium','low','ambiguous','unmatched','manual')),
            match_notes TEXT,
            diary_entry_id INTEGER REFERENCES diary_entries(id),
            UNIQUE(trade_group, account_id)
        );

        CREATE TABLE IF NOT EXISTS trade_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER REFERENCES accounts(id),
            trade_group TEXT NOT NULL,
            tag_type TEXT NOT NULL,
            tag_value TEXT NOT NULL,
            source TEXT NOT NULL CHECK(source IN ('ai','manual'))
        );

        CREATE TABLE IF NOT EXISTS daily_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER REFERENCES accounts(id),
            summary_date TEXT NOT NULL,
            ai_content TEXT NOT NULL,
            generated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(summary_date, account_id)
        );

        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL DEFAULT 0,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            UNIQUE(account_id, key)
        );

        -- The trader's playbook: named setups used to tag trades.
        CREATE TABLE IF NOT EXISTS custom_setups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            side TEXT,                      -- LONG | SHORT | NULL (either)
            notes TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        -- Leak Finder sentences, keyed by a hash of the numbers they explain
        -- (leaks.cache_key), so Claude is asked again only when the numbers change.
        CREATE TABLE IF NOT EXISTS leak_explanations (
            cache_key TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            generated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_trades_account_date ON trades(account_id, date);
        CREATE INDEX IF NOT EXISTS idx_trades_group ON trades(trade_group);
        CREATE INDEX IF NOT EXISTS idx_analysis_group ON trade_analysis(trade_group);
        CREATE INDEX IF NOT EXISTS idx_tags_group ON trade_tags(trade_group);
    """)

    conn.commit()

    # Safe migrations — ignored if column already exists
    for ddl in [
        "ALTER TABLE trade_analysis ADD COLUMN target_price REAL",
        "ALTER TABLE trade_analysis ADD COLUMN trade_rating INTEGER",
        "ALTER TABLE trade_analysis ADD COLUMN idea_source TEXT",
        # Playbook setup tag + optional execution-quality grade
        "ALTER TABLE trades ADD COLUMN setup TEXT",           # playbook setup name, or NONE
        "ALTER TABLE trades ADD COLUMN setup_grade TEXT",     # A++ | A+ | A | B | C | D | F
        "ALTER TABLE trades ADD COLUMN setup_notes TEXT",     # JSON: free-form notes
        "ALTER TABLE trades ADD COLUMN setup_features TEXT",  # JSON: market state at entry
        # how the tag was set ('manual' in this demo; kept for compatibility)
        "ALTER TABLE trades ADD COLUMN setup_source TEXT DEFAULT 'auto'",
        # Trade-quality metrics measured from 1-min bars over the hold window.
        # MFE = best unrealised gain reached, MAE = worst unrealised loss reached,
        # exit_efficiency = realised / MFE, i.e. share of the move captured.
        "ALTER TABLE trades ADD COLUMN mfe_pct REAL",
        "ALTER TABLE trades ADD COLUMN mae_pct REAL",
        "ALTER TABLE trades ADD COLUMN exit_efficiency REAL",
        "ALTER TABLE accounts ADD COLUMN import_timezone TEXT",
    ]:
        try:
            conn.execute(ddl)
            conn.commit()
        except Exception:
            pass

    migrate_review_keys(conn)

    # Exit Lab: additive, idempotent tables and columns (backend/exitlab/schema.py)
    from exitlab.schema import migrate
    migrate(conn)
    conn.close()


def _run_in_transaction(conn: sqlite3.Connection, script: str) -> None:
    conn.commit()
    try:
        conn.executescript(f"BEGIN;\n{script}\nCOMMIT;")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def _rebuild_analysis(conn, create, names, values, first_account) -> None:
    _run_in_transaction(conn, f"""
        {create};
        INSERT INTO trade_analysis_new (id, account_id, {names})
            SELECT ta.id, {first_account}, {values} FROM trade_analysis ta;
        INSERT INTO trade_analysis_new (account_id, {names})
            SELECT acc.account_id, {values}
            FROM trade_analysis ta
            JOIN (SELECT DISTINCT trade_group, account_id FROM trades) acc
              ON acc.trade_group = ta.trade_group
            WHERE acc.account_id > {first_account};
        DROP TABLE trade_analysis;
        ALTER TABLE trade_analysis_new RENAME TO trade_analysis;
        CREATE INDEX IF NOT EXISTS idx_analysis_group ON trade_analysis(trade_group);
    """)


def migrate_review_keys(conn: sqlite3.Connection) -> None:
    """Key trade_analysis and trade_tags by (trade_group, account_id). Idempotent.

    Older databases keyed them by trade_group alone. account_id is filled in from
    trades. A group that more than one account traded gets a copy of its notes and
    tags for each of those accounts (the row id stays with the lowest account id),
    so every account keeps seeing what it saw before. Rows with no trade keep a
    NULL account_id.
    """
    analysis_cols = {r[1] for r in conn.execute("PRAGMA table_info(trade_analysis)")}
    tag_cols = {r[1] for r in conn.execute("PRAGMA table_info(trade_tags)")}

    if "account_id" not in analysis_cols:
        create = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='trade_analysis'").fetchone()[0].rstrip()
        if not create.endswith(")") or "trade_group TEXT NOT NULL UNIQUE" not in create:
            raise RuntimeError("trade_analysis has an unexpected schema; not migrating it")
        create = create[:-1].rstrip() + ",\n            UNIQUE(trade_group, account_id)\n        )"
        create = create.replace("CREATE TABLE trade_analysis", "CREATE TABLE trade_analysis_new", 1)
        create = create.replace("trade_group TEXT NOT NULL UNIQUE",
                                "account_id INTEGER REFERENCES accounts(id),\n            trade_group TEXT NOT NULL", 1)
        cols = sorted(c for c in analysis_cols if c != "id")
        names = ", ".join(cols)
        values = ", ".join(f"ta.{c}" for c in cols)
        first_account = "(SELECT MIN(t.account_id) FROM trades t WHERE t.trade_group = ta.trade_group)"
        # SQLite's table-rebuild procedure: foreign keys off while rows are copied, so a
        # reference that already dangles (a note whose diary entry was deleted) is carried
        # over as it is instead of failing the migration.
        conn.commit()
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        conn.execute("PRAGMA foreign_keys=OFF")
        try:
            _rebuild_analysis(conn, create, names, values, first_account)
        finally:
            conn.execute(f"PRAGMA foreign_keys={'ON' if foreign_keys else 'OFF'}")

    if "account_id" not in tag_cols:
        _run_in_transaction(conn, """
            ALTER TABLE trade_tags ADD COLUMN account_id INTEGER REFERENCES accounts(id);
            INSERT INTO trade_tags (account_id, trade_group, tag_type, tag_value, source)
                SELECT acc.account_id, tt.trade_group, tt.tag_type, tt.tag_value, tt.source
                FROM trade_tags tt
                JOIN (SELECT DISTINCT trade_group, account_id FROM trades) acc
                  ON acc.trade_group = tt.trade_group
                WHERE acc.account_id > (SELECT MIN(t.account_id) FROM trades t
                                        WHERE t.trade_group = tt.trade_group);
            UPDATE trade_tags
               SET account_id = (SELECT MIN(t.account_id) FROM trades t
                                 WHERE t.trade_group = trade_tags.trade_group)
             WHERE account_id IS NULL;
        """)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_tags_group_account ON trade_tags(trade_group, account_id)")
    conn.commit()


def row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


def in_marks(ids) -> str:
    """Placeholders for an IN (...) filter over a list of account ids."""
    return ",".join("?" * len(ids))
