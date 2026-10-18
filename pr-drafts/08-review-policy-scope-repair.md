# Proposed policy amendment — typed reviewer workflows

**Status: READY_FOR_INDEPENDENT_VALIDATION — NOT_ADOPTED.** The current `AGENTS.md` reviewer-dispatch rule remains controlling until an authorized maintainer explicitly accepts and applies an amendment. This draft does not authorize reviewer dispatch, waive a gate, or change runtime/tool permissions. The policy-validation scenarios below are specification checks; they do not dispatch reviewers or constitute acceptance. Independent validation and adoption have not occurred.

## Purpose

The existing `prepare-review` checkpoint validates crypto research evidence. Requiring it for engineering reviews makes the research evidence workflow a prerequisite for reviewing changes to that workflow. This amendment scopes that checkpoint to research reviews while defining a separate evidence contract for engineering reviews. It preserves the distinction between reviewing the implementation of a reviewer system and deciding who may change its authority.

## Proposed replacement for the global reviewer-dispatch rule

### Triage and classification

Before any reviewer is selected or dispatched, record the exact target and requested decision. Classify each reviewer decision using both:

- `RESEARCH_REVIEW`: assess crypto market or other research evidence, methods, claims, or conclusions, regardless of whether the target is prose, JSON, code, a generated artifact, or a research-produced source file; or
- `ENGINEERING_REVIEW`: assess whether specified source-code or control-plane implementation properties meet stated technical requirements, without approving research evidence, methodology, or conclusions.

File extension, reviewer role, repository location, and artifact origin do not determine the class. The requested decision and the part of the target whose properties are being decided do. Record the classification, exact target, requested decision, scope, evidence references, and prerequisites before dispatch. Fix that record for the dispatch; a changed scope or decision requires a new record before any later dispatch.

If the request does not make the target or decision clear enough to apply these definitions, classify it `UNRESOLVED` and do not dispatch. Ask for or establish a precise target and decision, then classify the clarified request before considering dispatch.

If a request asks for both implementation and research judgments, split it into separately recorded decisions with separate scopes and findings. Each decision must meet its own prerequisites. Do not issue a combined approval. An engineering finding cannot validate, approve, imply, or be represented as approval of research evidence or conclusions.

### Research review requirements

For `RESEARCH_REVIEW`, the existing `.agents/skills/crypto-mcp-research/scripts/native_lifecycle.py prepare-review` checkpoint is mandatory. It must validate terminal specialist receipts, saved handoffs, closed collection, completed reconciliation, the frozen snapshot, and canonical digest before producing a dispatch permit. If it returns `BLOCKED` or otherwise fails, dispatch no reviewer for that research decision.

A failed research gate cannot be bypassed by changing the class label, file format, reviewer role, or by dividing the same research decision across artifacts or subtargets. A later engineering-only request is a new decision and is eligible for `ENGINEERING_REVIEW` only if its target and technical question are independently meaningful and separable, its scope expressly excludes research evidence/methodology/conclusions, and neither the request nor its findings will be used or represented as a substitute for the blocked research decision. Record the new target, decision, exclusions, and rationale. If the proposed split cannot meet those conditions, it remains `RESEARCH_REVIEW` and is blocked by the failed gate. Validate each returned research-review handoff with `native_lifecycle.py validate-review`.

### Engineering review requirements

For `ENGINEERING_REVIEW`, establish and record:

- a known Git baseline and exact review target;
- the scoped source changes and available source artifacts;
- available test or verification evidence, including checks not run and why;
- reviewer identity, implementation role, authorship/contribution disclosure, conflict disclosure, and the independence disposition; and
- the technical requirements against which implementation properties are assessed.

An engineering reviewer is independent only if they did not author or implement the target and did not materially contribute to the changes under review. A reviewer who participated in implementing the target is ineligible for that target. The reviewer must disclose other relevant conflicts; if a conflict could reasonably affect impartiality, the reviewer is ineligible. Record the factual basis for the disposition. Reassign an ineligible reviewer before dispatch; do not cure the conflict by relabeling the review.

An `ENGINEERING_REVIEW` may assess source-code and control-plane implementation decisions only. It must not validate or approve market or other research conclusions, issue or substitute for a research-review permit, or satisfy `prepare-review` requirements. Research-generated code or artifacts are classified by the decision requested: implementation properties can be engineering review, while research evidence, methodology, or conclusions require research review. Engineering approval never implies research-evidence approval.

