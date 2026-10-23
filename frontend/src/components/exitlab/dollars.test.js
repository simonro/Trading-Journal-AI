import { render, screen, within } from '@testing-library/react';
import { MoneyResult, OutcomeTable, summary } from './simple';
import { money } from './shared';

test('money matches signed journal dollars, separators and rounded zero', () => {
  expect(money(1234.56)).toBe('+$1,234.56');
  expect(money(-1234.56)).toBe('-$1,234.56');
  expect(money(-0.00001)).toBe('+$0.00');
  for (const value of [null, undefined, NaN, Infinity]) expect(money(value)).toBe('Not available');
});

test('single dollar result shows the correct loss color', () => {
  const { container } = render(<MoneyResult value={-61} />);
  expect(container).toHaveTextContent('-$61.00');
  expect(screen.queryByText('+$61.00')).not.toBeInTheDocument();
  expect(screen.getByText('-$61.00')).toHaveClass('v3-neg');
});

test('actual dollars stay recorded net even with modeled costs or a missing stop', () => {
  const replay = { rows: [{ journal_trade: { net_pnl: -12.34 } }], rules: [{ rule_hash: 'one', rule: { name: '1R target' },
    trades: [{ eligible: true, actual_r: .5, actual_pnl_modeled_costs: 50, pnl_r: .9, pnl: 90 }] }] };
  const { rerender } = render(<OutcomeTable replay={replay} />);
  expect(screen.getByRole('columnheader', { name: 'Result ($)' })).toBeVisible();
  let cells = within(screen.getAllByRole('row')[1]).getAllByRole('cell');
  expect(cells[2]).toHaveTextContent('+0.50R');
  expect(cells[3]).toHaveTextContent('-$12.34');
  expect(cells[3]).toHaveClass('v3-neg');
  expect(screen.getByText('+$90.00')).toHaveClass('v3-pos');
  rerender(<OutcomeTable replay={{ ...replay, rules: [{ ...replay.rules[0], trades: [{ eligible: false, pnl: null, pnl_r: null }] }] }} />);
  cells = within(screen.getAllByRole('row')[1]).getAllByRole('cell');
  expect(cells[2]).toHaveTextContent('Not available');
  expect(cells[3]).toHaveTextContent('-$12.34');
  expect(within(screen.getAllByRole('row')[2]).getAllByRole('cell')[3]).toHaveTextContent('Not available');
});

const history = () => ({
  rows: [
    { trade_id: 1, reasons: [], journal_trade: { ticker: 'ONE', net_pnl: 45 } },
    { trade_id: 2, reasons: [], journal_trade: { ticker: 'TWO', net_pnl: -210 } },
    { trade_id: 3, reasons: [], journal_trade: { ticker: 'EXCLUDED', net_pnl: 9999 } },
  ],
  rules: ['one', 'two'].map((key, i) => ({ rule_hash: key, rule: { name: key }, trades: [
    { trade_id: 1, eligible: true, actual_r: .5, pnl_r: 1, pnl: 100 },
    { trade_id: 2, eligible: true, actual_r: -1, pnl_r: -1, pnl: -200 },
    { trade_id: 3, eligible: i === 0, actual_r: 100, pnl_r: 100, pnl: 10000 },
  ] })),
});

test('dollar summaries sum actual-sized trades on the filtered common sample using stop-first outcomes', () => {
  const all = summary(history()).table;
  expect(all[0]).toMatchObject({ n: 2, avgDollars: -82.5, totalDollars: -165 });
  expect(all[1]).toMatchObject({ n: 2, avg: 0, total: 0, avgDollars: -50, totalDollars: -100, deltaDollars: 32.5 });
  expect(summary(history(), { symbol: 'ONE' }).table[1]).toMatchObject({ n: 1, avgDollars: 100, totalDollars: 100, deltaDollars: 55 });
  expect(summary(history(), { symbol: 'TWO' }).table[1]).toMatchObject({ n: 1, avgDollars: -200, deltaDollars: 10 });
  expect(summary(history(), { symbol: 'NONE' }).table[1]).toMatchObject({ n: 0, avgDollars: null, totalDollars: null, deltaDollars: null });
});

test('missing recorded P&L never silently becomes zero or changes the dollar sample', () => {
  const result = history(); result.rows[1].journal_trade.net_pnl = null;
  expect(summary(result).table[0]).toMatchObject({ n: 2, avgDollars: null, totalDollars: null });
  expect(summary(result).table[1]).toMatchObject({ avgDollars: -50, deltaDollars: null });
});
