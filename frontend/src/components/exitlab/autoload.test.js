import { act, render, screen, fireEvent, waitFor } from '@testing-library/react';
import ExitLab from '../ExitLab';
import TradeExits from './TradeExits';
import { exitlabApi } from '../../api';

jest.mock('../../api', () => ({ exitlabApi: { history: jest.fn(), simpleReplay: jest.fn(), fetchBars: jest.fn(), stop: jest.fn(), loadMissing: jest.fn(), loadMissingStatus: jest.fn() } }));
jest.mock('./ReplayChart', () => () => <div>Historical chart</div>);

const trade = { id: 1, account_id: 1, ticker: 'FIXTURE', side: 'LONG', source: 'imported', date: '2026-10-01', net_pnl: 47.5 };
const missing = { trade_id: 2, stop_price: 99, reasons: ['incomplete_minute_coverage_through_close'], journal_trade: { ...trade, id: 2, ticker: 'GAP' } };
const history = (count, rows = [missing]) => ({ data: { setup_scoped: true, rows, rules: [], missing_prices: count } });
const job = over => ({ data: { running: true, account_id: 1, total: 3, done: 0, failed: 0, failures: [], started_at: 'x', finished_at: null, ...over } });
const sip = { data: { feed: 'sip', benchmark: { feed: 'sip' }, warmup: { feed: 'sip' }, warmup_benchmark: { feed: 'sip' } } };
const replay = { rows: [{ trade_id: 1, stop_price: 99, reasons: [], journal_trade: trade, actual_r: .5 }], rules: [], reasons: [],
  trade: { entry: 100, exit: 100.5, stop: 99, entered_at: '2026-10-01T14:00:00Z', exited_at: '2026-10-01T14:05:00Z' }, bars: [{}] };
const gappy = { ...replay, rows: [{ ...replay.rows[0], actual_r: null, reasons: ['incomplete_minute_coverage_through_close'] }],
  reasons: ['incomplete_minute_coverage_through_close'] };
const workspace = () => render(<ExitLab accountId={1} accounts={[{ id: 1, name: 'Fixture' }]} onOpenDetail={jest.fn()} />);

beforeEach(() => {
  jest.useRealTimers();
  Object.values(exitlabApi).forEach(fn => fn.mockReset());
  exitlabApi.loadMissingStatus.mockResolvedValue(job({ running: false, total: 0 }));
});

test('button shows the trades excluded only for missing prices and hides at zero', async () => {
  exitlabApi.history.mockResolvedValue(history(1));
  const { unmount } = workspace();
  expect(await screen.findByRole('button', { name: 'Load missing prices (1 trade)' })).toBeVisible();
  unmount();
  exitlabApi.history.mockResolvedValue(history(0));
  workspace();
  expect(await screen.findByText(/1 trades not included/)).toBeVisible();
  expect(screen.queryByRole('button', { name: /Load missing prices/ })).not.toBeInTheDocument();
});

test('running job shows progress, polls every 2 s, then refreshes the table and lists failures', async () => {
  jest.useFakeTimers();
  exitlabApi.history.mockResolvedValueOnce(history(3)).mockResolvedValue(history(1));
  exitlabApi.loadMissing.mockResolvedValue(job());
  workspace();
  fireEvent.click(await screen.findByRole('button', { name: 'Load missing prices (3 trades)' }));
  expect(await screen.findByText('Loading prices 0 of 3')).toBeVisible();
  expect(screen.getByRole('progressbar', { name: 'Loading prices' })).toHaveAttribute('aria-valuenow', '0');
  expect(exitlabApi.loadMissing).toHaveBeenCalledWith(1);
  expect(screen.queryByRole('button', { name: /Load missing prices/ })).not.toBeInTheDocument();

  // One status read on open (a job may already be running), then one per 2 s tick.
  const opened = exitlabApi.loadMissingStatus.mock.calls.length;
  expect(opened).toBe(1);
  exitlabApi.loadMissingStatus.mockResolvedValueOnce(job({ done: 2 }));
  await act(async () => { jest.advanceTimersByTime(1999); });
  expect(exitlabApi.loadMissingStatus).toHaveBeenCalledTimes(opened);
  await act(async () => { jest.advanceTimersByTime(1); });
  expect(await screen.findByText('Loading prices 2 of 3')).toBeVisible();

  exitlabApi.loadMissingStatus.mockResolvedValueOnce(job({ running: false, done: 3, failed: 1, finished_at: 'y',
    failures: [{ ticker: 'GAP', date: '2026-10-01', reason: 'Alpaca historical bars failed (HTTP 401)' }] }));
  await act(async () => { jest.advanceTimersByTime(2000); });
  expect(await screen.findByText('Loaded prices for 2 of 3 trades. 1 could not load:')).toBeVisible();
  expect(screen.getByText('GAP / 2026-10-01')).toBeVisible();
  expect(screen.getByText('Alpaca historical bars failed (HTTP 401)')).toBeVisible();
  await waitFor(() => expect(exitlabApi.history).toHaveBeenCalledTimes(2));
  expect(await screen.findByRole('button', { name: 'Load missing prices (1 trade)' })).toBeVisible();
  await act(async () => { jest.advanceTimersByTime(6000); });
  expect(exitlabApi.loadMissingStatus).toHaveBeenCalledTimes(opened + 2);
});

