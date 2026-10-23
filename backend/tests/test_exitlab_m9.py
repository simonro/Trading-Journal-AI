"""Setup assignment snapshots, prior-minute VWAP and hand-derived 5-minute RRS."""
from dataclasses import replace
import json
import pytest
from test_exitlab_storage import db, store_trade
from test_exitlab_m5 import client_for, seed
from test_exitlab_engine import fixture
from exitlab.models import Bar, Trade, MINUTE
from exitlab.calendar import session, previous_session
from exitlab.rules import Rule, Tranche, Indicator
from exitlab.indicators import prepare, rs_rw_exits, rrs_value
from exitlab.simulate import simulate
from exitlab.schema import migrate
from exitlab import simple


def test_assignments_preserve_hash_and_presets(db):
    seed(db, [(100,100.5,99.5,100)])
    db.execute("UPDATE trades SET setup='LOD Reversal' WHERE id=1")
    db.commit()
    with client_for(db) as c:
        before = dict(db.execute('SELECT * FROM exit_rules WHERE id=1').fetchone())
        first = c.post('/api/exitlab/history', json={'account_id': 1}).json()
        assert c.get('/api/exitlab/setups?account_id=1').json() == [{'group':'strategy','label':'Strategies','items':[]}, {'group':'auto','label':'Auto setups','items':[{'name':'LOD Reversal','trades':1,'eligible':1}]}]
        for rid in (1,2,3):
            assert c.patch(f'/api/exitlab/rules/{rid}', json={'applies_to_setups': [{'group':'strategy','name':'Opening Drive'}]}).status_code == 200
        replay = c.get('/api/exitlab/trades/1/simple').json()
        assert [r['rule_id'] for r in replay['rules']] == [4]
        assert replay['rows'][0]['actual_r'] == 0
        history = c.post('/api/exitlab/history', json={'account_id': 1}).json()
        assert [r['eligible'] for r in history['rules']] == [0,0,0,1]
        after = dict(db.execute('SELECT * FROM exit_rules WHERE id=1').fetchone())
        assert {k:v for k,v in before.items() if k != 'applies_to_setups'} == {k:v for k,v in after.items() if k != 'applies_to_setups'}
        assert c.patch('/api/exitlab/rules/1', json={'applies_to_setups': None}).status_code == 200
        assert c.patch('/api/exitlab/rules/1', json={'applies_to_setups': []}).status_code == 422
        assert c.patch('/api/exitlab/rules/1', json={'applies_to_setups': ['NONE']}).status_code == 422
        migrate(db)
        assert c.get('/api/exitlab/rules').json()[1]['applies_to_setups'] == [{'group':'strategy','name':'Opening Drive'}]


def test_strategy_fallback_no_setup_and_duplicate_policy_assignments(db):
    seed(db, [(100,100.5,99.5,100)])
    group = db.execute('SELECT trade_group FROM trades WHERE id=1').fetchone()[0]
    db.execute("UPDATE trade_analysis SET strategy='Trend Pullback' WHERE trade_group=?", (group,))
    db.commit()
    with client_for(db) as c:
        assert c.get('/api/exitlab/setups?account_id=1').json() == [{'group':'strategy','label':'Strategies','items':[{'name':'Trend Pullback','trades':1,'eligible':1}]}, {'group':'auto','label':'Auto setups','items':[]}]
        a = c.post('/api/exitlab/rules', json={'name':'A', 'applies_to_setups':[{'group':'strategy','name':'Trend Pullback'}]}).json()
        b = c.post('/api/exitlab/rules', json={'name':'B', 'applies_to_setups':[{'group':'strategy','name':'Opening Drive'}]}).json()
        assert a['hash'] == b['hash']
        assert a['id'] in [r['rule_id'] for r in c.get('/api/exitlab/trades/1/simple').json()['rules']]
        db.execute("UPDATE trades SET setup='NONE'")
        db.commit()
        replay = c.get('/api/exitlab/trades/1/simple').json()
        assert replay['rows'][0]['setup_name'] == 'Trend Pullback'
        assert len(replay['rules']) == 5
        assert c.get('/api/exitlab/setups?account_id=1').json()[1]['items'] == [{'name':'NONE','trades':1,'eligible':1}]


