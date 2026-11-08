"""Notes, tags and AI reviews belong to one account's trade.

    cd backend && python -m pytest tests -q

trade_group names carry no account (csv_parser.trade_group_key builds
{date}_{ticker}_{instr}_{seq}), so two accounts trading the same ticker on the same
day get the same name: two accounts that both trade /ES on one day each get
11/5/26_/ESZ26_FUTURE_1 and _2. trade_analysis and trade_tags used to be keyed by
the name alone, so those trades would have shared one set of notes. All data synthetic.
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

SHARED = "11/5/26_/ESZ26_FUTURE_1"
MAIN, FUTURES = 1, 4
EXECS = json.dumps([
    {"date": "2026-11-05", "time": "09:45:00", "action": "BOT", "qty": 1, "price": 6800.0},
    {"date": "2026-11-05", "time": "10:05:00", "action": "SOLD", "qty": 1, "price": 6810.0},
])


def add_trade(conn, account_id, group, net_pnl):
    conn.execute(
        "INSERT INTO trades (account_id, trade_group, date, ticker, instrument_type, side, "
        "gross_pnl, net_pnl, commissions, executions, source) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (account_id, group, "2026-11-05", "/ESZ26", "FUTURE", "LONG", net_pnl, net_pnl, 0, EXECS,
         "imported"))


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "journal.db"
    monkeypatch.setenv("DATABASE_PATH", str(db))
    for name in ("database", "main"):
        sys.modules.pop(name, None)
    import database
    database.DB_PATH = str(db)
    import main
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        conn = main.get_db()
        conn.execute("INSERT INTO accounts (id, name, type) VALUES (?, 'Main', 'day_trading')", (MAIN,))
        conn.execute("INSERT INTO accounts (id, name, type) VALUES (?, 'Futures', 'day_trading')", (FUTURES,))
        add_trade(conn, MAIN, SHARED, 500.0)
        add_trade(conn, FUTURES, SHARED, -250.0)
        conn.commit()
        conn.close()
        c.db = str(db)
        c.main = main
        yield c


def trade_id(client, account_id):
    conn = sqlite3.connect(client.db)
    try:
        return conn.execute("SELECT id FROM trades WHERE trade_group=? AND account_id=?",
                            (SHARED, account_id)).fetchone()[0]
    finally:
        conn.close()


def analysis(client, account_id):
    url = f"/api/trades/{SHARED}/analysis"
    r = client.get(url, params={"account_id": account_id})
    assert r.status_code == 200, r.text
    return r.json()


def test_notes_and_tags_stay_with_their_account(client):
    url = f"/api/trades/{SHARED}/analysis"
    r = client.patch(url, params={"account_id": MAIN}, json={"notes": "Main note", "strategy": "Trend"})
    assert r.status_code == 200, r.text
    assert r.json()["account_id"] == MAIN
    client.patch(url, params={"account_id": FUTURES}, json={"notes": "Futures note"})
    r = client.post(f"/api/trades/{SHARED}/tags", params={"account_id": FUTURES},
                    json={"tag_type": "mistake", "tag_value": "chased entry"})
    assert r.status_code == 201, r.text

    main_view, fut_view = analysis(client, MAIN), analysis(client, FUTURES)
    assert main_view["analysis"]["notes"] == "Main note"
    assert main_view["analysis"]["strategy"] == "Trend"
    assert main_view["tags"] == []
    assert fut_view["analysis"]["notes"] == "Futures note"
    assert fut_view["analysis"]["strategy"] is None
    assert [t["tag_value"] for t in fut_view["tags"]] == ["chased entry"]

    # The trade list joins each trade to its own account's notes.
    rows = {r["account_id"]: r for r in client.get("/api/trades").json()}
    assert rows[MAIN]["analysis_notes"] == "Main note"
    assert rows[FUTURES]["analysis_notes"] == "Futures note"
    assert len(rows) == 2


def test_a_shared_name_without_account_id_is_refused(client):
    url = f"/api/trades/{SHARED}/analysis"
    assert client.get(url).status_code == 409
    assert client.patch(url, json={"notes": "which one?"}).status_code == 409
    assert client.post(f"/api/trades/{SHARED}/tags",
                       json={"tag_type": "mistake", "tag_value": "x"}).status_code == 409
    # A name only one account has still works without it.
    conn = sqlite3.connect(client.db)
    add_trade(conn, MAIN, "11/5/26_NVDA_STOCK_1", 10.0)
    conn.commit()
    conn.close()
    r = client.patch("/api/trades/11/5/26_NVDA_STOCK_1/analysis", json={"notes": "only Main"})
    assert r.status_code == 200 and r.json()["account_id"] == MAIN


def test_tag_lookups_for_reports_and_leaks_are_per_account(client):
    client.post(f"/api/trades/{SHARED}/tags", params={"account_id": MAIN},
                json={"tag_type": "mistake", "tag_value": "sized too big"})
    conn = client.main.get_db()
    try:
        fut_rows = client.main._leak_rows(conn, [FUTURES], None, None)
        main_rows = client.main._leak_rows(conn, [MAIN], None, None)
    finally:
        conn.close()
    assert fut_rows[0]["tags"] == []
    assert main_rows[0]["tags"] == [("mistake", "sized too big")]
    assert "sized too big" not in json.dumps(client.get("/api/reports", params={"account_id": FUTURES}).json())
    assert "sized too big" in json.dumps(client.get("/api/reports", params={"account_id": MAIN}).json())


def test_deleting_a_trade_or_an_account_keeps_the_other_accounts_notes(client):
    url = f"/api/trades/{SHARED}/analysis"
    for acc in (MAIN, FUTURES):
        client.patch(url, params={"account_id": acc}, json={"notes": f"note {acc}"})
        client.post(f"/api/trades/{SHARED}/tags", params={"account_id": acc},
                    json={"tag_type": "emotion", "tag_value": f"calm {acc}"})

    assert client.delete(f"/api/trades/{trade_id(client, FUTURES)}").status_code == 200
    assert analysis(client, MAIN)["analysis"]["notes"] == f"note {MAIN}"
    assert len(analysis(client, MAIN)["tags"]) == 1

    conn = sqlite3.connect(client.db)
    add_trade(conn, FUTURES, SHARED, -250.0)
    conn.commit()
    conn.close()
    client.patch(url, params={"account_id": FUTURES}, json={"notes": f"note {FUTURES}"})

    r = client.delete(f"/api/accounts/{MAIN}")
    assert r.status_code == 200, r.text
    assert r.json()["trade_notes"] == 1
    assert analysis(client, FUTURES)["analysis"]["notes"] == f"note {FUTURES}"
    conn = sqlite3.connect(client.db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM trade_analysis WHERE account_id=?", (MAIN,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM trade_tags WHERE account_id=?", (MAIN,)).fetchone()[0] == 0
    finally:
        conn.close()


def test_regrouped_import_moves_notes_only_within_its_account(client):
    conn = sqlite3.connect(client.db)
    for acc in (MAIN, FUTURES):
        conn.execute("INSERT INTO trade_analysis (trade_group, account_id, ticker, date, notes) "
                     "VALUES (?,?,'/ESZ26','2026-11-05',?)", (SHARED, acc, f"note {acc}"))
    conn.commit()
    conn.close()
    new = {"trade_group": "11/5/26_/ESZ26_FUTURE_9", "replaces": [SHARED], "executions": EXECS}
    conn = client.main.get_db()
    try:
        client.main._replace_regrouped_trades(conn, MAIN, [new])
        conn.commit()
        rows = conn.execute("SELECT account_id, trade_group, notes FROM trade_analysis "
                            "ORDER BY account_id").fetchall()
    finally:
        conn.close()
    assert [tuple(r) for r in rows] == [
        (MAIN, "11/5/26_/ESZ26_FUTURE_9", f"note {MAIN}"),
        (FUTURES, SHARED, f"note {FUTURES}"),
    ]


def test_ai_review_is_saved_under_the_diary_entrys_account(client):
    from ai_analysis import save_analysis_to_db
    conn = client.main.get_db()
    try:
        cur = conn.execute("INSERT INTO diary_entries (account_id, entry_date) VALUES (?, '2026-11-05')",
                           (FUTURES,))
        save_analysis_to_db(conn, cur.lastrowid, {"diary_date": "2026-11-05", "trade_analyses": [
            {"trade_group": SHARED, "ticker": "/ESZ26", "strategy": "Trend",
             "tags": [{"type": "mistake", "value": "late exit"}]}]})
    finally:
        conn.close()
    assert analysis(client, FUTURES)["analysis"]["diary_entry_id"] is not None
    assert [t["tag_value"] for t in analysis(client, FUTURES)["tags"]] == ["late exit"]
    assert analysis(client, MAIN)["analysis"] is None
    assert analysis(client, MAIN)["tags"] == []


OLD_SCHEMA = """
    CREATE TABLE accounts (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
        type TEXT NOT NULL, color TEXT NOT NULL DEFAULT '#6366f1', broker TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')));
    CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL REFERENCES accounts(id), trade_group TEXT NOT NULL,
        date TEXT NOT NULL, ticker TEXT NOT NULL, instrument_type TEXT NOT NULL, side TEXT NOT NULL,
        gross_pnl REAL, net_pnl REAL, commissions REAL DEFAULT 0, executions TEXT NOT NULL DEFAULT '[]',
        option_expiry TEXT, option_strike REAL, option_type TEXT, source TEXT NOT NULL DEFAULT 'imported',
        imported_at TEXT NOT NULL DEFAULT (datetime('now')), UNIQUE(trade_group, account_id));
    CREATE TABLE diary_entries (id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL REFERENCES accounts(id), entry_date TEXT NOT NULL,
        image_path TEXT, raw_text TEXT, ai_analysis TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')));
    CREATE TABLE trade_analysis (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_group TEXT NOT NULL UNIQUE,
        ticker TEXT NOT NULL,
        date TEXT NOT NULL,
        strategy TEXT, stop_loss REAL, risk_per_trade REAL, risk_reward REAL, r_multiple REAL,
        entry_reason TEXT, exit_reason TEXT, mistakes TEXT, emotional_state TEXT, notes TEXT,
        ai_feedback TEXT,
        match_confidence TEXT CHECK(match_confidence IN ('high','medium','low','ambiguous','unmatched','manual')),
        match_notes TEXT,
        diary_entry_id INTEGER REFERENCES diary_entries(id)
    , target_price REAL, trade_rating INTEGER, idea_source TEXT);
    CREATE TABLE trade_tags (id INTEGER PRIMARY KEY AUTOINCREMENT, trade_group TEXT NOT NULL,
        tag_type TEXT NOT NULL, tag_value TEXT NOT NULL,
        source TEXT NOT NULL CHECK(source IN ('ai','manual')));
    CREATE INDEX idx_analysis_group ON trade_analysis(trade_group);
    CREATE INDEX idx_tags_group ON trade_tags(trade_group);
"""


def test_migration_backfills_account_id_from_trades(tmp_path, monkeypatch):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(OLD_SCHEMA)
    conn.execute("INSERT INTO accounts (id, name, type) VALUES (1, 'Main', 'day_trading')")
    conn.execute("INSERT INTO accounts (id, name, type) VALUES (4, 'Futures', 'day_trading')")
    for acc, group in [(1, "solo_main"), (4, "solo_fut"), (1, SHARED), (4, SHARED)]:
        conn.execute("INSERT INTO trades (account_id, trade_group, date, ticker, instrument_type, side) "
                     "VALUES (?,?,'2026-11-05','X','STOCK','LONG')", (acc, group))
    conn.execute("INSERT INTO diary_entries (id, account_id, entry_date) VALUES (7, 1, '2026-11-05')")
    # 99 is a diary entry that was deleted: a real journal can hold such a dangling
    # reference, and the migration must carry it over instead of failing.
    for i, group, diary in [(10, "solo_main", 7), (11, "solo_fut", 7), (12, SHARED, 7), (13, "orphan", 99)]:
        conn.execute("INSERT INTO trade_analysis (id, trade_group, ticker, date, notes, target_price, "
                     "diary_entry_id) VALUES (?,?,'X','2026-11-05',?,1.5,?)", (i, group, f"n {group}", diary))
        conn.execute("INSERT INTO trade_tags (trade_group, tag_type, tag_value, source) "
                     "VALUES (?,'mistake',?,'manual')", (group, f"t {group}"))
    conn.commit()
    conn.close()

    monkeypatch.setenv("DATABASE_PATH", str(db))
    sys.modules.pop("database", None)
    import database
    database.DB_PATH = str(db)
    database.init_db()
    database.init_db()  # idempotent

    conn = sqlite3.connect(db)
    try:
        notes = conn.execute("SELECT id, account_id, trade_group, notes, target_price, diary_entry_id "
                             "FROM trade_analysis ORDER BY trade_group, account_id").fetchall()
        tags = conn.execute("SELECT account_id, trade_group, tag_value FROM trade_tags "
                            "ORDER BY trade_group, account_id").fetchall()
        create = conn.execute("SELECT sql FROM sqlite_master WHERE name='trade_analysis'").fetchone()[0]
        dangling = conn.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        conn.close()

    shared_copy_id = [n[0] for n in notes if n[2] == SHARED and n[1] == 4][0]
    assert notes == [
        (12, 1, SHARED, f"n {SHARED}", 1.5, 7),
        (shared_copy_id, 4, SHARED, f"n {SHARED}", 1.5, 7),
        (13, None, "orphan", "n orphan", 1.5, 99),
        (11, 4, "solo_fut", "n solo_fut", 1.5, 7),
        (10, 1, "solo_main", "n solo_main", 1.5, 7),
    ]
    assert tags == [
        (1, SHARED, f"t {SHARED}"), (4, SHARED, f"t {SHARED}"),
        (None, "orphan", "t orphan"),
        (4, "solo_fut", "t solo_fut"), (1, "solo_main", "t solo_main"),
    ]
    assert [(d[0], d[2]) for d in dangling] == [("trade_analysis", "diary_entries")]
    assert "UNIQUE(trade_group, account_id)" in create
    assert "trade_group TEXT NOT NULL UNIQUE" not in create
