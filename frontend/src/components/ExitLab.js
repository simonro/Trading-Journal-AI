import { useCallback, useEffect, useRef, useState } from 'react';
import { exitlabApi } from '../api';
import { PageHeader } from './ui';
import Advanced from './exitlab/Advanced';
import SetupAssignments, { SetupChips } from './exitlab/SetupAssignment';
import RuleVisibility from './exitlab/RuleVisibility';
import LoadMissing from './exitlab/LoadMissing';
import { signed, number, money, moneyTone, reasonText, errorText, DemoLabel, Notice, LoadPrices } from './exitlab/shared';
import { summary, historyLead, exclusionLine, setupOf, SimpleMethodology } from './exitlab/simple';

export default function ExitLab({ accountId, accounts, onSelectAccount, onOpenDetail }) {
  const effective = accountId || (accounts.length === 1 ? accounts[0].id : null);
  return <div className="xl-page"><PageHeader title="Exit Lab" subtitle="See how different exits would have changed your trades." />
    {!effective ? <div className="xl-body"><Notice>Choose an account to compare its trades.</Notice><label className="xl-filter">Account<select aria-label="Exit Lab account" value="" onChange={e => onSelectAccount(Number(e.target.value))}><option value="" disabled>Choose an account</option>{accounts.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label></div> : <Workspace key={effective} accountId={effective} onOpenDetail={onOpenDetail} />}
  </div>;
}

function Workspace({ accountId, onOpenDetail }) {
  const [result, setResult] = useState(null), [error, setError] = useState('');
  const [savingRule, setSavingRule] = useState(false);
  const [filters, setFilters] = useState({ from: '', to: '', symbol: '', setup: '', side: '' });
  const [selected, setSelected] = useState(''), [showExcluded, setShowExcluded] = useState(false);
  const [advanced, setAdvanced] = useState(() => localStorage.getItem('exitlab-advanced') === 'true');
  const generation = useRef(0);
  const load = useCallback(async (keepView = false) => {
    const seq = ++generation.current; setError('');
    if (!keepView) setResult(null);
    try { const r = await exitlabApi.history({ account_id: accountId }); if (seq === generation.current) setResult(r.data); }
    catch (e) { if (seq === generation.current) setError(errorText(e)); }
  }, [accountId]);
  useEffect(() => { load(); const gate = generation; return () => { gate.current++; }; }, [load]);
  const toggleRule = async (id, show) => {
    setSavingRule(true); setError('');
    try { await exitlabApi.showRule(id, show); await load(true); }
    catch (e) { setError(errorText(e)); }
    finally { setSavingRule(false); }
  };
  const data = summary(result, filters), rows = result?.rows || [];
  const displayed = data.table.find(r => r.key === selected);
  const options = field => [...new Set(rows.map(r => field === 'setup' ? setupOf(r) : r.journal_trade[field]).filter(Boolean))].sort();
  const update = (key, value) => setFilters(f => ({ ...f, [key]: value }));
  const openTrade = row => onOpenDetail(row.journal_trade, data.rows.map(r => r.journal_trade), 'Exits');
  return <div className="xl-body xl-simple-history">
    <DemoLabel show={rows.some(r => r.journal_trade.source === 'demo')} />
    {error && <Notice error>{error} <button className="btn btn-secondary btn-sm" onClick={() => load()}>Retry</button></Notice>}
    {!result && !error && <Notice>Comparing your trades...</Notice>}
    {result && <>
      <p className="xl-lead">{historyLead(data.table)}</p>
      <p className="xl-history-label">Across all your trades. This describes your history; it does not predict future results.</p>
      <div className="xl-fields xl-history-filters"><label>From<input type="date" value={filters.from} onChange={e => update('from', e.target.value)} /></label><label>To<input type="date" value={filters.to} onChange={e => update('to', e.target.value)} /></label>
        {[['symbol', 'Symbol', 'ticker'], ['setup', 'Setup', 'setup'], ['side', 'Side', 'side']].map(([key, label, field]) => <label key={key}>{label}<select aria-label={label} value={filters[key]} onChange={e => update(key, e.target.value)}><option value="">All</option>{options(field).map(v => <option key={v}>{v}</option>)}</select></label>)}
        {Object.values(filters).some(Boolean) && <button className="btn btn-ghost btn-sm" onClick={() => setFilters({ from: '', to: '', symbol: '', setup: '', side: '' })}>Clear filters</button>}
      </div>
      <details className="xl-drawer"><summary>Assign rules to setups</summary><SetupAssignments rules={result.assignment_rules} options={result.setup_options} onSaved={() => load(true)} /></details>
      <RuleVisibility rules={result.custom_rules || []} onChange={toggleRule} disabled={savingRule} />
      <div className="xl-scroll"><table className="xl-table xl-summary-table"><thead><tr><th>Exit</th><th>Trades</th><th>Avg R</th><th>Avg $</th><th>Win rate</th><th>Total R</th><th>Total $</th><th>vs your exits<small>avg R per trade</small></th><th>vs your exits ($)<small>avg $ per trade</small></th></tr></thead><tbody>{data.table.map(r => <tr key={r.key} className={selected === r.key ? 'xl-selected-row' : ''}><td><button className="xl-trade-link" aria-expanded={selected === r.key} onClick={() => setSelected(selected === r.key ? '' : r.key)}>{r.name}</button>{r.key !== 'actual' && <><SetupChips value={r.setups} />{r.excluded?.length > 0 && <details className="xl-rule-exclusions"><summary>{r.excluded.length} not evaluable</summary>{[...new Set(r.excluded.flatMap(t => t.reasons))].map(reason => <p key={reason}>{reasonText(reason)}</p>)}{r.excluded.map(t => <div key={t.trade_id}><span>#{t.trade_id} </span><LoadPrices row={rows.find(row => row.trade_id === t.trade_id)} reasons={t.reasons} onLoaded={() => load(true)} /></div>)}</details>}</>}</td><td>{r.n}</td><td>{r.avg == null ? 'Not available' : signed(r.avg)}</td><td className={moneyTone(r.avgDollars)}>{money(r.avgDollars)}</td><td>{r.win == null ? 'Not available' : `${number(r.win, 1)}%`}</td><td>{r.total == null ? 'Not available' : signed(r.total)}</td><td className={moneyTone(r.totalDollars)}>{money(r.totalDollars)}</td><td className={r.delta > 0 ? 'v3-pos' : r.delta < 0 ? 'v3-neg' : ''}>{r.key === 'actual' ? 'Your exits' : r.delta == null ? 'Not available' : signed(r.delta)}</td><td className={moneyTone(r.deltaDollars)}>{r.key === 'actual' ? 'Your exits' : money(r.deltaDollars)}</td></tr>)}</tbody></table></div>
      <div className="xl-exclusion-line">{exclusionLine(data.excluded)} {data.excluded.length > 0 && <button className="xl-trade-link" aria-expanded={showExcluded} onClick={() => setShowExcluded(!showExcluded)}>{showExcluded ? 'Hide' : 'Show'}</button>}<LoadMissing accountId={accountId} count={result.missing_prices || 0} onFinished={() => load(true)} /></div>
      {showExcluded && <ul className="xl-simple-exclusions">{data.excluded.map(row => <li key={row.trade_id}><span>{row.journal_trade.ticker} / {row.journal_trade.date}</span><span>{row.stop_price == null ? 'No stop' : row.reasons.map(reasonText).join(' ')}</span><button className="xl-trade-link" onClick={() => openTrade(row)}>{row.stop_price == null ? 'Add stop' : 'Open trade'}</button><LoadPrices row={row} reasons={row.reasons} onLoaded={() => load(true)} /></li>)}</ul>}
      {displayed && <section className="xl-panel"><div className="xl-panel-head"><h2>{displayed.name}: {displayed.n} trades</h2><button className="btn btn-ghost btn-sm" onClick={() => setSelected('')}>Close list</button></div><div className="xl-scroll xl-trades"><table className="xl-table"><thead><tr><th>Trade</th><th>Date</th><th>Side</th><th>Actual R</th><th>{displayed.name} R</th><th>vs your exit</th></tr></thead><tbody>{displayed.trades.map(t => { const row = rows.find(r => r.trade_id === t.trade_id); const value = displayed.key === 'actual' ? t.actual_r : t.pnl_r; return <tr key={t.trade_id}><td><button className="xl-trade-link" onClick={() => openTrade(row)}>{row.journal_trade.ticker} / #{t.trade_id}</button>{(t.scaled_entry?.warning || row.scaled_entry?.warning) && <small>{t.scaled_entry?.warning || row.scaled_entry.warning}</small>}</td><td>{row.journal_trade.date}</td><td>{row.journal_trade.side}</td><td>{signed(t.actual_r)}</td><td>{signed(value)}</td><td>{signed(value - t.actual_r)}</td></tr>; })}</tbody></table></div></section>}
      <details className="xl-drawer xl-advanced" open={advanced} onToggle={e => { const open = e.currentTarget.open; setAdvanced(open); localStorage.setItem('exitlab-advanced', String(open)); }}><summary>Advanced</summary>{advanced && <Advanced history={result} onRulesSaved={() => load(true)} onRuleVisibility={toggleRule} savingRule={savingRule} />}</details>
      <SimpleMethodology />
    </>}
  </div>;
}
