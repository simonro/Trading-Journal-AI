import { useState, useEffect } from 'react';
import { ChevronDown, ChevronUp, BookOpen, Trash2 } from 'lucide-react';
import { diaryApi, API_BASE } from '../api';
import { PageHeader } from './ui';
import EdgeDayNote, { isEdgeDayNote } from './EdgeDayNote';

const BACKEND = API_BASE;

function ConfidenceDot({ level }) {
  return (
    <span
      className={`confidence-dot confidence-${level || 'unmatched'}`}
      style={{ marginRight: 6 }}
      title={`Match: ${level}`}
    />
  );
}

function DeleteButton({ onDelete, small, label = 'Delete' }) {
  const [confirm, setConfirm] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const handleClick = async (e) => {
    e.stopPropagation();
    if (!confirm) { setConfirm(true); return; }
    setDeleting(true);
    await onDelete().catch(() => { setDeleting(false); setConfirm(false); });
  };

  if (confirm) {
    return (
      <span style={{ display: 'inline-flex', gap: 6 }} onClick={e => e.stopPropagation()}>
        <button
          type="button"
          onClick={handleClick}
          disabled={deleting}
          className="btn btn-danger btn-sm"
        >
          {deleting ? '…' : 'Confirm'}
        </button>
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); setConfirm(false); }}
          className="btn btn-ghost btn-sm"
        >
          Cancel
        </button>
      </span>
    );
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      className="btn btn-ghost btn-icon"
      title={label}
      aria-label={label}
    >
      <Trash2 size={small ? 14 : 15} />
    </button>
  );
}

const fieldLabel = { color: 'var(--text-secondary)', fontSize: 12.5, marginBottom: 3 };

