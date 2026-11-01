import { useEffect, useState } from 'react';
import { updateApi } from '../api';

// Notify only: the backend asked GitHub once (at most daily) whether a newer release exists.
// Nothing is downloaded or installed; the banner says how to update and can be dismissed
// for that version.
const DISMISS_KEY = 'update-banner-dismissed';

export default function UpdateBanner() {
  const [update, setUpdate] = useState(null);
  const [dismissed, setDismissed] = useState(() => {
    try { return localStorage.getItem(DISMISS_KEY); } catch { return null; }
  });

  useEffect(() => {
    let alive = true;
    updateApi.get()
      .then(r => { if (alive && r.data?.available) setUpdate(r.data); })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  if (!update || dismissed === update.latest) return null;

  const dismiss = () => {
    try { localStorage.setItem(DISMISS_KEY, update.latest); } catch { /* private window */ }
    setDismissed(update.latest);
  };

  return (
    <div className="update-banner" role="status">
      <div>
        <strong>Version {update.latest} is available</strong> (you have {update.current}).{' '}
        <a href={update.url} target="_blank" rel="noopener noreferrer">Release notes</a>
        <div className="update-banner-steps">
          To update: <code>git pull</code>, then run <code>setup.bat</code>. Downloaded the ZIP? Unzip the
          new version and copy <code>backend/.env</code> and <code>backend/trading_journal.db</code> into it.
        </div>
      </div>
      <button type="button" className="update-banner-close" onClick={dismiss} aria-label="Dismiss update notice">×</button>
    </div>
  );
}
