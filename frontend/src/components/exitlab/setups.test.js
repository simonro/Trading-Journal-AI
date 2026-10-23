import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { exitlabApi } from '../../api';
import RuleBuilder from './RuleBuilder';
import SetupAssignments, { SetupSelect, SetupChips } from './SetupAssignment';
import TradeExits from './TradeExits';
import { summary, setupOf, historyLead } from './simple';

jest.mock('../../api', () => ({ exitlabApi: { saveRule: jest.fn(), assignRule: jest.fn(), simpleReplay: jest.fn() } }));
jest.mock('./ReplayChart', () => () => <div>Replay chart</div>);
beforeEach(() => jest.clearAllMocks());

test('picker groups names, labels auto codes, shows zero counts and searches above twelve chips', () => {
  const change = jest.fn();
  const grouped = [
    { group: 'strategy', label: 'Strategies', items: ['A', ...Array.from({ length: 11 }, (_, i) => `Strategy ${i}`)].map(name => ({ name, trades: 0, eligible: 0 })) },
    { group: 'auto', label: 'Auto setups', items: [{ name: 'A', trades: 8, eligible: 3 }, { name: 'UNKNOWN', trades: 1, eligible: 0 }] },
  ];
  render(<SetupSelect options={grouped} value={[{ group: 'strategy', name: 'A' }]} onChange={change} />);
  const strategies = screen.getByRole('region', { name: 'Strategies' });
  const autos = screen.getByRole('region', { name: 'Auto setups' });
  expect(within(strategies).getByRole('button', { name: 'A 0 trades · 0 eligible' })).toHaveAttribute('aria-pressed', 'true');
  fireEvent.click(within(autos).getByRole('button', { name: 'Flush Reclaim (A) 8 trades · 3 eligible' }));
  expect(change).toHaveBeenCalledWith([{ group: 'strategy', name: 'A' }, { group: 'auto', name: 'A' }]);
  expect(within(autos).getByText('UNKNOWN')).toBeVisible();
  fireEvent.change(screen.getByLabelText('Search setups'), { target: { value: 'flush' } });
  expect(within(strategies).queryAllByRole('button')).toHaveLength(0);
  expect(within(autos).getAllByRole('button')).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name: 'All setups' }));
  expect(change).toHaveBeenLastCalledWith(null);
});

