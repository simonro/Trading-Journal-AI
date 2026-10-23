import { historyLead, summary } from './simple';
import { signed } from './shared';

const table = deltas => [
  { key: 'actual', name: 'Actual', n: 195, delta: null },
  ...['1R target', '2R target', 'Hold to close', 'Breakeven after +1R'].map((name, i) => ({ key: String(i), name, n: 195, delta: deltas[i] })),
];

test('history lead names the displayed largest improvement first and smallest last', () => {
  expect(historyLead(table([.08, .13, .12, .10]))).toBe('Across 195 trades with a stop, 2R target averaged 0.13R per trade more than your actual exits; 1R target averaged 0.08R per trade more than your actual exits.');
});

test('ties at both extremes use table rounding and name every tied rule', () => {
  const rows = table([.081, .134, .126, .084]);
  expect(rows.slice(1).map(row => signed(row.delta))).toEqual(['+0.08', '+0.13', '+0.13', '+0.08']);
  expect(historyLead(rows)).toBe('Across 195 trades with a stop, 2R target and Hold to close each averaged 0.13R per trade more than your actual exits; 1R target and Breakeven after +1R each averaged 0.08R per trade more than your actual exits.');
});

test('negative comparisons rank by value rather than absolute magnitude', () => {
  expect(historyLead(table([-.20, -.13, -.12, -.10]))).toBe('Across 195 trades with a stop, Breakeven after +1R averaged 0.10R per trade less than your actual exits; 1R target averaged 0.20R per trade less than your actual exits.');
});

test('all rules tied at displayed zero appear once without a false gain or loss', () => {
  expect(historyLead(table([-.004, .004, 0, -.001]))).toBe('Across 195 trades with a stop, 1R target, 2R target, Hold to close and Breakeven after +1R each averaged the same R per trade as your actual exits (0.00R difference).');
});

test('lead follows the filtered common-sample table rather than rule order', () => {
  const result = {
    rows: [1, 2].map(id => ({ trade_id: id, reasons: [], journal_trade: { ticker: id === 1 ? 'ONE' : 'TWO' } })),
    rules: ['1R target', '2R target', 'Hold to close'].map((name, i) => ({ rule: { name }, rule_hash: name,
      trades: [1, 2].map(id => ({ trade_id: id, eligible: true, actual_r: .5, pnl_r: id === 1 ? [.58, .63, .62][i] : [.8, .6, .7][i] })),
    })),
  };
  expect(historyLead(summary(result, { symbol: 'ONE' }).table)).toBe('Across 1 trades with a stop, 2R target averaged 0.13R per trade more than your actual exits; 1R target averaged 0.08R per trade more than your actual exits.');
  expect(historyLead(summary(result, { symbol: 'TWO' }).table)).toBe('Across 1 trades with a stop, 1R target averaged 0.30R per trade more than your actual exits; 2R target averaged 0.10R per trade more than your actual exits.');
  expect(historyLead(summary(result, { symbol: 'NONE' }).table)).toMatch(/^No trades to compare yet/);
});

test('unavailable comparisons are omitted and a sole valid rule appears once', () => {
  expect(historyLead(table([null, .13, undefined, NaN]))).toBe('Across 195 trades with a stop, 2R target averaged 0.13R per trade more than your actual exits.');
  expect(historyLead([])).toMatch(/^No trades to compare yet/);
});
