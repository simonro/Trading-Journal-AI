"""Additive and idempotent migrations, called from database.init_db."""
from .rules import PRESETS


def migrate(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS market_bar_windows (
            symbol TEXT NOT NULL, feed TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL,
            PRIMARY KEY(symbol,feed,start,end)
        );
        CREATE TABLE IF NOT EXISTS market_bars (
            symbol TEXT NOT NULL, ts TEXT NOT NULL, feed TEXT NOT NULL,
            open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
            volume REAL NOT NULL, vwap REAL, fetched_at TEXT NOT NULL, revision_hash TEXT NOT NULL,
            PRIMARY KEY (symbol, ts, feed)
        );
        CREATE TABLE IF NOT EXISTS exit_rules (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, json TEXT NOT NULL,
            hash TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now')),
            is_preset INTEGER NOT NULL DEFAULT 0
        );
        CREATE UNIQUE INDEX IF NOT EXISTS exit_preset_hash ON exit_rules(hash) WHERE is_preset=1;
    """)
    rule_columns = {r[1] for r in conn.execute("PRAGMA table_info(exit_rules)")}
    if "show_in_tables" not in rule_columns:
        conn.execute("ALTER TABLE exit_rules ADD COLUMN show_in_tables INTEGER NOT NULL DEFAULT 1 CHECK(show_in_tables IN (0,1))")
    if "applies_to_setups" not in rule_columns:
        conn.execute("ALTER TABLE exit_rules ADD COLUMN applies_to_setups TEXT NOT NULL DEFAULT 'null'")
    columns = {r[1] for r in conn.execute("PRAGMA table_info(trades)")}
    if "excursion_source" not in columns:
        conn.execute("ALTER TABLE trades ADD COLUMN excursion_source TEXT")
        # Before Exit Lab, only seed_demo.py populated these metric columns.
        # The old seed used source='imported', so preserve and label its values.
        conn.execute("""UPDATE trades SET excursion_source='legacy_synthetic'
            WHERE mfe_pct IS NOT NULL OR mae_pct IS NOT NULL OR exit_efficiency IS NOT NULL""")
    for rule in PRESETS:
        conn.execute("INSERT OR IGNORE INTO exit_rules(name,json,hash,is_preset) VALUES(?,?,?,1)",
                     (rule.name, rule.model_dump_json(), rule.rule_hash))
    from .assignments import migrate_assignments
    migrate_assignments(conn)
    conn.commit()
