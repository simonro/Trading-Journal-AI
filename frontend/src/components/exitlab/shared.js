import { useState } from 'react';
import { exitlabApi } from '../../api';
import './exitlab.css';

export const DEMO_LABEL = 'Synthetic trades, historical market prices';
export const number = (v, digits = 2) => Number.isFinite(v) ? (Number(v.toFixed(digits)) === 0 ? (0).toFixed(digits) : v.toFixed(digits)) : 'n/a';
export const signed = (v) => Number.isFinite(v) ? `${Number(v.toFixed(2)) >= 0 ? '+' : ''}${number(v)}` : 'n/a';
export const money = v => {
  if (!Number.isFinite(v)) return 'Not available';
  const rounded = Number(v.toFixed(2));
  return `${rounded < 0 ? '-' : '+'}$${Math.abs(rounded).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
};
export const moneyTone = v => !Number.isFinite(v) ? '' : Number(v.toFixed(2)) > 0 ? 'v3-pos' : Number(v.toFixed(2)) < 0 ? 'v3-neg' : '';
export const clockTime = ts => ts ? new Date(ts).toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', hour12: false }) : 'n/a';
const reasons = {
  vwap_touch_wrong_side_at_entry: 'Not applicable: a long must enter below VWAP, or a short above VWAP.',
  vwap_at_entry_unavailable: 'VWAP is not available from completed session bars at entry.',
  rs_rw_spy_minute_missing: 'Not evaluable: a SPY minute is missing. Load SPY prices; missing minutes are never interpolated.',
  rs_rw_stock_minute_missing: 'Not evaluable: a stock minute is missing from the RS/RW window.',
  rs_rw_warmup_insufficient: 'RS/RW needs 13 completed regular-hours 5-minute bars before entry, using the previous regular session when needed.',
  rs_rw_atr_unavailable: 'RS/RW cannot be calculated because a volatility denominator is zero.',
  missing_initial_stop: 'Stop loss is missing. Enter your journal stop below.',
  initial_stop_not_on_loss_side: 'Initial stop must be on the loss side. Review the journal stop loss.',
  incomplete_minute_coverage_through_close: 'Minute bars are missing. Fetch this session, then check coverage again.',
  fill_outside_bar_range: 'An actual fill is outside its minute range. Check feed, timestamps and symbol history.',
  initial_risk_under_two_ticks: 'Initial risk is under two ticks. Review the journal stop loss.',
  opening_fill_group_spans_minutes: 'Opening fills span minutes. Changing position size during the opening group needs finer simulation.',
  partial_open_minute_simulation_unsupported: 'Opening fill falls inside a minute. This version cannot replay it safely.',
  open_fill_not_at_bar_open: 'Opening fill differs from the minute open. Finer price data is needed for this replay.',
  not_stock: 'Only stock trades are supported. Options and futures are excluded.',
  requires_one_open_and_one_close_fill_group: 'This version needs one opening and one closing fill group.',
  not_completed_constant_quantity: 'The trade must be closed with the same opening and closing quantity.',
  not_completed_same_regular_session: 'Both fills must be in the same regular session.',
};
export function reasonText(r) {
  if (r.startsWith('regular_minute_missing|')) {
    const [, symbol, ts] = r.split('|');
    return `Not evaluable: ${symbol} ${clockTime(ts)} missing (${ts.slice(0, 10)}, New York). Load prices for the stock and SPY, including the previous regular session. Missing minutes are never interpolated.`;
  }
  return reasons[r] || `${r.replaceAll('_', ' ')}. Review the trade data or choose another rule.`;
}
export const needsPrices = reasons => (reasons || []).some(r => /coverage|warmup|minute_missing/.test(r));
export function LoadPrices({ row, reasons = [], onLoaded, disabled = false }) {
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  if (!row || !needsPrices(reasons)) return null;
  async function load() {
    setBusy(true); setError('');
    try {
      const day = row.journal_trade.date;
      const result = await exitlabApi.fetchBars({ symbol: row.journal_trade.ticker,
        start: `${day}T08:00:00Z`, end: `${day}T21:00:00Z`, feed: 'sip',
        include_warmup: true, refresh: true });
      if ([result.data, result.data.benchmark, result.data.warmup, result.data.warmup_benchmark]
        .some(item => item && item.feed !== 'sip')) throw new Error('SIP prices are unavailable for part of this request.');
      await onLoaded();
    } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  return <><button className="xl-trade-link" disabled={disabled || busy} onClick={load}>{busy ? 'Loading prices...' : 'Load prices'}</button>{error && <Notice error>{error}</Notice>}</>;
}
export function errorText(e) {
  const detail = e.response?.data?.detail || e.response?.data?.error;
  if (Array.isArray(detail)) return detail.map(x => `${x.loc?.slice(1).join(' / ')}: ${x.msg}`).join('; ');
  return typeof detail === 'string' ? `${detail}. Review the inputs and local data configuration, then retry.` : 'The calculation could not finish. Check that the local API is running, then retry.';
}
export function Notice({ children, error = false }) {
  return <div className={`xl-notice ${error ? 'xl-error' : ''}`} role={error ? 'alert' : 'status'}>{children}</div>;
}
export function DemoLabel({ show }) { return show ? <div className="xl-demo">{DEMO_LABEL}</div> : null; }
