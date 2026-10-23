// Display the journal scalar; retired range columns are intentionally ignored.
export function excursionText(trade, field, digits = 2) {
  return Number.isFinite(trade[field]) ? `${trade[field].toFixed(digits)}%` : null;
}
