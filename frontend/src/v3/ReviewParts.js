/* Review page pieces in the V3 language. Presentation only. */
import { useState, useMemo } from 'react';
import { Measures, Tabs, Grade, money, money2, tone } from './parts';
import { sideInfo, sideLabel } from '../sideLabel';

/* the clock every trading session runs on */
const OPEN = 9.5;
const CLOSE = 16;

export function tradeTime(t) {
  const execs = t.executions || [];
  const raw = execs.length ? (execs[0].time || execs[0].datetime || '') : (t.open_time || t.time || '');
  const m = String(raw).match(/(\d{1,2}):(\d{2})/);
  if (!m) return null;
  return Number(m[1]) + Number(m[2]) / 60;
}

/* ── the day, as one picture ────────────────────────────────────────────────
   The session's running P&L from the open to the close, with every trade
   marked on the curve at the time it was entered. Replaces having a separate
   equity line and a separate timeline showing the same session twice.      */
export function DayCurve({ trades, onPick }) {
  const [hover, setHover] = useState(null);

  const marks = useMemo(() => {
    const withTime = (trades || [])
      .map((t) => ({ t, at: tradeTime(t) }))
      .filter((x) => x.at != null)
      .sort((a, b) => a.at - b.at);
    let cum = 0;
    return withTime.map(({ t, at }) => {
      cum += Number(t.net_pnl) || 0;
      return { trade: t, at, pnl: Number(t.net_pnl) || 0, cum };
    });
  }, [trades]);

  const W = 1000;
  const H = 210;
  const PAD = 26;

  const geom = useMemo(() => {
    if (!marks.length) return null;
    const vals = marks.map((m) => m.cum).concat([0]);
    const lo = Math.min(...vals);
    const hi = Math.max(...vals);
    const span = (hi - lo) || 1;
    const X = (hourFrac) => ((hourFrac - OPEN) / (CLOSE - OPEN)) * W;
    const Y = (v) => H - PAD - ((v - lo) / span) * (H - PAD * 2);
    // a step line: the balance holds until the next trade closes it
    let d = `M0 ${Y(0).toFixed(1)}`;
    let prev = 0;
    marks.forEach((m) => {
      d += ` L${X(m.at).toFixed(1)} ${Y(prev).toFixed(1)} L${X(m.at).toFixed(1)} ${Y(m.cum).toFixed(1)}`;
      prev = m.cum;
    });
    d += ` L${W} ${Y(prev).toFixed(1)}`;
    // the fill closes on the zero line, so it shades only the distance from flat
    const area = `${d} L${W} ${Y(0).toFixed(1)} L0 ${Y(0).toFixed(1)} Z`;
    return { X, Y, d, area, zero: Y(0), final: prev };
  }, [marks]);

  // the day's extremes: best and worst running P&L, and the deepest give-back from a high
  const extremes = useMemo(() => {
    if (!marks.length) return null;
    let high = { v: 0, at: null };
    let low = { v: 0, at: null };
    let runPeak = 0;
    let dd = 0;
    marks.forEach((m) => {
      if (m.cum > high.v) high = { v: m.cum, at: m.at };
      if (m.cum < low.v) low = { v: m.cum, at: m.at };
      runPeak = Math.max(runPeak, m.cum);
      dd = Math.max(dd, runPeak - m.cum);
    });
    return { high, low, dd, close: marks[marks.length - 1].cum };
  }, [marks]);

  if (!geom) return <div className="v3-empty">No timed executions on this day, so the session cannot be drawn.</div>;

  const peak = Math.max(1, ...marks.map((m) => Math.abs(m.pnl)));
  // round to whole minutes first, so 9:59.6 reads 10:00 and never 9:60
  const clock = (at) => {
    if (at == null) return '';
    const m = Math.round(at * 60);
    return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')}`;
  };
  const { high, low, dd, close } = extremes;
  // a level line only earns its place when the day actually went there
  const levels = [
    high.at != null && { key: 'hi', v: high.v, cls: 'v3-pos', label: `High ${money(high.v)}`, at: high.at },
    low.at != null && { key: 'lo', v: low.v, cls: 'v3-neg', label: `Low ${money(low.v)}`, at: low.at },
  ].filter(Boolean);

  return (
    <>
    <div className="v3-daystats">
      <span>High <b className={high.v > 0 ? 'v3-pos' : ''}>{high.at != null ? money(high.v) : '$0'}</b>{high.at != null && <i>{clock(high.at)}</i>}</span>
      <span>Low <b className={low.v < 0 ? 'v3-neg' : ''}>{low.at != null ? money(low.v) : '$0'}</b>{low.at != null && <i>{clock(low.at)}</i>}</span>
      <span>Max drawdown <b className={dd > 0 ? 'v3-neg' : ''}>{dd > 0 ? money(-dd) : '$0'}</b></span>
      <span>Close <b className={close > 0 ? 'v3-pos' : close < 0 ? 'v3-neg' : ''}>{money(close)}</b></span>
    </div>
    <div className="v3-session" onPointerLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
        {/* green while the day is up, red while it is down: one curve, clipped at the zero line */}
        <defs>
          <clipPath id="v3day-above"><rect x="0" y="0" width={W} height={geom.zero} /></clipPath>
          <clipPath id="v3day-below"><rect x="0" y={geom.zero} width={W} height={Math.max(0, H - geom.zero)} /></clipPath>
          <linearGradient id="v3day-pos" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="0" y2={geom.zero}>
            <stop offset="0%" stopColor="var(--result-pos)" stopOpacity="0.22" />
            <stop offset="100%" stopColor="var(--result-pos)" stopOpacity="0.02" />
          </linearGradient>
          <linearGradient id="v3day-neg" gradientUnits="userSpaceOnUse" x1="0" y1={geom.zero} x2="0" y2={H}>
            <stop offset="0%" stopColor="var(--result-neg)" stopOpacity="0.02" />
            <stop offset="100%" stopColor="var(--result-neg)" stopOpacity="0.22" />
          </linearGradient>
        </defs>
        {/* the hours */}
        {[10, 11, 12, 13, 14, 15].map((h) => (
          <g key={h}>
            <line x1={geom.X(h)} y1="0" x2={geom.X(h)} y2={H} stroke="var(--divider-soft)" strokeWidth="1"
              vectorEffect="non-scaling-stroke" />
            <text className="v3-tl-lab" x={geom.X(h) + 5} y="12">{h}:00</text>
          </g>
        ))}
        <line x1="0" y1={geom.zero} x2={W} y2={geom.zero} stroke="var(--divider)" strokeWidth="1"
          vectorEffect="non-scaling-stroke" />
        {levels.map((l) => (
          <line key={l.key} x1="0" y1={geom.Y(l.v)} x2={W} y2={geom.Y(l.v)}
            stroke={l.key === 'hi' ? 'var(--result-pos)' : 'var(--result-neg)'} strokeOpacity="0.55"
            strokeWidth="1" strokeDasharray="4 4" vectorEffect="non-scaling-stroke" />
        ))}
        <path d={geom.area} fill="url(#v3day-pos)" clipPath="url(#v3day-above)" />
        <path d={geom.area} fill="url(#v3day-neg)" clipPath="url(#v3day-below)" />
        <path d={geom.d} fill="none" stroke="var(--result-pos)" clipPath="url(#v3day-above)"
          strokeWidth="1.8" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
        <path d={geom.d} fill="none" stroke="var(--result-neg)" clipPath="url(#v3day-below)"
          strokeWidth="1.8" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
      </svg>

      {/* the high and low, labelled at the left edge where the curve still sits on flat */}
      {levels.map((l) => (
        <div key={l.key} className={`v3-daylevel ${l.cls} ${l.key}`} style={{ top: `${(geom.Y(l.v) / H) * 100}%` }}>
          {l.label} <i>{clock(l.at)}</i>
        </div>
      ))}
      <div className={`v3-dayclose ${tone(close)}`} style={{ top: `${(geom.Y(close) / H) * 100}%` }}>
        {money(close)}
      </div>

      {/* one mark per trade, on the curve at its entry time */}
      {marks.map((m, i) => {
        const leftPct = (geom.X(m.at) / W) * 100;
        const topPct = (geom.Y(m.cum) / H) * 100;
        const size = 5 + (Math.abs(m.pnl) / peak) * 7;
        return (
          <button
            key={i}
            type="button"
            className="v3-daymark"
            style={{
              left: `${leftPct}%`, top: `${topPct}%`, width: size, height: size,
              background: m.pnl >= 0 ? 'var(--result-pos)' : 'var(--result-neg)',
            }}
            aria-label={`${m.trade.ticker} at ${m.at ? `${Math.floor(m.at)}:${String(Math.round((m.at % 1) * 60)).padStart(2, '0')}` : ''}, ${money2(m.pnl)}`}
            onPointerEnter={() => setHover(i)}
            onFocus={() => setHover(i)}
            onClick={() => onPick && onPick(m.trade)}
          />
        );
      })}

      {hover != null && (
        <div
          className="v3-tipbox"
          style={{
            left: (geom.X(marks[hover].at) / W) * 100 > 62 ? undefined : `calc(${(geom.X(marks[hover].at) / W) * 100}% + 14px)`,
            right: (geom.X(marks[hover].at) / W) * 100 > 62 ? `calc(${100 - (geom.X(marks[hover].at) / W) * 100}% + 14px)` : undefined,
            top: 8,
          }}
        >
          <div className="d">{marks[hover].trade.ticker}</div>
          <div className={`v ${tone(marks[hover].pnl)}`}>{money2(marks[hover].pnl)}</div>
          <div className="r">running {money(marks[hover].cum)}</div>
        </div>
      )}
    </div>
    </>
  );
}

