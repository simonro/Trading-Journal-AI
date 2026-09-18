import { useState, useEffect, useCallback } from 'react';
import { RefreshCw, Trash2, Play, UploadCloud } from 'lucide-react';
import { backupApi } from '../api';

const errText = (e) => e?.response?.data?.detail || e?.response?.data?.error || e?.message || 'Something went wrong';
const fmtBytes = (n) => {
  if (n == null) return '';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
};

const EMPTY_DEST = { name: '', type: 'scp', host: '', port: '', username: '', secret: '', key_path: '', remote_path: '.' };

export default function BackupSettings() {
  const [destinations, setDestinations] = useState([]);
  const [schedules, setSchedules] = useState([]);
  const [runs, setRuns] = useState([]);
  const [localFiles, setLocalFiles] = useState([]);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);

  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState(EMPTY_DEST);

  const [passphrase, setPassphrase] = useState('');
  const [restoreFile, setRestoreFile] = useState(null);
  const [restoreLocalName, setRestoreLocalName] = useState('');
  const [restorePassphrase, setRestorePassphrase] = useState('');
  const [confirmRestore, setConfirmRestore] = useState(false);

  const load = useCallback(async () => {
    try {
      const [d, s, r, lf] = await Promise.all([
        backupApi.listDestinations(), backupApi.listSchedules(), backupApi.listRuns(), backupApi.listLocalFiles(),
      ]);
      setDestinations(d.data);
      setSchedules(s.data);
      setRuns(r.data);
      setLocalFiles(lf.data);
    } catch (e) {
      setError(errText(e));
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const scheduleFor = (destId) => schedules.find(s => s.destination_id === destId);

  const saveDestination = async () => {
    setBusy(true); setError(null);
    try {
      await backupApi.createDestination({
        ...draft,
        port: draft.port ? Number(draft.port) : null,
      });
      setAdding(false);
      setDraft(EMPTY_DEST);
      setNotice('Destination added.');
      await load();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  };

  const testDestination = async (id) => {
    setBusy(true); setError(null); setNotice(null);
    try {
      await backupApi.testDestination(id);
      setNotice('Connection test succeeded.');
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  };

  const removeDestination = async (id) => {
    setBusy(true);
    try {
      await backupApi.deleteDestination(id);
      await load();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  };

  const setSchedule = async (destId, interval, hour, minute, dayOfWeek) => {
    setBusy(true); setError(null);
    try {
      // No interval selected -> keep (or create as disabled) so the scheduler skips it.
      await backupApi.setSchedule(destId, {
        interval: interval || 'daily',
        hour: Number(hour),
        minute: Number(minute),
        day_of_week: dayOfWeek != null ? Number(dayOfWeek) : null,
        enabled: Boolean(interval),
      });
      setNotice(interval ? 'Schedule saved.' : 'Schedule disabled.');
      await load();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  };

  const backupNow = async (destId = null) => {
    setBusy(true); setError(null); setNotice(null);
    try {
      const res = destId ? await backupApi.runNow(destId, passphrase || undefined) : await backupApi.runLocal(passphrase || undefined);
      setNotice(`Backup created: ${res.data.filename} (${fmtBytes(res.data.size_bytes)}).`);
      await load();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  };

  const restore = async () => {
    if (!confirmRestore) return;
    setBusy(true); setError(null); setNotice(null);
    try {
      const form = new FormData();
      form.append('passphrase', restorePassphrase);
      if (restoreFile) form.append('file', restoreFile);
      else if (restoreLocalName) form.append('local_filename', restoreLocalName);
      else throw new Error('Choose a backup file to restore from.');
      const res = await backupApi.restore(form);
      setNotice(`Restored. Safety copy of the previous database: ${res.data.safety_backup || 'none'}.`);
      setConfirmRestore(false);
      setRestorePassphrase('');
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
            <h2 className="section-title">1-click backup</h2>
            <div className="section-sub">Every backup is a passphrase-encrypted archive of your database and uploads. Kept locally, and pushed to any destinations below.</div>
          </div>
        </div>
        <div className="settings-form">
          <label style={{ flex: 2 }}>
            <span className="field-label">Passphrase (optional if BACKUP_PASSPHRASE is set on the server)</span>
            <input type="password" value={passphrase} onChange={e => setPassphrase(e.target.value)} placeholder="Leave blank to use the server default" />
          </label>
          <div className="settings-form-actions">
            <button type="button" className="btn btn-primary btn-sm" disabled={busy} onClick={() => backupNow(null)}>
              <Play size={13} /> Backup now
            </button>
          </div>
        </div>

        <div className="table-container">
          <table style={{ minWidth: 480 }}>
            <thead><tr><th style={{ paddingLeft: 20 }}>Local backup file</th><th className="num">Size</th><th style={{ paddingRight: 20 }}>Modified</th></tr></thead>
            <tbody>
              {localFiles.map(f => (
                <tr key={f.filename}>
                  <td style={{ paddingLeft: 20 }}>{f.filename}</td>
                  <td className="num">{fmtBytes(f.size_bytes)}</td>
                  <td style={{ paddingRight: 20 }}>{new Date(f.modified_at).toLocaleString()}</td>
                </tr>
              ))}
              {!localFiles.length && <tr><td colSpan={3}><div className="empty">No local backups yet.</div></td></tr>}
            </tbody>
          </table>
        </div>
      </section>

      <section className="card panel-flush">
        <div className="settings-head">
          <div>
            <h2 className="section-title">Destinations <span className="text-muted num" style={{ fontWeight: 500, fontSize: 14 }}>{destinations.length}</span></h2>
            <div className="section-sub">scp (SFTP), FTP, or rsync. Each can have its own schedule.</div>
          </div>
          <button type="button" className="btn btn-primary btn-sm" onClick={() => { setAdding(true); setDraft(EMPTY_DEST); }}>Add destination</button>
        </div>

        {adding && (
          <div className="settings-form">
            <label><span className="field-label">Name</span><input value={draft.name} onChange={e => setDraft(d => ({ ...d, name: e.target.value }))} /></label>
            <label><span className="field-label">Type</span>
              <select value={draft.type} onChange={e => setDraft(d => ({ ...d, type: e.target.value }))}>
                <option value="scp">scp (SFTP)</option>
                <option value="ftp">FTP</option>
                <option value="rsync">rsync</option>
              </select>
            </label>
            <label><span className="field-label">Host</span><input value={draft.host} onChange={e => setDraft(d => ({ ...d, host: e.target.value }))} /></label>
            <label><span className="field-label">Port</span><input value={draft.port} onChange={e => setDraft(d => ({ ...d, port: e.target.value }))} placeholder="default" /></label>
            <label><span className="field-label">Username</span><input value={draft.username} onChange={e => setDraft(d => ({ ...d, username: e.target.value }))} /></label>
            <label><span className="field-label">Password / passphrase</span><input type="password" value={draft.secret} onChange={e => setDraft(d => ({ ...d, secret: e.target.value }))} /></label>
            <label><span className="field-label">SSH key path (optional, scp/rsync)</span><input value={draft.key_path} onChange={e => setDraft(d => ({ ...d, key_path: e.target.value }))} /></label>
            <label style={{ flex: 2 }}><span className="field-label">Remote path</span><input value={draft.remote_path} onChange={e => setDraft(d => ({ ...d, remote_path: e.target.value }))} /></label>
            <div className="settings-form-actions">
              <button type="button" className="btn btn-primary btn-sm" disabled={busy || !draft.name || !draft.host} onClick={saveDestination}>Save</button>
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => setAdding(false)}>Cancel</button>
            </div>
          </div>
        )}

        <div className="table-container">
          <table style={{ minWidth: 720 }}>
            <thead>
              <tr>
                <th style={{ paddingLeft: 20 }}>Name</th><th>Type</th><th>Host</th><th>Schedule</th><th className="num" style={{ paddingRight: 20 }}><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {destinations.map(d => {
                const sched = scheduleFor(d.id);
                return (
                  <tr key={d.id}>
                    <td style={{ paddingLeft: 20, fontWeight: 600 }}>{d.name}</td>
                    <td>{d.type}</td>
                    <td className="text-muted">{d.host}{d.remote_path ? `:${d.remote_path}` : ''}</td>
                    <td>
                      <select
                        value={sched?.enabled ? sched.interval : ''}
                        onChange={e => setSchedule(d.id, e.target.value, sched?.hour ?? 2, sched?.minute ?? 0, sched?.day_of_week)}
                      >
                        <option value="">No schedule</option>
                        <option value="hourly">Hourly</option>
                        <option value="daily">Daily (2am)</option>
                        <option value="weekly">Weekly (Sun 2am)</option>
                      </select>
                    </td>
                    <td className="num" style={{ paddingRight: 20, whiteSpace: 'nowrap' }}>
                      <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={() => testDestination(d.id)}>Test</button>
                      <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={() => backupNow(d.id)}><UploadCloud size={13} /> Backup now</button>
                      <button type="button" className="btn btn-ghost btn-sm" style={{ color: 'var(--result-neg)' }} disabled={busy} onClick={() => removeDestination(d.id)}><Trash2 size={13} /></button>
                    </td>
                  </tr>
                );
              })}
              {!destinations.length && <tr><td colSpan={5}><div className="empty">No destinations yet. Local backups still work without one.</div></td></tr>}
            </tbody>
          </table>
        </div>
      </section>

      <section className="card panel-flush">
        <div className="settings-head">
          <div>
            <h2 className="section-title">Restore (disaster recovery)</h2>
            <div className="section-sub">Restoring overwrites the live database. A safety copy of what's currently live is made automatically first.</div>
          </div>
        </div>
        <div className="settings-form">
          <label style={{ flex: 2 }}>
            <span className="field-label">Backup file</span>
            <select value={restoreFile ? '__upload__' : restoreLocalName} onChange={e => { if (e.target.value === '__upload__') { setRestoreLocalName(''); } else { setRestoreFile(null); setRestoreLocalName(e.target.value); } }}>
              <option value="">Choose a local backup...</option>
              {localFiles.map(f => <option key={f.filename} value={f.filename}>{f.filename}</option>)}
            </select>
          </label>
          <label>
            <span className="field-label">...or upload a file</span>
            <input type="file" accept=".enc" onChange={e => { setRestoreFile(e.target.files[0] || null); setRestoreLocalName(''); }} />
          </label>
          <label>
            <span className="field-label">Passphrase</span>
            <input type="password" value={restorePassphrase} onChange={e => setRestorePassphrase(e.target.value)} />
          </label>
          <label className="checkbox-row" style={{ flexBasis: '100%' }}>
            <input type="checkbox" checked={confirmRestore} onChange={e => setConfirmRestore(e.target.checked)} />
            <span>I understand this replaces the live database.</span>
          </label>
          <div className="settings-form-actions">
            <button type="button" className="btn btn-danger btn-sm" disabled={busy || !confirmRestore || !restorePassphrase || (!restoreFile && !restoreLocalName)} onClick={restore}>
              <RefreshCw size={13} /> Restore
            </button>
          </div>
        </div>
      </section>

      <section className="card panel-flush">
        <div className="settings-head"><h2 className="section-title">Recent runs</h2></div>
        <div className="table-container">
          <table style={{ minWidth: 640 }}>
            <thead><tr><th style={{ paddingLeft: 20 }}>Started</th><th>Trigger</th><th>Status</th><th>File</th><th style={{ paddingRight: 20 }}>Message</th></tr></thead>
            <tbody>
              {runs.map(r => (
                <tr key={r.id}>
                  <td style={{ paddingLeft: 20 }}>{r.started_at}</td>
                  <td>{r.trigger}</td>
                  <td className={r.status === 'failed' ? 'neg' : ''}>{r.status}</td>
                  <td className="text-muted">{r.filename || ''}</td>
                  <td className="text-muted" style={{ paddingRight: 20 }}>{r.message || ''}</td>
                </tr>
              ))}
              {!runs.length && <tr><td colSpan={5}><div className="empty">No backups run yet.</div></td></tr>}
            </tbody>
          </table>
        </div>
      </section>

      {notice && <div className="notice pos settings-notice" role="status">{notice}</div>}
      {error && <div className="notice neg" role="alert">{error}</div>}
    </div>
  );
}