@pytest.mark.parametrize('direction', [1,-1])
@pytest.mark.parametrize('confirmation', [1,2])
def test_golden_rrs_flip_and_next_minute_open(direction, confirmation):
    trade, stock, spy = rs_fixture(direction)
    # Constant 5m TR=8. SPY is flat, so RRS = stock 12-bar move / 8.
    # At 09:30: +1/8 long (-1/8 short). At 09:35: -1/8 (+1/8).
    # At 09:40: -2/8 (+2/8). Default exits 09:35; N=2 exits 09:40.
    exits, values, error = rs_rw_exits(trade, stock, spy, confirmation)
    assert error is None
    assert values[(stock[64].ts+MINUTE).isoformat()] == pytest.approx(direction/8)
    assert values[(trade.entered_at+5*MINUTE).isoformat()] == pytest.approx(-direction/8)
    assert values[(trade.entered_at+10*MINUTE).isoformat()] == pytest.approx(-direction/4)
    rule = Rule(name='RS flip', indicator=Indicator(kind='rs_rw', timeframe=5, confirmation=confirmation))
    result = simulate(trade, stock, rule, spy_bars=spy)
    assert result['eligible'], result
    fill = result['fills'][0]
    assert fill['ts'] == (trade.entered_at+5*confirmation*MINUTE).isoformat()
    assert fill['price'] == 100
    assert fill['reason'] == 'rs_rw' and fill['timing'] == 'open'
    assert result['pnl'] == 0


def rs_fixture(direction=1):
    opening, closing = session('2026-10-16')
    _, prior_close = previous_session('2026-10-16')
    times = [prior_close-i*MINUTE for i in range(65,0,-1)] + [opening+i*MINUTE for i in range(int((closing-opening)/MINUTE))]
    stock, spy = [], []
    for i, ts in enumerate(times):
        group = i//5
        change = {12:1,13:-1,14:-2}.get(group,0)*direction
        stock.append(Bar(ts,100,104,96,100+change,100,vwap=100))
        spy.append(Bar(ts,100,104,96,100,100,vwap=100))
    trade = Trade(1,'FIXTURE',direction,10,100,100,opening,closing,90 if direction==1 else 110)
    return trade, stock, spy


def test_rrs_missing_spy_fails_even_after_signal_and_no_one_minute_option():
    trade, stock, spy = rs_fixture()
    rule = Rule(name='flip', indicator=Indicator(kind='rs_rw', timeframe=5))
    result = simulate(trade, stock, rule, spy_bars=spy[:-1])
    assert result['reasons'] == ['regular_minute_missing|SPY|2026-10-16T15:59:00-04:00']
    assert simulate(trade, stock, rule, spy_bars=spy[1:])['reasons'] == ['regular_minute_missing|SPY|2026-10-15T14:55:00-04:00']
    with pytest.raises(ValueError): Indicator(kind='rs_rw',timeframe=1)
    with pytest.raises(ValueError): Indicator(kind='rs_rw',timeframe=5,confirmation=0)
    assert simulate(trade, stock[5:], rule, spy_bars=spy)['reasons'] == ['regular_minute_missing|FIXTURE|2026-10-15T14:55:00-04:00']


def test_rrs_confirmation_resets_and_requires_a_favorable_state():
    trade, stock, spy = rs_fixture()
    # 09:35-09:40 closes neutral, breaking N=2 adverse confirmation.
    changed = [replace(b,close=100) if trade.entered_at+5*MINUTE <= b.ts < trade.entered_at+10*MINUTE else b for b in stock]
    exits, _, error = rs_rw_exits(trade,changed,spy,2)
    assert not error and not exits
    # No favorable pre-entry state, and permanently flat afterwards: no flip.
    flat = [replace(b,close=100) for b in stock]
    assert rs_rw_exits(trade,flat,spy,1)[0] == {}


@pytest.mark.parametrize('direction', [1,-1])
@pytest.mark.parametrize('fraction', [1,.5])
def test_vwap_touch_prior_minute_fill_no_improvement_and_tranche(direction,fraction):
    trade, bars = vwap_fixture(direction)
    tranches = (Tranche(kind='vwap_touch',fraction=fraction),)
    if fraction < 1: tranches += (Tranche(kind='close',fraction=1-fraction),)
    rule = Rule(name='VWAP',tranches=tranches)
    result = simulate(trade,bars,rule)
    assert result['eligible'], result
    fill = result['fills'][0]
    assert fill['price'] == 100+direction
    assert fill['quantity'] == 100*fraction
    assert fill['reason'] == 'vwap_touch'
    assert sum(f['quantity'] for f in result['fills']) == 100
    assert result['indicator_values']['vwap_targets'][trade.entered_at.isoformat()] == 100+direction


