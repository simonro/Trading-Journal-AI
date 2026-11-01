import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import update_check  # noqa: E402

NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def check_on(monkeypatch):
    monkeypatch.setenv("UPDATE_CHECK", "1")


def release(tag, url="https://github.com/simonro/Trading-Journal-AI/releases/tag/v1.6.0"):
    return lambda: {"tag": tag, "url": url}


def test_version_file_is_the_source_of_truth():
    version = update_check.current_version()
    assert update_check.parse_version(version) > (0, 0, 0)
    assert version == (Path(__file__).resolve().parents[2] / "VERSION").read_text().strip()


def test_newer_release_is_reported_and_cached(tmp_path):
    cache = tmp_path / "update_check.json"
    r = update_check.check(now=NOW, cache=cache, fetch=release("v1.6.0"), current="1.5.0")
    assert r == {"current": "1.5.0", "latest": "1.6.0", "available": True,
                 "url": "https://github.com/simonro/Trading-Journal-AI/releases/tag/v1.6.0"}
    assert json.loads(cache.read_text())["checked"] == NOW


def test_same_or_older_release_is_not_available(tmp_path):
    assert not update_check.check(now=NOW, cache=tmp_path / "a.json", fetch=release("v1.5.0"), current="1.5.0")["available"]
    assert not update_check.check(now=NOW, cache=tmp_path / "b.json", fetch=release("v1.4.9"), current="1.5.0")["available"]


def test_asks_github_at_most_once_a_day(tmp_path):
    cache = tmp_path / "update_check.json"
    calls = []

    def fetch():
        calls.append(1)
        return {"tag": "v1.6.0", "url": update_check.RELEASES_PAGE}

    update_check.check(now=NOW, cache=cache, fetch=fetch, current="1.5.0")
    r = update_check.check(now=NOW + 3600, cache=cache, fetch=fetch, current="1.5.0")
    assert len(calls) == 1 and r["available"]
    update_check.check(now=NOW + update_check.DAY + 1, cache=cache, fetch=fetch, current="1.5.0")
    assert len(calls) == 2


def test_offline_is_silent_and_retries_next_start(tmp_path):
    cache = tmp_path / "update_check.json"

    def offline():
        raise OSError("no network")

    r = update_check.check(now=NOW, cache=cache, fetch=offline, current="1.5.0")
    assert r["available"] is False and r["latest"] is None
    assert not cache.exists()


def test_opt_out_never_fetches(tmp_path, monkeypatch):
    monkeypatch.setenv("UPDATE_CHECK", "0")

    def fetch():
        raise AssertionError("must not reach GitHub")

    r = update_check.check(now=NOW, cache=tmp_path / "c.json", fetch=fetch, current="1.5.0")
    assert r["available"] is False
    assert not update_check.enabled()


def test_fetch_rejects_odd_tags_and_foreign_links(monkeypatch):
    class Resp:
        status_code = 200

        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    monkeypatch.setattr(update_check.httpx, "get", lambda *a, **k: Resp({"tag_name": "nightly", "html_url": "x"}))
    assert update_check._fetch() is None
    monkeypatch.setattr(update_check.httpx, "get", lambda *a, **k: Resp(
        {"tag_name": "v2.0.0", "html_url": "https://example.com/malware"}))
    assert update_check._fetch() == {"tag": "v2.0.0", "url": update_check.RELEASES_PAGE}


def test_api_endpoint_answers_without_a_check(monkeypatch):
    monkeypatch.setenv("UPDATE_CHECK", "0")
    from fastapi.testclient import TestClient
    import main

    with TestClient(main.app) as client:
        body = client.get("/api/update").json()
    assert body["available"] is False
    assert body["current"] == update_check.current_version()
