#!/usr/bin/env python3
"""Deterministic file and lifecycle gates for native research orchestration.

This module deliberately does not invoke Codex agents. The supervising native
session persists specialist returns, runs this gate, and may dispatch reviewers
only after the gate emits a verified permit. Codex does not enforce access to
its collaboration tool, so this is a supervisor checkpoint, not a security
boundary against a session that deliberately bypasses the workflow.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Callable


SCRIPT_DIR = Path(__file__).resolve().parent
ARTIFACT_VALIDATOR = SCRIPT_DIR / "validate_handoff_artifact.py"
LIFECYCLE_VALIDATOR = SCRIPT_DIR / "validate_handoffs.py"


def canonical_json_bytes(value):
    """Return canonical UTF-8 JSON bytes as defined by agent-handoffs.md."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def _load_json(path):
    raw = Path(path).read_bytes()
    if not raw:
        raise ValueError(f"empty JSON artifact: {path}")
    try:
        return json.loads(raw.decode("utf-8")), raw
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"malformed JSON artifact {path}: {exc}") from exc


def _run_validator(script, args):
    normalized = [str(x.resolve()) if isinstance(x, Path) else str(x) for x in args]
    command = [sys.executable, str(script.resolve()), *normalized]
    result = subprocess.run(command, cwd=SCRIPT_DIR.parent, capture_output=True,
                            text=True, encoding="utf-8", errors="replace")
    return {"command": command, "exit_code": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr}


