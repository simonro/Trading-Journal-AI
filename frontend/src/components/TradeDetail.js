import { useState, useEffect } from 'react';
import { ArrowLeft, ChevronLeft, ChevronRight, PlusCircle, Trash2, Pencil } from 'lucide-react';
import { tradesApi, chartApi } from '../api';
import TradingChart from './TradingChart';
import { PageHeader, KpiStrip, KpiCell, MoneyValue, PanelHead } from './ui';

const fmt$ = (v) => {
  if (v == null) return '—';
  const n = Number(v);
  return (n >= 0 ? '' : '-') + '$' + Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};

const fmtSigned$ = (v) => {
  if (v == null) return '—';
  const n = Number(v);
  return (n >= 0 ? '+$' : '-$') + Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};

function parseExecs(trade) {
  const raw = trade.executions;
  if (!raw) return [];
  if (Array.isArray(raw)) return raw;
  try { return JSON.parse(raw); } catch { return []; }
}

function computeStats(trade) {
  const execs = parseExecs(trade);
  const side = trade.side;
  const entryFills = execs.filter(e => side === 'LONG' ? e.action === 'BOT' : e.action === 'SOLD');
  const exitFills  = execs.filter(e => side === 'LONG' ? e.action === 'SOLD' : e.action === 'BOT');

  const avgPrice = (fills) => {
    const qty = fills.reduce((s, f) => s + (f.qty || 0), 0);
    if (!qty) return null;
    return fills.reduce((s, f) => s + (f.qty || 0) * (f.price || 0), 0) / qty;
  };

  const avgEntry = avgPrice(entryFills);
  const avgExit  = avgPrice(exitFills);
  const totalQty = entryFills.reduce((s, f) => s + (f.qty || 0), 0);
  const adjustedCost = avgEntry ? avgEntry * totalQty : null;
  const netRoi = adjustedCost ? (trade.net_pnl / adjustedCost * 100) : null;

  // Sort by full date+time (not time alone) so multi-day trades order correctly.
  const sortedExecs = [...execs]
    .filter(e => e.time)
    .sort((a, b) => `${a.date || ''}T${a.time}`.localeCompare(`${b.date || ''}T${b.time}`));
  const openExec  = sortedExecs[0];
  const closeExec = sortedExecs[sortedExecs.length - 1];
  const openTime  = openExec?.time;
  const closeTime = closeExec?.time;
  const openDate  = openExec?.date;
  const closeDate = closeExec?.date;

  let holdMinutes = null;
  if (openDate && openTime && closeDate && closeTime && exitFills.length > 0) {
    const openDt  = new Date(`${openDate}T${openTime}`);
    const closeDt = new Date(`${closeDate}T${closeTime}`);
    if (!isNaN(openDt) && !isNaN(closeDt)) {
      holdMinutes = Math.round((closeDt - openDt) / 60000);
    }
  }

  const fmtHold = (m) => {
    if (m == null) return '—';
    if (m < 60) return `${m}m`;
    const mins = m % 60;
    let hours = Math.floor(m / 60);
    const hrs = hours % 24;
    let days = Math.floor(hours / 24);
    const months = Math.floor(days / 30);
    days = days % 30;
    const parts = [];
    if (months > 0) parts.push(`${months}mo`);
    if (months > 0 || days > 0) parts.push(`${days}d`);
    parts.push(`${hrs}h`);
    parts.push(`${mins}m`);
    return parts.join(' ');
  };

  const isClosed = exitFills.length > 0;
  const isWin = (trade.net_pnl || 0) > 0;

  return { avgEntry, avgExit, totalQty, adjustedCost, netRoi, openTime, closeTime, openDate, closeDate, holdMinutes, fmtHold, isClosed, isWin, entryFills, exitFills };
}

// ── Stat row helper ────────────────────────────────────────────────────────────

function StatRow({ label, value, valueColor }) {
  if (value == null || value === '—' || value === '') return null;
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, padding: '9px 0', borderBottom: '1px solid var(--divider-soft)' }}>
      <span style={{ color: 'var(--text-secondary)', fontSize: 14 }}>{label}</span>
      <span className="num" style={{ fontSize: 14, fontWeight: 500, color: valueColor || 'var(--text-primary)', textAlign: 'right' }}>{value}</span>
    </div>
  );
}

// ── Shared edit-field helpers ──────────────────────────────────────────────────

const inputStyle = {
  width: '100%', fontSize: 14, minHeight: 34, padding: '5px 9px', boxSizing: 'border-box',
};

function EditField({ label, value, onChange, type = 'text', options }) {
  return (
    <label style={{ display: 'block' }}>
      <span className="field-label" style={{ marginBottom: 4 }}>{label}</span>
      {options ? (
        <select value={value} onChange={e => onChange(e.target.value)} style={inputStyle}>
          <option value="">—</option>
          {options.map(o => <option key={o} value={o}>{o}</option>)}
        </select>
      ) : (
        <input type={type} value={value} onChange={e => onChange(e.target.value)} style={inputStyle} />
      )}
    </label>
  );
}

function EditTextarea({ label, value, onChange }) {
  return (
    <label style={{ display: 'block' }}>
      <span className="field-label" style={{ marginBottom: 4 }}>{label}</span>
      <textarea value={value} onChange={e => onChange(e.target.value)} rows={3}
        style={{ ...inputStyle, resize: 'vertical', fontFamily: 'inherit' }} />
    </label>
  );
}

const EMOTIONAL_STATES = ['Focused', 'Confident', 'Calm', 'Anxious', 'FOMO', 'Frustrated', 'Greedy', 'Fearful', 'Undisciplined', 'Overconfident'];
const DEFAULT_SOURCES   = ['Watchlist', 'Scanner', 'Alert', 'News', 'Social Media', 'Own Research'];
const TAG_TYPES = ['strategy', 'setup', 'execution', 'mistake', 'emotion', 'outcome', 'source'];

// ── Dropdown with add-new option ──────────────────────────────────────────────

