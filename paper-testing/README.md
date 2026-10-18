# Opt-in paper testing

This package is separate from the read-only crypto-mcp-research workflow. It accepts a frozen, versioned signal record; it does not generate signals. The Codex runtime exposes Liquid Co-Invest tools, but the inventory inspected on 2026-10-18 did not expose a direct order-submission tool. Its suggest_order operation creates a paper review link that must be confirmed in Liquid. Since the web app does not inherit the connector's paper mode, that operation cannot meet this adapter's simulated execution boundary. See [runtime discovery](liquid-runtime-discovery-2026-10-18.md).

## Safety boundary

- No live trading, wallet connection, funding, signing, or credential storage is supported.
- A future connector must implement PaperVenue; each submit independently reads the exact runtime tool schemas, paper status, and instrument metadata. It requires the qualified account ID, current simulated mode, and a fresh schema digest. Missing, stale, ambiguous, or changed evidence fails closed.
- Only get_paper_status, get_instrument_metadata, submit_simulated_order, and reconcile_order are accepted. Unknown operations or schemas are rejected.
- Before dispatch, SQLite atomically reserves the signal ID and immutable request digest in one canonical registry at `paper-testing/runs/paper-idempotency.sqlite3`. Callers cannot select per-adapter registry paths. After reservation, the adapter rechecks tool schemas, instrument metadata, and fresh mode/account evidence immediately before dispatch; any failed recheck consumes the reservation without sending, and the signal cannot be retried. Any existing reservation blocks another submit across adapter restarts. An uncertain response becomes SUBMISSION_UNKNOWN; it is never retried and must be reconciled by its stable client order ID.
- This SQLite guarantee covers adapter processes on one host using the same persistent local filesystem. A deployment must preserve the canonical path across restarts and run a single adapter host. Multiple hosts or independent checkout/database copies are not qualified; they require a shared transactional idempotency service before dispatch is enabled. If the registry cannot be opened, submission fails before any venue call.
- The reservation retains the frozen signal, runtime schema snapshot, fresh mode evidence, instrument metadata, request digest, acknowledgement, and raw reconciliation response in the same durable database.
- The SQLite journal is the durable evidence store for this slice; a separate exporter that emits the final paper-run artifact is still required before the one-order acceptance test.
- The complete frozen signal is validated at submission, including direction/side consistency. Quantity and limit price must satisfy the instrument's finite decimal range and precision metadata.
- The current implementation is a connector contract plus offline validation and lifecycle bookkeeping. No qualified connector can currently satisfy it, so it cannot contact Liquid or claim an order was placed.

Validate records with:

```powershell
python paper-testing/validate_paper_record.py path\to\signal.json --kind signal
python paper-testing/validate_paper_record.py path\to\run.json --kind run
```

Keep records under paper-testing/runs/; do not place credentials there. Keep the SQLite registry on persistent local storage and out of version control. The proposed $10,000 balance, 30-day period, 7–21-day horizon, and BTC/ETH/SOL plus supported-altcoin universe are experiment parameters, not evidence that a run has started. Metrics remain incomplete unless required fills, fees, funding, equity history, and stable identifiers are observed.

## Liquid qualification required before the vertical slice

In a fresh trusted Codex session, establish whether Liquid exposes an order operation that can be programmatically and cryptographically bound to simulation mode. Inspect OAuth scopes, operation names and schemas, account identity, and instrument rules. Retain provider-returned evidence with UTC observation and retrieval times and schema digest. Stop if any part is unavailable or ambiguous. Do not use a review link that requires a separate UI confirmation. Do not modify research MCP allowlists to enable this.

## Run lifecycle

1. Validate and freeze a signal; retain qualified and rejected decisions.
2. Verify venue/account simulation mode and exact tool schema.
3. Submit one simulated order once, persist request digest and acknowledgement.
4. Reconcile stable order/fill IDs and account state. Preserve unavailable fields as null and set evaluation incomplete.
5. Only after accepted one-order evidence, start a separately recorded 30-day forward test. Do not combine its results with historical walk-forward evaluation.
