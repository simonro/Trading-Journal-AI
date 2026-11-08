/* Row actions in Trade View: move a trade to another account, or delete it.
   The gear is always visible (never hover-only); the menu is fixed-positioned so the
   table's scroll container cannot clip it on the last rows. */
import { useState, useRef, useEffect, useCallback } from 'react';
import { Settings, ArrowRightLeft, Trash2 } from 'lucide-react';
import { tradesApi, accountsApi } from '../api';
import useModalFocus from './useModalFocus';

const fmtMoney = (v) => {
  const n = Number(v) || 0;
  return (n > 0 ? '+' : n < 0 ? '-' : '') + '$' + Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};
const describe = (t) => `${t.ticker} ${String(t.side || '').toLowerCase()} on ${t.date} (${fmtMoney(t.net_pnl)})`;
const errText = (e) => e?.response?.data?.detail || e?.response?.data?.error || e?.message || 'Something went wrong.';

function Dialog({ title, id, onClose, children }) {
  const ref = useModalFocus(onClose);
  return (
    <div className="modal-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal trade-action-dialog" role="dialog" aria-modal="true" aria-labelledby={id} ref={ref} tabIndex={-1}>
        <h2 id={id} className="section-title">{title}</h2>
        {children}
      </div>
    </div>
  );
}

