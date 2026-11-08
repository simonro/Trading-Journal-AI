// The header's account selection is a list of account ids. An empty list means
// All Accounts, so an account created later is included without re-selecting.

// The value pages send as ?account_id=: null (all), one id, or "1,4".
export function accountFilter(ids) {
  if (!ids.length) return null;
  return ids.length === 1 ? ids[0] : ids.join(',');
}

// One account, or null when the selection is all accounts or several of them.
export function singleAccountId(ids) {
  return ids.length === 1 ? ids[0] : null;
}

export function selectionLabel(accounts, ids) {
  const picked = accounts.filter(a => ids.includes(a.id));
  if (!picked.length) return 'All Accounts';
  if (picked.length <= 2) return picked.map(a => a.name).join(' + ');
  return `${picked.length} accounts`;
}