def _exclusive_write(path, data):
    """Create a new file without replacing any retained artifact."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        # Keep any partial file as evidence of the failed write. The validation
        # gate will reject it; retained files are never silently replaced.
        raise
    return path


def persist_handoff(document, output_path):
    """Persist one native return and verify its saved bytes and schema."""
    if not isinstance(document, dict):
        raise ValueError("handoff must be a JSON object")
    logical = canonical_json_bytes(document)
    stored = (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    target = _exclusive_write(output_path, stored)
    reread, actual = _load_json(target)
    if reread != document:
        raise ValueError("persisted handoff content differs from returned JSON")
    result = _run_validator(ARTIFACT_VALIDATOR, [target])
    if result["exit_code"] != 0:
        raise ValueError("saved handoff validator failed: " + result["stdout"] + result["stderr"])
    return {
        "path": str(target),
        "agent_id": document.get("agent_id"),
        "task_id": document.get("task_id"),
        "logical_sha256": sha256(logical),
        "stored_bytes_sha256": sha256(actual),
        "byte_count": len(actual),
        "validator": result,
    }


def _build_review_permit(contract_path, snapshot_path, execution_path, handoff_paths):
    """Run all deterministic pre-review checks and return a bound permit."""
    contract, _ = _load_json(contract_path)
    snapshot, snapshot_bytes = _load_json(snapshot_path)
    execution, _ = _load_json(execution_path)
    if not isinstance(contract, dict) or not isinstance(snapshot, dict):
        raise ValueError("synthesis contract and snapshot must be JSON objects")
    phase = contract.get("specialist_phase")
    if not isinstance(phase, dict):
        raise ValueError("specialist phase record is invalid")
    expected = phase.get("expected_agent_ids")
    if (phase.get("state") != "CLOSED" or not isinstance(expected, list) or not expected or
            len(expected) > 3 or any(not isinstance(x, str) or not x.strip() for x in expected) or
            len(set(expected)) != len(expected)):
        raise ValueError("specialist collection is not closed with selected agents")
    reconciliation = contract.get("reconciliation")
    if not isinstance(reconciliation, dict) or reconciliation.get("completed") is not True:
        raise ValueError("reconciliation is not complete")
    if contract.get("reviews") not in ([], None):
        raise ValueError("pre-review synthesis must not already contain review entries")
    revision = contract.get("revision")
    if not isinstance(revision, dict):
        raise ValueError("synthesis revision record is invalid")
    revision_id = revision.get("id")
    if not isinstance(revision_id, str) or not revision_id:
        raise ValueError("synthesis revision ID is missing")
    computed_digest = sha256(canonical_json_bytes(snapshot))
    if revision.get("snapshot") != snapshot:
        raise ValueError("contract snapshot differs from persisted snapshot artifact")
    if not isinstance(revision.get("sha256"), str):
        raise ValueError("synthesis digest is missing")
    if revision["sha256"] != computed_digest:
        raise ValueError("synthesis digest mismatch")
    if contract.get("synthesis_owner") is None or not str(contract["synthesis_owner"]).strip():
        raise ValueError("synthesis owner is missing")

    runs = execution.get("specialists") if isinstance(execution, dict) else None
    if not isinstance(runs, list):
        raise ValueError("execution receipt has no specialist records")
    run_by_id = {}
    for run in runs:
        if not isinstance(run, dict) or not isinstance(run.get("agent_id"), str):
            raise ValueError("invalid specialist execution receipt")
        if run["agent_id"] in run_by_id:
            raise ValueError(f"duplicate specialist execution receipt: {run['agent_id']}")
        run_by_id[run["agent_id"]] = run
    if set(expected) != set(run_by_id):
        raise ValueError("terminal execution receipts do not exactly match selected specialists")
    terminal = {"COMPLETE", "PARTIAL", "FAILED", "BLOCKED"}
    for agent_id in expected:
        run = run_by_id[agent_id]
        if run.get("terminal") is not True or run.get("execution_status") not in terminal:
            raise ValueError(f"specialist has not reached a terminal state: {agent_id}")
        if not isinstance(run.get("native_thread_id"), str) or not run["native_thread_id"]:
            raise ValueError(f"specialist native thread ID is missing: {agent_id}")

    paths = [Path(p) for p in handoff_paths]
    loaded, artifact_receipts, seen = [], [], set()
    if len(paths) != len(expected):
        raise ValueError("handoff path count does not match selected specialists")
    for path in paths:
        doc, raw = _load_json(path)
        if not isinstance(doc, dict):
            raise ValueError(f"handoff root must be a JSON object: {path}")
        if doc.get("agent_id") not in expected:
            raise ValueError(f"unexpected or reviewer handoff in specialist set: {path}")
        if doc["agent_id"] in seen:
            raise ValueError(f"duplicate specialist handoff: {doc['agent_id']}")
        seen.add(doc["agent_id"])
        if doc.get("status") != run_by_id[doc["agent_id"]].get("execution_status"):
            raise ValueError(f"execution/handoff status mismatch: {doc['agent_id']}")
        artifact_result = _run_validator(ARTIFACT_VALIDATOR, [path])
        if artifact_result["exit_code"] != 0:
            raise ValueError(f"saved handoff validation failed for {path}: {artifact_result['stdout']}")
        loaded.append(doc)
        artifact_receipts.append({
            "path": str(path.resolve()), "agent_id": doc["agent_id"],
            "logical_sha256": sha256(canonical_json_bytes(doc)),
            "stored_bytes_sha256": sha256(raw), "byte_count": len(raw),
            "validator": artifact_result,
        })
    if seen != set(expected):
        raise ValueError("persisted handoffs do not exactly match selected specialists")

    lifecycle_result = _run_validator(LIFECYCLE_VALIDATOR,
                                      [*paths, "--synthesis", contract_path])
    if lifecycle_result["exit_code"] != 0:
        raise ValueError("pre-review lifecycle validator failed: " + lifecycle_result["stdout"])
    # Recompute after validator execution so the permit is bound to the bytes
    # that are actually present at the dispatch checkpoint.
    reread_snapshot, current_snapshot_bytes = _load_json(snapshot_path)
    verified_digest = sha256(canonical_json_bytes(reread_snapshot))
    if reread_snapshot != snapshot or verified_digest != computed_digest:
        raise ValueError("snapshot changed during pre-review verification")
    if sha256(snapshot_bytes) != sha256(current_snapshot_bytes):
        raise ValueError("snapshot stored bytes changed during pre-review verification")
    claims = snapshot.get("claims")
    if not isinstance(claims, list):
        raise ValueError("frozen snapshot claims must be a list")
    claim_ids = [c["id"] for c in claims
                 if isinstance(c, dict) and isinstance(c.get("id"), str) and c["id"]]
    if not claim_ids:
        raise ValueError("frozen snapshot has no reviewable claim IDs")
    permit = {
        "schema_version": 1,
        "state": "REVIEW_DISPATCH_ALLOWED",
        "synthesis_revision": revision_id,
        "synthesis_sha256": verified_digest,
        "snapshot_path": str(Path(snapshot_path).resolve()),
        "snapshot_stored_bytes_sha256": sha256(current_snapshot_bytes),
        "claim_ids": claim_ids,
        "specialists": [{"agent_id": x, "native_thread_id": run_by_id[x]["native_thread_id"],
                          "execution_status": run_by_id[x]["execution_status"]} for x in expected],
        "handoff_artifacts": artifact_receipts,
        "lifecycle_validator": lifecycle_result,
        "reconciliation_completed": True,
    }
    return permit


def prepare_review_dispatch(contract_path, snapshot_path, execution_path,
                            handoff_paths, permit_path):
    """Run checks and persist a permit without replacing an existing record."""
    permit = _build_review_permit(contract_path, snapshot_path, execution_path, handoff_paths)
    permit_bytes = (json.dumps(permit, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    _exclusive_write(permit_path, permit_bytes)
    return permit


def authorize_review_dispatch(contract_path, snapshot_path, execution_path,
                              handoff_paths, permit_path):
    """Recompute the gate and reject fabricated or stale permit records."""
    try:
        permit, _ = _load_json(permit_path)
    except (OSError, ValueError) as exc:
        raise ValueError(f"reviewer dispatch permit is unavailable: {exc}") from exc
    if not isinstance(permit, dict):
        raise ValueError("reviewer dispatch permit root must be a JSON object")
    expected = _build_review_permit(contract_path, snapshot_path, execution_path, handoff_paths)
    if permit != expected:
        raise ValueError("reviewer dispatch permit is fabricated or stale; rerun pre-review gate")
    return permit


def dispatch_reviewers_after_gate(contract_path, snapshot_path, execution_path,
                                  handoff_paths, permit_path, reviewer_dispatch: Callable):
    """Supervisor checkpoint; callback follows a fresh full gate verification."""
    permit = authorize_review_dispatch(contract_path, snapshot_path, execution_path,
                                       handoff_paths, permit_path)
    return reviewer_dispatch(permit)


def validate_review_handoffs(permit_path, contract_path, reviewer_paths):
    """Validate both saved reviewer returns against the permit and snapshot."""
    permit, _ = _load_json(permit_path)
    contract, _ = _load_json(contract_path)
    if not isinstance(permit, dict) or not isinstance(contract, dict):
        raise ValueError("review permit and synthesis contract must be JSON objects")
    if permit.get("state") != "REVIEW_DISPATCH_ALLOWED":
        raise ValueError("reviewer dispatch permit is absent or invalid")
    revision = contract.get("revision")
    if not isinstance(contract, dict) or not isinstance(revision, dict):
        raise ValueError("review synthesis contract is invalid")
    if revision.get("id") != permit.get("synthesis_revision"):
        raise ValueError("review synthesis revision differs from dispatch permit")
    if revision.get("sha256") != permit.get("synthesis_sha256"):
        raise ValueError("review synthesis digest differs from dispatch permit")
    handoff_artifacts = permit.get("handoff_artifacts")
    if (not isinstance(handoff_artifacts, list) or not handoff_artifacts or
            any(not isinstance(x, dict) or not isinstance(x.get("path"), str) or not x["path"]
                for x in handoff_artifacts)):
        raise ValueError("review permit specialist handoff bindings are malformed")
    expected_roles = {"adversarial_reviewer", "evidence_verifier"}
    docs, receipts, seen = [], [], set()
    specialist_paths = [Path(x["path"]) for x in handoff_artifacts]
    for path in reviewer_paths:
        doc, raw = _load_json(path)
        if not isinstance(doc, dict):
            raise ValueError(f"reviewer handoff root must be a JSON object: {path}")
        role = doc.get("agent_id")
        if role not in expected_roles or role in seen:
            raise ValueError(f"unexpected or duplicate reviewer handoff: {role}")
        seen.add(role)
        binding = doc.get("review", {})
        if not isinstance(binding, dict):
            raise ValueError(f"review binding must be a JSON object: {role}")
        if binding.get("synthesis_revision") != permit.get("synthesis_revision"):
            raise ValueError(f"reviewer revision mismatch: {role}")
        if binding.get("synthesis_sha256") != permit.get("synthesis_sha256"):
            raise ValueError(f"reviewer digest mismatch: {role}")
        if binding.get("claim_ids") != permit.get("claim_ids"):
            raise ValueError(f"reviewer claim IDs differ from permit: {role}")
        artifact_result = _run_validator(ARTIFACT_VALIDATOR, [path])
        if artifact_result["exit_code"] != 0:
            raise ValueError(f"saved reviewer handoff validation failed for {role}: {artifact_result['stdout']}")
        docs.append(doc)
        receipts.append({"path": str(Path(path).resolve()), "agent_id": role,
                         "logical_sha256": sha256(canonical_json_bytes(doc)),
                         "stored_bytes_sha256": sha256(raw), "byte_count": len(raw),
                         "validator": artifact_result})
    if seen != expected_roles:
        raise ValueError("both independent reviewer handoffs are required")
    declared_reviews = contract.get("reviews")
    if not isinstance(declared_reviews, list) or {x.get("agent_id") for x in declared_reviews if isinstance(x, dict)} != expected_roles:
        raise ValueError("final synthesis contract must bind exactly both reviewers")
    for review in declared_reviews:
        if not isinstance(review, dict):
            raise ValueError("invalid final reviewer binding entry")
        if (review.get("synthesis_revision") != permit["synthesis_revision"] or
                review.get("synthesis_sha256") != permit["synthesis_sha256"]):
            raise ValueError(f"final synthesis reviewer entry is unbound: {review.get('agent_id')}")
        if not isinstance(review.get("handoff"), str) or not review["handoff"]:
            raise ValueError(f"final synthesis reviewer handoff path is invalid: {review.get('agent_id')}")
        declared_path = Path(review["handoff"])
        if not declared_path.is_absolute():
            declared_path = Path(contract_path).resolve().parent / declared_path
        if not any(declared_path.resolve() == Path(p).resolve() for p in reviewer_paths):
            raise ValueError(f"final synthesis reviewer handoff path mismatch: {review.get('agent_id')}")
    validator_result = _run_validator(LIFECYCLE_VALIDATOR,
                                      [*specialist_paths, *[Path(x) for x in reviewer_paths],
                                       "--synthesis", Path(contract_path)])
    if validator_result["exit_code"] != 0:
        raise ValueError("review lifecycle validation failed: " + validator_result["stdout"])
    return {"state": "REVIEW_BINDINGS_VALIDATED", "synthesis_revision": permit["synthesis_revision"],
            "synthesis_sha256": permit["synthesis_sha256"], "reviewer_handoffs": receipts,
            "lifecycle_validator": validator_result}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    persist = sub.add_parser("persist-handoff")
    persist.add_argument("input", help="native handoff JSON file, or '-' to read JSON from stdin")
    persist.add_argument("--output", required=True, type=Path)
    gate = sub.add_parser("prepare-review")
    gate.add_argument("--contract", required=True, type=Path)
    gate.add_argument("--snapshot", required=True, type=Path)
    gate.add_argument("--execution", required=True, type=Path)
    gate.add_argument("--handoff", action="append", required=True, type=Path)
    gate.add_argument("--permit", required=True, type=Path)
    review = sub.add_parser("validate-review")
    review.add_argument("--contract", required=True, type=Path)
    review.add_argument("--permit", required=True, type=Path)
    review.add_argument("--handoff", action="append", required=True, type=Path)
    args = ap.parse_args()
    try:
        if args.command == "persist-handoff":
            if args.input == "-":
                try:
                    doc = json.load(sys.stdin)
                except (UnicodeError, json.JSONDecodeError) as exc:
                    raise ValueError(f"malformed handoff JSON on stdin: {exc}") from exc
            else:
                doc, _ = _load_json(Path(args.input))
            result = persist_handoff(doc, args.output)
        elif args.command == "prepare-review":
            result = prepare_review_dispatch(args.contract, args.snapshot, args.execution,
                                             args.handoff, args.permit)
        else:
            result = validate_review_handoffs(args.permit, args.contract, args.handoff)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"state": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
