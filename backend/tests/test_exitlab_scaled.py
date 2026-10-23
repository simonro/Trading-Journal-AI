"""Hand-derived average-entry goldens; all prices below are synthetic."""
from dataclasses import replace
from datetime import timedelta
import json
import pytest
from test_exitlab_engine import warmup_times, fixture, ZERO
from test_exitlab_storage import db, store_trade
from test_exitlab_m5 import client_for
from exitlab.models import MINUTE
from exitlab.eligibility import from_row, reasons
from exitlab.simulate import simulate
from exitlab.excursion import excursions
from exitlab.rules import PRESETS, Rule, Tranche, Costs, ATRTrail, Indicator
from exitlab.indicators import prepare
from exitlab.bars import put_bars
from exitlab.scaling import scaling_details, BUILD_WARNING


def scaled(direction=1, scale_out=False, boundary_stop=False):
    # 25*100 + 75*102 = 10150; average 101.5, quantity 100.
    # Stop 99 -> $250 risk. 1R=104, 2R=106.5, exit 105 -> $350=1.4R.
    # Reflect about 100 for the short: average 98.5, stop 101, exit 95.
    prices = [(100,110,90,101), (102,102.2,98 if boundary_stop else 101.8,102),
              (102,104.01,102,104), (104,106.51,104,105)]
    if direction == -1:
        prices = [(200-o,200-l,200-h,200-c) for o,h,l,c in prices]
    t, bars = fixture(prices, direction)
    def price(p):
        return p if direction == 1 else 200-p
    opening, closing = ('BOT','SOLD') if direction == 1 else ('SOLD','BOT')
    prints = [('09:30:10',opening,25,price(100)), ('09:31:20',opening,75,price(102))]
    prints += ([('09:32:30',closing,40,price(104)), ('09:33:30',closing,60,price(105))]
               if scale_out else [('09:34:00',closing,100,price(105))])
    row = dict(id=1,ticker='FIXTURE',side='LONG' if direction == 1 else 'SHORT',instrument_type='STOCK',
               executions=json.dumps([dict(date='2026-10-16',time=time,action=action,qty=q,price=p)
                                      for time,action,q,p in prints]))
    return from_row(row,price(99)), bars, row


@pytest.mark.parametrize('direction',[1,-1])
def test_scaled_long_short_presets_golden(direction):
    t,bars,_ = scaled(direction)
    assert t.entry == (101.5 if direction == 1 else 98.5)
    assert t.quantity == 100 and t.risk*t.quantity == 250
    assert reasons(t,bars,simulation=True) == []
    for rule, pnl, r in zip(PRESETS,[250,500,350,350],[1,2,1.4,1.4]):
        out = simulate(t,bars,rule,ZERO)
        assert out['eligible'],out
        assert out['pnl'] == pytest.approx(pnl)
        assert out['pnl_r'] == pytest.approx(r)
        assert out['actual_pnl_modeled_costs'] == pytest.approx(350)
        assert all(f['ts'] >= t.replay_at.isoformat() for f in out['fills'])
    # The average is outside the final entry candle, but the actual fill is inside.
    assert not bars[1].low <= t.entry <= bars[1].high
    details = scaling_details(t,bars)
    assert details['fill_count'] == 2 and details['initial_risk_dollars'] == 250
    assert details['warning'] == BUILD_WARNING
    assert {x['level'] for x in details['build_touches']} == {'stop','1R','2R'}


@pytest.mark.parametrize('direction',[1,-1])
def test_build_stop_target_and_breakeven_threshold_are_not_counted(direction):
    t,bars,_ = scaled(direction)
    # The build bar hits all levels. After completion price never reaches 1R,
    # and then falls through average entry, but not through the initial stop.
    def p(v): return v if direction == 1 else 200-v
    for i in range(2,len(bars)):
        bars[i] = replace(bars[i],open=p(102 if i == 2 else 100.5),
                          high=p(103) if direction == 1 else p(100.5),
                          low=p(100.5) if direction == 1 else p(103),close=p(100.5))
    t = replace(t,exit=p(100.5),fills=t.fills[:-1]+((*t.fills[-1][:3],p(100.5)),))
    for rule in PRESETS:
        out = simulate(t,bars,rule,ZERO)
        assert out['pnl_r'] == pytest.approx(-.4)
        assert out['fills'][0]['reason'] == 'session_close'


