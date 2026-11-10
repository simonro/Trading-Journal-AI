// Integration checks: every feature stays reachable from the app shell, and the
// Settings library behaves. The api module is mocked, so no test reaches a backend.
import { render, screen, within, fireEvent, waitFor, act } from '@testing-library/react';
import App from './App';
import { accountsApi, tradesApi, libraryApi, kpisApi, goalsApi, reportsApi, leaksApi, updateApi, diaryApi, edgeSyncApi, __restoreMocks } from './api';

jest.mock('./api', () => {
  const ok = (data) => Promise.resolve({ data });
  // CRA's jest preset resets mocks before each test, so every mock keeps its
  // implementation and __restoreMocks puts it back in beforeEach.
  const all = [];
  const fn = (impl) => { const f = jest.fn(impl); f.impl = impl; all.push(f); return f; };
  const TRADE = {
    id: 101, account_id: 1, trade_group: '9/10/26_TSLA_STOCK_1', date: '2026-09-10', ticker: 'TSLA',
    instrument_type: 'STOCK', side: 'LONG', gross_pnl: 195, net_pnl: 193.45, commissions: 1.55,
    executions: [
      { date: '2026-09-10', time: '09:54:10', action: 'BOT', qty: 200, price: 366.09, commission: 0 },
      { date: '2026-09-10', time: '10:08:24', action: 'SOLD', qty: 200, price: 367.07, commission: 1.55 },
    ],
    setup: 'VWAP Reclaim', setup_grade: 'B', setup_notes: null, setup_source: 'manual',
    mfe_pct: 0.85, mae_pct: -0.27, exit_efficiency: 31.25, strategy: 'Test Strategy', r_multiple: 0.41,
  };
  const TRADE_2 = { ...TRADE, id: 102, trade_group: '9/10/26_META_STOCK_1', ticker: 'META', net_pnl: -50 };
  // 300 bought, 100 sold: 200 still open.
  const OPEN_TRADE = {
    id: 103, account_id: 1, trade_group: '9/09/26_GOOG_STOCK_1', date: '2026-09-09', ticker: 'GOOG',
    instrument_type: 'STOCK', side: 'LONG', net_pnl: null, gross_pnl: null, commissions: 0,
    executions: [
      { date: '2026-09-09', time: '09:40:00', action: 'BOT', qty: 300, price: 100, commission: 0 },
      { date: '2026-09-09', time: '11:00:00', action: 'SOLD', qty: 100, price: 105, commission: 0 },
    ],
  };
  const ACCOUNTS = [
    { id: 1, name: 'Day Trading', type: 'day_trading', color: '#6366f1', broker: 'Schwab' },
    { id: 2, name: 'Swing', type: 'swing', color: '#6366f1', broker: 'Schwab' },
  ];
  const LIBRARY = {
    strategies: [
      { name: 'VWAP Cross', description: 'Reclaim of VWAP', trades: 10, aliases: [], },
      { name: 'Continuation RS', description: null, trades: 2, aliases: [], },
    ],
    sources: [
      { name: 'Scanner', description: null, trades: 8, aliases: [] },
      { name: 'OneOption', description: null, trades: 1, aliases: [] },
    ],
    tags: {
      mistake: [{ name: 'Sized too big', description: null, trades: 6, aliases: [] }],
      execution: [{ name: 'Scaled out', description: null, trades: 20, aliases: [] }],
      setup: [], emotion: [], outcome: [],
    },
    tag_types: ['setup', 'execution', 'mistake', 'emotion', 'outcome'],
  };
  // Anything not listed resolves with an empty object, which every page treats as "no data".
  const withDefault = (methods) => new Proxy(methods, {
    get: (target, key) => (key in target ? target[key] : fn(() => ok({}))),
  });
  return {
    __restoreMocks: () => all.forEach(f => f.mockImplementation(f.impl)),
    API_BASE: 'http://mocked.invalid',
    accountsApi: withDefault({
      list: fn(() => ok(ACCOUNTS)),
      create: fn(() => ok({ id: 3 })),
      update: fn(() => ok({})),
      usage: fn(() => ok({ trades: 12, first_date: '2026-09-01', last_date: '2026-09-30', diary_entries: 0 })),
      remove: fn(() => ok({ ok: true })),
    }),
    tradesApi: withDefault({
      list: fn((params = {}) => ok(params.open_only ? [OPEN_TRADE] : [TRADE, TRADE_2])),
      addExecution: fn(() => ok({})),
      getAnalysis: fn(() => ok(null)),
      getAnalysisOptions: fn(() => ok({ strategies: [], idea_sources: [] })),
      listCustomSetups: fn(() => ok([])),
      delete: fn(() => ok({ deleted: true })),
      moveAccount: fn(() => ok({ moved: true })),
    }),
    kpisApi: withDefault({ get: fn(() => ok({ total_net_pnl: 100, daily_pnl: [], by_strategy: [] })) }),
    importApi: withDefault({}),
    diaryApi: withDefault({ list: fn(() => ok([])), deleteByDate: fn(() => ok({ ok: true })) }),
    chartApi: withDefault({ get: fn(() => ok({ bars: [], warning: 'No chart data in tests' })) }),
    insightsApi: withDefault({}),
    calendarApi: withDefault({ get: fn(() => ok({ days: [] })) }),
    brainApi: withDefault({}),
    dailySummaryApi: withDefault({ get: fn(() => ok({ trades: [] })) }),
    syncApi: withDefault({}),
    edgeSyncApi: withDefault({ tradeEvents: fn(() => ok({ events: [] })) }),
    goalsApi: withDefault({ get: fn(() => ok({ win_rate: 65 })), put: fn(() => ok({ win_rate: 65 })) }),
    reportsApi: withDefault({ get: fn(() => Promise.reject(new Error('no reports in tests'))) }),
    edgeReportApi: withDefault({}),
    leaksApi: withDefault({
      get: fn(() => ok({
        has_data: true, trade_count: 40, net_pnl: 500, total_cost: 750, recent_from: '2026-09-01',
        cache_key: 'k1', coverage: { trades: 40, diary_trades: 4 },
        leaks: [{
          id: 'entry_window:11:00-13:30', dimension: 'entry_window', dimension_label: 'Entry time',
          bucket: '11:00-13:30', trades: 25, net_pnl: -750, avg_pnl: -30, rest_avg_pnl: 50, win_rate: 20,
          t: -9.1, confidence: 'solid', dedup_cost: 750, overlap_trades: 0, diary_based: false,
          recent: { trades: 12, net_pnl: -360 }, trade_ids: [101, 102],
          sentence: 'These 25 trades lost $750 in total, -$30 a trade against $50 for your other trades.',
        }],
        edges: [], new: [],
        management: { by_hold_time: [], by_management: [] },
        costs: [{ instrument: 'STOCK', trades: 40, gross_pnl: 540, commissions: 40, net_pnl: 500, per_trade: 1 }],
      })),
      explain: fn(() => ok({ cache_key: 'k1', cached: true, sentences: { 'entry_window:11:00-13:30': 'Midday entries cost you $750.' } })),
    }),
    weeklySummaryApi: withDefault({}),
    yearlyKpisApi: withDefault({ get: fn(() => ok({ months: [] })) }),
    updateApi: withDefault({ get: fn(() => ok({ current: '1.5.0', latest: null, available: false, checked: true })) }),
    pointValuesApi: withDefault({ get: fn(() => ok({ option: 100, futures: { '/ES': 50, '/MES': 5 } })) }),
    libraryApi: withDefault({
      list: fn(() => ok(LIBRARY)),
      merge: fn(() => ok({ moved: 2, target: 'VWAP Cross', trades: 12 })),
      remove: fn(() => ok({ affected: 1, reassigned_to: null })),
      update: fn(() => ok({ name: 'VWAP Cross', trades: 10 })),
      create: fn(() => ok({ name: 'New' })),
    }),
  };
});

