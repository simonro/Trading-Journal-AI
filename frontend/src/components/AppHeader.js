import { useState, useRef, useEffect } from 'react';
import { createPortal } from 'react-dom';
import {
  LayoutDashboard, TrendingUp, BarChart2, BookOpen, Plus, ChevronDown,
  CalendarDays, Check, X, Pencil, CalendarCheck, HelpCircle, Upload, Brain, FlaskConical, Settings as SettingsIcon, Trash2,
} from 'lucide-react';
import { accountsApi } from '../api';
import { selectionLabel } from '../accountSelection';
import useModalFocus from './useModalFocus';
import aiJournalLogo from '../assets/ai-journal-logo.png';

// Every page stays one click away. Import, Brain and Add Trade live with the
// account selector, Help and Settings on the right; seven pages keep their labels.
const NAV_ITEMS = [
  { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { id: 'trades', label: 'Trade View', icon: TrendingUp },
  { id: 'calendar', label: 'Calendar', icon: CalendarDays },
  { id: 'day-review', label: 'Day Review', icon: CalendarCheck },
  { id: 'reports', label: 'Reports', icon: BarChart2 },
  { id: 'exit-lab', label: 'Exit Lab', icon: FlaskConical },
  { id: 'diary', label: 'Diary', icon: BookOpen },

];

const NEW_ACCOUNT_DEFAULT = { name: '', type: 'day_trading', color: '#6366f1', broker: 'Thinkorswim' };

function DeleteAccountDialog({ account, onClose, onDeleted }) {
  const [usage, setUsage] = useState(null);
  const [error, setError] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const dialogRef = useModalFocus(onClose);

  useEffect(() => {
    accountsApi.usage(account.id)
      .then(r => setUsage(r.data))
      .catch(e => setError(e.response?.data?.error || e.response?.data?.detail || e.message));
  }, [account.id]);

  const confirm = async () => {
    setDeleting(true);
    setError(null);
    try {
      await accountsApi.remove(account.id);
      await onDeleted();
      onClose();
    } catch (e) {
      setError(e.response?.data?.error || e.response?.data?.detail || e.message);
      setDeleting(false);
    }
  };

  const trades = usage?.trades ?? 0;
  const tradeWord = trades === 1 ? 'trade' : 'trades';
  return createPortal(
    <div className="modal-overlay" onClick={(e) => { if (e.target === e.currentTarget && !deleting) onClose(); }}>
      <div className="modal acct-delete" role="alertdialog" aria-modal="true"
        aria-labelledby="acct-delete-title" aria-describedby="acct-delete-body" ref={dialogRef} tabIndex={-1}>
        <h2 id="acct-delete-title" className="acct-delete-title">
          <span className="acct-dot" style={{ background: account.color }} aria-hidden="true" />
          Delete {account.name}?
        </h2>
        <div id="acct-delete-body" className="acct-delete-body">
          {!usage && !error && <p>Counting what this account holds...</p>}
          {usage && (
            <p>
              {trades
                ? <>This permanently deletes <strong>{trades.toLocaleString()} {tradeWord}</strong>
                    {usage.first_date && <> from {usage.first_date} to {usage.last_date}</>}, with their journal
                    notes, tags and AI reviews</>
                : <>This account has no trades</>}
              {usage.diary_entries > 0
                ? <>, and {usage.diary_entries} diary {usage.diary_entries === 1 ? 'entry' : 'entries'}.</>
                : '.'}
            </p>
          )}
          <p className="acct-delete-note">A backup of the journal database is saved before anything is deleted.</p>
          {error && <div className="notice neg" role="alert">{error}</div>}
        </div>
        <div className="acct-delete-actions">
          <button type="button" className="btn btn-secondary" onClick={onClose} disabled={deleting}>Cancel</button>
          <button type="button" className="btn btn-danger" onClick={confirm} disabled={!usage || deleting}>
            <Trash2 size={15} aria-hidden="true" />
            {deleting ? 'Deleting...' : trades ? `Delete account and ${trades.toLocaleString()} ${tradeWord}` : 'Delete account'}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

function AccountMenu({ accounts, selectedAccountIds, onSelectAccounts, onAccountCreated }) {
  const [open, setOpen] = useState(false);
  const [showNewAccount, setShowNewAccount] = useState(false);
  const [newAcct, setNewAcct] = useState(NEW_ACCOUNT_DEFAULT);
  const [creating, setCreating] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [editName, setEditName] = useState('');
  const [deleting, setDeleting] = useState(null);
  const rootRef = useRef(null);
  const triggerRef = useRef(null);

  const picked = accounts.filter(a => selectedAccountIds.includes(a.id));
  const displayName = selectionLabel(accounts, selectedAccountIds);
  const allSelected = selectedAccountIds.length === 0;

  const close = (restoreFocus = true) => {
    setOpen(false);
    setEditingId(null);
    if (restoreFocus) triggerRef.current?.focus();
  };

  // Close on outside click and on Escape
  useEffect(() => {
    if (!open) return;
    const onDown = (e) => { if (rootRef.current && !rootRef.current.contains(e.target)) close(false); };
    const onKey = (e) => { if (e.key === 'Escape' && editingId == null) close(); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open, editingId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Clicking a name shows only that account, the same one-click switch as before.
  const choose = (id) => { onSelectAccounts(id == null ? [] : [id]); close(); };

  // Ticking a box adds or removes an account and keeps the menu open. Every
  // account ticked is the same as All Accounts.
  const toggle = (id) => {
    const next = selectedAccountIds.includes(id)
      ? selectedAccountIds.filter(x => x !== id)
      : [...selectedAccountIds, id];
    onSelectAccounts(next.length === accounts.length ? [] : accounts.map(a => a.id).filter(x => next.includes(x)));
  };

  const startDelete = (e, acct) => {
    e.stopPropagation();
    setDeleting(acct);
    close(false);
  };

  const endDelete = () => {
    setDeleting(null);
    triggerRef.current?.focus();
  };

  const startEdit = (e, acct) => {
    e.stopPropagation();
    setEditingId(acct.id);
    setEditName(acct.name);
  };

  const saveEdit = async (e, acctId) => {
    e.stopPropagation();
    if (!editName.trim()) return;
    try {
      await accountsApi.update(acctId, { name: editName.trim() });
      await onAccountCreated();
      setEditingId(null);
    } catch (err) {
      alert('Failed to rename: ' + (err.response?.data?.error || err.message));
    }
  };

  const handleCreateAccount = async (e) => {
    e.preventDefault();
    if (!newAcct.name.trim()) return;
    setCreating(true);
    try {
      await accountsApi.create(newAcct);
      await onAccountCreated();
      setNewAcct(NEW_ACCOUNT_DEFAULT);
      setShowNewAccount(false);
      close();
    } catch (err) {
      alert('Failed to create account: ' + (err.response?.data?.error || err.message));
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="acct" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className="acct-trigger"
        aria-haspopup="true"
        aria-expanded={open}
        aria-label={`Account: ${displayName}`}
        onClick={() => setOpen(v => !v)}
      >
        {picked.length > 0 && (
          <span className="acct-dots" aria-hidden="true">
            {picked.slice(0, 3).map(a => <span key={a.id} className="acct-dot" style={{ background: a.color }} />)}
          </span>
        )}
        <span className="acct-name">{displayName}</span>
        <ChevronDown size={14} aria-hidden="true" />
      </button>

      {open && (
        <div className="acct-menu" role="group" aria-label="Accounts">
          <div className="acct-menu-label">Show data for</div>
          <div className="acct-menu-hint">Click a name to show only it. Tick boxes to combine accounts.</div>
          <div className="dropdown-row acct-row">
            <input
              type="checkbox"
              className="acct-check"
              checked={allSelected}
              onChange={() => choose(null)}
              aria-label="Include all accounts"
            />
            <button
              type="button"
              aria-pressed={allSelected}
              className={`dropdown-item${allSelected ? ' selected' : ''}`}
              onClick={() => choose(null)}
              autoFocus
            >
              <span style={{ width: 8 }} aria-hidden="true" />
              All Accounts
            </button>
          </div>

          {accounts.map(acct => (
            <div key={acct.id} className="dropdown-row acct-row">
              {editingId === acct.id ? (
                <div className="dropdown-item" style={{ cursor: 'default' }}>
                  <span className="acct-dot" style={{ background: acct.color }} aria-hidden="true" />
                  <input
                    aria-label={`New name for ${acct.name}`}
                    value={editName}
                    onChange={e => setEditName(e.target.value)}
                    onKeyDown={e => {
                      if (e.key === 'Enter') saveEdit(e, acct.id);
                      if (e.key === 'Escape') { e.stopPropagation(); setEditingId(null); }
                    }}
                    onClick={e => e.stopPropagation()}
                    autoFocus
                    style={{ flex: 1, minWidth: 0, fontSize: 13, minHeight: 30, padding: '3px 8px' }}
                  />
                  <button type="button" className="btn btn-ghost btn-icon" onClick={e => saveEdit(e, acct.id)} aria-label="Save account name">
                    <Check size={15} className="pos" />
                  </button>
                  <button type="button" className="btn btn-ghost btn-icon" onClick={e => { e.stopPropagation(); setEditingId(null); }} aria-label="Cancel rename">
                    <X size={15} />
                  </button>
                </div>
              ) : (
                <>
                  <input
                    type="checkbox"
                    className="acct-check"
                    checked={selectedAccountIds.includes(acct.id)}
                    onChange={() => toggle(acct.id)}
                    aria-label={`Include ${acct.name}`}
                  />
                  <button
                    type="button"
                    aria-pressed={selectedAccountIds.includes(acct.id)}
                    className={`dropdown-item${selectedAccountIds.includes(acct.id) ? ' selected' : ''}`}
                    onClick={() => choose(acct.id)}
                    title={`Show only ${acct.name}`}
                  >
                    <span className="acct-dot" style={{ background: acct.color }} aria-hidden="true" />
                    <span>{acct.name}</span>
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost btn-icon"
                    onClick={e => startEdit(e, acct)}
                    aria-label={`Rename ${acct.name}`}
                    title="Rename account"
                  >
                    <Pencil size={13} />
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost btn-icon"
                    onClick={e => startDelete(e, acct)}
                    disabled={accounts.length <= 1}
                    aria-label={`Delete ${acct.name}`}
                    title={accounts.length <= 1 ? 'The only account cannot be deleted' : 'Delete account and its trades'}
                  >
                    <Trash2 size={13} />
                  </button>
                </>
              )}
            </div>
          ))}

          <div className="acct-menu-sep" />
          <button
            type="button"
            className="dropdown-item selected"
            aria-expanded={showNewAccount}
            onClick={() => setShowNewAccount(v => !v)}
          >
            <Plus size={15} aria-hidden="true" /> New Account
          </button>

          {showNewAccount && (
            <form onSubmit={handleCreateAccount} style={{ padding: '4px 12px 12px', display: 'grid', gap: 8 }}>
              <label className="sr-only" htmlFor="new-acct-name">Account name</label>
              <input
                id="new-acct-name"
                placeholder="Account name"
                value={newAcct.name}
                onChange={e => setNewAcct(p => ({ ...p, name: e.target.value }))}
                required
              />
              <label className="sr-only" htmlFor="new-acct-type">Account type</label>
              <select
                id="new-acct-type"
                value={newAcct.type}
                onChange={e => setNewAcct(p => ({ ...p, type: e.target.value }))}
              >
                <option value="day_trading">Day Trading</option>
                <option value="swing_trading">Swing Trading</option>
                <option value="investment">Investment</option>
              </select>
              <div style={{ display: 'flex', gap: 8 }}>
                <label className="sr-only" htmlFor="new-acct-color">Account color</label>
                <input
                  id="new-acct-color"
                  type="color"
                  value={newAcct.color}
                  onChange={e => setNewAcct(p => ({ ...p, color: e.target.value }))}
                  style={{ width: 44, minHeight: 36 }}
                />
                <button type="submit" className="btn btn-primary" disabled={creating} style={{ flex: 1 }}>
                  {creating ? 'Creating...' : 'Create'}
                </button>
              </div>
            </form>
          )}
        </div>
      )}

      {deleting && (
        <DeleteAccountDialog account={deleting} onClose={endDelete} onDeleted={onAccountCreated} />
      )}
    </div>
  );
}

export default function AppHeader({
  page, onNavigate, accounts, selectedAccountIds, onSelectAccounts,
  onAddTrade, onAccountCreated, brainOpen, onToggleBrain, detailOrigin = 'trades',
}) {
  // Trade detail keeps the page it was opened from highlighted (Trade View, Exit Lab or Reports).
  const activeId = page === 'trade-detail' ? detailOrigin : page;

  return (
    <header className="app-header">
      <div className="app-header-inner">
        <button type="button" className="app-brand" onClick={() => onNavigate('dashboard')} aria-label="AI Journal, go to Dashboard">
          <img className="app-brand-png" src={aiJournalLogo} alt="AI Journal" />
          {/* The PNG's wordmark is white, so themes draw the mark in their own colours (themes.css) */}
          <span className="app-brand-alt" aria-hidden="true">
            <svg viewBox="0 0 32 28" width="34" height="30">
              <defs>
                <linearGradient id="app-brand-grad" x1="0" y1="1" x2="1" y2="0">
                  <stop offset="0" stopColor="var(--logo-a)" />
                  <stop offset="1" stopColor="var(--logo-b)" />
                </linearGradient>
              </defs>
              <path d="M3 8c4-1.6 8.5-1.2 13 1.4C20.5 6.8 25 6.4 29 8v16c-4-1.6-8.5-1.2-13 1.4C11.5 22.8 7 22.4 3 24z"
                fill="none" stroke="url(#app-brand-grad)" strokeWidth="2" strokeLinejoin="round" />
              <path d="M16 9.4v16" stroke="url(#app-brand-grad)" strokeWidth="2" />
              <g fill="url(#app-brand-grad)">
                <rect x="18.6" y="13" width="2" height="5" rx=".5" />
                <rect x="21.6" y="10" width="2" height="5" rx=".5" />
                <rect x="24.6" y="7" width="2" height="5" rx=".5" />
              </g>
            </svg>
            <span className="app-brand-word">AI Journal</span>
          </span>
        </button>

        <nav className="app-nav" aria-label="Main">
          {NAV_ITEMS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              type="button"
              onClick={() => onNavigate(id)}
              className={`app-nav-item${activeId === id ? ' active' : ''}`}
              title={label}
              aria-label={label}
              aria-current={activeId === id ? 'page' : undefined}
            >
              <Icon size={16} aria-hidden="true" />
              <span className="app-nav-label">{label}</span>
            </button>
          ))}
        </nav>

        <div className="app-actions">
          {[["help", "Help", HelpCircle], ["settings", "Settings", SettingsIcon]].map(([id, label, Icon]) => <button key={id} type="button" className={`btn btn-ghost btn-icon${page === id ? ' active' : ''}`} aria-label={label} title={label} aria-current={page === id ? 'page' : undefined} onClick={() => onNavigate(id)}><Icon size={18} aria-hidden="true" /></button>)}
          <button
            type="button"
            className={`btn btn-ghost${brainOpen ? ' active' : ''}`}
            onClick={onToggleBrain}
            aria-pressed={brainOpen}
            title="Brain, your AI trading coach"
          >
            <Brain size={16} aria-hidden="true" /> Brain
          </button>
          <AccountMenu
            accounts={accounts}
            selectedAccountIds={selectedAccountIds}
            onSelectAccounts={onSelectAccounts}
            onAccountCreated={onAccountCreated}
          />
          <button
            type="button"
            className={`btn btn-ghost${page === 'import' ? ' active' : ''}`}
            onClick={() => onNavigate('import')}
            aria-current={page === 'import' ? 'page' : undefined}
          >
            <Upload size={16} aria-hidden="true" /> Import
          </button>
          <button type="button" className="btn btn-primary" onClick={onAddTrade}>
            <Plus size={16} aria-hidden="true" /> Add Trade
          </button>
        </div>
      </div>
    </header>
  );
}
