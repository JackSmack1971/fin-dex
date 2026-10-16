# Fin-Dex empirical evaluation artifacts

These files implement the targets in `../references/evaluation-plan.md` without claiming that they have been run:

- `corpus-v1.json`: frozen inputs, expected behavior, evidence requirements, scoring rules, failure criteria, and stable case IDs (20 positive routing, 20 negative routing, 10 neighbors, 10 research, 5 provider failure).
- `fixtures-v1.json`: explicit synthetic provider/routing fixtures. Synthetic observations are test data, not live provider evidence.
- `protocol-v1.json`: fixed baseline/treatment conditions, rubric, adjudication process, consequential-error definition, repetition counts, and targets.
- `corpus-digests-v1.json`: canonical SHA-256 digests for the frozen corpus, fixture set, and protocol.
- `result-manifest-template-v1.json`: a digest-bound, unexecuted result template. The preparation command generates a full matrix.

Run static corpus validation from the skill directory:

```powershell
python scripts/evaluate_workflow.py evaluation/corpus-v1.json
```

Prepare all 450 baseline/treatment trial slots as `UNVALIDATED` records:

```powershell
python scripts/evaluate_workflow.py evaluation/corpus-v1.json --prepare --protocol evaluation/protocol-v1.json --output evaluation/run-manifest.json
```

Preparation builds the paired matrix; it does not execute research agents. Carry out each trial with native Codex capabilities, then record the execution evidence and ratings in the manifest before scoring.

Score retained trials:

```powershell
python scripts/evaluate_workflow.py evaluation/corpus-v1.json path/to/result-manifest.json --output path/to/score.json
```

The manifest's `corpus_sha256` is SHA-256 over canonical JSON for the entire corpus object; `fixtures_sha256` is SHA-256 over canonical JSON for its `fixtures` object; `protocol_sha256` binds the full protocol artifact. Canonical JSON uses UTF-8, sorted keys, compact separators, preserved Unicode, and rejects NaN/Infinity. The separate `corpus-digests-v1.json` records the frozen values.

Record each trial's condition, case, replicate, fixture ID, evidence state, completion status, provenance, and results. Run the same case/replicate/fixture under both conditions. Complete trials bind a shared `paired_context`: prompt and fixture SHA-256, model/runtime ID, base-instruction SHA-256, and tool-permission SHA-256. The baseline omits the Fin-Dex skill and multi-agent review; treatment makes the skill available and uses selected specialists, synthesis, and both independent reviewers for research/failure scenarios. These recorded setup fields make pairing mismatches machine-detectable, while the harness does not independently prove an asserted native run happened. The harness scores retained trial records; it does not launch native agents or providers.

Routing rates remain `UNVALIDATED` unless all 50 routing cases have exactly the required three completed replicate IDs for that condition. Case activation uses a two-of-three majority. Research and provider-failure error reduction remains `UNMEASURED` unless all 15 scenarios have at least five complete, matching baseline/treatment replicate pairs, each error dimension is measured, and baseline error count is nonzero. Missing trials remain visible and do not reduce a required denominator.

Objective scores derive from explicit counts for asset identity errors, timestamp/unit mismatches, source-independence errors, and unauthorized external operations. Unsupported-claim counts require distinct claim IDs backed by failed fixture assertions or independent adjudication. Access/fee honesty, conflict handling, and completion calibration require two distinct raters; preserve both ratings and document disagreements and adjudication. Thesis quality and material omissions also require independent review where applicable. Latency and tool-call performance are reported as unmeasured unless supported by recorded native execution. An `UNAUTHORIZED_EXTERNAL_OPERATIONS` count is critical regardless of aggregate scores.

Task success is separately rated by the same two independent raters against the case's expected behavior and shared fixture. The scorer reports a task-success rate only when all required scenario trials have adjudicated PASS/FAIL outcomes.

`STATIC_VALIDATION` proves artifact structure only. `FIXTURE_REPLAY` proves deterministic behavior over retained/synthetic fixtures only. Neither establishes agent performance. Only recorded native executions may use `LIVE_AGENT_EVALUATION`; passing infrastructure tests does not demonstrate Fin-Dex superiority. The 25% relative reduction is a hypothesis and is undefined when the paired baseline error count is zero.
