import { useState } from 'react';
import { exitlabApi } from '../../api';
import { SetupSelect } from './SetupAssignment';
import { Notice, errorText } from './shared';

const kinds = { vwap_touch: 'Target at VWAP touch', target: 'R target', minutes: 'Minutes in trade', clock: 'Clock time (New York)', close: 'Session close' };
const indicators = { vwap: 'VWAP close', ema9: 'EMA 9 close', ema20: 'EMA 20 close', ema_cross: 'EMA 9/20 cross' };
const tranche = (kind, fraction = 1) => ({ kind, fraction, ...(kind === 'target' ? { r: 1 } : kind === 'minutes' ? { minutes: 30 } : kind === 'clock' ? { clock: '15:55' } : {}) });
export function describeRule(rule) {
  const parts = rule.tranches.map(t => `close ${Math.round(t.fraction * 10000) / 100}% ${t.kind === 'target' ? `at +${t.r}R` : t.kind === 'vwap_touch' ? 'at VWAP when a long below VWAP reaches it, or a short above VWAP reaches it' : t.kind === 'minutes' ? `after ${t.minutes} minutes` : t.kind === 'clock' ? `at ${t.clock} New York time` : 'at session close'}`);
  rule.r_steps?.forEach(s => parts.push(`move the stop to ${s.stop_r >= 0 ? '+' : ''}${s.stop_r}R at +${s.at_r}R`));
  if (rule.atr) parts.push(`trail completed closes by ${rule.atr.multiple}x pre-trade ATR (${rule.atr.length} bars)`);
  if (rule.indicator?.kind === 'rs_rw') parts.push(`long: exit when RS turns to RW; short: exit when RW turns to RS, confirmed by ${rule.indicator.confirmation || 1} consecutive completed 5-minute ${rule.indicator.confirmation === 1 || !rule.indicator.confirmation ? 'bar' : 'bars'}, filled at the next 1-minute open`);
  else if (rule.indicator) parts.push(`close the remainder on ${indicators[rule.indicator.kind]} (${rule.indicator.timeframe}-minute bars)`);
  return `Your rule: ${parts.join('; ')}. Initial stop always active. Any remainder closes with the session.`;
}
function Chip({ active, onClick, disabled, children }) { return <button type="button" className={`xl-chip ${active ? 'on' : ''}`} aria-pressed={active} disabled={disabled} onClick={onClick}>{children}</button>; }
function Family({ name, children, note }) { return <div className="xl-family"><strong>{name}</strong><div className="xl-chips">{children}</div><span>{note}</span></div>; }
export default function RuleBuilder({ onSaved, disabled, setupOptions = [] }) {
  const [name, setName] = useState('My exit rule');
  const [tranches, setTranches] = useState([tranche('target')]);
  const [steps, setSteps] = useState([]);
  const [atr, setAtr] = useState(null);
  const [indicator, setIndicator] = useState(null);
  const [setups, setSetups] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const rule = { name, tranches, r_steps: steps, atr, indicator, tick_size: 0.01 };
  const change = (i, value) => setTranches(ts => ts.map((t, index) => index === i ? value : t));
  const setCount = n => setTranches(Array.from({ length: n }, (_, i) => ({ ...(tranches[i] || tranche(i === n - 1 ? 'close' : 'target')), fraction: i === n - 1 ? 1 - Math.floor(100 / n) * (n - 1) / 100 : Math.floor(100 / n) / 100 })));
  async function save(e) {
    e.preventDefault(); setError('');
    if (Math.abs(tranches.reduce((sum, t) => sum + t.fraction, 0) - 1) > 1e-9) { setError('Tranche sizes must total 100%. Add a session-close tranche for any remainder.'); return; }
    setBusy(true);
    try { const r = await exitlabApi.saveRule({ ...rule, applies_to_setups: setups }); await onSaved(r.data.id); }
    catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  return <details className="xl-drawer xl-builder"><summary>Build an exit rule <span>Targets, time, scale-out, trailing and indicators</span></summary>
    <form onSubmit={save}><fieldset disabled={disabled || busy}>
      <p className="xl-rule-sentence">{describeRule(rule)}</p>
      <SetupSelect value={setups} options={setupOptions} onChange={setSetups} />
      <Family name="Targets" note="Set each tranche below">{[1, 2, 3].map(r => <Chip key={r} active={tranches[0].kind === 'target' && tranches[0].r === r} onClick={() => change(0, { ...tranche('target', tranches[0].fraction), r })}>+{r}R</Chip>)}<Chip active={tranches[0].kind === 'target' && ![1, 2, 3].includes(tranches[0].r)} onClick={() => change(0, { ...tranche('target', tranches[0].fraction), r: 1.5 })}>Custom R</Chip><Chip active={tranches[0].kind === 'vwap_touch'} onClick={() => change(0, tranche('vwap_touch', tranches[0].fraction))}>Target at VWAP touch</Chip></Family>
      <Family name="Time" note="Applies to last tranche">{['minutes', 'clock', 'close'].map(kind => <Chip key={kind} active={tranches[tranches.length - 1].kind === kind} onClick={() => change(tranches.length - 1, tranche(kind, tranches[tranches.length - 1].fraction))}>{kinds[kind]}</Chip>)}</Family>
      <Family name="Scale out" note={`${Math.round(tranches.reduce((s, t) => s + t.fraction, 0) * 10000) / 100}% allocated`}>{[1, 2, 3].map(n => <Chip key={n} active={tranches.length === n} onClick={() => setCount(n)}>{n} {n === 1 ? 'tranche' : 'tranches'}</Chip>)}</Family>
      <div className="xl-tranches">{tranches.map((t, i) => <div className="xl-fields" key={i}>
        <label>Tranche {i + 1}<select value={t.kind} onChange={e => change(i, tranche(e.target.value, t.fraction))}>{Object.entries(kinds).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
        <label>Size (%)<input required type="number" min="0.01" max="100" step="0.01" value={Math.round(t.fraction * 10000) / 100} onChange={e => change(i, { ...t, fraction: Number(e.target.value) / 100 })} /></label>
        {t.kind === 'target' && <label>R multiple<input required type="number" step="0.1" min="0.1" value={t.r} onChange={e => change(i, { ...t, r: Number(e.target.value) })} /></label>}
        {t.kind === 'minutes' && <label>Minutes<input required type="number" min="1" max="390" value={t.minutes} onChange={e => change(i, { ...t, minutes: Number(e.target.value) })} /></label>}
        {t.kind === 'clock' && <label>New York time<input required type="time" min="09:30" max="16:00" value={t.clock} onChange={e => change(i, { ...t, clock: e.target.value })} /></label>}
      </div>)}</div>
      <Family name="Trailing" note="Stops never loosen"><Chip active={steps.length > 0} onClick={() => setSteps(steps.length ? [] : [{ at_r: 1, stop_r: 0 }])}>R-step</Chip><Chip active={!!atr} onClick={() => setAtr(atr ? null : { length: 14, multiple: 2 })}>ATR</Chip><Chip disabled>N-bar low/high / coming next</Chip><Chip disabled>Chandelier / coming next</Chip></Family>
      {!!steps.length && <div className="xl-tranches">{steps.map((s, i) => <div key={i} className="xl-fields">
        <label>Activate at R<input required type="number" step="0.1" min="0.1" value={s.at_r} onChange={e => setSteps(steps.map((v, j) => j === i ? { ...v, at_r: Number(e.target.value) } : v))} /></label>
        <label>Move stop to R<input required type="number" step="0.1" min="-1" value={s.stop_r} onChange={e => setSteps(steps.map((v, j) => j === i ? { ...v, stop_r: Number(e.target.value) } : v))} /></label>
        <button type="button" className="btn btn-ghost" onClick={() => setSteps(steps.filter((_, j) => j !== i))}>Remove step {i + 1}</button>
      </div>)}<button type="button" disabled={steps.length >= 20} className="btn btn-secondary btn-sm" onClick={() => setSteps([...steps, { at_r: steps[steps.length - 1].at_r + 1, stop_r: steps[steps.length - 1].stop_r + 1 }])}>Add R-step</button></div>}
      {atr && <div className="xl-fields xl-tranches"><label>ATR length<input type="number" required min="2" max="200" value={atr.length} onChange={e => setAtr({ ...atr, length: Number(e.target.value) })} /></label><label>ATR multiple<input type="number" required min="0.1" max="100" step="0.1" value={atr.multiple} onChange={e => setAtr({ ...atr, multiple: Number(e.target.value) })} /></label></div>}
      <Family name="Indicator" note="Fills next open">{Object.entries(indicators).map(([kind, label]) => <Chip key={kind} active={indicator?.kind === kind} onClick={() => setIndicator(indicator?.kind === kind ? null : { kind, timeframe: indicator?.timeframe || 1 })}>{label}</Chip>)}<Chip active={indicator?.kind === 'rs_rw'} onClick={() => setIndicator(indicator?.kind === 'rs_rw' ? null : { kind: 'rs_rw', timeframe: 5, confirmation: 1 })}>RS/RW vs SPY</Chip></Family>
      {indicator?.kind === 'rs_rw' && <div className="xl-fields xl-tranches"><p>Long: exit when RS turns to RW. Short: exit when RW turns to RS. Always 5-minute bars.</p><label>Confirmation bars<input required type="number" min="1" max="20" value={indicator.confirmation} onChange={e => setIndicator({ ...indicator, confirmation: Number(e.target.value) })} /></label></div>}
      {indicator && indicator.kind !== 'rs_rw' && <label className="xl-filter">Indicator timeframe<select value={indicator.timeframe} onChange={e => setIndicator({ ...indicator, timeframe: Number(e.target.value) })}><option value="1">1 minute</option><option value="5">5 minutes</option></select></label>}
      <div className="xl-fields xl-save"><label className="xl-grow">Rule name<input required maxLength="100" value={name} onChange={e => setName(e.target.value)} /></label><button className="btn btn-primary">{busy ? 'Saving...' : 'Save rule'}</button></div>
      <p className="xl-muted">Saved rules are immutable. Saving adds a new rule. Setup assignments can be edited for every rule, including presets.</p>
    </fieldset>{error && <Notice error>{error}</Notice>}</form>
  </details>;
}
