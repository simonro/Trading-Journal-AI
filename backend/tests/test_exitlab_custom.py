"""Saved rule visibility, descriptive evaluations and fill narratives."""
from dataclasses import replace
from datetime import datetime
import json
import pytest
from test_exitlab_storage import db, store_trade
from test_exitlab_engine import warmup_times, fixture
from test_exitlab_m5 import client_for, seed
from test_exitlab_m4 import add_days
from exitlab import simple
from exitlab.models import UTC, MINUTE
from exitlab.bars import put_bars
from exitlab.schema import migrate

CUSTOM = {'name': 'Half at 1R, rest EMA 9',
          'tranches': [{'kind': 'target', 'fraction': .5, 'r': 1}, {'kind': 'close', 'fraction': .5}],
          'indicator': {'kind': 'ema9', 'timeframe': 1}}


def create(client, rule=CUSTOM):
    response = client.post('/api/exitlab/rules', json=rule)
    assert response.status_code == 201, response.text
    assert response.json()['show_in_tables'] is True
    return response.json()['id']


def test_existing_rules_migrate_to_shown_without_changing_definition_or_history(db):
    with client_for(db) as client:
        rule_id = create(client)
    before = tuple(db.execute('SELECT id,name,json,hash,created_at,is_preset FROM exit_rules WHERE id=?', (rule_id,)).fetchone())
    db.execute('ALTER TABLE exit_rules DROP COLUMN show_in_tables')
    migrate(db)
    after = db.execute('SELECT * FROM exit_rules WHERE id=?', (rule_id,)).fetchone()
    assert tuple(after[k] for k in ('id','name','json','hash','created_at','is_preset')) == before
    assert after['show_in_tables'] == 1
    db.execute('UPDATE exit_rules SET show_in_tables=0 WHERE id=?', (rule_id,))
    db.commit()
    migrate(db)
    assert db.execute('SELECT show_in_tables FROM exit_rules WHERE id=?', (rule_id,)).fetchone()[0] == 0
    assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_visibility_persists_in_both_tables_without_changing_rule(db):
    seed(db, [(100,100.5,99.5,100)])
    with client_for(db) as client:
        rule_id = create(client, {'name': 'Three R', 'tranches': [{'kind': 'target', 'r': 3, 'fraction': 1}]})
        before = dict(db.execute('SELECT * FROM exit_rules WHERE id=?', (rule_id,)).fetchone())
        history = client.post('/api/exitlab/history', json={'account_id': 1}).json()
        assert [r['rule']['name'] for r in history['rules']][-1] == 'Three R'
        assert history['rules'][-1]['rule_id'] == rule_id
        assert history['rules'][-1]['is_preset'] is False
        assert client.get('/api/exitlab/trades/1/simple').json()['rules'][-1]['rule_id'] == rule_id
        assert client.patch(f'/api/exitlab/rules/{rule_id}', json={'show_in_tables': False}).status_code == 200
        migrate(db)
        hidden = client.post('/api/exitlab/history', json={'account_id': 1}).json()
        assert hidden['custom_rules'] == [{'id': rule_id, 'name': 'Three R', 'show_in_tables': False}]
        assert len(client.get('/api/exitlab/trades/1/simple').json()['rules']) == 4
        assert client.patch(f'/api/exitlab/rules/{rule_id}', json={'show_in_tables': True}).status_code == 200
        assert len(client.get('/api/exitlab/trades/1/simple').json()['rules']) == 5
        assert dict(db.execute('SELECT * FROM exit_rules WHERE id=?', (rule_id,)).fetchone()) == before
        assert client.patch('/api/exitlab/rules/1', json={'show_in_tables': False}).status_code == 422
        assert client.patch('/api/exitlab/rules/999', json={'show_in_tables': False}).status_code == 404
        assert client.patch(f'/api/exitlab/rules/{rule_id}', json={'show_in_tables': 'false'}).status_code == 422
        assert client.patch(f'/api/exitlab/rules/{rule_id}', json={'show_in_tables': False, 'name': 'Oops'}).status_code == 422


