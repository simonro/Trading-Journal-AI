"""Leak Finder: rank the slices of your trading by what they cost.

Pure functions over trade rows (no database, no HTTP), so the rules are easy to test.

A *leak* is a bucket of trades, defined only by things known at entry (time, trade number,
losses before it, how the day stood, size, instrument...), whose net P&L is negative. Its cost
is what skipping those trades would have saved. Outcome slices such as hold time are not leaks:
a stop-out is short by design, so they are reported under trade management instead. Mistake tags
are logged after the trade and are mostly about what happened in it, so they sit there too.

Rules:
- A bucket needs MIN_TRADES trades, and the rest of its dimension needs as many, before it counts.
- Confidence is a Welch t against the rest of the same dimension.
- Leaks are picked greedily: take the worst bucket, remove its trades, recompute, repeat. The
  headline total adds the de-duplicated costs, so no trade is counted twice.
"""
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean, median, pvariance

MIN_TRADES = 20
RECENT_DAYS = 30
RECENT_MIN_TRADES = 10
MAX_LEAKS = 3
MAX_EDGES = 2
MAX_NEW = 2
PROMPT_VERSION = 3

_DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']


def _mins(t):
    if not t:
        return None
    try:
        p = str(t).split(':')
        return int(p[0]) * 60 + int(p[1])
    except Exception:
        return None


def session_bucket(r):
    """Same windows as the Reports Timing tab, so the two reconcile."""
    t = r.get('entry_min')
    if t is None:
        return None
    if t < 9 * 60 + 45:
        return '09:30-09:45'
    if t < 10 * 60 + 30:
        return '09:45-10:30'
    if t < 11 * 60:
        return '10:30-11:00'
    if t < 13 * 60 + 30:
        return '11:00-13:30'
    if t < 15 * 60 + 30:
        return '13:30-15:30'
    return '15:30-16:00'


def hold_bucket(r):
    """Same buckets as the Reports hold-time breakdown."""
    m = r.get('hold_min')
    if m is None or m < 0:
        return None
    if m < 5:
        return '0-5 min'
    if m < 15:
        return '5-15 min'
    if m < 30:
        return '15-30 min'
    if m < 60:
        return '30-60 min'
    if m < 120:
        return '1-2 hrs'
    return '2+ hrs'


def management_bucket(r):
    if r.get('n_exits', 0) > 1:
        return 'Scaled out'
    if r.get('n_exits') == 1:
        return 'All-or-nothing'
    return None


# ── per-trade features ─────────────────────────────────────────────────────

def _stamp(e, fallback_date):
    t = e.get('time')
    if not t:
        return None
    t = str(t)
    if len(t) == 5:
        t += ':00'
    return f"{e.get('date') or fallback_date} {t}"


def _minutes_between(a, b):
    fmt = '%Y-%m-%d %H:%M:%S'
    try:
        return (datetime.strptime(b, fmt) - datetime.strptime(a, fmt)).total_seconds() / 60
    except ValueError:
        return None


