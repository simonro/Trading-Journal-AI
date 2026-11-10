import { useState, useEffect } from 'react';
import { pointValue, loadPointValues } from '../pointValue';
import { X } from 'lucide-react';
import { tradesApi } from '../api';
import useModalFocus from './useModalFocus';

export default function AddTradeModal({ accounts, defaultAccountId, onClose, onSaved }) {
  const [form, setForm] = useState({
    account_id: defaultAccountId || (accounts[0]?.id || ''),
    date: new Date().toISOString().slice(0, 10),
    time: '',
    ticker: '',
    instrument_type: 'STOCK',
    side: 'LONG',
    entry_price: '',
    exit_price: '',
    quantity: 1,
    commissions: 0,
    strategy: '',
    stop_loss: '',
    risk_per_trade: '',
    notes: '',
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const update = (field, value) => setForm(p => ({ ...p, [field]: value }));
  const [, setPointValuesReady] = useState(false);
  useEffect(() => { let alive = true; loadPointValues().then(() => { if (alive) setPointValuesReady(true); }); return () => { alive = false; }; }, []);
  const dialogRef = useModalFocus(onClose);

  const previewPnl = () => {
    const entry = parseFloat(form.entry_price);
    const exit = parseFloat(form.exit_price);
    const qty = parseInt(form.quantity);
    const comm = parseFloat(form.commissions) || 0;
    if (!entry || !exit || !qty) return null;
    const mult = pointValue(form.instrument_type, form.ticker);
    const gross = (form.side === 'LONG' ? (exit - entry) : (entry - exit)) * qty * mult;
    return (gross - comm).toFixed(2);
  };

  const pnl = previewPnl();

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!form.account_id) { setError('Please select an account.'); return; }
    if (!form.ticker.trim()) { setError('Ticker is required.'); return; }
    setSaving(true);
    setError(null);
    try {
      const payload = {
        ...form,
        ticker: form.ticker.toUpperCase(),
        quantity: parseInt(form.quantity),
        commissions: parseFloat(form.commissions) || 0,
        entry_price: parseFloat(form.entry_price),
        exit_price: form.exit_price ? parseFloat(form.exit_price) : null,
        stop_loss: form.stop_loss ? parseFloat(form.stop_loss) : null,
        account_id: parseInt(form.account_id),
      };
      await tradesApi.create(payload);
      onSaved();
    } catch (err) {
      setError(err.response?.data?.error || err.message);
      setSaving(false);
    }
  };

  const fieldStyle = { width: '100%', marginBottom: 0 };

  return (
    <div className="modal-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="add-trade-title" ref={dialogRef} tabIndex={-1}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
          <h2 id="add-trade-title" className="section-title">Add Trade</h2>
          <button type="button" className="btn btn-ghost btn-icon" onClick={onClose} aria-label="Close"><X size={18} /></button>
        </div>

        <form onSubmit={handleSubmit}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>

            <div>
              <label className="field-label" htmlFor="at-account">Account</label>
              <select id="at-account" style={fieldStyle} value={form.account_id} onChange={e => update('account_id', e.target.value)} required>
                <option value="">Select account...</option>
                {accounts.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
              </select>
            </div>

            <div>
              <label className="field-label" htmlFor="at-ticker">Ticker</label>
              <input id="at-ticker" style={fieldStyle} placeholder="AAPL" value={form.ticker} onChange={e => update('ticker', e.target.value)} required />
            </div>

            <div>
              <label className="field-label" htmlFor="at-date">Date</label>
              <input id="at-date" type="date" style={fieldStyle} value={form.date} onChange={e => update('date', e.target.value)} required />
            </div>

            <div>
              <label className="field-label" htmlFor="at-time">Time (optional)</label>
              <input id="at-time" type="time" style={fieldStyle} value={form.time} onChange={e => update('time', e.target.value)} />
            </div>

            <div>
              <label className="field-label" htmlFor="at-type">Instrument Type</label>
              <select id="at-type" style={fieldStyle} value={form.instrument_type} onChange={e => update('instrument_type', e.target.value)}>
                <option value="STOCK">Stock</option>
                <option value="OPTION">Option</option>
                <option value="FUTURE">Future</option>
              </select>
            </div>

            <div>
              <span className="field-label" id="at-side-label">Side</span>
              <div className="seg" role="group" aria-labelledby="at-side-label" style={{ display: 'flex' }}>
                {['LONG', 'SHORT'].map(s => (
                  <button
                    key={s} type="button"
                    className="seg-btn"
                    aria-pressed={form.side === s}
                    style={{ flex: 1 }}
                    onClick={() => update('side', s)}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <label className="field-label" htmlFor="at-entry">Entry Price</label>
              <input id="at-entry" type="number" step="0.01" style={fieldStyle} placeholder="0.00" value={form.entry_price} onChange={e => update('entry_price', e.target.value)} required />
            </div>

            <div>
              <label className="field-label" htmlFor="at-exit">Exit Price</label>
              <input id="at-exit" type="number" step="0.01" style={fieldStyle} placeholder="0.00 (optional)" value={form.exit_price} onChange={e => update('exit_price', e.target.value)} />
            </div>

            <div>
              <label className="field-label" htmlFor="at-qty">Quantity</label>
              <input id="at-qty" type="number" min="1" style={fieldStyle} value={form.quantity} onChange={e => update('quantity', e.target.value)} />
            </div>

            <div>
              <label className="field-label" htmlFor="at-comm">Commissions ($)</label>
              <input id="at-comm" type="number" step="0.01" min="0" style={fieldStyle} value={form.commissions} onChange={e => update('commissions', e.target.value)} />
            </div>

            <div>
              <label className="field-label" htmlFor="at-stop">Stop Loss</label>
              <input id="at-stop" type="number" step="0.01" style={fieldStyle} placeholder="Price level" value={form.stop_loss} onChange={e => update('stop_loss', e.target.value)} />
            </div>

            <div>
              <label className="field-label" htmlFor="at-strategy">Strategy</label>
              <input id="at-strategy" style={fieldStyle} placeholder="VWAP Support..." value={form.strategy} onChange={e => update('strategy', e.target.value)} />
            </div>
          </div>

          <div style={{ marginTop: 14 }}>
            <label className="field-label" htmlFor="at-notes">Notes</label>
            <textarea
              id="at-notes"
              rows={3}
              style={{ width: '100%', resize: 'vertical' }}
              placeholder="Trade notes..."
              value={form.notes}
              onChange={e => update('notes', e.target.value)}
            />
          </div>

          {pnl != null && (
            <div className={`notice ${Number(pnl) >= 0 ? 'pos' : 'neg'}`} style={{ marginTop: 14 }} aria-live="polite">
              Estimated Net P&L: <strong className={`num ${Number(pnl) >= 0 ? 'pos' : 'neg'}`}>
                {Number(pnl) >= 0 ? '+' : '-'}${Math.abs(Number(pnl)).toLocaleString('en-US', { minimumFractionDigits: 2 })}
              </strong>
            </div>
          )}

          {error && (
            <div className="notice neg" role="alert" style={{ marginTop: 12 }}>{error}</div>
          )}

          <div style={{ display: 'flex', gap: 10, marginTop: 20 }}>
            <button type="submit" className="btn btn-primary" style={{ flex: 1 }} disabled={saving}>
              {saving ? 'Saving...' : 'Save Trade'}
            </button>
            <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
          </div>
        </form>
      </div>
    </div>
  );
}
