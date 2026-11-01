"""Load missing prices: one background job, planned from replay coverage, mocked HTTP only."""
from dataclasses import replace
import threading
import httpx
import pytest
from test_exitlab_engine import fixture
from test_exitlab_storage import db, store_trade
from test_exitlab_m5 import client_for
from exitlab import autoload, bars as cache
from exitlab.bars import put_bars, cached
from exitlab.calendar import session, previous_session
from exitlab.models import MINUTE, timestamp
from exitlab.service import readiness

PATH = [(100, 101.01, 99.5, 101)]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    # No credentials, no throttle sleep and no real client: every request is answered below.
    monkeypatch.setattr(cache, "credentials", lambda: {})
    monkeypatch.setattr(cache, "throttle", lambda: None)
    yield
    autoload.wait(10)


def serve(monkeypatch, handler):
    calls = []

    def recorder(request):
        calls.append(dict(request.url.params))
        return handler(request)
    monkeypatch.setattr(autoload, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(recorder)))
    return calls


def minute_bars(request):
    p = request.url.params
    start, end = timestamp(p["start"]), timestamp(p["end"])
    rows = [{"t": (start + i * MINUTE).isoformat(), "o": 100, "h": 101.5, "l": 99.5, "c": 101, "v": 100, "vw": 100.5}
            for i in range(int((end - start) / MINUTE))]
    return httpx.Response(200, json={"bars": {p["symbols"]: rows}, "next_page_token": None})


def add(db, day="2026-10-16", trade_id=1, stop=True):
    t, _ = fixture(PATH, day=day)
    t = replace(t, id=trade_id, stop=t.stop if stop else None)
    store_trade(db, t)
    return t


def finish(client):
    autoload.wait(10)
    return client.get("/api/exitlab/bars/load-missing/status").json()


def test_plan_lists_exactly_the_missing_stock_and_spy_windows(db):
    add(db)
    windows = [previous_session("2026-10-16"), session("2026-10-16")]
    items = autoload.plan(db, 1)
    assert [i["trade_id"] for i in items] == [1]
    assert items[0]["ticker"] == "FIXTURE" and items[0]["date"] == "2026-10-16"
    assert items[0]["windows"] == [(s, a, b) for s in ("FIXTURE", "SPY") for a, b in windows]
    # A complete stock session and a fetched (even sparse) SPY warm-up window are not missing.
    _, stock = fixture(PATH, day="2026-10-16")
    put_bars(db, "FIXTURE", stock)
    a, b = windows[0]
    db.execute("INSERT INTO market_bar_windows VALUES('SPY','sip',?,?)", (a.isoformat(), b.isoformat()))
    db.commit()
    assert autoload.plan(db, 1)[0]["windows"] == [("FIXTURE", *windows[0]), ("SPY", *windows[1])]


def test_plan_skips_no_stop_structural_exclusions_and_unfinished_sessions(db):
    add(db, trade_id=1, stop=False)
    add(db, day="2026-10-15", trade_id=2)
    db.execute("UPDATE trades SET instrument_type='OPTION' WHERE id=2")
    add(db, day="2026-10-14", trade_id=3)
    db.commit()
    assert [i["trade_id"] for i in autoload.plan(db, 1)] == [3]
    # The trade waits until its session closed 15 minutes ago; Alpaca refuses newer windows.
    _, closing = session("2026-10-14")
    assert autoload.plan(db, 1, now=closing + 14 * MINUTE) == []
    assert len(autoload.plan(db, 1, now=closing + 15 * MINUTE)[0]["windows"]) == 4


def test_job_loads_every_missing_window_once_then_the_trade_is_eligible(db, monkeypatch):
    add(db, day="2026-10-16", trade_id=1)
    add(db, day="2026-10-16", trade_id=2)  # Same day: SPY and FIXTURE are cached by the first trade.
    calls = serve(monkeypatch, minute_bars)
    assert readiness(db, 1, "sip")["missing_prices"] == 2
    with client_for(db) as client:
        started = client.post("/api/exitlab/bars/load-missing", json={"account_id": 1})
        assert started.status_code == 202
        assert started.json()["running"] and started.json()["total"] == 2
        status = finish(client)
        history = client.post("/api/exitlab/history", json={"account_id": 1}).json()
    assert status["running"] is False and status["done"] == 2 and status["failed"] == 0
    assert status["failures"] == [] and status["finished_at"] and status["account_id"] == 1
    assert sorted(c["symbols"] for c in calls) == ["FIXTURE", "FIXTURE", "SPY", "SPY"]
    assert all(c["feed"] == "sip" and c["timeframe"] == "1Min" for c in calls)
    assert len(cached(db, "SPY", *session("2026-10-16"), "sip")) == 390
    ready = readiness(db, 1, "sip")
    assert ready["missing_prices"] == 0 and ready["eligible"] == 2
    assert history["missing_prices"] == 0