function SelectWithAdd({ value, onChange, options, placeholder = 'Select', label }) {
  const [adding, setAdding] = useState(false);
  const [newVal, setNewVal] = useState('');

  // Always include current value even if not in options list yet
  const merged = value && !options.includes(value) ? [value, ...options] : options;

  const handleAdd = () => {
    const trimmed = newVal.trim();
    if (!trimmed) return;
    onChange(trimmed);
    setAdding(false);
    setNewVal('');
  };

  if (adding) {
    return (
      <div style={{ display: 'flex', gap: 4 }}>
        <input
          autoFocus
          type="text"
          value={newVal}
          onChange={e => setNewVal(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') handleAdd(); if (e.key === 'Escape') { setAdding(false); setNewVal(''); } }}
          placeholder="Type new value…"
          aria-label={label ? `New ${label.toLowerCase()}` : 'New value'}
          style={{ ...inputStyle, flex: 1 }}
        />
        <button type="button" onClick={handleAdd} className="btn btn-primary btn-sm">Add</button>
        <button type="button" onClick={() => { setAdding(false); setNewVal(''); }} className="btn btn-ghost btn-sm" aria-label="Cancel new value">✕</button>
      </div>
    );
  }

  return (
    <select
      aria-label={label}
      value={value || ''}
      onChange={e => e.target.value === '__add__' ? setAdding(true) : onChange(e.target.value)}
      style={inputStyle}
    >
      <option value="">{placeholder}</option>
      {merged.map(o => <option key={o} value={o}>{o}</option>)}
      <option value="__add__">+ Add new…</option>
    </select>
  );
}

const editPanelStyle = { marginTop: 12, padding: 14, background: 'var(--surface-inset)', borderRadius: 'var(--radius-md)', border: '1px solid var(--divider)' };
const editGridStyle  = { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 10, marginBottom: 10 };

// ── Tag badge ─────────────────────────────────────────────────────────────────

// Tag categories map onto the semantic palette (same mapping as Trade View).
const TAG_CLASS = {
  strategy: 'accent', setup: '', execution: 'pos',
  mistake: 'neg', emotion: 'caution', outcome: 'accent', source: '',
};

function TagBadge({ tag, onDelete }) {
  return (
    <span className={`chip ${TAG_CLASS[tag.tag_type] || ''}`} title={tag.tag_type} style={{ fontSize: 13, padding: onDelete ? '2px 4px 2px 10px' : '3px 10px' }}>
      {tag.tag_value}
      {onDelete && (
        <button type="button" onClick={onDelete} aria-label={`Remove tag ${tag.tag_value}`} title="Remove"
          style={{ background: 'none', border: 'none', color: 'inherit', opacity: 0.75, padding: '0 4px', lineHeight: 1, fontSize: 16, display: 'flex', alignItems: 'center' }}>
          ×
        </button>
      )}
    </span>
  );
}

// ── What If helpers ───────────────────────────────────────────────────────────

const TABS = ['Stats', 'Strategy', 'Tags', 'Executions', 'What If'];

const SCENARIOS = [
  { label: '+5 min',    offsetMin: 5 },
  { label: '+10 min',   offsetMin: 10 },
  { label: '+30 min',   offsetMin: 30 },
  { label: '+1 hour',   offsetMin: 60 },
  { label: 'End of day', offsetMin: null },
];

function getPriceAt(bars, hhmm) {
  if (!bars.length) return null;
  const firstD = new Date(bars[0].t);
  const firstUTCMin = firstD.getUTCHours() * 60 + firstD.getUTCMinutes();
  const etOffset = firstUTCMin >= 780 ? -4 : -5;
  const [h, m] = hhmm.split(':').map(Number);
  const scenarioUTCMin = (h - etOffset) * 60 + m;
  for (const bar of bars) {
    const d = new Date(bar.t);
    if (d.getUTCHours() * 60 + d.getUTCMinutes() >= scenarioUTCMin) return bar.c;
  }
  return bars[bars.length - 1].c;
}

function computeWhatIf(bars, stats, trade) {
  if (!bars.length || !stats.isClosed || !stats.avgExit || !stats.closeTime) return null;
  const [exitH, exitM] = stats.closeTime.split(':').map(Number);
  const isStock  = !trade.instrument_type || trade.instrument_type === 'STOCK';
  const sideSign = trade.side === 'LONG' ? 1 : -1;

  return SCENARIOS.map(({ label, offsetMin }) => {
    let scenarioHHMM;
    if (offsetMin === null) {
      scenarioHHMM = '16:00';
    } else {
      const total = exitH * 60 + exitM + offsetMin;
      scenarioHHMM = `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
    }
    const price = getPriceAt(bars, scenarioHHMM);
    if (price == null) return { label, scenarioHHMM, price: null, deltaPnl: null, whatIfPnl: null };
    const deltaPnl  = isStock ? (price - stats.avgExit) * stats.totalQty * sideSign : null;
    const whatIfPnl = deltaPnl != null ? (trade.net_pnl ?? 0) + deltaPnl : null;
    return { label, scenarioHHMM, price, deltaPnl, whatIfPnl };
  });
}

const EMPTY_EXEC = { action: 'BOT', qty: '', price: '0.00', commission: '0.00', date: '', time: '' };

// ── Main TradeDetail component ────────────────────────────────────────────────

function getDayTradeTime(t, which) {
  const execs = Array.isArray(t.executions) ? t.executions : [];
  const times = execs.map(e => e.time).filter(Boolean).sort();
  return which === 'open' ? times[0]?.slice(0, 5) : times[times.length - 1]?.slice(0, 5);
}

function DaySidebar({ currentTrade, onOpenDetail }) {
  const [dayTrades, setDayTrades] = useState([]);

  useEffect(() => {
    tradesApi.list({ date_from: currentTrade.date, date_to: currentTrade.date, account_id: currentTrade.account_id })
      .then(r => setDayTrades(r.data))
      .catch(() => {});
  }, [currentTrade.date, currentTrade.account_id]);

  const dayPnl = dayTrades.reduce((s, t) => s + (t.net_pnl || 0), 0);

  return (
    <section className="card panel-flush" aria-label="This session">
      <div style={{ padding: '16px 16px 12px', borderBottom: '1px solid var(--divider-soft)' }}>
        <h2 className="section-title" style={{ fontSize: 17 }}>This session</h2>
        <div className="num text-muted" style={{ fontSize: 13, marginTop: 2 }}>{currentTrade.date}</div>
      </div>
      <div style={{ overflowY: 'auto', maxHeight: 560 }}>
        {dayTrades.map(t => {
          const pnl = t.net_pnl ?? 0;
          const isActive = t.id === currentTrade.id;
          const openT = getDayTradeTime(t, 'open');
          const closeT = getDayTradeTime(t, 'close');
          return (
            <button
              type="button"
              key={t.id}
              className={`list-row${isActive ? ' active' : ''}`}
              aria-current={isActive ? 'true' : undefined}
              onClick={() => onOpenDetail && onOpenDetail(t)}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
                <span style={{ fontWeight: 600, fontSize: 15 }}>{t.ticker}</span>
                <span className={`num ${pnl > 0 ? 'pos' : pnl < 0 ? 'neg' : ''}`} style={{ fontSize: 14, fontWeight: 600 }}>
                  {pnl >= 0 ? '+' : '-'}${Math.abs(pnl).toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
                </span>
              </div>
              {(openT || closeT) && (
                <div className="num text-muted" style={{ fontSize: 12.5, marginTop: 2 }}>
                  {openT}{closeT && openT !== closeT ? ` to ${closeT}` : ''}
                  {isActive && <span className="text-purple" style={{ marginLeft: 6 }}>Selected</span>}
                </div>
              )}
            </button>
          );
        })}
      </div>
      <div style={{ padding: '14px 16px', borderTop: '1px solid var(--divider-soft)' }}>
        <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>Day net P&L · <span className="num">{dayTrades.length}</span> trades</div>
        <div className={`num ${dayPnl >= 0 ? 'pos' : 'neg'}`} style={{ fontSize: 22, fontWeight: 600, fontFamily: 'var(--font-display)', lineHeight: 1.2, marginTop: 2 }}>
          {dayPnl >= 0 ? '+' : '-'}${Math.abs(dayPnl).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
        </div>
      </div>
    </section>
  );
}

export default function TradeDetail({ trade: initialTrade, tradeNavList = [], onBack, onTradeUpdate, onNavigate, onOpenDetail }) {
  const [trade, setTrade] = useState(initialTrade);
  const [tab, setTab] = useState('Stats');
  const [analysis, setAnalysis] = useState(null);
  const [tags, setTags] = useState([]);

  // Executions
  const [showAddExec, setShowAddExec]     = useState(false);
  const [execForm, setExecForm]           = useState(EMPTY_EXEC);
  const [addingExec, setAddingExec]       = useState(false);
  const [execError, setExecError]         = useState(null);
  const [editingExecIdx, setEditingExecIdx] = useState(null);
  const [editExecForm, setEditExecForm]   = useState(null);
  const [savingEditExec, setSavingEditExec] = useState(false);

  // What If
  const [whatIfBars, setWhatIfBars]       = useState(null);
  const [whatIfLoading, setWhatIfLoading] = useState(false);

  // Stats edit
  const [editingStats, setEditingStats]   = useState(false);
  const [statsForm, setStatsForm]         = useState({});
  const [savingStats, setSavingStats]     = useState(false);

  // Strategy edit
  const [editingStrategy, setEditingStrategy] = useState(false);
  const [strategyForm, setStrategyForm]       = useState({});
  const [savingStrategy, setSavingStrategy]   = useState(false);

  // Tags
  const [addingTag, setAddingTag]   = useState(false);
  const [tagForm, setTagForm]       = useState({ tag_type: 'strategy', tag_value: '' });
  const [tagError, setTagError]     = useState(null);
  const [savingTag, setSavingTag]   = useState(false);

  // Dropdown options (fetched from DB)
  const [analysisOptions, setAnalysisOptions] = useState({ strategies: [], idea_sources: [] });

  useEffect(() => {
    tradesApi.getAnalysisOptions().then(r => setAnalysisOptions(r.data)).catch(() => {});
  }, []);

  useEffect(() => {
    tradesApi.getAnalysis(trade.trade_group).then(r => {
      setAnalysis(r.data.analysis || {});
      setTags(r.data.tags || []);
    }).catch(() => setAnalysis({}));
  }, [trade.trade_group]);

  // ── Execution handlers ────────────────────────────────────────────────────

  const handleAddExecution = async () => {
    if (!execForm.qty || execForm.price === '') return;
    setAddingExec(true);
    setExecError(null);
    try {
      const res = await tradesApi.addExecution(trade.id, {
        ...execForm,
        qty: Number(execForm.qty),
        price: Number(execForm.price),
        commission: Number(execForm.commission || 0),
        date: execForm.date || trade.date,
      });
      setTrade(res.data);
      if (onTradeUpdate) onTradeUpdate(res.data);
      setShowAddExec(false);
      setExecForm(EMPTY_EXEC);
    } catch (e) {
      setExecError(e.response?.data?.detail || e.message);
    } finally {
      setAddingExec(false);
    }
  };

  const handleDeleteExecution = async (idx) => {
    try {
      const res = await tradesApi.deleteExecution(trade.id, idx);
      setTrade(res.data);
      if (onTradeUpdate) onTradeUpdate(res.data);
    } catch (e) {
      setExecError(e.response?.data?.detail || e.message);
    }
  };

  const handleSaveEditExec = async () => {
    if (!editExecForm) return;
    setSavingEditExec(true);
    setExecError(null);
    try {
      const res = await tradesApi.updateExecution(trade.id, editingExecIdx, {
        ...editExecForm,
        qty: Number(editExecForm.qty),
        price: Number(editExecForm.price),
        commission: Number(editExecForm.commission || 0),
        date: editExecForm.date || trade.date,
      });
      setTrade(res.data);
      if (onTradeUpdate) onTradeUpdate(res.data);
      setEditingExecIdx(null);
      setEditExecForm(null);
    } catch (e) {
      setExecError(e.response?.data?.detail || e.message);
    } finally {
      setSavingEditExec(false);
    }
  };

  // ── Analysis handlers ─────────────────────────────────────────────────────

  const toNum = v => (v === '' || v == null) ? null : (parseFloat(v) || null);

  const handleSaveStats = async () => {
    setSavingStats(true);
    try {
      const res = await tradesApi.updateAnalysis(trade.trade_group, {
        strategy: statsForm.strategy || null,
        idea_source: statsForm.idea_source || null,
        stop_loss: toNum(statsForm.stop_loss),
        target_price: toNum(statsForm.target_price),
        emotional_state: statsForm.emotional_state || null,
      });
      setAnalysis(res.data);
      setEditingStats(false);
    } catch (e) {
      console.error(e);
    } finally {
      setSavingStats(false);
    }
  };

  const handleSaveStrategy = async () => {
    setSavingStrategy(true);
    try {
      const res = await tradesApi.updateAnalysis(trade.trade_group, {
        entry_reason: strategyForm.entry_reason || null,
        exit_reason:  strategyForm.exit_reason  || null,
        mistakes:     strategyForm.mistakes     || null,
      });
      setAnalysis(res.data);
      setEditingStrategy(false);
    } catch (e) {
      console.error(e);
    } finally {
      setSavingStrategy(false);
    }
  };

  // ── Tag handlers ──────────────────────────────────────────────────────────

  const handleAddTag = async () => {
    if (!tagForm.tag_value.trim()) return;
    setSavingTag(true);
    setTagError(null);
    try {
      const res = await tradesApi.addTag(trade.trade_group, tagForm);
      setTags(prev => [...prev, res.data]);
      setTagForm({ tag_type: 'strategy', tag_value: '' });
      setAddingTag(false);
    } catch (e) {
      setTagError(e.response?.data?.error || e.message);
    } finally {
      setSavingTag(false);
    }
  };

  const handleDeleteTag = async (tagId) => {
    try {
      await tradesApi.deleteTag(tagId);
      setTags(prev => prev.filter(t => t.id !== tagId));
    } catch (e) {
      console.error(e);
    }
  };

  // ── Computed values ───────────────────────────────────────────────────────

  const stats = computeStats(trade);

  useEffect(() => {
    if (whatIfBars !== null) return;
    if (!stats.isClosed) { setWhatIfBars([]); return; }
    setWhatIfLoading(true);
    chartApi.get(trade.ticker, trade.date, '1Min')
      .then(r => setWhatIfBars(r.data.bars || []))
      .catch(() => setWhatIfBars([]))
      .finally(() => setWhatIfLoading(false));
  }, [whatIfBars, trade.ticker, trade.date, stats.isClosed]);

  const pnl = trade.net_pnl ?? 0;

  const riskPerShare = analysis?.stop_loss && stats.avgEntry
    ? Math.abs(stats.avgEntry - analysis.stop_loss) : null;
  const tradeRisk = riskPerShare && stats.totalQty
    ? -(riskPerShare * stats.totalQty) : analysis?.risk_per_trade ? -Math.abs(analysis.risk_per_trade) : null;
  const plannedR  = analysis?.risk_reward ? `${Number(analysis.risk_reward).toFixed(2)}R` : null;
  const realizedR = analysis?.r_multiple != null ? `${Number(analysis.r_multiple).toFixed(2)}R` : null;

  // ── Render ────────────────────────────────────────────────────────────────

  const navIdx = tradeNavList.findIndex(t => t.id === trade.id);
  const hasPrev = navIdx > 0;
  const hasNext = navIdx !== -1 && navIdx < tradeNavList.length - 1;

  const goTo = (idx) => {
    const t = tradeNavList[idx];
    if (!t || !onNavigate) return;
    onNavigate(t);
    if (onTradeUpdate) onTradeUpdate(t);
  };

  return (
    <div>
      {/* Back nav + prev/next */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
        <button type="button" onClick={onBack} className="btn btn-ghost" style={{ paddingLeft: 8 }}>
          <ArrowLeft size={16} /> Back to trades
        </button>
      </div>

      <PageHeader
        title={trade.ticker}
        subtitle={<>
          <span className="num">{trade.date}</span>
          {' / '}{trade.instrument_type ? trade.instrument_type.charAt(0) + trade.instrument_type.slice(1).toLowerCase() : 'Stock'}
          {' / '}{trade.side === 'LONG' ? 'Long' : trade.side === 'SHORT' ? 'Short' : trade.side}
          {stats.openTime && <> · Opened <span className="num">{stats.openTime.slice(0, 5)}</span></>}
          {stats.closeTime && stats.isClosed && <> · Closed <span className="num">{stats.closeTime.slice(0, 5)}</span></>}
          {stats.holdMinutes != null && <> · Held <span className="num">{stats.fmtHold(stats.holdMinutes)}</span></>}
        </>}
        actions={tradeNavList.length > 1 ? <>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => goTo(navIdx - 1)}
            disabled={!hasPrev}
            title="Previous trade"
          >
            <ChevronLeft size={16} /> Previous trade
          </button>
          <span className="num text-muted" style={{ fontSize: 13, minWidth: 54, textAlign: 'center' }} aria-live="polite">
            {navIdx + 1} / {tradeNavList.length}
          </span>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => goTo(navIdx + 1)}
            disabled={!hasNext}
            title="Next trade"
          >
            Next trade <ChevronRight size={16} />
          </button>
        </> : null}
      >
        <div style={{ display: 'flex', gap: 6, marginTop: 10, flexWrap: 'wrap' }}>
          <span className="chip">{trade.side}</span>
          <span className="chip">{stats.isClosed ? 'Closed' : 'Open'}</span>
          {stats.isClosed && (
            <span className={`chip ${stats.isWin ? 'pos' : 'neg'}`}>{stats.isWin ? 'Win' : 'Loss'}</span>
          )}
        </div>
      </PageHeader>

      <KpiStrip label="Trade metrics">
        <KpiCell
          label="Net P&L"
          value={<MoneyValue value={pnl} />}
          tone={pnl >= 0 ? 'pos' : 'neg'}
          foot={<>
            {stats.netRoi != null && <>ROI <span className={`num ${stats.netRoi >= 0 ? 'pos' : 'neg'}`}>{stats.netRoi >= 0 ? '+' : ''}{stats.netRoi.toFixed(2)}%</span></>}
            {stats.netRoi != null && trade.gross_pnl != null && ' · '}
            {trade.gross_pnl != null && <>Gross <span className="num">{fmtSigned$(trade.gross_pnl)}</span></>}
          </>}
        />
        <KpiCell
          label="Realized R"
          value={<span className="num">{realizedR ? `${analysis.r_multiple > 0 ? '+' : ''}${realizedR}` : 'n/a'}</span>}
          tone={analysis?.r_multiple != null ? (analysis.r_multiple >= 0 ? 'pos' : 'neg') : undefined}
          foot={plannedR ? <>Planned <span className="num">{plannedR}</span></> : null}
        />
        <KpiCell label="Avg entry" value={<span className="num">{stats.avgEntry ? `$${stats.avgEntry.toFixed(2)}` : 'n/a'}</span>} />
        <KpiCell label="Avg exit" value={<span className="num">{stats.avgExit ? `$${stats.avgExit.toFixed(2)}` : 'n/a'}</span>} />
        <KpiCell label="Quantity" value={<span className="num">{stats.totalQty || 'n/a'}</span>} foot={trade.commissions ? <>Comm <span className="num">{fmt$(trade.commissions)}</span></> : null} />
        <KpiCell label="Risk" value={<span className="num">{tradeRisk ? fmt$(tradeRisk) : 'n/a'}</span>} />
      </KpiStrip>

      {/* Layout: session list | chart, then tabs beside notes */}
      <div className="td-grid">
        <div className="td-session">
          <DaySidebar currentTrade={trade} onOpenDetail={onOpenDetail} />
        </div>

        <div className="td-main">
          <section className="card">
            <TradingChart
              ticker={trade.ticker}
              date={trade.date}
              executions={parseExecs(trade)}
              side={trade.side}
              analysis={analysis}
              height={520}
            />
          </section>

          <div className="td-lower">
        {/* Middle: tabs + content */}
        <section className="card panel-flush" aria-label="Trade review">
          {/* Tab bar */}
          <div className="tabs" role="tablist" aria-label="Trade review sections" style={{ padding: '0 12px' }}>
            {TABS.map(t => (
              <button
                type="button"
                key={t}
                role="tab"
                id={`td-tab-${t}`}
                aria-selected={tab === t}
                aria-controls="td-panel"
                tabIndex={tab === t ? 0 : -1}
                className="tab"
                onClick={() => setTab(t)}
                onKeyDown={e => {
                  const i = TABS.indexOf(tab);
                  if (e.key === 'ArrowRight') setTab(TABS[(i + 1) % TABS.length]);
                  if (e.key === 'ArrowLeft') setTab(TABS[(i - 1 + TABS.length) % TABS.length]);
                }}
              >
                {t}
              </button>
            ))}
          </div>

          <div style={{ padding: '6px 20px 20px' }} role="tabpanel" id="td-panel" aria-labelledby={`td-tab-${tab}`}>

            {/* ── Stats tab ─────────────────────────────────────────────── */}
            {tab === 'Stats' && (
              <div>
                <div style={{ display: 'flex', justifyContent: 'flex-end', paddingTop: 10 }}>
                  {!editingStats ? (
                    <button
                      onClick={() => {
                        setStatsForm({
                          strategy: analysis?.strategy || '',
                          idea_source: analysis?.idea_source || 'Watchlist',
                          stop_loss: analysis?.stop_loss ?? '',
                          target_price: analysis?.target_price ?? '',
                          emotional_state: analysis?.emotional_state || '',
                        });
                        setEditingStats(true);
                      }}
                      className="btn btn-ghost btn-sm"
                      type="button"
                    >
                      <Pencil size={13} /> Edit
                    </button>
                  ) : (
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button type="button" onClick={handleSaveStats} disabled={savingStats} className="btn btn-primary btn-sm">{savingStats ? 'Saving…' : 'Save'}</button>
                      <button type="button" onClick={() => setEditingStats(false)} className="btn btn-ghost btn-sm">Cancel</button>
                    </div>
                  )}
                </div>

                <StatRow label="Side" value={trade.side} />
                <StatRow label="Stocks traded" value={stats.totalQty || '—'} />
                <StatRow label="Commissions & Fees" value={trade.commissions ? fmt$(trade.commissions) : '—'} />
                <StatRow label="Net ROI" value={stats.netRoi != null ? `${stats.netRoi >= 0 ? '+' : ''}${stats.netRoi.toFixed(2)}%` : '—'} valueColor={stats.netRoi != null ? (stats.netRoi >= 0 ? 'var(--green)' : 'var(--red)') : undefined} />
                <StatRow label="Gross P&L" value={trade.gross_pnl != null ? fmt$(trade.gross_pnl) : '—'} valueColor={trade.gross_pnl >= 0 ? 'var(--green)' : 'var(--red)'} />
                <StatRow label="Adjusted Cost" value={stats.adjustedCost ? fmt$(stats.adjustedCost) : '—'} />
                <StatRow label="Average Entry" value={stats.avgEntry ? `$${stats.avgEntry.toFixed(2)}` : '—'} />
                <StatRow label="Average Exit" value={stats.avgExit ? `$${stats.avgExit.toFixed(2)}` : '—'} />
                <StatRow label="Entry Time" value={stats.openTime ? `${stats.openDate ? stats.openDate + ' ' : ''}${stats.openTime.slice(0, 5)}` : '—'} />
                <StatRow label="Exit Time" value={(stats.isClosed && stats.closeTime) ? `${stats.closeDate ? stats.closeDate + ' ' : ''}${stats.closeTime.slice(0, 5)}` : '—'} />
                <StatRow label="Hold Time" value={stats.fmtHold(stats.holdMinutes)} />

                {editingStats ? (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--divider)' }}>
                    <div>
                      <div className="field-label" style={{ marginBottom: 4 }}>Strategy</div>
                      <SelectWithAdd
                        value={statsForm.strategy}
                        onChange={v => setStatsForm(f => ({ ...f, strategy: v }))}
                        options={analysisOptions.strategies}
                        label="Strategy"
                        placeholder="Select strategy"
                      />
                    </div>
                    <div>
                      <div className="field-label" style={{ marginBottom: 4 }}>Source (idea origin)</div>
                      <SelectWithAdd
                        value={statsForm.idea_source}
                        onChange={v => setStatsForm(f => ({ ...f, idea_source: v }))}
                        options={[...new Set([...DEFAULT_SOURCES, ...analysisOptions.idea_sources])]}
                        label="Source"
                        placeholder="Select source"
                      />
                    </div>
                    <EditField label="Stop Loss ($)" type="number" value={String(statsForm.stop_loss)} onChange={v => setStatsForm(f => ({ ...f, stop_loss: v }))} />
                    <EditField label="Profit Target ($)" type="number" value={String(statsForm.target_price)} onChange={v => setStatsForm(f => ({ ...f, target_price: v }))} />
                    <EditField label="Emotional State" value={statsForm.emotional_state} onChange={v => setStatsForm(f => ({ ...f, emotional_state: v }))} options={EMOTIONAL_STATES} />
                  </div>
                ) : analysis && (
                  <>
                    <StatRow label="Strategy" value={analysis.strategy} valueColor="var(--accent-line)" />
                    <StatRow label="Source" value={analysis.idea_source} valueColor="var(--text-secondary)" />
                    <StatRow label="Stop Loss" value={analysis.stop_loss ? `$${analysis.stop_loss}` : null} valueColor="var(--caution)" />
                    <StatRow label="Profit Target" value={analysis.target_price ? `$${analysis.target_price}` : null} valueColor="var(--accent-line)" />
                    <StatRow label="Trade Risk" value={tradeRisk ? fmt$(tradeRisk) : (analysis.risk_per_trade ? fmt$(-Math.abs(analysis.risk_per_trade)) : null)} valueColor="var(--caution)" />
                    <StatRow label="Planned R-Multiple" value={plannedR} />
                    <StatRow label="Realized R-Multiple" value={realizedR} valueColor={analysis?.r_multiple >= 0 ? 'var(--green)' : 'var(--red)'} />
                    {/* Excursion: how far the trade went your way and against you,
                        and how much of the favourable move you actually kept. */}
                    <StatRow
                      label="Max Favourable (MFE)"
                      value={trade.mfe_pct == null ? null : `+${Number(trade.mfe_pct).toFixed(2)}%`}
                      valueColor="var(--result-pos)"
                    />
                    <StatRow
                      label="Max Adverse (MAE)"
                      value={trade.mae_pct == null ? null : `${Number(trade.mae_pct).toFixed(2)}%`}
                      valueColor="var(--result-neg)"
                    />
                    <StatRow
                      label="Exit Efficiency"
                      value={trade.exit_efficiency == null ? null : `${Number(trade.exit_efficiency).toFixed(1)}%`}
                      valueColor={trade.exit_efficiency == null ? undefined
                        : trade.exit_efficiency < 0 ? 'var(--result-neg)'
                          : trade.exit_efficiency >= 50 ? 'var(--result-pos)' : 'var(--caution)'}
                    />
                    <StatRow label="Emotional State" value={analysis.emotional_state} />
                  </>
                )}
              </div>
            )}

            {/* ── Strategy tab ──────────────────────────────────────────── */}
            {tab === 'Strategy' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12, paddingTop: 8 }}>
                <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                  {!editingStrategy ? (
                    <button
                      onClick={() => {
                        setStrategyForm({
                          entry_reason: analysis?.entry_reason || '',
                          exit_reason:  analysis?.exit_reason  || '',
                          mistakes:     analysis?.mistakes     || '',
                        });
                        setEditingStrategy(true);
                      }}
                      className="btn btn-ghost btn-sm"
                      type="button"
                    >
                      <Pencil size={13} /> Edit
                    </button>
                  ) : (
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button type="button" onClick={handleSaveStrategy} disabled={savingStrategy} className="btn btn-primary btn-sm">{savingStrategy ? 'Saving…' : 'Save'}</button>
                      <button type="button" onClick={() => setEditingStrategy(false)} className="btn btn-ghost btn-sm">Cancel</button>
                    </div>
                  )}
                </div>

                {editingStrategy ? (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                    <EditTextarea label="Entry Reason" value={strategyForm.entry_reason} onChange={v => setStrategyForm(f => ({ ...f, entry_reason: v }))} />
                    <EditTextarea label="Exit Reason"  value={strategyForm.exit_reason}  onChange={v => setStrategyForm(f => ({ ...f, exit_reason: v }))} />
                    <EditTextarea label="Mistakes"     value={strategyForm.mistakes}     onChange={v => setStrategyForm(f => ({ ...f, mistakes: v }))} />
                  </div>
                ) : (
                  <>
                    {(analysis?.strategy || analysis?.idea_source) && (
                      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
                        {analysis?.strategy && <div><div className="field-label" style={{ marginBottom: 4 }}>Strategy</div><div style={{ color: 'var(--accent-line)', fontWeight: 600 }}>{analysis.strategy}</div></div>}
                        {analysis?.idea_source && <div><div className="field-label" style={{ marginBottom: 4 }}>Source</div><div style={{ color: 'var(--text-primary)', fontSize: 14 }}>{analysis.idea_source}</div></div>}
                      </div>
                    )}
                    {analysis?.entry_reason && <div><div className="field-label" style={{ marginBottom: 4 }}>Entry Reason</div><div style={{ fontSize: 14.5, lineHeight: 1.55 }}>{analysis.entry_reason}</div></div>}
                    {analysis?.exit_reason  && <div><div className="field-label" style={{ marginBottom: 4 }}>Exit Reason</div><div style={{ fontSize: 14.5, lineHeight: 1.55 }}>{analysis.exit_reason}</div></div>}
                    {analysis?.mistakes     && <div><div className="field-label" style={{ marginBottom: 4 }}>Mistakes</div><div className="neg" style={{ fontSize: 14.5, lineHeight: 1.55 }}>{analysis.mistakes}</div></div>}
                    {analysis?.ai_feedback  && (
                      <div className="notice accent">
                        {analysis.ai_feedback}
                      </div>
                    )}
                    {!analysis?.strategy && !analysis?.entry_reason && (
                      <div className="text-muted" style={{ fontSize: 14 }}>No strategy notes yet. Click Edit to add.</div>
                    )}
                  </>
                )}
              </div>
            )}

            {/* ── Tags tab ──────────────────────────────────────────────── */}
            {tab === 'Tags' && (
              <div style={{ paddingTop: 8 }}>
                {tags.length > 0 && (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
                    {tags.map(tag => (
                      <TagBadge key={tag.id} tag={tag} onDelete={() => handleDeleteTag(tag.id)} />
                    ))}
                  </div>
                )}
                {!addingTag ? (
                  <button type="button" onClick={() => setAddingTag(true)} className="btn btn-ghost" style={{ color: 'var(--accent-line)', paddingLeft: 6 }}>
                    <PlusCircle size={15} /> Add Tag
                  </button>
                ) : (
                  <div style={editPanelStyle}>
                    <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 10, color: 'var(--text-primary)' }}>Add Tag</div>
                    <div style={editGridStyle}>
                      <div>
                        <div className="field-label" style={{ marginBottom: 4 }}>Type</div>
                        <select aria-label="Tag type" value={tagForm.tag_type} onChange={e => setTagForm(f => ({ ...f, tag_type: e.target.value }))} style={inputStyle}>
                          {TAG_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                        </select>
                      </div>
                      <div>
                        <div className="field-label" style={{ marginBottom: 4 }}>Value</div>
                        <input
                          type="text" placeholder="tag value" aria-label="Tag value" value={tagForm.tag_value}
                          onChange={e => setTagForm(f => ({ ...f, tag_value: e.target.value }))}
                          onKeyDown={e => e.key === 'Enter' && handleAddTag()}
                          style={inputStyle}
                        />
                      </div>
                    </div>
                    {tagError && (
                      <div className="notice neg" role="alert" style={{ marginBottom: 10 }}>{tagError}</div>
                    )}
                    <div style={{ display: 'flex', gap: 8 }}>
                      <button type="button" onClick={handleAddTag} disabled={savingTag} className="btn btn-primary btn-sm">{savingTag ? 'Saving…' : 'Add'}</button>
                      <button type="button" onClick={() => { setAddingTag(false); setTagForm({ tag_type: 'strategy', tag_value: '' }); setTagError(null); }} className="btn btn-ghost btn-sm">Cancel</button>
                    </div>
                  </div>
                )}
                {tags.length === 0 && !addingTag && (
                  <div className="text-muted" style={{ fontSize: 14, marginTop: 8 }}>No tags yet.</div>
                )}
              </div>
            )}

            {/* ── Executions tab ────────────────────────────────────────── */}
            {tab === 'Executions' && (
              <div style={{ paddingTop: 8 }}>
                {/* The card wrapping this panel clips overflow with no scrollbar, so the
                    edit/delete column silently disappeared off the right edge on any
                    trade with enough columns to not fit the fixed-width side panel. An
                    explicit scroll container is what actually makes those reachable. */}
                <div className="scroll-x" style={{ margin: '0 -20px' }}>
                <table style={{ minWidth: 470 }}>
                  <thead>
                    <tr>
                      {['Date', 'Time', 'Action', 'Qty', 'Price', 'Comm.', ''].map((h, hi) => (
                        <th key={h || hi} className={h === 'Date' || h === 'Time' || h === 'Action' ? undefined : 'num'} style={{ paddingLeft: hi === 0 ? 20 : undefined, paddingRight: hi === 6 ? 20 : undefined }}>
                          {h || <span className="sr-only">Actions</span>}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {parseExecs(trade).map((ex, i) => (
                      <tr key={i}>
                        <td className="mono text-muted" style={{ paddingLeft: 20, fontSize: 13, whiteSpace: 'nowrap' }}>{ex.date ? ex.date.slice(5) : '—'}</td>
                        <td className="mono" style={{ fontSize: 13.5, whiteSpace: 'nowrap' }}>{ex.time?.slice(0, 5) || '—'}</td>
                        <td style={{ whiteSpace: 'nowrap' }}>{ex.action}</td>
                        <td className="num mono" style={{ fontSize: 13.5 }}>{ex.qty}</td>
                        <td className="num mono" style={{ fontSize: 13.5 }}>${Number(ex.price ?? 0).toFixed(2)}</td>
                        <td className="num mono text-muted" style={{ fontSize: 13.5 }}>{ex.commission ? `$${Number(ex.commission).toFixed(2)}` : '—'}</td>
                        <td className="num" style={{ whiteSpace: 'nowrap', paddingRight: 20 }}>
                          <button
                            title="Edit"
                            onClick={() => {
                              setEditingExecIdx(i);
                              setEditExecForm({ ...ex, qty: String(ex.qty), price: String(ex.price), commission: String(ex.commission || ''), date: ex.date || trade.date, time: ex.time || '' });
                              setShowAddExec(false);
                            }}
                            type="button"
                            aria-label={`Edit execution ${i + 1}`}
                            className="btn btn-ghost btn-icon"
                          >
                            <Pencil size={14} />
                          </button>
                          <button type="button" onClick={() => handleDeleteExecution(i)} title="Delete" aria-label={`Delete execution ${i + 1}`} className="btn btn-ghost btn-icon" style={{ color: 'var(--result-neg)' }}>
                            <Trash2 size={14} />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                </div>

                <div className="text-muted" style={{ fontSize: 13, marginTop: 10 }}>
                  Gross <span className="num">{trade.gross_pnl != null ? fmtSigned$(trade.gross_pnl) : 'n/a'}</span>
                  {' · '}Commissions <span className="num">{trade.commissions ? fmt$(trade.commissions) : '$0.00'}</span>
                </div>

                {/* Edit Execution inline panel */}
                {editingExecIdx !== null && editExecForm && (
                  <div style={editPanelStyle}>
                    <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 10, color: 'var(--text-primary)' }}>Edit Execution #{editingExecIdx + 1}</div>
                    <div style={editGridStyle}>
                      <div>
                        <div className="field-label" style={{ marginBottom: 4 }}>Action</div>
                        <select aria-label="Edit execution action" value={editExecForm.action} onChange={e => setEditExecForm(f => ({ ...f, action: e.target.value }))} style={inputStyle}>
                          <option value="BOT">BOT (Buy)</option>
                          <option value="SOLD">SOLD (Sell)</option>
                        </select>
                      </div>
                      <div>
                        <div className="field-label" style={{ marginBottom: 4 }}>Qty</div>
                        <input aria-label="Edit execution qty" type="number" min="1" value={editExecForm.qty} onChange={e => setEditExecForm(f => ({ ...f, qty: e.target.value }))} style={inputStyle} />
                      </div>
                      <div>
                        <div className="field-label" style={{ marginBottom: 4 }}>Price</div>
                        <input aria-label="Edit execution price" type="number" min="0" step="0.01" value={editExecForm.price} onChange={e => setEditExecForm(f => ({ ...f, price: e.target.value }))} style={inputStyle} />
                      </div>
                      <div>
                        <div className="field-label" style={{ marginBottom: 4 }}>Commission</div>
                        <input aria-label="Edit execution commission" type="number" min="0" step="0.01" value={editExecForm.commission} onChange={e => setEditExecForm(f => ({ ...f, commission: e.target.value }))} style={inputStyle} />
                      </div>
                      <div>
                        <div className="field-label" style={{ marginBottom: 4 }}>Date</div>
                        <input aria-label="Edit execution date" type="date" value={editExecForm.date} onChange={e => setEditExecForm(f => ({ ...f, date: e.target.value }))} style={inputStyle} />
                      </div>
                      <div>
                        <div className="field-label" style={{ marginBottom: 4 }}>Time</div>
                        <input aria-label="Edit execution time" type="time" value={editExecForm.time} onChange={e => setEditExecForm(f => ({ ...f, time: e.target.value }))} style={inputStyle} />
                      </div>
                    </div>
                    {execError && <div className="notice neg" role="alert" style={{ marginBottom: 10 }}>{execError}</div>}
                    <div style={{ display: 'flex', gap: 8 }}>
                      <button type="button" onClick={handleSaveEditExec} disabled={savingEditExec} className="btn btn-primary btn-sm">{savingEditExec ? 'Saving…' : 'Save'}</button>
                      <button type="button" onClick={() => { setEditingExecIdx(null); setEditExecForm(null); setExecError(null); }} className="btn btn-ghost btn-sm">Cancel</button>
                    </div>
                  </div>
                )}

                {/* Add Execution */}
                {!showAddExec ? (
                  <button
                    type="button"
                    onClick={() => { setShowAddExec(true); setEditingExecIdx(null); setEditExecForm(null); }}
                    className="btn btn-ghost"
                    style={{ marginTop: 12, color: 'var(--accent-line)', paddingLeft: 6 }}
                  >
                    <PlusCircle size={15} /> Add Execution
                  </button>
                ) : (
                  <div style={editPanelStyle}>
                    <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 10, color: 'var(--text-primary)' }}>Add Execution</div>
                    <div style={editGridStyle}>
                      <div>
                        <div className="field-label" style={{ marginBottom: 4 }}>Action</div>
                        <select aria-label="New execution action" value={execForm.action} onChange={e => setExecForm(f => ({ ...f, action: e.target.value }))} style={inputStyle}>
                          <option value="BOT">BOT (Buy)</option>
                          <option value="SOLD">SOLD (Sell)</option>
                        </select>
                      </div>
                      <div>
                        <div className="field-label" style={{ marginBottom: 4 }}>Qty</div>
                        <input aria-label="New execution qty" type="number" min="1" value={execForm.qty} placeholder="0" onChange={e => setExecForm(f => ({ ...f, qty: e.target.value }))} style={inputStyle} />
                      </div>
                      <div>
                        <div className="field-label" style={{ marginBottom: 4 }}>Price</div>
                        <input aria-label="New execution price" type="number" min="0" step="0.01" value={execForm.price} onChange={e => setExecForm(f => ({ ...f, price: e.target.value }))} style={inputStyle} />
                      </div>
                      <div>
                        <div className="field-label" style={{ marginBottom: 4 }}>Commission</div>
                        <input aria-label="New execution commission" type="number" min="0" step="0.01" value={execForm.commission} onChange={e => setExecForm(f => ({ ...f, commission: e.target.value }))} style={inputStyle} />
                      </div>
                      <div>
                        <div className="field-label" style={{ marginBottom: 4 }}>Date</div>
                        <input aria-label="New execution date" type="date" value={execForm.date || trade.date} onChange={e => setExecForm(f => ({ ...f, date: e.target.value }))} style={inputStyle} />
                      </div>
                      <div>
                        <div className="field-label" style={{ marginBottom: 4 }}>Time</div>
                        <input aria-label="New execution time" type="time" value={execForm.time} onChange={e => setExecForm(f => ({ ...f, time: e.target.value }))} style={inputStyle} />
                      </div>
                    </div>
                    {execError && <div className="notice neg" role="alert" style={{ marginBottom: 10 }}>{execError}</div>}
                    <div style={{ display: 'flex', gap: 8 }}>
                      <button type="button" onClick={handleAddExecution} disabled={addingExec} className="btn btn-primary btn-sm">{addingExec ? 'Saving…' : 'Save'}</button>
                      <button type="button" onClick={() => { setShowAddExec(false); setExecForm(EMPTY_EXEC); setExecError(null); }} className="btn btn-ghost btn-sm">Cancel</button>
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* ── What If tab ───────────────────────────────────────────── */}
            {tab === 'What If' && (
              <div style={{ paddingTop: 8 }}>
                {!stats.isClosed ? (
                  <div className="text-muted" style={{ fontSize: 14 }}>Available for closed trades only.</div>
                ) : whatIfLoading ? (
                  <div className="text-muted" role="status" style={{ fontSize: 14 }}>Loading 1-min bar data…</div>
                ) : whatIfBars !== null && whatIfBars.length === 0 ? (
                  <div className="text-muted" style={{ fontSize: 14 }}>Chart data unavailable. Alpaca market data is required for this feature.</div>
                ) : whatIfBars !== null && (() => {
                  const isStock  = !trade.instrument_type || trade.instrument_type === 'STOCK';
                  const scenarios = computeWhatIf(whatIfBars, stats, trade);
                  if (!scenarios) return <div className="text-muted" style={{ fontSize: 14 }}>Insufficient trade data.</div>;
                  return (
                    <div>
                      {!isStock && (
                        <div className="notice accent" style={{ fontSize: 13, marginBottom: 10 }}>
                          Prices shown are the <strong>underlying stock</strong>. Option P&L depends on delta, theta, and time value, so estimated P&L is not computed.
                        </div>
                      )}
                      <div className="text-muted" style={{ marginBottom: 10, fontSize: 13 }}>
                        Actual exit: <strong className="num" style={{ color: 'var(--text-primary)' }}>{stats.closeTime?.slice(0, 5)}</strong> @ <strong className="num" style={{ color: 'var(--text-primary)' }}>${stats.avgExit?.toFixed(2)}</strong>
                        {isStock && <> · Net P&L: <strong className={`num ${(trade.net_pnl ?? 0) >= 0 ? 'pos' : 'neg'}`}>{fmtSigned$(trade.net_pnl)}</strong></>}
                      </div>
                      <table>
                        <thead>
                          <tr>
                            <th className={undefined}>Scenario</th>
                            <th className={'num'}>Price</th>
                            {isStock && <>
                              <th className={'num'}>Est. P&L</th>
                              <th className={'num'}>vs Actual</th>
                            </>}
                          </tr>
                        </thead>
                        <tbody>
                          {scenarios.map((s, i) => {
                            const better = s.deltaPnl != null && s.deltaPnl > 0;
                            const worse  = s.deltaPnl != null && s.deltaPnl < 0;
                            return (
                              <tr key={i}>
                                <td style={{ fontWeight: 500 }}>{s.label}</td>
                                <td className="num">{s.price != null ? `$${s.price.toFixed(2)}` : '—'}</td>
                                {isStock && <>
                                  <td className={`num ${s.whatIfPnl != null ? (s.whatIfPnl >= 0 ? 'pos' : 'neg') : 'text-muted'}`} style={{ fontWeight: 600 }}>
                                    {s.whatIfPnl != null ? fmtSigned$(s.whatIfPnl) : '—'}
                                  </td>
                                  <td className={`num ${better ? 'pos' : worse ? 'neg' : 'text-muted'}`} style={{ fontWeight: 600 }}>
                                    {s.deltaPnl != null ? (s.deltaPnl === 0 ? '—' : (better ? '↑ +' : '↓ ') + '$' + Math.abs(s.deltaPnl).toFixed(0)) : '—'}
                                  </td>
                                </>}
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  );
                })()}
              </div>
            )}

          </div>
        </section>

        {/* Beside the tabs: notes, tags, AI analysis, what-if */}
        <div className="stack">
          {/* Strategy & notes */}
          {analysis && (analysis.entry_reason || analysis.exit_reason || analysis.mistakes) && (
            <section className="card">
              <PanelHead title="Strategy Notes" />
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                {analysis.entry_reason && (
                  <div>
                    <div className="field-label" style={{ marginBottom: 4 }}>Entry Reason</div>
                    <div style={{ fontSize: 14.5, lineHeight: 1.55 }}>{analysis.entry_reason}</div>
                  </div>
                )}
                {analysis.exit_reason && (
                  <div>
                    <div className="field-label" style={{ marginBottom: 4 }}>Exit Reason</div>
                    <div style={{ fontSize: 14.5, lineHeight: 1.55 }}>{analysis.exit_reason}</div>
                  </div>
                )}
                {analysis.mistakes && (
                  <div>
                    <div className="field-label" style={{ marginBottom: 4 }}>Mistakes</div>
                    <div className="neg" style={{ fontSize: 14.5, lineHeight: 1.55 }}>{analysis.mistakes}</div>
                  </div>
                )}
              </div>
            </section>
          )}

          {/* Tags */}
          {tags.length > 0 && (
            <section className="card">
              <PanelHead title="Tags" />
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {tags.map(tag => <TagBadge key={tag.id} tag={tag} />)}
              </div>
            </section>
          )}

          {/* AI Feedback — only shown when diary analysis exists */}
          {analysis?.ai_feedback && (
            <section className="card">
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12, flexWrap: 'wrap' }}>
                <h2 className="section-title">AI Analysis</h2>
                {analysis.match_confidence && (
                  <span className="chip">
                    <span className={`confidence-dot confidence-${analysis.match_confidence}`} />
                    {analysis.match_confidence} match
                  </span>
                )}
              </div>
              <div className="notice accent" style={{ lineHeight: 1.6 }}>
                {analysis.ai_feedback}
              </div>
              {analysis.r_multiple != null && (
                <div style={{ marginTop: 12, display: 'flex', gap: 16 }}>
                  <div>
                    <div className="field-label" style={{ marginBottom: 4 }}>Trade Quality (R)</div>
                    <div className={`num ${analysis.r_multiple >= 0 ? 'pos' : 'neg'}`} style={{ fontSize: 20, fontWeight: 600 }}>
                      {Number(analysis.r_multiple).toFixed(2)}R
                    </div>
                  </div>
                  {analysis.risk_reward && (
                    <div>
                      <div className="field-label" style={{ marginBottom: 4 }}>Planned R:R</div>
                      <div className="num" style={{ fontSize: 20, fontWeight: 600 }}>1:{Number(analysis.risk_reward).toFixed(1)}</div>
                    </div>
                  )}
                </div>
              )}
            </section>
          )}

          {/* What-if scenarios */}
          {stats.isClosed && whatIfBars !== null && whatIfBars.length > 0 && (() => {
            const isStock = !trade.instrument_type || trade.instrument_type === 'STOCK';
            const scenarios = computeWhatIf(whatIfBars, stats, trade);
            if (!scenarios) return null;
            return (
              <section className="card">
                <h2 className="section-title">What If Scenarios</h2>
                <div className="text-muted" style={{ fontSize: 13, margin: '4px 0 12px' }}>
                  Actual exit: <strong className="num" style={{ color: 'var(--text-primary)' }}>{stats.closeTime?.slice(0, 5)}</strong> @ <strong className="num" style={{ color: 'var(--text-primary)' }}>${stats.avgExit?.toFixed(2)}</strong>
                  {isStock && <> · Net P&L: <strong className={`num ${pnl >= 0 ? 'pos' : 'neg'}`}>{fmtSigned$(trade.net_pnl)}</strong></>}
                </div>
                {!isStock && (
                  <div className="notice accent" style={{ fontSize: 13, marginBottom: 10 }}>
                    Prices shown are the underlying stock. Option P&L not estimated.
                  </div>
                )}
                <div className="scroll-x" style={{ margin: '0 -24px', padding: '0 12px' }}>
                <table>
                  <thead>
                    <tr>
                      <th className={undefined}>Scenario</th>
                      <th className={'num'}>Price</th>
                      {isStock && <>
                        <th className={'num'}>Est. P&L</th>
                        <th className={'num'}>vs Actual</th>
                      </>}
                    </tr>
                  </thead>
                  <tbody>
                    {scenarios.map((s, i) => {
                      const better = s.deltaPnl != null && s.deltaPnl > 0;
                      const worse = s.deltaPnl != null && s.deltaPnl < 0;
                      return (
                        <tr key={i}>
                          <td style={{ fontWeight: 500 }}>{s.label}</td>
                          <td className="num">{s.price != null ? `$${s.price.toFixed(2)}` : '—'}</td>
                          {isStock && <>
                            <td className={`num ${s.whatIfPnl != null ? (s.whatIfPnl >= 0 ? 'pos' : 'neg') : 'text-muted'}`} style={{ fontWeight: 600 }}>
                              {s.whatIfPnl != null ? fmtSigned$(s.whatIfPnl) : '—'}
                            </td>
                            <td className={`num ${better ? 'pos' : worse ? 'neg' : 'text-muted'}`} style={{ fontWeight: 600 }}>
                              {s.deltaPnl != null ? (s.deltaPnl === 0 ? '—' : (better ? '↑ +' : '↓ ') + '$' + Math.abs(s.deltaPnl).toFixed(0)) : '—'}
                            </td>
                          </>}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                </div>
              </section>
            );
          })()}
        </div>
          </div>
        </div>
      </div>
    </div>
  );
}
