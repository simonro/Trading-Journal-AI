"""Contracts: safe readiness preflight, demo repeatability, credential aliases."""
from pathlib import Path
import sys
import pytest
from test_exitlab_engine import fixture, ZERO
from test_exitlab_storage import db, cohort
from exitlab.bars import credentials, put_bars
from exitlab.service import readiness
from exitlab.simple import simple_replay


@pytest.mark.parametrize("names", [("APCA_API_KEY_ID", "APCA_API_SECRET_KEY"), ("ALPACA_TRADING_KEY", "ALPACA_TRADING_SECRET")])
def test_explicit_credential_file_aliases_override_stale_environment(tmp_path, monkeypatch, names):
    path = tmp_path / "fixture.env"
    path.write_text(f"{names[0]}=synthetic-key\n{names[1]}=synthetic-secret\n")
    monkeypatch.setenv("ALPACA_ENV_FILE", str(path))
    monkeypatch.setenv("APCA_API_KEY_ID", "stale-fixture-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "stale-fixture-secret")
    assert credentials() == {"APCA-API-KEY-ID": "synthetic-key", "APCA-API-SECRET-KEY": "synthetic-secret"}


def test_demo_seed_is_offline_idempotent_and_keeps_other_accounts(db, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from seed_exitlab_demo import seed, LABEL
    import exitlab.bars
    monkeypatch.setattr(exitlab.bars, "fetch", lambda *a, **kw: pytest.fail("Offline seed made a network call"))
    days = ["2026-10-01", "2026-10-02"]
    symbols = ["FIXTURE", "SECOND", "THIRD"]
    for day in days:
        _, bars = fixture([(100, 101, 99, 100)], day=day)
        for symbol in symbols:
            put_bars(db, symbol, bars)
    db.commit()
    first = seed(db, days, symbols)
    assert first["added"] == 18 and not first["skipped"]
    second = seed(db, days, symbols)
    assert second["added"] == 0 and second["existing"] == 18
    assert db.execute("SELECT name FROM accounts WHERE id=1").fetchone()[0] == "Synthetic fixture"
    rows = db.execute("SELECT source,excursion_source,setup_notes FROM trades WHERE account_id=?", (first["account_id"],)).fetchall()
    assert all(r[0] == r[1] == "demo" and LABEL in r[2] for r in rows)
    assert db.execute('SELECT count(*) FROM trade_analysis WHERE stop_loss IS NOT NULL').fetchone()[0] > 0
    assert db.execute("SELECT COUNT(*) FROM market_bars").fetchone()[0] == len(days)*len(symbols)*len(bars)
