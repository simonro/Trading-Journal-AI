from datetime import date, datetime

from fastapi.testclient import TestClient

import futures_chart as fc
import main

ET = fc.ET


def ms(y, mo, d, h, mi):
    return int(datetime(y, mo, d, h, mi, tzinfo=ET).timestamp() * 1000)


def candle(t, o, h, l, c, v=10):
    return {"datetime": t, "open": o, "high": h, "low": l, "close": c, "volume": v}


def test_contract_root():
    assert fc.root_of("/ESZ26") == "/ES"
    assert fc.root_of("/MNQH27") == "/MNQ"
    assert fc.root_of("/ES") == "/ES"
    assert fc.is_future("/MES") and not fc.is_future("SPY")


def test_intraday_window_is_the_globex_session():
    start, end = fc.window(date(2026, 11, 17), 1, "5Min")
    assert (start.date(), start.hour, end.date(), end.hour) == (date(2026, 11, 16), 18, date(2026, 11, 17), 17)


def test_three_minute_bars_from_one_minute():
    cs = [candle(ms(2026, 11, 17, 10, m), 100 + m, 101 + m, 99 + m, 100.5 + m) for m in range(0, 6)]
    out = fc._combine(cs, 3, 1)
    assert len(out) == 2
    first = out[0]
    assert (first["open"], first["high"], first["low"], first["close"], first["volume"]) == (100, 103, 99, 102.5, 30)


def test_route_takes_a_contract_with_a_slash(monkeypatch):
    seen = {}

    def fake(symbol, freq_type, freq, start, end):
        seen.setdefault("symbols", []).append(symbol)
        return [candle(ms(2026, 11, 17, 15, 0), 7320, 7325, 7318, 7323, 5)]

    monkeypatch.setattr(fc, "_price_history", fake)
    fc._cache.clear()
    r = TestClient(main.app).get("/api/chart/%2FESZ26/2026-11-17", params={"timeframe": "5Min"})
    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == "/ESZ26" and body["source"] == "schwab"
    assert body["bars"] == [{"t": "2026-11-17T20:00:00Z", "o": 7320, "h": 7325, "l": 7318, "c": 7323, "v": 5,
                             "vw": 7322.0}]
    assert seen["symbols"] == ["/ESZ26"]


def test_expired_contract_falls_back_to_the_root(monkeypatch):
    def fake(symbol, freq_type, freq, start, end):
        return [] if symbol == "/ESU26" else [candle(ms(2026, 9, 10, 10, 0), 1, 2, 0.5, 1.5)]

    monkeypatch.setattr(fc, "_price_history", fake)
    fc._cache.clear()
    body = TestClient(main.app).get("/api/chart/%2FESU26/2026-09-10", params={"timeframe": "5Min"}).json()
    assert body["ticker"] == "/ES" and len(body["bars"]) == 1 and "continuous" in body["warning"]


def test_login_problem_is_a_warning_not_an_error(monkeypatch):
    def fake(*_a, **_k):
        raise fc.ChartUnavailable("Schwab login expired")

    monkeypatch.setattr(fc, "_price_history", fake)
    fc._cache.clear()
    body = TestClient(main.app).get("/api/chart/%2FNQZ26/2026-11-17").json()
    assert body["bars"] == [] and body["warning"] == "Schwab login expired"


def test_without_schwab_keys_the_chart_says_how_to_enable_it(monkeypatch):
    for name in ("SCHWAB_APP_KEY", "SCHWAB_APP_SECRET", "SCHWAB_ENV_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(fc, "_client", None)
    fc._cache.clear()
    body = TestClient(main.app).get("/api/chart/%2FMESZ26/2026-11-17").json()
    assert body["bars"] == []
    assert "SCHWAB_APP_KEY" in body["warning"] and "schwabdev" in body["warning"]


def test_missing_tokens_file_is_not_created(monkeypatch, tmp_path):
    db = tmp_path / "tokens.db"
    monkeypatch.setenv("SCHWAB_TOKENS_DB", str(db))
    assert fc._hours_left() is None and not db.exists()
