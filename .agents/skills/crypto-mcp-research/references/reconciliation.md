# Cross-provider reconciliation

1. **Identity:** token chain + contract or canonical asset ID; exchange instrument, pool or pair and quote conventions. If ambiguity persists, separate rows rather than merging.
2. **Temporal:** compare observations covering equivalent instants/windows; report maximum time mismatch, exchange time versus provider retrieval, missing time zones, and whether an event occurred before the price response.
3. **Units/definitions:** currency, decimal scaling, percentage versus basis points, spot versus derivative price, circulating versus diluted market cap, gross versus wash-trade-filtered volume, liquidity reserve versus depth.
4. **Provenance:** two endpoints from a common vendor/source are not independent. Prefer an independently measured observation for confirmation; otherwise label `SINGLE_SOURCE`.
5. **Conflicts:** preserve both raw numbers and sources. Record the magnitude, plausible definitional differences, and a test to resolve the mismatch. Do not select the more favorable observation because it supports the thesis.
6. **Derived metrics:** include formula, input observations, time anchors, units, and null/zero handling. Relative performance requires matched endpoints for both asset and benchmark and identical return methodology.

Example claim wording: “Provider A reported [metric, window, timestamp], while B reported [metric, window, timestamp]. The difference is unresolved, so [dependent finding] is CONFLICTED.”

## Stop / downgrade conditions

- Ambiguous contract or instrument: no combined performance series.
- Unmatched historical sample: no relative-return ranking.
- Unverified depth/fee/slippage: no execution-liquidity conclusion.
- Unverified event reporting: no factual catalyst assertion.
- Inaccessible premium feed: preserve the gap; no implied zero-cost entitlement.
