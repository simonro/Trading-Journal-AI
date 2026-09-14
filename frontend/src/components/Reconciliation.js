import { useState, useEffect, useCallback, useMemo } from 'react';
import { Plus, Check, X, Upload } from 'lucide-react';
import { reconciliationApi, accountsApi } from '../api';
import { PageHeader } from './ui';

const errText = (e) => e?.response?.data?.detail || e?.response?.data?.error || e?.message || 'Something went wrong';
const money = (n) => (n == null ? '' : n.toLocaleString(undefined, { style: 'currency', currency: 'USD' }));

const SECTIONS = [
  { id: 'fees', label: 'Fee reconciliation' },
  { id: 'dividends', label: 'Dividends' },
];

function FeeReconciliation({ accountId }) {
  const [rows, setRows] = useState([]);
  const [summary, setSummary] = useState(null);
  const [onlyUnreconciled, setOnlyUnreconciled] = useState(false);
  const [editing, setEditing] = useState(null); // trade_id
  const [feeInput, setFeeInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [importing, setImporting] = useState(false);

  const load = useCallback(async () => {
    try {
      const [r, s] = await Promise.all([
        reconciliationApi.listTrades({ account_id: accountId, unreconciled_only: onlyUnreconciled }),
        reconciliationApi.summary(accountId),
      ]);
      setRows(r.data);
      setSummary(s.data);
    } catch (e) {
      setError(errText(e));
    }
  }, [accountId, onlyUnreconciled]);

  useEffect(() => { load(); }, [load]);

  const startEdit = (row) => {
    setEditing(row.trade_id);
    setFeeInput(row.broker_actual_fee != null ? String(row.broker_actual_fee) : '');
  };

  const save = async (tradeId) => {
    setBusy(true); setError(null);
    try {
      await reconciliationApi.reconcileTrade(tradeId, { broker_actual_fee: Number(feeInput) });
      setEditing(null);
      await load();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  };

  const unreconcile = async (tradeId) => {
    setBusy(true);
    try {
      await reconciliationApi.unreconcileTrade(tradeId);
      await load();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  };

  const importCsv = async (file) => {
    if (!file || accountId == null) return;
    setImporting(true); setError(null); setNotice(null);
    try {
      const form = new FormData();
      form.append('file', file);
      const res = await reconciliationApi.importStatement(form, accountId);
      setNotice(`Matched ${res.data.matched} trade(s).${res.data.unmatched.length ? ` ${res.data.unmatched.length} row(s) could not be matched.` : ''}`);
      await load();
    } catch (e) {
      setError(errText(e));
    } finally {
      setImporting(false);
    }
  };

  return (
    <div className="stack">
      {summary && (
        <div className="kpi-strip" data-cols="4">
          <div className="kpi-cell"><div className="kpi-label">Reconciled</div><div className="kpi-value">{summary.reconciled_trades}/{summary.total_trades}</div></div>
          <div className="kpi-cell"><div className="kpi-label">Estimated fees</div><div className="kpi-value">{money(summary.total_estimated_fees)}</div></div>
          <div className="kpi-cell"><div className="kpi-label">Broker actual fees</div><div className="kpi-value">{money(summary.total_broker_actual_fees)}</div></div>
          <div className="kpi-cell"><div className="kpi-label">Variance</div><div className={`kpi-value ${summary.total_variance > 0 ? 'neg' : summary.total_variance < 0 ? 'pos' : ''}`}>{money(summary.total_variance)}</div></div>
        </div>
      )}

      <section className="card panel-flush">
        <div className="settings-head">
          <div>
            <h2 className="section-title">Trades</h2>
            <div className="section-sub">The journal estimates commissions when your broker's export omits them. Enter the actual fee from your statement to reconcile.</div>
          </div>
          <div className="settings-tools">
            <label className="checkbox-row">
              <input type="checkbox" checked={onlyUnreconciled} onChange={e => setOnlyUnreconciled(e.target.checked)} />
              <span>Unreconciled only</span>
            </label>
            <label className="btn btn-ghost btn-sm" style={{ cursor: importing ? 'wait' : 'pointer' }}>
              <Upload size={13} /> Import statement CSV
              <input type="file" accept=".csv" hidden disabled={importing} onChange={e => { importCsv(e.target.files[0]); e.target.value = ''; }} />
            </label>
          </div>
        </div>

        <div className="table-container">
          <table style={{ minWidth: 720 }}>
            <thead>
              <tr>
                <th style={{ paddingLeft: 20 }}>Date</th><th>Ticker</th><th className="num">Estimated</th>
                <th className="num">Broker actual</th><th className="num">Variance</th><th className="num" style={{ paddingRight: 20 }}><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.trade_id}>
                  <td style={{ paddingLeft: 20 }}>{r.date}</td>
                  <td style={{ fontWeight: 600 }}>{r.ticker}</td>
                  <td className="num">{money(r.estimated_fee)}</td>
                  <td className="num">
                    {editing === r.trade_id
                      ? <input style={{ width: 90, textAlign: 'right' }} value={feeInput} autoFocus onChange={e => setFeeInput(e.target.value)}
                          onKeyDown={e => { if (e.key === 'Enter') save(r.trade_id); if (e.key === 'Escape') setEditing(null); }} />
                      : money(r.broker_actual_fee)}
                  </td>
                  <td className={`num ${r.variance > 0 ? 'neg' : r.variance < 0 ? 'pos' : ''}`}>{r.variance != null ? money(r.variance) : ''}</td>
                  <td className="num" style={{ paddingRight: 20, whiteSpace: 'nowrap' }}>
                    {editing === r.trade_id ? (
                      <>
                        <button type="button" className="btn btn-ghost btn-sm" disabled={busy || !feeInput} onClick={() => save(r.trade_id)}><Check size={13} /></button>
                        <button type="button" className="btn btn-ghost btn-sm" onClick={() => setEditing(null)}><X size={13} /></button>
                      </>
                    ) : (
                      <>
                        <button type="button" className="btn btn-ghost btn-sm" onClick={() => startEdit(r)}>{r.reconciled ? 'Edit' : 'Reconcile'}</button>
                        {r.reconciled && <button type="button" className="btn btn-ghost btn-sm" style={{ color: 'var(--result-neg)' }} disabled={busy} onClick={() => unreconcile(r.trade_id)}>Clear</button>}
                      </>
                    )}
                  </td>
                </tr>
              ))}
              {!rows.length && <tr><td colSpan={6}><div className="empty">No trades to reconcile.</div></td></tr>}
            </tbody>
          </table>
        </div>
        {notice && <div className="notice pos settings-notice" role="status">{notice}</div>}
        {error && <div className="notice neg" role="alert">{error}</div>}
      </section>
    </div>
  );
}

