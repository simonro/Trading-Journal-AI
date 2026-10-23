"""SQLite, mocked REST, provenance, holdout exposure, and HTTP integration."""
from dataclasses import asdict, replace
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
import sys
import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_exitlab_engine import fixture, ZERO
from exitlab.schema import migrate
from exitlab.bars import put_bars, cached, fetch
from exitlab.models import UTC, MINUTE
from exitlab.stops import journal_stop
from exitlab.rules import PRESETS, Rule, Tranche
from exitlab.compare import compare
from exitlab.excursion import excursions, persist
from exitlab.service import readiness
from exitlab.simple import simple_replay


@pytest.fixture
def db(tmp_path, monkeypatch):
    import database
    monkeypatch.setattr(database,"DB_PATH",str(tmp_path/"test.db"))
    database.init_db()
    conn = database.get_db()
    conn.execute("INSERT INTO accounts(id,name,type) VALUES(1,'Synthetic fixture','day_trading')")
    conn.commit()
    yield conn
    conn.close()


def store_trade(db, t):
    from exitlab.models import NY
    execs = [{"date":dt.astimezone(NY).date().isoformat(),"time":dt.astimezone(NY).strftime("%H:%M:%S"),
              "action":action,"qty":t.quantity,"price":price} for dt,action,price in
             [(t.entered_at,"BOT" if t.direction==1 else "SOLD",t.entry),
              (t.exited_at,"SOLD" if t.direction==1 else "BOT",t.exit)]]
    db.execute("""INSERT INTO trades(id,account_id,trade_group,date,ticker,instrument_type,side,executions,source)
        VALUES(?,1,?,?,'FIXTURE','STOCK',?,?,?)""",
        (t.id,str(t.id),execs[0]["date"],"LONG" if t.direction==1 else "SHORT",json.dumps(execs),t.source))
    db.execute("INSERT INTO trade_analysis(trade_group,ticker,date,stop_loss) VALUES(?,'FIXTURE',?,?)",
               (str(t.id),execs[0]["date"],t.stop))
    db.commit()


def test_migrations_idempotent_and_presets(db):
    migrate(db)
    migrate(db)
    assert db.execute("SELECT count(*) FROM exit_rules WHERE is_preset=1").fetchone()[0] == 4


def test_cache_revisions_and_feed_isolation(db):
    t,bars = fixture([(100,100.5,99.5,100)])
    put_bars(db,t.symbol,bars[:1])
    old = db.execute("SELECT revision_hash FROM market_bars").fetchone()[0]
    put_bars(db,t.symbol,[replace(bars[0],high=101)])
    new = db.execute("SELECT revision_hash FROM market_bars").fetchone()[0]
    assert old != new
    assert not cached(db,t.symbol,t.entered_at,t.entered_at+MINUTE,"iex")
    assert cached(db,t.symbol,t.entered_at,t.entered_at+MINUTE,"sip")[0].high == 101


def test_rest_pagination_and_explicit_fallback(db, monkeypatch):
    import exitlab.bars as module
    monkeypatch.setattr(module,"credentials",lambda: {})
    monkeypatch.setattr(module,"throttle",lambda: None)
    t,bars = fixture([(100,100.5,99.5,100)],day="2026-10-01")
    calls=[]
    def handler(request):
        p=request.url.params
        calls.append(dict(p))
        if p['feed']=='sip':
            return httpx.Response(403)
        i=1 if 'page_token' in p else 0
        b=bars[i]
        return httpx.Response(200,json={"bars":{t.symbol:[{"t":b.ts.isoformat(),"o":b.open,"h":b.high,
            "l":b.low,"c":b.close,"v":b.volume,"vw":b.vwap}]},"next_page_token":None if i else "page2"})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result=fetch(db,t.symbol,t.entered_at,t.entered_at+2*MINUTE,client=client)
    assert result['feed']=='iex' and result['fallback']
    assert len(calls)==3 and calls[-1]['adjustment']=='raw'
    assert len(cached(db,t.symbol,t.entered_at,t.entered_at+2*MINUTE,'iex'))==2
    assert not cached(db,t.symbol,t.entered_at,t.entered_at+2*MINUTE,'sip')


