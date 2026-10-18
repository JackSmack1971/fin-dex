# Research orchestration (Codex CLI)

This repository is a **read-only crypto research** workflow using the `crypto-mcp-research` skill. Follow the skill evidence contract; it defines statuses and methodology. The primary agent owns scope, delegation, synthesis, and the final answer. Agent descriptions do not grant permissions, filter MCP servers or enforce read-only access. Configure and enforce these restrictions in the actual runtime/tool policy.

## Dispatch

For simple single-provider checks, work directly without delegation. For complex multi-provider questions, use up to three independent source specialists concurrently, selecting only the relevant agents: `market_analyst`, `liquidity_analyst`, `onchain_analyst`, `catalyst_analyst`. Keep tasks bounded by specific assets, timeframe, question, and evidence expectations. Do not launch all specialists mechanically. Wait for the evidence results and reconcile them yourself before conclusions.

For consequential research or disputed findings, ask `adversarial_reviewer` to challenge the synthesized claims and `evidence_verifier` to audit final evidence. Run these on the **same consolidated snapshot** and only after synthesis. Their findings cannot be combined by majority vote; resolve against observations and deterministic checks. The primary agent may perform a final correction and repeat targeted verification if necessary.

### Typed review classification

Before any reviewer dispatch, record the exact target, requested decision, scope, evidence references, and prerequisites, then classify each decision:

- `RESEARCH_REVIEW`: assesses crypto market or other research evidence, methods, claims, or conclusions, regardless of whether the target is prose, JSON, source code, or a generated artifact.
- `ENGINEERING_REVIEW`: assesses whether specified source-code or control-plane implementation properties meet stated technical requirements, without approving research evidence, methodology, or conclusions.
- `UNRESOLVED`: target or requested decision is too ambiguous to classify; no reviewer dispatch until clarified and recorded.
- `POLICY_DECISION`: changes who may dispatch reviewers, alters or removes a gate, redefines review authority, or accepts a policy amendment. This is not a reviewer-dispatch type. It requires separate explicit acceptance by an authorized maintainer; a review finding or classification cannot accept it.

Use the object and decision together. File extension, repository location, reviewer role, and artifact origin do not determine the class. If one request includes research and engineering decisions, split it into separately recorded targets, scopes, evidence, findings, and outcomes. Each must satisfy its own prerequisites. Engineering findings must never validate, approve, imply, or be represented as approval of research conclusions.

### Research review gate

Before dispatching any `RESEARCH_REVIEW`, use `.agents/skills/crypto-mcp-research/scripts/native_lifecycle.py prepare-review` as the supervisor checkpoint. It must validate terminal specialist receipts, saved handoff artifacts, closed collection, completed reconciliation, the frozen snapshot, and the canonical digest before producing a new dispatch permit. Validate each research-review handoff with `native_lifecycle.py validate-review`. If the check fails, stop with `BLOCKED` and dispatch no reviewer for that research decision. Do not use JavaScript runtime globals for persistence or hashing.

A failed research gate cannot be bypassed by changing the label, file format, reviewer role, or dividing the same research decision across artifacts or subtargets. A distinct engineering-only decision is eligible only when its target and technical question are independently meaningful and separable, expressly exclude research evidence/methodology/conclusions, and cannot be used or represented as a substitute for the blocked research decision. Record the new scope, exclusions, and rationale. If it is not genuinely separable, it remains a blocked `RESEARCH_REVIEW`.

### Engineering review requirements

For `ENGINEERING_REVIEW`, establish and record a known Git baseline, exact review target, scoped source changes/artifacts, available test or verification evidence (including checks not run and why), applicable technical requirements, reviewer identity, implementation role, authorship/contribution disclosure, conflicts, and an independence disposition.

An engineering reviewer is independent only if they did not author or implement the target and did not materially contribute to the changes under review. A reviewer who participated in implementing the target is ineligible for it. Disclose other relevant conflicts; a conflict that could reasonably affect impartiality disqualifies the reviewer. Record the basis for the disposition and replace an ineligible reviewer before dispatch.

