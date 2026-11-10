"""Edge Trader for Schwab -> journal sync (edge_sync.py, schwab_adapter.py).

    cd backend && python -m pytest tests/test_edge_sync.py -q

All data is synthetic: invented tickers' prices, sizes, ids and notes. Schwab transactions are
built in the shape the Trader API returns.
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import schwab_adapter as sa  # noqa: E402

HEAD = "Account Statement\n\nCash Balance\nDATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE\n"

_ids = iter(range(900000, 999999))


def fee(x):
    return {"instrument": {"assetType": "CURRENCY", "symbol": "CURRENCY_USD"}, "amount": x, "cost": -x,
            "feeType": "COMMISSION"}


def stock(utc, sym, qty, price, order=None, fees=0.0, aid=None):
    """qty > 0 buys. utc like '2026-11-04T14:40:18+0000'."""
    return {"activityId": aid or next(_ids), "time": utc, "type": "TRADE", "status": "VALID",
            "orderId": order or next(_ids), "tradeDate": utc,
            "transferItems": ([fee(fees)] if fees else []) + [
                {"instrument": {"assetType": "EQUITY", "symbol": sym}, "amount": qty, "price": price,
                 "cost": round(-qty * price, 2), "positionEffect": "OPENING"}]}


def option(utc, occ, qty, price, order=None, fees=0.0, aid=None):
    root = occ[:6].strip()
    return {"activityId": aid or next(_ids), "time": utc, "type": "TRADE", "status": "VALID",
            "orderId": order or next(_ids), "tradeDate": utc,
            "transferItems": ([fee(fees)] if fees else []) + [
                {"instrument": {"assetType": "OPTION", "symbol": occ, "underlyingSymbol": root},
                 "amount": qty, "price": price, "cost": round(-qty * price * 100, 2)}]}


def future(utc, sym, qty, price, trade_date, mult=50, fees=0.0, aid=None):
    return {"activityId": aid or next(_ids), "time": utc, "type": "TRADE", "status": "VALID",
            "orderId": next(_ids), "tradeDate": trade_date,
            "transferItems": ([fee(fees)] if fees else []) + [
                {"instrument": {"assetType": "FUTURE", "symbol": sym}, "amount": qty, "price": price,
                 "cost": round(-qty * price * mult, 2)}]}


# ── Adapter ────────────────────────────────────────────────────────────────────

def test_stock_fill_matches_statement_shape():
    ex, problems = sa.transactions_to_executions([stock("2026-11-04T14:40:18+0000", "MSFT", 120, 410.2319)])
    assert not problems
    (e,) = ex
    assert (e["date"], e["iso_date"], e["time"], e["action"], e["qty"], e["price"], e["ticker"]) == \
        ("11/4/26", "2026-11-04", "09:40:18", "BOT", 120, 410.2319, "MSFT")
    assert e["amount"] == -49227.83 and e["instrument_type"] == "STOCK"


def test_sub_penny_price_rounds_like_thinkorswim():
    (e,), _ = sa.transactions_to_executions([stock("2026-11-04T20:46:22+0000", "NVDA", -300, 180.443723)])
    assert e["price"] == 180.44372 and e["action"] == "SOLD"


def test_vertical_from_two_leg_transactions():
    order = 5001
    legs = [option("2026-11-04T15:13:02+0000", "SPY   261104P00600000", -1, 2.40, order=order, fees=0.65, aid=10),
            option("2026-11-04T15:13:02+0000", "SPY   261104P00595000", 1, 1.10, order=order, fees=0.65, aid=11)]
    (v,), _ = sa.transactions_to_executions(legs)
    assert (v["action"], v["qty"], v["price"], v["option_strike"], v["option_strike2"], v["option_type"]) == \
        ("SOLD", 1, 1.3, 600.0, 595.0, "PUT")
    assert v["commission"] == 1.3 and v["amount"] == 130.0 and v["_fill_keys"] == ["10", "11"]


def test_vertical_close_lists_the_bought_leg_first():
    order = 77
    legs = [option("2026-11-04T15:18:26+0000", "SPY   261104P00595000", -1, 0.80, order=order, aid=20),
            option("2026-11-04T15:18:26+0000", "SPY   261104P00600000", 1, 1.95, order=order, aid=21)]
    (v,), _ = sa.transactions_to_executions(legs)
    assert (v["action"], v["price"], v["option_strike"], v["option_strike2"]) == ("BOT", 1.15, 600.0, 595.0)


def test_futures_use_trade_date_and_drop_exchange():
    (e,), _ = sa.transactions_to_executions([
        future("2026-11-06T02:10:38+0000", "/NQZ26:XCME", 1, 21000.25, "2026-11-06T05:00:00+0000", mult=20)])
    assert (e["ticker"], e["iso_date"], e["date"], e["time"]) == ("/NQZ26", "2026-11-06", "11/6/26", "21:10:38")


def test_spxw_option_is_booked_as_spx():
    (e,), _ = sa.transactions_to_executions([option("2026-11-02T16:16:34+0000", "SPXW  261102P06000000", -1, 3.2)])
    assert (e["ticker"], e["option_expiry"], e["option_strike"]) == ("SPX", "2026-11-02", 6000.0)


def test_busted_and_non_trade_transactions_are_skipped():
    t = stock("2026-11-04T14:40:18+0000", "MSFT", 1, 1.0)
    t["status"] = "INVALID"
    ex, _ = sa.transactions_to_executions([t, {"type": "DIVIDEND_OR_INTEREST", "activityId": 1}])
    assert ex == []


# ── Sync endpoint ──────────────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "journal.db"
    monkeypatch.setenv("DATABASE_PATH", str(db))
    (tmp_path / "uploads").mkdir()
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    for name in ("database", "main", "edge_sync", "trade_store"):
        sys.modules.pop(name, None)
    import database
    database.DB_PATH = str(db)
    import main
    import edge_sync
    edge_sync.DB_PATH = str(db)
    # Never reach a real AI from tests, whatever the environment holds.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(edge_sync, "start_feedback", lambda targets: None)
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        c.db = str(db)
        conn = sqlite3.connect(c.db)
        for i, name in ((1, "Main"), (2, "Second"), (3, "Futures")):
            conn.execute("INSERT OR IGNORE INTO accounts (id, name, type) VALUES (?, ?, 'day_trading')", (i, name))
        conn.commit()
        conn.close()
        yield c


def q(c, sql, *args):
    conn = sqlite3.connect(c.db)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


BASE = {"routes": {"STOCK": 1, "OPTION": 1, "FUTURE": 3}, "dedup_accounts": [1, 2, 3], "exclude_symbols": ["IWM"]}

DAY = [  # an AAPL round trip, 10:00 to 10:05 ET on Nov 4
    stock("2026-11-04T15:00:00+0000", "AAPL", 100, 200.0, aid=1001),
    stock("2026-11-04T15:05:00+0000", "AAPL", -100, 201.0, fees=0.5, aid=1002),
]
DAY_CSV = [
    '11/4/26,10:00:00,TRD,="1",BOT +100 AAPL @200.00,,,"-20,000.00","1"',
    '11/4/26,10:05:00,TRD,="2",SOLD -100 AAPL @201.00,-0.50,,"20,100.00","1"',
]
GROUP = "11/4/26_AAPL_STOCK_1"


def sync(c, dry_run=True, **kw):
    body = {**BASE, "dry_run": dry_run, **kw}
    r = c.post("/api/sync/edge-trader", json=body)
    assert r.status_code in (200, 409), r.text
    return r


def apply(c, **kw):
    token = sync(c, True, **kw).json()["state_token"]
    r = sync(c, False, state_token=token, **kw)
    assert r.status_code == 200, r.text
    return r.json()


def import_csv(c, rows, account="1"):
    r = c.post("/api/import-csv", data={"account_id": account, "broker": "thinkorswim"},
               files={"file": ("s.csv", (HEAD + "\n".join(rows) + "\n").encode(), "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()


def test_info(client):
    j = client.get("/api/sync/info").json()
    assert j["api"] == "edge-trader-sync" and j["api_version"] == 1
    assert "strategy" in j["fields"] and "stop_moved" in j["event_kinds"] and "mistake" in j["tag_types"]
    assert j["journal_version"] == (BACKEND.parent / "VERSION").read_text().strip()


def test_dry_run_writes_nothing_and_apply_writes_once(client):
    pre = sync(client, transactions=DAY).json()
    assert pre["dry_run"] is True and pre["applied"] is False
    assert pre["fills"] == {"new": 2} and pre["trades"][0]["trade_group"] == GROUP
    assert q(client, "SELECT COUNT(*) n FROM trades")[0]["n"] == 0
    done = apply(client, transactions=DAY)
    assert done["applied"] and done["backup"]
    (t,) = q(client, "SELECT * FROM trades")
    assert t["net_pnl"] == 99.5 and t["source"] == "imported"
    again = apply(client, transactions=DAY)
    assert again["fills"] == {"already": 2} and again["trades"] == []


def test_server_side_post_needs_no_browser_origin(client):
    """Edge Trader's Python server posts without an Origin header: no CORS involved."""
    r = client.post("/api/sync/edge-trader", json={**BASE, "transactions": DAY}, headers={})
    assert r.status_code == 200 and "access-control-allow-origin" not in r.headers


