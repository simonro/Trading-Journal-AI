# Exit Lab

Engine: `exitlab-stop-first-v1`. All views describe the account's full historical trade
record. The package uses cached minute prices and places no orders. Price fetching is
an explicit REST action; calculations never fetch prices automatically.

## User views

The Exits tab calculates Actual, 1R target, 2R target, Hold to close, Breakeven after
+1R and applicable saved rules. Every rule has one result in R and dollars and one
fill sequence. The replay chart shows the actual executions and saved-rule fills.

Exit Lab shows each rule's assigned, eligible trades after date, symbol, setup and
side filters. Its comparison to actual exits uses those same trades. Actual covers
all base-eligible filtered trades. Samples can differ between rules. These are
descriptive trade counterfactuals, not portfolio returns or forecasts.

Advanced contains the rule builder and saved rules with Show in tables and Applies
to setups. There is no separate research comparison or fill-detail view. Definitions
and semantic hashes remain unchanged by display preferences and setup assignments.

The stop is `trade_analysis.stop_loss` matched by the trade's `trade_group`. When
that value is absent, imported rows can use their own `trades.stop_loss` or
`trades.stop` field if present. A zero or invalid non-null journal stop is an
eligibility error, not permission to substitute a different value. Enter your stop
writes `trade_analysis.stop_loss`, the same field as the journal's analysis editor,
creating its row if necessary and preserving other analysis fields. Future journal
edits immediately change replay. There is no independent stop history or selection.

## Deterministic replay

When events compete within one minute, the protective stop fills first (including
a newly activated breakeven stop), followed by targets, then indicator or time exits.

- A single state replaces the former intraminute event graph. `pnl`, `pnl_r`,
  `delta_r` and `exit_price` are scalars; `fills` is the only fill list.
- Stop touches fill at the stop. A gap through an active stop fills at the worse
  opening price. ATR updates known at that open tighten the stop before evaluation.
- R-step activations are checked in ascending order. If the same candle touches
  the newly tightened stop, it fills before later steps or competing targets.
- Targets require one tick beyond the target and fill at the target price, without
  favorable gap improvement. Multiple targets process in favorable price order.
- Close-derived indicator exits fill at the next minute's open. If that minute
  also touches an active stop or target, the priority rule above applies first.
  Scheduled time exits use the due open after protective stops and targets too.
- No action can fill more than the remaining shares. Integer positions use whole
  shares with the last tranche taking the residual; fractional positions retain
  fractional shares. Up to three tranches must total 100%.
- Any remainder closes at the regular session close. Clock rules reject 16:00;
  a later clock on an early-close session uses `session_close_clock_fallback`.
- Modeled costs and slippage are zero. Legacy cost arguments/settings are ignored.
  Actual dollars retain journal net P&L and commissions; actual R is gross price
  P&L divided by initial dollar risk. These deliberately have different cost bases.

## Scaled entries and eligibility

Completed same-session stock trades need one opening action group followed by one
closing action group with equal total quantity. Multi-minute entries use their
quantity-weighted average and total quantity. Initial dollar risk is the absolute
average-entry-to-stop distance times quantity. Every actual execution must fit its
own minute's range within one tick, and initial risk must be at least two ticks.
Adds after partial exits, side flips and incompatible executions remain excluded.

Replay, elapsed-time rules and indicator availability begin at the final entry fill
for scaled entries. Build-window stops, targets and thresholds do not execute or
activate rules; touches produce the existing advisory flag. The final fill price
is the starting price for the boundary candle, while the average is the cost basis.
For partial boundary candles, adverse stop touches count and favorable movement is
credited only when established by the fill or close. Full-minute replay begins at
the next minute. The same conservative treatment covers second-level single entries.

Complete regular-session minute coverage from the first entry through the close is
required. Missing data is an exclusion, never zero or an interpolated price. Naive
execution timestamps use America/New_York. NYSE holidays, weekends, DST and actual
early closes come from the exchange calendar. SIP and IEX caches stay separate.

## Indicators and excursions

RS/RW uses complete 5-minute stock and SPY candles, a 12-period close difference and
Wilder ATR seeded from the first true range. Seed with 13 completed candles from
today and only the needed tail of the previous regular exchange session. A favorable
state arms the rule; post-entry adverse bars count toward confirmation (default 1),
and neutral breaks that count. Missing required stock or SPY minutes through close
exclude the trade and name the first missing symbol, New York date and time.

