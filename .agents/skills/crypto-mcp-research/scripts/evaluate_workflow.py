#!/usr/bin/env python3
"""Offline, deterministic evaluation contracts for the Fin-Dex workflow.

This module validates the frozen case corpus and scores retained trial records.
It never launches agents or contacts providers; fixture replay is not live
agent performance evidence.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

CONDITIONS = {"BASELINE", "FIN_DEX"}
EVIDENCE_STATES = {"STATIC_VALIDATION", "FIXTURE_REPLAY", "LIVE_AGENT_EVALUATION"}
ROUTING_COUNTS = {"routing_positive": 20, "routing_negative": 20, "neighbor": 10}
SCENARIO_COUNTS = {"research": 10, "provider_failure": 5}
ROUTING_REPEATS = 3
SCENARIO_REPEATS = 5
RUBRIC = (
    "asset_identity", "time_normalization", "source_provenance",
    "access_fee_honesty", "conflict_handling", "completion_calibration",
    "unauthorized_calls",
)
HUMAN_RUBRIC = ("access_fee_honesty", "conflict_handling", "completion_calibration")
ERROR_COUNTS = ("unsupported_consequential_claims", "asset_identity_errors",
                "timestamp_unit_mismatches", "source_independence_errors",
                "unauthorized_external_operations")


def load_json(path):
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)


def canonical_digest(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_corpus(corpus):
    errors = []
    if not isinstance(corpus, dict) or corpus.get("schema_version") != 1:
        return ["corpus must be an object with schema_version 1"]
    if corpus.get("freeze_status") != "FROZEN_BEFORE_EVALUATION":
        errors.append("freeze_status must be FROZEN_BEFORE_EVALUATION")
    cases = corpus.get("cases")
    if not isinstance(cases, list):
        return ["cases must be an array"]
    if corpus.get("case_count") != len(cases):
        errors.append("case_count does not match cases array length")
    ids, counts = set(), {}
    required = {"case_id", "category", "inputs", "expected_behavior",
                "evidence_requirements", "scoring_rules", "failure_conditions",
                "fixture_id"}
    for index, case in enumerate(cases):
        where = f"cases[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{where} must be an object")
            continue
        missing = required - case.keys()
        if missing:
            errors.append(f"{where} missing fields: {', '.join(sorted(missing))}")
            continue
        case_id, category = case["case_id"], case["category"]
        if not isinstance(case_id, str) or not case_id:
            errors.append(f"{where}.case_id must be nonempty")
        elif case_id in ids:
            errors.append(f"duplicate case_id: {case_id}")
        else:
            ids.add(case_id)
        if not isinstance(category, str):
            errors.append(f"{where}.category must be a string")
        else:
            counts[category] = counts.get(category, 0) + 1
        for key in ("inputs", "expected_behavior", "evidence_requirements", "scoring_rules", "failure_conditions"):
            value = case[key]
            if not isinstance(value, (str, list, dict)) or not value:
                errors.append(f"{where}.{key} must be a nonempty string, list, or object")
        if not isinstance(case["fixture_id"], str) or not case["fixture_id"]:
            errors.append(f"{where}.fixture_id must be nonempty")
    expected = ROUTING_COUNTS | SCENARIO_COUNTS
    for category, count in expected.items():
        if counts.get(category, 0) != count:
            errors.append(f"{category} count must be {count}, got {counts.get(category, 0)}")
    unexpected = set(counts) - set(expected)
    if unexpected:
        errors.append("unexpected categories: " + ", ".join(sorted(unexpected)))
    fixtures = corpus.get("fixtures")
    if not isinstance(fixtures, dict):
        errors.append("fixtures must be an object")
        fixtures = {}
    if corpus.get("fixture_count") != len(fixtures):
        errors.append("fixture_count does not match fixtures object size")
    for case in cases:
        if isinstance(case, dict) and isinstance(case.get("fixture_id"), str) and case.get("fixture_id") not in fixtures:
            errors.append(f"{case.get('case_id')}: missing fixture {case.get('fixture_id')}")
    for fixture_id, fixture in fixtures.items():
        if (not isinstance(fixture, dict) or fixture.get("version") != 1
                or not isinstance(fixture.get("provenance"), str)
                or fixture.get("provenance") not in {"SYNTHETIC", "RETAINED"}):
            errors.append(f"fixture {fixture_id}: version 1 and provenance SYNTHETIC or RETAINED required")
    return errors


def validate_protocol(protocol):
    errors = []
    if not isinstance(protocol, dict):
        return ["protocol must be an object"]
    expected = {"routing_repetitions": ROUTING_REPEATS,
                "scenario_repetitions_per_condition": SCENARIO_REPEATS}
    for key, value in expected.items():
        if protocol.get(key) != value:
            errors.append(f"{key} must be {value}")
    if protocol.get("baseline_condition") != "BASELINE" or protocol.get("treatment_condition") != "FIN_DEX":
        errors.append("condition identities must be BASELINE and FIN_DEX")
    dimensions = protocol.get("rubric_dimensions")
    if not isinstance(dimensions, list) or any(not isinstance(value, str) for value in dimensions) or set(dimensions) != set(RUBRIC):
        errors.append("rubric_dimensions must contain the seven frozen dimensions")
    if protocol.get("relative_error_reduction") != "1 - treatment_error_rate / baseline_error_rate; error count is unsupported consequential claims + asset identity errors + timestamp/unit mismatches + source-independence classification errors; calculate only for all 75 complete paired scenario trials with matching case, replicate and fixture IDs; undefined when baseline error rate is zero or any paired dimension is UNMEASURED":
        errors.append("relative_error_reduction contract mismatch")
    return errors


def _validate_trial(trial, case_by_id, fixtures, errors, index):
    where = f"trials[{index}]"
    if not isinstance(trial, dict):
        errors.append(f"{where} must be an object")
        return
    required = {"case_id", "condition", "replicate", "fixture_id", "evidence_state", "status",
                "paired_context", "condition_setup"}
    missing = required - trial.keys()
    if missing:
        errors.append(f"{where} missing fields: {', '.join(sorted(missing))}")
        return
    if not isinstance(trial["case_id"], str):
        errors.append(f"{where}.case_id must be a string")
        return
    case = case_by_id.get(trial["case_id"])
    if case is None:
        errors.append(f"{where} unknown case_id")
        return
    if not isinstance(trial["condition"], str) or trial["condition"] not in CONDITIONS:
        errors.append(f"{where} invalid condition")
    if trial["fixture_id"] != case["fixture_id"]:
        errors.append(f"{where} fixture mismatch")
    if not isinstance(trial["evidence_state"], str) or trial["evidence_state"] not in EVIDENCE_STATES:
        errors.append(f"{where} invalid evidence_state")
    if not isinstance(trial["status"], str) or trial["status"] not in {"COMPLETE", "INCOMPLETE", "UNVALIDATED"}:
        errors.append(f"{where} invalid status")
    if not isinstance(trial["replicate"], int) or isinstance(trial["replicate"], bool) or trial["replicate"] < 1:
        errors.append(f"{where} replicate must be positive integer")
    if case["category"] in {"routing_positive", "routing_negative", "neighbor"} and isinstance(trial["replicate"], int) and trial["replicate"] > ROUTING_REPEATS:
        errors.append(f"{where}: routing corpus is fixed at exactly three replicates")
    if trial["status"] != "COMPLETE":
        return
    context = trial["paired_context"]
    context_fields = {"input_sha256", "fixture_sha256", "model_runtime_id",
                      "base_instructions_sha256", "tool_permissions_sha256"}
    if not isinstance(context, dict) or set(context) != context_fields:
        errors.append(f"{where}.paired_context must contain exact matched-context fields")
    else:
        if context["input_sha256"] != canonical_digest(case["inputs"]):
            errors.append(f"{where}.paired_context input digest mismatch")
        if context["fixture_sha256"] != canonical_digest(fixtures[case["fixture_id"]]):
            errors.append(f"{where}.paired_context fixture content digest mismatch")
        for field in ("input_sha256", "fixture_sha256", "base_instructions_sha256", "tool_permissions_sha256"):
            value = context[field]
            if not isinstance(value, str) or len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
                errors.append(f"{where}.paired_context.{field} must be lowercase SHA-256")
        if not isinstance(context["model_runtime_id"], str) or not context["model_runtime_id"]:
            errors.append(f"{where}.paired_context.model_runtime_id must be nonempty")
    setup = trial["condition_setup"]
    setup_fields = {"fin_dex_skill_available", "multi_agent_review_enabled", "selected_specialists", "reviewer_ids"}
    if not isinstance(setup, dict) or set(setup) != setup_fields:
        errors.append(f"{where}.condition_setup must contain exact condition fields")
    else:
        is_treatment = trial["condition"] == "FIN_DEX"
        category = case["category"]
        scenario = category in {"research", "provider_failure"}
        if setup["fin_dex_skill_available"] is not is_treatment:
            errors.append(f"{where}: specialized skill availability contradicts condition")
        if setup["multi_agent_review_enabled"] is not (is_treatment and scenario):
            errors.append(f"{where}: multi-agent review setup contradicts condition and case")
        specialists = setup["selected_specialists"]
        reviewers = setup["reviewer_ids"]
        valid_specialists = {"market_analyst", "liquidity_analyst", "onchain_analyst", "catalyst_analyst"}
        if not isinstance(specialists, list) or any(not isinstance(x, str) or x not in valid_specialists for x in specialists) or len(set(specialists)) != len(specialists) or len(specialists) > 3:
            errors.append(f"{where}.condition_setup.selected_specialists must be unique relevant specialist IDs, at most three")
        if not isinstance(reviewers, list) or any(not isinstance(x, str) for x in reviewers):
            errors.append(f"{where}.condition_setup.reviewer_ids must be an array of reviewer IDs")
        elif is_treatment and scenario and (not specialists or len(reviewers) != 2 or set(reviewers) != {"adversarial_reviewer", "evidence_verifier"}):
            errors.append(f"{where}: research treatment requires selected specialists and both independent reviewers")
        elif (not is_treatment or not scenario) and (specialists or reviewers):
            errors.append(f"{where}: baseline and routing trials must not dispatch specialists or reviewers")
    if trial["evidence_state"] == "LIVE_AGENT_EVALUATION" and trial.get("provenance") != "recorded_native_agent_execution":
        errors.append(f"{where} live evaluation requires native execution provenance")
    if trial["evidence_state"] != "LIVE_AGENT_EVALUATION" and trial.get("provenance") == "recorded_native_agent_execution":
        errors.append(f"{where} fixture/static evidence cannot claim native execution provenance")
    expected_fields = {"routing_positive": {"skill_activated"},
                       "routing_negative": {"skill_activated"},
                       "neighbor": {"skill_activated"},
                       "research": set(ERROR_COUNTS) | {"human_judgment", "unsupported_claim_basis", "premature_completion", "tool_calls", "latency_ms"},
                       "provider_failure": set(ERROR_COUNTS) | {"human_judgment", "unsupported_claim_basis", "recovered", "premature_completion", "tool_calls", "latency_ms"}}
    needed = expected_fields[case["category"]]
    if "result" not in trial or not isinstance(trial["result"], dict):
        errors.append(f"{where} complete trial requires result object")
        return
    absent = needed - trial["result"].keys()
    if absent:
        errors.append(f"{where}.result missing fields: {', '.join(sorted(absent))}")
        return
    result = trial["result"]
    for key, value in result.items():
        if key == "skill_activated" and not isinstance(value, bool):
            errors.append(f"{where}.result.{key} must be boolean")
        elif key in {"recovered", "premature_completion"} and not isinstance(value, bool) and value != "UNMEASURED":
            errors.append(f"{where}.result.{key} must be boolean")
        elif key in set(ERROR_COUNTS) | {"tool_calls"}:
            if value != "UNMEASURED" and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                errors.append(f"{where}.result.{key} must be a nonnegative integer")
        elif key == "latency_ms" and value is not None and (not isinstance(value, (int, float)) or value < 0 or not math.isfinite(value)):
            errors.append(f"{where}.result.latency_ms must be nonnegative finite number or null")
    if case["category"] in {"research", "provider_failure"}:
        basis = result.get("unsupported_claim_basis")
        count = result.get("unsupported_consequential_claims")
        if not isinstance(basis, list) or any(not isinstance(item, str) or not item for item in basis):
            errors.append(f"{where}.result.unsupported_claim_basis must be an array of distinct claim IDs")
        elif isinstance(count, int) and len(set(basis)) != count:
            errors.append(f"{where}: unsupported claim basis count must equal distinct unsupported claim IDs")
        judgment = result.get("human_judgment")
        if not isinstance(judgment, dict):
            errors.append(f"{where}.result.human_judgment must be an object")
        else:
            rater_ids = judgment.get("rater_ids")
            scores = judgment.get("scores")
            if not isinstance(rater_ids, list) or len(rater_ids) != 2 or not all(isinstance(x, str) and x for x in rater_ids) or len(set(rater_ids)) != 2:
                errors.append(f"{where}.result.human_judgment.rater_ids must contain two distinct raters")
            if not isinstance(scores, dict) or set(scores) != set(HUMAN_RUBRIC):
                errors.append(f"{where}.result.human_judgment.scores must cover the three human rubric dimensions")
            else:
                for dimension, record in list(scores.items()) + [("task_success", judgment.get("task_success"))]:
                    if not isinstance(record, dict) or set(record) != {"rater_1", "rater_2", "adjudicated", "adjudication_note"}:
                        errors.append(f"{where}.result.human_judgment.scores.{dimension} has invalid shape")
                        continue
                    if any(not isinstance(record[key], str) or record[key] not in {"PASS", "FAIL", "UNMEASURED"} for key in ("rater_1", "rater_2", "adjudicated")):
                        errors.append(f"{where}.result.human_judgment.scores.{dimension} ratings must be PASS, FAIL, or UNMEASURED")
                    elif record["rater_1"] != record["rater_2"] and (record["adjudicated"] not in {"PASS", "FAIL", "UNMEASURED"} or not record["adjudication_note"]):
                        errors.append(f"{where}.result.human_judgment.scores.{dimension} disagreement requires documented adjudication")
                    elif record["rater_1"] == record["rater_2"] and record["adjudicated"] != record["rater_1"]:
                        errors.append(f"{where}.result.human_judgment.scores.{dimension} agreement must retain the agreed rating")


def score(corpus, manifest):
    if not isinstance(manifest, dict):
        return {"errors": ["result manifest must be an object"]}
    errors = validate_corpus(corpus) + validate_protocol(manifest.get("protocol"))
    if errors:
        return {"errors": errors}
    frozen_protocol_path = Path(__file__).resolve().parents[1]/"evaluation"/"protocol-v1.json"
    try:
        frozen_protocol = load_json(frozen_protocol_path)
        if manifest.get("protocol_sha256") != canonical_digest(frozen_protocol):
            errors.append("protocol_sha256 does not match the frozen protocol-v1.json")
        protocol_view = {key: frozen_protocol[key] for key in (
            "routing_repetitions", "scenario_repetitions_per_condition", "baseline_condition",
            "treatment_condition", "rubric_dimensions", "relative_error_reduction")}
        if manifest.get("protocol") != protocol_view:
            errors.append("result manifest protocol differs from the frozen protocol")
    except (OSError, ValueError, KeyError) as exc:
        errors.append(f"cannot load frozen evaluation protocol: {exc}")
    if manifest.get("schema_version") != 1:
        errors.append("result manifest schema_version must be 1")
    if not isinstance(manifest.get("evaluation_mode"), str) or manifest.get("evaluation_mode") not in EVIDENCE_STATES:
        errors.append("evaluation_mode must explicitly identify STATIC_VALIDATION, FIXTURE_REPLAY, or LIVE_AGENT_EVALUATION")
    if not isinstance(manifest.get("evidence_state"), str) or manifest.get("evidence_state") not in EVIDENCE_STATES:
        errors.append("evidence_state must explicitly identify STATIC_VALIDATION, FIXTURE_REPLAY, or LIVE_AGENT_EVALUATION")
    if manifest.get("evidence_state") != manifest.get("evaluation_mode"):
        errors.append("evaluation_mode and evidence_state must match")
    if manifest.get("corpus_sha256") != canonical_digest(corpus):
        errors.append("corpus_sha256 does not match the supplied frozen corpus")
    if manifest.get("fixtures_sha256") != canonical_digest(corpus.get("fixtures")):
        errors.append("fixtures_sha256 does not match the supplied frozen fixtures")
    if not isinstance(manifest.get("baseline_provenance"), dict) or not manifest["baseline_provenance"].get("description"):
        errors.append("baseline_provenance.description is required")
    case_by_id = {case["case_id"]: case for case in corpus.get("cases", []) if isinstance(case, dict) and "case_id" in case}
    trials = manifest.get("trials")
    if not isinstance(trials, list):
        errors.append("trials must be an array")
        trials = []
    keys = set()
    for index, trial in enumerate(trials):
        _validate_trial(trial, case_by_id, corpus["fixtures"], errors, index)
        if isinstance(trial, dict) and trial.get("status") == "COMPLETE" and manifest.get("evidence_state") == "STATIC_VALIDATION":
            errors.append(f"trials[{index}] STATIC_VALIDATION cannot score completed performance trials")
        if isinstance(trial, dict) and trial.get("status") == "COMPLETE" and trial.get("evidence_state") != manifest.get("evidence_state"):
            errors.append(f"trials[{index}] evidence_state does not match result manifest")
        if isinstance(trial, dict):
            case_id, condition, replicate = trial.get("case_id"), trial.get("condition"), trial.get("replicate")
            if isinstance(case_id, str) and isinstance(condition, str) and isinstance(replicate, int) and not isinstance(replicate, bool):
                key = (case_id, condition, replicate)
                if key in keys:
                    errors.append(f"duplicate trial identity: {key}")
                keys.add(key)
    if errors:
        return {"errors": errors}

    def enough(case, condition):
        needed = ROUTING_REPEATS if case["category"].startswith("routing_") or case["category"] == "neighbor" else SCENARIO_REPEATS
        values = [t for t in trials if t["case_id"] == case["case_id"] and t["condition"] == condition and t["status"] == "COMPLETE"]
        reps = {t["replicate"] for t in values}
        complete = reps == set(range(1, needed + 1)) if needed == ROUTING_REPEATS else len(reps) >= needed
        return values if complete else []

    by_condition, pairs = {}, {}
    for condition in sorted(CONDITIONS):
        eligible = {case["case_id"]: enough(case, condition) for case in corpus["cases"]}
        validated = {cid: vals for cid, vals in eligible.items() if vals}
        counts = {category: sum(1 for case in corpus["cases"] if case["category"] == category and case["case_id"] in validated)
                  for category in ROUTING_COUNTS | SCENARIO_COUNTS}
        scenarios_complete = all(counts[k] == v for k, v in SCENARIO_COUNTS.items())
        positives = [case for case in corpus["cases"] if case["category"] == "routing_positive" and case["case_id"] in validated]
        negatives = [case for case in corpus["cases"] if case["category"] == "routing_negative" and case["case_id"] in validated]
        neighbors = [case for case in corpus["cases"] if case["category"] == "neighbor" and case["case_id"] in validated]
        pred = lambda case: sum(t["result"]["skill_activated"] for t in validated[case["case_id"]]) >= 2
        tp = sum(pred(case) for case in positives)
        fn = len(positives) - tp
        fp = sum(pred(case) for case in negatives)
        tn = len(negatives) - fp
        neighbor_fp = sum(pred(case) for case in neighbors)
        routing_complete = all(counts[k] == v for k, v in ROUTING_COUNTS.items())
        precision = tp / (tp + fp) if routing_complete and tp + fp else None
        recall = tp / (tp + fn) if routing_complete and positives else None
        neighbor_rate = neighbor_fp / len(neighbors) if routing_complete and neighbors else None
        research_trials = [t for t in trials if t["condition"] == condition and t["status"] == "COMPLETE" and case_by_id[t["case_id"]]["category"] in {"research", "provider_failure"}]
        def total(field):
            if not scenarios_complete:
                return None
            values = [t["result"].get(field, "UNMEASURED") for t in research_trials]
            return None if not values or "UNMEASURED" in values else sum(values)
        latencies = [t["result"]["latency_ms"] for t in research_trials if t["result"].get("latency_ms") is not None]
        objective_rubric = {"asset_identity": "asset_identity_errors",
                            "time_normalization": "timestamp_unit_mismatches",
                            "source_provenance": "source_independence_errors",
                            "unauthorized_calls": "unauthorized_external_operations"}
        rubric = {}
        for dimension in RUBRIC:
            if dimension in objective_rubric:
                field = objective_rubric[dimension]
                values = [t["result"].get(field, "UNMEASURED") for t in research_trials]
                rubric[dimension] = {"pass": sum(isinstance(v, int) and v == 0 for v in values),
                                     "fail": sum(isinstance(v, int) and v > 0 for v in values),
                                     "unmeasured": sum(v == "UNMEASURED" for v in values)}
            else:
                rubric[dimension] = {state.lower(): sum(
                    t["result"]["human_judgment"]["scores"][dimension]["adjudicated"] == state
                    for t in research_trials) for state in ("PASS", "FAIL", "UNMEASURED")}
            rubric[dimension].update({"status": "MEASURED" if scenarios_complete else "UNVALIDATED",
                                      "observed_trials": len(research_trials),
                                      "required_trials": SCENARIO_REPEATS * sum(SCENARIO_COUNTS.values())})
        failures = [t for t in research_trials if case_by_id[t["case_id"]]["category"] == "provider_failure"]
        recovery_measured = [t for t in failures if isinstance(t["result"].get("recovered"), bool)]
        task_success_values = [t["result"]["human_judgment"]["task_success"]["adjudicated"] for t in research_trials]
        task_success_measured = scenarios_complete and len(task_success_values) >= SCENARIO_REPEATS * sum(SCENARIO_COUNTS.values()) and all(v in {"PASS", "FAIL"} for v in task_success_values)
        unsupported_total = total("unsupported_consequential_claims")
        by_condition[condition] = {
            "evidence_state": manifest.get("evidence_state"), "validated_case_counts": counts,
            "routing": {"status": "MEASURED" if routing_complete else "UNVALIDATED",
                        "required_case_counts": ROUTING_COUNTS,
                        "true_positive": tp, "false_negative": fn, "false_positive": fp, "true_negative": tn,
                        "precision": precision, "recall": recall, "neighbor_false_activation_rate": neighbor_rate},
            "research_trials_scored": len(research_trials), "rubric": rubric,
            "scenario_coverage_status": "MEASURED" if scenarios_complete else "UNVALIDATED",
            "required_scenario_case_counts": SCENARIO_COUNTS,
            "task_success_rate": sum(v == "PASS" for v in task_success_values) / len(task_success_values) if task_success_measured else None,
            "task_success_status": "MEASURED" if task_success_measured else "UNVALIDATED" if not scenarios_complete else "UNMEASURED",
            "task_success_adjudicated_trials": sum(v in {"PASS", "FAIL"} for v in task_success_values),
            "task_success_required_trials": SCENARIO_REPEATS * sum(SCENARIO_COUNTS.values()),
            "unsupported_consequential_claims": total("unsupported_consequential_claims"),
            "unsupported_consequential_claims_status": "UNVALIDATED" if not scenarios_complete else "UNMEASURED" if total("unsupported_consequential_claims") is None else "MEASURED",
            "unsupported_consequential_claim_trial_rate": (sum(t["result"]["unsupported_consequential_claims"] > 0 for t in research_trials) / len(research_trials)) if unsupported_total is not None and research_trials else None,
            "unsupported_consequential_claim_trial_rate_denominator": len(research_trials) if unsupported_total is not None else 0,
            "asset_identity_errors": total("asset_identity_errors"),
            "asset_identity_errors_status": "UNVALIDATED" if not scenarios_complete else "UNMEASURED" if total("asset_identity_errors") is None else "MEASURED",
            "timestamp_unit_mismatches": total("timestamp_unit_mismatches"),
            "timestamp_unit_mismatches_status": "UNVALIDATED" if not scenarios_complete else "UNMEASURED" if total("timestamp_unit_mismatches") is None else "MEASURED",
            "source_independence_errors": total("source_independence_errors"),
            "source_independence_errors_status": "UNVALIDATED" if not scenarios_complete else "UNMEASURED" if total("source_independence_errors") is None else "MEASURED",
            "provider_failure_recovery_rate": (sum(t["result"]["recovered"] for t in recovery_measured) / len(recovery_measured)) if scenarios_complete and len(recovery_measured) == len(failures) and len(failures) >= SCENARIO_COUNTS["provider_failure"] * SCENARIO_REPEATS else None,
            "provider_failure_recovery_status": "MEASURED" if scenarios_complete and len(recovery_measured) == len(failures) and len(failures) >= SCENARIO_COUNTS["provider_failure"] * SCENARIO_REPEATS else "UNVALIDATED" if not scenarios_complete else "UNMEASURED",
            "premature_completions": sum(t["result"].get("premature_completion") is True for t in research_trials),
            "premature_completion_status": "UNVALIDATED" if not scenarios_complete else "UNMEASURED" if not research_trials or any(t["result"].get("premature_completion") == "UNMEASURED" for t in research_trials) else "MEASURED",
            "tool_calls": total("tool_calls"),
            "tool_call_measurement_status": "UNVALIDATED" if not scenarios_complete else "UNMEASURED" if total("tool_calls") is None or manifest.get("evidence_state") != "LIVE_AGENT_EVALUATION" else "MEASURED_FROM_RECORDED_TRIALS",
            "completion_latency_ms_mean": sum(latencies) / len(latencies) if latencies else None,
            "latency_measured_trials": len(latencies) if manifest.get("evidence_state") == "LIVE_AGENT_EVALUATION" else 0,
            "completion_latency_required_scenario_trials": SCENARIO_REPEATS * sum(SCENARIO_COUNTS.values()),
            "completion_latency_status": "MEASURED_FROM_RECORDED_TRIALS" if len(latencies) >= SCENARIO_REPEATS * sum(SCENARIO_COUNTS.values()) and manifest.get("evidence_state") == "LIVE_AGENT_EVALUATION" else "PARTIAL" if latencies and manifest.get("evidence_state") == "LIVE_AGENT_EVALUATION" else "UNMEASURED",
            "unauthorized_external_operations": total("unauthorized_external_operations"),
            "unauthorized_external_operations_status": "UNVALIDATED" if not scenarios_complete else "UNMEASURED" if total("unauthorized_external_operations") is None else "MEASURED",
        }
        pairs[condition] = validated
    paired_cases = set(pairs["BASELINE"]) & set(pairs["FIN_DEX"])
    baseline_errors = treatment_errors = 0
    pair_measurements_complete = True
    paired_n = 0
    paired_scenario_cases = 0
    for case_id in paired_cases:
        baseline = pairs["BASELINE"][case_id]
        treatment = pairs["FIN_DEX"][case_id]
        baseline_by_rep = {x["replicate"]: x for x in baseline}
        treatment_by_rep = {x["replicate"]: x for x in treatment}
        common_replicates = set(baseline_by_rep) & set(treatment_by_rep)
        for replicate in common_replicates:
            if baseline_by_rep[replicate]["fixture_id"] != treatment_by_rep[replicate]["fixture_id"]:
                return {"errors": [f"paired fixture mismatch: {case_id} replicate {replicate}"]}
            if baseline_by_rep[replicate].get("paired_context") != treatment_by_rep[replicate].get("paired_context"):
                return {"errors": [f"paired context mismatch: {case_id} replicate {replicate}"]}
        category = case_by_id[case_id]["category"]
        if category in {"research", "provider_failure"}:
            if len(common_replicates) < SCENARIO_REPEATS:
                continue
            paired_scenario_cases += 1
            for replicate in sorted(common_replicates):
                paired_n += 1
                # Consequential error is one adjudicated or deterministically
                # identified unsupported claim or identity/time/unit/provenance error.
                fields = ("unsupported_consequential_claims", "asset_identity_errors",
                          "timestamp_unit_mismatches", "source_independence_errors")
                baseline_values = [baseline_by_rep[replicate]["result"].get(field, "UNMEASURED") for field in fields]
                treatment_values = [treatment_by_rep[replicate]["result"].get(field, "UNMEASURED") for field in fields]
                if "UNMEASURED" in baseline_values or "UNMEASURED" in treatment_values:
                    pair_measurements_complete = False
                else:
                    baseline_errors += sum(baseline_values)
                    treatment_errors += sum(treatment_values)
    required_scenario_pairs = sum(SCENARIO_COUNTS.values()) * SCENARIO_REPEATS
    reduction = None if paired_n < required_scenario_pairs or paired_scenario_cases != sum(SCENARIO_COUNTS.values()) or not pair_measurements_complete or baseline_errors == 0 else 1 - treatment_errors / baseline_errors
    expected_trial_keys = set()
    for case in corpus["cases"]:
        repetitions = ROUTING_REPEATS if case["category"] in {"routing_positive", "routing_negative", "neighbor"} else SCENARIO_REPEATS
        for condition in CONDITIONS:
            for replicate in range(1, repetitions + 1):
                expected_trial_keys.add((case["case_id"], condition, replicate))
    trial_by_key = {(t["case_id"], t["condition"], t["replicate"]): t for t in trials}
    complete_required_slots = sum(trial_by_key.get(key, {}).get("status") == "COMPLETE" for key in expected_trial_keys)
    unvalidated_required_slots = len(expected_trial_keys) - complete_required_slots
    extra_unvalidated_records = sum(t["status"] != "COMPLETE" for key, t in trial_by_key.items() if key not in expected_trial_keys)
    return {"errors": [], "evaluation_mode": manifest.get("evaluation_mode"),
            "evidence_state": manifest.get("evidence_state"), "baseline_provenance": manifest["baseline_provenance"],
            "conditions": by_condition, "paired_scenario_cases": paired_scenario_cases,
            "paired_scenario_completed_replicates": paired_n,
            "relative_error_reduction": {"value": reduction, "status": "UNMEASURED" if reduction is None else "MEASURED",
                                          "baseline_error_count": baseline_errors, "treatment_error_count": treatment_errors,
                                          "paired_trial_denominator": paired_n, "required_paired_trial_denominator": required_scenario_pairs,
                                          "all_error_dimensions_measured": pair_measurements_complete,
                                          "consequential_error_definition": "Per trial: count unsupported consequential claims plus asset identity errors plus timestamp/unit mismatches plus source-independence classification errors. Each count must be supported by deterministic fixture assertions or independent adjudication; the harness does not infer research quality from prose.",
                                          "formula": "1 - treatment_error_rate / baseline_error_rate"},
            "required_trial_slots": len(expected_trial_keys),
            "completed_required_trial_slots": complete_required_slots,
            "incomplete_or_unvalidated_trials": unvalidated_required_slots + extra_unvalidated_records,
            "live_agent_claim_permitted": manifest.get("evidence_state") == "LIVE_AGENT_EVALUATION"}


def prepare_manifest(corpus, protocol):
    """Create the fixed paired trial matrix; all entries begin unvalidated."""
    protocol_view = {key: protocol[key] for key in (
        "routing_repetitions", "scenario_repetitions_per_condition",
        "baseline_condition", "treatment_condition", "rubric_dimensions",
        "relative_error_reduction")}
    trials = []
    for case in corpus["cases"]:
        repeats = ROUTING_REPEATS if case["category"] in {"routing_positive", "routing_negative", "neighbor"} else SCENARIO_REPEATS
        for condition in ("BASELINE", "FIN_DEX"):
            for replicate in range(1, repeats + 1):
                trials.append({"case_id": case["case_id"], "condition": condition,
                               "replicate": replicate, "fixture_id": case["fixture_id"],
                               "evidence_state": "STATIC_VALIDATION", "status": "UNVALIDATED"})
    return {"schema_version": 1, "evaluation_id": "replace-with-run-id",
            "evaluation_mode": "STATIC_VALIDATION", "evidence_state": "STATIC_VALIDATION",
            "corpus_sha256": canonical_digest(corpus),
            "fixtures_sha256": canonical_digest(corpus["fixtures"]),
            "protocol_sha256": canonical_digest(protocol),
            "protocol": protocol_view,
            "baseline_provenance": {"description": "Unexecuted trial matrix. Record baseline model/runtime, instructions, tool permissions, and execution evidence before scoring."},
            "trials": trials}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("manifest", type=Path, nargs="?")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--prepare", action="store_true", help="write the full unvalidated paired trial matrix")
    parser.add_argument("--protocol", type=Path, help="protocol JSON required with --prepare")
    args = parser.parse_args()
    corpus = load_json(args.corpus)
    if args.prepare:
        if not args.protocol:
            parser.error("--prepare requires --protocol")
        protocol = load_json(args.protocol)
        result = prepare_manifest(corpus, protocol)
    elif args.manifest:
        result = score(corpus, load_json(args.manifest))
    else:
        result = {"errors": validate_corpus(corpus)}
    payload = json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 1 if result.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
