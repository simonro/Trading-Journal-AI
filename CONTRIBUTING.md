# Contributing to Trading Journal AI

Thanks for helping. Bug reports, broker samples, fixes and features are all welcome. This page
covers how to set up, what to test, and what a pull request needs before it can be merged.

**Security problems:** please do not open a public issue. Follow [SECURITY.md](SECURITY.md) and use
GitHub's private vulnerability reporting.

## What the project cares about

These decide most review questions, so they are worth knowing before you start:

1. **Local first.** Everything runs on the user's machine against one SQLite file. No accounts, no
   telemetry, no cloud service. A change that sends data somewhere new, opens a network port beyond
   `localhost`, or adds a background job needs to be discussed in an issue first.
2. **The numbers are deterministic.** Trade grouping, P&L, fees and statistics are plain code, never
   an LLM. Any change to how money is calculated needs a test with the expected figures worked out.
3. **It refuses rather than guesses.** An importer that cannot read a row stops and says which line,
   instead of skipping it or assuming a value. A silently wrong number is worse than an error.
4. **Small and focused.** One pull request, one change. Large pull requests that bundle several
   features are hard to review and will usually be asked to split.

## Setting up

Requirements: Python 3.11+ and Node.js 18+ (CI uses Node 24).

On Windows, run `setup.bat` once and `launch.bat` to start. By hand:

```bash
python -m venv .venv
.venv\Scripts\activate            # source .venv/bin/activate on Mac/Linux
pip install -r backend/requirements.txt pytest
cd frontend && npm ci && cd ..
```

The backend runs on http://localhost:8010 and the frontend on http://localhost:3010.

A new install is always empty. For development only, `python scripts/seed_demo.py` fills a database
with the synthetic demo data the screenshots use; never run it on a database you keep. **Never use or
commit real trades**: not in tests, fixtures, screenshots or issues. Delete
`backend/trading_journal.db` to go back to a clean install.

## Tests

CI runs all three of these on every pull request, and they must pass:

```bash
# backend
python -m pytest backend/tests -q

# frontend: integration tests, then a production build
cd frontend
npx craco test --watchAll=false
npm run build
```

In CI, `npm run build` treats lint warnings as errors, so an unused variable or import fails the
build. Run `npx eslint src` in `frontend/` to see them locally.

What to add:

- **A fix:** a test that fails without it.
- **Anything touching imports, P&L, fees or statistics:** fixtures with hand-worked expected values.
  Cover the awkward cases that apply: partial fills, scale-ins, shorts, options (x100), futures
  point values, commissions, and importing the same file twice.
- **A UI change:** an integration test in `frontend/src/app.integration.test.js` when the behavior
  can break, and a screenshot in the pull request.

## Adding a broker importer

Every parser has one job: turn the broker's file into a list of execution rows. Grouping into
round trips, duplicate detection and P&L are shared, so a new broker needs no changes there.

1. In `backend/csv_parser.py`, write `parse_<broker>_csv(content, account_id, conn=None)`. Use
   `parse_ibkr_csv` as the template. Each execution is a dict with `action` (`BOT`/`SOLD`), `qty`,
   `ticker`, `price`, `instrument_type` (`STOCK`/`OPTION`/`FUTURE`), `date`, `iso_date`, `time`,
   `amount` (signed cash amount including the multiplier) and `commission`, plus
   `option_expiry`/`option_strike`/`option_type` for options. Hand the list to
   `build_trades_from_executions`.
2. Register it in `BROKER_PARSERS` and `BROKER_LABELS`, and teach `detect_broker` to recognise the
   file from its header.
3. Add it to `BROKERS` in `frontend/src/components/Import.js` with short export instructions.
4. Add tests in `backend/tests/` using a small sample file with **account numbers, names and real
   trades removed**. Invented prices are fine; the sample only needs the broker's exact layout.

If you just want your broker supported, open an issue with a redacted sample export and the steps
you use to download it. Until then, the generic template on the Import page works for any broker.

## Pull requests

- Branch from `main` and keep the change focused.
- Describe what changed and why, and how you tested it.
- Say so if the change adds a dependency, a network call, file access outside the app folder, or a
  database change. New dependencies need a reason.
- Database changes must keep existing databases working: new tables and columns are added on start,
  and nothing a user already has is rewritten or dropped.
- Never include `.env` files, API keys, databases or broker statements.

## Releases and the version number

The `VERSION` file at the repo root is the single source of truth for the app version. The backend
reads it, sends it as the User-Agent of the once-a-day update check, and compares it with the latest
GitHub release to decide whether to show the update notice.

To cut a release: bump `VERSION` (for example `1.5.0` to `1.6.0`) in the pull request that finishes
the release, merge it, then tag that commit `v` plus the same number (`v1.6.0`) and publish the GitHub
release from the tag. If the tag and `VERSION` disagree, users on the new release are told to update
to the version they already have, or never hear about it.

## Code style

Match the code around you. Python follows the style of `backend/`; React components follow
`frontend/src/`. Comments explain why something is done, not what the next line does. Keep
user-facing text plain and specific: say what happened and what to do next.

## Questions

Open an issue. For anything about your own data, never paste real account details.