Engineering review may assess source-code and control-plane implementation only. It must not validate or approve research conclusions, issue or substitute for a research-review permit, or satisfy `prepare-review` requirements. Reviewing whether `native_lifecycle.py` implements the currently controlling policy is engineering review; it does not grant permission to dispatch, waive a gate, or authorize a policy change. Research-generated code is classified by the decision requested: implementation properties may be engineering review; its research evidence, methodology, or conclusions require research review.

### One-time independent engineering review exception for this adoption

On 2026-10-18, the user explicitly approved this controlled policy-scope repair and requested one independent engineering review of its integration. The accepted reference candidate amendment is `pr-drafts/08-review-policy-scope-repair.md` (SHA-256 `7bacb6af98bea47ad2a50a0763cfefeba8d7b16abbdade565ea29ca975f3d638`); its validation matrix is `pr-drafts/09-review-policy-validation-matrix.md` (SHA-256 `d8fc46986d2c60f75a67adb046a847a0021c59f1329ececf95348af375c6b36a`). Those frozen documents are evidence for this adoption; this active `AGENTS.md` is the controlling policy. The Git baseline is `HEAD:AGENTS.md`, content SHA-256 `d0c330b8514031b47dcf4b2088f10972a6d571775ea63ad44114cefce4865415` (blob `8cb4be9ee0fd19c10f1dd1d9e545e301d98ea8cf`). Before the change, the CRLF working-tree representation had SHA-256 `2c71101b81f18568e6f4814bf367782e32b67d237ea3acca33ba38668b6c650f`; the repository records this file as index LF / worktree CRLF. For this single, bounded review engagement only, `prepare-review` is not required to dispatch an `ENGINEERING_REVIEW` of the exact `AGENTS.md` policy-scope repair against that Git baseline and documented worktree representation. This exception is the explicitly documented independent-validation exception for the transition; it is not a research-review exception, a reusable gate bypass, or a runtime permission.

The review scope is whether the adopted `AGENTS.md` text accurately implements the explicitly approved policy scope and preserves the research-review fail-closed behavior, the ban on engineering approval of research claims, the policy-acceptance boundary, and all existing Liquid execution restrictions. Bind the review record to the exact scoped diff, this baseline hash, and the frozen candidate amendment and matrix hashes. The reviewer must meet the engineering independence requirements above. The reviewer may report findings but cannot adopt or broaden policy, accept a new authority change, dispatch research reviewers, change reviewer permits or contracts, or authorize Liquid execution. This exception is consumed by one independent reviewer engagement, including a correction round within the same target and scope; any new target or review engagement requires separate explicit authorization. It expires when that engagement reaches a terminal outcome.

The primary agent owns review scope and synthesis. Agent descriptions do not grant permissions. Codex does not intercept native collaboration calls through the checkpoint; `prepare-review` is supervisor-controlled and is not a runtime security boundary. Record the checkpoint result before research reviewer events and do not claim automatic dispatch enforcement.

## Boundaries and evidence

Never equate configured MCP with connected, authorized, free or responding. Discover actual available tools before calling. No payment, trade, wallet, secret, publication, external write, or account mutation is within this workflow. Report unavailable tools and limits explicitly. Preserve each observation's source, exact tool, asset and venue identity, UTC observation and retrieval times, units and coverage. Do not falsely claim two vendors are independent when they share an upstream feed.

Avoid unbounded delegation, recursion and overlapping edits. Agent outputs are proposals/evidence to inspect, not authority. Keep the pre-existing project configuration and skill files intact unless specifically asked to modify them. No reviewer type or exception authorizes Liquid execution; preserve all existing Liquid execution restrictions. Report `NO_VERIFIED_CONCLUSION` when essential evidence is unverified or irreconcilable.