def vwap_fixture(direction=1):
    trade,bars = fixture([(100,102,99.5,100)],offset=30)
    if direction == -1:
        trade=replace(trade,direction=-1,stop=101)
        bars=[replace(b,open=200-b.open,high=200-b.low,low=200-b.high,close=200-b.close,vwap=200-b.vwap) for b in bars]
    prior=[Bar(trade.entered_at-i*MINUTE,100+direction,100+direction,100+direction,100+direction,100,vwap=100+direction) for i in range(30,0,-1)]
    return trade,prior+bars


@pytest.mark.parametrize('direction', [1,-1])
def test_vwap_one_tick_and_same_bar_stop_first(direction):
    trade,bars=vwap_fixture(direction)
    rule=Rule(name='VWAP',tranches=(Tranche(kind='vwap_touch',fraction=1),))
    index=30
    # Touch alone at entry does not fill. Subsequent gap executes at lagged VWAP.
    bars[index]=replace(bars[index],high=101 if direction==1 else 100.5,low=99.5 if direction==1 else 99,vwap=100)
    assert simulate(trade,bars,rule)['fills'][0]['reason'] == 'session_close'
    bars[index]=replace(bars[index],high=101.01 if direction==1 else 101.1,low=98.9 if direction==1 else 98.99)
    result=simulate(trade,bars,rule)
    assert result['pnl_r'] == pytest.approx(-1)


def test_vwap_wrong_entry_side_and_missing_session_vwap():
    trade,bars=vwap_fixture()
    rule=Rule(name='VWAP',tranches=(Tranche(kind='vwap_touch',fraction=1),))
    wrong=[replace(b,vwap=99) for b in bars]
    assert simulate(trade,wrong,rule)['reasons'] == ['vwap_touch_wrong_side_at_entry']
    missing=list(bars); missing[0]=replace(missing[0],vwap=None)
    assert simulate(trade,missing,rule)['reasons'] == ['vwap_bar_values_missing']


def test_existing_indicator_hash_does_not_change():
    from exitlab.models import digest
    rule=Rule(name='old',indicator=Indicator(kind='ema9',timeframe=1))
    old=rule.model_dump(exclude={'name'})
    old['indicator'].pop('confirmation')
    assert rule.rule_hash == digest(old)


def test_rrs_benchmark_normalization_and_wilder_first_bar_seed():
    # Twelve-bar moves: stock +4, SPY +1. Their constant TRs are 4 and 8.
    # Expected stock move = (1/8)*4 = .5, so RRS = (4-.5)/4 = .875.
    stock=[(i,104,100,100 if i==0 else 104) for i in range(13)]
    spy=[(i,108,100,100 if i==0 else 101) for i in range(13)]
    assert rrs_value(stock,spy) == pytest.approx(.875)
    # Double the last stock TR to 8: EWM ATR = 4+(8-4)/12 = 13/3.
    stock[-1]=(12,108,100,104)
    assert rrs_value(stock,spy) == pytest.approx(4/(13/3)-1/8)


def test_bar_fetch_fetches_benchmark_in_same_cache_api(db,monkeypatch):
    from exitlab import api
    calls=[]
    def fetch(conn,symbol,start,end,feed,refresh):
        calls.append((symbol,feed))
        return {'feed':feed,'bars':5,'cached':False}
    monkeypatch.setattr(api,'fetch',fetch)
    with client_for(db) as c:
        r=c.post('/api/exitlab/bars/fetch',json={'symbol':'QQQ','start':'2026-10-16T13:30:00Z','end':'2026-10-16T13:35:00Z'})
        assert r.status_code==200
        assert calls==[('QQQ','sip'),('SPY','sip')]
        assert r.json()['benchmark']['bars']==5


def test_vwap_gap_fills_lagged_target_without_price_improvement():
    trade,bars=vwap_fixture()
    bars[30]=replace(bars[30],high=100.5,close=100,vwap=100)
    bars[31]=replace(bars[31],open=103,high=103,low=100,close=100)
    rule=Rule(name='VWAP',tranches=(Tranche(kind='vwap_touch',fraction=1),))
    result=simulate(trade,bars,rule)
    assert result['eligible']
    fill=result['fills'][0]
    assert fill['timing']=='open'
    assert fill['price']==pytest.approx((30*101+100)/31)
    assert fill['price'] < 103