def trade_times(ex, side, date):
    """Entry and exit times of one trade from its executions. Reports uses this too, so the two reconcile.

    Fills are ordered by full timestamp (date and seconds), so hold_min is right for a trade held
    overnight. notional is the size of the first entry only: an add made later was not known when
    the trade was entered.
    """
    ea = 'BOT' if side == 'LONG' else 'SOLD'
    xa = 'SOLD' if side == 'LONG' else 'BOT'
    key = lambda e: (_stamp(e, date) or '', e.get('time', ''))
    ent = sorted([e for e in ex if e.get('action') == ea], key=key)
    xit = sorted([e for e in ex if e.get('action') == xa], key=key)
    entry_ts = _stamp(ent[0], date) if ent else None
    exit_ts = _stamp(xit[-1], date) if xit else None
    entry_min = _mins(ent[0].get('time')) if ent else None
    exit_min = _mins(xit[-1].get('time')) if xit else None
    if entry_ts and exit_ts:
        hold_min = _minutes_between(entry_ts, exit_ts)
    else:
        hold_min = exit_min - entry_min if entry_min is not None and exit_min is not None else None
    first = [e for e in ent if _stamp(e, date) == entry_ts] if entry_ts else ent[:1]
    try:
        notional = sum(float(e.get('qty') or 0) * float(e.get('price') or 0) for e in first) or None
    except (TypeError, ValueError):
        notional = None
    try:
        bought = sum(float(e.get('qty') or 0) for e in ex if e.get('action') == 'BOT')
        sold = sum(float(e.get('qty') or 0) for e in ex if e.get('action') == 'SOLD')
        closed = bool(ent) and bool(xit) and abs(bought - sold) < 1e-6
    except (TypeError, ValueError):
        closed = False
    return {
        'entry_min': entry_min, 'exit_min': exit_min, 'hold_min': hold_min,
        'n_exits': len({e.get('time') for e in xit}) if xit else 0,
        'entry_ts': entry_ts, 'exit_ts': exit_ts, 'notional': notional, 'closed': closed,
    }


def derive(rows):
    """Add entry/exit minutes, notional, and the at-entry sequence features to each row.

    Pass every trade in the range, breakeven and still-open ones included, so trade numbers and
    loss streaks count every entry; drop the zero-P&L rows afterwards. The sequence features use
    full fill timestamps: a trade counts as "before" this one only if it was fully closed earlier
    in the session this one was entered, so an open position, a same-minute trade or a swing
    trade closing days later never leaks in.
    """
    for r in rows:
        try:
            ex = json.loads(r.get('executions') or '[]')
        except Exception:
            ex = []
        r.update(trade_times(ex, r.get('side'), r['date']))
        try:
            r['dow'] = datetime.strptime((r['entry_ts'] or r['date'])[:10], '%Y-%m-%d').weekday()
        except Exception:
            r['dow'] = None

    days = defaultdict(list)
    for r in rows:
        r['trade_no'] = r['day_run'] = r['day_peak'] = r['loss_streak'] = r['since_loss'] = None
        if r['entry_ts']:
            days[(r.get('account_id'), r['entry_ts'][:10])].append(r)
    for day in days.values():
        day.sort(key=lambda r: (r['entry_ts'], r.get('id') or 0))
        for i, r in enumerate(day, 1):
            r['trade_no'] = i
            closed = sorted(
                [o for o in day if o is not r and o['closed'] and o['exit_ts'] and o['exit_ts'] < r['entry_ts']],
                key=lambda o: (o['exit_ts'], o.get('id') or 0))
            run = peak = 0.0
            streak = 0
            last_loss = None
            for o in closed:
                pnl = o.get('net_pnl') or 0
                run += pnl
                peak = max(peak, run)
                if pnl < 0:
                    streak += 1
                    last_loss = o['exit_ts']
                else:
                    streak = 0
            r['day_run'], r['day_peak'] = run, peak
            r['loss_streak'] = streak
            r['since_loss'] = None if last_loss is None else _minutes_between(last_loss, r['entry_ts'])

    # Usual size is the median of the earlier trades in the same ticker and account, so it is known
    # at entry and one account's size never sets another's.
    seen = defaultdict(list)
    for r in sorted(rows, key=lambda r: (r['entry_ts'] or '9999', r.get('id') or 0)):
        key = (r.get('account_id'), r.get('ticker'), r.get('instrument_type'))
        prior = seen[key]
        r['size_ratio'] = (r['notional'] / median(prior)) if (r['notional'] and len(prior) >= 5) else None
        if r['notional'] and r['entry_ts']:
            prior.append(r['notional'])
    return rows


# ── dimensions: everything here is known when the trade is entered ─────────

def _trade_no(r):
    n = r.get('trade_no')
    if n is None:
        return None
    return '1st' if n == 1 else '2nd-3rd' if n <= 3 else '4th-6th' if n <= 6 else '7th or later'