### Review-system changes and policy decisions

Reviewing whether `native_lifecycle.py` or other reviewer-system code correctly implements the currently controlling policy is `ENGINEERING_REVIEW`, provided the decision is limited to implementation correctness and meets the engineering prerequisites. That review cannot grant permission to dispatch, waive or satisfy `prepare-review`, or authorize a policy change.

A request to change who may dispatch reviewers, alter or remove a gate, redefine review authority, or adopt this amendment is a **policy decision**, not an `ENGINEERING_REVIEW` or `RESEARCH_REVIEW` dispatch classification. Policy validation is a recorded, non-dispatch procedure. It cannot use either review label to dispatch a reviewer or to approve the proposed authority change. The amendment's author/implementer cannot serve as its independent verifier. Validation results, technical review findings, and explicit policy acceptance are separate records and decisions. Only an authorized maintainer may explicitly accept a policy change; a reviewer, validator, classification, or successful engineering finding cannot do so on the maintainer's behalf.

If a request combines a review-system implementation question with a policy decision, split them. The implementation decision follows its applicable review prerequisites and cannot decide the policy question. The policy question follows the separate validation and explicit-acceptance process. Until acceptance and integration, the current policy governs every reviewer dispatch, including engineering review of reviewer-system code.

### Evidence records and authority boundary

Record the review type (or `UNRESOLVED` / `POLICY_DECISION` where applicable), target, requested decision, scope, evidence references, reviewer identity and independence disposition, findings, and acceptance decision separately for each decision. Do not represent `UNRESOLVED` or `POLICY_DECISION` as permission to dispatch.

Review classification changes procedural evidence requirements only. It does not grant filesystem, network, tool, or execution permissions. All actions remain subject to effective runtime policy and authorization. Neither review type authorizes Liquid execution. A successful checkpoint or review record is not a runtime security boundary.

## Policy-validation scenarios required before adoption

The policy owner must evaluate and record the 12 scenarios in `09-review-policy-validation-matrix.md`. An independent verifier who did not author or implement the amendment must check the expected classifications and outcomes against this text and record disagreements. The verifier checks policy application; this step does not dispatch reviewer agents, validate research conclusions, or accept/adopt the amendment. Each scenario's input facts, expected classification, prerequisites, dispatch decision, and rule reference must be retained so another evaluator can repeat the analysis. An unresolved outcome is a validation failure requiring revision, not a passing result.

Policy validation, independent validation, explicit acceptance, policy integration, regression testing, and later implementation review are separate stages. No stage implies or substitutes for a later one.

## Scope and adoption

This proposal changes only reviewer-policy scope. It does not alter the v1 research gate, activate v2 identity-contract behavior, repair the LINK `O-dp-top-pool` raw-response reference, or authorize a review. Until explicit acceptance and integration, current `AGENTS.md` remains controlling, including its global `prepare-review` requirement before any reviewer dispatch and its fail-closed result.

Adoption requires completed scenario validation, independent validation, and explicit acceptance by an authorized maintainer. Only then may the governing policy be updated and its classification requirements checked. Keep evidence of each stage separate. The adoption sequence is:

`DRAFT → POLICY_VALIDATION → INDEPENDENT_VALIDATION → EXPLICIT_ACCEPTANCE → AGENTS.md UPDATE → REGRESSION_TESTS → INDEPENDENT_ENGINEERING_REVIEW`

Regression tests, if the amendment is accepted for integration, must cover the 12 classification and negative cases in the matrix and verify that research dispatch still fails closed when `prepare-review` fails. Do not dispatch an engineering reviewer under this proposed policy before it is accepted and integrated.

## Follow-on sequence after explicit adoption

1. Validate that research-review dispatch remains fail-closed under the adopted policy.
2. Establish a reproducible, scoped Git review target for the four v2 files, excluding unrelated worktree changes.
3. Conduct an independent engineering review under `ENGINEERING_REVIEW`.
4. Resolve findings and make a separate v2 contract-acceptance decision.
5. Only after separate evaluation, consider whether research-review dispatch should accept `RUNTIME_RETURN_BOUND`.

The LINK `O-dp-top-pool` raw-response defect remains independent and requires a genuine retained response reference or explicit evidence correction.
