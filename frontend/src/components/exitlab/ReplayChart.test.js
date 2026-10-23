import { render } from '@testing-library/react';
import { createChart } from 'lightweight-charts';
import ReplayChart, { customExitMarkers } from './ReplayChart';

jest.mock('lightweight-charts', () => ({ createChart: jest.fn(), LineStyle: { Dashed: 2 } }));

test('scaled entries mark every execution at its own price with the average Entry line', () => {
  global.ResizeObserver = class { observe() {} disconnect() {} };
  const points = [], lines = [], markers = [];
  createChart.mockReturnValue({
    addCandlestickSeries: () => ({ setData() {}, createPriceLine: line => lines.push(line) }),
    addLineSeries: () => ({ setData: data => points.push(...data), setMarkers: data => markers.push(...data) }),
    timeScale: () => ({ setVisibleLogicalRange() {} }), applyOptions() {}, remove() {},
  });
  const fills = [{ ts: '2026-10-16T13:30:10Z', price: 100, quantity: 25 }, { ts: '2026-10-16T13:31:20Z', price: 102, quantity: 75 }];
  render(<ReplayChart replay={{
    trade: { entered_at: fills[0].ts, exited_at: '2026-10-16T13:32:00Z', entry: 101.5, exit: 104, stop: 99, direction: 1 },
    scaled_entry: { fills }, bars: [
      { ts: '2026-10-16T13:30:00Z', open: 100, high: 101, low: 99.5, close: 101 },
      { ts: '2026-10-16T13:31:00Z', open: 102, high: 104, low: 102, close: 104 },
    ],
  }} />);
  expect(points).toEqual(expect.arrayContaining(fills.map(f => ({ time: Math.floor(new Date(f.ts).getTime()/60000)*60, value: f.price }))));
  expect(markers.filter(m => /^Entry /.test(m.text)).map(m => m.text)).toEqual(['Entry 1','Entry 2']);
  expect(lines.find(l => l.title === 'Entry').price).toBe(101.5);
});

test('first 1R and 2R touches in one minute keep exact markers alongside entry and actual exit', () => {
  global.ResizeObserver = class { observe() {} disconnect() {} };
  const points = [];
  const lines = [];
  const colors = { '--text-secondary': '#999999', '--accent-line': '#ffaa00', '--result-pos': '#00aa00', '--result-neg': '#cc0000' };
  Object.entries(colors).forEach(([key, value]) => document.documentElement.style.setProperty(key, value));
  const chart = {
    addCandlestickSeries: () => ({ setData() {}, createPriceLine: line => lines.push(line) }),
    addLineSeries: () => ({ setData: data => points.push(...data), setMarkers() {} }),
    timeScale: () => ({ setVisibleLogicalRange() {} }), applyOptions() {}, remove: jest.fn(),
  };
  createChart.mockReturnValue(chart);
  const replay = {
    trade: { entry: 100, exit: 100.5, stop: 99, direction: 1, entered_at: '2026-10-01T14:00:00Z', exited_at: '2026-10-01T14:02:00Z' },
    first_hits: [{ label: '1R', ts: '2026-10-01T14:00:00Z', price: 101 }, { label: '2R', ts: '2026-10-01T14:00:00Z', price: 102 }],
    bars: [{ ts: '2026-10-01T14:00:00Z', open: 100, high: 102.5, low: 99.5, close: 102, feed: 'sip' }, { ts: '2026-10-01T14:01:00Z', open: 102, high: 103, low: 100, close: 101, feed: 'sip' }],
    rules: [{ rule_hash: 'fixture', rule: { name: 'Synthetic fixture' }, trades: [{ eligible: true, fills: [{ ts: '2026-10-01T14:00:00Z', price: 101 }, { ts: '2026-10-01T14:00:00Z', price: 102 }] }] }],
  };
  const view = render(<ReplayChart replay={replay} />);
  const time = new Date('2026-10-01T14:00:00Z').getTime() / 1000;
  expect(points).toEqual(expect.arrayContaining([{ time, value: 101 }, { time, value: 102 }]));
  expect(lines).toEqual([
    { price: 100, title: 'Entry', color: colors['--text-secondary'], lineWidth: 1, lineStyle: 2, axisLabelVisible: true },
    { price: 99, title: 'Stop', color: colors['--result-neg'], lineWidth: 1, lineStyle: 2, axisLabelVisible: true },
    { price: 101, title: '1R', color: colors['--accent-line'], lineWidth: 1, lineStyle: 2, axisLabelVisible: true },
    { price: 102, title: '2R', color: colors['--result-pos'], lineWidth: 1, lineStyle: 2, axisLabelVisible: true },
  ]);
  expect(view.getByText(/Square: actual exit/)).toBeVisible();
  expect(view.getByText(/Circle: 1R touch/)).toBeVisible();
  view.unmount();
  Object.keys(colors).forEach(key => document.documentElement.style.removeProperty(key));
  expect(chart.remove).toHaveBeenCalled();
});

test('saved rules mark each executed tranche once by name', () => {
  global.ResizeObserver = class { observe() {} disconnect() {} };
  const markers = [], points = [];
  const chart = {
    addCandlestickSeries: () => ({ setData() {}, createPriceLine() {} }),
    addLineSeries: () => ({ setData: data => points.push(...data), setMarkers: data => markers.push(...data) }),
    timeScale: () => ({ setVisibleLogicalRange() {} }), applyOptions() {}, remove() {},
  };
  createChart.mockReturnValue(chart);
  const at = '2026-10-01T14:00:00Z', later = '2026-10-01T14:01:00Z';
  const fills = [{ ts: at, price: 101, quantity: 50 }, { ts: later, price: 100.5, quantity: 50 }];
  const saved = { rule_id: 5, is_preset: false, rule: { name: 'Half at 1R, rest EMA 9' }, trades: [{ eligible: true, fills: fills }] };
  const replay = {
    trade: { entry: 100, exit: 100.5, stop: 99, direction: 1, entered_at: at, exited_at: later },
    bars: [{ ts: at, open: 100, high: 101.1, low: 99.5, close: 101 }, { ts: later, open: 100.5, high: 101, low: 100, close: 100.5 }],
    rules: [saved, { ...saved, is_preset: true }, { ...saved, trades: [{ eligible: false }] }],
  };
  render(<ReplayChart replay={replay} />);
  expect(markers.filter(m => m.text === saved.rule.name)).toHaveLength(2);
  expect(markers.filter(m => m.text === saved.rule.name).map(m => m.position)).toEqual(['aboveBar', 'belowBar']);
  expect(markers.filter(m => m.text === saved.rule.name).every(m => m.shape === 'arrowDown')).toBe(true);
  expect(points).toEqual(expect.arrayContaining(fills.map(f => ({ time: new Date(f.ts).getTime()/1000, value: f.price }))));
  expect(customExitMarkers(replay).map(f => f.label)).toEqual([saved.rule.name, saved.rule.name]);
  expect(customExitMarkers({ ...replay, rules: [] })).toEqual([]);
});
