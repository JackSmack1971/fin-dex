# Policy-scope repair — independent engineering review record

**Decision:** `PASS` after one correction round. The reviewer assessed the policy integration only; this review is not policy acceptance, adoption authority, or permission for research reviewer dispatch or Liquid execution. Policy adoption was explicitly authorized separately by the user.

## Review binding

- Repository: `C:\TEST-repos\fin-dex`
- Branch: `codex/proposal-only-liquid-workflow`
- Target: `AGENTS.md` policy-scope repair only
- Git baseline: `HEAD:AGENTS.md`, blob `8cb4be9ee0fd19c10f1dd1d9e545e301d98ea8cf`, content SHA-256 `d0c330b8514031b47dcf4b2088f10972a6d571775ea63ad44114cefce4865415`
- Pre-change checkout representation: CRLF SHA-256 `2c71101b81f18568e6f4814bf367782e32b67d237ea3acca33ba38668b6c650f`; repository reports index LF / worktree CRLF.
- Final scoped diff: 9,560 bytes; SHA-256 `5f69eed917b31dff0490cedfd87fc0ce5600d14c26f21e713d5c5b329cfc972d`
- Final `AGENTS.md` checkout bytes (CRLF): SHA-256 `eb6321d8360d6043a64681d483aa12ba2e73fbc51c8733031f8866f6412aacb5`
- Final staged `AGENTS.md` index bytes (LF): SHA-256 `e3ba2aa9d0a670d32a16ab02e37b5ca4d36de64171c23638ba1ce70f4cb4fc07`
- Frozen reference amendment: `08-review-policy-scope-repair.md`, SHA-256 `7bacb6af98bea47ad2a50a0763cfefeba8d7b16abbdade565ea29ca975f3d638`
- Frozen reference matrix: `09-review-policy-validation-matrix.md`, SHA-256 `d8fc46986d2c60f75a67adb046a847a0021c59f1329ececf95348af375c6b36a`

## Reviewer and independence

- Reviewer identity: `/root/policy_scope_review` (separate agent session).
- Role: independent engineering reviewer.
- Independence: reviewer reported no authorship or implementation contribution to the target.
- Review scope: whether the adopted `AGENTS.md` text faithfully implements the approved policy scope and preserves research fail-closed behavior, mixed-scope separation, policy acceptance boundaries, reviewer independence, the bounded exception, and Liquid restrictions.
- Exclusions: unrelated worktree changes, LINK research evidence, v2 acceptance, runtime dispatch, and trading or Liquid operations.

## Findings and resolution

The initial review returned `CHANGES_REQUESTED` because the stated `2c711...` baseline digest was the pre-change CRLF worktree representation, not the Git blob content. The policy record was corrected to distinguish the Git blob SHA-256 (`d0c330...`) from the CRLF worktree SHA-256 (`2c711...`) and to include the Git blob ID. The reviewer verified the correction and confirmed the final diff/current-file/candidate hashes.

The final independent review returned `PASS`. It found no material policy loophole in the scoped change. It confirmed that the research gate remains mandatory and fail-closed for `RESEARCH_REVIEW`; the same research decision cannot be relabeled; mixed decisions are separately scoped; engineering review cannot approve research conclusions; `UNRESOLVED` blocks dispatch; `POLICY_DECISION` requires separate explicit acceptance; reviewer independence disqualifies target authors/implementers/material contributors; and the one-time exception is bounded to this AGENTS.md integration review and excludes research dispatch, permits/contracts, runtime authority, and Liquid execution.

The reviewer confirmed the instructions remain supervisor-controlled and do not claim runtime enforcement. The one-time exception is consumed and expired when this review engagement reached its terminal `PASS` outcome.

## Verification and limitations

- `python -m unittest discover -s .agents/skills/crypto-mcp-research/tests -p test_native_lifecycle.py`: **36 passed**. This exercises the existing research lifecycle, not typed review classification.
- No executable typed-policy validator or typed-classification test suite exists. The 12 scenarios remain manual specification-level evidence in the frozen matrix.
- The reviewer did not run tests; the primary agent ran the lifecycle suite. The final correction changed only baseline-hash documentation.
- No research-review dispatch, v2 acceptance, reviewer-permit change, Liquid execution, commit, or push is authorized by this review record.
