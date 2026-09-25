import { useState } from 'react';
import { tradesApi } from '../api';
import { ChevronRight, Trash2 } from 'lucide-react';

const signed$ = (v) => {
  if (v == null) return '-';
  const n = Number(v);
  return (n > 0 ? '+' : n < 0 ? '-' : '') + '$' + Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};

function ConfidenceDot({ level }) {
  return (
    <span
      className={`confidence-dot confidence-${level || 'unmatched'}`}
      title={`Match confidence: ${level || 'unmatched'}`}
    />
  );
}


export default function TradeRow({ trade, openTime, onOpenDetail, customSetups = [], onCustomSetupsChanged, onTradeDeleted }) {
  const pnl = trade.net_pnl ?? 0;
  const pnlTone = pnl > 0 ? 'pos' : pnl < 0 ? 'neg' : '';
  const [deleting, setDeleting] = useState(false);

  // A row is a link to the trade. The in-place expand was removed: it showed a
  // subset of the detail page and split the same job across two places.
  const handleOpen = () => { if (onOpenDetail) onOpenDetail(trade); };

  const handleDelete = async (e) => {
    e.stopPropagation();
    const label = `${trade.ticker} on ${trade.date}`;
    if (!window.confirm(`Delete this trade (${label})? This removes the trade and all its buy/sell executions permanently.`)) {
      return;
    }
    setDeleting(true);
    try {
      await tradesApi.delete(trade.id);
      if (onTradeDeleted) await onTradeDeleted();
    } catch (err) {
      alert('Could not delete trade: ' + (err?.response?.data?.detail || err.message));
      setDeleting(false);
    }
  };

  // Playbook setup tag: set by hand from the dropdown below.
  const ADD_NEW = '__add_new__';

  /** Setup cell: shows the badge, click to tag. Stock trades only. */
  function SetupEditor({ trade }) {
    const [editing, setEditing] = useState(false);
    const [saving, setSaving] = useState(false);
    const [local, setLocal] = useState({
      setup: trade.setup, grade: trade.setup_grade,
      notes: trade.setup_notes, source: trade.setup_source,
    });

    if (trade.instrument_type && trade.instrument_type !== 'STOCK') {
      return <span className="text-faint" style={{ fontSize: 13 }} title="Setups are tagged on stock trades only">—</span>;
    }

    const save = async (value) => {
      if (value === ADD_NEW) {
        const name = window.prompt(
          'Name your setup, e.g. "Bookmap absorption read".\n\n'
          + 'It gets added to the dropdown for every trade.');
        if (!name || !name.trim()) { setEditing(false); return; }
        setSaving(true);
        try {
          await tradesApi.createCustomSetup({ name: name.trim() });
          if (onCustomSetupsChanged) await onCustomSetupsChanged();
          value = name.trim();
        } catch (e) {
          alert('Could not add setup: ' + (e?.response?.data?.detail || e.message));
          setSaving(false); setEditing(false); return;
        }
      }
      setSaving(true);
      try {
        const { data } = await tradesApi.setSetup(trade.id, value === '' ? null : value);
        setLocal({
          setup: data.setup,
          grade: data.setup_grade !== undefined ? data.setup_grade : local.grade,
          notes: null,
          source: data.setup_source,
        });
        setEditing(false);
      } catch (e) {
        alert('Could not save setup: ' + (e?.response?.data?.detail || e.message));
      } finally {
        setSaving(false);
      }
    };

    if (editing) {
      return (
        <select
          autoFocus
          disabled={saving}
          aria-label={`Setup for ${trade.ticker} on ${trade.date}`}
          defaultValue={local.setup === 'NONE' ? 'NONE' : (local.setup || '')}
          onChange={e => save(e.target.value)}
          onBlur={() => setEditing(false)}
          onKeyDown={e => { if (e.key === 'Escape') setEditing(false); }}
          style={{ fontSize: 13, padding: '4px 8px', minHeight: 32, maxWidth: 260, borderColor: 'var(--accent-line)' }}
        >
          <option value="">(clear tag)</option>
          {customSetups.length > 0 && (
            <optgroup label="Playbook">
              {customSetups.map(cs => (
                <option key={cs.id} value={cs.name}>
                  {cs.name}{cs.side ? ` (${cs.side.toLowerCase()})` : ''}
                </option>
              ))}
            </optgroup>
          )}
          <optgroup label="Other">
            <option value="NONE">No setup</option>
            <option value={ADD_NEW}>+ Add a new setup…</option>
          </optgroup>
        </select>
      );
    }

    return (
      <button
        type="button"
        onClick={() => setEditing(true)}
        title="Click to set the setup manually"
        aria-label={`Setup: ${local.setup || trade.strategy || 'none'}. Change setup`}
        style={{ background: 'none', border: 0, padding: '2px 0', display: 'inline-flex', alignItems: 'center', gap: 6, textAlign: 'left' }}
      >
        <SetupBadge
          setup={local.setup}
          grade={local.grade}
          notes={local.notes}
          strategy={trade.strategy}
          source={local.source}
        />
        {local.source === 'manual' && (
          <span
            title="Manually tagged"
            className="text-faint"
            style={{ fontSize: 11 }}
          >
            ✎
          </span>
        )}
      </button>
    );
  }
  /** MFE / MAE / exit efficiency — how much of the move was there, and how much was taken. */
  function Excursion({ trade }) {
    const { mfe_pct: mfe, mae_pct: mae, exit_efficiency: eff } = trade;
    if (mfe == null && mae == null) {
      return <span className="text-faint" style={{ fontSize: 13 }}>—</span>;
    }
    // Green when most of the available move was captured, red when little was.
    const effCls = eff == null ? 'text-muted'
      : eff >= 60 ? 'pos' : eff >= 35 ? 'caution' : 'neg';
    const title = [
      `MFE  ${mfe >= 0 ? '+' : ''}${Number(mfe).toFixed(2)}%: best unrealised gain while open (the opportunity)`,
      `MAE  ${Number(mae).toFixed(2)}%: worst unrealised loss while open (the heat taken)`,
      eff != null ? `Exit efficiency ${Number(eff).toFixed(0)}%: share of the available move you captured` : null,
    ].filter(Boolean).join('\n');
    return (
      <span title={title} className="num" style={{ display: 'inline-flex', gap: 6, alignItems: 'baseline', fontSize: 13 }}>
        <span className="pos">{mfe >= 0 ? '+' : ''}{Number(mfe).toFixed(1)}%</span>
        <span className="text-faint">/</span>
        <span className="neg">{Number(mae).toFixed(1)}%</span>
        {eff != null && (
          <span className={effCls} style={{ fontWeight: 700 }}>{Number(eff).toFixed(0)}%</span>
        )}
      </span>
    );
  }

  // Grade colours: A-range reads positive, C/D caution, F neutral. Meaning is unchanged.
  const GRADE_CLASS = {
    'A++': 'pos', 'A+': 'pos', A: 'pos', B: 'pos',
    C: 'caution', D: 'caution', F: 'text-faint',
  };
  const GRADE_MEANING = {
    'A++': 'textbook execution',
    'A+': 'excellent execution',
    A: 'good execution, one minor slip',
    B: 'solid, minor execution warnings',
    C: 'one clear rule broken',
    D: 'multiple rules broken',
    F: 'no qualifying setup',
  };

  function SetupBadge({ setup, grade, notes, strategy, source }) {
    // No auto-classified setup: fall back to the manually tagged strategy.
    if (!setup || setup === 'NONE') {
      return strategy
        ? <span className="text-muted" style={{ fontSize: 14 }}>{strategy}</span>
        : <span className="text-faint" style={{ fontSize: 13 }}>—</span>;
    }
    let violations = [];
    try {
      const parsed = typeof notes === 'string' ? JSON.parse(notes) : notes;
      violations = parsed?.violations || [];
    } catch { /* notes may be absent or malformed; badge still renders */ }
    const highs = violations.filter(v => v.severity === 'high').length;
    // Graded setups read as a chip; an ungraded one is just the name.
    const gradeCls = GRADE_CLASS[grade] || 'text-muted';
    const title = [
      setup + '  (playbook setup)',
      grade ? `Grade ${grade}${GRADE_MEANING[grade] ? `: ${GRADE_MEANING[grade]}` : ''}` : null,
      strategy ? `Tagged strategy: ${strategy}` : null,
      violations.length ? '' : null,
      ...violations.map(v => `${v.severity === 'high' ? '✕' : '!'} ${v.msg}`),
    ].filter(x => x !== null).join('\n');
    return (
      <span title={title} style={{ display: 'inline-flex', alignItems: 'center', gap: 7, fontSize: 14 }}>
        <span style={{ color: 'var(--text-primary)', whiteSpace: 'nowrap' }}>{setup}</span>
        {grade && (
          <span className={`chip ${gradeCls === 'pos' ? 'pos' : gradeCls === 'caution' ? 'caution' : ''}`} style={{ fontSize: 11.5, padding: '1px 6px' }}>
            {grade}
          </span>
        )}
        {highs > 0 && (
          <span className="neg" style={{ fontSize: 12, fontWeight: 700 }} title={title}>
            ✕{highs}
          </span>
        )}
      </span>
    );
  }

  const side = (trade.side || '').toUpperCase();

  return (
    <>
      <tr
        className="row-link"
        onClick={handleOpen}
        tabIndex={0}
        onKeyDown={e => {
          if (e.target !== e.currentTarget) return;
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleOpen(); }
        }}
      >
        <td>
          <div className="num">{trade.date}</div>
          {openTime && <div className="num text-muted" style={{ fontSize: 13 }}>{openTime.slice(0, 5)}</div>}
        </td>
        <td>
          <span style={{ fontWeight: 600 }}>{trade.ticker}</span>
        </td>
        <td>
          <span className={`badge badge-${trade.instrument_type?.toLowerCase()}`}>
            {trade.instrument_type}
          </span>
        </td>
        <td className="text-muted">
          {side === 'LONG' ? 'Long' : side === 'SHORT' ? 'Short' : trade.side}
        </td>
        <td className={`num ${pnlTone}`} style={{ fontWeight: 600 }}>{signed$(pnl)}</td>
        <td onClick={e => e.stopPropagation()} onKeyDown={e => e.stopPropagation()}>
          <SetupEditor trade={trade} />
        </td>
        <td><Excursion trade={trade} /></td>
        <td className={`num ${trade.r_multiple > 0 ? 'pos' : trade.r_multiple < 0 ? 'neg' : 'text-muted'}`}>
          {trade.r_multiple != null ? `${trade.r_multiple > 0 ? '+' : ''}${Number(trade.r_multiple).toFixed(2)}R` : '—'}
        </td>
        <td style={{ textAlign: 'right' }} onClick={e => e.stopPropagation()}>
          <span className="text-muted" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            {trade.match_confidence && <ConfidenceDot level={trade.match_confidence} />}
            <button
              type="button"
              onClick={handleDelete}
              disabled={deleting}
              title="Delete trade"
              aria-label={`Delete trade ${trade.ticker} on ${trade.date}`}
              className="btn btn-ghost btn-icon"
              style={{ color: 'var(--result-neg)' }}
            >
              <Trash2 size={14} />
            </button>
            <span onClick={handleOpen} style={{ display: 'inline-flex', cursor: 'pointer' }}>
              <ChevronRight size={15} aria-hidden="true" />
            </span>
          </span>
        </td>
      </tr>

    </>
  );
}

