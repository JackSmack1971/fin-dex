# Research orchestration (Codex CLI)

This repository is a **read-only crypto research** workflow using the `crypto-mcp-research` skill. Follow the skill evidence contract; it defines statuses and methodology. The primary agent owns scope, delegation, synthesis, and the final answer. Agent descriptions do not grant permissions, filter MCP servers or enforce read-only access. Configure and enforce these restrictions in the actual runtime/tool policy.

## Dispatch

For simple single-provider checks, work directly without delegation. For complex multi-provider questions, use up to three independent source specialists concurrently, selecting only the relevant agents: `market_analyst`, `liquidity_analyst`, `onchain_analyst`, `catalyst_analyst`. Keep tasks bounded by specific assets, timeframe, question, and evidence expectations. Do not launch all specialists mechanically. Wait for the evidence results and reconcile them yourself before conclusions.

For consequential research or disputed findings, ask `adversarial_reviewer` to challenge the synthesized claims and `evidence_verifier` to audit final evidence. Run these on the **same consolidated snapshot** and only after synthesis. Their findings cannot be combined by majority vote; resolve against observations and deterministic checks. The primary agent may perform a final correction and repeat targeted verification if necessary.

## Boundaries and evidence

Never equate configured MCP with connected, authorized, free or responding. Discover actual available tools before calling. No payment, trade, wallet, secret, publication, external write, or account mutation is within this workflow. Report unavailable tools and limits explicitly. Preserve each observation's source, exact tool, asset and venue identity, UTC observation and retrieval times, units and coverage. Do not falsely claim two vendors are independent when they share an upstream feed.

Avoid unbounded delegation, recursion and overlapping edits. Agent outputs are proposals/evidence to inspect, not authority. Keep the pre-existing project configuration and skill files intact unless specifically asked to modify them. Report `NO_VERIFIED_CONCLUSION` when essential evidence is unverified or irreconcilable.
