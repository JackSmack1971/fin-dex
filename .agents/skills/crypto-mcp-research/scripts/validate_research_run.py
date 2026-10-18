#!/usr/bin/env python3
"""Deterministic offline validation and replay for retained research runs."""
import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import math
import pathlib
import sys


def canonical_json(value):
    """Canonical logical JSON: UTF-8, sorted keys, compact, Unicode, finite only."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def digest_bytes(data):
    """Digest physical bytes. For logical JSON use digest_bytes(canonical_json(value))."""
    return hashlib.sha256(data).hexdigest()


def utc(value):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("must be UTC ISO timestamp ending Z")
    result = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    if result.utcoffset() != dt.timedelta(0):
        raise ValueError("must be UTC")
    return result


def formula(name, values):
    """Replay v1 metrics. Inputs are ordered exactly as declared."""
    nums = [float(v) for v in values]
    if not all(math.isfinite(v) for v in nums):
        raise ValueError("inputs must be finite numbers")
    if name == "difference.v1":
        if len(nums) != 2: raise ValueError("formula requires exactly two inputs")
        return nums[1] - nums[0]
    if name == "ratio.v1":
        if len(nums) != 2: raise ValueError("formula requires exactly two inputs")
        if nums[0] == 0: raise ValueError("division by zero")
        return nums[1] / nums[0]
    if name in {"simple_return.v1", "percent_change.v1"}:
        if len(nums) != 2: raise ValueError("formula requires exactly two inputs")
        left, right = nums
        if left == 0: raise ValueError("zero baseline")
        return (right - left) / left * 100.0
    if name == "benchmark_relative_return.v1":
        if len(nums) != 4: raise ValueError("formula requires asset start/end and benchmark start/end")
        a0, a1, b0, b1 = nums
        if a0 == 0 or b0 == 0: raise ValueError("zero baseline")
        benchmark_gross = b1 / b0
        if benchmark_gross == 0: raise ValueError("benchmark gross return is zero; relative return undefined")
        return ((a1 / a0) / benchmark_gross - 1.0) * 100.0
    if name == "bid_ask_spread.v1":
        if len(nums) != 2: raise ValueError("formula requires bid and ask")
        bid, ask = nums
        mid = (bid + ask) / 2.0
        if mid == 0: raise ValueError("zero midpoint")
        return (ask - bid) / mid * 100.0
    if name == "sampled_extrema.v1":
        if not nums: raise ValueError("formula requires at least one sampled value")
        return {"minimum": min(nums), "maximum": max(nums)}
    raise ValueError(f"unsupported formula: {name}")


def _load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _validator(filename, function):
    path = pathlib.Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location("fin_dex_" + filename[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, function)


def validate(run_path, replay=False):
    run_path = pathlib.Path(run_path).resolve()
    errors, blocked = [], []
    try: doc = _load_json(run_path)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc: return [f"manifest: cannot read JSON: {exc}"]
    if not isinstance(doc, dict): return ["manifest: root must be object"]
    required = {"schema_version", "run_id", "task_specification", "timestamps", "asset_identity", "research_horizon", "repository", "methodology_version", "configuration_references", "specialists", "lifecycle", "evidence_ledgers", "synthesis", "reviews", "verdict", "artifacts", "calculations"}
    missing = required - doc.keys()
    if missing: errors.append("manifest: missing fields " + ", ".join(sorted(missing)))
    version = doc.get("schema_version")
    if version not in {1, 2}: errors.append("manifest: unsupported schema_version")
    if version == 2:
        for key in ("lifecycle_id", "artifact_id"):
            if not isinstance(doc.get(key), str) or not doc[key].strip(): errors.append(f"manifest: {key} required for v2")
    for key in ("run_id", "task_specification", "methodology_version"):
        if not isinstance(doc.get(key), str) or not doc[key].strip(): errors.append(f"manifest: {key} must be a nonempty string")
    asset = doc.get("asset_identity")
    if not isinstance(asset, dict) or not isinstance(asset.get("canonical_id"), str) or not asset.get("canonical_id") or asset.get("identity_status") not in {"RESOLVED", "AMBIGUOUS", "UNRESOLVED"}:
        errors.append("asset_identity: canonical_id and valid identity_status required")
    if not isinstance(doc.get("configuration_references"), list) or any(not isinstance(x, str) for x in doc.get("configuration_references", [])):
        errors.append("configuration_references must be an array of strings")
    if doc.get("verdict") not in {"IN_PROGRESS", "NO_VERIFIED_CONCLUSION", "SUPPORTED", "NOT_SUPPORTED"}:
        errors.append("verdict: invalid status")
    if not isinstance(doc.get("artifacts"), list): errors.append("artifacts must be an array")
    if not isinstance(doc.get("calculations"), list): errors.append("calculations must be an array")
    if not isinstance(doc.get("reviews"), list): errors.append("reviews must be an array")
    if not isinstance(doc.get("evidence_ledgers"), list): errors.append("evidence_ledgers must be an array")
    root = run_path.parent
    artifacts, observations = {}, {}
    for i, item in enumerate(doc.get("artifacts", []) if isinstance(doc.get("artifacts", []), list) else []):
        if not isinstance(item, dict): errors.append(f"artifacts[{i}]: expected object"); continue
        name = item.get("path")
        if not isinstance(name, str) or not name: errors.append(f"artifacts[{i}]: path required"); continue
        if name in artifacts: errors.append(f"artifacts[{i}]: duplicate path {name}")
        artifacts[name] = item
        target = (root / name).resolve()
        if root not in target.parents: errors.append(f"artifacts[{i}]: path escapes run directory"); continue
        try: raw = target.read_bytes()
        except OSError: errors.append(f"artifact missing: {name}"); continue
        if digest_bytes(raw) != item.get("sha256"): errors.append(f"artifact digest mismatch: {name}")
        if item.get("logical_sha256") is not None:
            try:
                logical = json.loads(raw.decode("utf-8"))
                if digest_bytes(canonical_json(logical)) != item["logical_sha256"]: errors.append(f"artifact logical digest mismatch: {name}")
            except (UnicodeError, json.JSONDecodeError, ValueError) as exc: errors.append(f"artifact logical JSON invalid: {name}: {exc}")
        if item.get("kind") == "observations":
            try:
                data = json.loads(raw.decode("utf-8")); records = data.get("observations", data) if isinstance(data, dict) else data
                for record in records:
                    if isinstance(record, dict) and isinstance(record.get("id"), str):
                        if record["id"] in observations: errors.append(f"duplicate observation id: {record['id']}")
                        observations[record["id"]] = (record, name)
            except (UnicodeError, json.JSONDecodeError, TypeError): errors.append(f"invalid observations fixture: {name}")

    def reference(name, context):
        if name not in artifacts: errors.append(f"{context}: missing artifact reference {name}")
    ts = doc.get("timestamps", {})
    try: started = utc(ts.get("started_at_utc"))
    except (ValueError, AttributeError) as exc: started = None; errors.append(f"timestamps.started_at_utc: {exc}")
    completed = None
    if isinstance(ts, dict) and ts.get("completed_at_utc") is not None:
        try: completed = utc(ts["completed_at_utc"])
        except ValueError as exc: errors.append(f"timestamps.completed_at_utc: {exc}")
    if started and completed and completed < started: errors.append("completed_at_utc precedes started_at_utc")
    horizon = doc.get("research_horizon", {})
    try:
        begin = utc(horizon["start_utc"]) if horizon.get("start_utc") else None
        end = utc(horizon["end_utc"]) if horizon.get("end_utc") else None
        if begin and end and begin > end: errors.append("research horizon starts after end")
    except (ValueError, KeyError, AttributeError) as exc: errors.append(f"research_horizon: {exc}")
    repo = doc.get("repository", {})
    if isinstance(repo, dict):
        if repo.get("status") not in {"AVAILABLE", "UNAVAILABLE"}: errors.append("repository status must be AVAILABLE or UNAVAILABLE")
        if repo.get("status") == "UNAVAILABLE" and (repo.get("revision") is not None or not repo.get("reason")):
            errors.append("repository unavailable requires null revision and reason")
        if repo.get("status") == "AVAILABLE" and not repo.get("revision"):
            errors.append("repository available requires revision")
    specialists = doc.get("specialists", [])
    if not isinstance(specialists, list): errors.append("specialists must be an array"); specialists = []
    if len(specialists) > 3: errors.append("specialist dispatch exceeds limit of three")
    lifecycle = doc.get("lifecycle", {})
    if version == 2:
        if not isinstance(lifecycle, dict) or lifecycle.get("schema_version") != 2:
            errors.append("lifecycle: v2 lifecycle record required")
        else:
            if lifecycle.get("lifecycle_id") != doc.get("lifecycle_id"): errors.append("lifecycle_id mismatch")
            if lifecycle.get("task_id") != doc.get("run_id"): errors.append("lifecycle task_id must match run_id")
            if lifecycle.get("collection_state") not in {"OPEN", "CLOSED"} or not isinstance(lifecycle.get("reconciliation_completed"), bool):
                errors.append("lifecycle: valid collection_state and reconciliation_completed required")
    elif not isinstance(lifecycle, dict) or lifecycle.get("collection_state") not in {"OPEN", "CLOSED"} or not isinstance(lifecycle.get("reconciliation_completed"), bool):
        errors.append("lifecycle: valid collection_state and reconciliation_completed required")
    elif doc.get("reviews") and (lifecycle["collection_state"] != "CLOSED" or lifecycle["reconciliation_completed"] is not True):
        errors.append("reviews cannot bind before collection closure and completed reconciliation")
    if version == 2 and isinstance(lifecycle, dict):
        validate_v2 = _validator("native_lifecycle.py", "validate_lifecycle_v2")
        try:
            bound_paths = [root / item["path"] for item in doc.get("artifacts", [])
                           if isinstance(item, dict) and isinstance(item.get("path"), str)
                           and item.get("artifact_id")]
            # Run records name the exact artifact IDs at each path; the lifecycle
            # validator independently verifies canonical bytes and expected receipts.
            for item in doc.get("artifacts", []):
                if not isinstance(item, dict) or not item.get("artifact_id"): continue
                artifact_doc = _load_json(root / item["path"])
                if artifact_doc.get("artifact_id") != item["artifact_id"] or artifact_doc.get("lifecycle_id") != doc.get("lifecycle_id"):
                    errors.append(f"artifact lifecycle binding mismatch: {item['path']}")
            history = doc.get("retained_artifact_history", [])
            errors.extend(f"lifecycle: {e}" for e in validate_v2(lifecycle, bound_paths, history))
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            errors.append(f"lifecycle v2 validation failed safely: {exc}")
    validate_handoff = _validator("validate_handoffs.py", "validate_handoff")
    for i, agent in enumerate(specialists):
        if not isinstance(agent, dict): errors.append(f"specialists[{i}]: expected object"); continue
        ref = agent.get("handoff"); reference(ref, f"specialists[{i}]")
        if ref in artifacts:
            try:
                handoff = _load_json(root / ref); errors.extend(f"{ref}: {m}" for m in validate_handoff(handoff))
                if handoff.get("agent_id") != agent.get("agent_id") or handoff.get("task_id") != agent.get("task_id") or handoff.get("status") != agent.get("execution_status"): errors.append(f"specialists[{i}]: handoff identity/status mismatch")
            except (OSError, UnicodeError, json.JSONDecodeError, AttributeError) as exc: errors.append(f"specialists[{i}]: invalid handoff: {exc}")
    validate_ledger = _validator("validate_evidence.py", "validate")
    for ref in doc.get("evidence_ledgers", []) if isinstance(doc.get("evidence_ledgers", []), list) else []:
        reference(ref, "evidence_ledgers")
        if ref in artifacts:
            try: errors.extend(f"{ref}: {m}" for m in validate_ledger(_load_json(root / ref)))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc: errors.append(f"{ref}: invalid evidence ledger: {exc}")
    synthesis = doc.get("synthesis")
    if synthesis:
        reference(synthesis.get("artifact"), "synthesis")
        if synthesis.get("artifact") in artifacts:
            # The frozen binding is over canonical logical JSON, independent of file formatting.
            try:
                snap = _load_json(root / synthesis["artifact"])
                if digest_bytes(canonical_json(snap)) != synthesis.get("sha256"): errors.append("synthesis canonical snapshot digest mismatch")
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc: errors.append(f"synthesis snapshot invalid: {exc}")
    reviews = doc.get("reviews", []) if isinstance(doc.get("reviews", []), list) else []
    roles = [r.get("agent_id") for r in reviews if isinstance(r, dict)]
    if len(roles) != len(set(roles)): errors.append("duplicate reviewer role")
    for role in ("adversarial_reviewer", "evidence_verifier"):
        if roles and role not in roles: errors.append(f"reviewer binding missing required role {role}")
    for i, review in enumerate(reviews):
        if not isinstance(review, dict): errors.append(f"reviews[{i}]: expected object"); continue
        reference(review.get("handoff"), f"reviews[{i}]")
        if not synthesis or review.get("synthesis_revision") != synthesis.get("revision") or review.get("synthesis_sha256") != synthesis.get("sha256"): errors.append(f"reviews[{i}]: synthesis binding mismatch")
        if review.get("handoff") in artifacts:
            try:
                handoff = _load_json(root / review["handoff"]); errors.extend(f"{review['handoff']}: {m}" for m in validate_handoff(handoff))
                binding = handoff.get("review", {})
                if handoff.get("agent_id") != review.get("agent_id") or binding.get("synthesis_revision") != review.get("synthesis_revision") or binding.get("synthesis_sha256") != review.get("synthesis_sha256"): errors.append(f"reviews[{i}]: review handoff binding mismatch")
            except (OSError, UnicodeError, json.JSONDecodeError, AttributeError) as exc: errors.append(f"reviews[{i}]: invalid review handoff: {exc}")

    for i, calc in enumerate(doc.get("calculations", []) if isinstance(doc.get("calculations", []), list) else []):
        p = f"calculations[{i}]"
        if not isinstance(calc, dict): errors.append(f"{p}: expected object"); continue
        if calc.get("status") == "NOT_REPLAYABLE":
            missing_inputs = calc.get("missing_inputs")
            if not isinstance(missing_inputs, list) or not missing_inputs or not all(isinstance(x, str) and x for x in missing_inputs): errors.append(f"{p}: NOT_REPLAYABLE requires exact missing_inputs")
            else: blocked.extend(f"{p}: missing input {x}" for x in missing_inputs)
            continue
        if calc.get("status") is None:
            blocked.append(f"{p}: missing replay status, input timestamps, source artifact bindings, unit-bearing observations, and formula rounding metadata")
            continue
        inputs = calc.get("inputs", [])
        if not isinstance(inputs, list) or not inputs or not all(isinstance(x, str) and x for x in inputs):
            errors.append(f"{p}: inputs must be a nonempty array of observation IDs"); continue
        records = []
        absent = [x for x in inputs if x not in observations]
        if absent:
            if calc.get("status") == "NOT_REPLAYABLE": continue
            errors.append(f"{p}: missing calculation inputs: {', '.join(map(str, absent))}"); continue
        for ident in inputs: records.append(observations[ident][0])
        if calc.get("formula_version") != calc.get("formula"):
            errors.append(f"{p}: formula_version must match formula")
        if calc.get("status") != "REPLAYABLE": errors.append(f"{p}: status must be REPLAYABLE or NOT_REPLAYABLE")
        if calc.get("rounding") != "python_round_half_even.v1": errors.append(f"{p}: unsupported rounding method")
        digits = calc.get("rounding_digits")
        if isinstance(digits, bool) or not isinstance(digits, int) or not 0 <= digits <= 15:
            errors.append(f"{p}: rounding_digits must be an integer from 0 to 15")
        timestamps, sources = calc.get("input_timestamps"), calc.get("source_observations")
        if not isinstance(timestamps, dict) or set(timestamps) != set(inputs): errors.append(f"{p}: input_timestamps must bind every input exactly once")
        if not isinstance(sources, dict) or set(sources) != set(inputs): errors.append(f"{p}: source_observations must bind every input exactly once")
        for artifact in sources.values() if isinstance(sources, dict) else []:
            if artifact not in artifacts: errors.append(f"{p}: source observation references unknown artifact {artifact}")
        if calc.get("missing_data") not in {"error", "NOT_REPLAYABLE"}: errors.append(f"{p}: missing_data must be error or NOT_REPLAYABLE")
        if len(records) != len(inputs): continue
        units = []
        for j, record in enumerate(records):
            if not isinstance(record.get("unit"), str) or not record["unit"].strip(): errors.append(f"{p}: input {inputs[j]} missing unit")
            else: units.append(record["unit"])
            if isinstance(record.get("value"), bool) or not isinstance(record.get("value"), (int, float)) or not math.isfinite(record["value"]): errors.append(f"{p}: input {inputs[j]} value must be finite numeric")
            stamp = record.get("observed_at_utc")
            if not stamp: errors.append(f"{p}: input {inputs[j]} missing observation timestamp")
            else:
                try: utc(stamp)
                except ValueError as exc: errors.append(f"{p}: invalid timestamp for {inputs[j]}: {exc}")
                if isinstance(timestamps, dict) and timestamps.get(inputs[j]) != stamp: errors.append(f"{p}: timestamp mismatch for {inputs[j]}")
            if isinstance(sources, dict) and sources.get(inputs[j]) != observations[inputs[j]][1]: errors.append(f"{p}: source observation artifact mismatch for {inputs[j]}")
        if len(units) == len(records) and len(set(units)) != 1: errors.append(f"{p}: input units are incompatible")
        output_unit = calc.get("unit")
        if calc.get("formula") in {"simple_return.v1", "percent_change.v1", "benchmark_relative_return.v1", "bid_ask_spread.v1"} and output_unit != "percent":
            errors.append(f"{p}: formula output unit must be percent")
        if calc.get("formula") == "sampled_extrema.v1" and units and output_unit != units[0]:
            errors.append(f"{p}: extrema output unit must match sampled input unit")
        if replay:
            try:
                got = formula(calc.get("formula"), [r.get("value") for r in records])
                if isinstance(got, dict):
                    expected = calc.get("expected_value")
                    tol = float(calc.get("tolerance", 0))
                    if not isinstance(expected, dict) or not math.isfinite(tol) or tol < 0: raise ValueError("expected extrema and finite nonnegative tolerance required")
                    if any(k not in expected or isinstance(expected[k], bool) or not isinstance(expected[k], (int, float)) or not math.isfinite(expected[k]) for k in got): raise ValueError("expected extrema must contain finite numeric minimum and maximum")
                    rounded = {k: round(v, digits) for k, v in got.items()}
                    if any(k not in expected or abs(rounded[k] - float(expected[k])) > tol for k in rounded): errors.append(f"{p}: replay mismatch (got {rounded}, expected {expected})")
                else:
                    expected, tol = float(calc.get("expected_value")), float(calc.get("tolerance"))
                    if not math.isfinite(expected) or not math.isfinite(tol) or tol < 0: raise ValueError("expected_value and tolerance must be finite; tolerance nonnegative")
                    rounded = round(got, digits)
                    if abs(rounded - expected) > tol: errors.append(f"{p}: replay mismatch (got {rounded}, expected {expected} ± {tol})")
            except (ValueError, TypeError, OverflowError, ArithmeticError, KeyError) as exc: errors.append(f"{p}: {exc}")
    if errors: return errors
    return ["BLOCKED: " + x for x in blocked]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=pathlib.Path)
    parser.add_argument("--replay", action="store_true", help="replay declared calculations from local fixtures only")
    args = parser.parse_args()
    outcomes = validate(args.manifest, replay=args.replay)
    for result in outcomes:
        if result.startswith("BLOCKED: "): print(result)
        else: print("ERROR:", result)
    status = "FAIL" if any(not x.startswith("BLOCKED: ") for x in outcomes) else "BLOCKED" if outcomes else "PASS"
    print(f"{status}: offline research-run validation; retained observations do not prove provider truth")
    return {"PASS": 0, "FAIL": 1, "BLOCKED": 2}[status]


if __name__ == "__main__": sys.exit(main())
