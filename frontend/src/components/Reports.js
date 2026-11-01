import { useState, useEffect } from 'react';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Cell, ReferenceLine, AreaChart, Area,
} from 'recharts';
import { reportsApi, edgeReportApi, leaksApi } from '../api';
import DateRangePicker from './DateRangePicker';
import { RMultipleDist, EmotionTable, MistakeFreq, HoldTime } from './Edge';
import { PageHeader, PanelHead } from './ui';
import { Measures, Seg } from '../v3/parts';
import Leaks from './Leaks';

const fmt$ = (v) =>
  `${v < 0 ? '-' : ''}$${Math.abs(Number(v || 0)).toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
const signed$ = (v) => `${Number(v) > 0 ? '+' : ''}${fmt$(v)}`;
const tone = (v) => (Number(v) > 0 ? 'pos' : Number(v) < 0 ? 'neg' : '');
const AXIS_TICK = { fontSize: 11, fill: 'var(--text-secondary)' };

/* Rows under this many trades are demoted: a one-trade strategy at 100% and
   an infinite profit factor is noise, and sorting by net puts it on top. */
export const THIN_SAMPLE = 10;

const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'leaks', label: 'Leaks' },
  { id: 'setups', label: 'Setups & Strategy' },
  { id: 'sources-tags', label: 'Sources & Tags' },
  { id: 'timing', label: 'Timing' },
  { id: 'execution', label: 'Execution' },
  { id: 'symbols', label: 'Symbols' },
  { id: 'psychology', label: 'Psychology' },
];

// Strategy and source tags mirror their own fields, so the tag report skips them.
const TAG_TYPE_ORDER = ['mistake', 'execution', 'setup', 'emotion', 'outcome'];
const TAG_TYPE_LABEL = { mistake: 'Mistakes', execution: 'Execution', setup: 'Setup', emotion: 'Emotion', outcome: 'Outcome' };

function Section({ title, hint, children }) {
  return (
    <section className="card">
      <PanelHead title={title} sub={hint} />
      {children}
    </section>
  );
}

function NoData({ msg }) {
  return (
    <div className="empty">
      {msg || 'Not enough data yet.'}
    </div>
  );
}

/* Every /api/reports bucket has the same shape, so one table renders all of them. */
function BucketTable({ rows, labelHead = 'Bucket', sortByPnl = false, max }) {
  if (!rows || !rows.length) return <NoData />;
  let data = sortByPnl ? [...rows].sort((a, b) => b.net_pnl - a.net_pnl) : rows;
  if (max) data = data.slice(0, max);

  const best = Math.max(...data.map(r => Math.abs(r.net_pnl)), 1);

  return (
    <div className="scroll-x" style={{ margin: '0 -24px' }}>
      <table style={{ minWidth: 820 }}>
        <thead>
          <tr>
            <th style={{ paddingLeft: 24 }}>{labelHead}</th>
            <th className="num">Trades</th>
            <th className="num">Win %</th>
            <th className="num">Net P&amp;L</th>
            <th className="num">Avg</th>
            <th className="num">Avg Win</th>
            <th className="num">Avg Loss</th>
            <th className="num">PF</th>
            <th className="num">Exit Eff.</th>
            <th style={{ width: 120, paddingRight: 24 }}><span className="sr-only">Relative size</span></th>
          </tr>
        </thead>
        <tbody>
          {data.map(r => {
            const pos = r.net_pnl >= 0;
            return (
              <tr key={r.key} className={r.trades < THIN_SAMPLE ? 'thin' : undefined}>
                <td style={{ paddingLeft: 24, fontWeight: 600 }}>
                  {r.label}
                  {r.trades < THIN_SAMPLE && (
                    <span className="v3-thin" title={`Fewer than ${THIN_SAMPLE} trades, so treat this row as noise`}>thin</span>
                  )}
                </td>
                <td className="num text-muted">{r.trades}</td>
                <td className="num">{r.win_rate}%</td>
                <td className={`num ${tone(r.net_pnl)}`} style={{ fontWeight: 600 }}>
                  {signed$(r.net_pnl)}
                </td>
                <td className={`num ${tone(r.avg_pnl)}`}>
                  {signed$(r.avg_pnl)}
                </td>
                <td className="num text-muted">{signed$(r.avg_win)}</td>
                <td className="num text-muted">{fmt$(r.avg_loss)}</td>
                <td className="num" style={{ fontWeight: 600 }}>
                  {r.profit_factor == null ? <span className="text-muted" title="No losing trades in this row">&mdash;</span> : r.profit_factor.toFixed(2)}
                </td>
                <td className="num text-muted">
                  {r.exit_efficiency == null ? '-' : `${r.exit_efficiency}%`}
                </td>
                <td style={{ paddingRight: 24 }} aria-hidden="true">
                  <div style={{ background: 'var(--surface-inset)', height: 6, borderRadius: 3, overflow: 'hidden' }}>
                    <div style={{
                      width: `${Math.abs(r.net_pnl) / best * 100}%`, height: '100%',
                      background: pos ? 'var(--result-pos)' : 'var(--result-neg)', borderRadius: 3,
                    }} />
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/* One breakdown, drawn either way. The page holds a single view mode, so the
   switch at the top flips every section at once rather than per section. */
function Breakdown({ view, rows, labelHead, sortByPnl, max, height }) {
  if (!rows || !rows.length) return <NoData />;
  return view === 'table'
    ? <BucketTable rows={rows} labelHead={labelHead} sortByPnl={sortByPnl} max={max} />
    : <BucketBars rows={sortByPnl ? [...rows].sort((a, b) => b.net_pnl - a.net_pnl).slice(0, max || 24) : rows} height={height} />;
}

/* Net P&L bars for an ordered bucket list (timing charts read better than a table). */
function BucketBars({ rows, height = 240 }) {
  if (!rows || !rows.length) return <NoData />;
  const data = rows.map(r => ({ name: r.label, pnl: r.net_pnl, trades: r.trades, wr: r.win_rate }));
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: 8, bottom: 4 }}>
        <CartesianGrid stroke="var(--divider-soft)" vertical={false} />
        <XAxis dataKey="name" tick={AXIS_TICK} axisLine={false} tickLine={false} />
        <YAxis tick={AXIS_TICK} axisLine={false} tickLine={false}
               tickFormatter={(v) => fmt$(v)} width={62} />
        <Tooltip
          cursor={{ fill: 'var(--accent-soft)' }}
          content={({ active, payload, label }) => {
            if (!active || !payload?.length) return null;
            const d = payload[0].payload;
            return (
              <div className="card" style={{ padding: '8px 12px', fontSize: 13 }}>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>{label}</div>
                <div className={`num ${tone(d.pnl)}`} style={{ fontWeight: 600 }}>
                  {signed$(d.pnl)}
                </div>
                <div className="text-muted">{d.trades} trades · {d.wr}% win</div>
              </div>
            );
          }}
        />
        <ReferenceLine y={0} stroke="var(--divider)" />
        <Bar dataKey="pnl" radius={[4, 4, 0, 0]}>
          {data.map((d, i) => (
            <Cell key={i} fill={d.pnl >= 0 ? 'var(--result-pos)' : 'var(--result-neg)'} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

function EquityCurve({ curve }) {
  if (!curve || curve.length < 2) return <NoData />;
  return (
    <ResponsiveContainer width="100%" height={300}>
      <AreaChart data={curve} margin={{ top: 8, right: 8, left: 8, bottom: 4 }}>
        <defs>
          <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--accent-line)" stopOpacity={0.22} />
            <stop offset="100%" stopColor="var(--accent-line)" stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="var(--divider-soft)" vertical={false} />
        <XAxis dataKey="date" tick={AXIS_TICK}
               axisLine={false} tickLine={false} minTickGap={40} />
        <YAxis tick={AXIS_TICK} axisLine={false}
               tickLine={false} tickFormatter={(v) => fmt$(v)} width={68} />
        <Tooltip
          content={({ active, payload, label }) => {
            if (!active || !payload?.length) return null;
            const d = payload[0].payload;
            return (
              <div className="card" style={{ padding: '8px 12px', fontSize: 13 }}>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>{label}</div>
                <div>Equity: <b className="num">{fmt$(d.cumulative)}</b></div>
                <div className={`num ${tone(d.pnl)}`}>
                  Day: {signed$(d.pnl)}
                </div>
                {d.drawdown < 0 && (
                  <div className="num neg">Drawdown: {fmt$(d.drawdown)}</div>
                )}
              </div>
            );
          }}
        />
        <ReferenceLine y={0} stroke="var(--divider)" />
        <Area type="monotone" dataKey="cumulative" stroke="var(--accent-line)" strokeWidth={2}
              fill="url(#eqGrad)" dot={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

function DrawdownCurve({ curve }) {
  if (!curve || curve.length < 2) return <NoData />;
  return (
    <ResponsiveContainer width="100%" height={300}>
      <AreaChart data={curve} margin={{ top: 8, right: 8, left: 8, bottom: 4 }}>
        <defs>
          <linearGradient id="ddGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--result-neg)" stopOpacity={0.05} />
            <stop offset="100%" stopColor="var(--result-neg)" stopOpacity={0.3} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="var(--divider-soft)" vertical={false} />
        <XAxis dataKey="date" tick={AXIS_TICK}
               axisLine={false} tickLine={false} minTickGap={40} />
        <YAxis tick={AXIS_TICK} axisLine={false}
               tickLine={false} tickFormatter={(v) => fmt$(v)} width={68} />
        <Tooltip
          content={({ active, payload, label }) => {
            if (!active || !payload?.length) return null;
            return (
              <div className="card" style={{ padding: '8px 12px', fontSize: 13 }}>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>{label}</div>
                <div className="num neg">{fmt$(payload[0].value)} off peak</div>
              </div>
            );
          }}
        />
        <Area type="monotone" dataKey="drawdown" stroke="var(--result-neg)" strokeWidth={1.5}
              fill="url(#ddGrad)" dot={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

// `initial` restores the tab and range when Back from a trade returns here.
export default function Reports({ accountId, onOpenDetail, initial }) {
  // one view mode for the whole page: you are either scanning or reading numbers
  const [view, setView] = useState('bars');
  const [tab, setTab] = useState(initial?.tab || 'overview');
  const [data, setData] = useState(null);
  const [edge, setEdge] = useState(null);
  const [loading, setLoading] = useState(true);
  const [dateFrom, setDateFrom] = useState(initial?.dateFrom || '');
  const [dateTo, setDateTo] = useState(initial?.dateTo || '');
  const [leaks, setLeaks] = useState(null);

  useEffect(() => {
    setLoading(true);
    const params = {};
    if (accountId != null) params.account_id = accountId;
    if (dateFrom) params.date_from = dateFrom;
    if (dateTo) params.date_to = dateTo;
    Promise.all([
      reportsApi.get(params).then(r => r.data).catch(() => null),
      edgeReportApi.get(params).then(r => r.data).catch(() => null),
    ]).then(([rep, edg]) => {
      setData(rep && rep.has_data ? rep : null);
      setEdge(edg);
      setLoading(false);
    });
  }, [accountId, dateFrom, dateTo]);

  // Leaks load only when their tab is open, and again whenever the filters change.
  const leakParams = {};
  if (accountId != null) leakParams.account_id = accountId;
  if (dateFrom) leakParams.date_from = dateFrom;
  if (dateTo) leakParams.date_to = dateTo;
  useEffect(() => { setLeaks(null); }, [accountId, dateFrom, dateTo]);
  useEffect(() => {
    if (tab !== 'leaks' || leaks) return undefined;
    let live = true;
    leaksApi.get(leakParams)
      .then(r => { if (live) setLeaks(r.data); })
      .catch(() => { if (live) setLeaks({ has_data: false, failed: true }); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, leaks, accountId, dateFrom, dateTo]);

  const s = data?.summary;
  const gap = { display: 'flex', flexDirection: 'column', gap: 20 };

  return (
    <div>
      <PageHeader
        title="Reports"
        subtitle={loading ? 'Loading...' : data
          ? `${data.trade_count.toLocaleString('en-US')} trades across ${s.trading_days} sessions`
          : 'No trades in range'}
        actions={
          <DateRangePicker
            dateFrom={dateFrom}
            dateTo={dateTo}
            onChange={({ dateFrom: f, dateTo: t }) => { setDateFrom(f); setDateTo(t); }}
          />
        }
      />

      <div className="tabs" role="tablist" aria-label="Report sections" style={{ marginBottom: 20 }}>
        {TABS.map(t => (
          <button
            type="button"
            role="tab"
            id={`report-tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls="report-panel"
            tabIndex={tab === t.id ? 0 : -1}
            key={t.id}
            className="tab"
            onClick={() => setTab(t.id)}
            onKeyDown={e => {
              const i = TABS.findIndex(x => x.id === tab);
              if (e.key === 'ArrowRight') { setTab(TABS[(i + 1) % TABS.length].id); }
              if (e.key === 'ArrowLeft') { setTab(TABS[(i - 1 + TABS.length) % TABS.length].id); }
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end', margin: '0 0 18px' }}>
        <Seg
          label="How to show every breakdown"
          value={view}
          onChange={setView}
          options={[{ id: 'bars', label: 'Bars' }, { id: 'table', label: 'Table' }]}
        />
      </div>

      {loading ? (
        <div style={gap}>
          {[1, 2, 3].map(i => <div key={i} className="skeleton" style={{ height: 220 }} />)}
        </div>
      ) : !data ? (
        <div className="card"><NoData msg="Import trades to see reports." /></div>
      ) : (
        <div style={gap} role="tabpanel" id="report-panel" aria-labelledby={`report-tab-${tab}`}>

          {tab === 'overview' && (
            <>
              <Measures
                items={[
                  { label: 'Net P&L', value: signed$(s.net_pnl), met: s.net_pnl >= 0,
                    read: `${s.green_days} green sessions against ${s.red_days} red` },
                  { label: 'Max drawdown', value: fmt$(s.max_drawdown), tone: 'neg',
                    read: `Deepest on ${s.max_drawdown_date}` },
                  { label: 'Green days', value: `${s.green_days} / ${s.trading_days}`,
                    met: s.green_days > s.trading_days / 2,
                    read: `${Math.round(s.green_days / s.trading_days * 100)}% of sessions closed up` },
                  { label: 'Best day', value: signed$(s.best_day), met: true, read: 'Your largest single session' },
                  { label: 'Worst day', value: fmt$(s.worst_day), tone: 'neg', read: 'Your deepest single session' },
                  { label: 'Avg green day', value: signed$(s.avg_green_day), met: true,
                    read: `Against an average red day of ${fmt$(s.avg_red_day)}` },
                  { label: 'Longest streak', value: `${s.longest_win_streak}W`, met: true,
                    read: `Worst run ${s.longest_loss_streak}L` },
                  { label: 'Trades / day', value: String(s.avg_trades_per_day), read: 'Across every session in range' },
                ]}
              />

              <div className="grid-2-1">
                <Section title="Equity Curve" hint="Cumulative net P&L by trading day.">
                  <EquityCurve curve={data.equity_curve} />
                </Section>

                <Section title="Drawdown" hint="Distance below the running equity peak. This is the number a prop firm watches.">
                  <DrawdownCurve curve={data.equity_curve} />
                </Section>
              </div>

              <Section title="Monthly Performance">
                <Breakdown view={view} rows={data.by_month} labelHead="Month" height={260} />
              </Section>
            </>
          )}

          {tab === 'leaks' && (
            !leaks ? (
              <div style={gap}>{[1, 2].map(i => <div key={i} className="skeleton" style={{ height: 180 }} />)}</div>
            ) : leaks.failed ? (
              <div className="notice neg" role="alert">The leaks could not be loaded. Check that the backend is running, then reopen this tab.</div>
            ) : (
              <Leaks
                data={leaks}
                params={leakParams}
                onOpenDetail={(t, list) => onOpenDetail && onOpenDetail(t, list, 'Stats',
                  { page: 'reports', state: { tab: 'leaks', dateFrom, dateTo } })}
              >
                <section className="lk-group">
                  <div className="lk-group-head">
                    <h2 className="v3-h">Trade management</h2>
                    <p className="v3-h-sub">Outcomes, not entry decisions, so they are never ranked as a leak. Short holds include every stop that did its job; Exit Lab shows what other exits would have made.</p>
                  </div>
                  <Breakdown view={view} rows={leaks.management?.by_hold_time} labelHead="Hold" />
                  <div className="lk-group-head">
                    <h2 className="v3-h">Scaled out vs all-or-nothing</h2>
                  </div>
                  <Breakdown view={view} rows={leaks.management?.by_management} labelHead="Management" height={200} />
                </section>
              </Leaks>
            )
          )}

          {tab === 'setups' && (
            <>
              <Section title="By Setup" hint="Playbook setups you tagged on your trades.">
                <Breakdown view={view} rows={data.by_setup} labelHead="Setup" sortByPnl />
              </Section>
              <Section title="By Setup Grade" hint="A++ down to F. A monotonic ladder means the grading is real.">
                <Breakdown view={view} rows={data.by_grade} labelHead="Grade" />
              </Section>
              <Section title="By Strategy" hint="What the diary analysis tagged the trade as.">
                <Breakdown view={view} rows={data.by_strategy} labelHead="Strategy" sortByPnl max={20} />
              </Section>
              <div className="grid-2">
                <Section title="By Instrument">
                  <Breakdown view={view} rows={data.by_instrument} labelHead="Type" sortByPnl />
                </Section>
                <Section title="Long vs Short">
                  <Breakdown view={view} rows={data.by_side} labelHead="Side" sortByPnl />
                </Section>
              </div>
            </>
          )}

          {tab === 'sources-tags' && (
            <>
              <Section title="By Source" hint="Where the idea or alert came from. Each trade has one source.">
                <Breakdown view={view} rows={data.by_source || []} labelHead="Source" sortByPnl />
              </Section>
              {TAG_TYPE_ORDER.filter(t => (data.by_tag || {})[t]?.length).map((t, i) => (
                <Section
                  key={t}
                  title={`Tags: ${TAG_TYPE_LABEL[t]}`}
                  hint={i === 0 ? 'A trade with several tags counts under each of them, so tag rows do not add up to your net P&L.' : undefined}
                >
                  <Breakdown view={view} rows={data.by_tag[t]} labelHead="Tag" sortByPnl />
                </Section>
              ))}
              {!Object.keys(data.by_tag || {}).length && (
                <Section title="Tags"><div className="empty">No tagged trades in this period.</div></Section>
              )}
            </>
          )}

          {tab === 'timing' && (
            <>
              <Section title="By Day of Week">
                <Breakdown view={view} rows={data.by_day_of_week} labelHead="Day" />
              </Section>
              <Section title="By Time of Day" hint="Bucketed on first entry.">
                <Breakdown view={view} rows={data.by_session} labelHead="Entry window" />
              </Section>
              <Section title="By Hold Time" hint="First entry to last exit. Short holds are usually stop-outs and chases.">
                <Breakdown view={view} rows={data.by_hold_time} labelHead="Hold" />
              </Section>
            </>
          )}

          {tab === 'execution' && (
            <>
              <Section title="Scaled Out vs All-or-Nothing"
                       hint="Trades with more than one exit fill vs a single exit.">
                <Breakdown view={view} rows={data.by_management} labelHead="Management" height={200} />
              </Section>
              <div className="grid-2">
                <Section title="R-Multiple Distribution" hint="Realized R per trade, from the diary analysis.">
                  <RMultipleDist data={edge?.r_multiple_dist} />
                </Section>
                <Section title="Hold Time: Winners vs Losers">
                  <HoldTime data={edge?.hold_time} />
                </Section>
              </div>
              <Section title="Exit Efficiency by Setup"
                       hint="Percent of the maximum favorable excursion you actually captured, winners only. 40-60% is a normal band for a discretionary day trader.">
                <Breakdown view={view} rows={data.by_setup} labelHead="Setup" sortByPnl />
              </Section>
            </>
          )}

          {tab === 'symbols' && (
            <Section title="By Symbol" hint="Top 40 by net P&L. Watch for a single name carrying the account.">
              <Breakdown view={view} rows={data.by_symbol} labelHead="Symbol" sortByPnl />
            </Section>
          )}

          {tab === 'psychology' && (
            <>
              <Section title="By Emotional State" hint="Self-reported in the diary. Revenge and frustrated rows are the ones to read.">
                <Breakdown view={view} rows={data.by_emotion} labelHead="Emotion" sortByPnl />
              </Section>
              <Section title="Emotion vs Outcome">
                <EmotionTable data={edge?.emotion_outcomes} />
              </Section>
              <Section title="Mistake Frequency">
                <MistakeFreq data={edge?.mistake_frequency} />
              </Section>
            </>
          )}

        </div>
      )}
    </div>
  );
}
