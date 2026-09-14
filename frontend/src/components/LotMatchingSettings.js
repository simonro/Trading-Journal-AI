import { useState, useEffect, useCallback } from 'react';
import { lotMethodApi } from '../api';

const errText = (e) => e?.response?.data?.detail || e?.response?.data?.error || e?.message || 'Something went wrong';

export default function LotMatchingSettings() {
  const [method, setMethod] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [confirming, setConfirming] = useState(null); // method pending confirmation

  const load = useCallback(async () => {
    try {
      const res = await lotMethodApi.get();
      setMethod(res.data.method);
    } catch (e) {
      setError(errText(e));
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const apply = async (newMethod) => {
    setBusy(true); setError(null); setNotice(null);
    try {
      const res = await lotMethodApi.set(newMethod);
      setMethod(res.data.method);
      setNotice(`Switched to ${res.data.method}. Recalculated ${res.data.recalculated_trades} trade(s).`);
      setConfirming(null);
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
          <h2 className="section-title">Lot matching method</h2>
          <div className="section-sub">
            The default used to decide which opening lot each closing fill closes: First-In-First-Out or
            Last-In-First-Out. Changing this recalculates every trade's lot breakdown; realized P&amp;L per trade
            does not change, but the holding period and cost basis of each lot does.
          </div>
        </div>
      </div>

      <div className="settings-form">
        {['FIFO', 'LIFO'].map(m => (
          <label key={m} className="checkbox-row" style={{ gap: 8 }}>
            <input type="radio" name="lot-method" checked={method === m} disabled={busy} onChange={() => setConfirming(m)} />
            <span>{m === 'FIFO' ? 'FIFO (First-In-First-Out, broker default)' : 'LIFO (Last-In-First-Out)'}</span>
          </label>
        ))}
      </div>

      {confirming && confirming !== method && (
        <div className="settings-form">
          <div style={{ flexBasis: '100%' }} className="notice neg" role="alert">
            Switch the account default to {confirming}? This recalculates every trade in the database now.
          </div>
          <div className="settings-form-actions">
            <button type="button" className="btn btn-primary btn-sm" disabled={busy} onClick={() => apply(confirming)}>Recalculate now</button>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setConfirming(null)}>Cancel</button>
          </div>
        </div>
      )}

      <div className="text-muted" style={{ padding: '0 20px 16px', fontSize: 13 }}>
        Under IRS rules (Pub. 550), FIFO is the default absent "specific identification" at the time of sale.
        You can override the method for a single trade from that trade's detail page, for the cases your broker
        lets you pick which lot to sell.
      </div>

      {notice && <div className="notice pos settings-notice" role="status">{notice}</div>}
      {error && <div className="notice neg" role="alert">{error}</div>}
    </section>
  );
}