test('small picker needs no search and assignment chips render typed values', () => {
  render(<><SetupSelect options={[]} value={[{ group: 'strategy', name: 'Saved unused' }]} onChange={() => {}} />
    <SetupChips value={[{ group: 'auto', name: 'B_LITE' }, { group: 'strategy', name: 'VWAP Cross' }]} /></>);
  expect(screen.queryByLabelText('Search setups')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Saved unused 0 trades · 0 eligible' })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByText('Leader Long Lite (B_LITE)')).toBeVisible();
  expect(screen.getByText('VWAP Cross')).toBeVisible();
  expect(setupOf({ journal_trade: { setup: 'A' } })).toBe('Flush Reclaim (A)');
  expect(setupOf({ journal_trade: { setup: 'A', strategy: 'VWAP Cross' } })).toBe('VWAP Cross');
});
const options = [{ group: 'strategy', label: 'Strategies', items: [{ name: 'LOD Reversal', trades: 3, eligible: 2 }, { name: 'Opening Drive', trades: 2, eligible: 1 }] }, { group: 'auto', label: 'Auto setups', items: [] }];


test('builder saves VWAP tranche and fixed five-minute RS confirmation with setup assignment', async () => {
  exitlabApi.saveRule.mockResolvedValue({ data: { id: 8 } });
  const saved = jest.fn();
  render(<RuleBuilder onSaved={saved} setupOptions={options} />);
  fireEvent.click(screen.getByText('Build an exit rule'));
  fireEvent.click(screen.getByRole('button', { name: 'LOD Reversal 3 trades · 2 eligible' }));
  fireEvent.click(screen.getByRole('button', { name: 'Target at VWAP touch' }));
  fireEvent.click(screen.getByRole('button', { name: 'RS/RW vs SPY' }));
  expect(screen.queryByLabelText('Indicator timeframe')).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Confirmation bars'), { target: { value: '2' } });
  expect(screen.getByText(/Your rule:/)).toHaveTextContent('long: exit when RS turns to RW');
  expect(screen.getByText(/Your rule:/)).toHaveTextContent('2 consecutive completed 5-minute bars');
  fireEvent.click(screen.getByRole('button', { name: 'Save rule' }));
  await waitFor(() => expect(saved).toHaveBeenCalledWith(8));
  expect(exitlabApi.saveRule).toHaveBeenCalledWith(expect.objectContaining({
    applies_to_setups: [{ group: 'strategy', name: 'LOD Reversal' }], tranches: [{ kind: 'vwap_touch', fraction: 1 }],
    indicator: { kind: 'rs_rw', timeframe: 5, confirmation: 2 },
  }));
});

test('preset assignment is editable without sending a new policy', async () => {
  exitlabApi.assignRule.mockResolvedValue({ data: {} });
  const saved = jest.fn();
  render(<SetupAssignments rules={[{ id: 1, name: '1R target', applies_to_setups: null }]} options={options} onSaved={saved} />);
  fireEvent.click(screen.getByRole('button', { name: 'LOD Reversal 3 trades · 2 eligible' }));
  fireEvent.click(screen.getByRole('button', { name: 'Opening Drive 2 trades · 1 eligible' }));
  fireEvent.click(screen.getByRole('button', { name: 'Save assignment for 1R target' }));
  await waitFor(() => expect(saved).toHaveBeenCalled());
  expect(exitlabApi.assignRule).toHaveBeenCalledWith(1, [{ group: 'strategy', name: 'LOD Reversal' }, { group: 'strategy', name: 'Opening Drive' }]);
  expect(exitlabApi.saveRule).not.toHaveBeenCalled();
});

test('Setup page filter AND rule assignment retain separate samples and paired actual baselines', () => {
  const rows = [1, 2, 3].map(id => ({ trade_id: id, setup_name: id === 1 ? 'Opening Drive' : 'LOD Reversal',
    journal_trade: { ticker: 'TEST', date: '2026-10-16', net_pnl: id * 10 }, reasons: [], actual_r: id / 10, stop_price: 99 }));
  const outcome = id => ({ trade_id: id, eligible: true, actual_r: id / 10, pnl_r: 1, pnl: 100 });
  const result = { setup_scoped: true, rows, rules: [
    { rule_id: 1, rule: { name: 'LOD VWAP' }, applies_to_setups: [{ group: 'strategy', name: 'LOD Reversal' }], trades: [outcome(2), outcome(3)] },
    { rule_id: 2, rule: { name: 'Drive RS' }, applies_to_setups: [{ group: 'strategy', name: 'Opening Drive' }], trades: [outcome(1)] },
  ] };
  const all = summary(result).table;
  expect(all.map(r => r.n)).toEqual([3, 2, 1]);
  expect(all[1].delta).toBeCloseTo(.75);
  expect(all[1].deltaDollars).toBeCloseTo(75);
  expect(historyLead(all)).toContain('Samples differ between rows');
  const filtered = summary(result, { setup: 'LOD Reversal' }).table;
  expect(filtered.map(r => r.n)).toEqual([2, 2, 0]);
  expect(filtered[2].avg).toBeNull();
  expect(filtered[1].setups).toEqual([{ group: 'strategy', name: 'LOD Reversal' }]);
  expect(historyLead(summary({ ...result, rules: [result.rules[0]] }).table)).toContain('LOD VWAP averaged');
  expect(setupOf({ journal_trade: { setup: 'NONE', strategy: 'Old tag' } })).toBe('Old tag');
  expect(setupOf({ journal_trade: { strategy: 'Trend Pullback' } })).toBe('Trend Pullback');
});

test('LOD Exits shows assigned rules, a plain not-applicable reason and editable assignments', async () => {
  exitlabApi.simpleReplay.mockResolvedValue({ data: {
    trade: null, bars: [], reasons: [], setup_options: options,
    rows: [{ trade_id: 1, setup_name: 'LOD Reversal', stop_price: 99, actual_r: .5, journal_trade: { net_pnl: 50 } }],
    assignment_rules: [{ id: 1, name: '1R target', applies_to_setups: [{ group: 'strategy', name: 'Opening Drive' }] }],
    rules: [{ rule_id: 8, rule: { name: 'LOD VWAP' }, trades: [{ eligible: false, reasons: ['vwap_touch_wrong_side_at_entry'] }] }],
  } });
  render(<TradeExits trade={{ id: 1 }} />);
  await screen.findByText('LOD VWAP');
  const table = screen.getByRole('table');
  expect(within(table).getAllByRole('row')).toHaveLength(3);
  expect(within(table).queryByText('1R target')).not.toBeInTheDocument();
  expect(screen.getByText(/Not applicable: a long must enter below VWAP/)).toBeVisible();
  expect(screen.getByText(/Showing rules for: LOD Reversal/)).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
  expect(screen.getByRole('button', { name: 'Opening Drive 2 trades · 1 eligible' })).toHaveAttribute('aria-pressed', 'true');
});