def test_stale_preview_is_refused(client):
    token = sync(client, transactions=DAY).json()["state_token"]
    import_csv(client, DAY_CSV[:1])
    r = sync(client, False, state_token=token, transactions=DAY)
    assert r.status_code == 409 and r.json()["applied"] is False and r.json()["state_token"] != token
    assert q(client, "SELECT COUNT(*) n FROM trades")[0]["n"] == 1   # only the CSV's


def test_csv_then_sync_then_csv_never_duplicates(client):
    import_csv(client, DAY_CSV)
    assert apply(client, transactions=DAY)["fills"] == {"already": 2}
    assert import_csv(client, DAY_CSV)["imported"] == 0
    (t,) = q(client, "SELECT executions FROM trades")
    assert len(json.loads(t["executions"])) == 2


def test_sync_then_csv_with_more_fills_regroups_and_annotations_follow(client):
    apply(client, transactions=DAY, annotations=[{"id": "a1", "fill_keys": ["1001", "1002"],
                                                 "fields": {"strategy": "Opening Range"}}])
    more = DAY_CSV + ['11/4/26,10:10:00,TRD,="3",BOT +50 AAPL @202.00,,,"-10,100.00","1"',
                      '11/4/26,10:12:00,TRD,="4",SOLD -50 AAPL @203.00,-0.25,,"10,150.00","1"']
    assert import_csv(client, more)["imported"] == 2
    groups = {t["trade_group"] for t in q(client, "SELECT trade_group FROM trades")}
    assert groups == {GROUP, "11/4/26_AAPL_STOCK_2"}
    (a,) = q(client, "SELECT * FROM trade_analysis")
    assert a["trade_group"] == GROUP and a["strategy"] == "Opening Range"


def test_an_earlier_round_trip_imported_later_moves_the_timeline_with_its_trade(client):
    """The import renames the synced trade (_1 becomes _2): its timeline follows its fills."""
    apply(client, transactions=DAY, annotations=[{"id": "a1", "fill_keys": ["1001", "1002"], "fields": {}}],
          events=[{"id": "e1", "annotation_id": "a1", "ts": "2026-11-04T15:00:30Z", "kind": "stop_set",
                   "price": 199.0}])
    earlier = ['11/4/26,09:40:00,TRD,="5",BOT +10 AAPL @198.00,,,"-1,980.00","1"',
               '11/4/26,09:45:00,TRD,="6",SOLD -10 AAPL @199.00,,,"1,990.00","1"']
    import_csv(client, earlier)
    rows = {t["trade_group"]: json.loads(t["executions"])[0]["time"]
            for t in q(client, "SELECT trade_group, executions FROM trades")}
    assert rows == {GROUP: "09:40:00", "11/4/26_AAPL_STOCK_2": "10:00:00"}
    tl = client.get("/api/sync/trade-events", params={"trade_group": "11/4/26_AAPL_STOCK_2", "account_id": 1}).json()
    assert [e["id"] for e in tl["events"]] == ["e1"]


def test_a_fill_imported_into_another_account_is_not_added_again(client):
    import_csv(client, DAY_CSV, account="2")
    out = apply(client, transactions=DAY)
    assert out["fills"] == {"already": 2}
    assert q(client, "SELECT COUNT(*) n FROM trades WHERE account_id=1")[0]["n"] == 0


def test_a_synced_trade_moved_to_another_account_is_not_added_again(client):
    """L9: sync, move the trade in Trade View, sync again: no duplicate, and its picks and timeline
    follow it to the new account."""
    ev = [{"id": "e1", "annotation_id": "a1", "ts": "2026-11-04T15:00:30Z", "kind": "stop_set", "price": 199.0}]
    apply(client, transactions=DAY, annotations=[ANN], events=ev)
    (t,) = q(client, "SELECT id FROM trades")
    assert client.patch(f"/api/trades/{t['id']}/account", json={"account_id": 2}).status_code == 200
    out = apply(client, transactions=DAY, annotations=[ANN], events=ev)
    assert out["fills"] == {"already": 2} and out["trades"] == []
    assert [(r["account_id"], r["trade_group"]) for r in q(client, "SELECT account_id, trade_group FROM trades")] ==         [(2, GROUP)]
    (ann,) = out["annotations"]
    assert ann["status"] == "ok" and ann["trades"] == [{"account_id": 2, "trade_group": GROUP}]
    assert {f["status"] for f in ann["fields"]} == {"same"}
    tl = client.get("/api/sync/trade-events", params={"trade_group": GROUP, "account_id": 2}).json()
    assert [e["id"] for e in tl["events"]] == ["e1"]


def test_statement_fill_that_merged_two_schwab_fills_covers_them(client):
    import_csv(client, ['11/4/26,10:00:00,TRD,="1",BOT +200 AMD @150.00,,,"-30,000.00","1"'])
    two = [stock("2026-11-04T15:00:00+0000", "AMD", 100, 150.0), stock("2026-11-04T15:00:01+0000", "AMD", 100, 150.0)]
    assert apply(client, transactions=two)["fills"] == {"covered": 2}
    assert q(client, "SELECT COUNT(*) n FROM trades")[0]["n"] == 1


def test_identical_fills_count_one_to_one(client):
    import_csv(client, ['11/4/26,10:00:00,TRD,="1",SOLD -100 INTC @25.50,,,"2,550.00","1"'])
    same = [stock("2026-11-04T15:00:00+0000", "INTC", -100, 25.50, order=5),
            stock("2026-11-04T15:00:00+0000", "INTC", -100, 25.50, order=5)]
    out = apply(client, transactions=same)
    assert out["fills"] == {"already": 1, "new": 1}
    (t,) = q(client, "SELECT executions FROM trades")
    assert len(json.loads(t["executions"])) == 2


def test_futures_route_to_their_account_and_excluded_symbols_skip(client):
    txns = [future("2026-11-04T15:31:32+0000", "/ESZ26:XCME", 2, 6000.0, "2026-11-04T04:00:00+0000"),
            future("2026-11-04T15:34:36+0000", "/ESZ26:XCME", -2, 6000.25, "2026-11-04T04:00:00+0000"),
            option("2026-11-04T15:00:00+0000", "IWM   261113P00200000", -1, 10.0)]
    out = apply(client, transactions=txns)
    assert out["fills"] == {"new": 2, "excluded": 1}
    (t,) = q(client, "SELECT * FROM trades")
    assert t["account_id"] == 3 and t["ticker"] == "/ESZ26" and t["gross_pnl"] == 25.0
    out = apply(client, transactions=txns[:2], exclude_symbols=["/ES"])
    assert out["fills"] == {"excluded": 2}


