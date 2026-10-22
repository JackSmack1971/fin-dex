# Codex control-plane alignment

Provenance for the Codex surfaces in this repo. Each row records the official page(s) fetched through the `openaiDeveloperDocs` MCP, the date, and the result. Labels: `ALIGNED`, `DRIFT`, `NOT IN DOCS`, `DOCS CONFLICT`.

Latest audit: 2026-10-22 (UTC), branch `claude/codex-alignment-audit-main`, clean worktree of `origin/main` at `7bda72375d8077c005e081e3ab569eb8d3dd82bd` (PR #18 merge). Performed by Claude Code. This is not an independent engineering review and says nothing about research conclusions. No fixes were applied. "Re-verify by" is a proposed 30-day cadence (7 days for the open DRIFT), not a docs-defined interval.

Line references are to files as checked out on that commit. `AGENTS.md` is 9,218 B in the Git blob (LF) and 9,266 B in a CRLF checkout.

## Doc pages fetched (this session, 2026-10-22)

| Short name | URL |
| - | - |
| Codex manual | https://developers.openai.com/codex/codex-manual.md (fetched; 3.1 MB, spot-checked only, not read in full; no finding rests on it alone) |
| Subagents | https://learn.chatgpt.com/docs/agent-configuration/subagents |
| AGENTS.md guide | https://learn.chatgpt.com/docs/agent-configuration/agents-md |
| Build skills | https://learn.chatgpt.com/docs/build-skills |
| Config basics | https://learn.chatgpt.com/docs/config-file/config-basic |
| Config reference | https://learn.chatgpt.com/docs/config-file/config-reference (searched by key, not read in full) |
| Config advanced | https://learn.chatgpt.com/docs/config-file/config-advanced#project-instructions-discovery |
| MCP | https://learn.chatgpt.com/docs/extend/mcp |
| Hooks | https://learn.chatgpt.com/docs/hooks |
| Rules | https://learn.chatgpt.com/docs/agent-configuration/rules |
| Approvals and security | https://learn.chatgpt.com/docs/agent-approvals-security#protected-paths-in-writable-roots |

## Results (origin/main `7bda723`)

| ID | Surface (file:line) | Doc(s) | Verified | Re-verify by | Result |
| - | - | - | - | - | - |
| F01 | `.codex/config.toml:1-3` project location, trusted projects only | Config basics, MCP | 2026-10-22 | 2026-11-22 | ALIGNED |
| F02 | `config.toml:8-42` `url`, `enabled`, `startup_timeout_sec`, `enabled_tools` per `[mcp_servers.*]` | MCP, Config reference | 2026-10-22 | 2026-11-22 | ALIGNED. Docs: `startup_timeout_sec` default 10 (ours 30); `enabled_tools` is an allow list; `enabled` disables without removing |
| F03 | `config.toml:44-47` `[agents] enabled`, `max_concurrent_threads_per_session`; comment "limits concurrency, not tool authorization" | Subagents, Config reference | 2026-10-22 | 2026-11-22 | ALIGNED |
| F04 | `.codex/agents/*.toml` (6 files, lines 1-3: `name`, `description`, `developer_instructions` only) | Subagents | 2026-10-22 | 2026-11-22 | ALIGNED. Required three present; no undocumented field |
| F05 | Agent files omit `sandbox_mode` / `mcp_servers` | Subagents | 2026-10-22 | 2026-11-22 | ALIGNED. Docs: such settings do not override the parent |
| F06 | `AGENTS.md:3` and `:42` "Agent descriptions do not grant permissions / filter MCP / enforce read-only" | Subagents | 2026-10-22 | 2026-11-22 | ALIGNED |
| F07 | Root `AGENTS.md` discovery and size (9,218 B blob / 9,266 B CRLF vs 32 KiB default); only `AGENTS.md` on main | AGENTS.md guide, Config reference | 2026-10-22 | 2026-11-22 | ALIGNED |
| F08 | Size-limit wording | AGENTS.md guide vs Config advanced / Config reference | 2026-10-22 | 2026-11-22 | DOCS CONFLICT (minor): guide says *combined* size reaching `project_doc_max_bytes` stops loading; Config advanced says how much to read from *each* `AGENTS.md`; Config reference says maximum bytes read from `AGENTS.md`. Repo is under either reading |
| F09 | `.agents/skills/crypto-mcp-research/SKILL.md:1-4` frontmatter: line 1 `---`, `name`, `description`, then `# Crypto MCP Research` at line 4; **no closing `---`** (28 lines, one `---`) | Build skills | 2026-10-22 | 2026-10-29 | DRIFT. Docs show frontmatter delimited by `---` before and after `name`/`description`. Minimal fix: insert a `---` line after line 3. Absent already at `39e8e88`; present at `a13a8c6` |
| F10 | `SKILL.md:2-3` `name: crypto-mcp-research`, `description` present | Build skills | 2026-10-22 | 2026-11-22 | ALIGNED |
| F11 | Skill dir `references/`, `assets/`, `scripts/` | Build skills | 2026-10-22 | 2026-11-22 | ALIGNED (optional dirs) |
| F12 | Skill dir `evaluation/`, `tests/` | Build skills | 2026-10-22 | 2026-11-22 | NOT IN DOCS (harmless) |
| F13 | `AGENTS.md` CRLF in checkout vs LF in index (`i/lf w/crlf`, no `.gitattributes`) | AGENTS.md guide | 2026-10-22 | 2026-11-22 | NOT IN DOCS |
| F14 | `.codex/agents/*.toml` in an untrusted project | Config basics | 2026-10-22 | 2026-11-22 | NOT IN DOCS (docs name config, hooks and rules as skipped) |
| F15 | `.agents/` and `.codex/` read-only in `workspace-write` | Approvals and security | 2026-10-22 | 2026-11-22 | ALIGNED (rule as documented; no repo content depends on writing there on main) |
| F16 | New on main: `scripts/validate_review_classification.py`, `tests/test_review_classification.py`, `references/review-classification.md`, `assets/review-classification-record.v1.schema.json`; no hooks/rules configured in `.codex/` | Build skills, Hooks, Rules | 2026-10-22 | 2026-11-22 | ALIGNED for placement; the validator's claim that Codex does not route dispatch through it is consistent with no hook configured |

Counts: ALIGNED 11 (F01-F07, F10, F11, F15, F16), DRIFT 1 (F09), DOCS CONFLICT 1 (F08), NOT IN DOCS 3 (F12-F14). Total 16.

Not repo surfaces (documented mechanisms, none configured): lifecycle hooks (`<repo>/.codex/hooks.json` or `[hooks]`, `PreToolUse` can deny; docs call them "a useful guardrail, not a complete enforcement boundary") and `prefix_rule` rules (experimental, shell commands only). Adopting either would be a `POLICY_DECISION`.

## Prior verification (preserved)

**2026-10-18** (earlier tree, `learn.chatgpt.com` Subagents, AGENTS.md guide, Build skills, Config basics, Config reference, MCP, Approvals and security): same surfaces recorded ALIGNED (root `AGENTS.md` then 9,266 B); CRLF vs LF NOT IN DOCS; untrusted-project agent-file behavior NOT IN DOCS; `.agents/`/`.codex/` protection UNVERIFIED for untracked `.agents/research-runs/20261017/`; hooks, rules, permission profiles, Windows sandbox NOT ASSESSED.

**2026-10-22, dirty tree** (branch `claude/codex-alignment-audit`, commit `40a2fc2`, base `a13a8c6` plus uncommitted changes): same results, but `SKILL.md` had a closing `---` at line 4 (ALIGNED) and `tests/fixtures/instruction-invariants-v1.json` was reported absent. Both differ on `origin/main`: the closing delimiter is missing (F09) and the fixture exists at `.agents/skills/crypto-mcp-research/tests/fixtures/instruction-invariants-v1.json`. That run's nested `AGENTS.md` files (`.codex/`, skill dir, `paper-testing/`) are untracked in that tree and not on main.
