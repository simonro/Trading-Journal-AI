"""Leak Finder rules on synthetic trades.

    cd backend && python -m pytest tests/test_leaks.py -q
"""
import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import leaks  # noqa: E402

_next_id = [0]


def trade(date, t_in, t_out, pnl, side='LONG', ticker='AAA', qty=100, price=10.0,
          inst='STOCK', account=1, **extra):
    _next_id[0] += 1
    ea, xa = ('BOT', 'SOLD') if side == 'LONG' else ('SOLD', 'BOT')
    r = {
        'id': _next_id[0], 'account_id': account, 'date': date, 'ticker': ticker, 'side': side,
        'instrument_type': inst, 'net_pnl': pnl, 'gross_pnl': pnl + 1, 'commissions': 1.0,
        'executions': json.dumps([
            {'time': t_in, 'action': ea, 'qty': qty, 'price': price},
            {'time': t_out, 'action': xa, 'qty': qty, 'price': price},
        ]),
        'tags': [],
    }
    r.update(extra)
    return r


def test_sequence_features_only_count_trades_closed_before_entry():
    rows = leaks.derive([
        trade('2026-10-01', '09:35:00', '09:40:00', -50),
        trade('2026-10-01', '09:38:00', '10:30:00', 80),   # entered while the first was open
        trade('2026-10-01', '09:43:00', '09:50:00', -20),  # 3 min after the loss closed
        trade('2026-10-01', '10:00:00', '10:10:00', 10),   # after 2 losses, day red
        trade('2026-10-01', '11:00:00', '11:05:00', 5),    # after the +80 closed: day green, at high
    ])
    a, b, c, d, e = rows
    assert a['trade_no'] == 1 and a['loss_streak'] == 0 and a['since_loss'] is None
    assert b['loss_streak'] == 0, 'the first trade was still open when this one started'
    assert c['since_loss'] == 3 and leaks._reentry(c) == 'Within 5 min of a loss'
    assert d['loss_streak'] == 2 and leaks._day_state(d) == 'Day already red'
    assert e['day_run'] == pytest.approx(20) and leaks._day_state(e) == 'Flat or at the day high'


def test_giving_back_a_green_day():
    rows = leaks.derive([
        trade('2026-10-02', '09:35:00', '09:40:00', 100),
        trade('2026-10-02', '09:45:00', '09:50:00', -40),
        trade('2026-10-02', '10:00:00', '10:05:00', 5),
    ])
    assert leaks._day_state(rows[2]) == 'Giving back a green day'


def test_size_against_the_ticker_median():
    rows = [trade('2026-10-03', f'10:0{i}:00', f'10:0{i}:30', 1, qty=100) for i in range(6)]
    rows.append(trade('2026-10-03', '11:00:00', '11:05:00', 1, qty=200))
    rows.append(trade('2026-10-03', '12:00:00', '12:05:00', 1, qty=50))
    leaks.derive(rows)
    assert leaks._size(rows[0]) is None, 'no earlier trades to compare with'
    assert leaks._size(rows[5]) == 'Your usual size'
    assert leaks._size(rows[6]) == '1.5x+ your usual size'
    assert leaks._size(rows[7]) == 'Under 2/3 of your usual size'


def _sessions(n_days, morning_pnl, midday_pnl, start=1):
    rows = []
    for d in range(start, start + n_days):
        date = f'2026-09-{d:02d}'
        wobble = (d % 3) - 1  # -1, 0, +1 so the buckets have some spread
        rows.append(trade(date, '09:50:00', '09:55:00', morning_pnl + wobble))
        rows.append(trade(date, '12:00:00', '12:05:00', midday_pnl + wobble))
    return rows


def test_minimum_sample_and_leak_cost():
    few = leaks.derive(_sessions(10, 50, -30))
    assert leaks.find_leaks(few)['leaks'] == [], 'fewer than 20 trades per bucket never counts'

    rows = leaks.derive(_sessions(25, 50, -30))
    res = leaks.find_leaks(rows)
    top = res['leaks'][0]
    assert top['id'] == 'entry_window:11:00-13:30'
    assert top['trades'] == 25 and top['net_pnl'] == -750
    assert top['dedup_cost'] == 750 and res['total_cost'] >= 750
    assert top['confidence'] == 'solid'
    assert 'lost $750' in top['sentence']


def test_greedy_dedup_never_counts_a_trade_twice():
    rows = leaks.derive(_sessions(25, 50, -30))
    res = leaks.find_leaks(rows)
    seen = set()
    total = 0
    for lk in res['leaks']:
        total += lk['dedup_cost']
    # the de-duplicated total can never exceed what every losing bucket lost together
    assert total <= sum(-r['net_pnl'] for r in rows if r['net_pnl'] < 0)
    assert res['total_cost'] == pytest.approx(total)
    for lk in res['leaks'][1:]:
        assert lk['overlap_trades'] <= lk['trades']
        seen |= set(lk['trade_ids'])


