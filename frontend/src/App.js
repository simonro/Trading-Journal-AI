import { useState, useEffect, useCallback, useRef } from 'react';
import './index.css';
import { accountsApi, kpisApi } from './api';
import AppHeader from './components/AppHeader';
import Dashboard from './components/Dashboard';
import Trades from './components/Trades';
import Calendar from './components/Calendar';
import Import from './components/Import';
import Diary from './components/Diary';
import AddTradeModal from './components/AddTradeModal';
import TradeDetail from './components/TradeDetail';
import Brain from './components/Brain';
import DailySummary from './components/DailySummary';
import Reports from './components/Reports';
import Help from './components/Help';
import Settings from './components/Settings';
import ExitLab from './components/ExitLab';
import { DemoLabel } from './components/exitlab/shared';
import UpdateBanner from './components/UpdateBanner';
import { accountFilter, singleAccountId } from './accountSelection';

export default function App() {
  const [page, setPage] = useState('dashboard');
  const [accounts, setAccounts] = useState([]);
  // [] is All Accounts. Pages get accountId: null, one id, or "1,4" for several.
  const [selectedAccountIds, setSelectedAccountIds] = useState([]);
  const accountId = accountFilter(selectedAccountIds);
  const oneAccountId = singleAccountId(selectedAccountIds);
  const selectOneAccount = (id) => setSelectedAccountIds(id == null ? [] : [id]);
  const [showAddTrade, setShowAddTrade] = useState(false);
  const [tradesFilter, setTradesFilter] = useState({ dateFrom: '', dateTo: '' });
  const [selectedTrade, setSelectedTrade] = useState(null);
  const [detailTab, setDetailTab] = useState('Stats');
  const [detailOrigin, setDetailOrigin] = useState('trades');
  // Reports tab and range to restore when Back from a trade returns to Reports.
  const [reportsReturn, setReportsReturn] = useState(null);
  const [tradeNavList, setTradeNavList] = useState([]);
  const [selectedDate, setSelectedDate] = useState(new Date().toISOString().split('T')[0]);
  // Open Day Review on the most recent session, not on today. Today has no
  // trades on a weekend, a holiday, or any day before the market opens.
  const seededDate = useRef(false);
  useEffect(() => {
    if (seededDate.current) return;
    seededDate.current = true;
    kpisApi.get({})
      .then(r => {
        const days = r.data?.daily_pnl || [];
        if (days.length) setSelectedDate(days[days.length - 1].date);
      })
      .catch(() => {});
  }, []);
  const [brainOpen, setBrainOpen] = useState(false);

  const loadAccounts = useCallback(async () => {
    try {
      const res = await accountsApi.list();
      setAccounts(res.data);
      // Drop a deleted account from the selection.
      setSelectedAccountIds(ids => {
        const kept = ids.filter(id => res.data.some(a => a.id === id));
        return kept.length === ids.length ? ids : kept;
      });
    } catch (e) {
      console.error('Failed to load accounts', e);
    }
  }, []);

  useEffect(() => { loadAccounts(); }, [loadAccounts]);

  const handleTradeAdded = () => {
    setShowAddTrade(false);
    if (page === 'dashboard') setPage('_refresh');
    setTimeout(() => setPage('dashboard'), 0);
  };

  const handleCalendarDayClick = (date) => {
    setSelectedDate(date);
    setPage('day-review');
  };

  const navigate = (p) => {
    if (p !== 'trades') setTradesFilter({ dateFrom: '', dateTo: '' });
    if (p !== 'trade-detail') setSelectedTrade(null);
    setReportsReturn(null);
    setPage(p);
  };

  // `from` lets another page (Reports > Leaks) be the one Back returns to.
  const handleOpenDetail = (trade, list = [], initialTab = 'Stats', from = null) => {
    setDetailTab(initialTab);
    setDetailOrigin(from?.page || (initialTab === 'Exits' ? 'exit-lab' : 'trades'));
    if (from?.page === 'reports') setReportsReturn(from.state || null);
    setSelectedTrade(trade);
    setTradeNavList(list);
    setPage('trade-detail');
  };

  return (
    <div className="app-shell">
      <AppHeader
        page={page}
        detailOrigin={detailOrigin}
        onNavigate={navigate}
        accounts={accounts}
        selectedAccountIds={selectedAccountIds}
        onSelectAccounts={setSelectedAccountIds}
        onAddTrade={() => setShowAddTrade(true)}
        onAccountCreated={loadAccounts}
        brainOpen={brainOpen}
        onToggleBrain={() => setBrainOpen(v => !v)}
      />

      <main className="app-main" id="main">
        <UpdateBanner />
        {page !== 'exit-lab' && page !== 'trade-detail' && <DemoLabel show={accounts.some(a => a.broker === 'Synthetic demo' && (selectedAccountIds.length === 0 || selectedAccountIds.includes(a.id)))} />}
        {page === 'dashboard' && (
          <Dashboard
            accountId={accountId}
            accounts={accounts}
            selectedAccountIds={selectedAccountIds}
            onDayClick={handleCalendarDayClick}
            onOpenDetail={handleOpenDetail}
            onViewAllTrades={() => navigate('trades')}
          />
        )}
        {page === 'trades' && (
          <Trades
            key={`${tradesFilter.dateFrom}|${tradesFilter.dateTo}`}
            accountId={accountId}
            initialDateFrom={tradesFilter.dateFrom}
            initialDateTo={tradesFilter.dateTo}
            onOpenDetail={handleOpenDetail}
          />
        )}
        {page === 'trade-detail' && selectedTrade && (
          <TradeDetail
            key={selectedTrade.id}
            initialTab={detailTab}
            backLabel={detailOrigin === 'exit-lab' ? 'Back to Exit Lab'
              : detailOrigin === 'reports' ? 'Back to Leaks' : 'Back to trades'}
            trade={selectedTrade}
            tradeNavList={tradeNavList}
            onBack={() => { setSelectedTrade(null); setPage(detailOrigin); }}
            onTradeUpdate={(updated) => setSelectedTrade(updated)}
            onNavigate={(trade) => setSelectedTrade(trade)}
            onOpenDetail={handleOpenDetail}
          />
        )}
        {page === 'calendar' && (
          <Calendar
            accountId={accountId}
            onDayClick={handleCalendarDayClick}
          />
        )}
        {page === 'import' && (
          <Import
            accountId={oneAccountId}
            accounts={accounts}
            onImportDone={() => {}}
          />
        )}
        {page === 'diary' && <Diary accountId={accountId} />}
        {page === 'day-review' && (
          <DailySummary
            accountId={accountId}
            date={selectedDate}
            onDateChange={setSelectedDate}
            onOpenDetail={handleOpenDetail}
          />
        )}
        {page === 'reports' && (
          <Reports accountId={accountId} onOpenDetail={handleOpenDetail} initial={reportsReturn} />
        )}
        {page === 'help' && <Help />}
        {page === 'settings' && <Settings />}
        {page === 'exit-lab' && (
          <ExitLab
            accountId={oneAccountId}
            accounts={accounts}
            onSelectAccount={selectOneAccount}
            onOpenDetail={handleOpenDetail}
          />
        )}
      </main>

      <Brain accountId={accountId} open={brainOpen} onOpenChange={setBrainOpen} />

      {showAddTrade && (
        <AddTradeModal
          accounts={accounts}
          defaultAccountId={oneAccountId}
          onClose={() => setShowAddTrade(false)}
          onSaved={handleTradeAdded}
        />
      )}
    </div>
  );
}
