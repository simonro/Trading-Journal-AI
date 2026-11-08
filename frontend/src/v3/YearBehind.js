/* "The year behind it": every month, each closing where the next one opens.
   The running balance is the thing a month grid cannot tell you. */
import { useState, useEffect } from 'react';
import { reportsApi } from '../api';
import { BarRow, money, tone, MONTH_NAMES } from './parts';

export default function YearBehind({ accountId }) {
  const [months, setMonths] = useState(null);

  useEffect(() => {
    const params = {};
    if (accountId != null) params.account_id = accountId;
    let live = true;
    reportsApi.get(params)
      .then((r) => { if (live) setMonths(r.data?.by_month || []); })
      .catch(() => { if (live) setMonths([]); });
    return () => { live = false; };
  }, [accountId]);

  if (months == null) return <div className="v3-empty">Loading…</div>;
  if (!months.length) return <div className="v3-empty">No months to show yet.</div>;

  const peak = Math.max(1, ...months.map((m) => Math.abs(m.net_pnl || 0)));
  let running = 0;

  return (
    <div className="v3-scroll">
      <table className="v3-t">
        <thead>
          <tr>
            <th>Month</th>
            <th className="r">Trades</th>
            <th className="r v3-hide-s">Win rate</th>
            <th className="v3-hide-s" aria-label="Shape"><span className="v3-zero-head" aria-hidden="true"><span>Loss</span><span>0</span><span>Profit</span></span></th>
            <th className="r">Net</th>
            <th className="r">Balance</th>
          </tr>
        </thead>
        <tbody>
          {months.map((m) => {
            running += m.net_pnl || 0;
            const parts = String(m.key || '').split('-');
            const name = parts.length === 2
              ? `${MONTH_NAMES[Number(parts[1]) - 1]} ${parts[0]}`
              : (m.label || m.key);
            return (
              <tr key={m.key}>
                <td className="v3-tick">{name}</td>
                <td className="r v3-mono">{m.trades}</td>
                <td className="r v3-mono v3-hide-s v3-read">
                  {m.win_rate == null ? '—' : `${Number(m.win_rate).toFixed(0)}%`}
                </td>
                <td className="v3-hide-s">
                  <BarRow frac={Math.abs(m.net_pnl || 0) / peak} negative={(m.net_pnl || 0) < 0} diverge />
                </td>
                <td className={`r v3-mono ${tone(m.net_pnl)}`} style={{ fontWeight: 600 }}>
                  {money(m.net_pnl)}
                </td>
                <td className={`r v3-mono ${tone(running)}`}>{money(running)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
