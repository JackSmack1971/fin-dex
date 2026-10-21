# Review classification record validator

`scripts/validate_review_classification.py` checks a **recorded** typed-review classification against the controlling policy in the repository `AGENTS.md` ("Typed review classification", "Research review gate", "Engineering review requirements"). It never classifies from prose, and it does not change, broaden or reinterpret that policy. Record schema: `assets/review-classification-record.v1.schema.json` (structure only; the script enforces the rest).

## What it does and does not establish

The validator checks declared structure and policy consistency, not review quality or truth. It is not a runtime security boundary: Codex does not route reviewer dispatch through it, so a session can dispatch without running it. It does not authenticate reviewers or maintainers, does not verify that a Git commit, file, hash or permit exists, and does not re-run or replace `native_lifecycle.py prepare-review` / `validate-review`. A research record only *references* a permit; the permit is produced and checked by `prepare-review`. A passing result is a supervisor aid, never an authorization token, and never permission for filesystem, network, tool or Liquid execution.

## Usage

```
python scripts/validate_review_classification.py record.json [related-record.json ...]
```

Pass every related record in one invocation (split siblings, a superseded record, a blocked research record). Cross-record references that are not supplied are errors. Exit codes: `0` every record valid and dispatch-eligible by its own record; `1` invalid or inconsistent (fail closed; also any missing, unknown or mistyped field, duplicate JSON key, or non-finite number); `2` unreadable input; `3` all records valid but at least one is not dispatchable (`UNRESOLVED`, `POLICY_DECISION`, gate `BLOCKED`, ineligible reviewer, or `NO_DISPATCH`). Only `0` may be read as "this record permits dispatch".

## Record outline (version 1)

Common fields: `record_id`, `recorded_at_utc`, `request` (`request_id`, `sibling_record_ids`), `classification`, `target` (`kind` is informational), `requested_decision`, `decision_aspects`, `scope`, `evidence_references`, `prerequisites`, `dispatch_disposition`, `class_fields`; optional `supersedes_record_id`, `exception_ref`. `decision_aspects` is the recorder's declaration of what is being decided (`RESEARCH_EVIDENCE`/`RESEARCH_METHODOLOGY`/`RESEARCH_CONCLUSIONS`, `IMPLEMENTATION_PROPERTIES`, `REVIEW_AUTHORITY_CHANGE`, `UNDETERMINED`); the validator checks it against the class, never against file extension or location.

Class-specific `class_fields`: `RESEARCH_REVIEW` carries `prepare_review` (gate result, permit reference, recorded checkpoint output); `ENGINEERING_REVIEW` carries Git baseline, scoped changes, source artifacts, checks run and not run, technical requirements, target contributors, reviewer identity/role/disclosures/conflicts/independence disposition, research exclusions and representation flags (all must be `false`); `UNRESOLVED` carries the clarification needed; `POLICY_DECISION` carries the change kind and an acceptance state.

## Rules and their AGENTS.md source

| Tag | Encoded rule | AGENTS.md text |
| - | - | - |
| `TRC-RECORD` | Exact target, requested decision, scope, evidence references and prerequisites are recorded; prerequisites declared `UNMET` block `DISPATCH_PERMITTED`. | "record the exact target, requested decision, scope, evidence references, and prerequisites" |
| `TRC-OBJECT` | Class must match the declared decision aspects; extension, location, `target.kind`, reviewer role and origin are ignored. | "File extension, repository location, reviewer role, and artifact origin do not determine the class." |
| `TRC-SPLIT` | A record mixing research, engineering and/or policy aspects is rejected; related records share `request_id`, list each other as siblings, and differ in target/decision and scope. | "split it into separately recorded targets, scopes, evidence, findings, and outcomes" |
| `TRC-UNRESOLVED` | `UNRESOLVED` is never `DISPATCH_PERMITTED`; it names what must be clarified; a clarified request is a new record that supersedes it. | "no reviewer dispatch until clarified and recorded" |
| `TRC-POLICY` | `POLICY_DECISION` is never dispatchable, even when accepted; acceptance must be a separate record by an `AUTHORIZED_MAINTAINER`, not this record, another supplied record or a cited finding. | "a review finding or classification cannot accept it" |
| `RRG-PERMIT` | `RESEARCH_REVIEW` needs a recorded `PASSED` gate and a permit reference (`REVIEW_DISPATCH_ALLOWED`, 64-hex payload digest); `BLOCKED` means no permit and no dispatch; an engineering record cannot carry or stand in for a permit. | "before producing a new dispatch permit" |
| `RRG-RELABEL` | Same target+decision as a blocked research record under another class is rejected; overlapping engineering targets need a recorded separation; a blocked decision is superseded only by a research record with a passed gate. | "A failed research gate cannot be bypassed by changing the label, file format, reviewer role, or dividing the same research decision across artifacts or subtargets." |
| `ENG-REQ` | Known Git baseline (non-zero commit id), exact target, scoped changes, checks run and/or not run with reasons, technical requirements. | "establish and record a known Git baseline, exact review target" |
| `ENG-INDEP` | Reviewer identity, role, disclosures, conflicts and disposition; authors, implementers, material contributors, listed contributors and impartiality conflicts cannot be `INDEPENDENT`. | "A reviewer who participated in implementing the target is ineligible for it." |
| `ENG-NOAPPROVE` | Engineering records exclude research evidence/methodology/conclusions and may not claim to approve conclusions, issue or substitute for a permit, satisfy `prepare-review`, waive a gate or authorize a policy change or Liquid execution. | "Engineering findings must never validate, approve, imply, or be represented as approval of research conclusions." |
| `EXC-ONETIME` | The 2026-10-18 one-time exception is consumed; any citation is rejected, as is any unknown exception. | "It expires when that engagement reaches a terminal outcome." |

