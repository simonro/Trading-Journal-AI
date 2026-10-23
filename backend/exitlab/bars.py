"""REST-only cache. Feed is part of every key; incomplete data stays incomplete."""
from dataclasses import asdict
from datetime import datetime, timedelta
import os
import threading
import time
import httpx
from dotenv import dotenv_values
from .models import Bar, UTC, iso, timestamp, digest, continuous

_lock = threading.Lock()
_last_request = 0.0


def throttle():
    global _last_request
    with _lock:
        delay = max(0, 1.01 - (time.monotonic() - _last_request))
        time.sleep(delay)
        _last_request = time.monotonic()


def credentials():
    path = os.getenv("ALPACA_ENV_FILE")
    values = dotenv_values(path) if path else {}
    # An explicit env file is authoritative, never combine halves from two accounts.
    if path:
        key = values.get("ALPACA_TRADING_KEY") or values.get("APCA_API_KEY_ID")
        secret = values.get("ALPACA_TRADING_SECRET") or values.get("APCA_API_SECRET_KEY")
    else:
        key, secret = os.getenv("APCA_API_KEY_ID"), os.getenv("APCA_API_SECRET_KEY")
    if not key or not secret:
        raise ValueError("Alpaca credentials missing; set ALPACA_ENV_FILE or APCA_API_KEY_ID/APCA_API_SECRET_KEY")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def put_bars(conn, symbol, bars):
    now = iso(datetime.now(UTC))
    for b in bars:
        raw = asdict(b)
        conn.execute("""INSERT INTO market_bars
            (symbol,ts,feed,open,high,low,close,volume,vwap,fetched_at,revision_hash)
            VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(symbol,ts,feed) DO UPDATE SET
            open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,
            volume=excluded.volume,vwap=excluded.vwap,fetched_at=excluded.fetched_at,
            revision_hash=excluded.revision_hash""",
            (symbol.upper(), iso(b.ts), b.feed, b.open, b.high, b.low, b.close,
             b.volume, b.vwap, now, digest(raw)))


def cached(conn, symbol, start, end, feed):
    rows = conn.execute("SELECT * FROM market_bars WHERE symbol=? AND ts>=? AND ts<? AND feed=? ORDER BY ts",
                        (symbol.upper(), iso(start), iso(end), feed))
    return [Bar(timestamp(r["ts"]), r["open"], r["high"], r["low"], r["close"], r["volume"], r["feed"], r["vwap"]) for r in rows]


def fetch(conn, symbol, start, end, feed="sip", refresh=False, client=None):
    if feed not in ("sip", "iex") or end <= start:
        raise ValueError("invalid_bar_request")
    if end > datetime.now(UTC) - timedelta(minutes=15):
        raise ValueError("Only completed historical windows older than 15 minutes are supported")
    existing = cached(conn, symbol, start, end, feed)
    fetched = conn.execute("SELECT 1 FROM market_bar_windows WHERE symbol=? AND feed=? AND start<=? AND end>=?",
                           (symbol.upper(), feed, iso(start), iso(end))).fetchone()
    if not refresh and (fetched or continuous(existing, start, end)):
        return {"feed": feed, "bars": len(existing), "cached": True}
    own_client = client is None
    client = client or httpx.Client(timeout=30)
    headers = credentials()
    try:
        selected = feed
        while True:
            collected, token, seen = [], None, set()
            while True:
                params = {"symbols": symbol.upper(), "start": iso(start), "end": iso(end),
                          "timeframe": "1Min", "feed": selected, "adjustment": "raw", "limit": 10000,
                          "sort": "asc"}
                if token:
                    params["page_token"] = token
                for attempt in range(4):
                    throttle()
                    response = client.get("https://data.alpaca.markets/v2/stocks/bars", params=params, headers=headers)
                    if response.status_code != 429 and response.status_code < 500:
                        break
                    if attempt < 3:
                        time.sleep(min(30, max(2**attempt, float(response.headers.get("Retry-After", 0)))))
                if response.status_code == 403 and selected == "sip":
                    break
                if response.status_code != 200:
                    raise ValueError(f"Alpaca historical bars failed (HTTP {response.status_code})")
                data = response.json()
                for b in data.get("bars", {}).get(symbol.upper(), []):
                    ts = timestamp(b["t"])
                    if start <= ts < end:
                        collected.append(Bar(ts, b["o"], b["h"], b["l"], b["c"], b["v"], selected, b.get("vw")))
                token = data.get("next_page_token")
                if not token:
                    # Complete response replaces the requested slice, including removed revisions.
                    with conn:
                        conn.execute("DELETE FROM market_bars WHERE symbol=? AND feed=? AND ts>=? AND ts<?",
                                     (symbol.upper(), selected, iso(start), iso(end)))
                        put_bars(conn, symbol, collected)
                        conn.execute("INSERT OR IGNORE INTO market_bar_windows VALUES(?,?,?,?)",
                                     (symbol.upper(), selected, iso(start), iso(end)))
                    return {"feed": selected, "bars": len(collected), "cached": False,
                            "fallback": selected != feed}
                if token in seen:
                    raise ValueError("Alpaca pagination repeated a token; cache unchanged")
                seen.add(token)
            selected = "iex"
    finally:
        if own_client:
            client.close()
