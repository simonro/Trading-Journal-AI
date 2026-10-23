"""Synthetic fixtures. Expected cash flows are hand-derived in each assertion."""
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from exitlab.models import Bar, Trade, UTC, MINUTE
from exitlab.calendar import session, previous_session
from exitlab.models import NY
from exitlab.rules import Rule, Tranche, RStep, ATRTrail, Indicator, Costs, PRESETS
from exitlab.simulate import simulate
from exitlab.excursion import excursions
from exitlab.indicators import prepare
from exitlab.eligibility import from_row, reasons

ZERO = Costs(slippage_bps=0)


def fixture(path, direction=1, quantity=100, day="2026-10-16", offset=0):
    opening, closing = session(day)
    start = opening+offset*MINUTE
    bars = []
    for i in range(int((closing-start)/MINUTE)):
        o, h, l, c = path[i] if i < len(path) else (path[-1][-1],)*4
        bars.append(Bar(start+i*MINUTE, o, h, l, c, 100, vwap=(o+h+l+c)/4))
    trade = Trade(1, "FIXTURE", direction, quantity, 100, path[-1][-1], start,
                  min(start+len(path)*MINUTE, closing), 99 if direction == 1 else 101)
    return trade, bars


@pytest.mark.parametrize("direction,path,expected", [
    (1, [(100, 101.01, 99.5, 101)], 100),
    (-1, [(100, 100.5, 98.99, 99)], 100),
])
def test_target_one_tick_penetration(direction, path, expected):
    t, bars = fixture(path, direction)
    r = simulate(t, bars, PRESETS[0], ZERO)
    assert r["pnl"] == pytest.approx(expected)
    assert r["fills"][0]["reason"] == "target"


@pytest.mark.parametrize("direction,path", [
    (1, [(100, 101, 99.5, 100.5)]),
    (-1, [(100, 100.5, 99, 99.5)]),
])
def test_touch_is_not_limit_fill(direction, path):
    t, bars = fixture(path, direction)
    r = simulate(t, bars, PRESETS[0], ZERO)
    assert r["pnl"] == pytest.approx(50)
    assert r["fills"][0]["reason"] == "session_close"


@pytest.mark.parametrize("direction,path,price", [
    (1, [(100,100.2,99.5,100), (98,98.5,97.5,98)], 98),
    (-1, [(100,100.5,99.8,100), (102,102.5,101.5,102)], 102),
])
def test_gap_stop_uses_worse_open(direction, path, price):
    t, bars = fixture(path, direction)
    r = simulate(t, bars, PRESETS[2], ZERO)
    assert r["pnl"] == pytest.approx(-200)
    assert r["fills"][0]["price"] == price


@pytest.mark.parametrize("direction,path", [
    (1, [(100,101.01,98.9,100)]), (-1, [(100,101.1,98.99,100)]),
])
def test_stop_first_collision(direction, path):
    t, bars = fixture(path, direction)
    r = simulate(t, bars, PRESETS[0], ZERO)
    assert r["pnl_r"] == pytest.approx(-1)


def test_breakeven_activation_then_reversal():
    t, bars = fixture([(100,101.2,98.8,100.5)])
    r = simulate(t, bars, PRESETS[3], ZERO)
    assert r["pnl_r"] == pytest.approx(-1)


def test_first_activated_step_stop_precedes_later_step():
    t, bars = fixture([(100,102.2,99.5,102)])
    rule = Rule(name="two steps", r_steps=(RStep(at_r=1, stop_r=0), RStep(at_r=2, stop_r=1)))
    r = simulate(t, bars, rule, ZERO)
    # The first tightened stop fills at 0R before the later 2R threshold.
    assert r["pnl_r"] == pytest.approx(0)


def test_three_tranches_ignore_legacy_fees_and_remaining_quantity_reconcile():
    t, bars = fixture([(100,103.02,99.5,103)], quantity=10)
    rule = Rule(name="thirds", tranches=(Tranche(kind="target", r=1, fraction=.2),
                                         Tranche(kind="target", r=2, fraction=.3),
                                         Tranche(kind="target", r=3, fraction=.5)))
    r = simulate(t, bars, rule, Costs(slippage_bps=0, per_fill=1, per_share=.1))
    # Gross 2*1 + 3*2 + 5*3 = 23. Legacy fee arguments are ignored.
    assert r["pnl"] == pytest.approx(23)
    for ledger in (r["fills"], r["fills"]):
        assert sum(f["quantity"] for f in ledger) == pytest.approx(10)
        assert sum(f["fee"] for f in ledger) + r["initial_fee"] == pytest.approx(0)


def test_competing_exits_never_overfill():
    t, bars = fixture([(100,101.02,98.8,100)], quantity=7)
    rule = Rule(name="half and close", tranches=(Tranche(kind="target", r=1, fraction=.5), Tranche(kind="close", fraction=.5)))
    r = simulate(t, bars, rule, ZERO)
    assert r["pnl"] == pytest.approx(-7)
    assert all(sum(f["quantity"] for f in r[key]) == pytest.approx(7) for key in ("fills", "fills"))


