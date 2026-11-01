import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import ExitLab from '../ExitLab';
import TradeExits from './TradeExits';
import RuleBuilder from './RuleBuilder';
import { SIMPLE_NAMES, summary, exclusionLine, resultText } from './simple';
import { excursionText } from '../../utils/excursions';
import { exitlabApi } from '../../api';

jest.mock('../../api', () => ({ exitlabApi: { history: jest.fn(), simpleReplay: jest.fn(), rules: jest.fn(), saveRule: jest.fn(), stop: jest.fn(), fetchBars: jest.fn(), saveSettings: jest.fn(), loadMissing: jest.fn(), loadMissingStatus: jest.fn() } }));
jest.mock('./ReplayChart', () => () => <div>Historical chart</div>);
const trade = { id: 1, account_id: 1, ticker: 'FIXTURE', side: 'LONG', source: 'demo', date: '2026-10-01', setup: 'Breakout', net_pnl: 47.5 };
const ruleList = SIMPLE_NAMES.slice(1).map((name, i) => ({ id: i + 1, name, is_preset: 1, hash: String(i) }));
const fill = { ts: '2026-10-01T14:07:00Z', price: 101, quantity: 100, fee: 0, reason: 'target', timing: 'within_minute' };
const outcome = { trade_id: 1, eligible: true, reasons: [], session: trade.date, pnl_r: -1, pnl: -100, delta_r: -1.5, actual_r: .5, fills: [{ ...fill, price: 99, reason: 'stop' }], what_happened: 'Stop hit at 10:07' };
const row = { trade_id: 1, stop_price: 99, reasons: [], journal_trade: trade };
const history = { costs: { slippage_bps: 0, per_fill: 0, per_share: 0 }, rows: [row], rules: ruleList.map(r => ({ rule: { name: r.name }, rule_hash: r.hash, trades: [outcome] })) };
const replay = { ...history, reasons: [], trade: { entry: 100, exit: 100.5, stop: 99, entered_at: '2026-10-01T14:00:00Z', exited_at: '2026-10-01T14:05:00Z' }, bars: [{}] };
beforeEach(() => {
  localStorage.clear();
  exitlabApi.history.mockResolvedValue({ data: history });
  exitlabApi.simpleReplay.mockResolvedValue({ data: replay });
  exitlabApi.rules.mockResolvedValue({ data: ruleList });
  exitlabApi.saveRule.mockResolvedValue({ data: { id: 3 } });
  exitlabApi.stop.mockResolvedValue({ data: { stop_loss: 99 } });
  exitlabApi.saveSettings.mockResolvedValue({ data: {} });
});
const workspace = props => render(<ExitLab accountId={1} accounts={[{ id: 1, name: 'Fixture' }]} onOpenDetail={jest.fn()} {...props} />);

test('simple history calculates on open and has exactly five rows without research controls', async () => {
  workspace();
  const table = await screen.findByRole('table');
  expect(within(table).getAllByRole('row')).toHaveLength(6);
  expect(within(table).getAllByRole('button').map(b => b.textContent)).toEqual(SIMPLE_NAMES);
  expect(within(table).getAllByRole('columnheader').map(c => c.textContent)).toEqual(['Exit', 'Trades', 'Avg R', 'Avg $', 'Win rate', 'Total R', 'Total $', 'vs your exitsavg R per trade', 'vs your exits ($)avg $ per trade']);
  const cells = within(within(table).getAllByRole('row')[2]).getAllByRole('cell');
  expect(cells[3]).toHaveTextContent('-$100.00');
  expect(cells[6]).toHaveTextContent('-$100.00');
  expect(cells[8]).toHaveTextContent('-$147.50');
  expect(cells[8]).toHaveClass('v3-neg');
  expect(exitlabApi.history).toHaveBeenCalledWith({ account_id: 1 });
  expect(screen.getByText(/Across 1 trades with a stop/)).toBeVisible();
  expect(screen.getByText('Across all your trades. This describes your history; it does not predict future results.')).toBeVisible();
  expect(screen.queryByText('Rules tried: 4')).not.toBeInTheDocument();
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
});

test('trade auto replay shows one stop-first result with no removed controls', async () => {
  render(<TradeExits trade={trade} />);
  expect(await screen.findByText('Historical chart')).toBeVisible();
  expect(exitlabApi.simpleReplay).toHaveBeenCalledWith(1);
  expect(screen.getAllByText('Stop hit at 10:07')).toHaveLength(4);
  expect(screen.getAllByText('-1.00R')).toHaveLength(4);
  expect(screen.getByText('+0.50R actual')).toBeVisible();
  expect(screen.getAllByRole('row')).toHaveLength(6);
  for (const text of ['Cost assumptions', 'Stop provenance', 'Every modeled fill', 'Replay selected exits', 'Holdout']) expect(screen.queryByText(text)).not.toBeInTheDocument();
});