function DividendsList({ accountId }) {
  const [dividends, setDividends] = useState([]);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState({ date: '', ticker: '', amount: '', notes: '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      const res = await reconciliationApi.listDividends(accountId);
      setDividends(res.data);
    } catch (e) {
      setError(errText(e));
    }
  }, [accountId]);

  useEffect(() => { load(); }, [load]);

  const save = async () => {
    setBusy(true); setError(null);
    try {
      await reconciliationApi.createDividend({ account_id: accountId, date: draft.date, ticker: draft.ticker, amount: Number(draft.amount), notes: draft.notes || null });
      setAdding(false);
      setDraft({ date: '', ticker: '', amount: '', notes: '' });
      await load();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id) => {
    setBusy(true);
    try {
      await reconciliationApi.deleteDividend(id);
      await load();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card panel-flush">
      <div className="settings-head">
        <div>
          <h2 className="section-title">Dividends <span className="text-muted num" style={{ fontWeight: 500, fontSize: 14 }}>{dividends.length}</span></h2>
          <div className="section-sub">Not in your CSV import — record them here so account income is complete.</div>
        </div>
        <button type="button" className="btn btn-primary btn-sm" onClick={() => setAdding(true)}><Plus size={14} /> Add dividend</button>
      </div>

      {adding && (
        <div className="settings-form">
          <label><span className="field-label">Date</span><input type="date" value={draft.date} onChange={e => setDraft(d => ({ ...d, date: e.target.value }))} /></label>
          <label><span className="field-label">Ticker</span><input value={draft.ticker} onChange={e => setDraft(d => ({ ...d, ticker: e.target.value }))} /></label>
          <label><span className="field-label">Amount</span><input value={draft.amount} onChange={e => setDraft(d => ({ ...d, amount: e.target.value }))} /></label>
          <label style={{ flex: 2 }}><span className="field-label">Notes</span><input value={draft.notes} onChange={e => setDraft(d => ({ ...d, notes: e.target.value }))} /></label>
          <div className="settings-form-actions">
            <button type="button" className="btn btn-primary btn-sm" disabled={busy || !draft.date || !draft.ticker || !draft.amount} onClick={save}>Save</button>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setAdding(false)}>Cancel</button>
          </div>
        </div>
      )}

      <div className="table-container">
        <table style={{ minWidth: 560 }}>
          <thead><tr><th style={{ paddingLeft: 20 }}>Date</th><th>Ticker</th><th className="num">Amount</th><th>Notes</th><th className="num" style={{ paddingRight: 20 }}><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>
            {dividends.map(d => (
              <tr key={d.id}>
                <td style={{ paddingLeft: 20 }}>{d.date}</td>
                <td style={{ fontWeight: 600 }}>{d.ticker}</td>
                <td className="num pos">{money(d.amount)}</td>
                <td className="text-muted">{d.notes || ''}</td>
                <td className="num" style={{ paddingRight: 20 }}>
                  <button type="button" className="btn btn-ghost btn-sm" style={{ color: 'var(--result-neg)' }} disabled={busy} onClick={() => remove(d.id)}>Delete</button>
                </td>
              </tr>
            ))}
            {!dividends.length && <tr><td colSpan={5}><div className="empty">No dividends recorded yet.</div></td></tr>}
          </tbody>
        </table>
      </div>
      {error && <div className="notice neg" role="alert">{error}</div>}
    </section>
  );
}

