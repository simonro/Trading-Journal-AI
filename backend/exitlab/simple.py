"""All-history descriptive tables and individual trade replay."""
from dataclasses import asdict
import json
from fastapi import HTTPException
from .models import NY, iso, timestamp
from .rules import Costs, Rule
from .assignments import applies, saved_rule, setup_name, setup_options
from .service import load_entries
from .stops import journal_stop
from .compare import compare
from .scaling import scaling_details
from .excursion import excursions


def settings(conn):
    # Existing rows are intentionally left untouched and ignored.
    return Costs()


def save_settings(conn, costs):
    # Compatibility endpoint for an older frontend; cost settings are retired.
    return Costs().model_dump()


def clock(value):
    return timestamp(value).astimezone(NY).strftime('%H:%M')


def hits(trade, bars):
    """Chart annotations only. Outcomes always come from simulate, never this scan."""
    if trade.stop is None or trade.risk <= 0:
        return []
    result = []
    for label, price in [('Stop', trade.stop), ('1R', trade.entry + trade.direction * trade.risk),
                         ('2R', trade.entry + trade.direction * 2 * trade.risk)]:
        for b in bars:
            if b.ts < trade.replay_at.replace(second=0, microsecond=0):
                continue
            reached = (b.low <= price if trade.direction == 1 else b.high >= price) if label == 'Stop' else (b.high >= price if trade.direction == 1 else b.low <= price)
            if reached:
                result.append({'label': label, 'ts': iso(b.ts), 'price': price})
                break
    return result


def fill_story(fills, trade):
    """Describe executed quantities, prices and reasons, never the intended allocation."""
    parts = []
    closed = 0
    verb = 'Sold' if trade.direction == 1 else 'Covered'
    labels = {'ema9': 'on EMA 9 close', 'ema20': 'on EMA 20 close',
              'ema_cross': 'on EMA 9/20 cross', 'vwap': 'on VWAP close',
              'vwap_touch': 'at VWAP touch', 'rs_rw': 'on the RS/RW flip',
              'stop': 'at the stop', 'minutes': 'at the timed exit',
              'clock': 'at the clock exit', 'session_close': 'at session close',
              'session_close_clock_fallback': 'at early session close'}
    for f in fills:
        closed += f['quantity']
        size = 'rest' if parts and abs(closed - trade.quantity) < 1e-8 else f"{100 * f['quantity'] / trade.quantity:.2f}".rstrip('0').rstrip('.') + '%'
        action = size if size == 'rest' else f'{verb} {size}'
        if f['reason'] == 'target':
            multiple = trade.direction * (f['price'] - trade.entry) / trade.risk
            why = f'at {multiple:+g}R'
        else:
            why = labels.get(f['reason'], f['reason'].replace('_', ' '))
        parts.append(f"{action} {why} {clock(f['ts'])}")
    return ', '.join(parts)


def happened(result, rule, trade, bars, custom=False):
    if not result['eligible']:
        return None
    fills = result["fills"]
    if custom:
        return fill_story(fills, trade)
    f = fills[-1]
    when = clock(f['ts'])
    if f['reason'] == 'target':
        return f'Hit {rule.tranches[0].r:g}R at {when} before the stop'
    activated = clock(result['activated_at']) if result.get('activated_at') else None
    if f['reason'] == 'stop':
        if activated and abs(f['price'] - trade.entry) < trade.risk * .5:
            return f'Stop moved to entry at {activated}, stopped at {when}'
        return f'Stop hit at {when}'
    if activated:
        return f'Stop moved to entry at {activated}, held to close'
    return f'Stop never hit, closed at {when}'


def decorate(result, entries):
    by_id = {e['id']: e for e in entries}
    for r in result['rules']:
        if r.get('is_preset', True) and r['rule']['name'] == 'Breakeven at +1R':
            r['rule']['name'] = 'Breakeven after +1R'
        for t in r['trades']:
            e = by_id[t['trade_id']]
            trade = e['trade']
            t['what_happened'] = happened(t, Rule.model_validate(r['rule']), trade, e['bars'], custom=r.get('is_preset') is False)
            if t['eligible']:
                t['actual_r'] = t['actual_pnl_modeled_costs'] / (trade.risk * trade.quantity)
    return result


def history(conn, account_id, only_ids=None):
    entries = load_entries(conn, account_id, 'sip')
    saved = conn.execute('SELECT * FROM exit_rules ORDER BY is_preset DESC, id').fetchall()
    selected_rules = [r for r in saved if r['is_preset'] or r['show_in_tables']]
    if only_ids is not None:
        selected_rules = [r for r in selected_rules if any(applies(saved_rule(r), e['journal_trade']) for e in entries if e['id'] in only_ids)]
    policies = [saved_rule(r) for r in selected_rules]
    result = compare(entries, policies, only_ids=only_ids)
    shown = [e for e in entries if only_ids is None or e['id'] in only_ids]
    for item, row in zip(result['rules'], selected_rules):
        item.update(rule_id=row['id'], is_preset=bool(row['is_preset']))
    result['setup_scoped'] = True
    result['setup_options'] = setup_options(conn, account_id, entries)
    result['assignment_rules'] = [{'id': r['id'], 'name': r['name'], 'applies_to_setups': json.loads(r['applies_to_setups'])} for r in saved]
    result['custom_rules'] = [{'id': r['id'], 'name': r['name'], 'show_in_tables': bool(r['show_in_tables'])}
                              for r in saved if not r['is_preset']]
    decorate(result, shown)
    result['rows'] = []
    for e in shown:
        t = e.get('trade')
        stop = journal_stop(conn, e['journal_trade'])
        row = {'trade_id': e['id'], 'journal_trade': e['journal_trade'], 'reasons': e['reasons'],
               'stop_price': t.stop if t else stop, 'setup_name': setup_name(e['journal_trade']),
               'scaled_entry': scaling_details(t, e['bars']) if t else None}
        row['actual_r'] = (t.direction*(t.exit-t.entry)/t.risk if t and t.risk and not e['reasons'] else None)
        result['rows'].append(row)
    result['costs'] = settings(conn).model_dump()
    return result, shown


def simple_replay(conn, trade_id):
    row = conn.execute('SELECT account_id FROM trades WHERE id=?', (trade_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'trade_not_found')
    result, entries = history(conn, row[0], only_ids={trade_id})
    e = entries[0]
    trade = e.get('trade')
    result.update(trade=asdict(trade) if trade else None, bars=[asdict(b) for b in e['bars']],
                  reasons=e['reasons'], first_hits=hits(trade, e['bars']) if trade else [],
                  scaled_entry=scaling_details(trade, e['bars']) if trade else None,
                  excursion=excursions(trade, e['bars']) if trade else None)
    return result