test('missing stop is one inline field, saves only price and recalculates', async () => {
  exitlabApi.simpleReplay.mockResolvedValueOnce({ data: { ...replay, trade: { ...replay.trade, stop: null }, reasons: ['missing_initial_stop'], rules: history.rules.map(r => ({ ...r, trades: [{ eligible: false }] })) } });
  render(<TradeExits trade={trade} />);
  fireEvent.change(await screen.findByLabelText('Enter your stop'), { target: { value: '99' } });
  expect(screen.queryByLabelText('Evidence reference')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Save stop' }));
  await waitFor(() => expect(exitlabApi.stop).toHaveBeenCalledWith(1, { price: 99 }));
  expect(await screen.findByText('+0.50R actual')).toBeVisible();
  expect(exitlabApi.simpleReplay).toHaveBeenCalledTimes(2);
});

test('rule list opens and trade opens its Exits tab', async () => {
  const open = jest.fn(); workspace({ onOpenDetail: open });
  fireEvent.click(await screen.findByRole('button', { name: '1R target' }));
  fireEvent.click(await screen.findByRole('button', { name: 'FIXTURE / #1' }));
  expect(open).toHaveBeenCalledWith(trade, [trade], 'Exits');
});

test('filters recompute the same eligible sample and keep conservative means', async () => {
  const data = summary(history);
  expect(data.table[0].avg).toBe(.5);
  expect(data.table[1]).toMatchObject({ avg: -1, delta: -1.5, total: -1, win: 0 });
  expect(summary(history, { symbol: 'OTHER' }).table[1].avg).toBeNull();
  expect(summary(history, { setup: 'Other' }).table[0].n).toBe(0);
  expect(summary(history, { side: 'SHORT' }).table[0].n).toBe(0);
  expect(summary(history, { from: '2026-10-02' }).table[0].n).toBe(0);
  expect(summary(history, { to: '2026-09-30' }).table[0].n).toBe(0);
  workspace();
  fireEvent.change(await screen.findByLabelText('From'), { target: { value: '2026-10-02' } });
  expect(screen.getByText(/No trades to compare yet/)).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }));
  expect(screen.getByText(/Across 1 trades with a stop/)).toBeVisible();
});

test('exclusions count no stop and missing data once and provide Add stop links', async () => {
  const missing = { ...row, trade_id: 2, stop_price: null, journal_trade: { ...trade, id: 2 }, reasons: ['missing_initial_stop'] };
  const prices = { ...row, trade_id: 3, journal_trade: { ...trade, id: 3 }, reasons: ['incomplete_minute_coverage_through_close'] };
  expect(exclusionLine([...Array(14).fill(missing), ...Array(7).fill(prices)])).toBe('21 trades not included: 14 have no stop, 7 have missing price data.');
  exitlabApi.history.mockResolvedValue({ data: { ...history, rows: [row, missing, prices] } });
  const open = jest.fn(); workspace({ onOpenDetail: open });
  expect(await screen.findByText(/2 trades not included: 1 have no stop, 1 have missing price data/)).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Show' }));
  fireEvent.click(screen.getByRole('button', { name: 'Add stop' }));
  expect(open).toHaveBeenCalledWith(missing.journal_trade, expect.any(Array), 'Exits');
});



test('Advanced has no journal cost controls or sensitivity even for legacy results', async () => {
  localStorage.setItem('exitlab-advanced', 'true'); workspace();
  await screen.findByText('Saved rules');
  expect(screen.queryByText('Journal costs')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Slippage (bp)')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Commission per share ($)')).not.toBeInTheDocument();
  expect(screen.queryByText(/Slippage check:/)).not.toBeInTheDocument();
  expect(exitlabApi.saveSettings).not.toHaveBeenCalled();
  expect(screen.getByText(/Rule simulations always use zero modeled costs and zero slippage/)).toBeInTheDocument();
});

test('Exits loads a missing SPY minute on open, both symbols with prior-session warm-up', async () => {
  const gap = 'regular_minute_missing|SPY|2026-10-01T10:37:00-04:00';
  exitlabApi.simpleReplay.mockResolvedValueOnce({ data: { ...replay,
    rules: [{ rule: { name: 'RS/RW' }, rule_hash: 'rs', trades: [{ trade_id: 1, eligible: false, reasons: [gap] }] }] } });
  exitlabApi.fetchBars.mockResolvedValue({ data: { feed: 'sip', benchmark: { feed: 'sip' }, warmup: { feed: 'sip' }, warmup_benchmark: { feed: 'sip' } } });
  render(<TradeExits trade={trade} />);
  await waitFor(() => expect(exitlabApi.fetchBars).toHaveBeenCalledWith({
    symbol: 'FIXTURE', start: '2026-10-01T08:00:00Z', end: '2026-10-01T21:00:00Z', feed: 'sip', include_warmup: true, refresh: false }));
  await waitFor(() => expect(exitlabApi.simpleReplay).toHaveBeenCalledTimes(2));
  expect(await screen.findByText('+0.50R actual')).toBeVisible();
  expect(screen.queryByText(/SPY 10:37 missing/)).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Load prices' })).not.toBeInTheDocument();
});

