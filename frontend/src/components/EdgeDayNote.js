import { NotebookPen } from 'lucide-react';
import { EDGE_SOURCE, etParts } from './edgeEvents';

export const isEdgeDayNote = (entry) => entry?.source === EDGE_SOURCE;

// updated_at is SQLite datetime('now'): UTC with no offset.
function syncedAt(updatedAt) {
  if (!updatedAt) return null;
  const iso = /[zZ]|[+-]\d\d:?\d\d$/.test(updatedAt) ? updatedAt : `${updatedAt.replace(' ', 'T')}Z`;
  const p = etParts(iso);
  return p ? `${p.date.slice(5, 7)}/${p.date.slice(8, 10)} ${p.time.slice(0, 5)} ET` : null;
}

/**
 * The day note written in Edge Trader and synced into the diary: the trader's own
 * words, no image and no AI reading. `actions` sits top right (the Diary page puts
 * its delete button there).
 */
export default function EdgeDayNote({ entry, accountLabel, actions, as: Tag = 'article' }) {
  const synced = syncedAt(entry.updated_at);
  return (
    <Tag className="card edge-note" aria-label={`Day note from Edge Trader${accountLabel ? `, ${accountLabel}` : ''}`}>
      <div className="edge-note-tile" aria-hidden="true">
        <NotebookPen size={18} />
      </div>
      <div className="edge-note-body">
        <div className="edge-note-head">
          <span className="edge-note-label">Day note</span>
          <span className="chip accent">From Edge Trader</span>
          {accountLabel && <span className="edge-note-meta">{accountLabel}</span>}
          {synced && <span className="edge-note-meta">Synced <span className="num mono">{synced}</span></span>}
          {actions && <span className="edge-note-actions">{actions}</span>}
        </div>
        <p className="edge-note-text">{entry.raw_text}</p>
      </div>
    </Tag>
  );
}
