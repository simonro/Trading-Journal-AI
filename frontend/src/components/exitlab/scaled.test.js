import { render, screen, within, fireEvent } from '@testing-library/react';
import TradeExits from './TradeExits';
import ExitLab from '../ExitLab';
import { summary, exclusionLine } from './simple';
import { exitlabApi } from '../../api';

jest.mock('../../api', () => ({ exitlabApi: { simpleReplay: jest.fn(), history: jest.fn() } }));
jest.mock('./ReplayChart', () => () => <div>Historical chart</div>);
const warning = 'Touched the stop/target while scaling in; not counted';
const scaling = { fill_count: 2, first_fill_at: '2026-10-16T13:30:10Z', last_fill_at: '2026-10-16T13:31:20Z', average_entry: 101.5, warning };
const journal = { id: 1, ticker: 'FIXTURE', date: '2026-10-16', side: 'LONG', net_pnl: 350 };
const data = {
  setup_scoped: true, rows: [{ trade_id: 1, actual_r: 1.4, reasons: [], stop_price: 99, journal_trade: journal, scaled_entry: scaling }],
  rules: [{ rule_id: 1, is_preset: true, rule: { name: '1R target' }, trades: [{ trade_id: 1, eligible: true, reasons: [], actual_r: 1.4, pnl_r: 1, pnl: 250, what_happened: 'Hit 1R at 09:32 before the stop', scaled_entry: scaling }] }],
  trade: { entry: 101.5, exit: 105, stop: 99, entered_at: scaling.first_fill_at, exited_at: '2026-10-16T13:34:00Z' },
  bars: [], reasons: [], scaled_entry: scaling, excursion: { eligible: true },
};

beforeEach(() => { jest.clearAllMocks(); localStorage.clear(); });

test('scaled header and results use computed metadata and explain the post-build hold', async () => {
  exitlabApi.simpleReplay.mockResolvedValue({ data });
  render(<TradeExits trade={journal} />);
  expect(await screen.findByText('Scaled entry: 2 fills 09:30 to 09:31, avg $101.50, simulated from the last fill')).toBeVisible();
  const actual = screen.getByText('Actual', { exact: true }).closest('tr');
  expect(within(actual).getByText('+1.40R')).toBeVisible();
  expect(within(actual).getByText('+$350.00')).toBeVisible();
  expect(within(actual).getByText(warning)).toBeVisible();
  expect(screen.getByText(/MFE \/ MAE \/ efficiency: average entry/)).toBeVisible();
  fireEvent.click(screen.getByText('How this is calculated'));
  expect(screen.getByText(/Scaled entries use the quantity-weighted average/)).toBeVisible();
  expect(screen.getByText(/Stops, targets and rule thresholds during the build window/)).toBeVisible();
  expect(screen.queryByText('Not available')).not.toBeInTheDocument();
});

test('scaled trades count in history without appearing among exclusions and retain the row flag', async () => {
  const result = summary(data);
  expect(result.table.map(r => r.n)).toEqual([1,1]);
  expect(result.excluded).toEqual([]);
  expect(exclusionLine(result.excluded)).toBe('0 trades not included.');
  exitlabApi.history.mockResolvedValue({ data });
  render(<ExitLab accountId={1} onOpenDetail={() => {}} />);
  fireEvent.click(await screen.findByRole('button', { name: '1R target' }));
  expect(screen.getByText(warning)).toBeVisible();
  expect(screen.getByText('0 trades not included.')).toBeVisible();
});
