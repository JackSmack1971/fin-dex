#!/usr/bin/env python3
"""Offline validator and deterministic calculator for retained research runs."""
import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import math
import pathlib
import sys
import importlib.util


def canonical_json(value):
    """UTF-8 JSON, sorted keys, compact separators, no NaN/Infinity."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


def utc(value):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("must be UTC ISO timestamp ending Z")
    result = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    if result.utcoffset() != dt.timedelta(0):
        raise ValueError("must be UTC")
    return result


def formula(name, values):
    if len(values) != 2:
        raise ValueError("formula requires exactly two inputs")
    left, right = map(float, values)
    if not all(math.isfinite(v) for v in (left, right)):
        raise ValueError("inputs must be finite numbers")
    if name == "difference.v1": return right - left
    if name == "ratio.v1":
        if left == 0: raise ValueError("division by zero")
        return right / left
    if name == "percent_change.v1":
        if left == 0: raise ValueError("zero baseline")
        return (right - left) / left * 100.0
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
    errors = []
    try: doc = _load_json(run_path)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc: return [f"manifest: cannot read JSON: {exc}"]
    if not isinstance(doc, dict): return ["manifest: root must be object"]
    required = {"schema_version", "run_id", "task_specification", "timestamps", "asset_identity", "research_horizon", "repository", "methodology_version", "configuration_references", "specialists", "lifecycle", "evidence_ledgers", "synthesis", "reviews", "verdict", "artifacts", "calculations"}
    missing = required - doc.keys()
    if missing: errors.append("manifest: missing fields " + ", ".join(sorted(missing)))
    if doc.get("schema_version") != 1: errors.append("manifest: unsupported schema_version")
    root = run_path.parent
    artifacts = doc.get("artifacts", [])
    index = {}
    for i, item in enumerate(artifacts if isinstance(artifacts, list) else []):
        if not isinstance(item, dict): errors.append(f"artifacts[{i}]: expected object"); continue
        name = item.get("path")
        if not isinstance(name, str) or not name: errors.append(f"artifacts[{i}]: path required"); continue
        if name in index: errors.append(f"artifacts[{i}]: duplicate path {name}")
        index[name] = item
        target = (root / name).resolve()
        if root not in target.parents: errors.append(f"artifacts[{i}]: path escapes run directory"); continue
        try: actual = digest_bytes(target.read_bytes())
        except OSError: errors.append(f"artifact missing: {name}"); continue
        if actual != item.get("sha256"): errors.append(f"artifact digest mismatch: {name}")
    def reference(name, context):
        if name not in index: errors.append(f"{context}: missing artifact reference {name}")
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
    if isinstance(repo, dict) and repo.get("status") == "UNAVAILABLE" and repo.get("revision") is not None:
        errors.append("repository revision must be null when provenance is unavailable")
    specialists = doc.get("specialists", [])
    if not isinstance(specialists, list): errors.append("specialists must be an array"); specialists = []
    if len(specialists) > 3: errors.append("specialist dispatch exceeds limit of three")
    lifecycle = doc.get("lifecycle", {})
    if not isinstance(lifecycle, dict) or lifecycle.get("collection_state") not in {"OPEN", "CLOSED"} or not isinstance(lifecycle.get("reconciliation_completed"), bool):
        errors.append("lifecycle: valid collection_state and reconciliation_completed required")
    elif doc.get("reviews") and (lifecycle["collection_state"] != "CLOSED" or lifecycle["reconciliation_completed"] is not True):
        errors.append("reviews cannot bind before collection closure and completed reconciliation")
    validate_handoff = _validator("validate_handoffs.py", "validate_handoff")
    for i, agent in enumerate(specialists):
        if not isinstance(agent, dict): errors.append(f"specialists[{i}]: expected object"); continue
        ref = agent.get("handoff"); reference(ref, f"specialists[{i}]")
        if ref in index:
            try:
                handoff = _load_json(root / ref)
                errors.extend(f"{ref}: {message}" for message in validate_handoff(handoff))
                if handoff.get("agent_id") != agent.get("agent_id") or handoff.get("task_id") != agent.get("task_id") or handoff.get("status") != agent.get("execution_status"):
                    errors.append(f"specialists[{i}]: handoff identity/status mismatch")
                if not handoff.get("claims") and not handoff.get("observations") and agent.get("execution_status") == "COMPLETE":
                    pass
            except (OSError, UnicodeError, json.JSONDecodeError, AttributeError) as exc: errors.append(f"specialists[{i}]: invalid handoff: {exc}")
    validate_ledger = _validator("validate_evidence.py", "validate")
    for key in ("evidence_ledgers",):
        for ref in doc.get(key, []) if isinstance(doc.get(key, []), list) else []:
            reference(ref, key)
            if ref in index:
                try: errors.extend(f"{ref}: {message}" for message in validate_ledger(_load_json(root / ref)))
                except (OSError, UnicodeError, json.JSONDecodeError) as exc: errors.append(f"{ref}: invalid evidence ledger: {exc}")
    synthesis = doc.get("synthesis")
    if synthesis:
        reference(synthesis.get("artifact"), "synthesis")
        if synthesis.get("artifact") in index and index[synthesis["artifact"]].get("sha256") != synthesis.get("sha256"):
            errors.append("synthesis digest binding mismatch")
    for i, review in enumerate(doc.get("reviews", []) if isinstance(doc.get("reviews", []), list) else []):
        reference(review.get("handoff"), f"reviews[{i}]")
        if not synthesis or review.get("synthesis_revision") != synthesis.get("revision") or review.get("synthesis_sha256") != synthesis.get("sha256"):
            errors.append(f"reviews[{i}]: synthesis binding mismatch")
        if review.get("handoff") in index:
            try:
                handoff = _load_json(root / review["handoff"])
                errors.extend(f"{review['handoff']}: {message}" for message in validate_handoff(handoff))
                binding = handoff.get("review", {})
                if handoff.get("agent_id") != review.get("agent_id") or binding.get("synthesis_revision") != review.get("synthesis_revision") or binding.get("synthesis_sha256") != review.get("synthesis_sha256"):
                    errors.append(f"reviews[{i}]: review handoff binding mismatch")
            except (OSError, UnicodeError, json.JSONDecodeError, AttributeError) as exc:
                errors.append(f"reviews[{i}]: invalid review handoff: {exc}")
    observations = {}
    for artifact, meta in index.items():
        if meta.get("kind") == "observations":
            try:
                data = _load_json(root / artifact)
                records = data.get("observations", data) if isinstance(data, dict) else data
                for record in records:
                    if isinstance(record, dict) and "id" in record: observations[record["id"]] = record.get("value")
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError): errors.append(f"invalid observations fixture: {artifact}")
    for i, calc in enumerate(doc.get("calculations", []) if isinstance(doc.get("calculations", []), list) else []):
        if not isinstance(calc, dict): errors.append(f"calculations[{i}]: expected object"); continue
        inputs = calc.get("inputs", [])
        if len(inputs) != 2 or any(x not in observations for x in inputs): errors.append(f"calculations[{i}]: missing calculation inputs"); continue
        if calc.get("missing_data") != "error": errors.append(f"calculations[{i}]: missing_data must be 'error'")
        if replay:
            try:
                result = formula(calc.get("formula"), [observations[x] for x in inputs])
                expected = float(calc.get("expected_value")); tolerance = float(calc.get("tolerance"))
                if not math.isfinite(expected) or not math.isfinite(tolerance) or tolerance < 0:
                    raise ValueError("expected_value and tolerance must be finite; tolerance nonnegative")
                if abs(result - expected) > tolerance: errors.append(f"calculations[{i}]: replay mismatch (got {result}, expected {expected} ± {tolerance})")
            except (ValueError, TypeError, OverflowError) as exc: errors.append(f"calculations[{i}]: {exc}")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=pathlib.Path)
    parser.add_argument("--replay", action="store_true", help="replay declared calculations from local observation fixtures only")
    args = parser.parse_args()
    errors = validate(args.manifest, replay=args.replay)
    for error in errors: print("ERROR:", error)
    print(f"{'FAIL' if errors else 'PASS'}: offline research-run validation; captured observations are not provider truth")
    return int(bool(errors))


if __name__ == "__main__": sys.exit(main())
