# Exit Lab

Compare alternative exits across all completed eligible stock trades. Open Exit Lab
for history tables or a trade's Exits tab for its replay and one result per rule.
The stop comes from the journal entry's Stop Loss. Enter your stop updates that same
journal field. Imports can fall back to their own stop field if no analysis stop exists.

When a one-minute candle contains competing events, the protective stop fills first,
then targets, then indicator or time exits. Stop gaps fill at the worse open. Results
include R and actual-sized dollars, with no modeled costs. Actual dollars retain the
journal's recorded net P&L. Each rule compares against your exits on its own assigned,
eligible trades. This describes history and does not predict future results.

Advanced contains the rule builder and saved rules with Show in tables and Applies
to setups. Supported exits include R targets, breakeven/R-step and ATR trailing,
scale-outs, timed exits, VWAP touch/close, EMA close/cross and 5-minute RS/RW.
Scaled entries use average cost and begin replay at the last entry execution.
Missing required regular-hours prices remain an explicit exclusion. Load missing prices
fetches them for every such trade in one background job; Load prices requests one trade's
stock and benchmark data, including the previous session when needed. The Exits tab loads
a trade's missing prices once when it opens.

The demo is labeled **Synthetic trades, historical market prices**. It is separate
from the real journal and places no orders.

See the [engine and demo guide](../backend/exitlab/README.md) for eligibility, fill
priority, storage and API details.
