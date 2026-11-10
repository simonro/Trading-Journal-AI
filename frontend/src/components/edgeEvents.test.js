import { etParts, describeEvent, prepareEvents, levelSteps, etWallSec } from './edgeEvents';

const ev = (ts, kind, price = null, extra = {}) => ({ id: `${ts}-${kind}`, ts, kind, price, qty: null, text: null, origin: 'schwab', ...extra });

test('times read in US Eastern across daylight saving', () => {
  expect(etParts('2026-10-27T15:17:42+00:00')).toEqual({ date: '2026-10-27', time: '11:17:42' });
  expect(etParts('2026-11-04T20:37:17+00:00')).toEqual({ date: '2026-11-04', time: '15:37:17' });
});

test('labels read like the trader would say them', () => {
  expect(describeEvent(ev('t', 'stop_set', 199)).plain).toBe('Stop set 199.00');
  expect(describeEvent(ev('t', 'breakeven', 200)).plain).toBe('Stop moved to break-even 200.00');
  expect(describeEvent(ev('t', 'target_moved', 206)).plain).toBe('Target moved to 206.00');
  expect(describeEvent(ev('t', 'stop_cancelled', 150.25)).plain).toBe('Stop 150.25 cancelled');
  expect(describeEvent(ev('t', 'stop_cancelled')).plain).toBe('Stop cancelled');
  expect(describeEvent(ev('t', 'trail', null, { text: 'trailing 0.50' })).plain).toBe('Trailing stop: trailing 0.50');
  expect(describeEvent(ev('t', 'note', null, { text: 'Scaled out' })).plain).toBe('Note: Scaled out');
  expect(describeEvent(ev('t', 'entry', 201.35, { qty: 50 })).plain).toBe('Entry 50 at 201.35');
  expect(describeEvent(ev('t', 'target_set', 0.425)).price).toBe('0.425');
});

test('stop steps follow sets and cancels and end at the exit', () => {
  const events = prepareEvents([
    ev('2026-10-27T15:17:42+00:00', 'stop_set', 150.25),
    ev('2026-10-27T16:08:21+00:00', 'stop_cancelled', 150.25),
    ev('2026-10-27T16:08:22+00:00', 'stop_moved', 150.25),
    ev('2026-10-27T16:08:31+00:00', 'breakeven', 151.5),
    ev('2026-10-27T16:13:48+00:00', 'trail'),
    ev('2026-10-27T17:00:00+00:00', 'stop_moved', 153),
  ]);
  const end = etWallSec('2026-10-27', '12:27:51');
  const steps = levelSteps(events, 'stop', end);
  expect(steps.map(s => s.price)).toEqual([150.25, 150.25, null, 150.25, 151.5, 151.5]);
  expect(steps[steps.length - 1].sec).toBe(end);
  expect(levelSteps(events, 'target', end)).toEqual([]);
});