def test_unknown_route_account_is_refused(client):
    r = client.post("/api/sync/edge-trader", json={**BASE, "routes": {"STOCK": 99}, "transactions": DAY})
    assert r.status_code == 422


def test_a_reversal_fill_counts_once_and_each_side_keeps_its_annotation(client):
    """The journal splits a fill that flips the position into two trades. Re-syncing finds the one
    Schwab fill behind both halves, and each Edge Trader trade lands on its own half."""
    flip = [stock("2026-11-04T15:00:00+0000", "AMD", 100, 150.0, aid=2001),
            stock("2026-11-04T15:10:00+0000", "AMD", -200, 151.0, aid=2002),
            stock("2026-11-04T15:20:00+0000", "AMD", 100, 149.0, aid=2003)]
    long_ = {"id": "t2001", "fill_keys": ["2001", "2002"], "fields": {"strategy": "Opening Range"}}
    short = {"id": "t2002", "fill_keys": ["2002", "2003"], "fields": {"strategy": "VWAP Reclaim"}}
    out = apply(client, transactions=flip, annotations=[long_, short])
    assert out["fills"] == {"new": 3}
    trades = {t["trade_group"]: t for t in q(client, "SELECT * FROM trades")}
    assert {g: t["side"] for g, t in trades.items()} == {"11/4/26_AMD_STOCK_1": "LONG", "11/4/26_AMD_STOCK_2": "SHORT"}
    st = {a["id"]: (a["status"], a["trades"]) for a in out["annotations"]}
    assert st["t2001"][0] == "ok" and st["t2002"][0] == "ok"
    picks = {a["trade_group"]: a["strategy"] for a in q(client, "SELECT * FROM trade_analysis")}
    assert picks == {"11/4/26_AMD_STOCK_1": "Opening Range", "11/4/26_AMD_STOCK_2": "VWAP Reclaim"}
    again = apply(client, transactions=flip, annotations=[long_, short])
    assert again["fills"] == {"already": 3} and again["trades"] == []


def test_a_reversal_that_opened_a_trade_still_open_anchors_it(client):
    flip = [stock("2026-11-04T15:00:00+0000", "AMD", 100, 150.0, aid=2101),
            stock("2026-11-04T15:10:00+0000", "AMD", -200, 151.0, aid=2102)]
    out = apply(client, transactions=flip, annotations=[
        {"id": "t2102", "fill_keys": ["2102"], "fields": {"strategy": "VWAP Reclaim"}}])
    (ann,) = out["annotations"]
    assert ann["status"] == "ok" and ann["trades"] == [{"account_id": 1, "trade_group": "11/4/26_AMD_STOCK_2"}]


# ── Fields, tags, events, notes ────────────────────────────────────────────────

ANN = {"id": "a1", "fill_keys": ["1001", "1002"], "updated_at": "2026-11-04T15:06:00Z",
       "fields": {"strategy": "Opening Range", "idea_source": "Scanner", "stop_loss": 199.5,
                  "emotional_state": "calm", "entry_reason": "reclaim of VWAP"}}


def analysis(c):
    rows = q(c, "SELECT * FROM trade_analysis")
    return rows[0] if rows else {}


def test_fields_are_written_then_a_journal_edit_wins(client):
    out = apply(client, transactions=DAY, annotations=[ANN])
    (ann,) = out["annotations"]
    assert ann["status"] == "ok" and {f["status"] for f in ann["fields"]} == {"write"}
    a = analysis(client)
    assert (a["strategy"], a["idea_source"], a["stop_loss"], a["emotional_state"]) == \
        ("Opening Range", "Scanner", 199.5, "calm")

    # The trader edits the strategy in the journal, then changes it in Edge Trader too.
    client.patch(f"/api/trades/{GROUP}/analysis?account_id=1", json={"strategy": "Breakout Retest"})
    edited = {**ANN, "fields": {**ANN["fields"], "strategy": "VWAP Reclaim", "emotional_state": "anxious"}}
    out = apply(client, transactions=DAY, annotations=[edited])
    st = {f["field"]: f["status"] for f in out["annotations"][0]["fields"]}
    assert st["strategy"] == "conflict" and st["emotional_state"] == "write" and st["stop_loss"] == "same"
    a = analysis(client)
    assert a["strategy"] == "Breakout Retest" and a["emotional_state"] == "anxious"


def test_blank_pick_never_clears_and_alias_counts_as_unchanged(client):
    apply(client, transactions=DAY, annotations=[ANN])
    conn = sqlite3.connect(client.db)
    conn.execute("INSERT INTO library_aliases (kind, tag_type, alias, canonical) VALUES ('source','','Scanner','Gap Scanner')")
    conn.execute("UPDATE trade_analysis SET idea_source='Gap Scanner'")
    conn.commit()
    conn.close()
    blank = {**ANN, "fields": {"strategy": "", "idea_source": "Gap list"}}
    out = apply(client, transactions=DAY, annotations=[blank])
    st = {f["field"]: f["status"] for f in out["annotations"][0]["fields"]}
    assert "strategy" not in st and st["idea_source"] == "write"
    a = analysis(client)
    assert a["strategy"] == "Opening Range" and a["idea_source"] == "Gap list"


def test_tags_added_once_and_only_sync_tags_removed(client):
    tagged = {**ANN, "tags": {"mistake": ["moved stop"], "emotion": ["calm"]}}
    apply(client, transactions=DAY, annotations=[tagged])
    client.post(f"/api/trades/{GROUP}/tags?account_id=1", json={"tag_type": "mistake", "tag_value": "chased"})
    out = apply(client, transactions=DAY, annotations=[tagged])
    assert out["annotations"][0]["tags"]["add"] == []
    fewer = {**ANN, "tags": {"mistake": [], "emotion": ["calm"]}}
    out = apply(client, transactions=DAY, annotations=[fewer])
    assert out["annotations"][0]["tags"]["remove"] == [{"type": "mistake", "value": "moved stop"}]
    tags = {(t["tag_type"], t["tag_value"]) for t in q(client, "SELECT * FROM trade_tags")}
    assert tags == {("emotion", "calm"), ("mistake", "chased")}


def test_annotation_without_a_known_fill_waits(client):
    out = apply(client, transactions=DAY, annotations=[{**ANN, "fill_keys": ["555"]}],
                events=[{"id": "e1", "annotation_id": "a1", "ts": "2026-11-04T15:01:00Z", "kind": "note",
                         "text": "held the level"}])
    assert out["annotations"][0]["status"] == "pending" and out["events"]["pending"] == 1
    assert analysis(client) == {}


def test_two_annotations_on_one_trade_apply_the_latest(client):
    older = {**ANN, "id": "a0", "fill_keys": ["1001"], "updated_at": "2026-11-04T15:01:00Z",
             "fields": {"strategy": "VWAP Reclaim"}}
    newer = {**ANN, "id": "a1", "fill_keys": ["1002"], "updated_at": "2026-11-04T15:09:00Z",
             "fields": {"strategy": "Opening Range"}}
    out = apply(client, transactions=DAY, annotations=[older, newer])
    st = {a["id"]: a["status"] for a in out["annotations"]}
    assert st == {"a0": "merge", "a1": "ok"} and analysis(client)["strategy"] == "Opening Range"


