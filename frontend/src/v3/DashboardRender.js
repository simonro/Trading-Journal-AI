/* The V3 Today page. Presentation only: every value, handler and piece of
   state is passed in from Dashboard.js, so no behaviour lives here. */
import { useState, useEffect } from 'react';
import { X } from 'lucide-react';
import { calendarApi } from '../api';
import {
  Measures, BarRow, Tabs, Seg, EquityCurve, SessionStrip, MonthGrid,
  money, money2, moneyK, tone, shortDate, MONTH_NAMES,
} from './parts';

/* ── the month, fetched per month like the old mini calendar ───────────── */
function MonthPanel({ accountId, onDayClick, latestDate }) {
  const now = new Date();
  // Open on the month of the most recent session, not on today. Today's month
  // is empty whenever the last trade was in an earlier month, which left the
  // dashboard showing a blank grid.
  const seed = latestDate ? String(latestDate).split('-') : null;
  const [year, setYear] = useState(seed ? Number(seed[0]) : now.getFullYear());
  const [month, setMonth] = useState(seed ? Number(seed[1]) : now.getMonth() + 1);
  const [byDay, setByDay] = useState({});

  useEffect(() => {
    const params = { year, month };
    if (accountId != null) params.account_id = accountId;
    let live = true;
    calendarApi.get(params).then((r) => {
      if (!live) return;
      const map = {};
      for (const d of r.data) map[d.date] = d;
      setByDay(map);
    }).catch(() => {});
    return () => { live = false; };
  }, [year, month, accountId]);

  const prev = () => (month === 1 ? (setYear((y) => y - 1), setMonth(12)) : setMonth((m) => m - 1));
  const next = () => (month === 12 ? (setYear((y) => y + 1), setMonth(1)) : setMonth((m) => m + 1));

  const all = Object.values(byDay);
  const total = all.reduce((s, d) => s + (d.net_pnl || 0), 0);
  const todayKey = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;

  return (
    <>
      <div className="v3-cal-head">
        <h2 className="v3-cal-month">{MONTH_NAMES[month - 1]}<span>{year}</span></h2>
        <div className="v3-step">
          <button type="button" onClick={prev} aria-label="Previous month">&lsaquo;</button>
          <button type="button" onClick={next} aria-label="Next month">&rsaquo;</button>
        </div>
        <div className="v3-cal-tot">
          {all.length} session{all.length === 1 ? '' : 's'} traded
          <b className={tone(total)}>{all.length ? moneyK(total) : ''}</b>
        </div>
      </div>
      <div className="v3-scroll">
        <MonthGrid year={year} month={month} byDay={byDay} today={todayKey} onPick={onDayClick} />
      </div>
    </>
  );
}

