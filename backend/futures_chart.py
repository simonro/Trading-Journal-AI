"""Futures chart bars from Schwab's price history (free with a Schwab API login).

Alpaca has no futures, so /ES, /MES, /NQ, /MNQ (and any other contract Schwab lists) chart from
Schwab instead, at the contract's real prices. The exact contract ('/ESZ26') is asked first; an
expired one that Schwab no longer serves falls back to the continuous root ('/ES').

Optional. Needs the schwabdev package (pip install schwabdev) and a Schwab developer app.
Keys: SCHWAB_APP_KEY / SCHWAB_APP_SECRET, from backend/.env or the .env file SCHWAB_ENV_FILE points
to (for example Edge Trader for Schwab's, to share one Schwab login). Tokens: ~/.schwabdev/tokens.db
(SCHWAB_TOKENS_DB), shared with the other schwabdev apps; schwabdev coordinates refreshes between
processes. This module never signs in: when the login has expired it says so, and Edge Trader for
Schwab's login.bat (or any schwabdev sign-in) renews it.
Read-only: it only ever calls price_history. Never opens a streamer.

Bars come back in Alpaca's shape ({t, o, h, l, c, v, vw}, t in UTC ISO) so the chart treats both
the same. Schwab has no per-bar VWAP, so vw is the bar's typical price (h+l+c)/3.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import dotenv_values

ET = ZoneInfo("America/New_York")
UTC = timezone.utc
REFRESH_TOKEN_DAYS = 7
SAFETY_HOURS = 2
LOGIN_EXPIRED = ("Schwab login missing or expired: sign in with Edge Trader for Schwab's login.bat "
                 "(or any schwabdev login), then reload the chart.")

_CONTRACT = re.compile(r"^(/[A-Z0-9]+?)([FGHJKMNQUVXZ])(\d{1,2})$")

# timeframe -> (Schwab frequencyType, frequency, how many Schwab bars make one chart bar)
_FREQ = {
    "1Min": ("minute", 1, 1), "3Min": ("minute", 1, 3), "5Min": ("minute", 5, 1),
    "10Min": ("minute", 10, 1), "15Min": ("minute", 15, 1), "30Min": ("minute", 30, 1),
    "1Hour": ("minute", 30, 2), "1Day": ("daily", 1, 1), "1Week": ("weekly", 1, 1),
}


class ChartUnavailable(RuntimeError):
    pass


def is_future(ticker: str) -> bool:
    return ticker.startswith("/")


def root_of(ticker: str) -> str:
    """'/ESZ26' -> '/ES'; a root stays as it is."""
    m = _CONTRACT.match(ticker.upper())
    return m.group(1) if m else ticker.upper()


def _setting(name: str) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    shared = os.environ.get("SCHWAB_ENV_FILE")
    if shared and Path(os.path.expanduser(shared)).exists():
        return dotenv_values(os.path.expanduser(shared)).get(name) or None
    return None


def _tokens_db() -> Path:
    return Path(os.path.expanduser(os.environ.get("SCHWAB_TOKENS_DB", "~/.schwabdev/tokens.db")))


def _hours_left() -> float | None:
    db = _tokens_db()
    if not db.exists():
        return None
    try:
        with sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True) as con:
            row = con.execute("select refresh_token_issued from schwabdev").fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    issued = datetime.fromisoformat(row[0])
    if issued.tzinfo is None:
        issued = issued.replace(tzinfo=UTC)
    return (REFRESH_TOKEN_DAYS - (datetime.now(UTC) - issued).total_seconds() / 86400) * 24


_client = None
_client_lock = threading.Lock()


def _get_client():
    global _client
    key, secret = _setting("SCHWAB_APP_KEY"), _setting("SCHWAB_APP_SECRET")
    if not key or not secret:
        raise ChartUnavailable("Futures charts use Schwab price history: set SCHWAB_APP_KEY and SCHWAB_APP_SECRET "
                               "(or SCHWAB_ENV_FILE) in backend/.env and pip install schwabdev. See the README.")
    try:
        import schwabdev
    except ImportError as exc:
        raise ChartUnavailable("Futures charts need the schwabdev package: pip install schwabdev in the "
                               "backend's Python, then restart the backend.") from exc
    left = _hours_left()
    if left is None or left < SAFETY_HOURS:
        raise ChartUnavailable(LOGIN_EXPIRED)
    with _client_lock:
        if _client is None:

            def _no_prompt(*_a, **_k):
                raise ChartUnavailable(LOGIN_EXPIRED)

            inner = schwabdev.Client(app_key=key, app_secret=secret,
                                     callback_url=_setting("SCHWAB_CALLBACK_URL") or "https://127.0.0.1",
                                     tokens_db=str(_tokens_db()), call_on_auth=_no_prompt,
                                     open_browser_for_auth=False)
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry
            inner._session.mount("https://", HTTPAdapter(max_retries=Retry(total=0, raise_on_status=False)))
            _client = inner
        return _client


def _price_history(symbol: str, freq_type: str, freq: int, start: datetime, end: datetime) -> list[dict]:
    period_type = "day" if freq_type == "minute" else "year"
    resp = _get_client().price_history(symbol, periodType=period_type, frequencyType=freq_type, frequency=freq,
                                       startDate=start, endDate=end, needExtendedHoursData=True)
    if resp.status_code == 401:
        raise ChartUnavailable(LOGIN_EXPIRED)
    if not resp.ok:
        raise ChartUnavailable(f"Schwab price history error {resp.status_code}")
    return resp.json().get("candles") or []


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day_iso(ms: int) -> str:
    """A daily/weekly candle stamped at midnight ET of its own date, like Alpaca's."""
    d = datetime.fromtimestamp(ms / 1000, ET).date()
    return datetime(d.year, d.month, d.day, tzinfo=ET).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _bar(c: dict, t: str) -> dict:
    return {"t": t, "o": c["open"], "h": c["high"], "l": c["low"], "c": c["close"], "v": c.get("volume", 0),
            "vw": round((c["high"] + c["low"] + c["close"]) / 3, 4)}