def _loss_streak(r):
    s = r.get('loss_streak')
    if s is None:
        return None
    return 'No loss just before' if s == 0 else 'After 1 loss' if s == 1 else 'After 2+ losses in a row'


def _reentry(r):
    m = r.get('since_loss')
    if m is None:
        return None
    return 'Within 5 min of a loss' if m <= 5 else '5-15 min after a loss' if m <= 15 else '15+ min after a loss'


def _day_state(r):
    run, peak = r.get('day_run'), r.get('day_peak')
    if run is None:
        return None
    if run < 0:
        return 'Day already red'
    if peak > run:
        return 'Giving back a green day'
    return 'Flat or at the day high'


def _size(r):
    x = r.get('size_ratio')
    if x is None:
        return None
    return '1.5x+ your usual size' if x >= 1.5 else 'Under 2/3 of your usual size' if x <= 2 / 3 else 'Your usual size'


def _tags(kind):
    def fn(r):
        vals = sorted({v for (t, v) in r.get('tags', []) if t == kind})
        return vals or None
    return fn


# (id, label shown in the UI, bucket function). A function may return a list (tags).
DIMENSIONS = [
    ('entry_window', 'Entry time', session_bucket),
    ('trade_no', 'Trade of the day', _trade_no),
    ('loss_streak', 'Losses just before', _loss_streak),
    ('reentry', 'Back in after a loss', _reentry),
    ('day_state', 'How the day stood', _day_state),
    ('size', 'Size', _size),
    ('instrument', 'Instrument', lambda r: r.get('instrument_type')),
    ('side', 'Side', lambda r: r.get('side')),
    ('weekday', 'Day of week', lambda r: None if r.get('dow') is None else _DOW[r['dow']]),
    ('setup', 'Setup', lambda r: r.get('setup_label')),
    ('strategy', 'Strategy', lambda r: r.get('strategy') or None),
    ('source', 'Source', lambda r: r.get('idea_source') or None),
    ('emotion', 'Emotion', lambda r: r.get('emotional_state') or None),
]
# What "the rest" is for each dimension: every other trade that has a value in it.
REST_LABEL = {
    'reentry': 'other trades taken after a loss',
    'setup': 'other trades with a setup tag',
    'strategy': 'other trades with a strategy',
    'source': 'other trades with a source',
    'emotion': 'other trades with an emotion logged',
    'size': 'other trades in tickers you had traded 5+ times',
}
# Rows these dimensions depend on come from the diary, which covers few trades.
DIARY_DIMENSIONS = {'emotion', 'strategy', 'source'}


def mistake_rows(rows):
    """One row per (trade, mistake tag) for the breakdown shown beside the leaks, never ranked."""
    return [dict(r, mistake_tag=v) for r in rows for v in (_tags('mistake')(r) or [])]


def _keys(fn, r):
    k = fn(r)
    if k is None or k == '':
        return []
    return k if isinstance(k, list) else [k]


def _welch_t(a, b):
    if len(a) < 2 or len(b) < 2:
        return 0.0
    diff = mean(a) - mean(b)
    se = math.sqrt(pvariance(a) / len(a) + pvariance(b) / len(b))
    if not se:
        # identical values on both sides: any difference is certain, but keep it JSON-safe
        return 0.0 if not diff else math.copysign(99.0, diff)
    return diff / se


def confidence(t):
    t = abs(t)
    return 'solid' if t >= 2 else 'likely' if t >= 1 else 'noise'


