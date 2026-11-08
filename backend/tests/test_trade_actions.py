"""Moving a trade to another account, and deleting one, from Trade View.

    cd backend && python -m pytest tests/test_trade_actions.py -q

Notes and tags are keyed by (trade_group, account_id), so a move must carry them along, and a
trade with the same name already in the target account must block the move. All data synthetic.
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

MAIN, FUTURES, DEMO = 1, 4, 5
GROUP = "11/6/26_AMD_STOCK_1"
EXECS = json.dumps([
    {"date": "2026-11-06", "time": "09:45:00", "action": "BOT", "qty": 100, "price": 250.0},
    {"date": "2026-11-06", "time": "10:05:00", "action": "SOLD", "qty": 100, "price": 252.0},
])


def add_trade(conn, account_id, group=GROUP):
    cur = conn.execute(
        "INSERT INTO trades (account_id, trade_group, date, ticker, instrument_type, side, "
        "gross_pnl, net_pnl, commissions, executions, source) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (account_id, group, "2026-11-06", "AMD", "STOCK", "LONG", 200.0, 199.0, 1.0, EXECS, "imported"))
    return cur.lastrowid


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "journal.db"
    monkeypatch.setenv("DATABASE_PATH", str(db))
    (tmp_path / "uploads").mkdir()
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    for name in ("database", "main"):
        sys.modules.pop(name, None)
    import database
    database.DB_PATH = str(db)
    import main
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        conn = main.get_db()
        for aid, name in ((MAIN, "Main"), (FUTURES, "Futures"), (DEMO, "Demo")):
            conn.execute("INSERT INTO accounts (id, name, type) VALUES (?, ?, 'day_trading')", (aid, name))
        c.trade = add_trade(conn, DEMO)
        conn.execute("INSERT INTO trade_analysis (account_id, trade_group, ticker, date, strategy, notes) "
                     "VALUES (?,?,?,?,?,?)", (DEMO, GROUP, "AMD", "2026-11-06", "Continuation", "held the stop"))
        conn.execute("INSERT INTO trade_tags (account_id, trade_group, tag_type, tag_value, source) "
                     "VALUES (?,?,?,?,?)", (DEMO, GROUP, "mistake", "chased entry", "manual"))
        conn.commit()
        conn.close()
        c.db = str(db)
        yield c


def rows(client, sql, *args):
    conn = sqlite3.connect(client.db)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def test_move_carries_notes_and_tags(client):
    r = client.patch(f"/api/trades/{client.trade}/account", json={"account_id": MAIN})
    assert r.status_code == 200, r.text
    assert r.json()["account_name"] == "Main"
    assert rows(client, "SELECT account_id FROM trades WHERE id=?", client.trade) == [(MAIN,)]
    assert rows(client, "SELECT account_id, notes FROM trade_analysis WHERE trade_group=?", GROUP) == [
        (MAIN, "held the stop")]
    assert rows(client, "SELECT account_id, tag_value FROM trade_tags WHERE trade_group=?", GROUP) == [
        (MAIN, "chased entry")]


def test_move_refuses_a_name_clash_and_changes_nothing(client):
    conn = sqlite3.connect(client.db)
    add_trade(conn, MAIN)
    conn.commit()
    conn.close()
    r = client.patch(f"/api/trades/{client.trade}/account", json={"account_id": MAIN})
    assert r.status_code == 409
    assert "Main already has a AMD trade on 2026-11-06" in r.json()["detail"]
    assert rows(client, "SELECT account_id FROM trades WHERE id=?", client.trade) == [(DEMO,)]
    assert rows(client, "SELECT account_id FROM trade_analysis WHERE trade_group=?", GROUP) == [(DEMO,)]


def test_move_to_the_same_or_a_missing_account(client):
    assert client.patch(f"/api/trades/{client.trade}/account", json={"account_id": DEMO}).status_code == 400
    assert client.patch(f"/api/trades/{client.trade}/account", json={"account_id": 99}).status_code == 404
    assert client.patch("/api/trades/999999/account", json={"account_id": MAIN}).status_code == 404


def test_delete_removes_the_trade_with_its_notes_and_tags(client):
    r = client.delete(f"/api/trades/{client.trade}")
    assert r.status_code == 200
    assert rows(client, "SELECT 1 FROM trades WHERE id=?", client.trade) == []
    assert rows(client, "SELECT 1 FROM trade_analysis WHERE trade_group=?", GROUP) == []
    assert rows(client, "SELECT 1 FROM trade_tags WHERE trade_group=?", GROUP) == []


def test_move_clears_leftover_notes_in_the_target_instead_of_failing(client):
    # Notes and a tag in Main under this name with no trade behind them (a re-import renamed it).
    conn = sqlite3.connect(client.db)
    conn.execute("INSERT INTO trade_analysis (account_id, trade_group, ticker, date, notes) VALUES (?,?,?,?,?)",
                 (MAIN, GROUP, "AMD", "2026-11-06", "orphan note"))
    conn.execute("INSERT INTO trade_tags (account_id, trade_group, tag_type, tag_value, source) VALUES (?,?,?,?,?)",
                 (MAIN, GROUP, "emotion", "orphan tag", "manual"))
    conn.commit()
    conn.close()
    r = client.patch(f"/api/trades/{client.trade}/account", json={"account_id": MAIN})
    assert r.status_code == 200, r.text
    assert r.json()["cleared_leftovers"] == 2
    assert rows(client, "SELECT notes FROM trade_analysis WHERE trade_group=? AND account_id=?", GROUP, MAIN) == [
        ("held the stop",)]
    assert rows(client, "SELECT tag_value FROM trade_tags WHERE trade_group=? AND account_id=?", GROUP, MAIN) == [
        ("chased entry",)]


def test_a_same_named_trade_in_a_third_account_keeps_its_notes(client):
    conn = sqlite3.connect(client.db)
    add_trade(conn, FUTURES)
    conn.execute("INSERT INTO trade_analysis (account_id, trade_group, ticker, date, notes) VALUES (?,?,?,?,?)",
                 (FUTURES, GROUP, "AMD", "2026-11-06", "futures note"))
    conn.commit()
    conn.close()
    assert client.patch(f"/api/trades/{client.trade}/account", json={"account_id": MAIN}).status_code == 200
    assert rows(client, "SELECT notes FROM trade_analysis WHERE trade_group=? AND account_id=?", GROUP, FUTURES) == [
        ("futures note",)]
    assert rows(client, "SELECT count(*) FROM trades WHERE trade_group=? AND account_id=?", GROUP, FUTURES) == [(1,)]


def test_move_unlinks_the_old_accounts_diary_entry(client):
    conn = sqlite3.connect(client.db)
    entry = conn.execute("INSERT INTO diary_entries (account_id, entry_date, raw_text) VALUES (?,?,?)",
                         (DEMO, "2026-11-06", "AMD long, held the stop")).lastrowid
    conn.execute("UPDATE trade_analysis SET diary_entry_id=? WHERE trade_group=? AND account_id=?",
                 (entry, GROUP, DEMO))
    conn.commit()
    conn.close()
    assert client.patch(f"/api/trades/{client.trade}/account", json={"account_id": MAIN}).status_code == 200
    assert rows(client, "SELECT diary_entry_id, notes FROM trade_analysis WHERE trade_group=? AND account_id=?",
                GROUP, MAIN) == [(None, "held the stop")]


def test_deleting_an_account_drops_summaries_cached_for_sets_that_included_it(client):
    conn = sqlite3.connect(client.db)
    for key in ("2026-11-06|accounts=1,5", "2026-11-06|accounts=1,4", "2026-11-06"):
        conn.execute("INSERT INTO daily_summaries (account_id, summary_date, ai_content) VALUES (NULL, ?, 'x')", (key,))
    conn.commit()
    conn.close()
    assert client.delete(f"/api/accounts/{DEMO}").status_code == 200
    assert sorted(k for (k,) in rows(client, "SELECT summary_date FROM daily_summaries")) == [
        "2026-11-06", "2026-11-06|accounts=1,4"]