function MoveDialog({ trade, accounts: initial, onClose, onDone }) {
  // Accounts can be added, renamed or deleted from the header at any time: refresh on open.
  const [accounts, setAccounts] = useState(initial);
  useEffect(() => {
    let live = true;
    accountsApi.list().then((res) => { if (live && res?.data) setAccounts(res.data); }).catch(() => {});
    return () => { live = false; };
  }, []);
  const others = accounts.filter((a) => a.id !== trade.account_id);
  const from = accounts.find((a) => a.id === trade.account_id);
  const [target, setTarget] = useState(others[0]?.id ?? '');
  // keep the selection valid when the refreshed list arrives
  useEffect(() => {
    if (!others.some((a) => String(a.id) === String(target))) setTarget(others[0]?.id ?? '');
  }, [others, target]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const submit = async (e) => {
    e.preventDefault();
    if (!target) return;
    setBusy(true);
    setError(null);
    try {
      await tradesApi.moveAccount(trade.id, Number(target));
      onDone('moved');
    } catch (err) {
      setError(errText(err));
      setBusy(false);
    }
  };

  return (
    <Dialog title="Move trade to another account" id={`move-title-${trade.id}`} onClose={onClose}>
      <form onSubmit={submit}>
        <p className="trade-action-text">
          {describe(trade)}{from ? <>, now in <b>{from.name}</b></> : null}. Its notes and tags move with it.
        </p>
        {others.length ? (
          <>
            <label className="field-label" htmlFor={`move-to-${trade.id}`}>Move to</label>
            <select id={`move-to-${trade.id}`} value={target} onChange={(e) => setTarget(e.target.value)} style={{ width: '100%' }}>
              {others.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          </>
        ) : (
          <div className="notice">There is no other account to move it to. Create one from the account menu first.</div>
        )}
        <p className="trade-action-hint">
          Importing this day's statement into {from ? from.name : 'the old account'} again would add
          the trade back there.
        </p>
        {error && <div className="notice neg" role="alert">{error}</div>}
        <div className="trade-action-buttons">
          <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy || !others.length}>
            {busy ? 'Moving...' : 'Move trade'}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

function DeleteDialog({ trade, onClose, onDone }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      await tradesApi.delete(trade.id);
      onDone('deleted');
    } catch (err) {
      setError(errText(err));
      setBusy(false);
    }
  };

  return (
    <Dialog title="Delete this trade?" id={`delete-title-${trade.id}`} onClose={onClose}>
      <p className="trade-action-text">
        {describe(trade)} will be removed with its notes and tags. This cannot be undone.
      </p>
      <p className="trade-action-hint">Importing this day's statement again would add the trade back.</p>
      {error && <div className="notice neg" role="alert">{error}</div>}
      <div className="trade-action-buttons">
        <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
        <button type="button" className="btn btn-danger" onClick={confirm} disabled={busy}>
          {busy ? 'Deleting...' : 'Delete trade'}
        </button>
      </div>
    </Dialog>
  );
}

export default function TradeActions({ trade, openTime = '', accounts = [], onChanged }) {
  // Several trades can share a ticker and a day, so the entry time keeps the label unique.
  const label = `Actions for ${trade.ticker} on ${trade.date}${openTime ? ` at ${openTime.slice(0, 5)}` : ''}`;
  const [menu, setMenu] = useState(null);       // {top, left} while the menu is open
  const [dialog, setDialog] = useState(null);   // 'move' | 'delete'
  const button = useRef(null);
  const menuRef = useRef(null);

  const close = useCallback((refocus = true) => {
    setMenu(null);
    if (refocus) button.current?.focus();
  }, []);

  const open = () => {
    const r = button.current.getBoundingClientRect();
    const width = 200;
    const height = 84;   // two items; flip above the gear when there is no room below
    const below = r.bottom + 4 + height <= window.innerHeight;
    setMenu({
      top: below ? r.bottom + 4 : Math.max(8, r.top - 4 - height),
      left: Math.max(8, Math.min(r.right - width, window.innerWidth - width - 8)),
    });
  };

  // The open menu: first item focused, arrows move, Escape and outside clicks close it.
  useEffect(() => {
    if (!menu) return undefined;
    const items = () => [...(menuRef.current?.querySelectorAll('[role="menuitem"]') || [])];
    // preventScroll: the menu sits inside the table's scroller, and a focus scroll would close it
    const focus = (el) => el?.focus({ preventScroll: true });
    focus(items()[0]);
    const onKey = (e) => {
      const list = items();
      const i = list.indexOf(document.activeElement);
      if (e.key === 'Escape') { e.preventDefault(); close(); }
      else if (e.key === 'ArrowDown') { e.preventDefault(); focus(list[(i + 1) % list.length]); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); focus(list[(i - 1 + list.length) % list.length]); }
      else if (e.key === 'Tab') close(false);
    };
    const onPointer = (e) => {
      if (!menuRef.current?.contains(e.target) && !button.current?.contains(e.target)) close(false);
    };
    // A scroll of the page or the table moves the gear away from the fixed menu: close it.
    const onMove = (e) => { if (!menuRef.current?.contains(e.target)) close(false); };
    document.addEventListener('keydown', onKey);
    document.addEventListener('mousedown', onPointer);
    window.addEventListener('scroll', onMove, true);
    window.addEventListener('resize', onMove);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('mousedown', onPointer);
      window.removeEventListener('scroll', onMove, true);
      window.removeEventListener('resize', onMove);
    };
  }, [menu, close]);

  const pick = (which) => { setMenu(null); setDialog(which); };
  // A moved trade usually stays on screen, so the gear gets focus back; a deleted one does not,
  // so Trade View decides where focus goes.
  const done = (what) => {
    setDialog(null);
    Promise.resolve(onChanged?.(what)).then(() => { if (what === 'moved') button.current?.focus(); });
  };
  const cancel = () => { setDialog(null); button.current?.focus(); };

  return (
    <>
      <button
        type="button"
        ref={button}
        className="btn btn-ghost btn-icon trade-actions-btn"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={menu ? 'true' : 'false'}
        title="Move or delete"
        onClick={() => (menu ? close() : open())}
      >
        <Settings size={15} aria-hidden="true" />
      </button>
      {menu && (
        <div className="trade-actions-menu" role="menu" ref={menuRef} aria-label={label}
          style={{ top: menu.top, left: menu.left }}>
          <button type="button" role="menuitem" onClick={() => pick('move')}>
            <ArrowRightLeft size={14} aria-hidden="true" /> Move to account...
          </button>
          <button type="button" role="menuitem" className="danger" onClick={() => pick('delete')}>
            <Trash2 size={14} aria-hidden="true" /> Delete trade
          </button>
        </div>
      )}
      {dialog === 'move' && <MoveDialog trade={trade} accounts={accounts} onClose={cancel} onDone={done} />}
      {dialog === 'delete' && <DeleteDialog trade={trade} onClose={cancel} onDone={done} />}
    </>
  );
}