def test_failure_does_not_cache_partial_pages(db,monkeypatch):
    import exitlab.bars as module
    monkeypatch.setattr(module,'credentials',lambda:{})
    monkeypatch.setattr(module,'throttle',lambda:None)
    t,bars=fixture([(100,100.5,99.5,100)],day='2026-10-01')
    def handler(request):
        if 'page_token' in request.url.params:
            return httpx.Response(401)
        return httpx.Response(200,json={'bars':{t.symbol:[]},'next_page_token':'second'})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError,match='HTTP 401'):
            fetch(db,t.symbol,t.entered_at,t.entered_at+2*MINUTE,client=client)
    assert db.execute('SELECT count(*) FROM market_bars').fetchone()[0]==0


def test_legacy_stop_is_unknown_and_synthetic_metrics_preserved(db):
    t,bars=fixture([(100,100.5,99.5,100)])
    t=replace(t,source='demo')
    store_trade(db,t)
    row=db.execute('SELECT * FROM trades').fetchone()
    assert journal_stop(db,row)==99
    db.execute('UPDATE trades SET mfe_pct=123')
    persist(db,t,excursions(t,bars))
    assert db.execute('SELECT mfe_pct FROM trades').fetchone()[0]==123


def test_real_metrics_survive_missing_data(db):
    t,bars=fixture([(100,100.5,99.5,100)])
    store_trade(db,t)
    persist(db,t,excursions(t,bars))
    assert db.execute('SELECT mfe_pct FROM trades').fetchone()[0]==.5
    persist(db,t,excursions(t,bars[:-1]))
    assert db.execute('SELECT mfe_pct FROM trades').fetchone()[0] == .5


def cohort(db):
    entries=[]
    for i,day in enumerate(['2026-10-01','2026-10-02','2026-10-05','2026-10-06','2026-10-07','2026-10-08','2026-10-09','2026-10-12','2026-10-13','2026-10-14']):
        t,bars=fixture([(100,101.01,99.5,101)],day=day)
        t=replace(t,id=i+1)
        store_trade(db,t)
        put_bars(db,t.symbol,bars)
        entries.append({'id':t.id,'trade':t,'bars':bars,'reasons':[]})
    db.commit()
    return entries


def test_http_rules_readiness_comparison_and_replay(db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from exitlab.api import router, connection
    app=FastAPI()
    app.include_router(router)
    app.dependency_overrides[connection]=lambda:db
    t,bars=fixture([(100,101.01,99.5,101)])
    store_trade(db,t)
    put_bars(db,t.symbol,bars)
    db.commit()
    with TestClient(app) as client:
        assert len(client.get('/api/exitlab/rules').json())==4
        created=client.post('/api/exitlab/rules',json={'name':'Timed','tranches':[{'kind':'minutes','minutes':2,'fraction':1}]})
        assert created.status_code==201
        assert client.post('/api/exitlab/rules',json={'name':'bad','tranches':[{'kind':'target','r':1,'fraction':.5}]}).status_code==422
        assert client.get('/api/exitlab/readiness?account_id=1').json()['eligible']==1
        r=client.post('/api/exitlab/history',json={'account_id':1})
        assert r.status_code==200,r.text
        assert r.json()['rules'][0]['trades'][0]['pnl_r']==1
        assert client.get('/api/exitlab/trades/1/simple').status_code==200


def test_legacy_seed_migration_preserves_synthetic_metrics(db):
    t,bars=fixture([(100,100.5,99.5,100)])
    store_trade(db,t)
    db.execute('ALTER TABLE trades DROP COLUMN excursion_source')
    db.execute('UPDATE trades SET mfe_pct=123')
    db.commit()
    migrate(db)
    row=db.execute('SELECT * FROM trades').fetchone()
    assert row['source']=='imported'
    assert row['excursion_source']=='legacy_synthetic'
    from exitlab.eligibility import from_row
    assert from_row(row).source=='demo'
    persist(db,t,excursions(t,bars))
    assert db.execute('SELECT mfe_pct FROM trades').fetchone()[0]==123


def test_main_mount_and_fresh_database(db,monkeypatch):
    import database
    import main
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        assert client.get('/').json()=={'status':'ok'}
        assert client.get('/api/exitlab/rules').status_code==200
        paths=client.get('/openapi.json').json()['paths']
        assert '/api/exitlab/trades/{trade_id}/simple' in paths
        assert '/api/exitlab/history' in paths
