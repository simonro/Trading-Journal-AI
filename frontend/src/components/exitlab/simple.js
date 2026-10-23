import { useState } from 'react';
import { exitlabApi } from '../../api';
import { signed, number, money, moneyTone, clockTime, reasonText, Notice, errorText } from './shared';
import { autoSetupLabel } from './SetupAssignment';

export const SIMPLE_NAMES = ['Actual', '1R target', '2R target', 'Hold to close', 'Breakeven after +1R'];
export const resultText = value => Number.isFinite(value) ? `${signed(value)}R` : 'Not available';
export const setupOf = row => row.setup_name || row.journal_trade?.strategy || autoSetupLabel(row.journal_trade?.setup) || 'No setup';
export function matches(row, filters) {
  const t = row.journal_trade;
  return (!filters.from || t.date >= filters.from) && (!filters.to || t.date <= filters.to) &&
    (!filters.symbol || t.ticker === filters.symbol) && (!filters.setup || setupOf(row) === filters.setup) &&
    (!filters.side || t.side === filters.side);
}
export function summary(result, filters = {}) {
  const rows = (result?.rows || []).filter(row => matches(row, filters));
  const ids = new Set(rows.map(r => r.trade_id));
  const rules = result?.rules || [];
  const scoped = result?.setup_scoped;
  const common = rows.filter(row => scoped ? Number.isFinite(row.actual_r) : rules.length && rules.every(r => r.trades.some(t => t.trade_id === row.trade_id && t.eligible)));
  const included = new Set(common.map(r => r.trade_id));
  const netPnl = new Map(rows.map(r => [r.trade_id, r.journal_trade.net_pnl]));
  const stats = (trades, actual = false) => {
    const values = trades.map(t => actual ? t.actual_r : t.pnl_r);
    const dollars = trades.map(t => actual ? netPnl.get(t.trade_id) : t.pnl);
    const n = values.length, total = values.reduce((a, b) => a + b, 0);
    const totalDollars = n && dollars.every(Number.isFinite) ? dollars.reduce((a, b) => a + b, 0) : null;
    return { n, avg: n ? total / n : null, total: n ? total : null, avgDollars: totalDollars == null ? null : totalDollars / n,
      totalDollars, win: n ? values.filter(v => v > 1e-8).length / n * 100 : null };
  };
  const actualTrades = scoped ? common.map(r => ({ trade_id: r.trade_id, actual_r: r.actual_r })) : (rules[0]?.trades || []).filter(t => included.has(t.trade_id));
  const actual = stats(actualTrades, true);
  const table = [{ name: 'Actual', key: 'actual', ...actual, delta: null, trades: actualTrades }];
  rules.forEach(r => {
    const trades = r.trades.filter(t => scoped ? ids.has(t.trade_id) && t.eligible : included.has(t.trade_id));
    const data = stats(trades), baseline = scoped ? stats(trades, true) : actual;
    table.push({ name: r.rule.name, key: r.rule_id == null ? r.rule_hash : `rule:${r.rule_id}`, ...data, setups: r.applies_to_setups, scoped, excluded: r.trades.filter(t => ids.has(t.trade_id) && !t.eligible), delta: data.n ? data.avg - baseline.avg : null,
      deltaDollars: data.avgDollars != null && baseline.avgDollars != null ? data.avgDollars - baseline.avgDollars : null,
      trades });
  });
  const excluded = rows.filter(r => !included.has(r.trade_id)).map(row => ({ ...row,
    reasons: [...new Set([...row.reasons, ...rules.flatMap(r => r.trades.find(t => t.trade_id === row.trade_id)?.reasons || [])])] }));
  return { rows, table, excluded };
}
export function historyLead(table) {
  const n = table[0]?.n || 0;
  if (!n) return 'No trades to compare yet. Add a stop or review the missing price data below.';
  // Rank at the same precision as the table, including ties and rounded zero.
  const rules = table.filter(row => row.key !== 'actual' && Number.isFinite(row.delta))
    .map(row => ({ ...row, delta: Number(number(row.delta)) }));
  if (!rules.length) return 'No exit rules to compare yet.';
  const high = Math.max(...rules.map(row => row.delta));
  const low = Math.min(...rules.map(row => row.delta));
  const phrase = delta => {
    const names = rules.filter(row => row.delta === delta).map(row => row.name);
    const label = names.length < 2 ? names[0] : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
    const subject = `${label}${names.length > 1 ? ' each' : ''} averaged`;
    return delta === 0 ? `${subject} the same R per trade as your actual exits (0.00R difference)`
      : `${subject} ${number(Math.abs(delta))}R per trade ${delta > 0 ? 'more' : 'less'} than your actual exits`;
  };
  const samples = table.filter(r => r.key !== 'actual').map(r => JSON.stringify(r.trades?.map(t => t.trade_id).sort((a, b) => a - b)));
  if (table.some(r => r.scoped) && new Set(samples).size > 1) return 'Each rule compares its assigned setups with your actual exits on those same eligible trades. Samples differ between rows.';
  const count = table.some(r => r.scoped) ? rules[0].n : n;
  return `Across ${count} trades with a stop, ${[...new Set([high, low])].map(phrase).join('; ')}.`;
}
export function exclusionLine(rows) {
  const missing = rows.filter(r => r.stop_price == null || r.reasons.includes('missing_initial_stop')).length;
  const prices = rows.filter(r => r.stop_price != null && r.reasons.some(x => /coverage|bars|warmup/.test(x))).length;
  const other = rows.length - missing - prices;
  return `${rows.length} trades not included${rows.length ? ': ' + [missing && `${missing} have no stop`, prices && `${prices} have missing price data`, other && `${other} need trade details reviewed`].filter(Boolean).join(', ') : ''}.`;
}
export function SimpleStop({ tradeId, onSaved }) {
  const [price, setPrice] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function save(e) {
    e.preventDefault(); setBusy(true); setError('');
    try { await exitlabApi.stop(tradeId, { price: Number(price) }); await onSaved(); }
    catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  return <form className="xl-inline-stop" onSubmit={save}><div className="xl-fields"><label>Enter your stop<input autoFocus required type="number" min="0.0001" step="any" value={price} onChange={e => setPrice(e.target.value)} /></label><button className="btn btn-primary" disabled={busy || !price || Number(price) <= 0}>{busy ? 'Saving...' : 'Save stop'}</button></div>{error && <Notice error>{error}</Notice>}</form>;
}
export function SimpleMethodology() {
  return <details className="xl-method-link"><summary>How this is calculated</summary><p>Each exit starts with your actual entry and the stop loss on your journal entry. R is the distance from entry to stop. The stop stays active through the session close, including early closes. Times are New York time.</p><p>Scaled entries use the quantity-weighted average of every entry fill and the total entered quantity. One R in dollars is the absolute distance from that average to the initial stop times total quantity. Actual and simulated R share that denominator. Replay begins at the last entry fill: the boundary minute counts adverse stop touches and credits favorable movement only from the fill or closing price; full-minute simulation starts the following minute. MFE, MAE and efficiency use the average entry over the hold after the last fill.</p><p>Stops, targets and rule thresholds during the build window do not trigger exits or activate rules. A stop or target touch while scaling in is flagged and not counted. This method models the completed position, not changing exposure while it was built. Scale-outs remain supported; adding after a partial exit or flipping side remains excluded.</p><p>When events compete within one minute, the protective stop fills first (including a newly activated breakeven stop), followed by targets, then indicator or time exits. Targets require one tick beyond the level; gaps through a stop fill at the opening price. The opening minute can include prices from before your entry.</p><p>Only closed, same-session stock trades with a usable stop, matching fills and complete price data can be compared. Rule simulations always use zero modeled costs and zero slippage. Actual fills are never slipped again. RS/RW stays on 5-minute candles. RS/RW, EMA 9/20 and ATR use regular-hours warm-up only: completed bars from today, then the needed tail of the previous exchange session, including early closes. Every required minute must exist; missing prices are never interpolated.</p><p>Actual dollars are your recorded net P&amp;L, including recorded commissions. Simulated dollars use your actual quantity with no modeled costs. Dollar comparisons use recorded net P&amp;L; R comparisons use actual gross price P&amp;L divided by initial dollar risk. Each rule uses its own assigned, eligible trades after page filters. Its comparison to your exits uses those same trades. The Actual row covers all eligible actual trades. Samples can differ between rules.</p></details>;
}
export function MoneyResult({ value }) {
  return <span className={moneyTone(value)}>{money(value)}</span>;
}
export function OutcomeTable({ replay }) {
  const t = replay.trade;
  const first = replay.rules?.flatMap(r => r.trades).find(r => r.eligible);
  const actualR = replay.rows?.[0]?.actual_r ?? first?.actual_r;
  const actualDollars = replay.rows?.[0]?.journal_trade.net_pnl;
  return <div className="xl-scroll"><table className="xl-table xl-simple-outcomes"><thead><tr><th>Exit</th><th>What happened</th><th>Result (R)</th><th>Result ($)</th></tr></thead><tbody>
    <tr><td>Actual</td><td>{t ? `Exited at ${clockTime(t.exited_at)} at $${number(t.exit)}` : 'Review the trade details'}{replay.scaled_entry?.warning && <small>{replay.scaled_entry.warning}</small>}</td><td>{Number.isFinite(actualR) ? resultText(actualR) : 'Not available'}</td><td className={moneyTone(actualDollars)}>{money(actualDollars)}</td></tr>
    {(replay.rules || []).map(r => { const value = r.trades[0]; return <tr key={r.rule_id ?? r.rule_hash}><td>{r.rule.name}</td><td>{value?.what_happened || (value?.reasons?.length ? value.reasons.map(reasonText).join(' ') : null) || (t?.stop == null ? 'Enter your stop to compare' : 'Price or trade details need review')}</td><td className={value?.pnl_r > 0 ? 'v3-pos' : value?.pnl_r < 0 ? 'v3-neg' : ''}>{resultText(value?.pnl_r)}</td><td><MoneyResult value={value?.pnl} /></td></tr>; })}
  </tbody></table></div>;
}
