# Trading Journal AI

[![CI](https://github.com/simonro/Trading-Journal-AI/actions/workflows/ci.yml/badge.svg)](https://github.com/simonro/Trading-Journal-AI/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/simonro/Trading-Journal-AI)](https://github.com/simonro/Trading-Journal-AI/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A trading journal that runs on your own machine. Import your broker's CSV and it rebuilds your
round-trip trades, tracks the numbers that matter (win rate, profit factor, expectancy, drawdown,
MFE/MAE, exit efficiency), and, if you want it to, uses Claude to read your trading diary and grade
your days on process.

- **Your data stays on your computer.** One SQLite file, no account, no telemetry.
- **The numbers are not AI.** Trade grouping, P&L, fees and statistics are plain code with tests.
  AI is an optional coaching layer on top.
- **Stocks, options and futures**, with partial fills, scale-ins and shorts grouped automatically.
- **Thinkorswim, Interactive Brokers and Robinhood** importers, plus a template for any other broker.
- **Free and MIT licensed.** No paid tier.

**[Quick start](#quick-start)** · **[Watch the walkthrough](https://www.youtube.com/watch?v=LTR4HOfS_hc)** · **[Releases](https://github.com/simonro/Trading-Journal-AI/releases)** · **[Privacy](#privacy-and-your-data)**

**What this is not:** Not financial advice. Not a signal service. Every screenshot below is the
synthetic demo seed, not anyone's real trades.

![The dashboard: net P&L over a live equity curve, every session as one strip, measures against your goals, and the month beside your recent trades](docs/screenshot-dashboard.png)

## Privacy and your data

Trading Journal AI is designed as a **local-first application**.

Your trading journal database, imported broker data, notes, and uploaded files are stored locally on your computer. Trading Journal AI does not require an account and does not include telemetry or analytics that send your usage data back to the project. The backend listens on `localhost` only.

### What stays local

- Your trading database
- Imported broker CSV files
- Trade history and performance data
- Journal entries and notes
- Uploaded diary files and images
- Application settings

### Optional external services

Some features use third-party APIs and are completely optional.

**Claude / Anthropic**

When you use AI analysis or the Brain assistant, the information required to answer your request may be sent to Anthropic's API. This can include trade information, journal context, or images you explicitly ask the AI to analyze.

The core journal, trade reconstruction, P&L calculations, reports, and statistics do not require Claude.

**Market data (Alpaca)**

If you add Alpaca keys, the trade chart asks Alpaca for price bars: the ticker and the date range,
nothing about your trades or account.

### API keys

API keys are configured locally and should never be committed to GitHub.

Do not share or commit:

- `.env` files
- API keys or secrets
- Local database files
- Raw broker statements containing personal information
- Screenshots containing account numbers or other sensitive financial information

The repository's `.gitignore` is configured to exclude common local data and credential files.

### Deterministic calculations

AI is not used to calculate your trading results.

Trade reconstruction, P&L, commissions, statistics, and other core trading calculations are handled by deterministic application code. AI features are an optional analysis and coaching layer on top of those calculations.

## What's inside

- **Dashboard**: your net P&L over a live equity curve (hover it for any day's running balance),
  every session in the period as a single strip you can scrub, measures against your own goals,
  the month beside your recent trades and open positions, and a tabbed breakdown by time of day,
  day of week and strategy
- **Trade View**: every trade with executions, playbook setup tags, MFE/MAE and exit efficiency, its AI analysis and an intraday chart with your fills on it
- **Reports**: breakdowns by day of week, time of day, hold time, setup, grade, symbol, side, emotion,
  plus a Sources & Tags tab that scores where your ideas come from. One switch flips the whole page
  between bars and full numeric tables, and rows under ten trades are marked thin so a one-trade
  strategy at 100% cannot sit at the top
- **Diary**: upload handwritten notes, screenshots, or typed text; Claude extracts strategy, stops, R-multiples, emotional state, and mistakes, and matches them to your actual trades
- **Day Review**: the session drawn as one picture, running P&L from the open to the close with every
  trade marked where you entered it, plus an AI coaching report graded on process rather than P&L.
  Each trade's grade carries the reason it was given
- **Brain**: a chat that answers questions against your full trading history
- **Settings**: the name library. Strategies, sources and tags in one place, with rename, merge and
  delete. Merging rewrites every trade that used the old name and remembers it, so the next diary
  analysis that produces the duplicate saves it under the name you kept
- **Import**: Thinkorswim account statement CSV, Interactive Brokers (IBKR) Activity Statement CSV and Robinhood activity report CSV, with a broker dropdown (auto-detect by default). Any other broker imports through a generic CSV template, one row per fill

## Screenshots

**Trade View.** Every trade with its executions, MFE/MAE and exit efficiency, the realized R, and the
setup you tagged. Click any row to open the full trade.

![Trade View: the trade log with setups, excursion and R columns](docs/screenshot-trade-view.png)

**Trade Details.** The executions on one trade, the planned and realized R, the stop and target you
wrote before entry, and an intraday chart with your fills marked on it. Charts open on the trade
day's session; the legend entries switch layers on and off.

![Trade Details: executions, R-multiple, stop and target, and an intraday chart with fills](docs/screenshot-trade-detail.png)

**Day Review.** The session as one picture: running P&L from the open to the close with every trade
marked where you entered it. The day's measures sit under it, each set against your all-time figure:
win rate, profit factor, average win, average per trade against your expectancy, exit efficiency,
and how much was given back from the session high. Underneath, an AI coaching report graded on process rather than P&L,
which reads your trades and your diary together and is willing to tell you a profitable day was
badly run.

![Day Review: the session drawn as running P&L with each trade marked, day measures, and an AI coaching report](docs/screenshot-day-review.png)

**Reports.** Equity curve, drawdown against the running peak, and breakdowns by setup, timing,
execution, symbol, source, tag and psychology.

![Reports: equity curve, drawdown from peak, and monthly performance](docs/screenshot-reports.png)

**Settings.** The vocabulary the journal uses. Rename a strategy, merge two that mean the same thing,
or delete one and reassign its trades.

![Settings: the strategy, source and tag library with rename, merge and delete](docs/screenshot-settings.png)

## Watch the walkthrough

[![Watch: I built my own AI trading journal and stopped paying monthly](docs/video-thumbnail.png)](https://www.youtube.com/watch?v=LTR4HOfS_hc)

A full tour of the app, an install from an empty folder, and three prompts that change it while
the camera is running. Every prompt used in the video is in the video description, ready to paste.

## Quick start

Requirements: [Python 3.11+](https://www.python.org/downloads/) and [Node.js 18+](https://nodejs.org/) (the current LTS is recommended).

**Windows, two steps:** download or clone the repo, then double-click

1. `setup.bat`, once. It creates the Python environment, installs everything and creates
   `backend\.env` for your optional keys.
2. `launch.bat`, every time. Then open http://localhost:3010

**Manual setup (Mac, Linux, or if you prefer):**

```bash
# 1. Python environment + backend dependencies
python -m venv .venv
.venv\Scripts\activate         # Windows (source .venv/bin/activate on Mac/Linux)
pip install -r backend/requirements.txt

# 2. Frontend dependencies
cd frontend
npm ci
cd ..

# 3. Run both (Windows; launch.bat picks up .venv automatically)
launch.bat
```

`launch.bat` starts the FastAPI backend on http://localhost:8010 and the React frontend on http://localhost:3010. On Mac/Linux run them manually: `python -m uvicorn main:app --reload --port 8010` from `backend/`, and `PORT=3010 npm start` from `frontend/`.

To run them on other ports, tell each side about the other: `REACT_APP_API_URL` for the frontend,
and, only if the frontend is not on localhost, `FRONTEND_ORIGINS` (comma separated) for the
backend's CORS allow list. Any localhost port is accepted without configuration.

Every install starts empty: no accounts, no trades, no demo data. Add your first account in the app,
then import your broker's statement on the Import page.

## Updating to a new release

Your trades live in `backend/trading_journal.db` and your keys in `backend/.env`. Neither is part of
a release, so an update never touches them. New tables are created on the first start and nothing
existing is rewritten.

**If you cloned with git:**

```bash
git pull
pip install -r backend/requirements.txt   # only if requirements changed
cd frontend && npm install && cd ..       # only if package.json changed
launch.bat
```

**If you downloaded the ZIP:** unzip the new version into a new folder, then copy your two files
across before starting it:

```bash
copy old-folder\backend\.env               new-folder\backend\.env
copy old-folder\backend\trading_journal.db new-folder\backend\trading_journal.db
```

Then run `npm install` in `frontend/` once and start it with `launch.bat`.

Back up `trading_journal.db` first if you have trades you care about. It is a single SQLite file, so
copying it somewhere safe is the whole backup.

To check what changed, see the [releases page](https://github.com/simonro/Trading-Journal-AI/releases).

## Importing from a broker that is not listed

Thinkorswim, Interactive Brokers and Robinhood have dedicated importers. For anything else, use the generic
template: one row per fill, which the journal groups into round-trip trades exactly like a broker
import. On the Import page, open **Broker not listed?** to download it.

- Blank template: [`frontend/public/templates/generic_trades_template.csv`](frontend/public/templates/generic_trades_template.csv)
- Worked example: [`frontend/public/templates/generic_trades_example.csv`](frontend/public/templates/generic_trades_example.csv)
  (a long with a partial exit, a short, an option and a micro future)

| Column | Needed | What goes in it |
|---|---|---|
| `date` | Required | `YYYY-MM-DD`, or `MM/DD/YYYY`. Day-first dates are refused because `03/04` is ambiguous |
| `time` | Required | 24 hour `HH:MM` or `HH:MM:SS`, or `1:05 PM` |
| `symbol` | Required | `AAPL`. Futures start with a slash: `/MESU26` |
| `side` | Required | `BUY` or `SELL`. `BUY TO COVER`, `SELL SHORT`, `BOT` and `SOLD` work too |
| `quantity` | Required | Shares or contracts, always positive |
| `price` | Required | Fill price per share or per contract |
| `commission` | Optional | Fees for that fill. Blank means 0 |
| `asset_type` | Optional | `STOCK` (the default), `OPTION` or `FUTURE` |
| `expiry`, `strike`, `put_call` | Options | `2026-08-28`, `765`, `CALL` or `PUT`. Options use a 100 multiplier |
| `multiplier` | Optional | Point value for a future the app does not know, for example `50` for `/ES` |

Columns can be in any order, common names such as `Ticker`, `Qty` and `Fees` are recognised, and
extra columns are ignored, so an export that already uses these headers imports without editing.

If any row cannot be read, **nothing is imported** and the error names the line and the problem. A
silently skipped fill would change every P&L figure after it, so the importer refuses instead.

Adding a dedicated parser for your broker is welcome: open an issue with a sample export that has
the account numbers and personal details removed, or send a pull request against
`backend/csv_parser.py`. Every parser only has to produce execution rows; grouping, duplicate
detection and P&L are shared.

## Environment variables

Copy `.env.example` to `backend/.env` and fill in the keys yourself, or ask Claude Code to do it:

**Add your API keys:**

> Copy .env.example to backend/.env. Then ask me for my Anthropic API key, and after that my Alpaca key ID and secret key, one at a time. Write each one into the matching line in backend/.env exactly as I paste it. Do not print any of them back to me or log them anywhere else. When all three are in, tell me to restart launch.bat.

Everything is optional; the app runs without any keys and tells you exactly which feature each missing key disables.

| Variable | Enables |
|---|---|
| `ANTHROPIC_API_KEY` | Diary analysis, Day Review, Weekly Summary, Insights, and the Brain chat |
| `APCA_API_KEY_ID` / `APCA_API_SECRET_KEY` | Intraday price charts on each trade (free Alpaca account works) |
| `ALPACA_DATA_FEED` | Optional, defaults to `iex` (free-tier data). Set to `sip` only if your key has a paid market-data subscription. |
| `FRONTEND_ORIGINS` | Optional. Any `localhost`/`127.0.0.1` port is already accepted; add comma-separated origins here only for a frontend served from another host. |

## Make it yours with Claude Code

This whole app was built by describing problems to Claude Code, one session at a time: "my spreadsheet can't group partial fills", "I want my handwritten diary matched to my trades", "show me when in the day I lose money". No web framework expertise required to get here, and none required to make it yours.

This repo is meant to be adapted, and the fastest way is to point Claude Code at it. A ready-to-paste prompt:

**Adapt the importer to your broker:**

> Read backend/csv_parser.py. It parses Thinkorswim account statement CSVs and Interactive Brokers Activity Statement CSVs: each broker parser reads execution rows (date, time, buy/sell, quantity, symbol, price, fees) into a common execution dict shape (action BOT/SOLD, qty, ticker, price, instrument_type, date, iso_date, time, amount, commission) and hands them to build_trades_from_executions, which groups them into round-trip trades by position open/close cycles and de-duplicates against the database. Here is a sample CSV export from my broker (pasted below / attached). Write a parse_<broker>_csv function for my broker's format following parse_ibkr_csv as the template, register it in BROKER_PARSERS and BROKER_LABELS, teach detect_broker to recognise the file, and add the broker to the BROKERS dropdown in frontend/src/components/Import.js with its export instructions. Keep the duplicate-detection fingerprints working.

## Contributing

Bug reports, broker samples and pull requests are welcome. [CONTRIBUTING.md](CONTRIBUTING.md) covers
the setup, the tests to run and how to add a broker importer. Please report security problems
privately, as described in [SECURITY.md](SECURITY.md), not in a public issue.

## Who made this

Simon, a day trader. I built this because the journals I was paying for made me fill in forms hours
after the trade, and I never kept it up. I am not a programmer; this was built by describing problems
to Claude Code, and the prompts are in the video description.

- YouTube: [@tapetoedge](https://www.youtube.com/@tapetoedge), where I show what I build and how
- X: [@tapetoedge](https://x.com/tapetoedge)
- Newsletter: [tape-to-edge.beehiiv.com](https://tape-to-edge.beehiiv.com), a free community for
  traders sharing the tools we make and the strategies we run. One email a week. Nothing for sale.

I take no affiliate money from any broker or tool, and this app has no paid tier, no account and no
telemetry. If it is useful, fork it.

Educational content, not financial advice. I have no affiliate relationship with anything I show or
use, ever.

## License

MIT. See [LICENSE](LICENSE).
