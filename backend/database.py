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

        CREATE TABLE IF NOT EXISTS trade_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_group TEXT NOT NULL UNIQUE,
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
            diary_entry_id INTEGER REFERENCES diary_entries(id)
        );

        CREATE TABLE IF NOT EXISTS trade_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    ]:
        try:
            conn.execute(ddl)
            conn.commit()
        except Exception:
            pass

    from exitlab.schema import migrate
    migrate(conn)
    conn.close()


def row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)