beforeAll(() => {
  // jsdom gaps that charts and menus touch.
  global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  Element.prototype.scrollIntoView = jest.fn();
  window.alert = jest.fn();
});

beforeEach(() => __restoreMocks());

async function renderApp() {
  render(<App />);
  // Accounts load on mount; waiting for them lets the first render settle.
  await waitFor(() => expect(accountsApi.list).toHaveBeenCalled());
  await act(async () => {});
}

const nav = () => screen.getByRole('navigation', { name: 'Main' });

test('header keeps every page, Settings, Import, Add Trade and a labeled Brain entry visible', async () => {
  await renderApp();
  for (const label of ['Dashboard', 'Trade View', 'Calendar', 'Day Review', 'Reports', 'Exit Lab', 'Diary']) {
    expect(within(nav()).getByRole('button', { name: label })).toBeVisible();
    expect(within(nav()).getByText(label)).toBeVisible();
  }
  const banner = screen.getByRole('banner');
  for (const label of ['Help', 'Settings']) {
    expect(within(banner).getByRole('button', { name: label })).toBeVisible();
    expect(within(nav()).queryByRole('button', { name: label })).not.toBeInTheDocument();
  }
  expect(within(banner).getByRole('button', { name: /^Import$/ })).toBeVisible();
  expect(within(banner).getByRole('button', { name: /Add Trade/ })).toBeVisible();
  expect(within(banner).getByRole('button', { name: /Brain/ })).toBeVisible();
});

test('Brain opens from the header as a dialog and closes on Escape', async () => {
  await renderApp();
  const trigger = within(screen.getByRole('banner')).getByRole('button', { name: /Brain/ });
  fireEvent.click(trigger);
  const dialog = await screen.findByRole('dialog', { name: /Brain/i });
  expect(dialog).toBeVisible();
  fireEvent.keyDown(dialog, { key: 'Escape' });
  await waitFor(() => expect(screen.queryByRole('dialog', { name: /Brain/i })).not.toBeInTheDocument());
});

