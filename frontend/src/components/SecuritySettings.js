import { useState, useEffect, useCallback } from 'react';
import { ShieldCheck, ShieldOff, UploadCloud, Trash2 } from 'lucide-react';
import { tlsApi } from '../api';

const errText = (e) => e?.response?.data?.detail || e?.response?.data?.error || e?.message || 'Something went wrong';

export default function SecuritySettings() {
  const [status, setStatus] = useState(null);
  const [certFile, setCertFile] = useState(null);
  const [keyFile, setKeyFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);

  const load = useCallback(async () => {
    try {
      const res = await tlsApi.status();
      setStatus(res.data);
    } catch (e) {
      setError(errText(e));
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const upload = async () => {
    if (!certFile || !keyFile) return;
    setBusy(true); setError(null); setNotice(null);
    try {
      const form = new FormData();
      form.append('certificate', certFile);
      form.append('private_key', keyFile);
      await tlsApi.upload(form);
      setNotice('Certificate installed. Restart the backend for it to take effect.');
      setCertFile(null); setKeyFile(null);
      await load();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true); setError(null);
    try {
      await tlsApi.remove();
      setNotice('Certificate removed. Restart the backend to go back to plain HTTP.');
      await load();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack">
      <section className="card panel-flush">
        <div className="settings-head">
          <div>
            <h2 className="section-title">TLS certificate</h2>
            <div className="section-sub">Encrypts every connection to the backend, even on your own LAN. Takes effect on the next backend restart.</div>
          </div>
        </div>

        {status && (
          <div className="settings-form" style={{ alignItems: 'center' }}>
            {status.configured ? (
              <>
                <ShieldCheck size={18} style={{ color: 'var(--result-pos)' }} />
                <div style={{ flex: 2 }}>
                  <div><strong>Installed</strong>{status.is_expired ? <span className="neg"> (expired)</span> : ''}</div>
                  <div className="text-muted" style={{ fontSize: 13 }}>
                    {status.subject} - valid until {status.not_valid_after ? new Date(status.not_valid_after).toLocaleDateString() : '?'}
                  </div>
                </div>
                <button type="button" className="btn btn-ghost btn-sm" style={{ color: 'var(--result-neg)' }} disabled={busy} onClick={remove}>
                  <Trash2 size={13} /> Remove
                </button>
              </>
            ) : (
              <>
                <ShieldOff size={18} className="text-muted" />
                <div style={{ flex: 2 }} className="text-muted">No certificate installed - connections are plain HTTP.</div>
              </>
            )}
          </div>
        )}

        <div className="settings-form">
          <label>
            <span className="field-label">Certificate (PEM, full chain)</span>
            <input type="file" accept=".pem,.crt,.cer" onChange={e => setCertFile(e.target.files[0] || null)} />
          </label>
          <label>
            <span className="field-label">Private key (PEM)</span>
            <input type="file" accept=".pem,.key" onChange={e => setKeyFile(e.target.files[0] || null)} />
          </label>
          <div className="settings-form-actions">
            <button type="button" className="btn btn-primary btn-sm" disabled={busy || !certFile || !keyFile} onClick={upload}>
              <UploadCloud size={13} /> Install certificate
            </button>
          </div>
        </div>

        <div className="text-muted" style={{ padding: '0 20px 16px', fontSize: 13 }}>
          No certificate yet? Run <code>python scripts/generate_self_signed_cert.py &lt;your-lan-ip&gt;</code> on the
          server for a free LAN-only certificate (devices will need to trust it once), or upload a real one
          (e.g. Let's Encrypt) here.
        </div>

        {notice && <div className="notice pos settings-notice" role="status">{notice}</div>}
        {error && <div className="notice neg" role="alert">{error}</div>}
      </section>
    </div>
  );
}
