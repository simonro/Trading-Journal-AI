// Events Edge Trader for Schwab syncs into the journal (backend/edge_sync.py):
// stop and target changes, fills and the trader's own notes for one trade.
// Shared by the trade Timeline and the trade chart overlays.

export const EDGE_SOURCE = 'edge-trader';

const ET_FORMAT = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York',
  year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit',
  hourCycle: 'h23',
});

/** ISO timestamp (any UTC offset) to US Eastern wall clock: { date: 'YYYY-MM-DD', time: 'HH:MM:SS' }. */
export function etParts(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const p = {};
  ET_FORMAT.formatToParts(d).forEach(x => { p[x.type] = x.value; });
  return { date: `${p.year}-${p.month}-${p.day}`, time: `${p.hour}:${p.minute}:${p.second}` };
}

/**
 * Seconds on the chart's timeline: US Eastern wall clock read as if it were UTC.
 * Executions are stored as ET wall clock and the trade chart places them the same
 * way, so events land next to the fills they belong to.
 */
export function etWallSec(dateStr, timeStr) {
  if (!dateStr || !timeStr) return null;
  const t = timeStr.length === 5 ? `${timeStr}:00` : timeStr.slice(0, 8);
  const ms = Date.parse(`${dateStr}T${t}Z`);
  return Number.isNaN(ms) ? null : Math.floor(ms / 1000);
}

export function eventWallSec(iso) {
  const p = etParts(iso);
  return p ? etWallSec(p.date, p.time) : null;
}

export function fmtPrice(v) {
  if (v == null || v === '') return null;
  const n = Number(v);
  if (!Number.isFinite(n)) return null;
  return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: Math.abs(n) < 1 ? 4 : 2 });
}

const fmtQty = (v) => {
  const n = Number(v);
  return v == null || !Number.isFinite(n) || n === 0 ? null : n.toLocaleString('en-US', { maximumFractionDigits: 4 });
};

export const STOP_LEVEL_KINDS = new Set(['stop_set', 'stop_moved', 'breakeven', 'trail']);
export const TARGET_LEVEL_KINDS = new Set(['target_set', 'target_moved']);

/** Which family an event belongs to: drives its mark in the Timeline. */
export function eventGroup(kind) {
  if (STOP_LEVEL_KINDS.has(kind) || kind === 'stop_cancelled') return 'stop';
  if (TARGET_LEVEL_KINDS.has(kind) || kind === 'target_cancelled') return 'target';
  if (kind === 'entry' || kind === 'exit' || kind === 'partial' || kind === 'close') return 'fill';
  if (kind === 'note') return 'note';
  return 'other';
}

/**
 * A readable label for one event, split so the price can be set in tabular figures:
 * { lead, price, tail, detail, plain }. `plain` is the whole label as one string.
 */
export function describeEvent(e) {
  const price = fmtPrice(e.price);
  const qty = fmtQty(e.qty);
  const text = (e.text || '').trim() || null;
  let lead = '';
  let tail = '';
  let detail = text;
  let shownPrice = price;

  // Fills read "Entry 50 at 201.35".
  const fill = (word) => {
    const base = qty ? `${word} ${qty}` : word;
    return price ? `${base} at` : base;
  };

  switch (e.kind) {
    case 'entry': lead = fill('Entry'); break;
    case 'exit': lead = fill('Exit'); break;
    case 'partial': lead = fill('Partial exit'); break;
    case 'close': lead = fill('Position closed'); break;
    case 'stop_set': lead = 'Stop set'; break;
    case 'stop_moved': lead = price ? 'Stop moved to' : 'Stop moved'; break;
    case 'breakeven': lead = 'Stop moved to break-even'; break;
    case 'trail': lead = 'Trailing stop'; break;
    case 'stop_cancelled': lead = 'Stop'; tail = 'cancelled'; break;
    case 'target_set': lead = 'Target set'; break;
    case 'target_moved': lead = price ? 'Target moved to' : 'Target moved'; break;
    case 'target_cancelled': lead = 'Target'; tail = 'cancelled'; break;
    case 'note': lead = 'Note'; shownPrice = null; break;
    default:
      lead = text || 'Event';
      detail = null;
  }
  if (!shownPrice && (e.kind === 'stop_cancelled' || e.kind === 'target_cancelled')) {
    lead = `${lead} ${tail}`;
    tail = '';
  }
  const plain = [lead, shownPrice, tail].filter(Boolean).join(' ') + (detail ? `: ${detail}` : '');
  return { lead, price: shownPrice, tail, detail, plain };
}

export const ORIGIN_LABEL = { schwab: 'Schwab', edge: 'Edge Trader' };

/** Events with their US Eastern time attached, oldest first (stable for equal times). */
export function prepareEvents(events) {
  return (events || [])
    .map((e, i) => ({ ...e, _i: i, _et: etParts(e.ts), _sec: eventWallSec(e.ts) }))
    .sort((a, b) => ((a._sec ?? 0) - (b._sec ?? 0)) || (a._i - b._i));
}

/**
 * The level a stop or target sat at over time, as steps in chart seconds:
 * [{ sec, price }] with price null where the order was cancelled. A level still
 * working at `endSec` (the trade's last exit) ends there. Events after the exit
 * do not move the line.
 */
export function levelSteps(prepared, which, endSec) {
  const setKinds = which === 'stop' ? STOP_LEVEL_KINDS : TARGET_LEVEL_KINDS;
  const cancelKind = which === 'stop' ? 'stop_cancelled' : 'target_cancelled';
  const steps = [];
  let active = null;
  for (const e of prepared) {
    if (e._sec == null) continue;
    if (endSec != null && e._sec > endSec) break;
    if (setKinds.has(e.kind)) {
      const n = Number(e.price);
      // A trailing stop with no price cannot be drawn; the last known level stays.
      if (e.price == null || !Number.isFinite(n)) continue;
      steps.push({ sec: e._sec, price: n });
      active = n;
    } else if (e.kind === cancelKind && active != null) {
      steps.push({ sec: e._sec, price: active });
      steps.push({ sec: e._sec, price: null });
      active = null;
    }
  }
  if (active != null) steps.push({ sec: endSec ?? Infinity, price: active });
  return steps;
}
