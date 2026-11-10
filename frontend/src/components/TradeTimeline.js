import { describeEvent, eventGroup, ORIGIN_LABEL } from './edgeEvents';

const CANCEL_KINDS = new Set(['stop_cancelled', 'target_cancelled']);

// MM/DD for an event on another day than the trade (overnight holds, options).
const shortDate = (iso) => `${iso.slice(5, 7)}/${iso.slice(8, 10)}`;

/**
 * One trade's stop, target, fill and note events from Edge Trader's Sync, oldest
 * first. `events` come from prepareEvents (they carry their US Eastern time).
 */
const eventKey = (e) => `${e.id ?? e._i}`;

/**
 * Hovering or focusing a row shows that event on the trade chart (onPreview);
 * clicking it, or Enter, keeps it there until clicked again (onPin).
 */
export default function TradeTimeline({ status, events, tradeDate, error, pinned = null, onPreview, onPin }) {
  const count = events.length;

  return (
    <section className="card" aria-labelledby="trade-timeline-title">
      <div className="panel-head">
        <div style={{ minWidth: 0 }}>
          <h2 className="section-title" id="trade-timeline-title">Timeline</h2>
          <div className="section-sub">
            Stops, targets and notes from Edge Trader. Times are US Eastern.
            {onPreview && status === 'ok' && count > 0 && ' Point at an event to see it on the chart; click it to keep it there.'}
          </div>
        </div>
        {status === 'ok' && count > 0 && (
          <span className="chip"><span className="num">{count}</span>&nbsp;{count === 1 ? 'event' : 'events'}</span>
        )}
      </div>

      {status === 'loading' && (
        <div role="status" aria-label="Loading the timeline" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
          {[0, 1, 2].map(i => <div key={i} className="skeleton" style={{ height: 22 }} />)}
        </div>
      )}

      {status === 'error' && (
        <div className="notice neg" role="alert">
          The timeline did not load{error ? ` (${error})` : ''}. Reload the trade to try again.
        </div>
      )}

      {status === 'ok' && count === 0 && (
        <div className="tl-empty">
          No events for this trade yet. When Edge Trader's Sync sends this trade, its stop and target
          changes and your notes show up here and on the chart.
        </div>
      )}

      {status === 'ok' && count > 0 && (
        <ol className="tl-list">
          {events.map((e, i) => {
            const d = describeEvent(e);
            const group = eventGroup(e.kind);
            const prev = events[i - 1];
            const sameTime = prev && prev._et && e._et && prev._et.date === e._et.date && prev._et.time === e._et.time;
            const otherDay = e._et && tradeDate && e._et.date !== tradeDate;
            const timeText = e._et ? `${otherDay ? `${shortDate(e._et.date)} ` : ''}${e._et.time}` : 'n/a';
            const cancelled = CANCEL_KINDS.has(e.kind);
            const linked = !!onPreview;
            const isPinned = linked && pinned != null && eventKey(pinned) === eventKey(e);
            const linkProps = linked ? {
              tabIndex: 0,
              'aria-current': isPinned ? 'true' : undefined,
              title: isPinned ? 'Shown on the chart. Click to release.' : 'Click to keep this on the chart',
              onPointerEnter: () => onPreview(e),
              onPointerLeave: () => onPreview(null),
              onFocus: () => onPreview(e),
              onBlur: () => onPreview(null),
              onClick: () => onPin && onPin(e),
              onKeyDown: (ev) => {
                if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); if (onPin) onPin(e); }
              },
            } : {};
            return (
              <li
                key={e.id ?? i}
                className={`tl-row tl-${group}${cancelled ? ' tl-cancelled' : ''}${linked ? ' tl-linked' : ''}${isPinned ? ' tl-pinned' : ''}`}
                {...linkProps}
              >
                <span className={`tl-time mono${sameTime ? ' tl-time-same' : ''}`}>
                  {sameTime ? <span className="sr-only">{timeText}</span> : timeText}
                </span>
                <span className="tl-mark" aria-hidden="true" />
                <div className="tl-body">
                  <div className="tl-title">
                    {d.lead}
                    {d.price && <> <span className="mono num">{d.price}</span></>}
                    {d.tail && <> {d.tail}</>}
                  </div>
                  {d.detail && <div className={e.kind === 'note' ? 'tl-note-text' : 'tl-detail'}>{d.detail}</div>}
                </div>
                <span className="tl-origin">{ORIGIN_LABEL[e.origin] || ''}</span>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
