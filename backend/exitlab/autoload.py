"""One background job that loads every missing session and warm-up price window.

The plan reuses the replay's own windows (`regular_windows`: previous regular session
for warm-up, then the trade's session) and the cache's own completeness test
(`bars.covered`), so a window counts as missing exactly when Load prices would request it.
Requests go through `bars.fetch`, which keeps the shared one-request-per-second budget.
"""
from datetime import datetime
import threading
import httpx
from . import bars as cache
from .bars import covered, fetch, loadable
from .calendar import regular_windows
from .models import NY, UTC, iso
from .eligibility import MISSING_PRICES
from .service import load_entries

_lock = threading.Lock()
_state = {"running": False, "account_id": None, "total": 0, "done": 0, "failed": 0,
          "failures": [], "started_at": None, "finished_at": None}
_thread = None


def http_client():
    return httpx.Client(timeout=30)


def plan(conn, account_id, feed="sip", now=None, entries=None):
    """Stock trades with a stop, excluded for nothing but missing prices, and their missing windows.

    Trades already eligible still qualify when SPY or the warm-up session is missing, since
    rule replays need them. A session that closed less than 15 minutes ago waits.
    """
    items = []
    for entry in entries if entries is not None else load_entries(conn, account_id, feed):
        trade = entry.get("trade")
        if trade is None or trade.stop is None or not set(entry["reasons"]) <= {MISSING_PRICES}:
            continue
        windows = regular_windows(trade)
        if not loadable(windows[-1][1], now):
            continue  # Today's session: Alpaca serves it 15 minutes after the close.
        symbols = {trade.symbol.upper(): entry["bars"]}
        symbols.setdefault("SPY", entry.get("spy_bars", []))
        missing = [(symbol, start, end) for symbol, existing in symbols.items()
                   for start, end in windows if not covered(conn, symbol, start, end, feed, existing)]
        if missing:
            items.append({"trade_id": trade.id, "ticker": trade.symbol,
                          "date": trade.entered_at.astimezone(NY).date().isoformat(), "windows": missing})
    return items


def status():
    with _lock:
        return {**_state, "failures": list(_state["failures"])}


def _update(**changes):
    with _lock:
        _state.update(changes)


def _run(items, feed, opener):
    conn = client = None
    # A window that failed once fails for every trade that needs it (SPY's are shared), so it
    # is asked for once per job, not once per trade.
    failed_windows = {}
    try:
        conn = opener()
        client = http_client()
        for item in items:
            try:
                for window in item["windows"]:
                    if window in failed_windows:
                        raise ValueError(failed_windows[window])
                    symbol, start, end = window
                    try:
                        result = fetch(conn, symbol, start, end, feed, client=client)
                        if result["feed"] != feed:
                            raise ValueError(f"{feed.upper()} prices are unavailable for {symbol}; nothing was saved for this trade's comparison")
                    except Exception as exc:
                        failed_windows[window] = str(exc) or exc.__class__.__name__
                        raise
            except Exception as exc:  # One trade's failure never stops the rest.
                with _lock:
                    _state["failed"] += 1
                    _state["failures"].append({"ticker": item["ticker"], "date": item["date"],
                                               "reason": str(exc) or exc.__class__.__name__})
            finally:
                with _lock:
                    _state["done"] += 1
    except Exception as exc:  # setup failed (database or HTTP client): report it, never stay "running"
        with _lock:
            _state["failures"].append({"ticker": "All trades", "date": "not started", "reason": str(exc) or exc.__class__.__name__})
    finally:
        for closer in (client, conn):
            try:
                if closer is not None:
                    closer.close()
            except Exception:
                pass
        _update(running=False, finished_at=iso(datetime.now(UTC)))


def start(conn, account_id, opener, feed="sip"):
    """Plan and start the job. Returns the starting status, or None when one is already running."""
    global _thread
    with _lock:
        if _state["running"]:
            return None
        _state.update(running=True, account_id=account_id, total=0, done=0, failed=0, failures=[],
                      started_at=iso(datetime.now(UTC)), finished_at=None)
    try:
        items = plan(conn, account_id, feed)
        if items:
            cache.credentials()  # Fail once, plainly, rather than once per trade.
    except Exception:
        _update(running=False, finished_at=iso(datetime.now(UTC)))
        raise
    _update(total=len(items))
    if not items:
        _update(running=False, finished_at=iso(datetime.now(UTC)))
        return status()
    _thread = threading.Thread(target=_run, args=(items, feed, opener), name="exitlab-load-missing", daemon=True)
    _thread.start()
    return status()


def wait(timeout=None):
    """Test helper: block until the current job thread ends."""
    if _thread is not None:
        _thread.join(timeout)
