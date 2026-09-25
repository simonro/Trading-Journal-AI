import { useState, useEffect, useCallback, useRef } from 'react';
import { Search, ChevronUp, ChevronDown } from 'lucide-react';
import { tradesApi } from '../api';
import TradeRow from './TradeRow';
import { PageHeader, KpiStrip, KpiCell, MoneyValue } from './ui';

const PAGE_SIZE = 25;
const MONTHS_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const fmtDay = (d) => {
  if (!d) return '';
  const [y, m, day] = d.split('-');
  return `${MONTHS_SHORT[Number(m) - 1]} ${Number(day)}, ${y}`;
};

function getOpenTime(trade) {
  const execs = trade.executions || [];
  if (!execs.length) return '';
  return [...execs].sort((a, b) => (a.time || '').localeCompare(b.time || ''))[0].time || '';
}

function SortIcon({ col, sortCol, sortDir }) {
  if (sortCol !== col) return <ChevronDown size={12} style={{ opacity: 0.35 }} aria-hidden="true" />;
  return sortDir === 'asc'
    ? <ChevronUp size={12} aria-hidden="true" />
    : <ChevronDown size={12} aria-hidden="true" />;
}

export default function Trades({ accountId, initialDateFrom = '', initialDateTo = '', onOpenDetail }) {
  const [trades, setTrades] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [page, setPage] = useState(1);

  const [customSetups, setCustomSetups] = useState([]);
  const reloadCustomSetups = useCallback(async () => {
    try {
      const res = await tradesApi.listCustomSetups();
      setCustomSetups(res.data || []);
    } catch { /* dropdown still works with the built-in setups */ }
  }, []);
  useEffect(() => { reloadCustomSetups(); }, [reloadCustomSetups]);

  const [sortCol, setSortCol] = useState('datetime');
  const [sortDir, setSortDir] = useState('desc');

  const [ticker, setTicker] = useState('');
  const [instrType, setInstrType] = useState('');
  const [dateFrom, setDateFrom] = useState(initialDateFrom);
  const [dateTo, setDateTo] = useState(initialDateTo);

  // A slow reply from an earlier filter must not overwrite the newest one.
  const loadRun = useRef(0);
  const load = useCallback(async () => {
    const run = ++loadRun.current;
    setLoading(true);
    setError(null);
    try {
      const params = {};
      if (accountId != null) params.account_id = accountId;
      if (ticker) params.ticker = ticker;
      if (instrType) params.instrument_type = instrType;
      if (dateFrom) params.date_from = dateFrom;
      if (dateTo) params.date_to = dateTo;
      const res = await tradesApi.list(params);
      if (run !== loadRun.current) return;
      setTrades(res.data);
    } catch (e) {
      if (run !== loadRun.current) return;
      setError(e.message);
    } finally {
      if (run === loadRun.current) setLoading(false);
    }
  }, [accountId, ticker, instrType, dateFrom, dateTo]);

  useEffect(() => { load(); setPage(1); }, [load]);

  const handleSort = (col) => {
    if (sortCol === col) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    } else {
      setSortCol(col);
      setSortDir(col === 'datetime' ? 'desc' : 'asc');
    }
    setPage(1);
  };

  const sorted = [...trades].sort((a, b) => {
    let av, bv;
    switch (sortCol) {
      case 'datetime':
        av = (a.date || '') + '|' + getOpenTime(a);
        bv = (b.date || '') + '|' + getOpenTime(b);
        break;
      case 'ticker': av = a.ticker || ''; bv = b.ticker || ''; break;
      case 'side': av = a.side || ''; bv = b.side || ''; break;
      case 'type': av = a.instrument_type || ''; bv = b.instrument_type || ''; break;
      case 'net_pnl': av = a.net_pnl ?? 0; bv = b.net_pnl ?? 0; break;
      case 'r_multiple': av = a.r_multiple ?? -999; bv = b.r_multiple ?? -999; break;
      default: av = a.date || ''; bv = b.date || '';
    }
    const cmp = av < bv ? -1 : av > bv ? 1 : 0;
    return sortDir === 'asc' ? cmp : -cmp;
  });

  const totalNet = trades.reduce((s, t) => s + (t.net_pnl || 0), 0);
  const winners = trades.filter(t => (t.net_pnl || 0) > 0).length;
  const winRate = trades.length ? (winners / trades.length * 100).toFixed(1) : 0;

  const paginated = sorted.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const totalPages = Math.ceil(sorted.length / PAGE_SIZE);
  const firstShown = sorted.length ? (page - 1) * PAGE_SIZE + 1 : 0;
  const lastShown = Math.min(page * PAGE_SIZE, sorted.length);

  const period = dateFrom || dateTo
    ? `${dateFrom ? fmtDay(dateFrom) : 'Start'} to ${dateTo ? fmtDay(dateTo) : 'today'}`
    : 'All dates';

  // Plain render function (not a component) so headers keep focus across re-sorts.
  const sortTh = (col, label, className) => (
    <th key={col} className={className} aria-sort={sortCol === col ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}>
      <button type="button" className="th-sort" onClick={() => handleSort(col)} data-active={sortCol === col ? 'true' : undefined}>
        {label} <SortIcon col={col} sortCol={sortCol} sortDir={sortDir} />
      </button>
    </th>
  );

  return (
    <div>
      <PageHeader
        title="Trade View"
        subtitle="Click a row to open the trade. The Setup column is editable in place."
      />

      <KpiStrip label="Filtered trade summary">
        <KpiCell label="Selected period" value={<span style={{ fontSize: 22 }}>{period}</span>} />
        <KpiCell label="Net P&L" value={<MoneyValue value={totalNet} />} tone={totalNet >= 0 ? 'pos' : 'neg'} />
        <KpiCell label="Trades" value={<span className="num">{trades.length.toLocaleString('en-US')}</span>} />
        <KpiCell label="Win rate" value={<span className="num">{winRate}%</span>} foot={<><span className="num">{winners}</span> winners</>} />
      </KpiStrip>

      {/* Filter bar */}
      <div role="search" aria-label="Filter trades" style={{ display: 'flex', gap: 10, marginBottom: 16, flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <div>
          <label className="field-label" htmlFor="tv-ticker">Ticker</label>
          <div style={{ position: 'relative' }}>
            <Search size={15} style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-secondary)' }} aria-hidden="true" />
            <input
              id="tv-ticker"
              placeholder="Ticker..."
              value={ticker}
              onChange={e => setTicker(e.target.value)}
              style={{ paddingLeft: 32, width: 170 }}
            />
          </div>
        </div>
        <div>
          <label className="field-label" htmlFor="tv-type">Type</label>
          <select id="tv-type" value={instrType} onChange={e => setInstrType(e.target.value)} style={{ width: 140 }}>
            <option value="">All Types</option>
            <option value="STOCK">Stock</option>
            <option value="OPTION">Option</option>
            <option value="FUTURE">Future</option>
          </select>
        </div>
        <div>
          <label className="field-label" htmlFor="tv-from">From</label>
          <input id="tv-from" type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} style={{ width: 160 }} />
        </div>
        <div>
          <label className="field-label" htmlFor="tv-to">To</label>
          <input id="tv-to" type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} style={{ width: 160 }} />
        </div>
        {(ticker || instrType || dateFrom || dateTo) && (
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => { setTicker(''); setInstrType(''); setDateFrom(''); setDateTo(''); }}
          >
            Clear
          </button>
        )}
      </div>

      {error && (
        <div className="notice neg" role="alert" style={{ marginBottom: 16 }}>
          {error}
        </div>
      )}

      <section className="card panel-flush" aria-label="Trades">
        <div className="table-container">
          <table style={{ minWidth: 980 }}>
            <thead>
              <tr>
                {sortTh('datetime', 'Date / Time')}
                {sortTh('ticker', 'Ticker')}
                {sortTh('type', 'Type')}
                {sortTh('side', 'Side')}
                {sortTh('net_pnl', 'Net P&L', 'num')}
                <th>Setup / Strategy</th>
                <th title="MFE / MAE / exit efficiency: best gain reached, worst loss reached, and the share of the move you captured">MFE / MAE / Exit</th>
                {sortTh('r_multiple', 'R', 'num')}
                <th><span className="sr-only">Expand</span></th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                [...Array(5)].map((_, i) => (
                  <tr key={i}>
                    {[...Array(9)].map((_, j) => (
                      <td key={j}><div className="skeleton" style={{ height: 16, width: '80%' }} /></td>
                    ))}
                  </tr>
                ))
              ) : paginated.length === 0 ? (
                <tr>
                  <td colSpan={9} className="empty">
                    No trades found. Import a CSV to get started.
                  </td>
                </tr>
              ) : (
                paginated.map(trade => (
                  <TradeRow
                    key={trade.id}
                    trade={trade}
                    openTime={getOpenTime(trade)}
                    onOpenDetail={(t) => onOpenDetail(t, paginated)}
                    customSetups={customSetups}
                    onCustomSetupsChanged={reloadCustomSetups}
                    onTradeDeleted={load}
                  />
                ))
              )}
            </tbody>
          </table>
        </div>

        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap',
          gap: 8, padding: '12px 20px', borderTop: '1px solid var(--divider-soft)',
        }}>
          <span className="text-muted" style={{ fontSize: 13 }}>
            Showing <span className="num">{firstShown}-{lastShown}</span> of <span className="num">{sorted.length}</span> trades
          </span>
          {totalPages > 1 && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <button type="button" className="btn btn-secondary btn-sm" onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}>
                ← Prev
              </button>
              <span className="text-muted num" style={{ fontSize: 13 }}>Page {page} of {totalPages}</span>
              <button type="button" className="btn btn-secondary btn-sm" onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page === totalPages}>
                Next →
              </button>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
