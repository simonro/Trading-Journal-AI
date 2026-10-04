import { useState, useRef, useMemo, useCallback, useId } from 'react';

/* ── formatting ─────────────────────────────────────────────────────────── */

export const n0 = (v) => Math.abs(Math.round(Number(v) || 0)).toLocaleString('en-US');
export const money = (v) => {
  const n = Number(v) || 0;
  return `${n < 0 ? '−$' : '+$'}${n0(n)}`;
};
export const money2 = (v) => {
  const n = Number(v) || 0;
  return `${n < 0 ? '−$' : '+$'}${Math.abs(n).toFixed(2)}`;
};
export const moneyK = (v) => {
  const n = Number(v) || 0;
  const a = Math.abs(n);
  const s = n < 0 ? '−$' : '+$';
  if (a >= 1000) {
    const k = a / 1000;
    return `${s}${k % 1 === 0 ? k.toFixed(0) : k.toFixed(1)}K`;
  }
  return `${s}${Math.round(a)}`;
};
export const tone = (v) => (Number(v) > 0 ? 'v3-pos' : Number(v) < 0 ? 'v3-neg' : 'v3-flat');
export const pct = (v, d = 1) => `${(Number(v) || 0).toFixed(d)}%`;

const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
export const MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December'];
export const shortDate = (d) => {
  if (!d) return '';
  const p = String(d).split('-');
  return `${Number(p[2])} ${MON[Number(p[1]) - 1]}`;
};

/* ── the measures line ──────────────────────────────────────────────────── */

export function Measures({ items, className = '' }) {
  return (
    <div className={`v3-measures ${className}`.trim()}>
      {items.map((m, i) => (
        <div className={`v3-m${m.met ? ' met' : ''}`} key={i}>
          <div className="v3-lab">{m.label}</div>
          <div className="v3-m-val" style={m.amber ? { color: 'var(--caution)' } : m.tone ? { color: `var(--result-${m.tone})` } : undefined}>
            {m.value}
          </div>
          {m.goalPct != null && (
            <div className="v3-track">
              <div className="v3-track-rail" />
              <div className={`v3-track-fill${m.met ? ' met' : ''}`} style={{ '--f': m.fill }} />
              <div className="v3-track-goal" style={{ '--g': `${m.goalPct}%` }} data-g={`goal ${m.goal}`} />
            </div>
          )}
          {m.read && <div className="v3-m-read">{m.read}</div>}
        </div>
      ))}
    </div>
  );
}

/* ── bar + value on one row ─────────────────────────────────────────────── */

export function BarRow({ frac, label, negative }) {
  const w = Math.max(0, Math.min(1, Number(frac) || 0));
  return (
    <div className="v3-barrow">
      <div className="v3-bar">
        <div className="v3-bar-rail" />
        <div className={`v3-bar-fill${negative ? ' neg' : ''}`} style={{ '--w': w }} />
      </div>
      {label != null && <span className="v3-bar-val">{label}</span>}
    </div>
  );
}

/* ── grade, with its reason reachable by hover AND keyboard ─────────────── */

export function Grade({ grade, reason }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  if (!grade || grade === '-') return <span className="v3-flat">&mdash;</span>;
  const cls = /^A/.test(grade) ? 'v3-g-a' : grade === 'F' ? 'v3-g-f' : 'v3-g-b';
  return (
    <span
      className="v3-gwrap"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        className={`v3-grade ${cls}`}
        aria-describedby={open ? id : undefined}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={(e) => { e.stopPropagation(); setOpen((v) => !v); }}
      >
        {grade}
      </button>
      {open && reason && (
        <span className="v3-gtip" id={id} role="tooltip">
          <b>Grade {grade}</b>{reason}
        </span>
      )}
    </span>
  );
}

/* ── tabs ───────────────────────────────────────────────────────────────── */