def test_events_timeline_and_updates(client):
    ev = [{"id": "e1", "annotation_id": "a1", "ts": "2026-11-04T15:00:30Z", "kind": "stop_set", "price": 199.0},
          {"id": "e2", "annotation_id": "a1", "ts": "2026-11-04T15:02:00Z", "kind": "stop_moved", "price": 200.0},
          {"id": "e3", "annotation_id": "a1", "ts": "2026-11-04T15:03:00Z", "kind": "note", "text": "B/E now"}]
    out = apply(client, transactions=DAY, annotations=[ANN], events=ev)
    assert out["events"] == {"new": 3}
    ev[2]["text"] = "moved to B/E after the push"
    out = apply(client, transactions=DAY, annotations=[ANN], events=ev)
    assert out["events"] == {"same": 2, "updated": 1}
    tl = client.get("/api/sync/trade-events", params={"trade_group": GROUP, "account_id": 1}).json()
    assert [e["kind"] for e in tl["events"]] == ["stop_set", "stop_moved", "note"]
    assert tl["events"][2]["text"] == "moved to B/E after the push"
    assert set(tl["events"][0]) == {"id", "ts", "kind", "price", "qty", "text", "origin"}


def test_unknown_event_kind_and_field_are_refused(client):
    r = client.post("/api/sync/edge-trader", json={**BASE, "events": [
        {"id": "x", "ts": "2026-11-04T15:00:00Z", "kind": "rocket"}]})
    assert r.status_code == 422
    r = client.post("/api/sync/edge-trader", json={**BASE, "annotations": [{"id": "x", "fields": {"pnl": 1}}]})
    assert r.status_code == 422


def test_day_note_upserts(client):
    note = {"date": "2026-11-04", "account_id": 1, "text": "Plan: only A setups."}
    assert apply(client, day_notes=[note])["day_notes"][0]["status"] == "new"
    assert apply(client, day_notes=[note])["day_notes"][0]["status"] == "same"
    note["text"] += " Stopped after two losses."
    assert apply(client, day_notes=[note])["day_notes"][0]["status"] == "updated"
    (d,) = q(client, "SELECT * FROM diary_entries")
    assert d["source"] == "edge-trader" and d["raw_text"].endswith("two losses.")
    (listed,) = client.get("/api/diary").json()
    assert listed["source"] == "edge-trader" and listed["updated_at"]


def test_deleting_a_trade_releases_its_sync_rows(client):
    apply(client, transactions=DAY, annotations=[{**ANN, "tags": {"emotion": ["calm"]}}],
          events=[{"id": "e1", "annotation_id": "a1", "ts": "2026-11-04T15:00:30Z", "kind": "stop_set", "price": 199}])
    (t,) = q(client, "SELECT id FROM trades")
    client.delete(f"/api/trades/{t['id']}")
    assert q(client, "SELECT trade_group FROM trade_events")[0]["trade_group"] is None
    assert q(client, "SELECT COUNT(*) n FROM sync_tags")[0]["n"] == 0


def test_moving_a_trade_releases_its_sync_rows(client):
    apply(client, transactions=DAY, annotations=[ANN],
          events=[{"id": "e1", "annotation_id": "a1", "ts": "2026-11-04T15:00:30Z", "kind": "stop_set", "price": 199}])
    (t,) = q(client, "SELECT id FROM trades")
    assert client.patch(f"/api/trades/{t['id']}/account", json={"account_id": 2}).status_code == 200
    assert q(client, "SELECT trade_group FROM trade_events")[0]["trade_group"] is None
    assert analysis(client)["account_id"] == 2


def test_deleting_an_account_drops_its_sync_rows(client):
    apply(client, transactions=DAY, annotations=[ANN],
          day_notes=[{"date": "2026-11-04", "account_id": 1, "text": "Plan."}])
    assert client.delete("/api/accounts/1").status_code == 200
    for table in ("sync_fills", "sync_annotations", "sync_day_notes", "sync_field_state"):
        assert q(client, f"SELECT COUNT(*) n FROM {table} WHERE account_id=1")[0]["n"] == 0


def test_backups_are_capped(client, monkeypatch):
    import edge_sync
    monkeypatch.setattr(edge_sync, "BACKUPS_KEPT", 2)
    for _ in range(4):
        apply(client, transactions=DAY)
    folder = Path(client.db).parent / "sync-backups"
    assert len(list(folder.glob("*.presync-*.db"))) == 2


# ── AI feedback ────────────────────────────────────────────────────────────────

def test_without_an_ai_key_feedback_is_skipped(client):
    out = apply(client, transactions=DAY, annotations=[ANN])
    assert out["feedback"]["queued"] == 0 and "No AI key" in out["feedback"]["skipped"]


def test_ai_feedback_fills_blanks_only_and_a_later_pick_wins(client, monkeypatch):
    import edge_sync
    seen = {}

    def fake_ai(contexts):
        (c,) = contexts
        seen.update(c)
        return {c["key"]: {"key": c["key"], "ai_feedback": "Clean entry, stop managed well.",
                           "fill": {"exit_reason": "took profit into resistance", "entry_reason": "ignored",
                                    "strategy": "not allowed"}}}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(edge_sync, "ask_ai", fake_ai)
    monkeypatch.setattr(edge_sync, "start_feedback", lambda targets: edge_sync.run_feedback(targets))
    out = apply(client, transactions=DAY, annotations=[ANN],
                events=[{"id": "n1", "annotation_id": "a1", "ts": "2026-11-04T15:04:00Z", "kind": "note",
                         "text": "sold into the 201 shelf"}])
    assert out["feedback"]["queued"] == 1
    assert "exit_reason" in seen["blank_fields"] and "entry_reason" not in seen["blank_fields"]
    assert any(e["kind"] == "note" for e in seen["events"])
    a = analysis(client)
    assert a["ai_feedback"] == "Clean entry, stop managed well."
    assert a["exit_reason"] == "took profit into resistance"
    assert a["entry_reason"] == "reclaim of VWAP" and a["strategy"] == "Opening Range"
    (st,) = q(client, "SELECT writer FROM sync_field_state WHERE field='exit_reason'")
    assert st["writer"] == "ai"
    # The trader later picks an exit reason in Edge Trader: it replaces the AI's guess.
    picked = {**ANN, "fields": {**ANN["fields"], "exit_reason": "hit my target"}}
    monkeypatch.setattr(edge_sync, "start_feedback", lambda targets: None)
    out = apply(client, transactions=DAY, annotations=[picked])
    st = {f["field"]: f["status"] for f in out["annotations"][0]["fields"]}
    assert st["exit_reason"] == "write" and analysis(client)["exit_reason"] == "hit my target"


def test_ai_failure_leaves_the_sync_intact(client, monkeypatch):
    import edge_sync

    def boom(contexts):
        raise RuntimeError("overloaded")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(edge_sync, "ask_ai", boom)
    monkeypatch.setattr(edge_sync, "start_feedback", lambda targets: edge_sync.run_feedback(targets))
    out = apply(client, transactions=DAY, annotations=[ANN])
    assert out["applied"] and analysis(client)["strategy"] == "Opening Range"
    assert analysis(client)["ai_feedback"] is None


def test_clearing_a_days_diary_keeps_the_edge_trader_day_note(client):
    apply(client, day_notes=[{"date": "2026-11-04", "account_id": 1, "text": "Plan: A setups only."}])
    conn = sqlite3.connect(client.db)
    conn.execute("INSERT INTO diary_entries (account_id, entry_date, raw_text) VALUES (1, '2026-11-04', 'upload')")
    conn.commit()
    conn.close()
    assert client.delete("/api/diary/by-date/2026-11-04?account_id=1").status_code == 200
    (d,) = q(client, "SELECT source, raw_text FROM diary_entries")
    assert d["source"] == "edge-trader"


# ── Regressions ────────────────────────────────────────────────────────────────

