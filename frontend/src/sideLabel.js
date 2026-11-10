// Display label for a trade's side.
//
// The stored `side` is the mechanical one (LONG = bought to open, SHORT = sold to open) and the
// fill matching, R and P&L maths depend on it, so it never changes. For options that word is
// misleading: a sold put or a put credit spread is stored SHORT but is a bullish trade. Options
// therefore show their directional bias instead; stocks and futures keep Long / Short.

const isSpread = (t) => {
  // Imported spreads carry both strikes in the trade group, e.g. "..._735-730_PUT_1".
  const g = String(t.trade_group || '');
  return /_\d+(\.\d+)?-\d+(\.\d+)?_(PUT|CALL)_/i.test(g);
};

// { label, mechanics, title } for display. `title` spells out the mechanics for a tooltip.
export function sideInfo(t) {
  const side = String(t?.side || '').toUpperCase();
  const plain = side === 'LONG' ? 'Long' : side === 'SHORT' ? 'Short' : (t?.side || '');
  const type = String(t?.option_type || '').toUpperCase();
  const isOption = String(t?.instrument_type || '').toUpperCase() === 'OPTION';
  if (!isOption || (type !== 'PUT' && type !== 'CALL') || (side !== 'LONG' && side !== 'SHORT')) {
    return { label: plain, title: undefined, mechanics: undefined };
  }
  const bullish = (type === 'CALL') === (side === 'LONG');
  const kind = type === 'PUT' ? 'put' : 'call';
  const what = isSpread(t)
    ? `${kind} ${side === 'SHORT' ? 'credit' : 'debit'} spread`
    : `${side === 'SHORT' ? 'sold' : 'bought'} ${kind}`;
  const mechanics = `${what.charAt(0).toUpperCase()}${what.slice(1)}`;
  return {
    label: bullish ? 'Bullish' : 'Bearish',
    mechanics,
    title: `${mechanics} (stored side: ${plain})`,
  };
}

export const sideLabel = (t) => sideInfo(t).label;