EMA 9/20 uses a simple-average seed then alpha 2/(N+1), at the selected 1- or 5-minute
timeframe. ATR uses the latest N+1 completed regular minutes for N true ranges and
stays frozen at the replay boundary. The trailing reference is the best completed
close; updates act at the next open. Both use only the needed previous regular
session tail when current-session history is insufficient. No premarket warm-up.

VWAP uses provider minute VWAP times volume from the current session open. VWAP touch
targets the completed prior-minute session value. Longs must enter below VWAP and
shorts above it; the other side is not applicable. VWAP close and EMA close/cross
signals act at the next open. Missing VWAP has no OHLC substitute.

Excursions cover the actual hold after the final entry fill using the average cost
basis. Partial candles return conservative scalars: minimum established MFE, maximum
possible adverse excursion, and the lower efficiency implied by those extrema.
Efficiency is unavailable if favorable excursion may be zero. Replay never persists
journal metrics. Explicit recompute writes scalar metrics only for eligible real
trades, preserving old values on failure and keeping synthetic metrics untouched.
Old range columns remain in existing databases and are ignored by display/recompute.

## Storage and API

Active storage: `market_bars`, `market_bar_windows`, `exit_rules`, journal trades and
`trade_analysis`. Rule assignments use `{group: "strategy" | "auto", name}` objects
or null for All setups. Either assigned field can match. Picker strategies include
account analysis names, library strategies and active custom setups, even unused ones.
Auto codes retain raw values with friendly labels. Optional schema fields are detected.

Existing unused tables are left in place: `stop_versions`, `exit_forward_locks`,
`exit_cohorts`, `exit_holdout_periods`, `analysis_runs`, `trade_results`, and
`exit_settings`. Runtime and migrations do not read, write, migrate or recreate them.
There is no trial counter, session partition, exposure logging, promotion or lock.
History/replay work on a read-only connection after initialization.

Routes under `/api/exitlab`:

- GET/POST `/rules`, PATCH `/rules/{id}` for visibility and setup assignments.
- GET `/setups`, GET `/readiness` for account setup choices and base eligibility.
- POST `/history` with `account_id`; GET `/trades/{id}/simple` for replay.
- POST `/trades/{id}/stops` with `price`, writing the journal stop loss.
- POST `/bars/fetch`; POST `/excursions/recompute` with account and feed.
- GET/PUT `/settings` remain zero-cost compatibility stubs without storage access.

Removed routes return 404: `/comparisons`, `/trades/{id}/replay`, `/holdout/promote`,
`/forward-locks` and `/forward-locks/{id}/results`. Research selection/scope fields
are rejected by the strict history request. Old response bounds are not supported.

Historical Alpaca REST uses raw 1Min bars with feed-specific pagination and explicit
IEX fallback only for SIP 403. Credentials are read through `ALPACA_ENV_FILE` or
supported environment names, never logged or copied. Reads never spend a price budget.
Load prices requests both stock and SPY, including previous-session regular windows.
Sparse fetched windows are tracked, while explicit retry refreshes gaps.

## Demo and local verification

The demo label is **Synthetic trades, historical market prices**. Its fixed synthetic
mix of scratches, timed exits, partial exits and holds respects a generated hard stop.
Actual fills use a synthetic path within real minute ranges, not historical ticks.
Results never tune the generator. Some journal stop losses are intentionally missing.
The reset script copies cache and optionally saved rules into a fresh demo, without
reading retired tables. Existing open databases and processes must remain untouched.

`scripts/seed_exitlab_demo.py --fetch` downloads the demo's minute prices once with
your own Alpaca keys into `backend/exitlab_local.db`, the local price cache. Without
`--fetch` it works offline from that cache. `scripts/reset_exitlab_demo.py` builds a
fresh `backend/exitlab_demo.db` from the cache (`--replace` rebuilds an existing demo
and refuses a database that holds non-demo trades). `scripts/launch_exitlab_demo.bat`
runs both when needed and starts the demo on ports 8020 and 3020. Both scripts write
only `backend/exitlab_*.db` files, which `.gitignore` excludes with every `*.db`.

Backend tests: `python -m pytest backend/tests -q`. Frontend tests and build, from
`frontend/`: `npx craco test --watchAll=false` and `npm run build`.