test('a job already running reports progress on open, and a refused second start follows it', async () => {
  exitlabApi.history.mockResolvedValue(history(3));
  exitlabApi.loadMissingStatus.mockResolvedValue(job({ done: 1 }));
  const { unmount } = workspace();
  expect(await screen.findByText('Loading prices 1 of 3')).toBeVisible();
  unmount();
  exitlabApi.loadMissingStatus.mockReset();
  exitlabApi.loadMissingStatus.mockResolvedValueOnce(job({ running: false })).mockResolvedValue(job({ done: 2 }));
  exitlabApi.loadMissing.mockRejectedValue({ response: { status: 409, data: { detail: 'Prices are already loading' } } });
  workspace();
  fireEvent.click(await screen.findByRole('button', { name: 'Load missing prices (3 trades)' }));
  expect(await screen.findByText('Loading prices 2 of 3')).toBeVisible();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

test('a refused start explains why', async () => {
  exitlabApi.history.mockResolvedValue(history(1));
  exitlabApi.loadMissing.mockRejectedValue({ response: { status: 400, data: { detail: 'Alpaca credentials missing; set ALPACA_ENV_FILE or APCA_API_KEY_ID/APCA_API_SECRET_KEY' } } });
  workspace();
  fireEvent.click(await screen.findByRole('button', { name: 'Load missing prices (1 trade)' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('APCA_API_KEY_ID');
});

test('Exits tab loads missing prices once on open, then replays again', async () => {
  let release;
  exitlabApi.simpleReplay.mockResolvedValueOnce({ data: gappy }).mockResolvedValue({ data: replay });
  exitlabApi.fetchBars.mockReturnValue(new Promise(resolve => { release = resolve; }));
  render(<TradeExits trade={trade} />);
  expect(await screen.findByText('Loading prices for this session...')).toBeVisible();
  expect(screen.queryByRole('table')).not.toBeInTheDocument();
  expect(screen.queryByText(/Minute bars are missing/)).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Load prices' })).not.toBeInTheDocument();
  expect(exitlabApi.fetchBars).toHaveBeenCalledWith({ symbol: 'FIXTURE', start: '2026-10-01T08:00:00Z',
    end: '2026-10-01T21:00:00Z', feed: 'sip', include_warmup: true, refresh: false });
  await act(async () => { release(sip); });
  expect(await screen.findByText('+0.50R actual')).toBeVisible();
  expect(screen.getByRole('table')).toBeVisible();
  expect(screen.queryByText('Loading prices for this session...')).not.toBeInTheDocument();
  expect(exitlabApi.simpleReplay).toHaveBeenCalledTimes(2);
});

test('Exits tab never retries automatically; the manual link is the retry', async () => {
  exitlabApi.simpleReplay.mockResolvedValue({ data: gappy });
  exitlabApi.fetchBars.mockResolvedValue(sip);
  render(<TradeExits trade={trade} />);
  const retry = await screen.findByRole('button', { name: 'Load prices' });
  await waitFor(() => expect(exitlabApi.simpleReplay).toHaveBeenCalledTimes(2));
  expect(exitlabApi.fetchBars).toHaveBeenCalledTimes(1);
  expect(screen.getByText(/Minute bars are missing/)).toBeVisible();
  fireEvent.click(retry);
  await waitFor(() => expect(exitlabApi.fetchBars).toHaveBeenLastCalledWith(expect.objectContaining({ refresh: true })));
  await waitFor(() => expect(exitlabApi.simpleReplay).toHaveBeenCalledTimes(3));
  expect(exitlabApi.fetchBars).toHaveBeenCalledTimes(2);
});

test('Exits tab shows a failed load plainly with the retry link', async () => {
  exitlabApi.simpleReplay.mockResolvedValue({ data: gappy });
  exitlabApi.fetchBars.mockRejectedValue({ response: { data: { detail: 'Alpaca historical bars failed (HTTP 401)' } } });
  render(<TradeExits trade={trade} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('Prices did not load. Alpaca historical bars failed (HTTP 401)');
  expect(screen.getByRole('button', { name: 'Load prices' })).toBeEnabled();
  expect(screen.getByRole('table')).toBeVisible();
  expect(exitlabApi.simpleReplay).toHaveBeenCalledTimes(1);
});