def test_edges_are_positive_and_one_per_dimension():
    rows = leaks.derive(_sessions(25, 50, -30))
    res = leaks.find_leaks(rows)
    assert res['edges'], 'the morning bucket is a clear edge'
    assert all(e['net_pnl'] > 0 and e['t'] >= 1 for e in res['edges'])
    dims = [e['dimension'] for e in res['edges']]
    assert len(dims) == len(set(dims))


def test_new_leak_in_the_last_30_days():
    # August: midday made money. Late September: midday loses.
    old = []
    for d in range(1, 29):
        date = f'2026-08-{d:02d}'
        old.append(trade(date, '09:50:00', '09:55:00', 10))
        old.append(trade(date, '12:00:00', '12:05:00', 40))
    late = _sessions(12, 10, -30, start=15)
    res = leaks.find_leaks(leaks.derive(old + late))
    ids = [f['id'] for f in res['new']]
    assert 'entry_window:11:00-13:30' in ids
    assert res['new'][0]['recent']['net_pnl'] < 0


def test_cache_key_is_stable_and_moves_with_the_numbers():
    a = leaks.find_leaks(leaks.derive(_sessions(25, 50, -30)))
    b = leaks.find_leaks(leaks.derive(_sessions(25, 50, -30)))
    c = leaks.find_leaks(leaks.derive(_sessions(25, 50, -31)))
    assert leaks.cache_key(a) == leaks.cache_key(b)
    assert leaks.cache_key(a) != leaks.cache_key(c)


def test_explain_payload_has_no_trade_ids():
    res = leaks.find_leaks(leaks.derive(_sessions(25, 50, -30)))
    for f in leaks.explain_payload(res):
        assert 'trade_ids' not in f


# ── review findings, 2026-10-31 ────────────────────────────────────────────

def test_a_leak_is_never_shown_in_profit():
    """Greedy picking runs on what is left, but a shown leak must lose over the whole range too."""
    import random
    for seed in range(40):
        rnd = random.Random(seed)
        rows = []
        for d in range(1, 29):
            for k in range(rnd.randint(2, 6)):
                h = rnd.choice([9, 10, 11, 12, 14])
                r = trade(f'2026-09-{d:02d}', f'{h:02d}:{rnd.randint(0, 50):02d}:00',
                          f'{h:02d}:{rnd.randint(51, 59):02d}:00', rnd.gauss(0, 100),
                          side=rnd.choice(['LONG', 'SHORT']), inst=rnd.choice(['STOCK', 'FUTURE']))
                if rnd.random() < 0.5:
                    r['tags'] = [('mistake', rnd.choice(['late', 'chased']))]
                rows.append(r)
        res = leaks.find_leaks(leaks.derive(rows))
        for lk in res['leaks']:
            assert lk['net_pnl'] < 0 and lk['dedup_cost'] > 0, (seed, lk['id'])


def test_swing_trade_closing_today_is_not_part_of_todays_sequence():
    swing = trade('2026-09-16', '15:00:00', '10:30:00', -500)
    ex = json.loads(swing['executions'])
    ex[0]['date'], ex[1]['date'] = '2026-09-14', '2026-09-16'
    swing['executions'] = json.dumps(ex)
    today = trade('2026-09-16', '10:00:00', '10:20:00', 40)
    later = trade('2026-09-16', '11:00:00', '11:10:00', 10)
    leaks.derive([swing, today, later])
    assert swing['trade_no'] == 1 and swing['entry_ts'].startswith('2026-09-14')
    assert today['trade_no'] == 1 and today['loss_streak'] == 0
    assert later['loss_streak'] == 0 and later['day_run'] == pytest.approx(40),         'a swing entered on another day never counts in this session'


def test_same_minute_trades_do_not_count_each_other():
    a = trade('2026-09-17', '10:05:10', '10:05:40', -30)
    b = trade('2026-09-17', '10:05:20', '10:05:50', -10)
    c = trade('2026-09-17', '10:05:45', '10:06:30', 5)
    leaks.derive([a, b, c])
    assert b['loss_streak'] == 0, 'a closed after b was entered'
    assert c['loss_streak'] == 1 and c['since_loss'] == pytest.approx(5 / 60)


def test_a_trade_without_times_gets_no_trade_number():
    r = trade('2026-09-18', '', '', 10)
    leaks.derive([r, trade('2026-09-18', '10:00:00', '10:05:00', 5)])
    assert r['trade_no'] is None and leaks._trade_no(r) is None