test('account menu keeps selection, rename and new-account controls', async () => {
  await renderApp();
  fireEvent.click(screen.getByRole('button', { name: 'Account: All Accounts' }));
  const menu = screen.getByRole('group', { name: 'Accounts' });
  expect(within(menu).getByRole('button', { name: 'All Accounts' })).toBeInTheDocument();
  expect(within(menu).getByRole('button', { name: 'Day Trading' })).toBeInTheDocument();
  expect(within(menu).getByRole('checkbox', { name: 'Include Day Trading' })).toBeInTheDocument();
  expect(within(menu).getByRole('button', { name: 'Delete Day Trading' })).toBeInTheDocument();
  expect(within(menu).getByRole('button', { name: /New Account/ })).toBeInTheDocument();

  fireEvent.click(within(menu).getByRole('button', { name: 'Rename Day Trading' }));
  const input = within(menu).getByRole('textbox', { name: 'New name for Day Trading' });
  fireEvent.change(input, { target: { value: 'Day Trading Main' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  await waitFor(() => expect(accountsApi.update).toHaveBeenCalledWith(1, { name: 'Day Trading Main' }));
});

test('choosing an account updates the shared header state', async () => {
  await renderApp();
  fireEvent.click(screen.getByRole('button', { name: 'Account: All Accounts' }));
  fireEvent.click(screen.getByRole('button', { name: 'Swing' }));
  expect(await screen.findByRole('button', { name: 'Account: Swing' })).toBeInTheDocument();
});

test('ticking accounts combines them, and ticking every one is All Accounts', async () => {
  await renderApp();
  fireEvent.click(screen.getByRole('button', { name: 'Account: All Accounts' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Include Swing' }));
  expect(await screen.findByRole('button', { name: 'Account: Swing' })).toBeInTheDocument();
  await waitFor(() => expect(kpisApi.get).toHaveBeenLastCalledWith(expect.objectContaining({ account_id: 2 })));

  // The menu stays open, so a second box can be ticked.
  fireEvent.click(screen.getByRole('checkbox', { name: 'Include Day Trading' }));
  expect(await screen.findByRole('button', { name: 'Account: All Accounts' })).toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: 'Include all accounts' })).toBeChecked();
});

test('deleting an account asks first and names how many trades go with it', async () => {
  await renderApp();
  fireEvent.click(screen.getByRole('button', { name: 'Account: All Accounts' }));
  fireEvent.click(screen.getByRole('button', { name: 'Delete Swing' }));
  const dialog = await screen.findByRole('alertdialog', { name: 'Delete Swing?' });
  const confirm = await within(dialog).findByRole('button', { name: 'Delete account and 12 trades' });
  expect(accountsApi.remove).not.toHaveBeenCalled();
  fireEvent.click(confirm);
  await waitFor(() => expect(accountsApi.remove).toHaveBeenCalledWith(2));
  await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument());
});

test('Add Trade opens a modal dialog that closes on Escape', async () => {
  await renderApp();
  fireEvent.click(within(screen.getByRole('banner')).getByRole('button', { name: /Add Trade/ }));
  const dialog = await screen.findByRole('dialog', { name: /Add Trade/ });
  expect(within(dialog).getByRole('button', { name: /Save Trade/ })).toBeInTheDocument();
  fireEvent.keyDown(dialog, { key: 'Escape' });
  await waitFor(() => expect(screen.queryByRole('dialog', { name: /Add Trade/ })).not.toBeInTheDocument());
});

test('Reports keeps its tabs, adds Sources & Tags, and supports arrow-key navigation', async () => {
  await renderApp();
  fireEvent.click(within(nav()).getByRole('button', { name: 'Reports' }));
  const tablist = await screen.findByRole('tablist');
  const names = within(tablist).getAllByRole('tab').map(t => t.textContent.trim());
  expect(names).toEqual(['Overview', 'Leaks', 'Setups & Strategy', 'Sources & Tags', 'Timing', 'Execution', 'Symbols', 'Psychology']);
  const overview = within(tablist).getByRole('tab', { name: 'Overview' });
  expect(overview).toHaveAttribute('aria-selected', 'true');
  fireEvent.keyDown(overview, { key: 'ArrowRight' });
  expect(within(tablist).getByRole('tab', { name: 'Leaks' })).toHaveAttribute('aria-selected', 'true');
  // Opening Leaks fetches them; let that settle inside act.
  await act(async () => {});
});

test('Trade View row gear moves a trade to another account and deletes one, without opening it', async () => {
  await renderApp();
  fireEvent.click(within(nav()).getByRole('button', { name: 'Trade View' }));
  const gear = await screen.findByRole('button', { name: /^Actions for TSLA on 2026-09-10 at \d\d:\d\d$/ });

  fireEvent.click(gear);
  expect(gear).toHaveAttribute('aria-expanded', 'true');
  fireEvent.click(screen.getByRole('menuitem', { name: /Move to account/ }));
  const move = await screen.findByRole('dialog', { name: 'Move trade to another account' });
  // The trade's own account is not offered as a target.
  expect(within(move).getByRole('combobox', { name: 'Move to' })).toHaveDisplayValue('Swing');
  const listCalls = tradesApi.list.mock.calls.length;
  fireEvent.click(within(move).getByRole('button', { name: 'Move trade' }));
  await waitFor(() => expect(tradesApi.moveAccount).toHaveBeenCalledWith(101, 2));
  await waitFor(() => expect(tradesApi.list.mock.calls.length).toBeGreaterThan(listCalls));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: /^Actions for TSLA on 2026-09-10 at \d\d:\d\d$/ }));
  fireEvent.click(screen.getByRole('menuitem', { name: /Delete trade/ }));
  const del = await screen.findByRole('dialog', { name: 'Delete this trade?' });
  fireEvent.click(within(del).getByRole('button', { name: 'Cancel' }));
  expect(tradesApi.delete).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole('button', { name: /^Actions for TSLA on 2026-09-10 at \d\d:\d\d$/ }));
  fireEvent.click(screen.getByRole('menuitem', { name: /Delete trade/ }));
  fireEvent.click(within(await screen.findByRole('dialog', { name: 'Delete this trade?' })).getByRole('button', { name: 'Delete trade' }));
  await waitFor(() => expect(tradesApi.delete).toHaveBeenCalledWith(101));
  // None of this opened the trade.
  expect(screen.queryByRole('tablist', { name: 'Trade review sections' })).not.toBeInTheDocument();
});