/* ── the day's measures ───────────────────────────────────────────────────
   Each cell reads the day's own KPIs and sets them against all time in the
   same cell, instead of a separate comparison row. Values that cannot be
   measured on a day (a profit factor with no losers) say so in words, and are
   never compared as if they were zero. */
function Delta({ day, all, digits = 1, unit = '', prefix = '' }) {
  if (day == null || all == null || !Number.isFinite(Number(day)) || !Number.isFinite(Number(all))) return null;
  const d = Number(day) - Number(all);
  if (Math.abs(d) < 10 ** -digits / 2) return <span className="v3-flat"> level</span>;
  const cls = d > 0 ? 'v3-pos' : 'v3-neg';
  const abs = Math.abs(d).toFixed(digits);
  return <span className={cls}> {d > 0 ? '+' : '−'}{prefix}{abs}{unit}</span>;
}

export function DayMeasures({ kpis, trades, summary, allTime }) {
  const k = kpis || {};
  const a = allTime || null;
  const net = Number(k.total_net_pnl || 0);
  const n = k.total_trades ?? (trades || []).length;

  // how far the day came off its own high water mark
  const { peak, given } = useMemo(() => {
    const withTime = (trades || [])
      .map((t) => ({ at: tradeTime(t), p: Number(t.net_pnl) || 0 }))
      .filter((x) => x.at != null)
      .sort((x, y) => x.at - y.at);
    let cum = 0; let hi = 0;
    withTime.forEach((x) => { cum += x.p; hi = Math.max(hi, cum); });
    return { peak: hi, given: Math.max(0, hi - cum) };
  }, [trades]);

  const breaks = (summary?.mistakes || []).length;
  const wins = (trades || []).filter((t) => (t.net_pnl || 0) > 0).length;
  const losses = (trades || []).filter((t) => (t.net_pnl || 0) < 0).length;

  const avgDay = a && a.trading_days ? Number(a.total_net_pnl || 0) / a.trading_days : null;
  const pf = k.profit_factor == null ? null : Number(k.profit_factor);
  const noLosers = pf == null && wins > 0;
  const eff = k.exit_efficiency == null ? null : Number(k.exit_efficiency);
  const allEff = a && a.exit_efficiency != null ? Number(a.exit_efficiency) : null;
  const perTrade = n ? net / n : null;
  const allExp = a && a.expectancy != null ? Number(a.expectancy) : null;

  const allLine = (label, value) => (a && value != null ? <>All-time {label}{value}</> : null);

  return (
    <Measures
      className="v3-measures-4"
      items={[
        {
          label: 'Net for the day',
          value: money2(net),
          met: net >= 0,
          tone: net < 0 ? 'neg' : undefined,
          read: avgDay == null
            ? `${wins} green, ${losses} red`
            : <>Your average day {money(avgDay)}<Delta day={net} all={avgDay} digits={0} prefix="$" /></>,
        },
        {
          label: 'Win rate',
          value: n ? `${Number(k.win_rate || 0).toFixed(0)}%` : '—',
          read: <>
            {wins} of {n} won.{' '}
            {allLine('', a ? `${Number(a.win_rate || 0).toFixed(1)}%` : null)}
            {a && n ? <Delta day={k.win_rate} all={a.win_rate} unit="pp" /> : null}
          </>,
        },
        {
          label: 'Profit factor',
          value: noLosers ? 'No losers' : pf == null ? '—' : pf.toFixed(2),
          met: noLosers || (pf != null && pf >= 1),
          tone: pf != null && pf < 1 ? 'neg' : undefined,
          read: <>
            {noLosers ? 'Nothing to divide by today. ' : ''}
            {allLine('', a && a.profit_factor != null ? Number(a.profit_factor).toFixed(2) : null)}
            {pf != null && a && a.profit_factor != null ? <Delta day={pf} all={a.profit_factor} digits={2} unit="x" /> : null}
          </>,
        },
        {
          label: 'Avg win',
          value: k.avg_win ? money(k.avg_win) : '—',
          read: <>
            {allLine('', a && a.avg_win ? money(a.avg_win) : null)}
            {k.avg_win && a && a.avg_win ? <Delta day={k.avg_win} all={a.avg_win} digits={0} prefix="$" /> : null}
          </>,
        },
        {
          label: 'Avg per trade',
          value: perTrade == null ? '—' : money2(perTrade),
          tone: perTrade != null && perTrade < 0 ? 'neg' : undefined,
          read: <>
            {allExp != null ? <>All-time expectancy {money2(allExp)}</> : 'Net divided by trades'}
            {perTrade != null && allExp != null ? <Delta day={perTrade} all={allExp} digits={0} prefix="$" /> : null}
          </>,
        },
        {
          label: 'Exit efficiency',
          value: eff == null ? '—' : `${eff.toFixed(0)}%`,
          amber: eff != null && allEff != null && eff < allEff,
          read: eff == null
            ? 'No excursion data for these trades'
            : <>
              Share of the move you kept.{' '}
              {allEff != null ? <>All-time {allEff.toFixed(0)}%</> : null}
              {allEff != null ? <Delta day={eff} all={allEff} digits={0} unit="pp" /> : null}
            </>,
        },
        {
          label: 'Given back',
          value: given > 0 ? money(-given) : '$0',
          amber: given > 0,
          read: peak > 0
            ? `From a session high of ${money(peak)}`
            : 'The session never went green',
        },
        {
          label: 'Rule breaks',
          value: String(breaks),
          amber: breaks > 0,
          read: breaks ? 'Flagged in the review below' : 'Nothing flagged',
        },
      ]}
    />
  );
}