export default function Reconciliation() {
  const [accounts, setAccounts] = useState([]);
  const [accountId, setAccountId] = useState(null);
  const [section, setSection] = useState('fees');

  useEffect(() => {
    accountsApi.list().then(res => {
      setAccounts(res.data);
      if (res.data.length) setAccountId(res.data[0].id);
    }).catch(() => {});
  }, []);

  const onTabKey = (e) => {
    const i = SECTIONS.findIndex(s => s.id === section);
    if (e.key === 'ArrowRight') setSection(SECTIONS[(i + 1) % SECTIONS.length].id);
    if (e.key === 'ArrowLeft') setSection(SECTIONS[(i - 1 + SECTIONS.length) % SECTIONS.length].id);
  };

  const accountOptions = useMemo(() => accounts.map(a => <option key={a.id} value={a.id}>{a.name}</option>), [accounts]);

  return (
    <div>
      <PageHeader
        title="Reconciliation"
        subtitle="Match the journal's estimated fees to your broker's statement, and track dividends the CSV import doesn't carry."
        actions={accounts.length > 1 && (
          <select value={accountId ?? ''} onChange={e => setAccountId(Number(e.target.value))}>
            {accountOptions}
          </select>
        )}
      />

      <div className="tabs" role="tablist" aria-label="Reconciliation sections" style={{ marginBottom: 'var(--space-5)' }}>
        {SECTIONS.map(s => (
          <button type="button" key={s.id} role="tab" aria-selected={section === s.id} tabIndex={section === s.id ? 0 : -1}
            className="tab" onClick={() => setSection(s.id)} onKeyDown={onTabKey}>
            {s.label}
          </button>
        ))}
      </div>

      {accountId == null ? (
        <div className="empty">Add an account first.</div>
      ) : section === 'fees' ? (
        <FeeReconciliation accountId={accountId} />
      ) : (
        <DividendsList accountId={accountId} />
      )}
    </div>
  );
}
