"""Dollar cash flows at actual size, including costs and ambiguous paths."""
from dataclasses import replace
import pytest
from test_exitlab_engine import fixture, ZERO
from test_exitlab_storage import db, store_trade
from test_exitlab_m5 import client_for
from exitlab.bars import put_bars
from exitlab.rules import Costs, PRESETS, Rule, Tranche
from exitlab.simulate import simulate


@pytest.mark.parametrize('direction', [1, -1])
@pytest.mark.parametrize('ambiguous', [False, True])
def test_dollar_outcomes_at_actual_size_and_risk(direction, ambiguous):
    path = [(100, 101.23, 98.77 if ambiguous else 99.8, 100)] if direction == 1 else [
        (100, 101.23 if ambiguous else 100.2, 98.77, 100)]
    trade, bars = fixture(path, direction=direction, quantity=50)
    trade = replace(trade, stop=100 - direction * 1.22)
    result = simulate(trade, bars, PRESETS[0], ZERO)
    # 50 shares * $1.22 initial risk = $61, independent of direction.
    assert result['pnl'] == pytest.approx(-61 if ambiguous else 61)
    assert result['pnl'] == pytest.approx(result['pnl_r'] * 61)


@pytest.mark.parametrize('direction', [1, -1])
@pytest.mark.parametrize('qty,quantities,gross', [(7, [3, 4], 11), (7.5, [3.75, 3.75], 11.25)])
@pytest.mark.parametrize('with_costs', [False, True])
def test_scale_out_dollars_use_real_tranches(direction, qty, quantities, gross, with_costs):
    path = [(100, 102.02, 99.5, 102)] if direction == 1 else [(100, 100.5, 97.98, 98)]
    trade, bars = fixture(path, direction=direction, quantity=qty)
    rule = Rule(name='Half at 1R, half at 2R', tranches=(
        Tranche(kind='target', r=1, fraction=.5), Tranche(kind='target', r=2, fraction=.5)))
    costs = Costs(per_fill=.25, per_share=.01, slippage_bps=5) if with_costs else ZERO
    result = simulate(trade, bars, rule, costs)
    # Legacy costs are ignored for both whole and fractional share tranches.
    expected = gross
    assert result['pnl'] == pytest.approx(expected)
    assert result['pnl'] == pytest.approx(result['pnl_r'] * qty)
    for key in ('fills', 'fills'):
        assert [f['quantity'] for f in result[key]] == pytest.approx(quantities)


@pytest.mark.parametrize('direction', [1, -1])
def test_legacy_costs_are_ignored_on_stop_first_dollar_outcome(direction):
    path = [(100, 101.23, 98.77, 100)]
    trade, bars = fixture(path, direction=direction, quantity=50)
    trade = replace(trade, stop=100 - direction * 1.22)
    result = simulate(trade, bars, PRESETS[0], Costs(per_share=.01, slippage_bps=5))
    # The stop-first outcome retains zero costs and zero slippage.
    expected_stop = -61
    assert result['pnl'] == pytest.approx(expected_stop)
    assert result['pnl'] == pytest.approx(result['pnl_r'] * 61)


def test_simple_api_keeps_recorded_net_pnl_and_returns_modeled_dollars(db):
    trade, bars = fixture([(100, 100.5, 99.5, 100.5)], quantity=50)
    store_trade(db, trade)
    put_bars(db, trade.symbol, bars)
    db.execute('UPDATE trades SET gross_pnl=25, commissions=1.23, net_pnl=23.77 WHERE id=1')
    db.commit()
    with client_for(db) as client:
        for costs, expected in [({'slippage_bps': 0, 'per_share': 0}, 25),
                                ({'slippage_bps': 5, 'per_share': .01}, 25)]:
            assert client.put('/api/exitlab/settings', json=costs).status_code == 200
            for response in [client.get('/api/exitlab/trades/1/simple'),
                             client.post('/api/exitlab/history', json={'account_id': 1})]:
                assert response.status_code == 200, response.text
                result = response.json()
                assert result['rows'][0]['journal_trade']['net_pnl'] == 23.77
                hold = result['rules'][2]['trades'][0]
                assert hold['pnl'] == pytest.approx(expected)
                assert hold['pnl'] == pytest.approx(hold['pnl_r'] * 50)
