import { useEffect, useMemo, useState } from 'react';
import { leaksApi, tradesApi } from '../api';
import { money, n0, shortDate } from '../v3/parts';
import '../leaks.css';

/* Leak Finder: the Reports "Leaks" tab. The rules live in backend/leaks.py; this file only
   names the buckets in plain words and lays them out. A leak only uses what was known when
   the trade was entered, so skipping it is something you could actually have done. */

const SIDE = { LONG: 'Long trades', SHORT: 'Short trades' };
const INSTRUMENT = { FUTURE: 'Futures', STOCK: 'Stocks', OPTION: 'Options' };
const WEEKDAY = { Mon: 'Mondays', Tue: 'Tuesdays', Wed: 'Wednesdays', Thu: 'Thursdays', Fri: 'Fridays', Sat: 'Saturdays', Sun: 'Sundays' };
const TRADE_NO = {
  '1st': 'First trade of the day',
  '2nd-3rd': 'Second and third trades of the day',
  '4th-6th': 'Fourth to sixth trades of the day',
  '7th or later': 'Seventh trade of the day and later',
};
const DAY_STATE = {
  'Day already red': 'Trading when the day is already red',
  'Giving back a green day': 'Trading while giving back a green day',
  'Flat or at the day high': 'Trading from flat or the day high',
};

function title(f) {
  const b = f.bucket;
  switch (f.dimension) {
    case 'entry_window': return `Entries ${b}`;
    case 'trade_no': return TRADE_NO[b] || b;
    case 'loss_streak': return b;
    case 'reentry': return `Getting back in ${b.charAt(0).toLowerCase()}${b.slice(1)}`;
    case 'day_state': return DAY_STATE[b] || b;
    case 'size': return `Trades at ${b}`;
    case 'instrument': return INSTRUMENT[b] || b;
    case 'side': return SIDE[b] || b;
    case 'weekday': return WEEKDAY[b] || b;
    case 'setup': return `Setup: ${b}`;
    case 'strategy': return `Strategy: ${b}`;
    case 'source': return `Source: ${b}`;
    case 'mistake': return `Tagged "${b}"`;
    case 'emotion': return `Feeling ${b}`;
    default: return b;
  }
}

const CONFIDENCE = {
  solid: { label: 'Solid pattern', hint: 'The gap to your other trades is about twice its noise or more.' },
  likely: { label: 'Likely pattern', hint: 'The gap to your other trades is larger than its noise, but not by much.' },
  noise: { label: 'Could be noise', hint: 'The gap to your other trades is within normal variation.' },
};

