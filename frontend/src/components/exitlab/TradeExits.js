import { useCallback, useEffect, useRef, useState } from 'react';
import { exitlabApi } from '../../api';
import SetupAssignments from './SetupAssignment';
import { setupOf } from './simple';
import ReplayChart from './ReplayChart';
import { number, signed, clockTime, reasonText, errorText, DemoLabel, Notice, LoadPrices, needsPrices, fetchSessionPrices } from './shared';
import { SimpleStop, SimpleMethodology, OutcomeTable } from './simple';

// The Exits tab: this trade replayed with every preset and saved rule that applies to its setup.
export default function TradeExits({ trade }) {
  const [replay, setReplay] = useState(null), [error, setError] = useState('');
  const [editingSetups, setEditingSetups] = useState(false);
  // Missing prices load once per open: 'idle' -> 'loading' -> 'done' | 'failed'. Never in a loop.
  const [prices, setPrices] = useState('idle'), [pricesError, setPricesError] = useState('');
  const generation = useRef(0), attempted = useRef(null), alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { setPrices('idle'); setPricesError(''); }, [trade.id]);
  const load = useCallback(async (keepView = false) => {
    const seq = ++generation.current;
    if (!keepView) setReplay(null);
    setError('');
    try { const r = await exitlabApi.simpleReplay(trade.id); if (seq === generation.current) setReplay({ ...r.data, forTrade: trade.id }); }
    catch (e) { if (seq === generation.current) setError(errorText(e)); }
  }, [trade.id]);
  useEffect(() => { load(); const gate = generation; return () => { gate.current++; }; }, [load]);
  const allReasons = replay ? [...(replay.reasons || []), ...(replay.rules || []).flatMap(r => r.trades.flatMap(x => x.reasons || []))] : [];
  const missingPrices = needsPrices(allReasons);
  useEffect(() => {
    if (!replay || replay.forTrade !== trade.id || !missingPrices || attempted.current === trade.id) return;
    attempted.current = trade.id;
    setPrices('loading'); setPricesError('');
    (async () => {
      try {
        await fetchSessionPrices(replay.rows?.[0]?.journal_trade || trade, false);
        if (!alive.current) return;
        await load(true);
        if (alive.current) setPrices('done');
      } catch (e) {
        if (alive.current) { setPricesError(errorText(e)); setPrices('failed'); }
      }
    })();
  }, [replay, missingPrices, trade, load]);
  const loadingPrices = prices === 'loading';
  const otherReasons = (replay?.reasons || []).filter(r => r !== 'missing_initial_stop');
  const t = replay?.trade;
  const actual = replay?.rows?.[0]?.actual_r ?? replay?.rules?.flatMap(r => r.trades).find(r => r.eligible)?.actual_r;
  return <div className="xl-replay xl-simple-replay">
    <DemoLabel show={trade.source === 'demo'} />
    {error ? <Notice error>{error} <button className="btn btn-secondary btn-sm" onClick={() => load()}>Retry</button></Notice> : !replay && <Notice>Calculating exits...</Notice>}
    {replay && <>
      {t && <div className="xl-actual-line"><strong>{trade.side} {trade.ticker}</strong><span>Entry {clockTime(t.entered_at)} / ${number(t.entry)}</span><span>Exit {clockTime(t.exited_at)} / ${number(t.exit)}</span><span>Stop {t.stop == null ? 'needed' : `$${number(t.stop)}`}</span><strong className={actual > 0 ? 'v3-pos' : actual < 0 ? 'v3-neg' : ''}>{actual == null ? 'R not available' : `${signed(actual)}R actual`}</strong></div>}
      {replay.scaled_entry && <p className="xl-muted">Scaled entry: {replay.scaled_entry.fill_count} fills {clockTime(replay.scaled_entry.first_fill_at)} to {clockTime(replay.scaled_entry.last_fill_at)}, avg ${number(replay.scaled_entry.average_entry)}, simulated from the last fill</p>}
      {replay.scaled_entry?.warning && <Notice>{replay.scaled_entry.warning}.</Notice>}
      {replay.scaled_entry && replay.excursion?.eligible && <p className="xl-muted">MFE / MAE / efficiency: average entry, measured over the hold after the last entry fill.</p>}
      {((t && t.stop == null) || (!t && replay.rows?.[0]?.stop_price == null) || replay.reasons?.includes('missing_initial_stop')) && <SimpleStop tradeId={trade.id} onSaved={() => load()} />}
      {otherReasons.length > 0 && !loadingPrices && <Notice>{otherReasons.map(reasonText).join(' ')}</Notice>}
      {t && replay.bars.length > 0 && <ReplayChart replay={replay} />}
      <p className="xl-muted">Showing rules for: {replay.rows?.[0] ? setupOf(replay.rows[0]) : 'No setup'} <span aria-hidden="true"> · </span><button className="xl-trade-link" onClick={() => setEditingSetups(!editingSetups)}>{editingSetups ? 'Done' : 'Edit'}</button></p>
      {editingSetups && <SetupAssignments rules={replay.assignment_rules} options={replay.setup_options} onSaved={() => load()} />}
      {loadingPrices ? <Notice>Loading prices for this session...</Notice> : <OutcomeTable replay={replay} />}
      {prices === 'failed' && <Notice error>Prices did not load. {pricesError}</Notice>}
      {(prices === 'failed' || prices === 'done') && <LoadPrices row={replay.rows?.[0]} reasons={allReasons} onLoaded={() => load(true)} />}
      <SimpleMethodology />
    </>}
  </div>;
}
