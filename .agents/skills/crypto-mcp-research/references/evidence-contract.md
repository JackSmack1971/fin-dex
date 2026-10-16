# Evidence contract

## Status definitions

- `CORROBORATED`: at least two genuinely independent, compatible observed sources support the stated claim, identifiers and windows match, and no unresolved material conflict remains.
- `SINGLE_SOURCE`: one observed source with traceable identifiers/time and a valid method supports the claim; independence not established.
- `CONFLICTED`: material disagreement after normalization, including mismatched identity/time that prevents direct comparison.
- `UNVERIFIED`: required facts, methodology, time, or trustworthy observations are missing; may be a lead but not a conclusion.
- `UNAVAILABLE` is a provider/observation access state, not a claim truth status.

## Optional JSON evidence ledger

The validator recognizes this structure; field contents must be authentic observations, not manufactured examples:

```json
{
  "as_of_utc": "2026-10-15T23:00:00Z",
  "claims": [
    {
      "id": "claim-1",
      "statement": "Example only; replace with observed finding",
      "status": "SINGLE_SOURCE",
      "observations": [
        {
          "provider": "coingecko",
          "tool": "actual_discovered_tool_name",
          "asset_id": "canonical-id-or-chain:contract",
          "metric": "price",
          "value": 1.23,
          "unit": "USD",
          "observed_at_utc": "2026-10-15T22:59:00Z",
          "retrieved_at_utc": "2026-10-15T23:00:00Z",
          "source_url": "https://example.org/original-source"
        }
      ]
    }
  ]
}
```

`source_url` is optional only when the service provides no inspectable upstream URL; report that provenance gap in prose. For paired independence, document upstream origin and measurement method. A deterministic validator can detect schema defects and some status inconsistencies, but cannot prove independence or data truth. Never treat validator PASS as factual verification.

## Final brief

A compact conclusion should include scope/as-of UTC, evidence table (finding; provider/tool; observed UTC; status), conflicts, falsifiers, missing inputs, and a verdict. Use `NO_VERIFIED_CONCLUSION` if missing or contradictory evidence defeats the core thesis. Avoid synthetic conviction scores.
