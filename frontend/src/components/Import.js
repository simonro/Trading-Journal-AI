import { useState, useRef, useEffect } from 'react';
import { Upload, FileText, Image, CheckCircle, AlertCircle } from 'lucide-react';
import { importApi } from '../api';
import { PageHeader } from './ui';

// Brokers the backend can parse (keys match csv_parser.BROKER_PARSERS).
// 'auto' lets the server sniff the format from the file's first lines.
const BROKERS = [
  { value: 'auto', label: 'Auto-detect' },
  { value: 'thinkorswim', label: 'Thinkorswim (Schwab)' },
  { value: 'ibkr', label: 'Interactive Brokers (IBKR)' },
  { value: 'robinhood', label: 'Robinhood' },
  { value: 'generic', label: 'Other broker (generic template)' },
];

// Served from frontend/public/templates, so they download straight from the app.
const TEMPLATE_URL = '/templates/generic_trades_template.csv';
const EXAMPLE_URL = '/templates/generic_trades_example.csv';

const BROKER_HELP = {
  auto: 'Pick a broker above, or leave Auto-detect and the importer will recognise a Thinkorswim account statement, an IBKR Activity Statement or a Robinhood activity report.',
  thinkorswim: <>Export from Thinkorswim desktop: <em>Monitor → Account Statement → export icon → Export to File (CSV)</em></>,
  ibkr: <>Export from IBKR Client Portal: <em>Performance &amp; Reports → Statements → Activity → pick the period → Download as CSV</em></>,
  robinhood: <>Export from Robinhood (web or app): <em>Account → Reports and statements → Reports → Generate new report</em>, pick the date range, then download the CSV. Robinhood reports have no execution times, so fills on the same day keep the report's order.</>,
  generic: <>Copy your fills into the template, one row per execution. Buys and sells of the same symbol are grouped into round-trip trades automatically, the same way as a broker import.</>,
};

const BROKER_DROP_LABEL = {
  auto: 'Drop your broker CSV (Thinkorswim, IBKR or Robinhood)',
  thinkorswim: 'Drop Thinkorswim account statement CSV',
  ibkr: 'Drop IBKR Activity Statement CSV',
  robinhood: 'Drop Robinhood activity report CSV',
  generic: 'Drop your filled-in generic template CSV',
};

// Map the free-text broker stored on an account to a dropdown value.
function brokerFromAccount(account) {
  const b = (account?.broker || '').toLowerCase();
  if (/ibkr|interactive/.test(b)) return 'ibkr';
  if (/thinkorswim|tos|schwab/.test(b)) return 'thinkorswim';
  if (/robinhood/.test(b)) return 'robinhood';
  return 'auto';
}

/* Shown on every broker choice, so someone whose broker is missing finds the
   way in before giving up. Expands into the column reference when the generic
   template is the selected broker. */
const TEMPLATE_COLUMNS = [
  ['date', 'Required', 'YYYY-MM-DD, or MM/DD/YYYY'],
  ['time', 'Required', '24 hour HH:MM or HH:MM:SS, or 1:05 PM'],
  ['symbol', 'Required', 'AAPL. Futures start with a slash: /MESU26'],
  ['side', 'Required', 'BUY or SELL. BUY TO COVER and SELL SHORT work too'],
  ['quantity', 'Required', 'Shares or contracts, always positive'],
  ['price', 'Required', 'Fill price per share or per contract'],
  ['commission', 'Optional', 'Fees for that fill. Blank means 0'],
  ['asset_type', 'Optional', 'STOCK (default), OPTION or FUTURE'],
  ['expiry, strike, put_call', 'Options', '2026-08-28, 765, CALL or PUT'],
  ['multiplier', 'Optional', 'Point value for a future the app does not know'],
];

