"""Regression tests for the v1.7.1 audit fixes: each test is the failure scenario that was
reported, run through the real import and API paths.

    cd backend && python -m pytest tests -q
"""
import io
import json
import sqlite3
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from csv_parser import aggregate_executions  # noqa: E402

HEADER = "date,time,symbol,side,quantity,price,commission,asset_type,expiry,strike,put_call,multiplier\n"


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "journal.db"
    monkeypatch.setenv("DATABASE_PATH", str(db))
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    for name in ("database", "main"):
        sys.modules.pop(name, None)
    import database
    database.DB_PATH = str(db)
    import main
    main.UPLOAD_DIR = str(tmp_path / "uploads")
    monkeypatch.setattr(main, "_classify_dates", lambda conn, dates: 0, raising=False)
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        c.db = str(db)
        c.tmp = tmp_path
        conn = sqlite3.connect(str(db))
        conn.execute("INSERT INTO accounts (id, name, type) VALUES (1, 'Day', 'day_trading')")
        conn.commit()
        conn.close()
        yield c


def post(client, rows):
    content = HEADER + "\n".join(rows) + "\n"
    r = client.post("/api/import-csv", data={"account_id": "1", "broker": "generic"},
                    files={"file": ("trades.csv", content.encode(), "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()


def trades(client):
    conn = sqlite3.connect(client.db)
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute("SELECT * FROM trades ORDER BY trade_group")]


# 1. A single fill that reverses the position closes one trade and opens the next.
def test_reversal_in_one_fill_closes_the_long_and_opens_a_short(client):
    post(client, ["2026-09-01,09:31:00,XYZ,BUY,100,10,1,STOCK,,,,",
                  "2026-09-01,09:40:00,XYZ,SELL,200,11,2,STOCK,,,,"])
    rows = trades(client)
    assert len(rows) == 2
    closed = [t for t in rows if t["gross_pnl"] != 0]
    assert len(closed) == 1 and closed[0]["side"] == "LONG"
    assert closed[0]["gross_pnl"] == pytest.approx(100.0)
    assert closed[0]["net_pnl"] == pytest.approx(98.0)  # 1 on the buy, half of the 2 on the flip
    opened = [t for t in rows if t is not closed[0]][0]
    assert opened["side"] == "SHORT"
    assert sum(e["qty"] for e in json.loads(opened["executions"])) == 100

    # Importing the same file again recognises the split fill as already stored.
    again = post(client, ["2026-09-01,09:31:00,XYZ,BUY,100,10,1,STOCK,,,,",
                          "2026-09-01,09:40:00,XYZ,SELL,200,11,2,STOCK,,,,"])
    assert again["imported"] == 0 and again["skipped"] == 2
    assert len(trades(client)) == 2


# 2. Fractional shares that add up to a closed position are a closed trade.
def test_fractional_shares_close_with_their_pnl():
    fills = [
        {"action": "BOT", "qty": 0.1, "price": 100, "amount": -10.0, "commission": 0},
        {"action": "BOT", "qty": 0.2, "price": 100, "amount": -20.0, "commission": 0},
        {"action": "SOLD", "qty": 0.3, "price": 110, "amount": 33.0, "commission": 0},
    ]
    assert aggregate_executions(fills)["gross_pnl"] == pytest.approx(3.0)


# 3. Two option contracts with the same time, size and price are different fills.
def test_a_second_contract_is_not_taken_for_a_duplicate(client):
    post(client, ["2026-09-02,10:00:00,SPY,BUY,1,2.00,0,OPTION,2026-09-18,600,CALL,",
                  "2026-09-02,10:30:00,SPY,SELL,1,3.00,0,OPTION,2026-09-18,600,CALL,"])
    second = post(client, ["2026-09-02,10:00:00,SPY,BUY,1,2.00,0,OPTION,2026-09-18,605,CALL,",
                           "2026-09-02,10:30:00,SPY,SELL,1,3.00,0,OPTION,2026-09-18,605,CALL,"])
    assert second["skipped"] == 0
    strikes = sorted(t["option_strike"] for t in trades(client))
    assert strikes == [600, 605]


# 4. A later file that closes an open option and opens a new one makes two trades.
def test_later_option_fills_split_into_round_trips(client):
    post(client, ["2026-09-03,10:00:00,QQQ,BUY,1,2.00,0,OPTION,2026-09-18,500,PUT,"])
    post(client, ["2026-09-04,10:00:00,QQQ,SELL,1,3.00,0,OPTION,2026-09-18,500,PUT,",
                  "2026-09-04,11:00:00,QQQ,BUY,1,4.00,0,OPTION,2026-09-18,500,PUT,"])
    rows = trades(client)
    assert len(rows) == 2
    assert sorted(t["gross_pnl"] for t in rows) == pytest.approx([0.0, 100.0])


# 5. Manual options and futures use the contract multiplier.
@pytest.mark.parametrize("instrument,ticker,entry,exit_,expected", [
    ("OPTION", "SPY", 2.0, 3.0, 100.0),
    ("FUTURE", "/ES", 6000.0, 6001.0, 50.0),
    ("FUTURE", "MES", 6000.0, 6001.0, 5.0),
    ("STOCK", "AAPL", 100.0, 101.0, 1.0),
])
def test_manual_trades_use_the_point_value(client, instrument, ticker, entry, exit_, expected):
    r = client.post("/api/trades", json={
        "account_id": 1, "date": "2026-09-05", "ticker": ticker, "instrument_type": instrument,
        "side": "LONG", "entry_price": entry, "exit_price": exit_, "quantity": 1, "commissions": 0})
    assert r.status_code == 201, r.text
    assert r.json()["gross_pnl"] == pytest.approx(expected)


# 6. Importing an earlier round trip later keeps each note on its own trade.
def test_notes_follow_their_trade_when_an_earlier_trip_is_imported(client):
    late = ["2026-09-08,11:00:00,ABC,BUY,10,50,0,STOCK,,,,",
            "2026-09-08,11:10:00,ABC,SELL,10,51,0,STOCK,,,,"]
    post(client, late)
    (group,) = [t["trade_group"] for t in trades(client)]
    conn = sqlite3.connect(client.db)
    conn.execute("INSERT INTO trade_analysis (trade_group, account_id, ticker, date, notes) "
                 "VALUES (?, 1, 'ABC', '2026-09-08', 'the 11:00 trade')", (group,))
    conn.commit()
    post(client, ["2026-09-08,09:30:00,ABC,BUY,10,40,0,STOCK,,,,",
                  "2026-09-08,09:40:00,ABC,SELL,10,42,0,STOCK,,,,"] + late)
    conn = sqlite3.connect(client.db)
    note_group = conn.execute("SELECT trade_group FROM trade_analysis WHERE notes='the 11:00 trade'").fetchone()[0]
    execs = json.loads(conn.execute("SELECT executions FROM trades WHERE trade_group=?",
                                    (note_group,)).fetchone()[0])
    assert execs[0]["time"].startswith("11:00")


# 7. Diary uploads stay inside the uploads folder and never overwrite each other.
def test_diary_upload_names_are_made_by_the_server(client, monkeypatch):
    import main
    monkeypatch.setattr(main, "analyze_diary_image", lambda *a, **k: {}, raising=False)
    monkeypatch.setattr(main, "analyze_diary_text", lambda *a, **k: {}, raising=False)
    bad = client.post("/api/upload-diary", data={"date": "../", "account_id": "1"},
                      files={"file": ("notes.txt", b"x", "text/plain")})
    assert bad.status_code >= 400
    assert not any(p.name.endswith("notes.txt") for p in client.tmp.iterdir())

    names = []
    for body in (b"first", b"second"):
        client.post("/api/upload-diary", data={"date": "2026-09-09", "account_id": "1"},
                    files={"file": ("image.txt", body, "text/plain")})
    uploads = client.tmp / "uploads"
    names = sorted(p.name for p in uploads.iterdir())
    assert len(names) == 2 and all(n.startswith("2026-09-09_1_image_") for n in names)
    assert sorted(p.read_bytes() for p in uploads.iterdir()) == [b"first", b"second"]


# 8 + 10. Profit factor uses net on both sides; open positions are not outcomes.
def test_kpis_use_net_profit_factor_and_skip_open_positions(client):
    post(client, ["2026-09-10,09:31:00,AAA,BUY,10,10,0,STOCK,,,,",
                  "2026-09-10,09:40:00,AAA,SELL,10,20,0,STOCK,,,,",     # +100
                  "2026-09-10,09:45:00,BBB,BUY,10,10,1,STOCK,,,,",
                  "2026-09-10,09:50:00,BBB,SELL,10,10.1,1,STOCK,,,,",   # gross +1, net -1
                  "2026-09-10,09:55:00,CCC,BUY,10,10,0,STOCK,,,,",
                  "2026-09-10,10:00:00,CCC,SELL,10,9.9,0,STOCK,,,,",    # -1
                  "2026-09-10,10:05:00,DDD,BUY,10,10,0,STOCK,,,,"])     # still open
    k = client.get("/api/kpis", params={"account_id": 1}).json()
    assert k["total_trades"] == 3
    assert k["win_rate"] == pytest.approx(33.33, abs=0.01)
    assert k["profit_factor"] == pytest.approx(50.0)
    assert k["open_trades"] == 1


# Second review pass ------------------------------------------------------------------------

def test_an_earlier_fill_imported_later_moves_the_reversal_point(client):
    post(client, ["2026-09-11,09:30:00,XYZ,BUY,100,10,0,STOCK,,,,",
                  "2026-09-11,09:40:00,XYZ,SELL,200,11,0,STOCK,,,,"])
    post(client, ["2026-09-11,09:35:00,XYZ,BUY,50,10,0,STOCK,,,,"])
    rows = trades(client)
    closed = [t for t in rows if t["gross_pnl"]]
    assert len(closed) == 1 and closed[0]["gross_pnl"] == pytest.approx(150.0)
    opened = [t for t in rows if not t["gross_pnl"]]
    assert len(opened) == 1 and opened[0]["side"] == "SHORT"
    assert sum(e["qty"] for e in json.loads(opened[0]["executions"])) == 50
    assert len(rows) == 2


def test_saving_an_unchanged_split_fill_keeps_reimport_dedup(client):
    rows = ["2026-09-12,09:31:00,XYZ,BUY,100,10,0,STOCK,,,,",
            "2026-09-12,09:40:00,XYZ,SELL,200,11,0,STOCK,,,,"]
    post(client, rows)
    closed = [t for t in trades(client) if t["gross_pnl"]][0]
    execs = json.loads(closed["executions"])
    idx = next(i for i, e in enumerate(execs) if e["action"] == "SOLD")
    e = execs[idx]
    r = client.put(f"/api/trades/{closed['id']}/executions/{idx}",
                   json={"date": e["date"], "time": e["time"], "action": e["action"],
                         "qty": e["qty"], "price": e["price"], "commission": e["commission"]})
    assert r.status_code == 200, r.text
    again = post(client, rows)
    assert again["imported"] == 0 and again["skipped"] == 2


def test_merged_trades_keep_both_reviews(client):
    post(client, ["2026-09-14,09:30:00,ABC,BUY,10,50,0,STOCK,,,,",
                  "2026-09-14,09:40:00,ABC,SELL,10,51,0,STOCK,,,,",
                  "2026-09-14,10:00:00,ABC,BUY,10,50,0,STOCK,,,,",
                  "2026-09-14,10:10:00,ABC,SELL,10,51,0,STOCK,,,,"])
    conn = sqlite3.connect(client.db)
    for g, note in zip([t["trade_group"] for t in trades(client)], ("first trip", "second trip")):
        conn.execute("INSERT INTO trade_analysis (trade_group, account_id, ticker, date, notes) "
                     "VALUES (?, 1, 'ABC', '2026-09-14', ?)", (g, note))
    conn.commit()
    # a missed scale-in and a late exit bridge both trips into one position
    post(client, ["2026-09-14,09:35:00,ABC,BUY,5,50,0,STOCK,,,,",
                  "2026-09-14,10:15:00,ABC,SELL,5,51,0,STOCK,,,,"])
    rows = trades(client)
    assert len(rows) == 1
    conn = sqlite3.connect(client.db)
    notes = conn.execute("SELECT notes FROM trade_analysis WHERE trade_group=?", (rows[0]["trade_group"],)).fetchone()[0]
    assert "first trip" in notes and "second trip" in notes
    assert conn.execute("SELECT COUNT(*) FROM trade_analysis").fetchone()[0] == 1


def test_contract_identity_covers_verticals_and_cross_section_matching():
    from csv_parser import _contract_id, _cross_section_key, _vertical_second_strike
    assert _contract_id("OPTION", "2026-09-18", 410, "PUT", 405) != _contract_id("OPTION", "2026-09-18", 410, "PUT", 407.5)
    assert _vertical_second_strike("9/15/26_SPY_OPTION_2026-09-18_410-405_PUT_1") == "405"
    assert _vertical_second_strike("9/15/26_SPY_OPTION_2026-09-18_410_PUT_1") is None
    base = {"iso_date": "2026-09-15", "ticker": "SPY", "action": "BOT", "qty": 1, "price": 2.0,
            "instrument_type": "OPTION", "option_expiry": "2026-09-18", "option_type": "CALL"}
    assert _cross_section_key({**base, "option_strike": 600}) != _cross_section_key({**base, "option_strike": 605})


def test_a_manual_option_closed_at_zero_counts_as_closed(client):
    r = client.post("/api/trades", json={
        "account_id": 1, "date": "2026-09-15", "ticker": "SPY", "instrument_type": "OPTION",
        "side": "SHORT", "entry_price": 2.0, "exit_price": 0.0, "quantity": 1, "commissions": 0})
    assert r.status_code == 201, r.text
    assert len(json.loads(r.json()["executions"])) == 2
    assert client.get("/api/kpis", params={"account_id": 1}).json()["total_trades"] == 1
    months = client.get("/api/yearly-kpis", params={"year": 2026, "account_id": 1}).json()
    sept = [m for m in (months["months"] if isinstance(months, dict) else months) if m.get("month") == 9][0]
    assert sept["net_pnl"] == pytest.approx(200.0)


# Third review pass -------------------------------------------------------------------------

def test_identical_reversals_in_one_second_each_keep_their_pair():
    from csv_parser import _split_at_reversals
    base = {"date": "2026-09-16", "iso_date": "2026-09-16", "time": "09:40:00", "ticker": "XYZ"}
    fills = [
        {**base, "time": "09:30:00", "action": "BOT", "qty": 100, "price": 10, "amount": -1000.0, "commission": 0},
        {**base, "action": "SOLD", "qty": 200, "price": 11, "amount": 2200.0, "commission": 0},
        {**base, "action": "BOT", "qty": 200, "price": 10.5, "amount": -2100.0, "commission": 0},
        {**base, "action": "SOLD", "qty": 200, "price": 11, "amount": 2200.0, "commission": 0},
        {**base, "time": "09:50:00", "action": "BOT", "qty": 100, "price": 10, "amount": -1000.0, "commission": 0},
    ]
    once = _split_at_reversals(fills)
    again = _split_at_reversals(once)  # a regroup rejoins and re-splits the stored halves
    for parts in (once, again):
        assert sum(f["qty"] for f in parts if f["action"] == "SOLD") == 400
        assert sum(f["qty"] for f in parts if f["action"] == "BOT") == 400
        assert sum(f["amount"] for f in parts) == pytest.approx(300.0)


def test_a_single_leg_fill_does_not_join_an_open_vertical(client):
    conn = sqlite3.connect(client.db)
    conn.execute(
        "INSERT INTO trades (account_id, trade_group, date, ticker, instrument_type, side, gross_pnl, net_pnl, "
        "commissions, executions, option_expiry, option_strike, option_type, source) VALUES "
        "(1, '9/15/26_SPY_OPTION_2026-09-18_600-595_PUT_1', '2026-09-15', 'SPY', 'OPTION', 'SHORT', 0, 0, 0, ?, "
        "'2026-09-18', 600, 'PUT', 'imported')",
        (json.dumps([{"date": "2026-09-15", "time": "10:00:00", "action": "SOLD", "qty": 1, "price": 2.0, "commission": 0}]),))
    conn.commit()
    post(client, ["2026-09-16,10:00:00,SPY,BUY,1,3.00,0,OPTION,2026-09-18,600,PUT,"])
    rows = {t["trade_group"]: t for t in trades(client)}
    vertical = rows["9/15/26_SPY_OPTION_2026-09-18_600-595_PUT_1"]
    assert len(json.loads(vertical["executions"])) == 1 and vertical["gross_pnl"] == 0
    assert len(rows) == 2


def test_merge_keeps_every_review_field(client):
    post(client, ["2026-09-17,09:30:00,ABC,BUY,10,50,0,STOCK,,,,",
                  "2026-09-17,09:40:00,ABC,SELL,10,51,0,STOCK,,,,",
                  "2026-09-17,10:00:00,ABC,BUY,10,50,0,STOCK,,,,",
                  "2026-09-17,10:10:00,ABC,SELL,10,51,0,STOCK,,,,"])
    first, second = [t["trade_group"] for t in trades(client)]
    conn = sqlite3.connect(client.db)
    conn.execute("INSERT INTO trade_analysis (trade_group, account_id, ticker, date, notes) VALUES (?, 1, 'ABC', '2026-09-17', 'a')", (first,))
    conn.execute("INSERT INTO trade_analysis (trade_group, account_id, ticker, date, notes, target_price, idea_source, trade_rating) "
                 "VALUES (?, 1, 'ABC', '2026-09-17', 'b', 55, 'Watchlist', 4)", (second,))
    conn.commit()
    post(client, ["2026-09-17,09:35:00,ABC,BUY,5,50,0,STOCK,,,,",
                  "2026-09-17,10:15:00,ABC,SELL,5,51,0,STOCK,,,,"])
    conn = sqlite3.connect(client.db)
    row = conn.execute("SELECT notes, target_price, idea_source, trade_rating FROM trade_analysis").fetchall()
    assert len(row) == 1
    notes, target, source, rating = row[0]
    assert "a" in notes and "b" in notes and target == 55 and source == "Watchlist" and rating == 4


# Review of the private adaptation ------------------------------------------------------------

def test_notes_on_the_short_half_of_a_reversal_stay_on_the_short(client):
    post(client, ["2026-09-18,09:31:00,XYZ,BUY,100,10,0,STOCK,,,,",
                  "2026-09-18,09:40:00,XYZ,SELL,200,11,0,STOCK,,,,"])
    short = [t for t in trades(client) if not t["gross_pnl"]][0]["trade_group"]
    conn = sqlite3.connect(client.db)
    conn.execute("INSERT INTO trade_analysis (trade_group, account_id, ticker, date, notes) "
                 "VALUES (?, 1, 'XYZ', '2026-09-18', 'short idea')", (short,))
    conn.commit()
    post(client, ["2026-09-18,10:00:00,XYZ,BUY,100,10.5,0,STOCK,,,,"])
    conn = sqlite3.connect(client.db)
    group = conn.execute("SELECT trade_group FROM trade_analysis WHERE notes='short idea'").fetchone()[0]
    side = conn.execute("SELECT side FROM trades WHERE trade_group=?", (group,)).fetchone()[0]
    assert side == "SHORT"


def test_a_failed_rebuild_undoes_the_import(client):
    # an open position: closing it on a later day rebuilds it under a new name
    post(client, ["2026-09-19,09:30:00,ABC,BUY,10,50,0,STOCK,,,,"])
    before = trades(client)
    conn = sqlite3.connect(client.db)
    conn.execute("CREATE TRIGGER no_abc BEFORE INSERT ON trades WHEN NEW.ticker = 'ABC' "
                 "BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    conn.commit()
    content = HEADER + "2026-09-22,10:10:00,ABC,SELL,10,52,0,STOCK,,,,\n"
    try:
        client.post("/api/import-csv", data={"account_id": "1", "broker": "generic"},
                    files={"file": ("t.csv", content.encode(), "text/csv")})
    except Exception:
        pass  # the test client may re-raise the server error
    after = trades(client)
    assert [t["trade_group"] for t in after] == [t["trade_group"] for t in before]
    assert after[0]["executions"] == before[0]["executions"]
