"""Stop-first cash flows, journal-stop ownership and retired feature boundaries."""
from dataclasses import replace
import json
import sqlite3
import pytest
from test_exitlab_engine import fixture, ZERO
from test_exitlab_storage import db, store_trade, cohort
from test_exitlab_m5 import client_for, seed
from exitlab import simple
from exitlab.schema import migrate
from exitlab.stops import journal_stop
from exitlab.models import MINUTE
from exitlab.rules import Rule, Tranche, RStep, PRESETS
from exitlab.simulate import simulate


@pytest.mark.parametrize('direction', [1, -1])
def test_stop_precedes_target_and_threshold_without_branches(direction):
    path = [(100, 103.1, 98.9, 100)]
    trade, bars = fixture(path, direction)
    rule = Rule(name='collision', tranches=(Tranche(kind='target', r=2, fraction=1),),
                r_steps=(RStep(at_r=1, stop_r=0),))
    result = simulate(trade, bars, rule)
    assert result['pnl_r'] == -1
    assert result['pnl'] == -100
    assert [f['reason'] for f in result['fills']] == ['stop']
    assert not {'ambiguous', 'worst_fills', 'best_fills', 'stop_provenance'} & result.keys()


@pytest.mark.parametrize('direction', [1, -1])
def test_new_breakeven_stop_precedes_target_in_activation_bar(direction):
    path = [(100, 102.1, 99.5, 101.5)]
    if direction == -1:
        path = [(200-o, 200-l, 200-h, 200-c) for o,h,l,c in path]
    trade, bars = fixture(path, direction)
    rule = Rule(name='BE and target', tranches=(Tranche(kind='target', r=2, fraction=1),),
                r_steps=(RStep(at_r=1, stop_r=0),))
    result = simulate(trade, bars, rule)
    assert result['pnl_r'] == 0
    assert result['fills'][0]['price'] == 100
    assert result['fills'][0]['reason'] == 'stop'


@pytest.mark.parametrize('direction', [1, -1])
def test_activated_stop_gap_fills_worse_open(direction):
    path = [(100,100.8,99.5,100.5), (100.5,101.2,100.5,101), (99.7,100.2,99.6,100)]
    if direction == -1:
        path = [(200-o,200-l,200-h,200-c) for o,h,l,c in path]
    trade, bars = fixture(path, direction)
    result = simulate(trade, bars, PRESETS[3])
    assert result['pnl_r'] == pytest.approx(-.3)
    assert result['fills'][0]['price'] == pytest.approx(100-direction*.3)
    assert result['fills'][0]['timing'] == 'open'


@pytest.mark.parametrize('direction', [1, -1])
@pytest.mark.parametrize('collision', ['stop', 'target', 'none'])
def test_bar_priority_before_scheduled_indicator_exit(monkeypatch, direction, collision):
    import exitlab.simulate as engine
    path = [(100,100.2,99.8,100), (100.2,101.2,98.8 if collision=='stop' else 99.8,100.2)]
    if collision == 'none':
        path[1] = (100.2,100.5,99.8,100.2)
    if direction == -1:
        path = [(200-o,200-l,200-h,200-c) for o,h,l,c in path]
    trade, bars = fixture(path, direction, quantity=10)
    monkeypatch.setattr(engine, 'prepare', lambda *args: ({bars[1].ts: 'ema9'}, {}, {}, None))
    rule = Rule(name='half target rest signal', tranches=(Tranche(kind='target',r=1,fraction=.5), Tranche(kind='close',fraction=.5)))
    result = simulate(trade, bars, rule)
    expected = {'stop': (-10, ['stop']), 'target': (6, ['target','ema9']), 'none': (2, ['ema9'])}
    amount, reasons = expected[collision]
    assert result['pnl'] == pytest.approx(amount)
    assert [f['reason'] for f in result['fills']] == reasons
    if reasons[-1] == 'ema9':
        assert result['fills'][-1]['timing'] == 'open'
        assert result['fills'][-1]['ts'] == bars[1].ts.isoformat()


@pytest.mark.parametrize('direction', [1, -1])
def test_stop_precedes_due_time_exit(direction):
    path = [(100,100.2,99.8,100), (100.2,101.2,98.8,100)]
    if direction == -1:
        path = [(200-o,200-l,200-h,200-c) for o,h,l,c in path]
    trade, bars = fixture(path, direction)
    rule = Rule(name='minute',tranches=(Tranche(kind='minutes',minutes=1,fraction=1),))
    result = simulate(trade,bars,rule)
    assert result['pnl_r'] == -1
    assert result['fills'][0]['reason'] == 'stop'