/* ── coaching: the report, with the lists behind tabs ───────────────────── */
export function Coaching({ summary, loading, onRegenerate }) {
  const [tab, setTab] = useState('strengths');
  if (loading) return <div className="v3-empty">Reading the session…</div>;
  if (!summary) return <div className="v3-empty">No review for this day yet.</div>;

  const lists = {
    strengths: summary.strengths || [],
    mistakes: summary.mistakes || [],
    focus: summary.coaching || [],
    patterns: summary.patterns || [],
  };
  const tabs = [
    { id: 'strengths', label: 'Strengths' },
    { id: 'mistakes', label: 'Mistakes' },
    { id: 'focus', label: 'Tomorrow’s focus' },
    { id: 'patterns', label: 'Patterns' },
  ];
  const rows = lists[tab];

  return (
    <>
      <div className="v3-sec-head">
        <div>
          <h2 className="v3-h">Coaching</h2>
          <p className="v3-h-sub">Written against your trades and your diary together, and graded on process</p>
        </div>
        <div className="v3-acts">
          {onRegenerate && (
            <button type="button" className="btn btn-secondary btn-sm" onClick={onRegenerate}>Regenerate</button>
          )}
        </div>
      </div>

      <div className="v3-cols">
        {summary.narrative && <p className="v3-narr">{summary.narrative}</p>}
        {summary.mental_game && <p className="v3-narr v3-narr-quiet">{summary.mental_game}</p>}
      </div>

      <div style={{ marginTop: 22 }}>
        <Tabs tabs={tabs} active={tab} onChange={setTab} label="Review detail" />
        {!rows.length ? (
          <div className="v3-empty">
            {tab === 'mistakes' ? 'Nothing flagged on this day.' : 'Nothing recorded here.'}
          </div>
        ) : tab === 'patterns' ? (
          <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap' }}>
            {rows.map((p, i) => <span className="v3-chip" key={i} style={{ whiteSpace: 'normal' }}>{p}</span>)}
          </div>
        ) : (
          <ul className="v3-list v3-cols">
            {rows.map((r, i) => (
              <li key={i} className={tab === 'mistakes' ? 'bad' : tab === 'focus' ? 'next' : 'good'}>{r}</li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}

/* ── the day's trades, with the grade carrying its reason ───────────────── */
export function DayTrades({ trades, gradeMap, loading, onOpen }) {
  if (loading) return <div className="v3-empty">Loading…</div>;
  if (!trades?.length) return <div className="v3-empty">No trades on this day.</div>;
  return (
    <div className="v3-scroll">
      <table className="v3-t">
        <thead>
          <tr>
            <th>Ticker</th>
            <th className="v3-hide-s">Side</th>
            <th className="v3-hide-s">Strategy</th>
            <th>Grade</th>
            <th className="r v3-hide-s">MFE / MAE</th>
            <th className="r v3-hide-s">Captured</th>
            <th className="r">R</th>
            <th className="r">P&amp;L</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t) => {
            const pnl = Number(t.net_pnl || 0);
            const g = gradeMap?.[t.trade_group];
            const open = () => onOpen && onOpen(t, trades);
            return (
              <tr
                key={t.id}
                className="clickable"
                tabIndex={0}
                onClick={open}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } }}
                aria-label={`Open ${t.ticker} trade`}
              >
                <td>
                  <div className="v3-tick">{t.ticker}</div>
                  <div className="v3-read">{t.instrument_type}</div>
                </td>
                <td className="v3-hide-s v3-side" title={sideInfo(t).title}>
                  {sideLabel(t)}
                </td>
                <td className="v3-hide-s v3-read">{t.strategy || 'not set'}</td>
                <td onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
                  {g ? <Grade grade={g.grade} reason={g.one_line} /> : <span className="v3-flat">&mdash;</span>}
                </td>
                <td className="r v3-mono v3-hide-s v3-read">
                  {t.mfe_pct == null && t.mae_pct == null ? '—' : (
                    <>
                      <span className="v3-pos">{t.mfe_pct == null ? '—' : `+${Number(t.mfe_pct).toFixed(1)}%`}</span>
                      <span className="v3-flat"> / </span>
                      <span className="v3-neg">{t.mae_pct == null ? '—' : `${Number(t.mae_pct).toFixed(1)}%`}</span>
                    </>
                  )}
                </td>
                <td className={`r v3-mono v3-hide-s ${t.exit_efficiency == null ? 'v3-flat' : t.exit_efficiency < 0 ? 'v3-neg' : t.exit_efficiency >= 50 ? 'v3-pos' : ''}`}>
                  {t.exit_efficiency == null ? '—' : `${Number(t.exit_efficiency).toFixed(0)}%`}
                </td>
                <td className={`r v3-mono ${t.r_multiple != null ? tone(t.r_multiple) : 'v3-flat'}`}>
                  {t.r_multiple != null ? `${t.r_multiple > 0 ? '+' : ''}${Number(t.r_multiple).toFixed(2)}R` : '—'}
                </td>
                <td className={`r v3-mono ${tone(pnl)}`} style={{ fontWeight: 600 }}>{money2(pnl)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
