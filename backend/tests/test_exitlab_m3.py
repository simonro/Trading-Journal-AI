"""Regression cases from the M1 and M2 code reviews, with explicit cash flows."""
from dataclasses import replace
from datetime import timedelta
import json
import sys
from pathlib import Path
import httpx
import pytest
from test_exitlab_engine import fixture, with_warmup, ZERO
from test_exitlab_storage import db, store_trade, cohort
from exitlab.models import MINUTE, NY
from exitlab.rules import PRESETS, Rule, Tranche, Indicator, ATRTrail, Costs
from exitlab.eligibility import from_row, reasons
from exitlab.simulate import simulate
from exitlab.excursion import excursions, persist
from exitlab.compare import compare
from exitlab.service import readiness
from exitlab.simple import simple_replay
from exitlab.bars import fetch, put_bars


@pytest.mark.parametrize('direction', [1, -1])
@pytest.mark.parametrize('seconds', [0, 7, 59])
def test_h1_entry_minute_extrema_are_optional(direction, seconds):
    t, bars = fixture([(99.8, 102, 98, 100.5)] if direction == 1 else [(100.2, 102, 98, 99.5)], direction)
    t = replace(t, entered_at=t.entered_at+timedelta(seconds=seconds))
    result = simulate(t, bars, PRESETS[2], ZERO)
    # Conservatively count the stop touch in the partial entry minute.
    assert result['pnl_r'] == pytest.approx(-1)


@pytest.mark.parametrize('field', ['entry', 'exit'])
@pytest.mark.parametrize('direction', [1, -1])
def test_h2_actual_prices_must_fit_their_minute(field, direction):
    t, bars = fixture([(100, 101, 99, 100)], direction)
    t = replace(t, **{field: 180})
    assert 'fill_outside_bar_range' in excursions(t, bars)['reasons']
    assert 'fill_outside_bar_range' in simulate(t, bars, PRESETS[0])['reasons']


def test_h2_tick_tolerance_and_session_close_fill():
    t, bars = fixture([(100, 100.5, 99.5, 100)])
    t = replace(t, exit=100.009, exited_at=bars[-1].ts+MINUTE)
    assert excursions(t, bars)['eligible']
    assert not excursions(replace(t, exit=100.011), bars)['eligible']


def test_m3_comparison_does_not_mutate_journal_metrics(db):
    entries = cohort(db)
    t, bars = entries[0]['trade'], entries[0]['bars']
    persist(db,t,excursions(t,bars))
    db.commit()
    original = tuple(db.execute('SELECT mfe_pct,excursion_source FROM trades WHERE id=1').fetchone())
    entries[0]['bars'] = []
    compare(entries,[PRESETS[0]])
    persist(db,t,excursions(t,[]))
    assert tuple(db.execute('SELECT mfe_pct,excursion_source FROM trades WHERE id=1').fetchone()) == original


def test_m5_split_fills_group_by_action_and_validate_each_print(db):
    t,bars = fixture([(100,101,99.5,100.5),(100.5,101,99.5,100.5)])
    store_trade(db,t)
    row = dict(db.execute('SELECT * FROM trades').fetchone())
    row['executions'] = json.dumps([
        {'date':'2026-10-16','time':'09:30:07','action':'BOT','qty':50,'price':99.9},
        {'date':'2026-10-16','time':'09:30:08','action':'BOT','qty':50,'price':100.1},
        {'date':'2026-10-16','time':'09:31:15','action':'SOLD','qty':40,'price':100.2},
        {'date':'2026-10-16','time':'09:31:25','action':'SOLD','qty':60,'price':100.7}])
    grouped = from_row(row,99)
    assert grouped.entry == 100 and grouped.exit == pytest.approx(100.5)
    assert grouped.entered_at.second == 7 and grouped.exited_at.second == 25
    result = simulate(grouped,bars,PRESETS[2],Costs(slippage_bps=0,per_fill=1))
    assert result['eligible'] and result['actual_pnl_modeled_costs'] == pytest.approx(50)
    bad = list(grouped.fills)
    bad[1] = (*bad[1][:3],180)
    assert 'fill_outside_bar_range' in reasons(replace(grouped,fills=tuple(bad)),bars)


def test_m6_persist_conservative_scalars(db):
    t,bars = fixture([(100,102,98,101),(101,105,97,103)])
    t = replace(t,entered_at=t.entered_at+timedelta(seconds=7), exited_at=t.entered_at+MINUTE+timedelta(seconds=7))
    store_trade(db,t)
    persist(db,t,excursions(t,bars))
    row = db.execute('SELECT * FROM trades').fetchone()
    assert row['mfe_pct'] == 3 and row['excursion_source'] == 'computed'
    assert row['exit_efficiency'] == 60
    assert row['mae_pct'] == -3


def test_l2_stopless_is_not_retrospective(db):
    cohort(db)
    db.execute('UPDATE trade_analysis SET stop_loss=NULL')
    result = readiness(db,1,'sip')
    assert result['missing_stops'] == 10


def test_l6_integer_thirds_reconcile_and_fractional_shares_stay_fractional():
    t,bars = fixture([(100,103.1,99.5,103)],quantity=100)
    rule = Rule(name='thirds',tranches=tuple(Tranche(kind='target',r=i,fraction=1/3) for i in (1,2,3)))
    result = simulate(t,bars,rule,ZERO)
    assert [f['quantity'] for f in result['fills']] == [33,33,34]
    assert result['pnl'] == pytest.approx(201)
    result = simulate(replace(t,quantity=1.5),bars,rule,ZERO)
    assert [f['quantity'] for f in result['fills']] == [.5,.5,.5]