def bucket_table(rows, min_trades=MIN_TRADES):
    """Every qualifying (dimension, bucket) over rows, with its stats against the rest."""
    out = []
    for dim, dim_label, fn in DIMENSIONS:
        groups = defaultdict(list)
        for r in rows:
            for k in _keys(fn, r):
                groups[k].append(r)
        members = {id(r): r for g in groups.values() for r in g}
        for k, g in groups.items():
            inside = {id(r) for r in g}
            rest = [r['net_pnl'] for i, r in members.items() if i not in inside]
            if len(g) < min_trades or len(rest) < min_trades:
                continue
            pnls = [r['net_pnl'] for r in g]
            out.append({
                'id': f'{dim}:{k}',
                'dimension': dim,
                'dimension_label': dim_label,
                'bucket': str(k),
                'trades': len(g),
                'net_pnl': round(sum(pnls), 2),
                'avg_pnl': round(mean(pnls), 2),
                'rest_avg_pnl': round(mean(rest), 2),
                'win_rate': round(sum(1 for p in pnls if p > 0) / len(pnls) * 100, 1),
                'rest_label': REST_LABEL.get(dim, 'your other trades'),
                't': round(_welch_t(pnls, rest), 2),
                'excess': round((mean(pnls) - mean(rest)) * len(g), 2),
                'trade_ids': [r['id'] for r in g],
                'diary_based': dim in DIARY_DIMENSIONS,
            })
    return out


def _usd(v):
    return f"{'-' if v < 0 else ''}${abs(v):,.0f}"


def template_sentence(f):
    """The plain-numbers sentence shown until (or instead of) Claude's."""
    if f['net_pnl'] < 0:
        return (f"These {f['trades']} trades lost {_usd(-f['net_pnl'])} in total, "
                f"{_usd(f['avg_pnl'])} a trade against {_usd(f['rest_avg_pnl'])} for {f['rest_label']}.")
    return (f"These {f['trades']} trades made {_usd(f['net_pnl'])} in total, "
            f"{_usd(f['avg_pnl'])} a trade against {_usd(f['rest_avg_pnl'])} for {f['rest_label']}.")


def _recent(rows):
    dates = [r['date'] for r in rows if r.get('date')]
    if not dates:
        return []
    start = (datetime.strptime(max(dates), '%Y-%m-%d') - timedelta(days=RECENT_DAYS - 1)).strftime('%Y-%m-%d')
    return [r for r in rows if r['date'] >= start]


def _strip(f):
    """A finding for the API: drop internal fields, keep trade ids for the drill-down."""
    f = dict(f)
    f.pop('excess', None)
    f['confidence'] = confidence(f['t'])
    f['sentence'] = template_sentence(f)
    return f


def _bucket_nets(rows):
    """Net P&L of every bucket over rows, with no minimum sample."""
    nets = defaultdict(float)
    for dim, _, fn in DIMENSIONS:
        for r in rows:
            for k in _keys(fn, r):
                nets[f'{dim}:{k}'] += r['net_pnl']
    return nets


