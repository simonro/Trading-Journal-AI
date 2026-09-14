import { useState, useRef, useEffect } from 'react';
import {
  LayoutDashboard, TrendingUp, BarChart2, BookOpen, Plus, ChevronDown,
  CalendarDays, Check, X, Pencil, CalendarCheck, HelpCircle, Upload, Brain, Settings as SettingsIcon,
  Scale,
} from 'lucide-react';
import { accountsApi } from '../api';
import aiJournalLogo from '../assets/ai-journal-logo.png';

// Every page stays one click away. Import, Brain and Add Trade live with the
// account selector on the right, the rest are the labeled navigation.
const NAV_ITEMS = [
  { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { id: 'trades', label: 'Trade View', icon: TrendingUp },
  { id: 'calendar', label: 'Calendar', icon: CalendarDays },
  { id: 'day-review', label: 'Day Review', icon: CalendarCheck },
  { id: 'reports', label: 'Reports', icon: BarChart2 },
  { id: 'reconciliation', label: 'Reconciliation', icon: Scale },
  { id: 'diary', label: 'Diary', icon: BookOpen },
  { id: 'help', label: 'Help', icon: HelpCircle },
  { id: 'settings', label: 'Settings', icon: SettingsIcon },
];

const NEW_ACCOUNT_DEFAULT = { name: '', type: 'day_trading', color: '#6366f1', broker: 'Thinkorswim' };

function AccountMenu({ accounts, selectedAccountId, onSelectAccount, onAccountCreated }) {
  const [open, setOpen] = useState(false);
  const [showNewAccount, setShowNewAccount] = useState(false);
  const [newAcct, setNewAcct] = useState(NEW_ACCOUNT_DEFAULT);
  const [creating, setCreating] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [editName, setEditName] = useState('');
  const rootRef = useRef(null);
  const triggerRef = useRef(null);

  const selectedAccount = accounts.find(a => a.id === selectedAccountId);
  const displayName = selectedAccount ? selectedAccount.name : 'All Accounts';

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

  const choose = (id) => { onSelectAccount(id); close(); };

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
        {selectedAccount && <span className="acct-dot" style={{ background: selectedAccount.color }} aria-hidden="true" />}
        <span className="acct-name">{displayName}</span>
        <ChevronDown size={14} aria-hidden="true" />
      </button>

      {open && (
        <div className="acct-menu" role="menu" aria-label="Accounts">
          <div className="acct-menu-label">Show data for</div>
          <button
            type="button"
            role="menuitemradio"
            aria-checked={selectedAccountId === null}
            className={`dropdown-item${selectedAccountId === null ? ' selected' : ''}`}
            onClick={() => choose(null)}
            autoFocus
          >
            <span style={{ width: 8 }} aria-hidden="true" />
            All Accounts
          </button>

          {accounts.map(acct => (
            <div key={acct.id} className="dropdown-row">
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
                  <button
                    type="button"
                    role="menuitemradio"
                    aria-checked={selectedAccountId === acct.id}
                    className={`dropdown-item${selectedAccountId === acct.id ? ' selected' : ''}`}
                    onClick={() => choose(acct.id)}
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
    </div>
  );
}

export default function AppHeader({
  page, onNavigate, accounts, selectedAccountId, onSelectAccount,
  onAddTrade, onAccountCreated, brainOpen, onToggleBrain,
}) {
  // Trade detail is reached from Trade View, so it keeps that tab highlighted.
  const activeId = page === 'trade-detail' ? 'trades' : page;

  return (
    <header className="app-header">
      <div className="app-header-inner">
        <button type="button" className="app-brand" onClick={() => onNavigate('dashboard')} aria-label="AI Journal, go to Dashboard">
          <img src={aiJournalLogo} alt="AI Journal" />
        </button>

        <nav className="app-nav" aria-label="Main">
          {NAV_ITEMS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              type="button"
              onClick={() => onNavigate(id)}
              className={`app-nav-item${activeId === id ? ' active' : ''}`}
              aria-current={activeId === id ? 'page' : undefined}
            >
              <Icon size={16} aria-hidden="true" />
              {label}
            </button>
          ))}
        </nav>

        <div className="app-actions">
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
            selectedAccountId={selectedAccountId}
            onSelectAccount={onSelectAccount}
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
