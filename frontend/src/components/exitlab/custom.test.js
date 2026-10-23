import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import ExitLab from '../ExitLab';
import TradeExits from './TradeExits';
import { summary, historyLead } from './simple';
import { exitlabApi } from '../../api';

jest.mock('../../api', () => ({ exitlabApi: { history: jest.fn(), simpleReplay: jest.fn(), rules: jest.fn(), saveRule: jest.fn(), showRule: jest.fn(), } }));
jest.mock('./ReplayChart', () => () => <div>Historical chart</div>);
const name = 'Half at 1R, rest EMA 9';
const trade = { id: 1, ticker: 'TEST', side: 'LONG', date: '2026-10-01', net_pnl: 25 };
const rules = ['1R target', '2R target', 'Hold to close', 'Breakeven after +1R', name].map((name, i) => ({ id: i + 1, name, is_preset: i < 4, show_in_tables: true }));
const data = (show = true) => ({
  costs: { slippage_bps: 0, per_share: 0 },
  custom_rules: [{ id: 5, name, show_in_tables: show }],
  rows: [{ trade_id: 1, stop_price: 99, reasons: [], journal_trade: trade }],
  rules: rules.filter(r => r.is_preset || show).map(r => ({ rule_id: r.id, is_preset: r.is_preset, rule_hash: String(r.id), rule: { name: r.name },
    trades: [{ trade_id: 1, eligible: true, actual_r: .25, fills: [], pnl_r: r.is_preset ? .5 : 1, pnl: r.is_preset ? 50 : 100,
      what_happened: r.is_preset ? 'Stop never hit, closed at 16:00' : 'Sold 50% at +1R 10:09, rest on EMA 9 close 10:41' }] })),
});
beforeEach(() => {
  localStorage.clear();
  exitlabApi.history.mockResolvedValue({ data: data() });
  exitlabApi.rules.mockResolvedValue({ data: rules });
  exitlabApi.showRule.mockResolvedValue({ data: {} });
});
const workspace = onOpenDetail => render(<ExitLab accountId={1} accounts={[{ id: 1 }]} onOpenDetail={onOpenDetail || jest.fn()} />);
const summaryTable = () => screen.getByRole('columnheader', { name: 'Avg $' }).closest('table');

test('custom rule follows five standard rows, ranks in the lead and opens its trades', async () => {
  const open = jest.fn(); workspace(open);
  fireEvent.click(await screen.findByRole('button', { name, exact: true }));
  const rows = within(summaryTable()).getAllByRole('row');
  expect(rows).toHaveLength(7);
  expect(within(rows[6]).getAllByRole('cell').map(c => c.textContent)).toEqual([name + 'All setups', '1', '+1.00', '+$100.00', '100.0%', '+1.00', '+$100.00', '+0.75', '+$75.00']);
  expect(screen.getByText(/Across 1 trades with a stop/)).toHaveTextContent(`${name} averaged 0.75R`);
  expect(screen.getByText(/This describes your history/)).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'TEST / #1' }));
  expect(open).toHaveBeenCalledWith(trade, [trade], 'Exits');
});

test('simple switch hides a row, keeps its chip and counts, and restores it', async () => {
  workspace();
  const toggle = await screen.findByRole('switch', { name: `Show in tables: ${name}` });
  expect(toggle).toBeChecked();
  exitlabApi.history.mockResolvedValue({ data: data(false) });
  fireEvent.click(toggle);
  await waitFor(() => expect(exitlabApi.showRule).toHaveBeenCalledWith(5, false));
  await waitFor(() => expect(within(summaryTable()).getAllByRole('row')).toHaveLength(6));
  expect(screen.getByRole('switch')).not.toBeChecked();
  exitlabApi.history.mockResolvedValue({ data: data() });
  fireEvent.click(screen.getByRole('switch'));
  await waitFor(() => expect(within(summaryTable()).getAllByRole('row')).toHaveLength(7));
  expect(screen.getByRole('switch')).toBeChecked();
});

test('Advanced switch synchronizes simple table without retired controls', async () => {
  localStorage.setItem('exitlab-advanced', 'true'); workspace();
  await screen.findByText('Saved rules');
  const switches = screen.getAllByRole('switch');
  expect(switches).toHaveLength(2);
  exitlabApi.history.mockResolvedValue({ data: data(false) });
  fireEvent.click(switches[1]);
  await waitFor(() => expect(screen.getAllByRole('switch').every(s => !s.checked)).toBe(true));
  expect(screen.queryByText('Forward testing')).not.toBeInTheDocument();
  expect(screen.queryByText(/Rules tried:/)).not.toBeInTheDocument();
  expect(screen.queryByText('Stop provenance and ambiguity detail')).not.toBeInTheDocument();
  expect(within(summaryTable()).getAllByRole('row')).toHaveLength(6);
});

test('failed visibility save keeps the rule visible and shows the error', async () => {
  workspace();
  exitlabApi.showRule.mockRejectedValueOnce(new Error('offline'));
  fireEvent.click(await screen.findByRole('switch'));
  expect(await screen.findByRole('alert')).toHaveTextContent('Check that the local API is running');
  expect(screen.getByRole('switch')).toBeChecked();
  expect(within(summaryTable()).getAllByRole('row')).toHaveLength(7);
});

test('saving from Advanced automatically refreshes the simple table without a compare click', async () => {
  localStorage.setItem('exitlab-advanced', 'true');
  const initial = { ...data(false), custom_rules: [], };
  exitlabApi.history.mockResolvedValueOnce({ data: initial });
  exitlabApi.rules.mockResolvedValueOnce({ data: rules.slice(0,4) });
  exitlabApi.saveRule.mockResolvedValue({ data: { id: 5 } });
  workspace();
  fireEvent.click(await screen.findByText(/Build an exit rule/, { selector: 'summary' }));
  fireEvent.click(screen.getByRole('button', { name: 'Save rule' }));
  expect(await screen.findByRole('button', { name, exact: true })).toBeVisible();
  expect(screen.getAllByRole('switch').every(s => s.checked)).toBe(true);
  expect(screen.queryByRole('button', { name: 'Compare custom rules' })).not.toBeInTheDocument();
  expect(exitlabApi.history).toHaveBeenLastCalledWith({ account_id: 1 });
});

test('trade Exits includes the saved fill story and scalar R and dollars', async () => {
  const replay = { ...data(), bars: [], reasons: [], trade: { entry: 100, exit: 100.25, stop: 99 } };
  replay.rules[4].trades[0] = { ...replay.rules[4].trades[0], pnl_r: -1, pnl: -100, };
  exitlabApi.simpleReplay.mockResolvedValue({ data: replay });
  render(<TradeExits trade={trade} />);
  const row = (await screen.findByText(name, { exact: true })).closest('tr');
  expect(row).toHaveTextContent('Sold 50% at +1R 10:09, rest on EMA 9 close 10:41');
  expect(row).toHaveTextContent('-1.00R');
  expect(row).toHaveTextContent('-$100.00');
});

test('custom eligibility and filters use the common sample and duplicate policy hashes keep distinct rows', () => {
  const result = data();
  result.rules[4].rule_hash = result.rules[0].rule_hash;
  expect(new Set(summary(result).table.map(r => r.key)).size).toBe(6);
  expect(summary(result, { symbol: 'NONE' }).table.every(r => r.n === 0)).toBe(true);
  result.rules[4].trades[0].eligible = false;
  expect(summary(result).table.every(r => r.n === 0)).toBe(true);
  expect(summary(result).excluded).toHaveLength(1);
  expect(historyLead(summary(result).table)).toMatch(/No trades to compare/);
});