def vertical(utc, qty_sign, short_px, long_px, order, aids):
    """An SPY 600/595 put vertical: qty_sign -1 opens (sell 600, buy 595), +1 closes."""
    return [option(utc, "SPY   261104P00600000", qty_sign, short_px, order=order, aid=aids[0]),
            option(utc, "SPY   261104P00595000", -qty_sign, long_px, order=order, aid=aids[1])]


def test_a_second_round_trip_never_overwrites_a_stored_vertical(client):
    first = vertical("2026-11-04T15:13:02+0000", -1, 2.40, 1.10, 1, (1, 2)) + \
        vertical("2026-11-04T15:18:26+0000", 1, 1.95, 0.80, 2, (3, 4))
    apply(client, transactions=first)
    second = first + vertical("2026-11-04T15:21:26+0000", -1, 2.10, 1.00, 3, (5, 6)) + \
        vertical("2026-11-04T16:55:19+0000", 1, 1.60, 0.70, 4, (7, 8))
    out = apply(client, transactions=second)
    assert out["fills"] == {"already": 2, "new": 2}
    trades = q(client, "SELECT trade_group, executions, net_pnl FROM trades ORDER BY trade_group")
    assert len(trades) == 2 and all(len(json.loads(t["executions"])) == 2 for t in trades)
    assert [t["trade_group"].rsplit("_", 1)[1] for t in trades] == ["1", "2"]
    assert [t["net_pnl"] for t in trades] == [15.0, 20.0]
    assert any("kept apart" in p["reason"] for p in out["problems"])


def test_a_trade_edited_in_the_journal_is_not_overwritten(client):
    apply(client, transactions=DAY)
    conn = sqlite3.connect(client.db)
    conn.execute("UPDATE trades SET source='edited', net_pnl=50")
    conn.commit()
    conn.close()
    later = DAY + [stock("2026-11-04T16:00:00+0000", "AAPL", 50, 202.0, aid=1003),
                   stock("2026-11-04T16:05:00+0000", "AAPL", -50, 203.0, aid=1004)]
    apply(client, transactions=later)
    rows = {t["trade_group"]: t for t in q(client, "SELECT * FROM trades")}
    assert rows[GROUP]["net_pnl"] == 50 and len(rows) == 2


def test_merged_statement_fill_covers_in_time_order_and_the_rest_is_new(client):
    import_csv(client, ['11/4/26,10:00:00,TRD,="1",BOT +200 AMD @150.00,,,"-30,000.00","1"'])
    three = [stock("2026-11-04T15:00:00+0000", "AMD", 100, 150.0), stock("2026-11-04T15:00:01+0000", "AMD", 100, 150.0),
             stock("2026-11-04T19:00:00+0000", "AMD", 50, 150.0)]
    out = apply(client, transactions=three)
    assert out["fills"] == {"covered": 2, "new": 1}
    bought = sum(e["qty"] for t in q(client, "SELECT executions FROM trades")
                 for e in json.loads(t["executions"]) if e["action"] == "BOT")
    assert bought == 250


def test_partly_covered_fill_is_reported_not_added(client):
    import_csv(client, ['11/4/26,10:00:00,TRD,="1",BOT +150 AMD @150.00,,,"-22,500.00","1"'])
    two = [stock("2026-11-04T15:00:00+0000", "AMD", 100, 150.0), stock("2026-11-04T15:00:01+0000", "AMD", 100, 150.0)]
    out = apply(client, transactions=two)
    assert out["fills"] == {"covered": 1, "partly_covered": 1}
    assert any("partly covered" in p["reason"] for p in out["problems"])


def test_a_value_cleared_in_the_journal_stays_cleared(client):
    apply(client, transactions=DAY, annotations=[ANN])
    client.patch(f"/api/trades/{GROUP}/analysis?account_id=1", json={"strategy": None})
    out = apply(client, transactions=DAY, annotations=[ANN])
    st = {f["field"]: f["status"] for f in out["annotations"][0]["fields"]}
    assert st["strategy"] == "cleared_in_journal" and analysis(client)["strategy"] is None


def test_a_tag_removed_in_the_journal_is_not_added_back(client):
    tagged = {**ANN, "tags": {"mistake": ["moved stop"]}}
    apply(client, transactions=DAY, annotations=[tagged])
    (tag,) = q(client, "SELECT id FROM trade_tags")
    client.delete(f"/api/trade-tags/{tag['id']}")
    out = apply(client, transactions=DAY, annotations=[tagged])
    assert out["annotations"][0]["tags"]["removed_in_journal"] == [{"type": "mistake", "value": "moved stop"}]
    assert q(client, "SELECT COUNT(*) n FROM trade_tags")[0]["n"] == 0


def test_a_trade_deleted_in_the_journal_does_not_come_back(client):
    apply(client, transactions=DAY)
    (t,) = q(client, "SELECT id FROM trades")
    client.delete(f"/api/trades/{t['id']}")
    out = apply(client, transactions=DAY)
    assert out["fills"] == {"removed_in_journal": 2}
    assert q(client, "SELECT COUNT(*) n FROM trades")[0]["n"] == 0


def test_merge_loser_events_still_belong_to_the_trade(client):
    older = {**ANN, "id": "a0", "fill_keys": ["1001"], "updated_at": "2026-11-04T15:01:00Z", "fields": {}}
    newer = {**ANN, "id": "a1", "fill_keys": ["1002"], "updated_at": "2026-11-04T15:09:00Z"}
    ev = [{"id": "n0", "annotation_id": "a0", "ts": "2026-11-04T15:02:00Z", "kind": "note", "text": "first idea"}]
    apply(client, transactions=DAY, annotations=[older, newer], events=ev)
    apply(client, transactions=DAY, annotations=[older, newer], events=ev)
    (e,) = q(client, "SELECT trade_group FROM trade_events")
    assert e["trade_group"] == GROUP


def test_busted_fill_is_reported_once(client):
    apply(client, transactions=DAY)
    busted = [DAY[0], {**DAY[1], "status": "CANCELED"}]
    out = apply(client, transactions=busted)
    assert [c["fill_key"] for c in out["changed_fills"]] == ["1002"]
    assert sync(client, transactions=busted).json()["changed_fills"] == []


def test_apply_without_a_token_is_refused(client):
    r = client.post("/api/sync/edge-trader", json={**BASE, "dry_run": False, "transactions": DAY})
    assert r.status_code == 422 and q(client, "SELECT COUNT(*) n FROM trades")[0]["n"] == 0


def test_refused_apply_takes_no_backup(client):
    r = sync(client, False, state_token="stale", transactions=DAY)
    assert r.status_code == 409
    assert not (Path(client.db).parent / "sync-backups").exists()


def test_deleted_note_is_removed(client):
    ev = [{"id": "n1", "annotation_id": "a1", "ts": "2026-11-04T15:02:00Z", "kind": "note", "text": "x"}]
    apply(client, transactions=DAY, annotations=[ANN], events=ev)
    out = apply(client, transactions=DAY, annotations=[ANN], deleted_events=["n1"])
    assert out["events"]["deleted"] == 1 and q(client, "SELECT COUNT(*) n FROM trade_events")[0]["n"] == 0


def test_ai_feedback_skips_a_trade_regrouped_meanwhile(client, monkeypatch):
    import edge_sync
    apply(client, transactions=DAY, annotations=[ANN])
    target = ("a1", 1, GROUP)

    def regroup_then_answer(contexts):
        conn = sqlite3.connect(client.db)
        conn.execute("UPDATE trades SET executions='[]'")    # another import rebuilt the trade
        conn.commit()
        conn.close()
        return {c["key"]: {"key": c["key"], "ai_feedback": "x", "fill": {"exit_reason": "y"}} for c in contexts}
    monkeypatch.setattr(edge_sync, "ask_ai", regroup_then_answer)
    assert edge_sync.run_feedback([target]) == 0
    assert analysis(client)["ai_feedback"] is None


