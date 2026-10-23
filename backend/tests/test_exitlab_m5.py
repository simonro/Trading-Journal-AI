"""Simple history, plain outcomes, zero costs and forward-only research."""
from dataclasses import replace
from datetime import datetime
import json
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from test_exitlab_engine import fixture, ZERO
from test_exitlab_storage import db, store_trade, cohort
from test_exitlab_m4 import add_days
from exitlab.bars import put_bars
from exitlab.models import UTC
from exitlab.rules import Costs
from exitlab import simple
from exitlab.api import router, connection
from exitlab.schema import migrate


def seed(db, path, stop=True, direction=1):
    t, bars = fixture(path, direction=direction)
    if not stop:
        t = replace(t, stop=None)
    store_trade(db, t)
    put_bars(db, t.symbol, bars)
    db.commit()
    return t, bars


def client_for(db):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[connection] = lambda: db
    return TestClient(app)


@pytest.mark.parametrize('direction,path', [(1, [(100,101.01,98.9,100)]), (-1, [(100,101.1,98.99,100)])])
def test_simple_collision_is_plain_and_stop_first(db, direction, path):
    seed(db, path, direction=direction)
    with client_for(db) as client:
        response = client.get('/api/exitlab/trades/1/simple')
        assert response.status_code == 200, response.text
        r = response.json()
    assert len(r['rules']) == 4
    target = r['rules'][0]['trades'][0]
    assert target['pnl_r'] == pytest.approx(-1)
    assert target['what_happened'] == 'Stop hit at 09:30'
    assert r['costs'] == {'slippage_bps': 0, 'per_fill': 0, 'per_share': 0}
    assert {h['label'] for h in r['first_hits']} == {'Stop', '1R'}
    assert r['rules'][3]['rule']['name'] == 'Breakeven after +1R'


def test_simple_no_stop_saves_silently_rejects_bad_price_and_recomputes(db):
    seed(db, [(100,101.01,99.5,101)], stop=False)
    with client_for(db) as client:
        assert 'missing_initial_stop' in client.get('/api/exitlab/trades/1/simple').json()['reasons']
        assert client.post('/api/exitlab/trades/1/stops', json={'price': 101}).status_code == 422
        assert db.execute('SELECT stop_loss FROM trade_analysis').fetchone()[0] is None
        assert client.post('/api/exitlab/trades/1/stops', json={'price': 99}).status_code == 201
        r = client.get('/api/exitlab/trades/1/simple').json()
    assert r['trade']['stop'] == 99 and not r['reasons']
    assert r['rules'][0]['trades'][0]['pnl_r'] == 1
    assert r['rules'][0]['trades'][0]['what_happened'] == 'Hit 1R at 09:30 before the stop'
    assert db.execute('SELECT stop_loss FROM trade_analysis').fetchone()[0] == 99


def test_history_includes_all_sessions_and_preserves_exclusions(db):
    cohort(db)
    db.execute("UPDATE trade_analysis SET stop_loss=NULL WHERE trade_group='1'")
    db.execute("DELETE FROM market_bars WHERE substr(ts,1,10)='2026-10-02'")
    db.commit()
    r, _ = simple.history(db, 1)
    assert len(r['rows']) == 10
    assert any('missing_initial_stop' in row['reasons'] for row in r['rows'])
    assert any('incomplete_minute_coverage_through_close' in row['reasons'] for row in r['rows'])


def test_journal_costs_are_zero_by_default_and_shared_with_trade_view(db):
    seed(db, [(100,100.5,99.5,100)])
    with client_for(db) as client:
        assert client.get('/api/exitlab/settings').json()['slippage_bps'] == 0
        assert client.put('/api/exitlab/settings', json={'slippage_bps': -1}).status_code == 422
        assert client.put('/api/exitlab/settings', json={'per_fill': 1}).status_code == 422
        assert client.put('/api/exitlab/settings', json={'per_share': .01, 'slippage_bps': 1}).status_code == 200
        r = client.get('/api/exitlab/trades/1/simple').json()
    assert r['rules'][2]['trades'][0]['pnl_r'] == pytest.approx(0)
    assert r['rules'][2]['trades'][0]['actual_r'] == pytest.approx(0)
    migrate(db)
    assert simple.settings(db).per_share == 0


def test_plain_close_and_breakeven_narratives_use_actual_engine_exit(db):
    seed(db, [(100,100.5,100,100.5), (100.5,101.2,100.5,101.1)])
    r = simple.simple_replay(db, 1)
    assert r['rules'][2]['trades'][0]['what_happened'] == 'Stop never hit, closed at 16:00'
    assert r['rules'][3]['trades'][0]['what_happened'] == 'Stop moved to entry at 09:31, held to close'


def test_structural_exclusions_with_a_stop_are_not_counted_as_missing_stops(db):
    seed(db, [(100,100.5,99.5,100)])
    db.execute("UPDATE trades SET instrument_type='OPTION' WHERE id=1")
    db.commit()
    r, _ = simple.history(db, 1)
    assert r['rows'][0]['stop_price'] == 99
    assert 'not_stock' in r['rows'][0]['reasons']
