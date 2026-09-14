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
import Reconciliation from './components/Reconciliation';

export default function App() {
  const [page, setPage] = useState('dashboard');
  const [accounts, setAccounts] = useState([]);
  const [selectedAccountId, setSelectedAccountId] = useState(null);
  const [showAddTrade, setShowAddTrade] = useState(false);
  const [tradesFilter, setTradesFilter] = useState({ dateFrom: '', dateTo: '' });
  const [selectedTrade, setSelectedTrade] = useState(null);
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
    setPage(p);
  };

  const handleOpenDetail = (trade, list = []) => {
    setSelectedTrade(trade);
    setTradeNavList(list);
    setPage('trade-detail');
  };

  return (
    <div className="app-shell">
      <AppHeader
        page={page}
        onNavigate={navigate}
        accounts={accounts}
        selectedAccountId={selectedAccountId}
        onSelectAccount={setSelectedAccountId}
        onAddTrade={() => setShowAddTrade(true)}
        onAccountCreated={loadAccounts}
        brainOpen={brainOpen}
        onToggleBrain={() => setBrainOpen(v => !v)}
      />

      <main className="app-main" id="main">
        {page === 'dashboard' && (
          <Dashboard
            accountId={selectedAccountId}
            accounts={accounts}
            selectedAccountId={selectedAccountId}
            onSelectAccount={setSelectedAccountId}
            onDayClick={handleCalendarDayClick}
            onOpenDetail={handleOpenDetail}
            onViewAllTrades={() => navigate('trades')}
          />
        )}
        {page === 'trades' && (
          <Trades
            key={`${tradesFilter.dateFrom}|${tradesFilter.dateTo}`}
            accountId={selectedAccountId}
            initialDateFrom={tradesFilter.dateFrom}
            initialDateTo={tradesFilter.dateTo}
            onOpenDetail={handleOpenDetail}
          />
        )}
        {page === 'trade-detail' && selectedTrade && (
          <TradeDetail
            key={selectedTrade.id}
            trade={selectedTrade}
            tradeNavList={tradeNavList}
            onBack={() => { setSelectedTrade(null); setPage('trades'); }}
            onTradeUpdate={(updated) => setSelectedTrade(updated)}
            onNavigate={(trade) => setSelectedTrade(trade)}
            onOpenDetail={handleOpenDetail}
          />
        )}
        {page === 'calendar' && (
          <Calendar
            accountId={selectedAccountId}
            onDayClick={handleCalendarDayClick}
          />
        )}
        {page === 'import' && (
          <Import
            accountId={selectedAccountId}
            accounts={accounts}
            onImportDone={() => {}}
          />
        )}
        {page === 'diary' && <Diary accountId={selectedAccountId} />}
        {page === 'day-review' && (
          <DailySummary
            accountId={selectedAccountId}
            date={selectedDate}
            onDateChange={setSelectedDate}
            onOpenDetail={handleOpenDetail}
          />
        )}
        {page === 'reports' && <Reports accountId={selectedAccountId} />}
        {page === 'reconciliation' && <Reconciliation accountId={selectedAccountId} />}
        {page === 'help' && <Help />}
        {page === 'settings' && <Settings />}
      </main>

      <Brain accountId={selectedAccountId} open={brainOpen} onOpenChange={setBrainOpen} />

      {showAddTrade && (
        <AddTradeModal
          accounts={accounts}
          defaultAccountId={selectedAccountId}
          onClose={() => setShowAddTrade(false)}
          onSaved={handleTradeAdded}
        />
      )}
    </div>
  );
}