test('a refused move shows the reason and keeps the dialog open', async () => {
  tradesApi.moveAccount.mockImplementation(() => Promise.reject({
    response: { data: { detail: 'Swing already has a TSLA trade on 2026-09-10 with the same name.' } } }));
  await renderApp();
  fireEvent.click(within(nav()).getByRole('button', { name: 'Trade View' }));
  fireEvent.click(await screen.findByRole('button', { name: /^Actions for TSLA on 2026-09-10 at \d\d:\d\d$/ }));
  fireEvent.click(screen.getByRole('menuitem', { name: /Move to account/ }));
  const move = await screen.findByRole('dialog', { name: 'Move trade to another account' });
  fireEvent.click(within(move).getByRole('button', { name: 'Move trade' }));
  expect(await within(move).findByRole('alert')).toHaveTextContent('Swing already has a TSLA trade');
  expect(screen.getByRole('dialog', { name: 'Move trade to another account' })).toBeInTheDocument();
});

test('deleting the only trade on the last page goes back a page instead of showing none', async () => {
  // 26 trades: page 2 holds one. After the delete the list has 25 and page 2 no longer exists.
  const many = Array.from({ length: 26 }, (_, i) => ({
    id: 500 + i, account_id: 1, trade_group: `9/10/26_T${i}_STOCK_1`, date: '2026-09-10', ticker: `T${i}`,
    instrument_type: 'STOCK', side: 'LONG', gross_pnl: 10, net_pnl: 10, commissions: 0,
    executions: [{ date: '2026-09-10', time: `09:${String(10 + i).padStart(2, '0')}:00`, action: 'BOT', qty: 1, price: 1 }],
  }));
  let current = many;
  tradesApi.list.mockImplementation(() => Promise.resolve({ data: current }));
  tradesApi.delete.mockImplementation((id) => { current = current.filter(t => t.id !== id); return Promise.resolve({ data: { deleted: true } }); });
  await renderApp();
  fireEvent.click(within(nav()).getByRole('button', { name: 'Trade View' }));
  fireEvent.click(await screen.findByRole('button', { name: /Next/ }));
  expect(await screen.findByText(/Page 2 of 2/)).toBeInTheDocument();
  const gear = screen.getAllByRole('button', { name: /^Actions for T\d+ on 2026-09-10/ });
  expect(gear).toHaveLength(1);
  fireEvent.click(gear[0]);
  fireEvent.click(screen.getByRole('menuitem', { name: /Delete trade/ }));
  fireEvent.click(within(await screen.findByRole('dialog', { name: 'Delete this trade?' })).getByRole('button', { name: 'Delete trade' }));
  await waitFor(() => expect(screen.getAllByRole('button', { name: /^Actions for T\d+ on 2026-09-10/ })).toHaveLength(25));
  expect(screen.queryByText(/No trades found/)).not.toBeInTheDocument();
  expect(screen.getByRole('region', { name: 'Trades' })).toHaveFocus();
});

test('Trade View opens Trade Details with all six tabs, back and previous/next', async () => {
  await renderApp();
  fireEvent.click(within(nav()).getByRole('button', { name: 'Trade View' }));
  await waitFor(() => expect(tradesApi.list).toHaveBeenCalled());
  // A row opens the trade. The in-place expand was removed in V3.
  const row = (await screen.findAllByText('TSLA'))[0].closest('tr');
  fireEvent.click(row);

  const tablist = await screen.findByRole('tablist', { name: 'Trade review sections' });
  const names = within(tablist).getAllByRole('tab').map(t => t.textContent.trim());
  expect(names).toEqual(['Stats', 'Strategy', 'Tags', 'Executions', 'What If', 'Exits']);
  expect(screen.getByRole('button', { name: /Back to trades/ })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Previous trade/ })).toBeDisabled();
  expect(screen.getByRole('button', { name: /Next trade/ })).toBeEnabled();
  // Trade View stays highlighted while a trade is open.
  expect(within(nav()).getByRole('button', { name: 'Trade View' })).toHaveAttribute('aria-current', 'page');

  fireEvent.click(within(tablist).getByRole('tab', { name: 'Executions' }));
  expect(screen.getByRole('button', { name: /Add Execution/ })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Edit execution 1' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Delete execution 1' })).toBeInTheDocument();
});

