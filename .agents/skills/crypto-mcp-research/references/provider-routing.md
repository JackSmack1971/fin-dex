# Provider routing and discovery (conditional)

The project configuration lists six *named connectors*. The following is **provisional routing by provider name**, not a verified promise of tools, accuracy, free use, or feature coverage. Discover live tools first.

| Connector | Candidate evidence | Must verify in live metadata and response |
|---|---|---|
| `coingecko` | spot assets, markets, historical market information | coin/contract mapping; quote currency; timestamps; market and plan limits |
| `coinpaprika` | independent asset taxonomy and market measurements | exchange/market universe; symbol disambiguation; stale records; free-tier restrictions |
| `dexpaprika` | on-chain DEX pools, swaps, pool liquidity | chain/contract and pool IDs; base/quote; reserve vs modeled depth; observed time |
| `cryptocom_market` | exchange/venue-oriented market observations | spot vs derivatives; instrument convention; price vs mark; depth definition |
| `hive` | specialized blockchain/project intelligence, if exposed | actual tool categories, provenance and pricing; don't assume all chains covered |
| `gdelt` | indexed reporting and event leads | article publication vs event time; publisher credibility; duplicate syndication; entity matching |

## Routing rules

- For a price/distribution claim use a price source and, if consequential, a different upstream price construction or venue; do not call two wrappers on the same upstream feed independent corroboration.
- For liquidity distinguish 24h dollar turnover, actual quoted depth, spread, reserve-based TVL, and capacity. If only volume is available, execution depth remains `UNVERIFIED`.
- For an event, preserve original article and dated primary announcement when available. A GDELT index hit is discovery evidence, not proof that the event happened.
- For on-chain claims require the exact supported chain and contract; generic project narrative is insufficient.
- Request the narrowest tools needed; stay within confirmed free quotas. On quota/auth/fee prompts, stop that provider branch without purchasing access.

## Availability checks

1. Confirm the workspace is a trusted Codex project and the expected MCP configuration is visible through local configuration inspection or `codex mcp list` if available.
2. Confirm the relevant server and advertised tool metadata are actually visible in the active session. Configuration presence alone is not connectivity.
3. For a representative read-only tool, call a low-impact query that meets the provider's published limits and inspect timestamp, identifiers, and error state.
4. Record statuses separately: `CONFIGURED`, `DISCOVERED`, `RESPONDED`, `QUALIFIED`, or `UNAVAILABLE`. Do not upgrade one stage without observing the next.