def test_usual_size_only_uses_earlier_trades():
    rows = [trade(f'2026-09-{d:02d}', '10:00:00', '10:05:00', 1, qty=100) for d in range(1, 6)]
    big_first = trade('2026-08-31', '10:00:00', '10:05:00', 1, qty=300)
    leaks.derive(rows + [big_first])
    assert big_first['size_ratio'] is None, 'nothing earlier to compare it with'
    assert rows[-1]['size_ratio'] is not None


def test_rest_label_names_the_comparison_group():
    rows = leaks.derive(_sessions(25, 50, -30))
    f = leaks.find_leaks(rows)['leaks'][0]
    assert f['rest_label'] == 'your other trades'
    assert leaks.REST_LABEL['reentry'] == 'other trades taken after a loss'


# ── API: /api/leaks and the cached explanations ────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "journal.db"
    monkeypatch.setenv("DATABASE_PATH", str(db))
    for name in ("database", "main"):
        sys.modules.pop(name, None)
    import database
    database.DB_PATH = str(db)
    import main
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        conn = main.get_db()
        if not conn.execute("SELECT 1 FROM accounts WHERE id=1").fetchone():
            conn.execute("INSERT INTO accounts (id, name, type) VALUES (1, 'Day', 'day_trading')")
        for r in _sessions(25, 50, -30):
            conn.execute(
                "INSERT INTO trades (account_id, trade_group, date, ticker, instrument_type, side, "
                "gross_pnl, net_pnl, commissions, executions, source) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (1, f"g{r['id']}", r['date'], r['ticker'], r['instrument_type'], r['side'],
                 r['gross_pnl'], r['net_pnl'], r['commissions'], r['executions'], 'imported'))
        conn.commit()
        conn.close()
        yield c, main


def test_api_returns_leaks_management_and_costs(client):
    c, _ = client
    body = c.get('/api/leaks', params={'account_id': 1}).json()
    assert body['has_data'] and body['trade_count'] == 50
    assert body['leaks'][0]['id'] == 'entry_window:11:00-13:30'
    assert body['management']['by_hold_time'] and body['costs'][0]['instrument'] == 'STOCK'
    assert c.get('/api/leaks', params={'account_id': 99}).json() == {'has_data': False}


def test_explain_caches_and_falls_back(client, monkeypatch):
    c, main = client
    calls = []

    def fake(findings):
        calls.append(len(findings))
        return {f['id']: 'Plain sentence.' for f in findings}

    monkeypatch.setattr(main, 'explain_leaks', fake)
    first = c.get('/api/leaks/explain', params={'account_id': 1}).json()
    again = c.get('/api/leaks/explain', params={'account_id': 1}).json()
    assert first['cached'] is False and again['cached'] is True
    assert first['sentences'] == again['sentences'] and len(calls) == 1

    # a partial answer is returned but not cached, so the next visit asks again
    conn = main.get_db()
    conn.execute("DELETE FROM leak_explanations")
    conn.commit()
    conn.close()
    monkeypatch.setattr(main, 'explain_leaks', lambda findings: {findings[0]['id']: 'Only one.'})
    part = c.get('/api/leaks/explain', params={'account_id': 1}).json()
    assert part['cached'] is False and len(part['sentences']) == 1
    conn = main.get_db()
    assert conn.execute("SELECT COUNT(*) FROM leak_explanations").fetchone()[0] == 0
    conn.close()

    def broken(findings):
        raise ValueError("ANTHROPIC_API_KEY is not set in .env file")

    conn = main.get_db()
    conn.execute("DELETE FROM leak_explanations")
    conn.commit()
    conn.close()
    monkeypatch.setattr(main, 'explain_leaks', broken)
    body = c.get('/api/leaks/explain', params={'account_id': 1}).json()
    assert body['sentences'] == {} and 'ANTHROPIC_API_KEY' in body['error']


def test_setup_slice_uses_the_playbook_setup_name(client):
    """The setup dimension reads trades.setup, the playbook name the trader tagged."""
    c, main = client
    conn = main.get_db()
    # midday trades tagged with one playbook setup, the morning ones with another, one as 'No setup'
    conn.execute("UPDATE trades SET setup = 'Midday Fade' WHERE executions LIKE '%12:00:00%'")
    conn.execute("UPDATE trades SET setup = 'Opening Drive' WHERE executions LIKE '%09:50:00%'")
    conn.execute("UPDATE trades SET setup = 'NONE' WHERE id = (SELECT MIN(id) FROM trades)")
    conn.commit()
    rows = main._leak_rows(conn, 1, None, None)
    conn.close()
    labels = {r['setup_label'] for r in rows}
    assert labels == {'Midday Fade', 'Opening Drive', 'No setup'}
    ids = {f['id'] for f in leaks.bucket_table(rows)}
    assert 'setup:Midday Fade' in ids and 'setup:Opening Drive' in ids