def test_legacy_slippage_is_ignored_for_market_exits():
    t, bars = fixture([(100,100.2,99.5,100)])
    r = simulate(t, bars, PRESETS[2], Costs(slippage_bps=1))
    assert r["pnl"] == pytest.approx(0)
    assert r["cost_label"] == "Zero modeled costs and slippage"


@pytest.mark.parametrize("kind,value", [("minutes", {"minutes": 1}), ("clock", {"clock": "09:31"})])
def test_time_exits_next_available_open(kind, value):
    t, bars = fixture([(100,100.5,99.5,100), (100.3,100.5,100.1,100.4)])
    rule = Rule(name="timed", tranches=(Tranche(kind=kind, fraction=1, **value),))
    r = simulate(t, bars, rule, ZERO)
    assert r["pnl"] == pytest.approx(30)
    assert r["fills"][0]["timing"] == "open"


def test_missing_data_is_null_and_never_zero():
    t, bars = fixture([(100,100.5,99.5,100.2)])
    bars.pop(15)
    assert excursions(t, bars)["mfe"] is None
    assert simulate(t, bars, PRESETS[0])["pnl_r"] is None


def test_actual_hold_only_and_conservative_partial_minute():
    t, bars = fixture([(100,102,98,101), (101,105,97,103), (103,110,90,100)])
    t = replace(t, entered_at=t.entered_at+timedelta(seconds=30),
                exited_at=t.entered_at+MINUTE+timedelta(seconds=30), exit=103)
    r = excursions(t, bars)
    assert r["mfe"] == 3
    assert r["mae"] == -3
    assert r["exit_efficiency"] == pytest.approx(60)
    assert simulate(t, bars, PRESETS[0])["eligible"]


def test_short_excursions_negative_efficiency():
    t, bars = fixture([(100,102,98,101)], direction=-1)
    r = excursions(t, bars)
    assert r["mfe"] == 2
    assert r["mae"] == -2
    assert r["exit_efficiency"] == -50


def test_zero_mfe_has_no_efficiency():
    t, bars = fixture([(100,100,99.5,99.8)])
    assert excursions(t, bars)["exit_efficiency"] is None


def test_warmup_insufficiency():
    t, bars = fixture([(100,100.1,99.5,99.8)])
    for kind in ("ema9", "ema20", "ema_cross"):
        r = simulate(t, bars, Rule(name=kind, indicator=Indicator(kind=kind)))
        assert r["reasons"][0].startswith("regular_minute_missing|FIXTURE|2026-10-15")
    assert simulate(t, bars, Rule(name="atr", atr=ATRTrail()))["reasons"][0].startswith("regular_minute_missing|FIXTURE|2026-10-15")


def warmup_times(trade, count):
    day = trade.entered_at.astimezone(NY).date().isoformat()
    opening, _ = session(day)
    _, prior_close = previous_session(day)
    entry = trade.entered_at.replace(second=0, microsecond=0)
    current = int((entry-opening)/MINUTE)
    return [prior_close-(count-current-i)*MINUTE if i < count-current else
            opening+(i-(count-current))*MINUTE for i in range(count)]


def with_warmup(trade, bars, count=100):
    return [Bar(ts,100,100.1,99.9,100,100,vwap=100) for ts in warmup_times(trade, count)] + bars


@pytest.mark.parametrize("kind", ["ema9", "ema20", "ema_cross", "vwap"])
@pytest.mark.parametrize("timeframe", [1,5])
def test_indicator_next_open_not_current_close(kind, timeframe):
    path = [(100,100.1,99.7,99.8)] + [(99.8,99.9,99.7,99.8)]*(timeframe-1)
    path += [(99.6,99.7,99.5,99.6)]
    t, bars = fixture(path)
    bars = with_warmup(t,bars)
    # Provider per-minute volume weighted price is 100, separate from last price.
    bars = [replace(b, vwap=100) for b in bars]
    r = simulate(t, bars, Rule(name=kind, indicator=Indicator(kind=kind,timeframe=timeframe)), ZERO)
    assert r["eligible"], r
    assert r["pnl"] == pytest.approx(-40)
    assert r["fills"][0]["ts"] == (t.entered_at+timeframe*MINUTE).isoformat()


def test_atr_is_frozen_pretrade_and_close_change_acts_next_open():
    t, bars = fixture([(100,102,99.5,101.5), (101,101.2,100.9,101)])
    # Pretrade TR=0.2, ATR multiple 2 -> next stop at 101.1. Next open gaps to 101.
    bars = with_warmup(t,bars)
    rule = Rule(name="atr", atr=ATRTrail(length=2,multiple=2))
    r = simulate(t,bars,rule,ZERO)
    assert r["pnl"] == pytest.approx(100)
    assert r["indicator_values"]["atr_at_open"] == pytest.approx(.2)
    assert r["fills"][0]["ts"] == (t.entered_at+MINUTE).isoformat()
    changed = [replace(b, high=b.high+5) if b.ts > t.entered_at+MINUTE else b for b in bars]
    assert simulate(t,changed,rule,ZERO)["pnl"] == r["pnl"]