## Matrix scenarios, clauses and tests

Scenario numbers are those of the frozen `pr-drafts/09-review-policy-validation-matrix.md`. Cases live in `tests/fixtures/review-classification/scenarios.json`; tests are in `tests/test_review_classification.py`.

| # | Scenario | Clause tags | Test |
| - | - | - | - |
| 1 | Engineering, complete evidence, no research-gate pass | `ENG-REQ`, `ENG-NOAPPROVE`, `RRG-PERMIT` | `MatrixScenarios.test_scenario_01_engineering_complete_without_research_gate` |
| 2 | Research with permit | `RRG-PERMIT` | `test_scenario_02_research_with_permit` |
| 3 | Mixed request split | `TRC-SPLIT` | `test_scenario_03_mixed_request_split` |
| 4 | Relabel after failed gate | `RRG-RELABEL` | `test_scenario_04_relabel_after_failed_gate` |
| 5 | Research-generated code, implementation only | `TRC-OBJECT`, `ENG-NOAPPROVE` | `test_scenario_05_research_generated_code_implementation_only` |
| 6 | Source review also certifies conclusions | `TRC-SPLIT`, `ENG-NOAPPROVE` | `test_scenario_06_source_review_also_certifies_conclusions` |
| 7 | No Git baseline | `ENG-REQ` | `test_scenario_07_no_git_baseline` |
| 8 | No exact target or diff scope | `ENG-REQ`, `TRC-OBJECT` | `test_scenario_08_no_exact_target_or_diff_scope` |
| 9 | Reviewer participated in implementation | `ENG-INDEP` | `test_scenario_09_reviewer_participated_in_implementation` |
| 10 | Ambiguous request | `TRC-UNRESOLVED` | `test_scenario_10_ambiguous_request` |
| 11 | Policy amendment decision | `TRC-POLICY` | `test_scenario_11_policy_amendment_decision` |
| 12 | Reviewer-gate code plus authority change | `TRC-POLICY`, `ENG-NOAPPROVE`, `TRC-SPLIT` | `test_scenario_12_reviewer_gate_code_plus_authority_change` |

Hostile cases (relabeling variants, extension/location independence, consumed-exception reuse, strict JSON, single-field mutation sweeps) are in `HostileCases` and `RequiredFieldMutation`.

## Not deterministically encodable

- Whether a request is genuinely mixed, or whether a target/decision is genuinely ambiguous, separable or "independently meaningful": the recorder declares aspects and separation flags; the validator checks them for consistency, not for honesty.
- Whether the reviewer really did not contribute, whether a conflict "could reasonably affect impartiality", and whether a named person is an authorized maintainer.
- Whether a Git commit, scoped change, evidence reference or permit file exists and matches; whether recorded checks really ran.
- Relabeling against a blocked decision that was never supplied in the same invocation.
- Whether findings, once returned, stay inside the recorded scope or claim research approval in prose.
- Runtime enforcement of any of the above.

## Interpretations and open limits

**Status: interpretations recorded for maintainer acceptance; not policy until accepted.** None of the items below is part of `AGENTS.md`, and none has been accepted by an authorized maintainer. They record how the validator currently reads gaps in the policy.

1. **Retry after a failed research gate.** `AGENTS.md` does not describe a retry path. The validator treats a new `RESEARCH_REVIEW` record whose `prepare-review` gate passed and which supersedes the blocked record as the only accepted retry. This is a validator interpretation, not `AGENTS.md` policy.
2. **Rules in `pr-drafts/08` that are deliberately not encoded.** "Fixed record per dispatch" is not deterministically enforceable offline. "The amendment's author cannot verify it" is already covered by the existing reviewer independence rule. If the maintainer wants either as policy, that is a separate `POLICY_DECISION`.
3. **Self-declared claims.** "Authorized maintainer" is a self-declared claim; the validator does not authenticate it. "Known Git baseline" means a well-formed, non-zero commit id; the validator does not check that the commit exists.
4. **One-time exception.** The 2026-10-18 exception is encoded as consumed, and any citation of it, or of an unknown exception, is rejected.
