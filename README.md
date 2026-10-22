# fin-dex

A read-only crypto research workflow for the Codex CLI, plus the local tooling that keeps its evidence honest.

fin-dex does not trade, sign, pay, or write to any external service. It gathers observations from six hosted research MCP servers, reconciles them, and reports what the evidence supports. When the evidence is not enough, the answer is `NO_VERIFIED_CONCLUSION`.

## What is in the repo

| Path | Purpose |
| - | - |
| `AGENTS.md` | Project instructions for Codex: dispatch rules, review classification, review gates, evidence boundaries. This is the controlling policy. |
| `.codex/config.toml` | Project config: the six MCP servers (CoinGecko, CoinPaprika, DexPaprika, Crypto.com Market Data, Hive, GDELT), each with an exact `enabled_tools` list, and the subagent concurrency limit. |
| `.codex/agents/*.toml` | Six subagents. Four source specialists: `market_analyst`, `liquidity_analyst`, `onchain_analyst`, `catalyst_analyst`. Two reviewers: `adversarial_reviewer`, `evidence_verifier`. |
| `.codex/*.json` | Data, not Codex config: provider capability inventory and routing tables that the skill's scripts read. |
| `.agents/skills/crypto-mcp-research/` | The research skill: `SKILL.md`, `references/`, `assets/` (schemas), `scripts/`, `tests/`, `evaluation/`. |
| `research-runs/` | Retained run records, kept as evidence. |
| `paper-testing/` | A separate, opt-in package for paper-trading proposals. See [paper-testing/README.md](paper-testing/README.md). |
| `docs/codex-alignment.md` | Provenance: which official Codex doc page each config surface was checked against, when, and the result. |
| `pr-drafts/` | Frozen evidence cited by hash from `AGENTS.md`. |

## Core ideas

- **Evidence before conclusions.** Every material observation carries provider, exact tool, asset identity, value and unit, observation time, retrieval time, and caveats. Claims are labeled `CORROBORATED`, `SINGLE_SOURCE`, `CONFLICTED`, or `UNVERIFIED`. See [references/evidence-contract.md](.agents/skills/crypto-mcp-research/references/evidence-contract.md).
- **Configured is not connected.** A server listed in `config.toml` is a candidate, not proof of access. `enabled_tools` exposes tools; it does not authorize them. Only tools classified `READ_ONLY` and allowlisted in `provider-capabilities.json` are eligible for automatic research.
- **The primary agent owns the answer.** Specialist output is evidence to inspect, not authority. Reviewers run only after synthesis, on the same frozen snapshot.
- **Research review and engineering review are different things.** `AGENTS.md` defines the classification (`RESEARCH_REVIEW`, `ENGINEERING_REVIEW`, `UNRESOLVED`, `POLICY_DECISION`) and the gates. Changing a gate or an authority is a policy decision that needs a maintainer's explicit approval.

## Getting started

Requirements:

- Python 3.x with `pytest` and `jsonschema`. There is no requirements file in the repo. The suite was last run on Python 3.14.3 with pytest 8.4.2.
- The Codex CLI, if you want to run the agent workflow. This README does not restate how Codex loads these files; see `docs/codex-alignment.md` for what was verified against the official docs.

First checks, all offline (run from the repo root):

```powershell
# Configuration inventory. PASS means local config only, not connectivity or entitlements.
python .agents/skills/crypto-mcp-research/scripts/check_config.py .codex/config.toml

# Full test suite
python -m pytest .agents/skills/crypto-mcp-research/tests paper-testing/tests -q

# What the repo can reach, per provider (MCP tools and local REST adapters). Never calls a provider.
python .agents/skills/crypto-mcp-research/scripts/effective_capabilities.py
```

Expected: every provider shows `CONFIGURED (connectivity untested)`, and the tests pass (286 at the time of writing).

## Running a research task

1. Read `AGENTS.md` and `.agents/skills/crypto-mcp-research/SKILL.md`. The skill's eight-step process is the contract: scope, discover capabilities, plan, retrieve and annotate, reconcile, falsify, verify, report.
2. Keep requests bounded: name the assets (chain plus contract for tokens), the timeframe, the question, and the evidence you expect.
3. Simple single-provider checks run directly. Multi-provider questions can use up to three source specialists in parallel.
4. For consequential or disputed findings, reviewers are dispatched after synthesis. Research reviews must pass the `native_lifecycle.py prepare-review` checkpoint first. If it fails, the result is `BLOCKED` and no reviewer is dispatched.

Deliverable: timestamped findings, falsifiers, an evidence and coverage table, confidence labels, blockers, and next checks.

## Skill scripts

All live in `.agents/skills/crypto-mcp-research/scripts/`.

| Script | Use |
| - | - |
| `check_config.py` | Offline check of `.codex/config.toml` against the inventory |
| `effective_capabilities.py` | Offline capability report. `--format v2` is the opt-in diagnostic contract |
| `validate_capabilities.py` | Validate capability data and policy; `--diagnostic report.json` checks a saved v2 report |
| `validate_evidence.py` | Validate a structured observations ledger |
| `validate_handoffs.py`, `validate_handoff_artifact.py` | Validate specialist and reviewer JSON returns |
| `validate_research_run.py` | Validate a run manifest; `--replay` replays retained calculations |
| `native_lifecycle.py` | Supervisor checkpoints: `prepare-review`, `validate-review` |
| `evaluate_workflow.py` | Static validation and preparation of the evaluation corpus |
| `dexscreener_public.py`, `dexpaprika_public.py`, `defillama_public.py`, `dex_pair_router.py` | Local REST adapters and routing |
| `indicator_replay.py` | Replay indicator calculations over retained data |

Passing an offline check says nothing about live provider behavior.

## Evaluation status

The evaluation harness under `.agents/skills/crypto-mcp-research/evaluation/` defines the corpus, protocol, and scoring. It is not a completed performance study. Prepared manifests, fixture replay, and static validation are not evidence of live agent performance, and unexecuted trials are `UNVALIDATED`. See [evaluation/README.md](.agents/skills/crypto-mcp-research/evaluation/README.md) for the current pilot status.

## Contributing

- Do not edit `pr-drafts/`, `research-runs/`, `.tmp/`, or `evaluation/pilots/`. They are retained evidence.
- `AGENTS.md` contains text that is bound by SHA-256 (baselines, the exception record). Any edit changes those hashes, so raise it with a maintainer first.
- Do not add, remove, or broaden MCP servers or `enabled_tools` entries without updating `provider-capabilities.json` and getting maintainer approval.
- Whoever implements a change cannot be its independent engineering reviewer.
- After changing a Codex-facing file, re-run the tests and record the verification in `docs/codex-alignment.md`.

## License

MIT. See [LICENSE](LICENSE).
