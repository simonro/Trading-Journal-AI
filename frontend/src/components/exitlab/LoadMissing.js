import { useCallback, useEffect, useRef, useState } from 'react';
import { exitlabApi } from '../../api';
import { errorText, Notice } from './shared';

const POLL_MS = 2000;
const trades = n => `${n} trade${n === 1 ? '' : 's'}`;

// "Load missing prices (N trades)": one background job on the local API, polled every 2 s.
// N comes from the history response: trades excluded for nothing but missing prices.
export default function LoadMissing({ accountId, count = 0, onFinished }) {
  const [job, setJob] = useState(null), [error, setError] = useState('');
  const [watched, setWatched] = useState(false);
  const timer = useRef(null), alive = useRef(true);
  const finished = useRef(onFinished);
  finished.current = onFinished;

  const poll = useCallback(async () => {
    clearTimeout(timer.current);
    try {
      const { data } = await exitlabApi.loadMissingStatus();
      if (!alive.current) return;
      setJob(data); setError('');
      if (data.running) timer.current = setTimeout(poll, POLL_MS);
      else await finished.current?.();
    } catch (e) {
      if (alive.current) setError(`Could not read loading progress. ${errorText(e)}`);
    }
  }, []);

  const pending = count > 0;
  useEffect(() => {
    alive.current = true;
    // A job started earlier (or in another tab) keeps reporting here.
    if (pending) exitlabApi.loadMissingStatus().then(({ data }) => {
      if (alive.current && data.running && data.account_id === accountId) { setJob(data); setWatched(true); timer.current = setTimeout(poll, POLL_MS); }
    }).catch(() => { /* The button still works; progress shows once a job starts. */ });
    return () => { alive.current = false; clearTimeout(timer.current); };
  }, [accountId, pending, poll]);

  async function start() {
    setError(''); setWatched(true);
    try {
      const { data } = await exitlabApi.loadMissing(accountId);
      if (!alive.current) return;
      setJob(data);
      if (data.running) timer.current = setTimeout(poll, POLL_MS);
      else await finished.current?.();
    } catch (e) {
      if (e.response?.status === 409) return poll();
      if (alive.current) setError(errorText(e));
    }
  }

  const running = Boolean(job?.running);
  const done = job && watched && !running ? job : null;
  if (!running && !done && !error && count <= 0) return null;
  return <div className="xl-load-missing">
    {!running && count > 0 && <button type="button" className="btn btn-secondary btn-sm xl-load-missing-btn" onClick={start}>Load missing prices ({trades(count)})</button>}
    {running && <div className="xl-progress-wrap" role="status">
      <span className="xl-progress-label num">Loading prices {job.done} of {job.total}</span>
      <div className="xl-progress" role="progressbar" aria-label="Loading prices" aria-valuemin={0} aria-valuemax={job.total} aria-valuenow={job.done}>
        <span style={{ transform: `scaleX(${job.total ? job.done / job.total : 0})` }} />
      </div>
    </div>}
    {done && done.total > 0 && <p className="xl-load-result" role="status">Loaded prices for {done.total - done.failed} of {trades(done.total)}.{done.failed > 0 && ` ${done.failed} could not load:`}</p>}
    {done && done.total === 0 && <p className="xl-load-result" role="status">No trades are missing prices that can be loaded now.</p>}
    {done?.failures?.length > 0 && <ul className="xl-load-failures">{done.failures.map((f, i) => <li key={`${f.ticker}-${f.date}-${i}`}><span className="num">{f.ticker} / {f.date}</span><span>{f.reason}</span></li>)}</ul>}
    {error && <Notice error>{error} {running && <button type="button" className="btn btn-secondary btn-sm" onClick={poll}>Check again</button>}</Notice>}
  </div>;
}
