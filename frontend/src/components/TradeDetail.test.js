import { computeStats } from './TradeDetail';

const trade = (side, execs) => ({ side, net_pnl: 0, executions: JSON.stringify(execs) });
const fill = (action, date, time, qty = 20, price = 21) => ({ action, date, time, qty, price });

test('hold time of an intraday trade', () => {
  const s = computeStats(trade('LONG', [
    fill('BOT', '2026-09-14', '09:35:00'),
    fill('SOLD', '2026-09-14', '11:40:00'),
  ]));
  expect(s.holdMinutes).toBe(125);
  expect(s.fmtHold(s.holdMinutes)).toBe('2h 5m');
  expect(s.fmtHold(45)).toBe('45m');
});

test('hold time of a multi-day trade counts the days, not just the time of day', () => {
  // the trade from the PR: BOT 20 @21.28 on 9/14, SOLD 20 @20.40 on 9/22, exit 12 minutes later in the day
  const s = computeStats(trade('LONG', [
    fill('BOT', '2026-09-14', '09:35:00', 20, 21.28),
    fill('SOLD', '2026-09-22', '09:47:00', 20, 20.40),
  ]));
  expect(s.holdMinutes).toBe(8 * 24 * 60 + 12);
  expect(s.fmtHold(s.holdMinutes)).toBe('8d 0h 12m');
  expect([s.openDate, s.openTime, s.closeDate, s.closeTime])
    .toEqual(['2026-09-14', '09:35:00', '2026-09-22', '09:47:00']);
});

test('fills are ordered by date before time of day', () => {
  // the exit's clock time is earlier than the entry's; sorting by time alone would swap them
  const s = computeStats(trade('SHORT', [
    fill('BOT', '2026-09-16', '10:00:00'),
    fill('SOLD', '2026-09-15', '15:30:00'),
  ]));
  expect([s.openDate, s.openTime]).toEqual(['2026-09-15', '15:30:00']);
  expect(s.holdMinutes).toBe(18 * 60 + 30);
  expect(s.fmtHold(s.holdMinutes)).toBe('18h 30m');
});

test('holds of 30 days or more show months', () => {
  const s = computeStats(trade('LONG', []));
  expect(s.fmtHold(45 * 24 * 60 + 61)).toBe('1mo 15d 1h 1m');
});

test('an open trade has no hold time', () => {
  const s = computeStats(trade('LONG', [fill('BOT', '2026-09-14', '09:35:00')]));
  expect(s.holdMinutes).toBeNull();
  expect(s.fmtHold(s.holdMinutes)).toBe('—');
});

test('an option trade counts 100 shares per contract in ROI', () => {
  const s = computeStats({ side: 'LONG', instrument_type: 'OPTION', net_pnl: 100, gross_pnl: 100,
    executions: JSON.stringify([fill('BOT', '2026-09-14', '09:35:00', 1, 2), fill('SOLD', '2026-09-14', '10:00:00', 1, 3)]) });
  expect(s.multiplier).toBe(100);
  expect(s.netRoi).toBeCloseTo(50);
});

test('a closed future uses the point value it was booked with', () => {
  const s = computeStats({ side: 'SHORT', instrument_type: 'FUTURE', ticker: '/ESZ26', net_pnl: 100, gross_pnl: 100,
    executions: JSON.stringify([fill('SOLD', '2026-09-14', '09:35:00', 1, 6002), fill('BOT', '2026-09-14', '10:00:00', 1, 6000)]) });
  expect(s.multiplier).toBeCloseTo(50);
});

test('a stock keeps a multiplier of 1', () => {
  const s = computeStats({ side: 'LONG', net_pnl: 10, gross_pnl: 10,
    executions: JSON.stringify([fill('BOT', '2026-09-14', '09:35:00', 10, 20), fill('SOLD', '2026-09-14', '10:00:00', 10, 21)]) });
  expect(s.multiplier).toBe(1);
  expect(s.netRoi).toBeCloseTo(5);
});
