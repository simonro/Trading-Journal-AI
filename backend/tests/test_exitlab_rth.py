"""Regular-session warm-up, strict paired minutes and retired costs."""
from dataclasses import replace
from datetime import datetime
import json
import pytest
from test_exitlab_engine import fixture, with_warmup
from test_exitlab_storage import db, store_trade
from test_exitlab_m5 import client_for, seed
from test_exitlab_m4 import add_days
from exitlab import api, simple
from exitlab.bars import put_bars
from exitlab.calendar import session, previous_session
from exitlab.models import Bar, Trade, MINUTE, NY, UTC
from exitlab.indicators import prepare
from exitlab.rules import Rule, Indicator, ATRTrail, Costs
from exitlab.simulate import simulate
from exitlab.service import readiness, load_entries
from exitlab.simple import simple_replay

RS = Rule(name='RS/RW', indicator=Indicator(kind='rs_rw', timeframe=5))


def paired(day='2026-10-16', offset=0):
    opening, closing = session(day)
    _, prior_close = previous_session(day)
    # Only the required tail is present. No premarket or overnight prices.
    prior_count = max(0, 65 - offset//5*5)
    times = [prior_close-i*MINUTE for i in range(prior_count,0,-1)]
    times += [opening+i*MINUTE for i in range(int((closing-opening)/MINUTE))]
    stock = [Bar(ts,100,102,98,100,100,vwap=100) for ts in times]
    spy = [replace(b,open=500,high=502,low=498,close=500,vwap=500) for b in stock]
    trade = Trade(1,'FIXTURE',1,10,100,100,opening+offset*MINUTE,closing,90)
    return trade, stock, spy


def test_sparse_premarket_and_afterhours_have_no_effect():
    trade, stock, spy = paired(offset=70)
    base = simulate(trade, stock, RS, spy_bars=spy)
    assert base['eligible']
    opening, closing = session('2026-10-16')
    noise = [replace(stock[0],ts=ts,high=900) for ts in
             [opening-90*MINUTE, opening-2*MINUTE, closing+3*MINUTE]]
    noisy = simulate(trade, sorted(stock+noise,key=lambda b:b.ts), RS,
                     spy_bars=sorted(spy+noise,key=lambda b:b.ts))
    assert noisy['pnl'] == base['pnl']
    assert noisy['indicator_values'] == base['indicator_values']


@pytest.mark.parametrize('symbol', ['SPY','FIXTURE'])
@pytest.mark.parametrize('offset', [0,70])
def test_regular_gap_names_first_missing_symbol_minute_even_after_exit(symbol,offset):
    trade, stock, spy = paired(offset=offset)
    opening, _ = session('2026-10-16')
    # 10:37 is before the 10:40 entry, and a second gap is later in the day.
    gaps = {opening+67*MINUTE, opening+100*MINUTE}
    if symbol == 'SPY': spy = [b for b in spy if b.ts not in gaps]
    else: stock = [b for b in stock if b.ts not in gaps]
    result = simulate(trade,stock,RS,spy_bars=spy)
    assert not result['eligible']
    assert result['reasons'] == [f'regular_minute_missing|{symbol}|2026-10-16T10:37:00-04:00']


@pytest.mark.parametrize('day,offset,prior_start,prior_last', [
    ('2026-10-16',30,'2026-10-15 15:25','2026-10-15 15:59'),
    ('2026-11-30',0,'2026-11-27 11:55','2026-11-27 12:59'),
    ('2026-11-27',0,'2026-11-25 14:55','2026-11-25 15:59'),
    ('2026-11-02',0,'2026-10-30 14:55','2026-10-30 15:59'),
])
def test_warmup_previous_exchange_session_tail(day,offset,prior_start,prior_last):
    trade, stock, spy = paired(day,offset)
    opening, _ = session(day)
    prior = [b for b in stock if b.ts < opening]
    assert prior[0].ts.astimezone(NY).strftime('%Y-%m-%d %H:%M') == prior_start
    assert prior[-1].ts.astimezone(NY).strftime('%Y-%m-%d %H:%M') == prior_last
    assert simulate(trade,stock,RS,spy_bars=spy)['eligible']
    missing = simulate(trade,stock,RS,spy_bars=[b for b in spy if b.ts >= opening])
    assert missing['reasons'] == [f'regular_minute_missing|SPY|{prior[0].ts.astimezone(NY).isoformat()}']


@pytest.mark.parametrize('kind,timeframe', [('ema9',1),('ema20',5),('ema_cross',5),('atr',1)])
def test_ema_and_atr_ignore_premarket_use_previous_early_close(kind,timeframe):
    trade,bars = fixture([(100,100.2,99.8,100)],day='2026-11-30')
    regular = with_warmup(trade,bars,100)
    rule = Rule(name=kind,atr=ATRTrail()) if kind=='atr' else Rule(name=kind,indicator=Indicator(kind=kind,timeframe=timeframe))
    assert prepare(trade,regular,rule)[-1] is None
    noisy = [replace(b,ts=trade.entered_at-(i+1)*MINUTE,high=1000) for i,b in enumerate(regular[:100])]
    assert prepare(trade,sorted(regular+noisy,key=lambda b:b.ts),rule) == prepare(trade,regular,rule)
    missing = prepare(trade,noisy+bars,rule)[-1]
    assert missing.startswith('regular_minute_missing|FIXTURE|2026-11-27T12:') or missing.startswith('regular_minute_missing|FIXTURE|2026-11-27T11:')


def test_atr_only_requires_the_minutes_it_uses():
    trade,bars = fixture([(100,100.2,99.8,100)],offset=60)
    # Earlier regular minutes are not part of the frozen ATR's 3-minute window.
    prior = [replace(bars[0],ts=trade.entered_at-i*MINUTE) for i in (3,2,1)]
    assert prepare(trade,prior+bars,Rule(name='atr',atr=ATRTrail(length=2)))[-1] is None


def test_previous_cache_loading_and_explicit_fetch_for_both_symbols(db,monkeypatch):
    trade,stock,spy = paired('2026-11-30')
    store_trade(db,trade)
    put_bars(db,trade.symbol,stock)
    put_bars(db,'SPY',spy)
    db.commit()
    calls=[]
    def fetch(conn,symbol,start,end,feed,refresh):
        calls.append((symbol,start,end,feed,refresh))
        return {'feed':feed,'bars':0}
    monkeypatch.setattr(api,'fetch',fetch)
    entry = load_entries(db,1,'sip')[0]
    assert not calls
    assert simulate(entry['trade'],entry['bars'],RS,spy_bars=entry['spy_bars'])['eligible']
    with client_for(db) as client:
        response = client.post('/api/exitlab/bars/fetch',json={
            'symbol':'FIXTURE','start':'2026-11-30T08:00:00Z','end':'2026-11-30T21:00:00Z',
            'include_warmup':True,'refresh':True})
    assert response.status_code == 200
    assert [c[0] for c in calls] == ['FIXTURE','SPY','FIXTURE','SPY']
    assert calls[2][1:3] == session('2026-11-27')
    assert calls[3][1:3] == session('2026-11-27')
    assert all(c[3:] == ('sip',True) for c in calls)


def test_nonregular_trade_exclusion_does_not_attempt_indicator_warmup():
    trade,stock,spy=paired()
    trade=replace(trade,entered_at=trade.entered_at-300*MINUTE)
    result=simulate(trade,stock,RS,spy_bars=spy)
    assert not result['eligible']
    assert 'not_completed_same_regular_session' in result['reasons']