def test_event_times_are_stored_in_utc_and_sort_in_time_order(client):
    ev = [{"id": "e1", "annotation_id": "a1", "ts": "2026-11-04T10:01:00-05:00", "kind": "stop_set", "price": 199},
          {"id": "e2", "annotation_id": "a1", "ts": "2026-11-04T15:00:30Z", "kind": "note", "text": "earlier"}]
    apply(client, transactions=DAY, annotations=[ANN], events=ev)
    tl = client.get("/api/sync/trade-events", params={"trade_group": GROUP, "account_id": 1}).json()
    assert [(e["id"], e["ts"]) for e in tl["events"]] == [("e2", "2026-11-04T15:00:30+00:00"),
                                                         ("e1", "2026-11-04T15:01:00+00:00")]
    r = client.post("/api/sync/edge-trader", json={**BASE, "events": [{"id": "x", "ts": "yesterday-ish", "kind": "note"}]})
    assert r.status_code == 422


def test_a_day_note_deleted_in_the_journal_stays_deleted_until_edited_again(client):
    note = {"date": "2026-11-04", "account_id": 1, "text": "Plan: A setups only."}
    apply(client, day_notes=[note])
    (d,) = q(client, "SELECT id FROM diary_entries")
    client.delete(f"/api/diary/{d['id']}")
    assert apply(client, day_notes=[note])["day_notes"][0]["status"] == "removed_in_journal"
    assert q(client, "SELECT COUNT(*) n FROM diary_entries")[0]["n"] == 0
    note["text"] += " Added after the close."
    assert apply(client, day_notes=[note])["day_notes"][0]["status"] == "new"


def test_chart_window_uses_the_real_eastern_offset(monkeypatch):
    """Winter sessions are UTC-5: the bar window must not start an hour early (09:30 EDT = 08:30 EST)."""
    import asyncio
    import main
    seen = []

    async def fake_fetch(client, url, params, headers, max_bars=5000):
        seen.append(params)
        return []
    monkeypatch.setattr(main, "ALPACA_KEY", "test-key")
    monkeypatch.setattr(main, "_fetch_alpaca_bars", fake_fetch)
    asyncio.run(main.get_chart("AAPL", "2026-11-06", timeframe="5Min", days_back=1))
    asyncio.run(main.get_chart("AAPL", "2026-10-27", timeframe="5Min", days_back=1))
    assert seen[0]["start"] == "2026-11-06T09:30:00-05:00" and seen[0]["end"] == "2026-11-06T16:00:00-05:00"
    assert seen[1]["start"] == "2026-10-27T09:30:00-04:00" and seen[1]["end"] == "2026-10-27T16:00:00-04:00"


def test_a_trade_picked_for_another_account_goes_there(client):
    pre = sync(client, transactions=DAY).json()
    (t,) = pre["trades"]
    assert t["account_id"] == 1 and t["fill_keys"] == ["1001", "1002"]
    out = apply(client, transactions=DAY, account_overrides={"1001": 2, "1002": 2})
    assert out["fills"] == {"new": 2} and out["trades"][0]["account_id"] == 2
    (row,) = q(client, "SELECT account_id FROM trades")
    assert row["account_id"] == 2
    again = sync(client, transactions=DAY).json()      # without the pick: already there, not added to Main
    assert again["fills"] == {"already": 2} and again["trades"] == []


def test_a_pick_for_a_deleted_account_falls_back_to_the_route(client):
    pre = sync(client, transactions=DAY, account_overrides={"1001": 99, "1002": 99}).json()
    assert pre["trades"][0]["account_id"] == 1
    assert any("account 99" in p["reason"] for p in pre["problems"])


# ── Audit findings (independent review of the port) ────────────────────────────

def test_a_later_fill_at_a_synced_price_is_new_not_covered(client):
    """H1: a fill an earlier sync stored is one Schwab fill; it never covers a later fill at the
    same price that day, even when the earlier one is not in this request."""
    first = stock("2026-11-04T15:00:00+0000", "AMD", 100, 150.0, aid=3001)
    later = stock("2026-11-04T16:00:00+0000", "AMD", 100, 150.0, aid=3002)
    apply(client, transactions=[first])
    out = apply(client, transactions=[later])
    assert out["fills"] == {"new": 1}
    bought = sum(e["qty"] for t in q(client, "SELECT executions FROM trades")
                 for e in json.loads(t["executions"]) if e["action"] == "BOT")
    assert bought == 200


def test_a_fill_an_earlier_sync_found_covered_keeps_its_share(client):
    """H1: a merged statement fill covers each Schwab fill once, across syncs: a later fill at the
    same price that day is new, even when the covered ones are not in this request."""
    import_csv(client, ['11/4/26,10:00:00,TRD,="1",BOT +200 AMD @150.00,,,"-30,000.00","1"'])
    two = [stock("2026-11-04T15:00:00+0000", "AMD", 100, 150.0, aid=3101),
           stock("2026-11-04T15:00:01+0000", "AMD", 100, 150.0, aid=3102)]
    assert apply(client, transactions=two)["fills"] == {"covered": 2}
    later = stock("2026-11-04T18:00:00+0000", "AMD", 100, 150.0, aid=3103)
    assert apply(client, transactions=[later])["fills"] == {"new": 1}
    assert apply(client, transactions=two + [later])["fills"] == {"covered": 2, "already": 1}


def test_apply_with_a_changed_day_note_is_refused(client):
    """H2: the token binds the request, not only the summary of what it does."""
    note = {"date": "2026-11-04", "account_id": 1, "text": "Plan A"}
    token = sync(client, day_notes=[note]).json()["state_token"]
    r = sync(client, False, state_token=token, day_notes=[{**note, "text": "Plan B"}])
    assert r.status_code == 409
    assert q(client, "SELECT COUNT(*) n FROM diary_entries")[0]["n"] == 0


def test_apply_with_a_changed_event_is_refused(client):
    ev = [{"id": "e1", "annotation_id": "a1", "ts": "2026-11-04T15:00:30Z", "kind": "stop_set", "price": 199.0}]
    token = sync(client, transactions=DAY, annotations=[ANN], events=ev).json()["state_token"]
    moved = [{**ev[0], "price": 150.0}]
    r = sync(client, False, state_token=token, transactions=DAY, annotations=[ANN], events=moved)
    assert r.status_code == 409 and q(client, "SELECT COUNT(*) n FROM trades")[0]["n"] == 0


def test_a_journal_change_between_preview_and_apply_is_refused(client):
    """H2: an edit the plan reads (another sync rewrote an event) invalidates the preview."""
    ev = [{"id": "e1", "annotation_id": "a1", "ts": "2026-11-04T15:00:30Z", "kind": "stop_set", "price": 199.0}]
    apply(client, transactions=DAY, annotations=[ANN], events=ev)
    token = sync(client, transactions=DAY, annotations=[ANN], events=ev).json()["state_token"]
    conn = sqlite3.connect(client.db)
    conn.execute("UPDATE trade_events SET text='changed elsewhere'")
    conn.commit()
    conn.close()
    r = sync(client, False, state_token=token, transactions=DAY, annotations=[ANN], events=ev)
    assert r.status_code == 409


def test_a_vertical_closed_by_the_sync_is_in_the_preview(client):
    """H2: a stored open vertical the sync closes in place is listed among the changed trades."""
    opened = vertical("2026-11-04T15:13:02+0000", -1, 2.40, 1.10, 11, (31, 32))
    apply(client, transactions=opened)
    closed = opened + vertical("2026-11-04T15:18:26+0000", 1, 1.95, 0.80, 12, (33, 34))
    pre = sync(client, transactions=closed).json()
    assert pre["fills"] == {"already": 1, "new": 1}
    (t,) = pre["trades"]
    assert t["status"] == "updated" and t["net_pnl"] == 15.0 and t["fill_keys"] == ["33", "34"]