def find_leaks(rows):
    """rows must already be derive()d. Returns leaks, edges and new-in-recent findings.

    Every finding carries a key ("leak|", "edge|" or "new|" plus its id): an edge and a new leak
    can share a bucket id while showing different periods, so sentences are stored by key.
    """
    by_id = {}
    full = bucket_table(rows)
    for f in full:
        by_id[f['id']] = f

    leaks = []
    pool = list(rows)
    taken = set()
    for _ in range(MAX_LEAKS):
        # It must lose on what is left AND over the whole range, so a leak is never shown in profit.
        cands = [f for f in bucket_table(pool)
                 if f['net_pnl'] < 0 and f['id'] not in taken and f['id'] in by_id and by_id[f['id']]['net_pnl'] < 0]
        if not cands:
            break
        pick = min(cands, key=lambda f: f['net_pnl'])
        base = dict(by_id[pick['id']])
        base['dedup_cost'] = round(-pick['net_pnl'], 2)
        earlier = set()
        for lk in leaks:
            earlier |= set(lk['trade_ids'])
        base['overlap_trades'] = len(set(base['trade_ids']) & earlier)
        leaks.append(base)
        taken.add(pick['id'])
        drop = set(pick['trade_ids'])
        pool = [r for r in pool if r['id'] not in drop]

    leak_dims = {f['id'] for f in leaks}
    edges = sorted([f for f in full if f['net_pnl'] > 0 and f['t'] >= 1 and f['id'] not in leak_dims],
                   key=lambda f: -f['excess'])
    picked_edges, seen_dims = [], set()
    for f in edges:
        if f['dimension'] in seen_dims:
            continue
        picked_edges.append(f)
        seen_dims.add(f['dimension'])
        if len(picked_edges) == MAX_EDGES:
            break

    recent_rows = _recent(rows)
    recent = {f['id']: f for f in bucket_table(recent_rows, RECENT_MIN_TRADES)}
    for lk in leaks:
        rf = recent.get(lk['id'])
        lk['recent'] = None if rf is None else {'trades': rf['trades'], 'net_pnl': rf['net_pnl']}

    # A bucket that is losing over the last 30 days but not over the whole range. The whole-range
    # check has no minimum sample: a bucket too small to rank there may still be losing.
    full_net = _bucket_nets(rows)
    new = sorted([f for f in recent.values()
                  if f['net_pnl'] < 0 and f['id'] not in leak_dims and full_net.get(f['id'], 0) >= 0],
                 key=lambda f: f['net_pnl'])[:MAX_NEW]

    return {
        'leaks': [dict(_strip(f), key=f"leak|{f['id']}") for f in leaks],
        'edges': [dict(_strip(f), key=f"edge|{f['id']}") for f in picked_edges],
        'new': [dict(_strip(f), key=f"new|{f['id']}", recent={'trades': f['trades'], 'net_pnl': f['net_pnl']})
                for f in new],
        'total_cost': round(sum(f['dedup_cost'] for f in leaks), 2),
        'recent_from': min((r['date'] for r in recent_rows), default=None),
    }


def costs(rows):
    by = defaultdict(lambda: {'trades': 0, 'gross_pnl': 0.0, 'commissions': 0.0, 'net_pnl': 0.0})
    for r in rows:
        b = by[r.get('instrument_type') or 'Other']
        b['trades'] += 1
        b['gross_pnl'] += r.get('gross_pnl') or 0
        b['commissions'] += r.get('commissions') or 0
        b['net_pnl'] += r['net_pnl']
    out = []
    for k, b in by.items():
        out.append({'instrument': k, 'trades': b['trades'],
                    'gross_pnl': round(b['gross_pnl'], 2), 'commissions': round(b['commissions'], 2),
                    'net_pnl': round(b['net_pnl'], 2),
                    'per_trade': round(b['commissions'] / b['trades'], 2) if b['trades'] else 0})
    return sorted(out, key=lambda b: -b['commissions'])


def coverage(rows):
    diary = sum(1 for r in rows if r.get('emotional_state') or r.get('tags') or r.get('strategy'))
    return {'trades': len(rows), 'diary_trades': diary}


def cache_key(result):
    """Changes whenever a number Claude would explain changes."""
    def slim(f):
        return [f['key'], f['trades'], f['net_pnl'], f['avg_pnl'], f['rest_avg_pnl'],
                f['win_rate'], f['confidence'], f.get('recent'), f.get('overlap_trades')]
    payload = {
        'v': PROMPT_VERSION,
        'leaks': [slim(f) for f in result['leaks']],
        'edges': [slim(f) for f in result['edges']],
        'new': [slim(f) for f in result['new']],
    }
    return 'leaks_' + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:32]


def explain_payload(result):
    """What Claude sees: the numbers, never trade ids."""
    def slim(f, kind):
        d = {k: f.get(k) for k in ('id', 'dimension_label', 'bucket', 'trades', 'net_pnl', 'avg_pnl',
                                    'rest_avg_pnl', 'rest_label', 'win_rate', 'confidence', 'recent',
                                    'overlap_trades', 'diary_based')}
        d['id'] = f['key']
        d['kind'] = kind
        return d
    return ([slim(f, 'leak') for f in result['leaks']]
            + [slim(f, 'edge') for f in result['edges']]
            + [slim(f, 'new_leak_last_30_days') for f in result['new']])