def test_second_concurrent_job_is_refused(db, monkeypatch):
    add(db)
    gate, entered = threading.Event(), threading.Event()

    def slow(request):
        entered.set()
        gate.wait(10)
        return minute_bars(request)
    serve(monkeypatch, slow)
    with client_for(db) as client:
        assert client.post("/api/exitlab/bars/load-missing", json={"account_id": 1}).status_code == 202
        assert entered.wait(10)
        second = client.post("/api/exitlab/bars/load-missing", json={"account_id": 1})
        running = client.get("/api/exitlab/bars/load-missing/status").json()
        gate.set()
        status = finish(client)
    assert second.status_code == 409
    assert running["running"] and running["done"] == 0 and running["total"] == 1
    assert status["done"] == 1 and not status["running"]


def test_failures_are_listed_and_do_not_stop_the_job(db, monkeypatch):
    add(db, day="2026-10-15", trade_id=1)
    t = add(db, day="2026-10-16", trade_id=2)
    db.execute("UPDATE trades SET ticker='BAD' WHERE id=1")
    db.commit()
    serve(monkeypatch, lambda r: httpx.Response(401) if r.url.params["symbols"] == "BAD" else minute_bars(r))
    with client_for(db) as client:
        client.post("/api/exitlab/bars/load-missing", json={"account_id": 1})
        status = finish(client)
    assert status["done"] == 2 and status["failed"] == 1
    assert status["failures"] == [{"ticker": "BAD", "date": "2026-10-15",
                                   "reason": "Alpaca historical bars failed (HTTP 401)"}]
    assert readiness(db, 1, "sip")["eligible"] == 1 and t.id == 2


def test_iex_fallback_counts_as_a_failure(db, monkeypatch):
    add(db)
    serve(monkeypatch, lambda r: httpx.Response(403) if r.url.params["feed"] == "sip" else minute_bars(r))
    with client_for(db) as client:
        client.post("/api/exitlab/bars/load-missing", json={"account_id": 1})
        status = finish(client)
    assert status["failed"] == 1 and "SIP prices are unavailable" in status["failures"][0]["reason"]


def test_nothing_missing_finishes_immediately_without_requests(db, monkeypatch):
    calls = serve(monkeypatch, minute_bars)
    with client_for(db) as client:
        response = client.post("/api/exitlab/bars/load-missing", json={"account_id": 1})
    assert response.status_code == 202
    assert response.json()["total"] == 0 and response.json()["running"] is False and not calls


def test_missing_credentials_refuse_before_starting(db, monkeypatch):
    add(db)

    def missing():
        raise ValueError("Alpaca credentials missing; set ALPACA_ENV_FILE or APCA_API_KEY_ID/APCA_API_SECRET_KEY")
    monkeypatch.setattr(cache, "credentials", missing)
    calls = serve(monkeypatch, minute_bars)
    with client_for(db) as client:
        response = client.post("/api/exitlab/bars/load-missing", json={"account_id": 1})
        status = client.get("/api/exitlab/bars/load-missing/status").json()
    assert response.status_code == 400 and "APCA_API_KEY_ID" in response.json()["detail"]
    assert status["running"] is False and not calls


def test_status_shape_before_and_request_validation(db):
    with client_for(db) as client:
        assert client.post("/api/exitlab/bars/load-missing", json={"account_id": 0}).status_code == 422
        assert client.post("/api/exitlab/bars/load-missing", json={"account_id": 1, "x": 1}).status_code == 422
        status = client.get("/api/exitlab/bars/load-missing/status").json()
    assert set(status) >= {"running", "total", "done", "failed", "failures", "started_at", "finished_at"}


def test_fetch_clips_a_window_into_the_last_15_minutes_instead_of_refusing(db):
    from datetime import datetime, timedelta
    from exitlab.models import UTC
    calls = []

    def recorder(request):
        calls.append(dict(request.url.params))
        return minute_bars(request)
    client = httpx.Client(transport=httpx.MockTransport(recorder))
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    result = cache.fetch(db, "FIXTURE", now - timedelta(hours=1), now + timedelta(hours=1), client=client)
    assert result["feed"] == "sip" and not result["cached"]
    assert timestamp(calls[0]["end"]) <= now - timedelta(minutes=14)  # a minute may tick over
    with pytest.raises(ValueError, match="not available yet"):
        cache.fetch(db, "FIXTURE", now - timedelta(minutes=5), now + timedelta(hours=1), client=client)