test('Import keeps broker CSV import and diary analysis, with keyboard dropzones', async () => {
  await renderApp();
  fireEvent.click(within(screen.getByRole('banner')).getByRole('button', { name: /^Import$/ }));
  expect(await screen.findByRole('heading', { name: /Upload CSV File/ })).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: /Analyze Trading Diary/ })).toBeInTheDocument();
  // Both supported brokers stay selectable, with auto-detect as the default.
  const broker = screen.getByLabelText('Broker');
  expect(broker).toHaveValue('auto');
  expect(within(broker).getByRole('option', { name: /Interactive Brokers/ })).toBeInTheDocument();
  expect(within(broker).getByRole('option', { name: /Thinkorswim/ })).toBeInTheDocument();
  const dropzones = screen.getAllByRole('button', { name: /Press Enter to browse/ });
  expect(dropzones).toHaveLength(2);
  dropzones.forEach(z => expect(z).toHaveAttribute('tabindex', '0'));
});

test('Help lists the metric reference and the feature guide', async () => {
  await renderApp();
  fireEvent.click(within(screen.getByRole('banner')).getByRole('button', { name: 'Help' }));
  expect(await screen.findByRole('heading', { name: 'Help and Reference' })).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Dashboard KPIs' })).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Features' })).toBeInTheDocument();
});

test('Calendar keeps the Month and Year views', async () => {
  await renderApp();
  fireEvent.click(within(nav()).getByRole('button', { name: 'Calendar' }));
  expect(await screen.findByRole('button', { name: 'Month' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Year' })).toBeInTheDocument();
});

test('Day Review keeps the loss-streak alert and its Dismiss control', async () => {
  const loss = (id, time, pnl) => ({
    id, account_id: 1, trade_group: `g${id}`, date: '2026-09-10', ticker: `L${id}`, instrument_type: 'STOCK',
    side: 'LONG', net_pnl: pnl, gross_pnl: pnl, commissions: 0,
    executions: [{ date: '2026-09-10', time, action: 'BOT', qty: 1, price: 10, commission: 0 }],
  });
  tradesApi.list.mockImplementation(() => Promise.resolve({ data: [loss(1, '09:40:00', -10), loss(2, '10:10:00', -20), loss(3, '11:00:00', -30)] }));
  await renderApp();
  fireEvent.click(within(nav()).getByRole('button', { name: 'Day Review' }));
  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent(/3 losses in a row/);
  fireEvent.click(within(alert).getByRole('button', { name: 'Dismiss loss-streak alert' }));
  await waitFor(() => expect(screen.queryByText(/3 losses in a row/)).not.toBeInTheDocument());
  // Previous, Next and Regenerate AI stay in the page header.
  expect(screen.getByRole('button', { name: /Previous/ })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Next/ })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Regenerate AI/ })).toBeInTheDocument();
});

test('Settings has General, Strategies, Sources, Tags and Appearance sections, and Tags leaves out strategy and source types', async () => {
  await renderApp();
  fireEvent.click(within(screen.getByRole('banner')).getByRole('button', { name: 'Settings' }));
  const tablist = await screen.findByRole('tablist', { name: 'Settings sections' });
  expect(within(tablist).getAllByRole('tab').map(t => t.textContent.replace(/\d+/g, '').trim()))
    .toEqual(['General', 'Strategies', 'Sources', 'Tags', 'Appearance']);
  expect(await screen.findByText('VWAP Cross')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Edit VWAP Cross' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Delete VWAP Cross' })).toBeInTheDocument();

  fireEvent.click(within(tablist).getByRole('tab', { name: /Tags/ }));
  expect(await screen.findByRole('region', { name: 'Mistakes tags' })).toBeInTheDocument();
  expect(screen.getByRole('region', { name: 'Execution tags' })).toBeInTheDocument();
  expect(screen.queryByRole('region', { name: /Strategy tags/ })).not.toBeInTheDocument();
  expect(screen.queryByRole('region', { name: /Source tags/ })).not.toBeInTheDocument();
});

test('Settings > Appearance switches the theme and remembers it in this browser', async () => {
  await renderApp();
  fireEvent.click(within(screen.getByRole('banner')).getByRole('button', { name: 'Settings' }));
  const tablist = await screen.findByRole('tablist', { name: 'Settings sections' });
  fireEvent.click(within(tablist).getByRole('tab', { name: 'Appearance' }));
  const group = await screen.findByRole('radiogroup', { name: 'Theme' });
  expect(within(group).getByRole('radio', { name: /Original/ })).toBeChecked();

  try {
    fireEvent.click(within(group).getByRole('radio', { name: /Natural/ }));
    expect(document.documentElement.getAttribute('data-theme')).toBe('natural');
    expect(window.localStorage.getItem('journal-theme')).toBe('natural');

    fireEvent.click(within(group).getByRole('radio', { name: /Original/ }));
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
    expect(window.localStorage.getItem('journal-theme')).toBeNull();
  } finally {
    // never let a theme leak into the tests that follow
    document.documentElement.removeAttribute('data-theme');
    window.localStorage.removeItem('journal-theme');
  }
});