/* ── the three breakdowns, sharing one space ───────────────────────────── */
function Patterns({ edge, byStrategy, onViewAll }) {
  const [tab, setTab] = useState('tod');
  const [view, setView] = useState('bars');
  const tabs = [
    { id: 'tod', label: 'Time of day' },
    { id: 'dow', label: 'Day of week' },
    { id: 'str', label: 'Strategy' },
  ];

  const rows = (() => {
    if (tab === 'tod') {
      return (edge?.time_of_day || []).map((r) => ({ name: r.bucket, n: r.trade_count, v: r.net_pnl }));
    }
    if (tab === 'dow') {
      return (edge?.day_of_week || []).map((r) => ({ name: r.day, n: r.trade_count, v: r.net_pnl }));
    }
    return (byStrategy || []).map((r) => ({
      name: r.strategy || r.setup || 'Untagged', n: r.count, v: r.net_pnl, wr: r.win_rate, avgR: r.avg_r,
    }));
  })();

  const peak = Math.max(1, ...rows.map((r) => Math.abs(r.v || 0)));

  return (
    <>
      <div className="v3-sec-head">
        <div>
          <h2 className="v3-h">Patterns</h2>
          <p className="v3-h-sub">
            {tab === 'tod' && 'Net P&L by the hour you entered'}
            {tab === 'dow' && 'Net P&L by weekday'}
            {tab === 'str' && 'Net P&L by the strategy on the trade'}
          </p>
        </div>
        <div className="v3-acts">
          <Seg
            label="How to show this breakdown"
            value={view}
            onChange={setView}
            options={[{ id: 'bars', label: 'Bars' }, { id: 'table', label: 'Table' }]}
          />
          <button type="button" className="btn btn-secondary btn-sm" onClick={onViewAll}>Full report</button>
        </div>
      </div>
      <Tabs tabs={tabs} active={tab} onChange={setTab} label="Breakdown" />
      {!rows.length ? (
        <div className="v3-empty">Nothing recorded in this range.</div>
      ) : (
        <div className="v3-scroll">
          <table className="v3-t">
            <thead>
              <tr>
                <th>{tab === 'tod' ? 'Hour' : tab === 'dow' ? 'Day' : 'Strategy'}</th>
                <th className="r">Trades</th>
                {tab === 'str' && <th className="r">Win rate</th>}
                {view === 'table' && <th className="r">Avg / trade</th>}
                {view === 'table' && tab === 'str' && <th className="r v3-hide-s">Avg R</th>}
                {view === 'bars' && <th>Net</th>}
                <th className="r">Total</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  <td className="v3-tick">{r.name}</td>
                  <td className="r v3-mono">{r.n ?? ''}</td>
                  {tab === 'str' && (
                    <td className="r v3-mono">{r.wr != null ? `${Number(r.wr).toFixed(0)}%` : '—'}</td>
                  )}
                  {view === 'table' && (
                    <td className={`r v3-mono ${r.n ? tone((r.v || 0) / r.n) : 'v3-flat'}`}>
                      {r.n ? money((r.v || 0) / r.n) : '—'}
                    </td>
                  )}
                  {view === 'table' && tab === 'str' && (
                    <td className={`r v3-mono v3-hide-s ${r.avgR != null ? tone(r.avgR) : 'v3-flat'}`}>
                      {r.avgR != null ? `${r.avgR > 0 ? '+' : ''}${Number(r.avgR).toFixed(2)}R` : '—'}
                    </td>
                  )}
                  {view === 'bars' && (
                    <td><BarRow frac={Math.abs(r.v || 0) / peak} negative={(r.v || 0) < 0} /></td>
                  )}
                  <td className={`r v3-mono ${tone(r.v)}`} style={{ fontWeight: 600 }}>{money(r.v)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

/* ── open positions, with the exit form ────────────────────────────────── */
function OpenPositions(p) {
  const {
    openPositions, onOpenDetail, openCloseModal,
    closingPos, setClosingPos, closeDate, setCloseDate, closeTime, setCloseTime,
    closePrice, setClosePrice, closeCommission, setCloseCommission,
    closeError, closeSubmitting, handleClosePosition,
  } = p;

  return (
    <div className="v3-subband">
      <div className="v3-sec-head">
        <div>
          <h2 className="v3-h">Open positions</h2>
          <p className="v3-h-sub">Recording an exit journals it, it does not place an order</p>
        </div>
        <span className={`v3-chip${openPositions?.length ? ' caution' : ''}`}>
          {openPositions?.length || 0} position{openPositions?.length === 1 ? '' : 's'}
        </span>
      </div>

      {!openPositions?.length ? (
        <div className="v3-empty">No open positions.</div>
      ) : (
        <div className="v3-scroll">
          <table className="v3-t">
            <thead>
              <tr>
                <th>Ticker</th>
                <th className="v3-hide-s">Side</th>
                <th className="r">Remaining</th>
                <th className="r">Avg entry</th>
                <th className="r"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {openPositions.map((pos, i) => {
                const execs = pos.executions || [];
                const side = (pos.side || 'LONG').toUpperCase();
                const entryAction = side === 'LONG' ? 'BOT' : 'SOLD';
                const exitAction = side === 'LONG' ? 'SOLD' : 'BOT';
                const entryFills = execs.filter((e) => e.action === entryAction);
                const totalQty = entryFills.reduce((a, e) => a + (e.qty || 0), 0);
                const exitQty = execs.filter((e) => e.action === exitAction).reduce((a, e) => a + (e.qty || 0), 0);
                const remainingQty = totalQty - exitQty;
                const avgEntry = totalQty > 0
                  ? entryFills.reduce((a, e) => a + (e.qty || 0) * (e.price || 0), 0) / totalQty : 0;
                const open = () => onOpenDetail && onOpenDetail(pos);
                return (
                  <tr
                    key={i}
                    className="clickable"
                    tabIndex={0}
                    onClick={open}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } }}
                    aria-label={`Open ${pos.ticker} position`}
                  >
                    <td className="v3-tick">{pos.ticker}</td>
                    <td className="v3-hide-s v3-side">{side === 'LONG' ? 'Long' : 'Short'}</td>
                    <td className="r v3-mono">
                      {remainingQty}
                      {exitQty > 0 && <span className="v3-read"> of {totalQty}</span>}
                    </td>
                    <td className="r v3-mono">
                      ${avgEntry.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </td>
                    <td className="r">
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={(e) => { e.stopPropagation(); openCloseModal(pos); }}
                        onKeyDown={(e) => e.stopPropagation()}
                      >
                        Close
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {closingPos && (
        <div className="v3-notice" style={{ marginTop: 16, display: 'block' }} role="group" aria-label={`Close ${closingPos.ticker}`}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
            <span style={{ fontWeight: 600, fontSize: 14 }}>
              Record exit: {closingPos.ticker} {closingPos.side} ({closingPos.openQty} remaining)
            </span>
            <button type="button" className="btn btn-ghost btn-icon" onClick={() => setClosingPos(null)} aria-label="Cancel closing position">
              <X size={14} />
            </button>
          </div>
          <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <div>
              <label className="field-label" htmlFor="close-date">Exit date</label>
              <input id="close-date" type="date" value={closeDate} onChange={(e) => setCloseDate(e.target.value)} style={{ width: 148 }} />
            </div>
            <div>
              <label className="field-label" htmlFor="close-time">Exit time</label>
              <input id="close-time" type="time" value={closeTime} onChange={(e) => setCloseTime(e.target.value)} style={{ width: 118 }} />
            </div>
            <div>
              <label className="field-label" htmlFor="close-price">Exit price</label>
              <input id="close-price" type="number" step="0.01" placeholder="0.00" value={closePrice}
                onChange={(e) => setClosePrice(e.target.value)} style={{ width: 118 }}
                onKeyDown={(e) => e.key === 'Enter' && handleClosePosition()} />
            </div>
            <div>
              <label className="field-label" htmlFor="close-comm">Fees</label>
              <input id="close-comm" type="number" step="0.01" min="0" value={closeCommission}
                onChange={(e) => setCloseCommission(e.target.value)} style={{ width: 96 }}
                onKeyDown={(e) => e.key === 'Enter' && handleClosePosition()} />
            </div>
            <button type="button" className="btn btn-primary" onClick={handleClosePosition}
              disabled={closeSubmitting || !closePrice || !closeDate}>
              {closeSubmitting ? 'Saving…' : 'Record exit'}
            </button>
          </div>
          {closeError && <div className="notice neg" role="alert" style={{ marginTop: 12 }}>{closeError}</div>}
        </div>
      )}
    </div>
  );
}

/* ── the page ──────────────────────────────────────────────────────────── */
export default function DashboardRender(p) {
  const {
    kpis, accountLabel, span, RangePicker,
    goalsNode, onToggleGoals, showGoals,
    accountId, onDayClick, onOpenDetail, onViewAllTrades,
    recentTrades, edgeReport, goals,
  } = p;

  const [scrub, setScrub] = useState(null);

  const k = kpis || {};
  const days = k.daily_pnl || [];
  const net = k.total_net_pnl || 0;
  // The headline shows dollars and cents separately, so split the value here
  // rather than through money(): money() rounds to whole dollars, which would
  // carry a >=50c fraction into the dollars (-17.57 showing as -$18.57).
  // Working in whole cents lets .995 carry into the dollars correctly.
  const netCents = Math.round(Math.abs(net) * 100);
  const dollars = `${net < 0 ? '−$' : '+$'}${Math.trunc(netCents / 100).toLocaleString('en-US')}`;
  const cents = `.${String(netCents % 100).padStart(2, '0')}`;

  const awin = Math.abs(k.avg_win || 0);
  const aloss = Math.abs(k.avg_loss || 0);
  const ratio = aloss > 0 ? awin / aloss : null;
  const eff = k.exit_efficiency == null ? null : Number(k.exit_efficiency);

  // These are the names the goals API actually returns. An earlier version
  // invented *_goal keys, so every goal silently fell back to a default and a
  // saved change never appeared on the card.
  const g = goals || {};
  const gWin = g.win_rate ?? 65;
  const gDay = g.day_win_rate ?? 75;
  const gPf = g.profit_factor ?? 1.5;
  const gRatio = g.avg_win_loss_ratio ?? 1.5;
  const gEff = g.exit_efficiency ?? 50;
  const gExp = g.expectancy ?? 50;
  const cap = (x) => Math.max(0, Math.min(1, x));

  const measures = [
    {
      label: 'Trade win rate', value: `${(k.win_rate || 0).toFixed(1)}%`,
      fill: cap((k.win_rate || 0) / 100), goal: `${gWin}%`, goalPct: cap(gWin / 100) * 100,
      met: (k.win_rate || 0) >= gWin,
      read: `${(k.winning_trades || 0).toLocaleString()} won, ${(k.losing_trades || 0).toLocaleString()} lost`,
    },
    {
      label: 'Day win rate', value: `${(k.day_win_rate || 0).toFixed(1)}%`,
      fill: cap((k.day_win_rate || 0) / 100), goal: `${gDay}%`, goalPct: cap(gDay / 100) * 100,
      met: (k.day_win_rate || 0) >= gDay,
      read: `${k.positive_days || 0} green days, ${(k.trading_days || 0) - (k.positive_days || 0)} red`,
    },
    {
      label: 'Profit factor',
      value: k.profit_factor == null ? '—' : Number(k.profit_factor).toFixed(2),
      fill: cap((k.profit_factor || 0) / gPf), goal: Number(gPf).toFixed(2), goalPct: 100,
      met: (k.profit_factor || 0) >= gPf,
      read: `$${Number(k.profit_factor || 0).toFixed(2)} won for every $1.00 lost`,
    },
    {
      label: 'Win / loss size', value: ratio == null ? '—' : ratio.toFixed(2),
      fill: cap((ratio || 0) / gRatio), goal: Number(gRatio).toFixed(2), goalPct: 100,
      met: ratio != null && ratio >= gRatio,
      read: (
        <>Your average win is <b>${Math.round(awin).toLocaleString()}</b>. Your average loss is{' '}
          <b>${Math.round(aloss).toLocaleString()}</b>.
          {ratio != null && ratio < 1 ? ' You win often and small, lose rarely and big.' : ''}
        </>
      ),
    },
    {
      label: 'Exit efficiency', value: eff == null ? '—' : `${eff.toFixed(0)}%`,
      fill: cap((eff || 0) / gEff), goal: `${gEff}%`, goalPct: 100,
      met: eff != null && eff >= gEff, amber: eff != null && eff < gEff,
      read: eff == null ? 'Not enough excursion data' : `You capture ${eff.toFixed(0)}% of the move you were right about`,
    },
    {
      label: 'Expectancy', value: money2(k.expectancy || 0),
      fill: cap((k.expectancy || 0) / gExp), goal: `$${gExp}`, goalPct: 100,
      met: (k.expectancy || 0) >= gExp,
      read: 'What the next trade is worth, on average',
    },
  ];

  // daily_pnl carries no trade count, but it does carry the running total,
  // which answers a better question: where did the account stand that day.
  const readout = scrub
    ? {
      label: shortDate(scrub.date),
      value: scrub.net_pnl,
      when: scrub.cumulative != null ? `balance ${money(scrub.cumulative)}` : '',
    }
    : (() => {
      if (!days.length) return null;
      const last = days[days.length - 1];
      return { label: 'Last session', value: last.net_pnl, when: shortDate(last.date) };
    })();

  return (
    <div>
      {/* 1. the account band */}
      <div className="v3-hero">
        <div className="v3-eyeline">
          <div>
            <p className="v3-acct">{accountLabel}{span ? ` · ${span}` : ''}</p>
            <h1 className={`v3-money ${tone(net)}`}>
              {dollars}<span className="cents">{cents}</span>
            </h1>
            <p className="v3-money-sub">
              <b>{(k.trading_days || 0).toLocaleString()} sessions</b>, {(k.total_trades || 0).toLocaleString()} trades.
              {' '}You kept <b>${Math.round(net).toLocaleString()}</b> of{' '}
              <b>${Math.round(k.total_gross_pnl || 0).toLocaleString()}</b> gross; commissions took{' '}
              <b>${Math.round(Math.abs(k.total_commissions || 0)).toLocaleString()}</b>.
            </p>
          </div>
          <div className="v3-heroside">
            <div className="v3-acts">
              <button
                type="button"
                className="btn btn-ghost"
                onClick={onToggleGoals}
                aria-pressed={showGoals}
                aria-expanded={showGoals}
              >
                Edit goals
              </button>
              {RangePicker}
            </div>
            {readout && (
              <dl className="v3-readout">
                <dt className="v3-lab">{readout.label}</dt>
                <dd className={tone(readout.value)}>{money(readout.value)}</dd>
                <div className="when">{readout.when}</div>
              </dl>
            )}
          </div>
        </div>

        <EquityCurve days={days} onPick={onDayClick} />

        <div className="v3-strip-wrap">
          <div className="v3-strip-head">
            <span className="v3-lab">Every session in this range</span>
            <span className="v3-lab v3-hide-s">Hover to scrub &middot; click to open the day</span>
          </div>
          <SessionStrip days={days} onPick={onDayClick} onHover={setScrub} />
        </div>
      </div>

      {/* 2. the measures line */}
      <Measures items={measures} />

      {goalsNode}

      {/* 3. the month takes two thirds; the right third is what is live now */}
      <section className="v3-band">
        <div className="v3-split">
          <div>
            <MonthPanel
              accountId={accountId}
              onDayClick={onDayClick}
              latestDate={days.length ? days[days.length - 1].date : null}
            />
          </div>
          <div className="v3-rightcol">
            <div>
              <div className="v3-sec-head">
                <div>
                  <h2 className="v3-h">Recent trades</h2>
                  <p className="v3-h-sub">The last ten closed</p>
                </div>
                <button type="button" className="btn btn-secondary btn-sm" onClick={onViewAllTrades}>View all</button>
              </div>
              {!recentTrades?.length ? (
                <div className="v3-empty">No trades in this range.</div>
              ) : (
                <div className="v3-scroll">
                  <table className="v3-t">
                    <thead>
                      <tr>
                        <th>Ticker</th>
                        <th className="v3-hide-s">Side</th>
                        <th className="r">Date</th>
                        <th className="r">P&amp;L</th>
                      </tr>
                    </thead>
                    <tbody>
                      {recentTrades.slice(0, 10).map((t, i) => (
                        <tr
                          key={i}
                          className="clickable"
                          tabIndex={0}
                          onClick={() => onOpenDetail && onOpenDetail(t)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpenDetail && onOpenDetail(t); }
                          }}
                          aria-label={`Open ${t.ticker} trade`}
                        >
                          <td className="v3-tick">{t.ticker}</td>
                          <td className="v3-hide-s v3-side">{(t.side || '').toLowerCase() === 'short' ? 'Short' : 'Long'}</td>
                          <td className="r v3-mono v3-read">{t.date}</td>
                          <td className={`r v3-mono ${tone(t.net_pnl)}`} style={{ fontWeight: 600 }}>{money2(t.net_pnl)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            <OpenPositions {...p} />
          </div>
        </div>
      </section>

      {/* 4. the three breakdowns, sharing one space */}
      <section className="v3-band">
        <Patterns edge={edgeReport} byStrategy={k.by_strategy} onViewAll={onViewAllTrades} />
      </section>
    </div>
  );
}