@pytest.mark.parametrize('direction',[1,-1])
def test_final_fill_minute_counts_adverse_touch(direction):
    t,bars,_ = scaled(direction,boundary_stop=True)
    out = simulate(t,bars,PRESETS[0],ZERO)
    assert out['pnl_r'] == pytest.approx(-1)
    assert isinstance(out['pnl_r'], float)
    assert out['fills'][0]['ts'] == bars[1].ts.isoformat()


@pytest.mark.parametrize('direction',[1,-1])
def test_scale_in_scale_out_tranches_and_fees(direction):
    t,bars,_ = scaled(direction,scale_out=True)
    rule = Rule(name='Half then half',tranches=[Tranche(kind='target',fraction=.5,r=1),Tranche(kind='target',fraction=.5,r=2)])
    out = simulate(t,bars,rule,ZERO)
    # 50*2.5 + 50*5 = 375; actual 40*2.5 + 60*3.5 = 310.
    assert out['pnl'] == pytest.approx(375)
    assert out['pnl_r'] == pytest.approx(1.5)
    assert out['actual_pnl_modeled_costs'] == pytest.approx(310)
    assert [f['quantity'] for f in out['fills']] == [50,50]
    paid = simulate(t,bars,rule,Costs(slippage_bps=0,per_fill=1,per_share=.01))
    assert paid['pnl'] == pytest.approx(375)
    assert paid['actual_pnl_modeled_costs'] == pytest.approx(310)


@pytest.mark.parametrize('direction',[1,-1])
def test_excursions_ignore_build_extrema_and_use_average(direction):
    t,bars,_ = scaled(direction)
    out = excursions(t,bars)
    # Build high/low are 110/90. Only post-build high 106.51 counts for long.
    assert out['mfe'] == pytest.approx(5.01)
    assert out['mae'] == 0
    assert out['exit_efficiency'] == pytest.approx(350/5.01)
    assert out['measured_from'] == t.replay_at.isoformat()
    assert out['label'] == 'Scaled entry: average price, hold after the last fill'


@pytest.mark.parametrize('same_second',[False,True])
def test_add_after_partial_exit_and_flip_remain_excluded(same_second):
    _,_,row = scaled()
    prints = json.loads(row['executions'])
    prints.insert(1,dict(date='2026-10-16',time='09:31:20' if same_second else '09:31:00',action='SOLD',qty=10,price=102))
    prints[-1]['qty'] = 90
    row['executions'] = json.dumps(prints)
    with pytest.raises(ValueError,match='requires_one_open_and_one_close_fill_group'):
        from_row(row,99)
    prints[1]['qty'] = 50  # Crosses through flat and reverses before the next buy.
    row['executions'] = json.dumps(prints)
    with pytest.raises(ValueError,match='requires_one_open_and_one_close_fill_group'):
        from_row(row,99)


def test_timed_rules_start_at_last_fill_and_indicators_use_that_boundary():
    t,bars,_ = scaled()
    out = simulate(t,bars,Rule(name='One minute',tranches=[Tranche(kind='minutes',minutes=1,fraction=1)]),ZERO)
    # Last fill 09:31:20 + 1 min is 09:32:20; next complete open 09:33 is 104.
    assert out['pnl_r'] == pytest.approx(1)
    assert out['fills'][0]['ts'] == bars[3].ts.isoformat()
    reference = replace(t,entered_at=t.replay_at,opening_last_at=None)
    for rule in [Rule(name='ATR',atr=ATRTrail(length=2,multiple=2)),
                 Rule(name='EMA',indicator=Indicator(kind='ema9'))]:
        assert prepare(t,bars,rule) == prepare(reference,bars,rule)