@pytest.mark.parametrize('direction,quantity,verb,percent', [(1,100,'Sold','50'), (-1,100,'Covered','50'), (1,61,'Sold','49.18'), (1,2.5,'Sold','50')])
def test_custom_scale_out_story_comes_from_executed_fills_and_preserves_dollars(db, direction, quantity, verb, percent):
    path = [(100,101.1,99.5,101), (101,101,100.2,100.2), (100.2,100.2,99.8,99.8)]
    if direction == -1:
        path = [(200-o,200-l,200-h,200-c) for o,h,l,c in path]
    trade, bars = fixture(path, direction=direction, quantity=quantity)
    warmup = [replace(bars[0], ts=ts, open=100, high=100, low=100, close=100) for ts in warmup_times(trade, 9)]
    store_trade(db, trade)
    put_bars(db, trade.symbol, warmup + bars)
    db.execute('UPDATE trades SET net_pnl=12.34 WHERE id=1')
    db.commit()
    with client_for(db) as client:
        create(client)
        replay = client.get('/api/exitlab/trades/1/simple').json()
        r = replay['rules'][-1]['trades'][0]
        assert r['eligible']
        assert [f['reason'] for f in r['fills']] == ['target', 'ema9']
        assert r['what_happened'] == f'{verb} {percent}% at +1R 09:30, rest on EMA 9 close 09:33'
        assert sum(f['quantity'] for f in r['fills']) == quantity
        assert r['pnl'] == pytest.approx(r['pnl_r'] * trade.risk * quantity)
        assert replay['rows'][0]['journal_trade']['net_pnl'] == 12.34
        assert client.post('/api/exitlab/history', json={'account_id': 1}).json()['rules'][-1]['trades'][0]['pnl'] == r['pnl']


@pytest.mark.parametrize('reason,label', [('ema20','on EMA 20 close'), ('ema_cross','on EMA 9/20 cross'), ('vwap','on VWAP close'), ('stop','at the stop'), ('minutes','at the timed exit'), ('clock','at the clock exit'), ('session_close','at session close'), ('session_close_clock_fallback','at early session close')])
def test_other_custom_fill_reasons_are_plain_and_not_called_session_close(reason, label):
    trade, _ = fixture([(100,100,100,100)])
    fills = [{'ts':'2026-10-16T14:41:00Z', 'price':100.5, 'quantity':100, 'reason':reason}]
    assert simple.fill_story(fills, trade) == f'Sold 100% {label} 10:41'


def test_multiple_targets_use_each_fill_price_and_do_not_collapse_to_last_exit():
    trade, _ = fixture([(100,100,100,100)])
    fills = [{'ts':'2026-10-16T14:09:00Z', 'price':101, 'quantity':25, 'reason':'target'},
             {'ts':'2026-10-16T14:41:00Z', 'price':102, 'quantity':75, 'reason':'target'}]
    assert simple.fill_story(fills, trade) == 'Sold 25% at +1R 10:09, rest at +2R 10:41'


def test_stop_first_custom_and_duplicate_policy_names_keep_separate_rows(db):
    seed(db, [(100,101.1,98.9,100)])
    with client_for(db) as client:
        first = create(client, {'name': 'My target', 'tranches': [{'kind': 'target','r': 1,'fraction': 1}]})
        second = create(client, {'name': 'Same policy, my name', 'tranches': [{'kind': 'target','r': 1,'fraction': 1}]})
        replay = client.get('/api/exitlab/trades/1/simple').json()
    assert [r['rule_id'] for r in replay['rules']][-2:] == [first, second]
    r = replay['rules'][-1]['trades'][0]
    assert r['what_happened'] == 'Sold 100% at the stop 09:30'