def test_l7_minimum_risk_and_l9_clock_validation():
    t,bars = fixture([(100,100.5,99.5,100)])
    assert simulate(replace(t,stop=99.99),bars,PRESETS[0])['reasons'] == ['initial_risk_under_two_ticks']
    with pytest.raises(ValueError):
        Tranche(kind='clock',clock='16:00',fraction=1)
    t,bars = fixture([(100,100.5,99.5,100)],day='2026-11-27')
    rule = Rule(name='late',tranches=(Tranche(kind='clock',clock='15:00',fraction=1),))
    assert simulate(t,bars,rule,ZERO)['fills'][0]['reason'] == 'session_close_clock_fallback'


def test_l8_sparse_fetched_window_does_not_refetch(db,monkeypatch):
    import exitlab.bars as module
    monkeypatch.setattr(module,'credentials',lambda:{})
    monkeypatch.setattr(module,'throttle',lambda:None)
    t,bars = fixture([(100,100.5,99.5,100)],day='2026-10-01')
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json={'bars':{}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert not fetch(db,t.symbol,t.entered_at,t.entered_at+MINUTE,client=client)['cached']
        assert fetch(db,t.symbol,t.entered_at,t.entered_at+MINUTE,client=client)['cached']
        assert len(calls) == 1
        fetch(db,t.symbol,t.entered_at,t.entered_at+MINUTE,refresh=True,client=client)
        assert len(calls) == 2


@pytest.mark.parametrize('kind', ['ema20','atr'])
def test_h1_second_level_indicator_warmup_uses_completed_boundary(kind):
    t,bars = fixture([(100,100.5,99.5,100)])
    bars = with_warmup(t,bars)
    t = replace(t,entered_at=t.entered_at+timedelta(seconds=7))
    rule = Rule(name=kind,indicator=Indicator(kind='ema20')) if kind=='ema20' else Rule(name=kind,atr=ATRTrail())
    assert simulate(t,bars,rule,ZERO)['eligible']


def test_l1_l10_http_not_found_stop_evidence_and_strict_recompute(db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from exitlab.api import router, connection
    app=FastAPI()
    app.include_router(router)
    app.dependency_overrides[connection]=lambda:db
    t,bars=fixture([(100,100.5,99.5,100)])
    store_trade(db,t)
    db.execute("UPDATE trades SET executions='[]'")
    db.commit()
    with TestClient(app) as client:
        assert client.post('/api/exitlab/trades/999/replay',json={}).status_code == 404
        assert client.post('/api/exitlab/history',json={'account_id':1,'rule_ids':[999]}).status_code == 422
        assert client.post('/api/exitlab/trades/1/stops',json={'price':99}).status_code == 201
        assert client.post('/api/exitlab/excursions/recompute',json={'account_id':1,'scope':'holdout'}).status_code == 422


def test_m2_demo_stops_and_mixed_actual_styles():
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
    from seed_exitlab_demo import actual_exits
    t,bars=fixture([(100,100.2,99.8,100)]*20+[(100,100.1,98,98.5)])
    styles=set()
    partial=False
    for serial in range(5):
        style,fills=actual_exits(bars,t.entered_at+timedelta(seconds=7),100,99,1,100,serial,bars[-1].ts+MINUTE)
        styles.add(style)
        partial |= len(fills)>1
        assert sum(q for _,q,_ in fills) == 100
        assert fills[-1][0] <= bars[20].ts+MINUTE
        assert all(p>=99 for _,_,p in fills)
    assert len(styles)==5 and partial


def test_reset_demo_closes_handles_and_preserves_only_price_cache(db,tmp_path,monkeypatch):
    import database
    import sqlite3
    from contextlib import closing
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
    import reset_exitlab_demo as reset
    source = Path(database.DB_PATH)
    t,bars=fixture([(100,100.5,99.5,100)])
    put_bars(db,t.symbol,bars)
    db.commit()
    root=tmp_path/'isolated'
    (root/'backend').mkdir(parents=True)
    dest=root/'backend'/'exitlab_demo.db'
    monkeypatch.setattr(reset,'ROOT',root)
    monkeypatch.setattr(database,'DB_PATH',str(dest.with_name('exitlab_demo_building.db')))
    def fake_seed(command,check):
        conn=database.get_db()
        conn.execute("INSERT INTO accounts(id,name,type) VALUES(1,'Exit Lab demo | Synthetic trades, historical market prices','day_trading')")
        conn.execute("INSERT INTO trades(account_id,trade_group,date,ticker,instrument_type,side,source) VALUES(1,'exitlab-demo-v2:test','2026-10-16','FIXTURE','STOCK','LONG','demo')")
        conn.commit()
        conn.close()
    monkeypatch.setattr(reset.subprocess,'run',fake_seed)
    monkeypatch.setattr(sys,'argv',['reset','--database',str(dest),'--cache',str(source)])
    reset.main()
    with closing(sqlite3.connect(dest)) as conn:
        assert conn.execute('SELECT count(*) FROM market_bars').fetchone()[0] == len(bars)
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name='analysis_runs'").fetchone()
    monkeypatch.setattr(sys,'argv',['reset','--database',str(dest),'--cache',str(source),'--replace'])
    reset.main()
    assert dest.is_file() and not dest.with_name('exitlab_demo_building.db').exists()


def test_l11_edited_demo_metrics_keep_synthetic_identity(db):
    t,bars=fixture([(100,100.5,99.5,100)])
    store_trade(db,t)
    db.execute("UPDATE trades SET source='edited',excursion_source='demo',mfe_pct=123")
    row=db.execute('SELECT * FROM trades').fetchone()
    parsed=from_row(row)
    assert parsed.source=='demo'
    persist(db,parsed,excursions(parsed,bars))
    assert db.execute('SELECT mfe_pct FROM trades').fetchone()[0]==123
