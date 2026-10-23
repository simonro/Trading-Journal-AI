import { useState } from 'react';
import { exitlabApi } from '../../api';
import { errorText, Notice } from './shared';

const AUTO_LABELS = { A: 'Flush Reclaim (A)', B: 'Leader Long (B)', B_LITE: 'Leader Long Lite (B_LITE)', C: 'Laggard Short (C)', NONE: 'No setup' };
export const autoSetupLabel = name => AUTO_LABELS[name] || name;
const labelOf = item => item.group === 'auto' ? autoSetupLabel(item.name) : item.name;
const keyOf = item => JSON.stringify([item.group, item.name]);

export function SetupChips({ value }) {
  return <div className="xl-setup-tags">{value == null ? <span>All setups</span> : value.map(item => <span key={keyOf(item)}>{labelOf(item)}</span>)}</div>;
}

export function SetupSelect({ value, options = [], onChange, disabled }) {
  const [search, setSearch] = useState('');
  const groups = [['strategy', 'Strategies'], ['auto', 'Auto setups']].map(([group, label]) => {
    const items = new Map((options.find(o => o.group === group)?.items || []).map(item => [item.name, { ...item, group }]));
    (value || []).filter(item => item.group === group).forEach(item => {
      if (!items.has(item.name)) items.set(item.name, { ...item, trades: 0, eligible: 0 });
    });
    return { group, label, items: [...items.values()] };
  });
  const selected = item => !!value?.some(v => keyOf(v) === keyOf(item));
  const toggle = item => {
    const next = selected(item) ? value.filter(v => keyOf(v) !== keyOf(item)) : [...(value || []), { group: item.group, name: item.name }];
    onChange(next.length ? next : null);
  };
  return <fieldset className="xl-setup-select" disabled={disabled}><legend>Applies to setups</legend>
    <button type="button" className={`xl-chip ${value == null ? 'on' : ''}`} aria-pressed={value == null} onClick={() => onChange(null)}>All setups</button>
    {groups.reduce((n, group) => n + group.items.length, 0) > 12 && <label className="xl-setup-search">Search setups<input type="search" value={search} onChange={e => setSearch(e.target.value)} /></label>}
    {groups.map(group => <section key={group.group} aria-label={group.label} className="xl-setup-group"><strong>{group.label}</strong><div className="xl-chips">
      {group.items.filter(item => labelOf(item).toLowerCase().includes(search.toLowerCase())).map(item => <button type="button" className={`xl-chip ${selected(item) ? 'on' : ''}`} key={keyOf(item)} aria-pressed={selected(item)} onClick={() => toggle(item)}>{labelOf(item)} <small>{item.trades} trades · {item.eligible} eligible</small></button>)}
    </div></section>)}
    <p className="xl-muted">All setups includes trades with no setup. Counts are from this account.</p>
  </fieldset>;
}

function AssignmentRow({ rule, options, onSaved }) {
  const [value, setValue] = useState(rule.applies_to_setups ?? null);
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const dirty = JSON.stringify(value) !== JSON.stringify(rule.applies_to_setups ?? null);
  async function save() {
    setBusy(true); setError('');
    try { await exitlabApi.assignRule(rule.id, value); await onSaved(); }
    catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  return <section className="xl-assignment-row"><strong>{rule.name}</strong>
    <SetupSelect value={value} options={options} onChange={setValue} disabled={busy} />
    {dirty && <button type="button" className="btn btn-secondary btn-sm" disabled={busy} onClick={save}>{busy ? 'Saving...' : `Save assignment for ${rule.name}`}</button>}
    {error && <Notice error>{error}</Notice>}
  </section>;
}

export default function SetupAssignments({ rules = [], options = [], onSaved }) {
  return <div className="xl-assignments">{rules.map(rule => <AssignmentRow key={`${rule.id}:${JSON.stringify(rule.applies_to_setups)}`} rule={rule} options={options} onSaved={onSaved} />)}</div>;
}
