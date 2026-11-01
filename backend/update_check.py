"""Is there a newer release on GitHub? Asked at most once a day, notify only.

A user who cloned or downloaded the journal has no other way to hear about the next release
(GitHub only notifies people who Watch the repo). On start the backend asks GitHub's public
releases endpoint for the latest tag, caches the answer for a day in backend/update_check.json,
prints one console line when a newer version exists, and serves the same answer to the frontend
banner through GET /api/update.

What goes out: one anonymous GET to api.github.com. GitHub sees your IP and the app version in the
User-Agent, nothing else: no keys, no trades, no settings. It gives up after 3 seconds and stays
silent when offline or rate limited. It never downloads or installs anything.
Turn it off with UPDATE_CHECK=0 in backend/.env.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

import httpx

REPO = "simonro/Trading-Journal-AI"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"
CACHE = Path(__file__).resolve().parent / "update_check.json"
DAY = 86400
RETRY_AFTER_FAIL = 3600  # a failed check (offline, rate limited) is retried after an hour, not every start
TIMEOUT = 3.0
_TAG_RE = re.compile(r"^v?\d+\.\d+\.\d+[0-9A-Za-z.\-]{0,20}$")


def current_version() -> str:
    """The VERSION file at the repo root is the single source of truth."""
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def parse_version(v: str) -> tuple[int, ...]:
    """'v1.2.3' -> (1, 2, 3). Anything unparsable is (0,), older than everything."""
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", (v or "").strip())
    return tuple(int(x) for x in m.groups()) if m else (0,)


def enabled() -> bool:
    return os.getenv("UPDATE_CHECK", "1").strip().lower() not in ("0", "false", "no", "off")


def _fetch() -> dict | None:
    r = httpx.get(RELEASES_API, timeout=TIMEOUT, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"trading-journal-ai/{current_version()}",
    })
    if r.status_code != 200:
        return None
    d = r.json()
    if not isinstance(d, dict):
        return None
    tag = str(d.get("tag_name") or "")
    url = str(d.get("html_url") or "")
    # Only a well-formed version tag counts, and the link only ever points at this repo's
    # releases, whatever the response says.
    if not _TAG_RE.match(tag):
        return None
    return {"tag": tag, "url": url if url.startswith(RELEASES_PAGE + "/") else RELEASES_PAGE}


def check(now: float | None = None, cache: Path = CACHE, fetch=_fetch, current: str | None = None) -> dict:
    """{"current", "latest", "available", "url"}. Uses a cached answer younger than a day. Never raises."""
    current = current or current_version()
    result = {"current": current, "latest": None, "available": False, "url": RELEASES_PAGE}
    if not enabled():
        return result
    now = time.time() if now is None else now
    try:
        cached = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cached = None
    if not isinstance(cached, dict):
        cached = {}
    if 0 <= now - cached.get("checked", 0) < DAY or 0 <= now - cached.get("failed", 0) < RETRY_AFTER_FAIL:
        latest = cached.get("latest")
    else:
        try:
            latest = fetch()
        except Exception:  # offline, timeout, GitHub down: all fine, try again in an hour
            latest = None
        if latest is not None:
            entry = {"checked": now, "latest": latest}
        else:
            # remember the failure so restarts do not keep asking; keep the last answer we had
            latest = cached.get("latest")
            entry = {"checked": cached.get("checked", 0), "latest": latest, "failed": now}
        try:
            cache.write_text(json.dumps(entry), encoding="utf-8")
        except OSError:
            pass
    if isinstance(latest, dict) and latest.get("tag"):
        result["latest"] = latest["tag"].lstrip("v")
        result["url"] = latest.get("url") or RELEASES_PAGE
        result["available"] = parse_version(latest["tag"]) > parse_version(current)
    return result


_state: dict | None = None
_lock = threading.Lock()


def status() -> dict:
    """The last answer, for GET /api/update. checked is False until the start-up check has finished,
    so the banner knows to ask again."""
    with _lock:
        if _state is not None:
            return dict(_state, checked=True)
    return {"current": current_version(), "latest": None, "available": False, "url": RELEASES_PAGE,
            "checked": not enabled()}


def _run() -> None:
    global _state
    result = check()
    with _lock:
        _state = result
    if result["available"]:
        print(
            f"\nA newer Trading Journal AI release is available: v{result['latest']} (you run v{result['current']}).\n"
            f"Release notes: {result['url']}\n"
            "To update: git pull, then run setup.bat. Downloaded the ZIP? Unzip the new version and copy\n"
            "backend/.env, backend/trading_journal.db and the backend/uploads folder into it.\n"
            "Turn this check off with UPDATE_CHECK=0.\n",
            flush=True,
        )


def start_background_check() -> None:
    """Check on a daemon thread so startup never waits on the network. No-op when UPDATE_CHECK=0."""
    if enabled():
        threading.Thread(target=_run, daemon=True, name="update-check").start()
