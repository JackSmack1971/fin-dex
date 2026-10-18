# Fresh-session prompt: Fin-Dex AERO capability and handoff recovery

Continue in `C:\TEST-repos\fin-dex`. Read `AGENTS.md`, `.agents/skills/crypto-mcp-research/SKILL.md`, `references/provider-routing.md`, and `references/agent-handoffs.md` first.

Use the latest regression evidence under `research-runs/20261018-aero-evidence-linked-r1/`. Do not edit `research-runs/20261018-aero/`, any earlier frozen run, or `paper-testing/`. Do not add providers, expand network permissions, use paid endpoints, trade, access wallets, or create Liquid proposals.

The saved `effective-capabilities.json` distinguishes MCP tools from local REST adapters. DEX Screener and DeFiLlama are local REST adapters, not native MCP tools. Inspect their checked-out code and invocation contracts. The three saved provider responses show only those exact routes and queries; they do not prove current-session availability, authorization, free entitlement, or provider-wide qualification. Do not re-query or relabel retrieval time as source observation time.

Read and validate the three persisted specialist handoffs before synthesis. Their terminal status is PARTIAL; preserve it. Confirm DEX Screener pair counts/pool metrics and DeFiLlama values against raw response bytes, retaining the stated unit, freshness, identity, and coverage limits. Confirm the market handoff's full AERO replay; missing fields must be NOT_REPORTED, never MATCH. EMA starts from the first close; RSI(14) uses the arithmetic mean of gains and losses, not Wilder smoothing; ATR remains unavailable without OHLC. The original disputed specialist MACD value is absent from retained handoffs, so report that specific conflict as not reproducible.

Use authentic collaboration evidence only. The saved execution receipt records that this interface exposed agent handles and terminal statuses but not native child thread IDs. Do not manufacture IDs from handles, task IDs, hashes, or paths. Persist any new handoff before review. Close and reconcile the exact selected specialist collection, freeze and digest the snapshot, then run `native_lifecycle.py prepare-review`. If authentic native thread IDs remain unavailable, retain the BLOCKED result and dispatch no reviewers. Do not weaken the lifecycle contract.

Report observed capability states, exact handoff and raw-artifact validation results, the review-gate outcome, remaining evidence gaps, and `NO_VERIFIED_CONCLUSION` wherever essential evidence remains unverified.