def test_half_day_and_holiday_calendar():
    t,bars = fixture([(100,100.2,99.5,100.1)],day="2026-11-27")
    assert len(bars) == 210
    assert simulate(t,bars,PRESETS[2],ZERO)["fills"][0]["ts"].endswith("18:00:00+00:00")
    with pytest.raises(ValueError, match="not_a_regular_session"):
        session("2026-12-25")


def test_rule_validation_and_semantic_hash():
    with pytest.raises(ValueError):
        Rule(name="invalid", tranches=(Tranche(kind="target",r=1,fraction=.5),))
    with pytest.raises(ValueError):
        Rule(name="invalid", r_steps=(RStep(at_r=1,stop_r=0),RStep(at_r=2,stop_r=-.5)))
    with pytest.raises(ValueError):
        Rule(name="invalid", chandelier={})
    assert PRESETS[0].model_copy(update={"name":"renamed"}).rule_hash == PRESETS[0].rule_hash


def test_bar_validation_and_mixed_feed():
    t,bars = fixture([(100,100.2,99.5,100)])
    with pytest.raises(ValueError):
        replace(bars[0], low=101)
    bars[1] = replace(bars[1],feed="iex")
    assert "mixed_feeds" in reasons(t,bars)


@pytest.mark.parametrize("kind", ["ema9", "ema20", "ema_cross", "vwap"])
def test_five_minute_candle_containing_open_is_available_at_its_close(kind):
    t,bars=fixture([(100,100.1,99.7,99.8)] + [(99.8,99.9,99.7,99.8)]*2 + [(99.6,99.7,99.5,99.6)],offset=2)
    bars=with_warmup(t,bars,110)
    bars=[replace(b,vwap=100) for b in bars]
    r=simulate(t,bars,Rule(name=kind,indicator=Indicator(kind=kind,timeframe=5)),ZERO)
    assert r['eligible'],r
    assert r['fills'][0]['ts']==(t.entered_at+3*MINUTE).isoformat()


@pytest.mark.parametrize("kind", ["ema9", "ema20", "ema_cross", "vwap"])
def test_short_indicator_is_mirrored(kind):
    t,bars=fixture([(100,100.3,99.9,100.2),(100.4,100.5,100.3,100.4)],direction=-1)
    bars=[replace(b,vwap=100) for b in with_warmup(t,bars)]
    r=simulate(t,bars,Rule(name=kind,indicator=Indicator(kind=kind)),ZERO)
    assert r['pnl']==pytest.approx(-40)


def test_short_breakeven_and_atr():
    t,bars=fixture([(100,101.2,98.8,99.5)],direction=-1)
    assert simulate(t,bars,PRESETS[3],ZERO)['pnl_r']==pytest.approx(-1)
    t,bars=fixture([(100,100.5,98,98.5),(99,99.1,98.8,99)],direction=-1)
    r=simulate(t,with_warmup(t,bars),Rule(name='atr',atr=ATRTrail(length=2,multiple=2)),ZERO)
    assert r['pnl']==pytest.approx(100)


def test_warmup_gap_and_missing_vwap_are_explicit():
    t,bars=fixture([(100,100.1,99.5,99.8)])
    warmed=with_warmup(t,bars,21)
    warmed.pop(15)
    r=simulate(t,warmed,Rule(name='ema20',indicator=Indicator(kind='ema20')),ZERO)
    assert r['reasons']==['regular_minute_missing|FIXTURE|2026-10-15T15:54:00-04:00']
    bars[0]=replace(bars[0],vwap=None)
    r=simulate(t,bars,Rule(name='vwap',indicator=Indicator(kind='vwap')),ZERO)
    assert r['reasons']==['vwap_bar_values_missing']


def test_gap_over_target_gets_no_improvement():
    t,bars=fixture([(100,100.5,99.5,100),(102,102.5,101.5,102)])
    r=simulate(t,bars,PRESETS[0],ZERO)
    assert r['pnl_r']==pytest.approx(1)
    assert r['fills'][0]['price']==101


def test_stop_cannot_loosen_after_a_close_reversal():
    t,bars=fixture([(100,101.5,99.5,101.5),(101.4,101.5,101.35,101.4),(101.2,101.25,101,101.1)])
    rule=Rule(name='tight',atr=ATRTrail(length=2,multiple=1))
    r=simulate(t,with_warmup(t,bars),rule,ZERO)
    assert r['pnl']==pytest.approx(120)


def test_fill_groups_and_structural_exclusions():
    import json
    row={'id':1,'ticker':'FIXTURE','instrument_type':'STOCK','side':'LONG','source':'imported',
         'executions':json.dumps([
            {'date':'2026-10-16','time':'09:30:00','action':'BOT','qty':2,'price':100},
            {'date':'2026-10-16','time':'09:30:00','action':'BOT','qty':3,'price':100},
            {'date':'2026-10-16','time':'09:31:00','action':'SOLD','qty':5,'price':101}])}
    assert from_row(row).quantity==5
    row['instrument_type']='OPTION'
    with pytest.raises(ValueError,match='not_stock'):
        from_row(row)