export function Tabs({ tabs, active, onChange, label }) {
  const onKey = (e) => {
    const i = tabs.findIndex((t) => t.id === active);
    if (e.key === 'ArrowRight') onChange(tabs[(i + 1) % tabs.length].id);
    if (e.key === 'ArrowLeft') onChange(tabs[(i - 1 + tabs.length) % tabs.length].id);
  };
  return (
    <div className="v3-tabs" role="tablist" aria-label={label} onKeyDown={onKey}>
      {tabs.map((t) => (
        <button
          key={t.id}
          type="button"
          role="tab"
          className="v3-tab"
          aria-selected={t.id === active}
          tabIndex={t.id === active ? 0 : -1}
          onClick={() => onChange(t.id)}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function Seg({ options, value, onChange, label }) {
  return (
    <div className="v3-seg" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.id} type="button" aria-pressed={o.id === value} onClick={() => onChange(o.id)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

/* ── the live equity curve ──────────────────────────────────────────────────
   Cumulative net P&L by trading day, with drawdown from the running peak
   shaded. Hovering anywhere reports that day's balance and result.          */

export function EquityCurve({ days, height = 190, onPick }) {
  const [hover, setHover] = useState(null);
  const box = useRef(null);

  const pts = useMemo(() => {
    let cum = 0; let pk = -Infinity;
    return (days || []).map((d) => {
      cum += Number(d.net_pnl) || 0;
      // the API sends its own running total; trust it when present
      const running = d.cumulative != null ? Number(d.cumulative) : cum;
      pk = Math.max(pk, running);
      return { date: d.date, day: Number(d.net_pnl) || 0, cum: running, peak: pk };
    });
  }, [days]);

  const W = 1000; const H = 260; const P = 8;
  const geom = useMemo(() => {
    if (pts.length < 2) return null;
    const lo = Math.min(0, ...pts.map((p) => p.cum));
    const hi = Math.max(...pts.map((p) => p.peak));
    const X = (i) => P + (i / (pts.length - 1)) * (W - P * 2);
    const Y = (v) => H - P - ((v - lo) / ((hi - lo) || 1)) * (H - P * 2);
    const line = pts.map((p, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)} ${Y(p.cum).toFixed(1)}`).join(' ');
    const pkl = pts.map((p, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)} ${Y(p.peak).toFixed(1)}`).join(' ');
    const back = pts.slice().reverse().map((p, j) => `L${X(pts.length - 1 - j).toFixed(1)} ${Y(p.cum).toFixed(1)}`).join(' ');
    return {
      X, Y, line,
      dd: `${pkl} ${back} Z`,
      area: `${line} L${X(pts.length - 1).toFixed(1)} ${H} L${X(0).toFixed(1)} ${H} Z`,
    };
  }, [pts]);

  const move = useCallback((e) => {
    if (!box.current || pts.length < 2) return;
    const r = box.current.getBoundingClientRect();
    const f = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
    setHover(Math.round(f * (pts.length - 1)));
  }, [pts.length]);

  if (!geom) return <div className="v3-empty">Not enough sessions to draw a curve yet.</div>;

  const h = hover == null ? null : pts[hover];
  const leftPct = hover == null ? 0 : (geom.X(hover) / W) * 100;
  const dotPct = h == null ? 0 : (geom.Y(h.cum) / H) * 100;
  const flip = leftPct > 62;

  return (
    <div
      className="v3-curve"
      ref={box}
      onPointerMove={move}
      onPointerLeave={() => setHover(null)}
      onClick={() => { if (h && onPick) onPick(h.date); }}
    >
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
        <defs>
          <linearGradient id="v3fade" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--result-pos)" stopOpacity="0.22" />
            <stop offset="100%" stopColor="var(--result-pos)" stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={geom.area} fill="url(#v3fade)" />
        <path d={geom.dd} fill="var(--result-neg)" fillOpacity="0.13" />
        <path d={geom.line} fill="none" stroke="var(--result-pos)" strokeWidth="1.6"
          vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
      </svg>
      {h && (
        <>
          <div className="v3-cursor" style={{ left: `${leftPct}%`, '--dot': `${dotPct}%` }} />
          <div
            className="v3-tipbox"
            style={{
              left: flip ? undefined : `calc(${leftPct}% + 14px)`,
              right: flip ? `calc(${100 - leftPct}% + 14px)` : undefined,
              top: 8,
            }}
          >
            <div className="d">{shortDate(h.date)}</div>
            <div className={`v v3-${h.cum >= 0 ? 'pos' : 'neg'}`}>{money(h.cum)}</div>
            <div className="r">
              <span className={tone(h.day)}>{money(h.day)}</span>
              <span style={{ color: 'var(--text-tertiary)' }}> that day</span>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

/* ── the session strip ──────────────────────────────────────────────────────
   Every trading day as one bar. Bars are anchored to the midline absolutely:
   gains grow up from it, losses grow down. An earlier version used flex
   align-self, which controls the horizontal axis in a column container and
   silently rendered gains and losses on the wrong sides.                     */

export function SessionStrip({ days, onPick, onHover }) {
  const peak = useMemo(
    () => Math.max(1, ...(days || []).map((d) => Math.abs(Number(d.net_pnl) || 0))),
    [days],
  );
  const { best, worst } = useMemo(() => {
    let b = 0; let w = 0;
    (days || []).forEach((d, i) => {
      if ((Number(d.net_pnl) || 0) > (Number(days[b].net_pnl) || 0)) b = i;
      if ((Number(d.net_pnl) || 0) < (Number(days[w].net_pnl) || 0)) w = i;
    });
    return { best: b, worst: w };
  }, [days]);

  if (!days || !days.length) return <div className="v3-empty">No sessions in this range.</div>;

  return (
    <div
      className="v3-strip"
      role="group"
      aria-label="Every session in the period"
      onPointerLeave={() => onHover && onHover(null)}
    >
      <div className="v3-midline" />
      {days.map((d, i) => {
        const v = Number(d.net_pnl) || 0;
        const up = v >= 0;
        const h = Math.max(2, Math.round((Math.abs(v) / peak) * 36));
        return (
          <button
            key={d.date || i}
            type="button"
            className={`v3-sess ${up ? 'up' : 'down'}`}
            aria-label={`${shortDate(d.date)}: ${money(v)}`}
            onPointerEnter={() => onHover && onHover(d)}
            onFocus={() => onHover && onHover(d)}
            onClick={() => onPick && onPick(d.date)}
          >
            <i style={{ height: h, '--bar': up ? 'var(--result-pos)' : 'var(--result-neg)' }} />
          </button>
        );
      })}
      {[best, worst].map((idx, k) => {
        const at = (idx / days.length) * 100;
        return (
          <span
            key={k}
            className={`v3-peak${k ? ' low' : ''}`}
            style={{ left: `${at}%`, transform: at > 70 ? 'translateX(-100%)' : undefined }}
          >
            {k ? 'worst ' : 'best '}<b>{shortDate(days[idx].date)}</b>
          </span>
        );
      })}
    </div>
  );
}

/* ── the month grid ─────────────────────────────────────────────────────── */

export function MonthGrid({ year, month, byDay, today, onPick }) {
  const dim = new Date(year, month, 0).getDate();
  const firstDow = new Date(year, month - 1, 1).getDay();
  const cells = [];
  for (let i = 1; i < (firstDow === 0 ? 6 : firstDow); i += 1) cells.push(null);
  for (let d = 1; d <= dim; d += 1) {
    const w = new Date(year, month - 1, d).getDay();
    if (w === 0 || w === 6) continue;
    cells.push(d);
  }
  const big = Math.max(1, ...Object.values(byDay || {}).map((v) => Math.abs(v.net_pnl || 0)));
  const pad = (x) => String(x).padStart(2, '0');
  const rows = [];
  for (let i = 0; i < cells.length; i += 5) rows.push(cells.slice(i, i + 5));

  return (
    <div className="v3-cal">
      {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Week'].map((d) => (
        <div className="v3-dow" key={d}>{d}</div>
      ))}
      {rows.map((row, ri) => {
        const full = [...row];
        while (full.length < 5) full.push(null);
        let wt = 0; let wd = 0;
        const cellNodes = full.map((d, ci) => {
          if (d == null) return <div className="v3-day" data-empty="" key={`e${ri}-${ci}`} />;
          const key = `${year}-${pad(month)}-${pad(d)}`;
          const v = byDay?.[key];
          const isToday = key === today;
          if (!v) {
            return (
              <div className="v3-day" data-empty="" data-today={isToday ? '' : undefined} key={key}>
                <span className="d">{d}</span>
              </div>
            );
          }
          wt += v.net_pnl; wd += 1;
          const ti = (0.05 + (Math.abs(v.net_pnl) / big) * 0.16).toFixed(3);
          return (
            <button
              type="button"
              className="v3-day"
              key={key}
              data-today={isToday ? '' : undefined}
              style={{ '--tint': v.net_pnl >= 0 ? 'var(--result-pos)' : 'var(--result-neg)', '--ti': ti }}
              aria-label={`${d} ${MONTH_NAMES[month - 1]}, ${money(v.net_pnl)}, ${v.trade_count ?? v.trades ?? 0} trades`}
              onClick={() => onPick && onPick(key)}
            >
              <span className="d">{d}</span>
              <span className={`v ${tone(v.net_pnl)}`}>{moneyK(v.net_pnl)}</span>
              <span className="c">
                {(v.trade_count ?? v.trades ?? 0)} trade{(v.trade_count ?? v.trades) === 1 ? '' : 's'}
                {v.win_rate != null && <span className="v3-flat"> &middot; {Number(v.win_rate).toFixed(0)}%</span>}
              </span>
            </button>
          );
        });
        return (
          <div style={{ display: 'contents' }} key={`r${ri}`}>
            {cellNodes}
            <div className="v3-wk">
              <span className="l">Week {ri + 1}</span>
              <span className={`v ${wd ? tone(wt) : 'v3-flat'}`}>{wd ? moneyK(wt) : ''}</span>
              <span className="c">{wd ? `${wd} session${wd === 1 ? '' : 's'}` : 'no sessions'}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
