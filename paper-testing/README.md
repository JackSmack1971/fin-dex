# Opt-in paper testing

This package is separate from the read-only `crypto-mcp-research` workflow. It accepts a frozen, versioned signal record; it does not generate signals. No Liquid connector is configured or qualified in this repository, so order submission is intentionally unavailable until a fresh runtime inventory verifies the simulation mode, exact tool schema, and account identity.

## Safety boundary

- No live trading, wallet connection, funding, signing, or credential storage is supported.
- A future connector must implement `PaperVenue` and provide fresh, retained evidence that the account and order operation are simulated. Missing, stale, ambiguous, or changed evidence fails closed.
- Only `get_paper_status`, `submit_simulated_order`, and `reconcile_order` are permitted by the adapter contract. Unknown operations are rejected.
- The adapter never retries order submission. An uncertain response becomes `SUBMISSION_UNKNOWN` and requires reconciliation by stable client/order ID before any operator decision.
- The current implementation is offline validation and lifecycle bookkeeping only. It cannot contact Liquid or claim an order was placed.

Validate records with:

```powershell
python paper-testing/validate_paper_record.py path\to\signal.json --kind signal
python paper-testing/validate_paper_record.py path\to\run.json --kind run
```

Keep records under `paper-testing/runs/`; do not place credentials there. The proposed $10,000 balance, 30-day period, 7–21-day horizon, and BTC/ETH/SOL plus supported-altcoin universe are experiment parameters, not evidence that a run has started. Metrics remain incomplete unless required fills, fees, funding, equity history, and stable identifiers are observed.

## Liquid qualification required before the vertical slice

In a fresh trusted Codex session, inspect the actual Liquid MCP connection and OAuth scopes, list tool names and schemas, establish the simulation account and mode using provider-returned evidence, and bind the exact simulated-order method and identity. Record source, exact tool, UTC observation and retrieval times, account/instrument identifiers, and schema digest. Stop if any part is unavailable or ambiguous. Do not modify research MCP allowlists to enable this.

## Run lifecycle

1. Validate and freeze a signal; retain qualified and rejected decisions.
2. Verify venue/account simulation mode and exact tool schema.
3. Submit one simulated order once, persist request digest and acknowledgement.
4. Reconcile stable order/fill IDs and account state. Preserve unavailable fields as `null` and set evaluation incomplete.
5. Only after accepted one-order evidence, start a separately recorded 30-day forward test. Do not combine its results with historical walk-forward evaluation.