test('Settings merges one strategy into another', async () => {
  await renderApp();
  fireEvent.click(within(screen.getByRole('banner')).getByRole('button', { name: 'Settings' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Merge Continuation RS into another strategy' }));
  fireEvent.change(screen.getByRole('combobox', { name: /Merge "Continuation RS" into/ }), { target: { value: 'VWAP Cross' } });
  fireEvent.click(screen.getByRole('button', { name: 'Merge' }));
  await waitFor(() => expect(libraryApi.merge).toHaveBeenCalledWith({
    kind: 'strategy', tag_type: '', source_name: 'Continuation RS', target_name: 'VWAP Cross',
  }));
  expect(await screen.findByRole('status')).toHaveTextContent(/2 trades moved/);
});

test('Settings delete asks to reassign and can leave trades blank', async () => {
  await renderApp();
  fireEvent.click(within(screen.getByRole('banner')).getByRole('button', { name: 'Settings' }));
  fireEvent.click(await screen.findByRole('tab', { name: /Sources/ }));
  fireEvent.click(await screen.findByRole('button', { name: 'Delete OneOption' }));
  expect(screen.getByRole('combobox', { name: /1 trade use "OneOption". Reassign them to/ })).toHaveValue('');
  fireEvent.click(screen.getAllByRole('button', { name: 'Delete' }).find(b => b.className.includes('btn-danger')));
  await waitFor(() => expect(libraryApi.remove).toHaveBeenCalledWith({
    kind: 'source', tag_type: '', name: 'OneOption', reassign_to: null,
  }));
});

const todayISO = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

test('Open Positions shows the remaining quantity, not the quantity entered', async () => {
  await renderApp();
  const row = (await screen.findByText('GOOG')).closest('tr');
  // 300 bought, 100 sold.
  expect(within(row).getByText('200')).toBeInTheDocument();
  expect(within(row).getByText(/of 300/)).toBeInTheDocument();
});

test('Recording an exit defaults to today, sends the entered time and fees, and refreshes', async () => {
  await renderApp();
  const row = (await screen.findByText('GOOG')).closest('tr');
  fireEvent.click(within(row).getByRole('button', { name: 'Close' }));

  const date = screen.getByLabelText('Exit date');
  expect(date).toHaveValue(todayISO());
  fireEvent.change(screen.getByLabelText('Exit time'), { target: { value: '15:45' } });
  fireEvent.change(screen.getByLabelText('Exit price'), { target: { value: '107.5' } });
  fireEvent.change(screen.getByLabelText('Fees'), { target: { value: '1.25' } });

  const callsBefore = tradesApi.list.mock.calls.length;
  fireEvent.click(screen.getByRole('button', { name: 'Record exit' }));
  await waitFor(() => expect(tradesApi.addExecution).toHaveBeenCalledWith(103, {
    action: 'SOLD', qty: 200, price: 107.5, date: todayISO(), time: '15:45:00', commission: 1.25,
  }));
  // The rest of the dashboard reloads instead of showing stale totals.
  await waitFor(() => expect(tradesApi.list.mock.calls.length).toBeGreaterThan(callsBefore));
});

test('Recording an exit refuses a date before the last fill', async () => {
  await renderApp();
  const row = (await screen.findByText('GOOG')).closest('tr');
  fireEvent.click(within(row).getByRole('button', { name: 'Close' }));
  fireEvent.change(screen.getByLabelText('Exit date'), { target: { value: '2026-09-08' } });
  fireEvent.change(screen.getByLabelText('Exit price'), { target: { value: '107.5' } });
  fireEvent.click(screen.getByRole('button', { name: 'Record exit' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(/cannot be earlier than the last fill on 2026-09-09/);
  expect(tradesApi.addExecution).not.toHaveBeenCalled();
});

test('A failed dashboard load keeps the page and offers Retry', async () => {
  kpisApi.get.mockImplementation(() => Promise.reject(new Error('Network Error')));
  await renderApp();
  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent(/Could not load the dashboard: Network Error/);
  // The shell stays: title and the date filter are still there.
  expect(screen.getByRole('heading', { name: 'Dashboard' })).toBeInTheDocument();
  expect(within(screen.getByRole('main')).getByRole('button', { name: /All time/ })).toBeInTheDocument();

  kpisApi.get.mockImplementation(() => Promise.resolve({ data: { total_net_pnl: 100, daily_pnl: [], by_strategy: [] } }));
  fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
  await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
});

test('Goals save failure is shown and keeps the panel open', async () => {
  goalsApi.put.mockImplementation(() => Promise.reject(new Error('Server error')));
  await renderApp();
  fireEvent.click(screen.getByRole('button', { name: 'Edit goals' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Save' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(/Could not save goals: Server error/);
  expect(screen.getByRole('heading', { name: 'Goals' })).toBeInTheDocument();
});

test('Reports > Leaks ranks a leak, shows the Claude sentence, and Back from a trade returns to Leaks', async () => {
  reportsApi.get.mockImplementation(() => Promise.resolve({ data: {
    has_data: true, trade_count: 40, equity_curve: [], by_month: [],
    summary: { net_pnl: 500, max_drawdown: -100, max_drawdown_date: '2026-09-02', trading_days: 25,
      green_days: 15, red_days: 10, best_day: 200, worst_day: -100, avg_green_day: 50, avg_red_day: -30,
      longest_win_streak: 4, longest_loss_streak: 2, avg_trades_per_day: 1.6 },
  } }));
  await renderApp();
  fireEvent.click(within(nav()).getByRole('button', { name: 'Reports' }));
  fireEvent.click(await screen.findByRole('tab', { name: 'Leaks' }));
  expect(await screen.findByRole('heading', { name: 'One leak cost you $750 in this range.' })).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Entries 11:00-13:30' })).toBeInTheDocument();
  expect(await screen.findByText('Midday entries cost you $750.')).toBeInTheDocument();
  expect(leaksApi.explain).toHaveBeenCalled();

  fireEvent.click(screen.getByRole('button', { name: 'Show 25 trades' }));
  const row = await screen.findByRole('row', { name: /Open META trade/ });
  fireEvent.click(row);
  await screen.findByRole('tablist', { name: 'Trade review sections' });
  expect(within(nav()).getByRole('button', { name: 'Reports' })).toHaveAttribute('aria-current', 'page');
  fireEvent.click(screen.getByRole('button', { name: /Back to Leaks/ }));
  expect(await screen.findByRole('tab', { name: 'Leaks' })).toHaveAttribute('aria-selected', 'true');
  expect(await screen.findByText('Midday entries cost you $750.')).toBeInTheDocument();
});

test('a newer release shows a dismissible update notice with the update steps', async () => {
  localStorage.clear();
  updateApi.get.mockImplementation(() => Promise.resolve({ data: {
    current: '1.5.0', latest: '1.6.0', available: true,
    url: 'https://github.com/simonro/Trading-Journal-AI/releases/tag/v1.6.0',
  } }));
  render(<App />);
  const notice = (await screen.findByText('Version 1.6.0 is available')).closest('[role="status"]');
  expect(notice).toHaveTextContent('(you have 1.5.0)');
  expect(within(notice).getByRole('link', { name: 'Release notes' }))
    .toHaveAttribute('href', 'https://github.com/simonro/Trading-Journal-AI/releases/tag/v1.6.0');
  expect(within(notice).getByText('setup.bat')).toBeInTheDocument();
  expect(within(notice).getByText('backend/uploads')).toBeInTheDocument();
  fireEvent.click(within(notice).getByRole('button', { name: 'Dismiss update notice' }));
  expect(screen.queryByText(/Version 1.6.0 is available/)).not.toBeInTheDocument();
  expect(localStorage.getItem('update-banner-dismissed')).toBe('1.6.0');
});

test('a Docker install shows the Docker update steps', async () => {
  localStorage.clear();
  updateApi.get.mockImplementation(() => Promise.resolve({ data: {
    current: '1.6.0', latest: '1.7.0', available: true, install: 'docker',
    url: 'https://github.com/simonro/Trading-Journal-AI/releases/tag/v1.7.0',
  } }));
  render(<App />);
  const notice = (await screen.findByText('Version 1.7.0 is available')).closest('[role="status"]');
  expect(within(notice).getByText('docker compose up -d --build')).toBeInTheDocument();
  expect(within(notice).queryByText('setup.bat')).not.toBeInTheDocument();
});

test('no update notice when the installed version is current', async () => {
  render(<App />);
  await waitFor(() => expect(updateApi.get).toHaveBeenCalled());
  expect(screen.queryByText(/is available/)).not.toBeInTheDocument();
});

test('Day Review loads lifetime numbers again for a newly chosen account', async () => {
  await renderApp();
  fireEvent.click(within(nav()).getByRole('button', { name: 'Day Review' }));
  await screen.findByRole('button', { name: /Regenerate AI/ });
  kpisApi.get.mockClear();
  fireEvent.click(screen.getByRole('button', { name: 'Account: All Accounts' }));
  fireEvent.click(screen.getByRole('button', { name: 'Swing' }));
  await waitFor(() => expect(kpisApi.get.mock.calls.some(([p]) => p.account_id === 2 && !p.date_from)).toBe(true));
});

test('Reports ignores a late answer for the account that is no longer selected', async () => {
  const report = (n) => ({ data: {
    has_data: true, trade_count: n, equity_curve: [], by_month: [],
    summary: { net_pnl: 0, max_drawdown: 0, max_drawdown_date: '2026-09-02', trading_days: 1,
      green_days: 0, red_days: 0, best_day: 0, worst_day: 0, avg_green_day: 0, avg_red_day: 0,
      longest_win_streak: 0, longest_loss_streak: 0, avg_trades_per_day: n },
  } });
  let releaseFirst;
  reportsApi.get.mockImplementation((params) => (params.account_id === 2
    ? Promise.resolve(report(22))
    : new Promise(resolve => { releaseFirst = () => resolve(report(11)); })));
  await renderApp();
  fireEvent.click(within(nav()).getByRole('button', { name: 'Reports' }));
  fireEvent.click(screen.getByRole('button', { name: 'Account: All Accounts' }));
  fireEvent.click(screen.getByRole('button', { name: 'Swing' }));
  expect(await screen.findByText(/22 trades across/)).toBeInTheDocument();
  await act(async () => { releaseFirst(); });
  expect(screen.getByText(/22 trades across/)).toBeInTheDocument();
  expect(screen.queryByText(/11 trades across/)).not.toBeInTheDocument();
});

test('Day Review keeps a regenerated review when the first answer arrives late', async () => {
  const { dailySummaryApi } = require('./api');
  let releaseFirst;
  dailySummaryApi.get.mockImplementation((params) => (params.force
    ? Promise.resolve({ data: { strengths: ['Fresh review'] } })
    : new Promise(resolve => { releaseFirst = () => resolve({ data: { strengths: ['Stale review'] } }); })));
  await renderApp();
  fireEvent.click(within(nav()).getByRole('button', { name: 'Day Review' }));
  fireEvent.click(await screen.findByRole('button', { name: /Regenerate AI/ }));
  expect(await screen.findByText('Fresh review')).toBeInTheDocument();
  await act(async () => { releaseFirst(); });
  expect(screen.getByText('Fresh review')).toBeInTheDocument();
  expect(screen.queryByText('Stale review')).not.toBeInTheDocument();
});

// ── Edge Trader sync: timeline, day note ──────────────────────────────────────

const EDGE_EVENTS = [
  { id: 'e1', ts: '2026-09-10T13:55:00+00:00', kind: 'stop_set', price: 365.5, qty: null, text: null, origin: 'schwab' },
  { id: 'e2', ts: '2026-09-10T14:02:00+00:00', kind: 'stop_moved', price: 366.09, qty: null, text: null, origin: 'schwab' },
  { id: 'n1', ts: '2026-09-10T14:05:00+00:00', kind: 'note', price: null, qty: null, text: 'Held the level', origin: 'edge' },
];
const DAY_NOTE = {
  id: 7, account_id: 1, entry_date: '2026-09-10', raw_text: 'Plan: only A setups today.', image_path: null,
  ai_analysis: null, source: 'edge-trader', updated_at: '2026-09-10 21:05:00',
};
const UPLOAD = { id: 8, account_id: 1, entry_date: '2026-09-10', raw_text: 'Uploaded notes', image_path: null, ai_analysis: null };

async function openTrade() {
  await renderApp();
  fireEvent.click(within(nav()).getByRole('button', { name: 'Trade View' }));
  await waitFor(() => expect(tradesApi.list).toHaveBeenCalled());
  // The click on the ticker reaches its row.
  fireEvent.click((await screen.findAllByText('TSLA'))[0]);
  return screen.findByRole('region', { name: 'Timeline' });
}

test('Trade Details lists the Edge Trader timeline in US Eastern time, and a click pins an event', async () => {
  edgeSyncApi.tradeEvents.mockImplementation(() => Promise.resolve({ data: { events: EDGE_EVENTS } }));
  const timeline = await openTrade();
  expect(edgeSyncApi.tradeEvents).toHaveBeenCalledWith('9/10/26_TSLA_STOCK_1', 1);
  expect(await within(timeline).findByText('Held the level')).toBeInTheDocument();
  expect(within(timeline).getByText('Stop set')).toBeInTheDocument();
  expect(within(timeline).getByText('09:55:00')).toBeInTheDocument();
  const rows = within(timeline).getAllByRole('listitem');
  expect(rows).toHaveLength(3);
  const row = rows[1];
  expect(row).toHaveTextContent('Stop moved to 366.09');
  fireEvent.click(row);
  expect(row).toHaveAttribute('aria-current', 'true');
  fireEvent.click(row);
  expect(row).not.toHaveAttribute('aria-current');
});

test('a trade with no synced events says how the timeline fills', async () => {
  const timeline = await openTrade();
  expect(await within(timeline).findByText(/No events for this trade yet/)).toBeInTheDocument();
});

test('the Diary shows the Edge Trader day note first and keeps it when the day is cleared', async () => {
  diaryApi.list.mockImplementation(() => Promise.resolve({ data: [UPLOAD, DAY_NOTE] }));
  await renderApp();
  fireEvent.click(within(nav()).getByRole('button', { name: 'Diary' }));
  const note = await screen.findByRole('article', { name: 'Day note from Edge Trader' });
  expect(within(note).getByText('Plan: only A setups today.')).toBeInTheDocument();
  expect(within(note).getByText('From Edge Trader')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Delete all diary entries for 2026-09-10' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Confirm' }));
  await waitFor(() => expect(diaryApi.deleteByDate).toHaveBeenCalled());
  expect(diaryApi.deleteByDate.mock.calls[0][0]).toBe('2026-09-10');
  await waitFor(() => expect(screen.queryByText('Uploaded notes')).not.toBeInTheDocument());
  expect(await screen.findByRole('article', { name: 'Day note from Edge Trader' })).toBeInTheDocument();
});

test('Day Review shows the Edge Trader day note for the day', async () => {
  // Day Review opens on today.
  const today = new Date().toISOString().split('T')[0];
  diaryApi.list.mockImplementation(() => Promise.resolve({ data: [{ ...DAY_NOTE, entry_date: today }] }));
  await renderApp();
  fireEvent.click(within(nav()).getByRole('button', { name: 'Day Review' }));
  const note = await screen.findByRole('region', { name: 'Day note from Edge Trader' });
  expect(within(note).getByText('Plan: only A setups today.')).toBeInTheDocument();
});
