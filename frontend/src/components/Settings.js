import { useState, useEffect, useCallback, useMemo } from 'react';
import { Plus, Pencil, GitMerge, Trash2, Search } from 'lucide-react';
import { libraryApi } from '../api';
import { PageHeader } from './ui';
import LotMatchingSettings from './LotMatchingSettings';
import BackupSettings from './BackupSettings';
import SecuritySettings from './SecuritySettings';

const SECTIONS = [
  { id: 'strategy', label: 'Strategies' },
  { id: 'source', label: 'Sources' },
  { id: 'tag', label: 'Tags' },
  { id: 'lot-matching', label: 'Lot Matching' },
  { id: 'backup', label: 'Backup' },
  { id: 'security', label: 'Security' },
];

const TAG_TYPE_LABEL = {
  mistake: 'Mistakes', execution: 'Execution', setup: 'Setup', emotion: 'Emotion', outcome: 'Outcome',
};
const TAG_TYPE_ORDER = ['mistake', 'execution', 'setup', 'emotion', 'outcome'];

const SECTION_COPY = {
  strategy: {
    title: 'Strategies',
    sub: 'Why you took the trade, the same idea as a setup.',
    noun: 'strategy',
  },
  source: {
    title: 'Sources',
    sub: 'Where the idea or alert came from.',
    noun: 'source',
  },
};

const errText = (e) => e?.response?.data?.detail || e?.response?.data?.error || e?.message || 'Something went wrong';
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