function DiaryCard({ entry, onDeleted }) {
  const [expanded, setExpanded] = useState(false);
  // A .txt or .csv upload also stores a path, but only a picture can be shown as one.
  const hasImage = !!entry.image_path && /\.(png|jpe?g|webp|gif|heic|heif)$/i.test(entry.image_path);
  const analysis = entry.ai_analysis;
  const panelId = `diary-entry-${entry.id}`;

  return (
    <div className="card" style={{ marginBottom: 'var(--space-3)' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
        <button
          type="button"
          className="diary-toggle"
          aria-expanded={expanded}
          aria-controls={panelId}
          onClick={() => setExpanded(v => !v)}
        >
          {/* Thumbnail */}
          {hasImage ? (
            <img
              src={`${BACKEND}/uploads/${entry.image_path}`}
              alt=""
              style={{ width: 76, height: 56, objectFit: 'cover', borderRadius: 'var(--radius-sm)', border: '1px solid var(--divider)', flexShrink: 0 }}
              onError={e => { e.target.style.display = 'none'; }}
            />
          ) : (
            <div style={{ width: 76, height: 56, background: 'var(--surface-inset)', borderRadius: 'var(--radius-sm)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
              <BookOpen size={18} color="var(--text-tertiary)" />
            </div>
          )}

          {/* Summary */}
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <span className="num" style={{ fontSize: 13, color: 'var(--text-secondary)' }}>Entry #{entry.id}</span>
              {analysis && (
                <span className="chip">
                  <span className="num">{analysis.trade_analyses?.length || 0}</span>&nbsp;trades
                </span>
              )}
            </div>
            {analysis?.overall_summary ? (
              <div style={{ color: 'var(--text-primary)', fontSize: 14.5, lineHeight: 1.55, marginTop: 6, display: '-webkit-box', WebkitLineClamp: expanded ? 'none' : 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                {analysis.overall_summary}
              </div>
            ) : (
              <div style={{ color: 'var(--text-secondary)', fontSize: 14, marginTop: 6 }}>No AI analysis available.</div>
            )}
          </div>
          <span style={{ color: 'var(--text-secondary)', paddingTop: 2 }} aria-hidden="true">
            {expanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
          </span>
        </button>
        <DeleteButton label={`Delete diary entry ${entry.id}`} onDelete={() => diaryApi.delete(entry.id).then(() => onDeleted(entry.id))} />
      </div>

      {expanded && analysis && (
        <div id={panelId} style={{ marginTop: 16, borderTop: '1px solid var(--divider)', paddingTop: 16 }}>

          {hasImage && (
            <div style={{ marginBottom: 16, textAlign: 'center' }}>
              <img
                src={`${BACKEND}/uploads/${entry.image_path}`}
                alt={`Diary entry ${entry.id}`}
                style={{ maxWidth: '100%', borderRadius: 'var(--radius-md)', border: '1px solid var(--divider)' }}
              />
            </div>
          )}

          <div className="grid-2" style={{ marginBottom: 16 }}>
            {analysis.patterns_identified?.length > 0 && (
              <div>
                <h3 style={{ fontWeight: 600, marginBottom: 8, fontSize: 15 }}>Patterns Identified</h3>
                <ul style={{ paddingLeft: 20, color: 'var(--text-secondary)', fontSize: 14, lineHeight: 1.75 }}>
                  {analysis.patterns_identified.map((p, i) => <li key={i}>{p}</li>)}
                </ul>
              </div>
            )}
            {analysis.improvement_areas?.length > 0 && (
              <div>
                <h3 style={{ fontWeight: 600, marginBottom: 8, fontSize: 15 }}>Improvement Areas</h3>
                <ul style={{ paddingLeft: 20, color: 'var(--text-secondary)', fontSize: 14, lineHeight: 1.75 }}>
                  {analysis.improvement_areas.map((a, i) => <li key={i}>{a}</li>)}
                </ul>
              </div>
            )}
          </div>

          {analysis.trade_analyses?.length > 0 && (
            <div>
              <h3 style={{ fontWeight: 600, marginBottom: 12, fontSize: 16 }}>Trade Analyses</h3>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {analysis.trade_analyses.map((ta, i) => (
                  <div key={i} style={{ background: 'var(--surface-inset)', border: '1px solid var(--divider-soft)', borderRadius: 'var(--radius-md)', padding: '14px 16px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10, flexWrap: 'wrap' }}>
                      <span style={{ fontWeight: 600, fontSize: 15 }}>{ta.ticker}</span>
                      {ta.strategy && <span className="chip accent">{ta.strategy}</span>}
                      <div style={{ display: 'flex', alignItems: 'center', marginLeft: 'auto', fontSize: 13, color: 'var(--text-secondary)', gap: 6 }}>
                        <ConfidenceDot level={ta.match_confidence} />
                        {ta.match_confidence}
                      </div>
                    </div>

                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12, fontSize: 14, lineHeight: 1.5 }}>
                      {ta.entry_reason && (
                        <div>
                          <div style={fieldLabel}>Entry Reason</div>
                          <div>{ta.entry_reason}</div>
                        </div>
                      )}
                      {ta.exit_reason && (
                        <div>
                          <div style={fieldLabel}>Exit Reason</div>
                          <div>{ta.exit_reason}</div>
                        </div>
                      )}
                      {ta.emotional_state && (
                        <div>
                          <div style={fieldLabel}>Emotional State</div>
                          <div>{ta.emotional_state}</div>
                        </div>
                      )}
                      {ta.r_multiple != null && (
                        <div>
                          <div style={fieldLabel}>R Multiple</div>
                          <div className={`num ${ta.r_multiple >= 0 ? 'pos' : 'neg'}`} style={{ fontWeight: 600 }}>
                            {ta.r_multiple > 0 ? '+' : ''}{Number(ta.r_multiple).toFixed(2)}R
                          </div>
                        </div>
                      )}
                      {ta.mistakes && (
                        <div>
                          <div style={fieldLabel}>Mistakes</div>
                          <div className="neg">{ta.mistakes}</div>
                        </div>
                      )}
                    </div>

                    {ta.ai_feedback && (
                      <div className="notice accent" style={{ marginTop: 12, fontSize: 14 }}>
                        {ta.ai_feedback}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function Diary({ accountId }) {
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    setLoading(true);
    const params = {};
    if (accountId != null) params.account_id = accountId;
    diaryApi.list(params)
      .then(r => { setEntries(r.data); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [accountId]);

  const removeEntry = (id) => setEntries(prev => prev.filter(e => e.id !== id));

  // Deleting a day's entries leaves its Edge Trader day note, as the server does.
  const removeDate = (date) => setEntries(prev => prev.filter(e => e.entry_date !== date || isEdgeDayNote(e)));

  // Group by date descending
  const byDate = entries.reduce((acc, e) => {
    (acc[e.entry_date] = acc[e.entry_date] || []).push(e);
    return acc;
  }, {});
  const dates = Object.keys(byDate).sort((a, b) => b.localeCompare(a));

  return (
    <div>
      <PageHeader
        title="Diary"
        subtitle={!loading && entries.length > 0
          ? <><span className="num">{entries.length}</span> {entries.length === 1 ? 'entry' : 'entries'} across <span className="num">{dates.length}</span> {dates.length === 1 ? 'day' : 'days'}</>
          : 'AI readings of your trading notes, newest first'}
      />

      {loading && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {[...Array(3)].map((_, i) => <div key={i} className="skeleton" style={{ height: 100 }} />)}
        </div>
      )}

      {error && (
        <div className="notice neg" role="alert">{error}</div>
      )}

      {!loading && entries.length === 0 && (
        <div className="card" style={{ textAlign: 'center', padding: 48, color: 'var(--text-secondary)' }}>
          <BookOpen size={40} style={{ marginBottom: 16, opacity: 0.6 }} />
          <div style={{ fontSize: 17, fontWeight: 600, marginBottom: 8, color: 'var(--text-primary)' }}>No diary entries yet</div>
          <div style={{ fontSize: 14 }}>Upload a diary screenshot on the Import page, or write a day note in Edge Trader and sync it.</div>
        </div>
      )}

      {dates.map(date => (
        <section key={date} style={{ marginBottom: 'var(--space-6)' }} aria-label={`Diary entries for ${date}`}>
          {/* Date group header */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10, gap: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <h2 className="section-title num">{date}</h2>
              <span className="chip">
                <span className="num">{byDate[date].length}</span>&nbsp;{byDate[date].length === 1 ? 'entry' : 'entries'}
              </span>
            </div>
            {byDate[date].length > 1 && (
              <DeleteButton
                label={`Delete all diary entries for ${date}`}
                onDelete={() => diaryApi.deleteByDate(date, accountId).then(() => removeDate(date))}
                small
              />
            )}
          </div>

          {/* The Edge Trader day note leads the day, then the uploaded diaries */}
          {(() => {
            const notes = byDate[date].filter(isEdgeDayNote);
            return notes.map(entry => (
              <div key={entry.id} style={{ marginBottom: 'var(--space-3)' }}>
                <EdgeDayNote
                  entry={entry}
                  accountLabel={notes.length > 1 ? `Account ${entry.account_id}` : null}
                  actions={<DeleteButton label={`Delete the Edge Trader day note for ${date}`} onDelete={() => diaryApi.delete(entry.id).then(() => removeEntry(entry.id))} />}
                />
              </div>
            ));
          })()}

          {/* Individual entry cards */}
          {byDate[date].filter(entry => !isEdgeDayNote(entry)).map(entry => (
            <DiaryCard
              key={entry.id}
              entry={entry}
              onDeleted={removeEntry}
            />
          ))}
        </section>
      ))}
    </div>
  );
}