def test_repeated_transactions_in_one_request_count_once(client):
    """H3: overlapping pages may repeat a transaction; it is booked once."""
    out = apply(client, transactions=[DAY[0], DAY[0], DAY[1], DAY[1]])
    assert out["fills"] == {"new": 2}
    (t,) = q(client, "SELECT net_pnl, executions FROM trades")
    assert t["net_pnl"] == 99.5 and len(json.loads(t["executions"])) == 2


def test_conflicting_duplicate_ids_are_refused(client):
    other = {**DAY[1], "transferItems": [{**DAY[1]["transferItems"][-1], "price": 205.0}]}
    r = client.post("/api/sync/edge-trader", json={**BASE, "transactions": [DAY[0], DAY[1], other]})
    assert r.status_code == 422 and "1002" in r.text


def test_a_leg_synced_alone_is_not_booked_again_inside_a_vertical(client):
    """H4: the first sync saw only one leg of a vertical; the next one sees both."""
    legs = vertical("2026-11-04T15:13:02+0000", -1, 2.40, 1.10, 21, (41, 42))
    apply(client, transactions=[legs[0]])
    out = apply(client, transactions=legs)
    assert out["fills"] == {"already": 1, "new": 1}
    rows = q(client, "SELECT trade_group, executions FROM trades ORDER BY trade_group")
    assert sum(len(json.loads(t["executions"])) for t in rows) == 2
    assert not any("600-595" in t["trade_group"] for t in rows)
    assert any("kept as separate legs" in p["reason"] for p in out["problems"])
    assert apply(client, transactions=legs)["fills"] == {"already": 2}


def _while_locked(fn, during=None):
    """Run fn (an HTTP call) in a thread while the test holds the import lock; it must wait for it.
    `during` runs in the test's thread with the lock held (a sync committing meanwhile)."""
    import threading
    import trade_store
    out = {}

    def run():
        out["r"] = fn()
    with trade_store.IMPORT_LOCK:
        th = threading.Thread(target=run)
        th.start()
        th.join(0.5)
        assert th.is_alive(), "the request did not wait for the lock"
        if during:
            during()
    th.join(10)
    assert not th.is_alive()
    return out["r"]


def _executions(client):
    (t,) = q(client, "SELECT executions FROM trades")
    return [(e["time"], e["action"], e["qty"], e["price"]) for e in json.loads(t["executions"])]


@pytest.mark.parametrize("change", ["add", "update", "delete"])
def test_execution_edits_wait_for_a_sync_in_progress(client, change):
    """H5: an execution edit reads, changes and rewrites the whole fill list; it holds the same lock
    as a sync, then succeeds on the list as it is once the lock is free."""
    apply(client, transactions=DAY)
    (t,) = q(client, "SELECT id FROM trades")
    fill = {"action": "BOT", "qty": 1, "price": 200.5, "date": "2026-11-04", "time": "10:30:00"}
    call = {"add": lambda: client.post(f"/api/trades/{t['id']}/executions", json=fill),
            "update": lambda: client.put(f"/api/trades/{t['id']}/executions/0", json={**fill, "time": "10:00:00", "qty": 100}),
            "delete": lambda: client.delete(f"/api/trades/{t['id']}/executions/1")}[change]
    r = _while_locked(call)
    assert r.status_code == 200, r.text
    expected = {"add": [("10:00:00", "BOT", 100, 200.0), ("10:05:00", "SOLD", 100, 201.0), ("10:30:00", "BOT", 1, 200.5)],
                "update": [("10:00:00", "BOT", 100, 200.5), ("10:05:00", "SOLD", 100, 201.0)],
                "delete": [("10:00:00", "BOT", 100, 200.0)]}[change]
    assert sorted(_executions(client)) == expected


def test_an_execution_edit_keeps_a_close_a_sync_committed_while_it_waited(client):
    """H5: the edit waits while a sync adds the trade's closing fill, then writes on top of it."""
    import edge_sync
    import database
    apply(client, transactions=DAY[:1])
    (t,) = q(client, "SELECT id FROM trades")

    def sync_the_close():
        conn = database.get_db()
        try:
            conn.execute("BEGIN IMMEDIATE")
            edge_sync.run_sync(conn, edge_sync.SyncRequest(**{**BASE, "transactions": DAY}))
            conn.commit()
        finally:
            conn.close()
    r = _while_locked(lambda: client.post(f"/api/trades/{t['id']}/executions", json={
        "action": "BOT", "qty": 1, "price": 200.5, "date": "2026-11-04", "time": "10:30:00"}), during=sync_the_close)
    assert r.status_code == 200, r.text
    assert sorted(_executions(client)) == [("10:00:00", "BOT", 100, 200.0), ("10:05:00", "SOLD", 100, 201.0),
                                           ("10:30:00", "BOT", 1, 200.5)]


OLD_SYNC_FILLS = """CREATE TABLE sync_fills (
    fill_key TEXT PRIMARY KEY, account_id INTEGER, match_key TEXT NOT NULL, agg_key TEXT NOT NULL,
    status TEXT NOT NULL, payload_hash TEXT NOT NULL,
    first_synced TEXT NOT NULL DEFAULT (datetime('now')), last_synced TEXT NOT NULL DEFAULT (datetime('now')))"""


def test_an_upgraded_database_never_lets_an_old_covered_pool_cover_a_new_fill(client):
    """Pass 2, high 1: rows covered before qty and lead_key existed have an unknown share of the
    merged statement fill. After the upgrade a later fill at that price is added as new and named."""
    import edge_sync
    import_csv(client, ['11/4/26,10:00:00,TRD,="1",BOT +200 AMD @150.00,,,"-30,000.00","1"'])
    two = [stock("2026-11-04T15:00:00+0000", "AMD", 100, 150.0, aid=7001),
           stock("2026-11-04T15:00:01+0000", "AMD", 100, 150.0, aid=7002)]
    assert apply(client, transactions=two)["fills"] == {"covered": 2}
    apply(client, transactions=DAY)          # one new and stored fill per row too
    # Rewrite the bookkeeping the way a journal before this version kept it, then upgrade it.
    conn = sqlite3.connect(client.db)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM sync_fills")]
    conn.execute("DROP TABLE sync_fills")
    conn.execute(OLD_SYNC_FILLS)
    for r in rows:
        conn.execute("INSERT INTO sync_fills (fill_key, account_id, match_key, agg_key, status, payload_hash) "
                     "VALUES (?,?,?,?,?,?)", (r["fill_key"], r["account_id"], r["match_key"], r["agg_key"],
                                              r["status"], r["payload_hash"]))
    conn.commit()
    edge_sync.init_sync_tables(conn)
    upgraded = {r["fill_key"]: dict(r) for r in conn.execute("SELECT * FROM sync_fills")}
    conn.close()
    assert upgraded["1001"]["qty"] == 100 and upgraded["1001"]["lead_key"] == "1001"
    assert upgraded["7001"]["qty"] is None and upgraded["7001"]["lead_key"] is None

    later = stock("2026-11-04T18:00:00+0000", "AMD", 100, 150.0, aid=7003)
    out = apply(client, transactions=[later])
    assert out["fills"] == {"new": 1}
    assert any("added as new" in p["reason"] for p in out["problems"])
    assert apply(client, transactions=two + [later])["fills"] == {"covered": 2, "already": 1}
    bought = sum(e["qty"] for t in q(client, "SELECT executions FROM trades WHERE ticker='AMD'")
                 for e in json.loads(t["executions"]) if e["action"] == "BOT")
    assert bought == 300