function GenericTemplateTip({ open, onUse }) {
  return (
    <div className="notice" style={{ marginTop: 16, display: 'block' }}>
      <div style={{ fontSize: 13.5, color: 'var(--text-primary)', fontWeight: 600, marginBottom: 4 }}>
        Broker not listed?
      </div>
      <div style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.55 }}>
        Download the{' '}
        <a href={TEMPLATE_URL} download="generic_trades_template.csv">blank template</a>
        {' '}or a{' '}
        <a href={EXAMPLE_URL} download="generic_trades_example.csv">filled-in example</a>
        , paste your fills in from any broker export or spreadsheet, and import it with
        {' '}
        {open ? <strong>Other broker (generic template)</strong> : (
          <button type="button" className="btn btn-ghost btn-sm" onClick={onUse} style={{ padding: '0 4px', verticalAlign: 'baseline' }}>
            Other broker (generic template)
          </button>
        )}
        . Columns can be in any order, common names like Ticker, Qty or Fees are recognised, and extra columns are ignored.
      </div>

      {open && (
        <div className="scroll-x" style={{ marginTop: 12 }}>
          <table style={{ fontSize: 12.5 }}>
            <caption className="sr-only">Generic template columns</caption>
            <thead>
              <tr><th>Column</th><th>Needed</th><th>What goes in it</th></tr>
            </thead>
            <tbody>
              {TEMPLATE_COLUMNS.map(([name, need, what]) => (
                <tr key={name}>
                  <td style={{ whiteSpace: 'nowrap', fontFamily: 'var(--font-mono)', textAlign: 'left' }}>{name}</td>
                  <td className="text-muted" style={{ whiteSpace: 'nowrap' }}>{need}</td>
                  <td>{what}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div style={{ fontSize: 12.5, color: 'var(--text-secondary)', marginTop: 8 }}>
            If any row cannot be read, nothing is imported and the error names the line, so a
            missing fill can never quietly change your P&amp;L.
          </div>
        </div>
      )}
    </div>
  );
}

function TextPreview({ file }) {
  const [text, setText] = useState('');
  useEffect(() => {
    const reader = new FileReader();
    reader.onload = e => setText(e.target.result);
    reader.readAsText(file);
  }, [file]);
  return (
    <div style={{ background: 'var(--surface-inset)', borderRadius: 'var(--radius-md)', border: '1px solid var(--divider)', padding: 12, fontSize: 13, color: 'var(--text-primary)', fontFamily: 'var(--font-mono)', maxHeight: 180, overflowY: 'auto', whiteSpace: 'pre-wrap', lineHeight: 1.5 }}>
      {text || 'Loading preview...'}
    </div>
  );
}

function DropZone({ label, accept, onFile, file, icon: Icon }) {
  const [active, setActive] = useState(false);
  const inputRef = useRef();

  const handleDrop = (e) => {
    e.preventDefault();
    setActive(false);
    const f = e.dataTransfer.files[0];
    if (f) onFile(f);
  };

  return (
    <div
      className={`dropzone ${active ? 'active' : ''}`}
      onDragOver={(e) => { e.preventDefault(); setActive(true); }}
      onDragLeave={() => setActive(false)}
      onDrop={handleDrop}
      onClick={() => inputRef.current.click()}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); inputRef.current.click(); } }}
      role="button"
      tabIndex={0}
      aria-label={file ? `Selected file ${file.name}. Choose a different file` : `${label}. Press Enter to browse`}
    >
      <input ref={inputRef} type="file" accept={accept} style={{ display: 'none' }} tabIndex={-1} onChange={e => onFile(e.target.files[0])} />
      {file ? (
        <div>
          <Icon size={24} style={{ marginBottom: 8, color: 'var(--accent-line)' }} />
          <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{file.name}</div>
          <div className="num" style={{ fontSize: 13, marginTop: 4 }}>{(file.size / 1024).toFixed(0)} KB</div>
        </div>
      ) : (
        <div>
          <Icon size={28} style={{ marginBottom: 10 }} />
          <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{label}</div>
          <div style={{ fontSize: 13, marginTop: 6 }}>Drag and drop, or click to browse</div>
        </div>
      )}
    </div>
  );
}