def test_all_history_is_read_only_and_legacy_tables_remain_untouched(db):
    cohort(db)
    retired = ('stop_versions','exit_forward_locks','exit_cohorts','exit_holdout_periods','analysis_runs','trade_results','exit_settings')
    for table in retired:
        # Invalid legacy payloads demonstrate that no old fields are interpreted.
        db.execute(f'CREATE TABLE {table}(legacy TEXT)')
        db.execute(f'INSERT INTO {table} VALUES(?)', ('untouched',))
    db.commit()
    def guard(action, table, column, *args):
        if table in retired:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    db.set_authorizer(guard)
    try:
        migrate(db)
        db.execute('PRAGMA query_only=ON')
        result, entries = simple.history(db,1)
        assert len(entries) == 10
        assert all(rule['eligible'] == 10 for rule in result['rules'])
        assert len(simple.simple_replay(db,10)['rules']) == 4
        assert not {'rules_tried','holdout_sessions','development_sessions','split_boundary'} & result.keys()
        db.execute('PRAGMA query_only=OFF')
        with client_for(db) as client:
            assert client.post('/api/exitlab/trades/1/stops',json={'price':98}).status_code == 201
    finally:
        db.set_authorizer(None)
        db.execute('PRAGMA query_only=OFF')
    for table in retired:
        assert [tuple(r) for r in db.execute(f'SELECT * FROM {table}')] == [('untouched',)]


def test_journal_stop_wins_over_import_field_and_edits_both_ways(db):
    trade, _ = seed(db,[(100,101.2,99.5,101)])
    db.execute('ALTER TABLE trades ADD COLUMN stop REAL')
    db.execute('UPDATE trades SET stop=97')
    db.execute("UPDATE trade_analysis SET strategy='Keep strategy',notes='Keep notes'")
    db.commit()
    with client_for(db) as client:
        assert client.get('/api/exitlab/trades/1/simple').json()['trade']['stop'] == 99
        assert client.post('/api/exitlab/trades/1/stops',json={'price':98}).status_code == 201
        analysis = dict(db.execute('SELECT * FROM trade_analysis').fetchone())
        assert (analysis['stop_loss'],analysis['strategy'],analysis['notes']) == (98,'Keep strategy','Keep notes')
        # Direct journal edit must be the next replay's stop.
        db.execute('UPDATE trade_analysis SET stop_loss=99.25')
        db.commit()
        assert client.get('/api/exitlab/trades/1/simple').json()['trade']['stop'] == 99.25
        db.execute('UPDATE trade_analysis SET stop_loss=NULL')
        db.commit()
        assert client.get('/api/exitlab/trades/1/simple').json()['trade']['stop'] == 97
        db.execute('DELETE FROM trade_analysis')
        db.commit()
        assert client.post('/api/exitlab/trades/1/stops',json={'price':98.5}).status_code == 201
        assert db.execute('SELECT stop_loss FROM trade_analysis').fetchone()[0] == 98.5
        assert client.post('/api/exitlab/trades/1/stops',json={'price':98,'evidence_ref':'removed'}).status_code == 422


def test_import_stop_loss_fallback_and_missing_optional_analysis_table(db):
    seed(db,[(100,100.5,99.5,100)],stop=False)
    db.execute('ALTER TABLE trades ADD COLUMN stop_loss REAL')
    db.execute('UPDATE trades SET stop_loss=98')
    row = db.execute('SELECT * FROM trades').fetchone()
    assert journal_stop(db,row) == 98
    db.execute('DROP TABLE trade_analysis')
    assert journal_stop(db,row) == 98


def test_removed_endpoints_and_research_payloads_are_not_supported(db):
    with client_for(db) as client:
        paths = client.get('/openapi.json').json()['paths']
        for path in ('/forward-locks','/forward-locks/{lock_id}/results','/holdout/promote','/comparisons','/trades/{trade_id}/replay'):
            assert '/api/exitlab'+path not in paths
        for path in ('/forward-locks','/forward-locks/1/results','/holdout/promote','/comparisons','/trades/1/replay'):
            assert client.post('/api/exitlab'+path,json={'account_id':1}).status_code == 404
        for extra in ({'advanced':True},{'rule_ids':[1]},{'scope':'holdout'}):
            assert client.post('/api/exitlab/history',json={'account_id':1,**extra}).status_code == 422