def test_a_moved_trade_never_covers_a_later_fill_in_its_new_account(client):
    """Pass 2, high 2: sync, move the trade in Trade View, then sync only a later fill at the same
    price: the moved fill is still a synced fill, so the later one is new."""
    first = stock("2026-11-04T15:00:00+0000", "AMD", 100, 150.0, aid=8001)
    apply(client, transactions=[first])
    (t,) = q(client, "SELECT id FROM trades")
    assert client.patch(f"/api/trades/{t['id']}/account", json={"account_id": 2}).status_code == 200
    assert {r["account_id"] for r in q(client, "SELECT account_id FROM sync_fills")} == {2}
    later = stock("2026-11-04T17:00:00+0000", "AMD", 100, 150.0, aid=8002)
    assert apply(client, transactions=[later])["fills"] == {"new": 1}
    bought = sum(e["qty"] for t in q(client, "SELECT executions FROM trades")
                 for e in json.loads(t["executions"]) if e["action"] == "BOT")
    assert bought == 200


def test_a_vertical_held_as_legs_closes_leg_by_leg(client):
    """Pass 2, high 3: a vertical kept as single legs (one leg was synced alone) is closed by a later
    closing vertical leg by leg: both positions go flat with their own realized P&L."""
    opening = vertical("2026-11-04T15:13:02+0000", -1, 2.40, 1.10, 61, (51, 52))
    closing = vertical("2026-11-04T15:40:00+0000", 1, 1.95, 0.80, 62, (53, 54))
    apply(client, transactions=[opening[0]])
    apply(client, transactions=opening)
    out = apply(client, transactions=opening + closing)
    assert out["fills"] == {"already": 2, "new": 2}
    assert any("single-leg positions" in p["reason"] for p in out["problems"])
    rows = q(client, "SELECT trade_group, option_strike, side, net_pnl, executions FROM trades ORDER BY option_strike")
    assert [(r["option_strike"], r["side"], r["net_pnl"]) for r in rows] == [(595.0, "LONG", -30.0), (600.0, "SHORT", 45.0)]
    for r in rows:
        execs = json.loads(r["executions"])
        assert len(execs) == 2 and sum(e["qty"] * (1 if e["action"] == "BOT" else -1) for e in execs) == 0
    assert not any("600-595" in r["trade_group"] for r in rows)
    assert apply(client, transactions=opening + closing)["fills"] == {"already": 4}


def test_browser_requests_to_the_sync_are_refused(client):
    """H6: Edge Trader posts server to server with no Origin; a page in a browser always sends one."""
    for headers in ({"Origin": "http://localhost:5173"}, {"Sec-Fetch-Site": "same-site"},
                    {"Origin": "null"}):
        r = client.post("/api/sync/edge-trader", json={**BASE, "transactions": DAY}, headers=headers)
        assert r.status_code == 403, headers
    r = client.post("/api/sync/edge-trader", json={**BASE, "transactions": DAY}, headers={"Sec-Fetch-Site": "none"})
    assert r.status_code == 200
    assert q(client, "SELECT COUNT(*) n FROM trades")[0]["n"] == 0
    # The journal's own pages still read the timeline and the version check.
    page = {"Origin": "http://localhost:3010"}
    assert client.get("/api/sync/info", headers=page).status_code == 200
    assert client.get("/api/sync/trade-events", params={"trade_group": GROUP, "account_id": 1},
                      headers=page).status_code == 200


def test_covered_fills_belong_to_the_account_holding_the_statement_fill(client):
    """M7: a merged statement fill in account 2 covers fills routed to account 1; the annotation
    finds the trade in account 2."""
    import_csv(client, ['11/4/26,10:00:00,TRD,="1",BOT +200 AMD @150.00,,,"-30,000.00","1"'], account="2")
    two = [stock("2026-11-04T15:00:00+0000", "AMD", 100, 150.0, aid=5001),
           stock("2026-11-04T15:00:01+0000", "AMD", 100, 150.0, aid=5002)]
    out = apply(client, transactions=two, annotations=[{"id": "t5001", "fill_keys": ["5001", "5002"],
                                                        "fields": {"strategy": "Opening Range"}}])
    assert out["fills"] == {"covered": 2}
    (ann,) = out["annotations"]
    assert ann["status"] == "ok" and ann["trades"][0]["account_id"] == 2
    assert {r["account_id"] for r in q(client, "SELECT account_id FROM sync_fills")} == {2}


@pytest.mark.parametrize("body", [
    {"events": [{"id": "x", "annotation_id": "a1", "ts": "2026-11-04T15:00:00Z", "kind": "stop_set", "price": "Infinity"}]},
    {"events": [{"id": "x", "annotation_id": "a1", "ts": "2026-11-04T15:00:00Z", "kind": "stop_set", "qty": "NaN"}]},
    {"annotations": [{"id": "a1", "fill_keys": ["1001"], "fields": {"stop_loss": "Infinity"}}]},
    {"annotations": [{"id": "a1", "fill_keys": ["1001"], "fields": {"r_multiple": float("nan")}}]},
    {"transactions": [{**DAY[0], "transferItems": [{**DAY[0]["transferItems"][0], "price": "NaN"}]}]},
    {"transactions": [{**DAY[0], "transferItems": [{**DAY[0]["transferItems"][0], "amount": float("inf")}]}]},
])
def test_non_finite_numbers_are_refused(client, body):
    """M8: Infinity and NaN are refused before anything runs (they would break later answers)."""
    payload = json.dumps({**BASE, "transactions": DAY, "annotations": [ANN], **body})   # NaN as Python writes it
    r = client.post("/api/sync/edge-trader", content=payload, headers={"Content-Type": "application/json"})
    assert r.status_code == 422, r.text


def test_a_single_leg_in_another_account_never_splits_a_closing_vertical(client):
    """Pass 3, high 1: the vertical is open in the account its close goes to; an unrelated single
    595 put in another account (looked at for duplicates) does not split the close."""
    opening = vertical("2026-11-04T15:13:02+0000", -1, 2.40, 1.10, 71, (61, 62))
    other = option("2026-11-04T15:15:00+0000", "SPY   261104P00595000", 1, 1.00, aid=63)
    apply(client, transactions=opening + [other], account_overrides={"63": 2})
    closing = vertical("2026-11-04T15:40:00+0000", 1, 1.95, 0.80, 72, (64, 65))
    out = apply(client, transactions=opening + [other] + closing, account_overrides={"63": 2})
    assert out["fills"] == {"already": 2, "new": 1}
    assert not any("separate legs" in p["reason"] for p in out["problems"])
    rows = q(client, "SELECT account_id, trade_group, net_pnl, executions FROM trades ORDER BY account_id")
    assert [(r["account_id"], "600-595" in r["trade_group"]) for r in rows] == [(1, True), (2, False)]
    assert rows[0]["net_pnl"] == 15.0 and len(json.loads(rows[0]["executions"])) == 2


@pytest.mark.parametrize("moved", ["SHORT", "LONG"])
def test_moving_one_side_of_a_reversal_never_lets_it_cover_a_later_fill(client, moved):
    """Pass 3, high 2: a fill that flipped the position is split between two trades. Moving either
    one keeps the whole fill counted as synced, so a later sale at that price is new."""
    flip = [stock("2026-11-04T15:00:00+0000", "AMD", 100, 150.0, aid=9001),
            stock("2026-11-04T15:10:00+0000", "AMD", -200, 150.0, aid=9002)]
    apply(client, transactions=flip)
    (t,) = q(client, "SELECT id FROM trades WHERE side=?", moved)
    assert client.patch(f"/api/trades/{t['id']}/account", json={"account_id": 2}).status_code == 200
    later = stock("2026-11-04T17:00:00+0000", "AMD", -100, 150.0, aid=9003)
    assert apply(client, transactions=[later])["fills"] == {"new": 1}
    sold = sum(e["qty"] for t in q(client, "SELECT executions FROM trades")
               for e in json.loads(t["executions"]) if e["action"] == "SOLD")
    assert sold == 300
