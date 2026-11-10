import { pointValuesApi } from './api';

// Dollars per point per contract. The futures table comes from the backend once
// (GET /api/point-values), so the app and the importers never disagree.
let table = null;
let pending = null;

export function loadPointValues() {
  if (table) return Promise.resolve(table);
  if (pending) return pending;
  const request = pointValuesApi?.get ? pointValuesApi.get() : Promise.reject(new Error('no api'));
  pending = request
    .then(r => { table = r?.data || null; return table; })
    .catch(() => null)
    .finally(() => { pending = null; });
  return pending;
}

export function pointValue(instrument, ticker) {
  const kind = (instrument || 'STOCK').toUpperCase();
  if (kind === 'OPTION') return 100;
  if (kind !== 'FUTURE') return 1;
  const futures = table?.futures || {};
  let t = (ticker || '').toUpperCase();
  if (t && !t.startsWith('/')) t = `/${t}`;
  const root = Object.keys(futures)
    .sort((a, b) => b.length - a.length)
    .find(k => t.startsWith(k.toUpperCase()));
  return root ? futures[root] : 1;
}