test('history rule exclusion offers Load prices without opening Advanced', async () => {
  const gap = 'regular_minute_missing|FIXTURE|2026-09-30T15:25:00-04:00';
  exitlabApi.history.mockResolvedValueOnce({ data: { ...history, setup_scoped: true,
    rows: [{ ...row, actual_r: .5 }], rules: [{ rule: { name: 'RS/RW' }, rule_hash: 'rs',
      trades: [{ trade_id: 1, eligible: false, reasons: [gap] }] }] } });
  exitlabApi.fetchBars.mockResolvedValue({ data: { feed: 'sip' } });
  workspace();
  fireEvent.click(await screen.findByText('1 not evaluable'));
  expect(screen.getByText(/FIXTURE 15:25 missing/)).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Load prices' }));
  await waitFor(() => expect(exitlabApi.history).toHaveBeenCalledTimes(2));
  expect(exitlabApi.fetchBars).toHaveBeenCalledWith(expect.objectContaining({ symbol: 'FIXTURE', include_warmup: true }));
});

test('failed previous-session fetch remains visible and can be retried', async () => {
  exitlabApi.simpleReplay.mockResolvedValue({ data: { ...replay, reasons: ['regular_minute_missing|SPY|2026-09-30T15:25:00-04:00'] } });
  exitlabApi.fetchBars.mockRejectedValueOnce({ response: { data: { detail: 'Previous session unavailable' } } });
  render(<TradeExits trade={trade} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('Previous session unavailable');
  expect(screen.getByRole('button', { name: 'Load prices' })).toBeEnabled();
  expect(exitlabApi.fetchBars).toHaveBeenCalledTimes(1);
});

test('failed history request can retry; account choice remains explicit', async () => {
  exitlabApi.history.mockRejectedValueOnce(new Error('offline')); workspace();
  expect(await screen.findByRole('alert')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(await screen.findByRole('table')).toBeVisible();
});

test('scalar results preserve signs and missing outcomes stay unavailable', () => {
  expect(resultText(-1)).toBe('-1.00R');
  expect(resultText(-.25)).toBe('-0.25R');
  expect(resultText(null)).toBe('Not available');
});

test('mixed accounts require an explicit account selection', () => {
  const select = jest.fn(); workspace({ accountId: null, accounts: [{ id: 1, name: 'One' }, { id: 2, name: 'Two' }], onSelectAccount: select });
  fireEvent.change(screen.getByRole('combobox', { name: 'Exit Lab account' }), { target: { value: '2' } });
  expect(select).toHaveBeenCalledWith(2);
  expect(exitlabApi.history).not.toHaveBeenCalled();
});


test('rule builder sends tranches, R-step, ATR and five-minute indicator with a plain summary', async () => {
  const onSaved = jest.fn(); render(<RuleBuilder onSaved={onSaved} />);
  fireEvent.click(screen.getByText('Build an exit rule', { exact: false, selector: 'summary' }));
  fireEvent.click(screen.getByRole('button', { name: '2 tranches' }));
  fireEvent.click(screen.getByRole('button', { name: 'R-step' }));
  fireEvent.click(screen.getByRole('button', { name: 'ATR', exact: true }));
  fireEvent.click(screen.getByRole('button', { name: 'EMA 9 close' }));
  fireEvent.change(screen.getByLabelText('Indicator timeframe'), { target: { value: '5' } });
  expect(screen.getByText(/Your rule: close 50% at \+1R; close 50% at session close/)).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Save rule' }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledWith(3));
  expect(exitlabApi.saveRule).toHaveBeenCalledWith(expect.objectContaining({ tranches: [{ kind: 'target', fraction: 0.5, r: 1 }, { kind: 'close', fraction: 0.5 }], r_steps: [{ at_r: 1, stop_r: 0 }], atr: { length: 14, multiple: 2 }, indicator: { kind: 'ema9', timeframe: 5 } }));
  expect(screen.getByRole('button', { name: /N-bar/ })).toBeDisabled();
  expect(screen.getByRole('button', { name: /RS\/RW/ })).toBeEnabled();
});

test('rule builder rejects allocations that do not total 100%', async () => {
  render(<RuleBuilder onSaved={jest.fn()} />);
  fireEvent.click(screen.getByText('Build an exit rule', { exact: false, selector: 'summary' }));
  fireEvent.change(screen.getByLabelText('Size (%)'), { target: { value: '50' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save rule' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Tranche sizes must total 100%');
  expect(exitlabApi.saveRule).not.toHaveBeenCalled();
});



test('journal excursion text ignores retired ranges', () => {
  expect(excursionText({ mfe_pct: null, mfe_pct_low: .3, mfe_pct_high: .5 }, 'mfe_pct')).toBeNull();
  expect(excursionText({ mae_pct: -.25 }, 'mae_pct')).toBe('-0.25%');
  expect(excursionText({}, 'mfe_pct')).toBeNull();
});