def _combine(candles: list[dict], n: int, minutes: int) -> list[dict]:
    """Schwab bars -> n-times-longer bars, each starting on a whole multiple of its length (ET)."""
    if n == 1:
        return candles
    out, cur, cur_key = [], None, None
    span = minutes * n
    for c in candles:
        et = datetime.fromtimestamp(c["datetime"] / 1000, ET)
        key = (et.date(), (et.hour * 60 + et.minute) // span)
        if key != cur_key:
            if cur:
                out.append(cur)
            start = datetime.combine(et.date(), datetime.min.time(), ET) + timedelta(minutes=key[1] * span)
            cur = dict(c, datetime=int(start.timestamp() * 1000))
            cur_key = key
        else:
            cur["high"] = max(cur["high"], c["high"])
            cur["low"] = min(cur["low"], c["low"])
            cur["close"] = c["close"]
            cur["volume"] = cur.get("volume", 0) + c.get("volume", 0)
    if cur:
        out.append(cur)
    return out


_cache: dict[tuple, list[dict]] = {}
_CACHE_MAX = 200


def bars(ticker: str, timeframe: str, start: datetime, end: datetime) -> tuple[list[dict], str]:
    """Chart bars for a futures contract between two aware datetimes. Returns (bars, symbol used)."""
    freq_type, freq, n = _FREQ.get(timeframe, _FREQ["5Min"])
    key = (ticker.upper(), timeframe, start.isoformat(), end.isoformat())
    if key in _cache:
        return _cache[key]
    symbols = [ticker.upper()]
    if root_of(ticker) != ticker.upper():
        symbols.append(root_of(ticker))
    candles, used = [], symbols[0]
    for sym in symbols:
        candles = _price_history(sym, freq_type, freq, start, end)
        if not candles and freq_type == "minute" and freq == 1:
            # 1-minute history is shorter than 5-minute: an older day still charts at 5m.
            candles = _price_history(sym, freq_type, 5, start, end)
            freq, n = 5, 1
        if candles:
            used = sym
            break
    lo, hi = start.timestamp() * 1000, end.timestamp() * 1000
    if freq_type == "minute":
        candles = [c for c in candles if lo <= c["datetime"] < hi]
        candles = _combine(sorted(candles, key=lambda c: c["datetime"]), n, freq)
        out = [_bar(c, _iso(c["datetime"])) for c in candles]
    else:
        out = [_bar(c, _day_iso(c["datetime"])) for c in sorted(candles, key=lambda c: c["datetime"])]
    result = (out, used)
    # Past windows never change; today's keeps filling in, so it is fetched again each time.
    if end.astimezone(ET).date() < datetime.now(ET).date():
        if len(_cache) >= _CACHE_MAX:
            _cache.pop(next(iter(_cache)))
        _cache[key] = result
    return result


def window(trade_day: date, days_back: int, timeframe: str) -> tuple[datetime, datetime]:
    """The time span a futures chart loads. Intraday: the full Globex sessions (18:00 ET the evening
    before through 17:00 ET), so pre-market and evening fills are on the chart too."""
    if timeframe in ("1Day", "1Week"):
        start_d = trade_day - timedelta(days=days_back - 1)
        end_d = min(trade_day + timedelta(days=10), datetime.now(ET).date())
        return (datetime(start_d.year, start_d.month, start_d.day, tzinfo=ET),
                datetime(end_d.year, end_d.month, end_d.day, 23, 59, tzinfo=ET))
    start_d = trade_day - timedelta(days=days_back)
    return (datetime(start_d.year, start_d.month, start_d.day, 18, 0, tzinfo=ET),
            datetime(trade_day.year, trade_day.month, trade_day.day, 17, 0, tzinfo=ET))