/** One editable list: strategies, sources, or a single tag type. */
function ItemList({ kind, tagType = '', title, sub, noun, items, onChanged }) {
  const [query, setQuery] = useState('');
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState({ name: '', description: '' });
  const [mode, setMode] = useState(null); // { type: 'edit'|'merge'|'delete', name }
  const [form, setForm] = useState({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);

  const base = { kind, tag_type: tagType };
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? items.filter(i => i.name.toLowerCase().includes(q) || (i.description || '').toLowerCase().includes(q)) : items;
  }, [items, query]);

  const close = () => { setMode(null); setForm({}); setError(null); };
  const run = async (fn, message) => {
    setBusy(true); setError(null);
    try {
      const res = await fn();
      setNotice(message(res?.data || {}));
      close();
      await onChanged();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  };

  const startAdd = () => { close(); setAdding(true); setDraft({ name: '', description: '' }); };
  const saveAdd = () => run(
    () => libraryApi.create({ ...base, name: draft.name, description: draft.description }),
    () => { setAdding(false); return `Added "${draft.name.trim()}".`; },
  );

  const open = (type, item) => {
    setAdding(false); setError(null); setNotice(null);
    setMode({ type, name: item.name });
    if (type === 'edit') setForm({ name: item.name, description: item.description || '' });
    if (type === 'merge') setForm({ target: '' });
    if (type === 'delete') setForm({ reassign: '' });
  };

  const others = (name) => items.filter(i => i.name !== name);

  return (
    <section className="card panel-flush" aria-label={title}>
      <div className="settings-head">
        <div style={{ minWidth: 0 }}>
          <h2 className="section-title">{title} <span className="text-muted num" style={{ fontWeight: 500, fontSize: 14 }}>{items.length}</span></h2>
          {sub && <div className="section-sub">{sub}</div>}
        </div>
        <div className="settings-tools">
          <label className="settings-search">
            <Search size={14} aria-hidden="true" />
            <input
              type="search"
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder={`Search ${title.toLowerCase()}`}
              aria-label={`Search ${title.toLowerCase()}`}
            />
          </label>
          <button type="button" className="btn btn-primary btn-sm" onClick={startAdd}>
            <Plus size={14} /> Add {noun}
          </button>
        </div>
      </div>

      {notice && <div className="notice pos settings-notice" role="status">{notice}</div>}

      {adding && (
        <div className="settings-form">
          <label>
            <span className="field-label">Name</span>
            <input autoFocus value={draft.name} onChange={e => setDraft(d => ({ ...d, name: e.target.value }))}
              onKeyDown={e => { if (e.key === 'Enter' && draft.name.trim()) saveAdd(); if (e.key === 'Escape') setAdding(false); }} />
          </label>
          <label style={{ flex: 2 }}>
            <span className="field-label">Description (optional)</span>
            <input value={draft.description} onChange={e => setDraft(d => ({ ...d, description: e.target.value }))} />
          </label>
          <div className="settings-form-actions">
            <button type="button" className="btn btn-primary btn-sm" disabled={busy || !draft.name.trim()} onClick={saveAdd}>Save</button>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => { setAdding(false); setError(null); }}>Cancel</button>
          </div>
          {error && <div className="notice neg" role="alert" style={{ flexBasis: '100%' }}>{error}</div>}
        </div>
      )}

      <div className="table-container">
        <table style={{ minWidth: 640 }}>
          <thead>
            <tr>
              <th style={{ paddingLeft: 20 }}>Name</th>
              <th>Description</th>
              <th className="num">Trades</th>
              <th className="num" style={{ paddingRight: 20 }}><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {shown.map(item => {
              const active = mode && mode.name === item.name ? mode.type : null;
              const fixed = false;
              return [
                <tr key={item.name} className={active ? 'row-selected' : undefined}>
                  <td style={{ paddingLeft: 20, fontWeight: 600 }}>
                    {item.name}
                    {item.aliases?.length > 0 && (
                      <div className="text-muted" style={{ fontSize: 12.5, fontWeight: 400, marginTop: 2 }}>
                        Also saved as: {item.aliases.join(', ')}
                      </div>
                    )}
                  </td>
                  <td className="text-muted" style={{ fontSize: 14 }}>{item.description || ''}</td>
                  <td className="num">{item.trades}</td>
                  <td className="num" style={{ paddingRight: 20, whiteSpace: 'nowrap' }}>
                    <button type="button" className="btn btn-ghost btn-sm" onClick={() => open('edit', item)} aria-label={`Edit ${item.name}`}>
                      <Pencil size={13} /> Edit
                    </button>
                    {!fixed && (
                      <>
                        <button type="button" className="btn btn-ghost btn-sm" onClick={() => open('merge', item)} aria-label={`Merge ${item.name} into another ${noun}`} disabled={items.length < 2}>
                          <GitMerge size={13} /> Merge
                        </button>
                        <button type="button" className="btn btn-ghost btn-sm" style={{ color: 'var(--result-neg)' }} onClick={() => open('delete', item)} aria-label={`Delete ${item.name}`}>
                          <Trash2 size={13} /> Delete
                        </button>
                      </>
                    )}
                  </td>
                </tr>,
                active && (
                  <tr key={`${item.name}-panel`} className="settings-panel-row">
                    <td colSpan={4}>
                      {active === 'edit' && (
                        <div className="settings-form">
                          <label>
                            <span className="field-label">Name</span>
                            <input value={form.name} disabled={fixed} autoFocus={!fixed}
                              onChange={e => setForm(f => ({ ...f, name: e.target.value }))} />
                          </label>
                          <label style={{ flex: 2 }}>
                            <span className="field-label">Description</span>
                            <input value={form.description} autoFocus={fixed}
                              onChange={e => setForm(f => ({ ...f, description: e.target.value }))} />
                          </label>
                          <div className="settings-form-actions">
                            <button type="button" className="btn btn-primary btn-sm" disabled={busy || !String(form.name || '').trim()}
                              onClick={() => run(
                                () => libraryApi.update({ ...base, name: item.name, new_name: form.name, description: form.description }),
                                (r) => (r.name && r.name !== item.name
                                  ? `Renamed "${item.name}" to "${r.name}" on ${plural(r.trades ?? item.trades, 'trade')}.`
                                  : `Saved "${item.name}".`),
                              )}>
                              Save
                            </button>
                            <button type="button" className="btn btn-ghost btn-sm" onClick={close}>Cancel</button>
                          </div>
                          {!fixed && item.trades > 0 && (
                            <div className="text-muted" style={{ flexBasis: '100%', fontSize: 13 }}>
                              Renaming updates {plural(item.trades, 'trade')}.
                            </div>
                          )}
                        </div>
                      )}

                      {active === 'merge' && (
                        <div className="settings-form">
                          <label style={{ flex: 2 }}>
                            <span className="field-label">Merge "{item.name}" into</span>
                            <select value={form.target} autoFocus onChange={e => setForm({ target: e.target.value })}>
                              <option value="">Choose a {noun}…</option>
                              {others(item.name).map(o => <option key={o.name} value={o.name}>{o.name} ({o.trades})</option>)}
                            </select>
                          </label>
                          <div className="settings-form-actions">
                            <button type="button" className="btn btn-primary btn-sm" disabled={busy || !form.target}
                              onClick={() => run(
                                () => libraryApi.merge({ ...base, source_name: item.name, target_name: form.target }),
                                (r) => `Merged "${item.name}" into "${form.target}": ${plural(r.moved ?? item.trades, 'trade')} moved.`,
                              )}>
                              Merge
                            </button>
                            <button type="button" className="btn btn-ghost btn-sm" onClick={close}>Cancel</button>
                          </div>
                          <div className="text-muted" style={{ flexBasis: '100%', fontSize: 13 }}>
                            {form.target
                              ? `Moves ${plural(item.trades, 'trade')} to "${form.target}" and removes "${item.name}". If the AI writes "${item.name}" again, it is saved as "${form.target}".`
                              : 'Pick the name to keep.'}
                          </div>
                        </div>
                      )}

                      {active === 'delete' && (
                        <div className="settings-form">
                          {item.trades > 0 ? (
                            <label style={{ flex: 2 }}>
                              <span className="field-label">{plural(item.trades, 'trade')} use "{item.name}". Reassign them to</span>
                              <select value={form.reassign} autoFocus onChange={e => setForm({ reassign: e.target.value })}>
                                <option value="">Leave blank</option>
                                {others(item.name).map(o => <option key={o.name} value={o.name}>{o.name}</option>)}
                              </select>
                            </label>
                          ) : (
                            <div style={{ flex: 2, alignSelf: 'center' }}>Delete "{item.name}"? No trades use it.</div>
                          )}
                          <div className="settings-form-actions">
                            <button type="button" className="btn btn-danger btn-sm" disabled={busy}
                              onClick={() => run(
                                () => libraryApi.remove({ ...base, name: item.name, reassign_to: form.reassign || null }),
                                (r) => (r.reassigned_to
                                  ? `Deleted "${item.name}". ${plural(r.affected, 'trade')} now use "${r.reassigned_to}".`
                                  : `Deleted "${item.name}".${r.affected ? ` ${plural(r.affected, 'trade')} left blank.` : ''}`),
                              )}>
                              Delete
                            </button>
                            <button type="button" className="btn btn-ghost btn-sm" onClick={close}>Cancel</button>
                          </div>
                        </div>
                      )}
                      {error && <div className="notice neg" role="alert" style={{ marginTop: 10 }}>{error}</div>}
                    </td>
                  </tr>
                ),
              ];
            })}
            {!shown.length && (
              <tr><td colSpan={4}><div className="empty">{query ? 'Nothing matches that search.' : `No ${title.toLowerCase()} yet.`}</div></td></tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default function Settings() {
  const [lib, setLib] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [section, setSection] = useState('strategy');

  const load = useCallback(async () => {
    try {
      const res = await libraryApi.list();
      setLib(res.data);
      setLoadError(null);
    } catch (e) {
      setLoadError(errText(e));
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const onTabKey = (e) => {
    const i = SECTIONS.findIndex(s => s.id === section);
    if (e.key === 'ArrowRight') setSection(SECTIONS[(i + 1) % SECTIONS.length].id);
    if (e.key === 'ArrowLeft') setSection(SECTIONS[(i - 1 + SECTIONS.length) % SECTIONS.length].id);
  };

  return (
    <div>
      <PageHeader
        title="Settings"
        subtitle="Clean up the names the journal uses. Renames and merges update every trade that uses the name."
      />

      <div className="tabs" role="tablist" aria-label="Settings sections" style={{ marginBottom: 'var(--space-5)' }}>
        {SECTIONS.map(s => (
          <button
            type="button"
            key={s.id}
            role="tab"
            id={`settings-tab-${s.id}`}
            aria-selected={section === s.id}
            aria-controls="settings-panel"
            tabIndex={section === s.id ? 0 : -1}
            className="tab"
            onClick={() => setSection(s.id)}
            onKeyDown={onTabKey}
          >
            {s.label}
            {lib && ['strategy', 'source', 'tag'].includes(s.id) && (
              <span className="text-muted num" style={{ marginLeft: 6, fontWeight: 500 }}>
                {s.id === 'tag'
                  ? TAG_TYPE_ORDER.reduce((n, t) => n + (lib.tags?.[t]?.length || 0), 0)
                  : (s.id === 'strategy' ? lib.strategies : lib.sources).length}
              </span>
            )}
          </button>
        ))}
      </div>

      <div role="tabpanel" id="settings-panel" aria-labelledby={`settings-tab-${section}`}>
        {loadError && <div className="notice neg" role="alert">{loadError}</div>}
        {!lib && !loadError && ['strategy', 'source', 'tag'].includes(section) && <div className="skeleton" style={{ height: 320 }} />}

        {lib && section === 'strategy' && (
          <ItemList kind="strategy" {...SECTION_COPY.strategy} items={lib.strategies} onChanged={load} />
        )}
        {lib && section === 'source' && (
          <ItemList kind="source" {...SECTION_COPY.source} items={lib.sources} onChanged={load} />
        )}
        {lib && section === 'tag' && (
          <div className="stack">
            {TAG_TYPE_ORDER.map(t => (
              <ItemList
                key={t}
                kind="tag"
                tagType={t}
                title={`${TAG_TYPE_LABEL[t]} tags`}
                noun="tag"
                items={lib.tags?.[t] || []}
                onChanged={load}
              />
            ))}
          </div>
        )}
        {section === 'lot-matching' && <LotMatchingSettings />}
        {section === 'backup' && <BackupSettings />}
        {section === 'security' && <SecuritySettings />}
      </div>
    </div>
  );
}