function Fact({ label, children }) {
  return (
    <div className="lk-fact">
      <dt className="v3-lab">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

/* The one picture on the tab: what you kept, and how far the leaks pushed it down. */
function KeptBar({ net, cost }) {
  const kept = Math.max(net, 0);
  const whole = kept + cost;
  if (whole <= 0) return null;
  const keptFrac = kept / whole;
  return (
    <div className="lk-kept">
      <div className="lk-kept-figs">
        <div>
          <div className="v3-lab">Your net in this range</div>
          <div className={`lk-kept-val ${net < 0 ? 'v3-neg' : 'v3-pos'}`}>{money(net)}</div>
        </div>
        <div className="lk-kept-right">
          <div className="v3-lab">Without the leaks</div>
          <div className="lk-kept-val">{money(net + cost)}</div>
        </div>
      </div>
      <div className="lk-kept-bar" role="img"
           aria-label={`Net ${money(net)}. The leaks cost ${money(-cost)}, so without them it would be ${money(net + cost)}.`}>
        <span className="lk-kept-in" style={{ '--f': keptFrac }} />
        <span className="lk-kept-out" style={{ '--f': 1 - keptFrac }} />
      </div>
    </div>
  );
}

function TradeList({ ids, trades, onOpen }) {
  const [all, setAll] = useState(false);
  if (!trades) return <div className="v3-empty">Loading trades…</div>;
  const rows = ids.map(id => trades.get(id)).filter(Boolean)
    .sort((a, b) => (b.date || '').localeCompare(a.date || '') || (b.id - a.id));
  const shown = all ? rows : rows.slice(0, 25);
  const firstTime = (t) => (t.executions || []).map(e => e.time).filter(Boolean).sort()[0];
  return (
    <div className="lk-trades">
      <div className="v3-scroll">
        <table className="v3-t">
          <thead>
            <tr>
              <th>Date</th>
              <th>Ticker</th>
              <th className="v3-hide-s">Side</th>
              <th className="r">P&amp;L</th>
            </tr>
          </thead>
          <tbody>
            {shown.map(t => {
              const open = () => onOpen && onOpen(t, rows);
              return (
                <tr key={t.id} className="clickable" tabIndex={0} onClick={open}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } }}
                    aria-label={`Open ${t.ticker} trade on ${t.date}`}>
                  <td className="v3-mono">{shortDate(t.date)} <span className="v3-read">{(firstTime(t) || '').slice(0, 5)}</span></td>
                  <td><span className="v3-tick">{t.ticker}</span></td>
                  <td className="v3-hide-s v3-side">{t.side === 'LONG' ? 'Long' : t.side === 'SHORT' ? 'Short' : t.side}</td>
                  <td className={`r v3-mono ${Number(t.net_pnl) < 0 ? 'v3-neg' : Number(t.net_pnl) > 0 ? 'v3-pos' : 'v3-flat'}`}>{money(t.net_pnl)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {rows.length > shown.length && (
        <button type="button" className="lk-link" onClick={() => setAll(true)}>
          Show all {rows.length} trades
        </button>
      )}
    </div>
  );
}

function Finding({ f, rank, kind, sentence, trades, onNeedTrades, onOpen }) {
  const [open, setOpen] = useState(false);
  const conf = CONFIDENCE[f.confidence] || CONFIDENCE.noise;
  const isNew = kind === 'new';
  const counted = kind === 'leak' && f.dedup_cost != null && Math.round(f.dedup_cost) !== Math.round(-f.net_pnl);
  const toggle = () => {
    if (!open) onNeedTrades();
    setOpen(o => !o);
  };
  return (
    <article className={`lk-row lk-${kind}${rank == null ? ' lk-unranked' : ''}`}>
      {rank != null && <div className="lk-rank v3-disp" aria-hidden="true">{rank}</div>}
      <div className="lk-body">
        <div className="lk-top">
          <div className="lk-name">
            <h3>{title(f)}</h3>
            <div className="lk-dim">{f.dimension_label}</div>
          </div>
          <div className="lk-amt">
            <div className={`lk-amt-val v3-disp ${f.net_pnl < 0 ? 'v3-neg' : 'v3-pos'}`}>{money(f.net_pnl)}</div>
            <div className="lk-amt-read">
              {isNew ? 'over the last 30 days' : 'net on these trades'}
            </div>
          </div>
        </div>

        <p className="lk-sentence">{sentence || f.sentence}</p>

        <dl className="lk-facts">
          <Fact label="Trades">{n0(f.trades)}</Fact>
          <Fact label="Winners">{Math.round(f.win_rate)}%</Fact>
          <Fact label="Per trade">
            <span className={f.avg_pnl < 0 ? 'v3-neg' : 'v3-pos'}>{money(f.avg_pnl)}</span>
            <span className="lk-vs"> against {money(f.rest_avg_pnl)} for {f.rest_label || 'your other trades'}</span>
          </Fact>
          {kind === 'leak' && f.recent && (
            <Fact label="Last 30 days">
              <span className={f.recent.net_pnl < 0 ? 'v3-neg' : 'v3-pos'}>{money(f.recent.net_pnl)}</span>
              <span className="lk-vs"> on {f.recent.trades}</span>
            </Fact>
          )}
        </dl>

        <div className="lk-tags">
          <span className={`v3-chip lk-conf lk-conf-${f.confidence}`} title={conf.hint}>{conf.label}</span>
          {f.diary_based && <span className="v3-chip" title="Only trades with diary notes carry this field">From diary notes</span>}
          {counted && (
            <span className="v3-chip" title="Trades already counted under a leak above are not counted again">
              Counts ${n0(f.dedup_cost)} after the leaks above
            </span>
          )}
          <button type="button" className="lk-link" aria-expanded={open} onClick={toggle}>
            {open ? 'Hide trades' : `Show ${f.trades} trades`}
          </button>
        </div>

        {open && <TradeList ids={f.trade_ids} trades={trades} onOpen={onOpen} />}
      </div>
    </article>
  );
}

function CostTable({ costs }) {
  if (!costs?.length) return null;
  return (
    <div className="v3-scroll">
      <table className="v3-t">
        <thead>
          <tr>
            <th>Instrument</th>
            <th className="r">Trades</th>
            <th className="r">Before costs</th>
            <th className="r">Commissions and fees</th>
            <th className="r v3-hide-s">Per trade</th>
            <th className="r">Net</th>
          </tr>
        </thead>
        <tbody>
          {costs.map(c => (
            <tr key={c.instrument}>
              <td><span className="v3-tick">{INSTRUMENT[c.instrument] || c.instrument}</span></td>
              <td className="r v3-mono">{n0(c.trades)}</td>
              <td className={`r v3-mono ${c.gross_pnl < 0 ? 'v3-neg' : 'v3-pos'}`}>{money(c.gross_pnl)}</td>
              <td className="r v3-mono">{money(-c.commissions)}</td>
              <td className="r v3-mono v3-hide-s">{money(-c.per_trade)}</td>
              <td className={`r v3-mono ${c.net_pnl < 0 ? 'v3-neg' : 'v3-pos'}`}>{money(c.net_pnl)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function Leaks({ data, params, onOpenDetail, children }) {
  const [explain, setExplain] = useState({ status: 'idle', sentences: {} });
  const [trades, setTrades] = useState(null);
  const [wantTrades, setWantTrades] = useState(false);
  const paramKey = JSON.stringify(params);

  // Claude's sentences: cached on the backend by a hash of the numbers, so this is
  // instant after the first visit and only calls Claude when the numbers change.
  useEffect(() => {
    if (!data?.has_data) return undefined;
    let live = true;
    setExplain({ status: 'loading', sentences: {} });
    leaksApi.explain(params)
      .then(r => {
        if (!live) return;
        const body = r.data || {};
        // Sentences written for other numbers (an import landed in between) are not shown.
        if (body.cache_key && body.cache_key !== data.cache_key) {
          setExplain({ status: 'stale', sentences: {} });
          return;
        }
        setExplain({ status: body.error ? 'error' : 'ready', sentences: body.sentences || {}, error: body.error });
      })
      .catch(e => { if (live) setExplain({ status: 'error', sentences: {}, error: e.message }); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data?.cache_key, paramKey]);

  useEffect(() => { setTrades(null); setWantTrades(false); }, [paramKey]);
  useEffect(() => {
    if (!wantTrades || trades) return undefined;
    let live = true;
    tradesApi.list(params)
      .then(r => { if (live) setTrades(new Map((r.data || []).map(t => [t.id, t]))); })
      .catch(() => { if (live) setTrades(new Map()); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wantTrades, trades, paramKey]);

  const need = () => setWantTrades(true);
  const open = (t, list) => onOpenDetail && onOpenDetail(t, list);
  const say = (f) => explain.sentences[f.id];

  const headline = useMemo(() => {
    if (!data?.leaks?.length) return null;
    const n = data.leaks.length;
    return `${n === 1 ? 'One leak' : n === 2 ? 'Two leaks' : 'Three leaks'} cost you $${n0(data.total_cost)} in this range.`;
  }, [data]);

  if (!data?.has_data) return <div className="v3-empty">No trades in this range.</div>;
  const cov = data.coverage || {};

  return (
    <div className="lk">
      <section className="lk-hero">
        <h2 className="lk-head">{headline || 'No leak found in this range.'}</h2>
        <p className="lk-sub">
          {headline
            ? 'A leak is a group of at least 20 trades, sharing something you knew when you entered, that lost money in total. Each trade counts once, under the first leak it falls in.'
            : 'No group of 20 or more trades, defined by anything you knew at entry, lost money in total. Widen the date range to give it more trades.'}
        </p>
        {headline && <KeptBar net={data.net_pnl} cost={data.total_cost} />}
        <p className="lk-note">
          {explain.status === 'loading' && 'Claude is writing the explanations. The plain numbers are shown until then.'}
          {explain.status === 'ready' && 'Explanations written by Claude from these numbers only.'}
          {explain.status === 'error' && `Claude's explanations are unavailable (${explain.error}). Showing the plain numbers.`}
          {explain.status === 'stale' && 'Your trades changed while this loaded. Reopen the tab for the explanations.'}
          {' '}Diary fields (tags, emotion, strategy, source) cover {n0(cov.diary_trades)} of {n0(cov.trades)} trades,
          so findings marked From diary notes rest on those trades only.
        </p>
        <p className="lk-note">
          Solid pattern: the gap to the other trades is about twice its normal variation or more.
          Likely pattern: larger than its variation. Could be noise: within it.
        </p>
      </section>

      {data.leaks.length > 0 && (
        <section className="lk-group" aria-label="Leaks, worst first">
          {data.leaks.map((f, i) => (
            <Finding key={f.id} f={f} rank={i + 1} kind="leak" sentence={say(f)}
                     trades={trades} onNeedTrades={need} onOpen={open} />
          ))}
        </section>
      )}

      {data.new?.length > 0 && (
        <section className="lk-group">
          <div className="lk-group-head">
            <h2 className="v3-h">New in the last 30 days</h2>
            <p className="v3-h-sub">Losing since {shortDate(data.recent_from)}, though not over the whole range. Groups of 10 or more trades.</p>
          </div>
          {data.new.map(f => (
            <Finding key={f.id} f={f} kind="new" sentence={say(f)}
                     trades={trades} onNeedTrades={need} onOpen={open} />
          ))}
        </section>
      )}

      {data.edges?.length > 0 && (
        <section className="lk-group">
          <div className="lk-group-head">
            <h2 className="v3-h">What is working</h2>
            <p className="v3-h-sub">The groups that beat the rest of your trades by the widest margin.</p>
          </div>
          {data.edges.map(f => (
            <Finding key={f.id} f={f} kind="edge" sentence={say(f)}
                     trades={trades} onNeedTrades={need} onOpen={open} />
          ))}
        </section>
      )}

      {children}

      <section className="lk-group">
        <div className="lk-group-head">
          <h2 className="v3-h">Costs</h2>
          <p className="v3-h-sub">Commissions and fees by instrument, already taken out of every figure above.</p>
        </div>
        <CostTable costs={data.costs} />
      </section>
    </div>
  );
}