def test_scaled_http_counts_actual_r_metadata_and_no_metric_writes(db):
    t,bars,row = scaled()
    store_trade(db,t)
    db.execute('UPDATE trades SET executions=?,net_pnl=350 WHERE id=1',(row['executions'],))
    put_bars(db,t.symbol,bars)
    db.commit()
    before = tuple(db.execute('SELECT mfe_pct,mae_pct,exit_efficiency FROM trades WHERE id=1').fetchone())
    with client_for(db) as client:
        replay = client.get('/api/exitlab/trades/1/simple').json()
        ready = client.get('/api/exitlab/readiness?account_id=1&feed=sip').json()
    assert ready['eligible'] == 1 and ready['excluded'] == 0
    assert replay['rows'][0]['actual_r'] == pytest.approx(1.4)
    assert replay['rows'][0]['scaled_entry']['warning'] == BUILD_WARNING
    assert replay['scaled_entry']['average_entry'] == 101.5
    assert replay['excursion']['mfe'] == pytest.approx(5.01)
    assert all(r['trades'][0]['eligible'] for r in replay['rules'])
    assert not replay['reasons']
    assert all(h['ts'] >= bars[1].ts.isoformat() for h in replay['first_hits'])
    assert tuple(db.execute('SELECT mfe_pct,mae_pct,exit_efficiency FROM trades WHERE id=1').fetchone()) == before


@pytest.mark.parametrize('direction',[1,-1])
def test_minute_aligned_final_fill_still_has_partial_boundary(direction):
    t,bars,_ = scaled(direction,boundary_stop=True)
    final = t.opening_last_at.replace(second=0)
    t = replace(t,opening_last_at=final,fills=(t.fills[0],(final,*t.fills[1][1:]),*t.fills[2:]))
    assert simulate(t,bars,PRESETS[0],ZERO)['pnl_r'] == pytest.approx(-1)
    out = excursions(t,bars)
    # Boundary low 98 (short high 102) may precede the fill: MAE [-3.5,0].
    assert out['mae'] == pytest.approx(-3.5)


def test_custom_target_touch_is_advisory_not_a_premature_fill():
    t,bars,_ = scaled()
    rule = Rule(name='3R',tranches=[Tranche(kind='target',fraction=1,r=3)])
    out = simulate(t,bars,rule,ZERO)
    assert out['pnl_r'] == pytest.approx(1.4)
    assert any(x['level'] == '3R' for x in out['scaled_entry']['build_touches'])
    assert out['fills'][0]['reason'] == 'session_close'


def test_missing_data_and_wrong_side_stop_remain_excluded():
    t,bars,_ = scaled()
    assert simulate(t,bars[1:],PRESETS[0],ZERO)['reasons'] == ['incomplete_minute_coverage_through_close']
    assert 'initial_stop_not_on_loss_side' in simulate(replace(t,stop=102),bars,PRESETS[0],ZERO)['reasons']


def test_scaled_indicator_warmup_and_signals_use_last_fill():
    t,bars,_ = scaled()
    # Add ten synthetic prior minutes, enough for EMA9 and ATR2.
    prior = [replace(bars[1],ts=ts,open=100,high=100.2,low=99.8,close=100)
             for ts in warmup_times(t, 10)]
    all_bars = prior+bars
    reference = replace(t,entered_at=t.replay_at,opening_last_at=None)
    for rule in [Rule(name='ATR',atr=ATRTrail(length=2,multiple=2)),
                 Rule(name='EMA',indicator=Indicator(kind='ema9'))]:
        actual = prepare(t,all_bars,rule)
        assert actual[-1] is None
        assert actual == prepare(reference,all_bars,rule)
        assert all(ts > t.replay_at for ts in actual[0])


def test_boundary_open_before_final_execution_does_not_become_known_mae():
    t,bars,_ = scaled(boundary_stop=True)
    final = t.opening_last_at.replace(second=0)
    t = replace(t,opening_last_at=final,fills=(t.fills[0],(final,*t.fills[1][1:]),*t.fills[2:]))
    bars[1] = replace(bars[1],open=98)
    assert excursions(t,bars)['mae'] == pytest.approx(-3.5)
    assert simulate(t,bars,PRESETS[0],ZERO)['pnl_r'] == pytest.approx(-1)