export default function Import({ accounts, accountId }) {
  // CSV import state
  const [csvFile, setCsvFile] = useState(null);
  const [csvAccountId, setCsvAccountId] = useState(accountId || '');
  const [csvBroker, setCsvBroker] = useState(() => brokerFromAccount(accounts.find(a => a.id === accountId)));
  const [importing, setImporting] = useState(false);
  const [csvResult, setCsvResult] = useState(null);
  const [csvError, setCsvError] = useState(null);

  // Diary upload state
  const [diaryFile, setDiaryFile] = useState(null);
  const [diaryDate, setDiaryDate] = useState(new Date().toISOString().slice(0, 10));
  const [diaryAccountId, setDiaryAccountId] = useState(accountId || '');
  const [analyzing, setAnalyzing] = useState(false);
  const [diaryResult, setDiaryResult] = useState(null);
  const [diaryError, setDiaryError] = useState(null);

  const handleCsvImport = async () => {
    if (!csvFile || !csvAccountId) {
      setCsvError('Please select a file and an account.');
      return;
    }
    setImporting(true);
    setCsvResult(null);
    setCsvError(null);
    const fd = new FormData();
    fd.append('file', csvFile);
    fd.append('account_id', csvAccountId);
    fd.append('broker', csvBroker);
    try {
      const res = await importApi.importCsv(fd);
      setCsvResult(res.data);
    } catch (e) {
      setCsvError(e.response?.data?.error || e.message);
    } finally {
      setImporting(false);
    }
  };

  const handleDiaryUpload = async () => {
    if (!diaryFile || !diaryAccountId || !diaryDate) {
      setDiaryError('Please select an image, account, and date.');
      return;
    }
    setAnalyzing(true);
    setDiaryResult(null);
    setDiaryError(null);
    const fd = new FormData();
    fd.append('file', diaryFile);
    fd.append('account_id', diaryAccountId);
    fd.append('date', diaryDate);
    try {
      const res = await importApi.uploadDiary(fd);
      setDiaryResult(res.data);
      if (res.data.analysis_error) {
        setDiaryError('Diary saved, but AI analysis failed: ' + res.data.analysis_error);
      }
    } catch (e) {
      setDiaryError(e.response?.data?.error || e.message);
    } finally {
      setAnalyzing(false);
    }
  };

  return (
    <div>
      <PageHeader title="Import" subtitle="Bring in your broker executions, or have Claude read a trading diary." />

      <div className="grid-2">

        {/* CSV Import */}
        <section className="card">
          <h2 className="section-title" style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
            <FileText size={18} color="var(--accent-line)" aria-hidden="true" />
            Import Broker CSV
          </h2>

          <DropZone
            label={BROKER_DROP_LABEL[csvBroker]}
            accept=".csv"
            onFile={setCsvFile}
            file={csvFile}
            icon={FileText}
          />

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12, marginTop: 16 }}>
            <div>
              <label className="field-label" htmlFor="imp-csv-broker">Broker</label>
              <select
                id="imp-csv-broker"
                value={csvBroker}
                onChange={e => setCsvBroker(e.target.value)}
                style={{ width: '100%' }}
              >
                {BROKERS.map(b => (
                  <option key={b.value} value={b.value}>{b.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="field-label" htmlFor="imp-csv-account">Account</label>
              <select
                id="imp-csv-account"
                value={csvAccountId}
                onChange={e => {
                  setCsvAccountId(e.target.value);
                  // Follow the account's broker when it has a recognisable one.
                  const acct = accounts.find(a => String(a.id) === e.target.value);
                  const guess = brokerFromAccount(acct);
                  if (guess !== 'auto') setCsvBroker(guess);
                }}
                style={{ width: '100%' }}
                required
              >
                <option value="">Select account...</option>
                {accounts.map(a => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </select>
            </div>
          </div>

          <button
            className="btn btn-primary"
            style={{ width: '100%', justifyContent: 'center', marginTop: 16 }}
            onClick={handleCsvImport}
            disabled={importing || !csvFile || !csvAccountId}
          >
            {importing ? <><span className="spinner" style={{ width: 16, height: 16 }} /> Importing...</> : <><Upload size={16} /> Import Trades</>}
          </button>

          {csvResult && (
            <div className="notice pos" role="status" style={{ marginTop: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--result-pos)', fontWeight: 600, marginBottom: 4 }}>
                <CheckCircle size={16} /> Import Complete
              </div>
              <div style={{ fontSize: 14 }}>{csvResult.message}</div>
              {csvResult.errors?.length > 0 && (
                <div style={{ fontSize: 13, color: 'var(--result-neg)', marginTop: 4 }}>
                  {csvResult.errors.length} DB error(s)
                </div>
              )}
            </div>
          )}

          {csvError && (
            <div className="notice neg" role="alert" style={{ marginTop: 12 }}>
              <AlertCircle size={14} style={{ marginRight: 6, verticalAlign: 'middle' }} />{csvError}
            </div>
          )}

          <div style={{ marginTop: 16, fontSize: 13, color: 'var(--text-secondary)' }}>
            {BROKER_HELP[csvBroker]}
          </div>

          <GenericTemplateTip open={csvBroker === 'generic'} onUse={() => setCsvBroker('generic')} />
        </section>

        {/* Diary Upload */}
        <section className="card">
          <h2 className="section-title" style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
            <Image size={18} color="var(--accent-line)" aria-hidden="true" />
            Analyze Trading Diary
          </h2>

          {diaryFile ? (
            <div style={{ marginBottom: 12 }}>
              {/\.(txt|csv)$/i.test(diaryFile.name) ? (
                <TextPreview file={diaryFile} />
              ) : /\.(heic|heif)$/i.test(diaryFile.name) ? (
                // Browsers cannot render HEIC; the server converts it to JPEG on upload.
                <div style={{ padding: 12, border: '1px solid var(--divider)', borderRadius: 'var(--radius-md)', fontSize: 14, color: 'var(--text-secondary)' }}>
                  {diaryFile.name} (iPhone photo, will be converted on upload)
                </div>
              ) : (
                <img src={URL.createObjectURL(diaryFile)} alt="Diary preview"
                  style={{ maxWidth: '100%', maxHeight: 180, borderRadius: 'var(--radius-md)', border: '1px solid var(--divider)', display: 'block' }} />
              )}
              <button type="button" className="btn btn-ghost btn-sm" style={{ marginTop: 8 }} onClick={() => setDiaryFile(null)}>Remove</button>
            </div>
          ) : (
            <DropZone
              label="Drop a photo of handwritten notes, a screenshot, or a .txt / .csv file"
              accept=".png,.jpg,.jpeg,.webp,.gif,.heic,.heif,.txt,.csv"
              onFile={setDiaryFile}
              file={null}
              icon={Image}
            />
          )}

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12, marginTop: 16 }}>
            <div>
              <label className="field-label" htmlFor="imp-diary-date">Date</label>
              <input
                id="imp-diary-date"
                type="date"
                value={diaryDate}
                onChange={e => setDiaryDate(e.target.value)}
                style={{ width: '100%' }}
              />
            </div>
            <div>
              <label className="field-label" htmlFor="imp-diary-account">Account</label>
              <select
                id="imp-diary-account"
                value={diaryAccountId}
                onChange={e => setDiaryAccountId(e.target.value)}
                style={{ width: '100%' }}
              >
                <option value="">Select account...</option>
                {accounts.map(a => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </select>
            </div>
          </div>

          <button
            className="btn btn-primary"
            style={{ width: '100%', justifyContent: 'center', marginTop: 16 }}
            onClick={handleDiaryUpload}
            disabled={analyzing || !diaryFile || !diaryAccountId || !diaryDate}
          >
            {analyzing
              ? <><span className="spinner" style={{ width: 16, height: 16 }} /> Analyzing with Claude AI...</>
              : <><Upload size={16} /> Analyze Diary</>
            }
          </button>

          {analyzing && (
            <div role="status" style={{ marginTop: 10, fontSize: 13, color: 'var(--text-secondary)', textAlign: 'center' }}>
              Claude is reading your diary. This takes 10 to 20 seconds...
            </div>
          )}

          {diaryResult && !diaryResult.analysis_error && (
            <div className="notice pos" role="status" style={{ marginTop: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--result-pos)', fontWeight: 600, marginBottom: 4 }}>
                <CheckCircle size={16} /> Analysis Complete
              </div>
              {diaryResult.trade_count != null && (
                <div style={{ fontSize: 14 }}>Found {diaryResult.trade_count} trade(s) in your diary.</div>
              )}
              <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 4 }}>View full analysis in the Diary page.</div>
            </div>
          )}

          {diaryError && (
            <div className="notice neg" role="alert" style={{ marginTop: 12 }}>
              <AlertCircle size={14} style={{ marginRight: 6, verticalAlign: 'middle' }} />{diaryError}
            </div>
          )}

          <div style={{ marginTop: 16, fontSize: 13, color: 'var(--text-secondary)' }}>
            Claude AI will read your handwritten or typed notes and extract strategy, stops, R-multiples, emotional state, and more.
          </div>
        </section>
      </div>
    </div>
  );
}
