#!/usr/bin/env python3
"""Offline structural and lifecycle validation for Fin-Dex agent handoffs."""
import argparse
import datetime as dt
import hashlib
import json
import pathlib
import re
import sys

AGENTS = {"market_analyst", "liquidity_analyst", "onchain_analyst", "catalyst_analyst", "adversarial_reviewer", "evidence_verifier"}
SPECIALISTS = AGENTS - {"adversarial_reviewer", "evidence_verifier"}
CLAIM_STATUSES = {"CORROBORATED", "SINGLE_SOURCE", "CONFLICTED", "UNVERIFIED", "UNAVAILABLE"}
OUTCOMES = {"SUCCESS", "UNAVAILABLE", "ERROR", "NOT_CALLED"}
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def timestamp(value):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("must be UTC ISO timestamp ending Z")
    parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    if parsed.utcoffset() != dt.timedelta(0):
        raise ValueError("must be UTC")
    return parsed


def validate_handoff(doc):
    errors = []
    if not isinstance(doc, dict):
        return ["root must be object"]
    required = {"schema_version", "agent_id", "task_id", "status", "asset_scope", "claims", "observations", "conflicts", "coverage_gaps", "tool_calls"}
    v2_fields = {"lifecycle_id", "artifact_id"} if doc.get("schema_version") == 2 else set()
    if set(doc) - required - {"review"} - v2_fields: errors.append("unknown top-level fields")
    for key in required - set(doc): errors.append(f"missing {key}")
    version = doc.get("schema_version")
    if version not in {1, 2}: errors.append("schema_version must be 1 or 2")
    if version == 2:
        for key in ("lifecycle_id", "artifact_id"):
            if not isinstance(doc.get(key), str) or not ID.fullmatch(doc.get(key, "")):
                errors.append(f"invalid {key}")
    if doc.get("agent_id") not in AGENTS: errors.append("invalid agent_id")
    if doc.get("status") not in {"COMPLETE", "PARTIAL", "FAILED", "BLOCKED"}: errors.append("invalid handoff status")
    if not isinstance(doc.get("task_id"), str) or not ID.fullmatch(doc.get("task_id", "")): errors.append("invalid task_id")
    for key in ("asset_scope", "claims", "observations", "conflicts", "coverage_gaps", "tool_calls"):
        if not isinstance(doc.get(key), list): errors.append(f"{key} must be array")
    if errors: return errors
    ids = {}
    def record_id(item, path):
        ident = item.get("id") if isinstance(item, dict) else None
        if not isinstance(ident, str) or not ID.fullmatch(ident): errors.append(f"{path}: invalid id")
        elif ident in ids: errors.append(f"{path}: duplicate id {ident}")
        else: ids[ident] = path
    obs_ids = set()
    for i, obs in enumerate(doc["observations"]):
        p = f"observations[{i}]"
        if not isinstance(obs, dict): errors.append(f"{p}: expected object"); continue
        record_id(obs, p)
        obs_ids.add(obs.get("id"))
        for k in ("source", "tool", "asset_id", "metric", "unit"):
            if not isinstance(obs.get(k), str) or not obs[k].strip(): errors.append(f"{p}.{k}: nonempty string required")
        parsed = {}
        if "observed_at_utc" not in obs:
            errors.append(f"{p}.observed_at_utc: required; use null when the provider supplies no observation time")
        elif obs["observed_at_utc"] is not None:
            try: parsed["observed_at_utc"] = timestamp(obs["observed_at_utc"])
            except (KeyError, TypeError, ValueError) as e: errors.append(f"{p}.observed_at_utc: {e}")
        try: parsed["retrieved_at_utc"] = timestamp(obs["retrieved_at_utc"])
        except (KeyError, TypeError, ValueError) as e: errors.append(f"{p}.retrieved_at_utc: {e}")
        if len(parsed) == 2 and parsed["observed_at_utc"] > parsed["retrieved_at_utc"]:
            errors.append(f"{p}: observed time after retrieval time")
    for collection in ("claims", "conflicts", "coverage_gaps"):
        for i, item in enumerate(doc[collection]):
            p = f"{collection}[{i}]"
            if not isinstance(item, dict): errors.append(f"{p}: expected object"); continue
            record_id(item, p)
            if collection == "claims":
                if not isinstance(item.get("statement"), str) or not item["statement"].strip(): errors.append(f"{p}.statement required")
                if item.get("status") not in CLAIM_STATUSES: errors.append(f"{p}.status invalid")
                refs = item.get("observation_ids")
                if not isinstance(refs, list) or len(refs) != len(set(refs)) if isinstance(refs, list) else True: errors.append(f"{p}.observation_ids must be unique array")
                elif any(x not in obs_ids for x in refs): errors.append(f"{p}: missing observation reference")
                if item.get("status") in {"CORROBORATED", "SINGLE_SOURCE"} and not refs: errors.append(f"{p}: supported claim requires observations")
            if collection == "conflicts":
                refs = item.get("claim_ids", [])
                if not isinstance(refs, list) or not refs: errors.append(f"{p}.claim_ids required")
            if collection == "coverage_gaps" and not isinstance(item.get("description"), str): errors.append(f"{p}.description required")
    claim_ids = {c.get("id") for c in doc["claims"] if isinstance(c, dict)}
    for i, conflict in enumerate(doc["conflicts"]):
        if isinstance(conflict, dict) and any(x not in claim_ids for x in conflict.get("claim_ids", []) if isinstance(x, str)):
            errors.append(f"conflicts[{i}]: missing claim reference")
    seen_calls = set()
    for i, call in enumerate(doc["tool_calls"]):
        p = f"tool_calls[{i}]"
        if not isinstance(call, dict): errors.append(f"{p}: expected object"); continue
        if not all(isinstance(call.get(k), str) and call[k].strip() for k in ("tool", "source")): errors.append(f"{p}: source and tool required")
        if call.get("outcome") not in OUTCOMES: errors.append(f"{p}.outcome invalid")
        if "retrieved_at_utc" in call:
            try: timestamp(call["retrieved_at_utc"])
            except (TypeError, ValueError) as e: errors.append(f"{p}.retrieved_at_utc: {e}")
        key = (call.get("source"), call.get("tool"))
        if key in seen_calls: errors.append(f"{p}: duplicate tool-call summary")
        seen_calls.add(key)
    for i, scope in enumerate(doc["asset_scope"]):
        if not isinstance(scope, dict) or not isinstance(scope.get("asset_id"), str) or not scope["asset_id"].strip(): errors.append(f"asset_scope[{i}]: asset_id required")
    role = doc.get("agent_id")
    if role in SPECIALISTS and "review" in doc: errors.append("source specialist cannot include review binding")
    if role in {"adversarial_reviewer", "evidence_verifier"}:
        review = doc.get("review")
        if not isinstance(review, dict): errors.append("review binding required")
        else:
            if not isinstance(review.get("synthesis_revision"), str) or not ID.fullmatch(review.get("synthesis_revision", "")): errors.append("invalid review synthesis_revision")
            if not isinstance(review.get("synthesis_sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", review.get("synthesis_sha256", "")): errors.append("invalid review synthesis_sha256")
            refs = review.get("claim_ids")
            if not isinstance(refs, list) or not refs or len(refs) != len(set(refs)) if isinstance(refs, list) else True: errors.append("review claim_ids must be unique nonempty array")
            elif any(x not in claim_ids for x in refs): errors.append("review references missing claim id")
    return errors


def snapshot_digest(snapshot):
    raw = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def validate_synthesis(doc, handoffs):
    errors = []
    if not isinstance(doc, dict): return ["synthesis contract must be object"]
    phase = doc.get("specialist_phase", {})
    if phase.get("state") not in {"OPEN", "CLOSED"}: errors.append("invalid specialist phase state")
    expected = phase.get("expected_agent_ids")
    if not isinstance(expected, list) or not expected or len(expected) != len(set(expected)) or any(x not in SPECIALISTS for x in expected): errors.append("expected_agent_ids must be unique specialists")
    elif len(expected) > 3: errors.append("specialist dispatch exceeds concurrency bound of three")
    received = {h.get("agent_id") for h in handoffs if isinstance(h, dict)}
    if phase.get("state") == "CLOSED" and set(expected or []) - received: errors.append("cannot close collection with incomplete specialist handoffs")
    reconciliation = doc.get("reconciliation", {})
    review_ready = phase.get("state") == "CLOSED" and reconciliation.get("completed") is True
    revision = doc.get("revision", {})
    snapshot = revision.get("snapshot")
    try:
        digest = snapshot_digest(snapshot) if snapshot is not None else None
    except (TypeError, ValueError) as exc:
        digest = None
        errors.append(f"snapshot is not canonical-JSON serializable: {exc}")
    if snapshot is not None and revision.get("sha256") != digest: errors.append("snapshot digest mismatch")
    if doc.get("reviews") and not review_ready: errors.append("review cannot start before collection closes and reconciliation completes")
    if doc.get("reviews") and not isinstance(revision.get("id"), str): errors.append("reviews require frozen synthesis revision")
    if doc.get("reviews"):
        if not isinstance(doc.get("synthesis_owner"), str) or not doc["synthesis_owner"].strip(): errors.append("synthesis_owner required")
        snapshot_claims = {c.get("id") for c in snapshot.get("claims", []) if isinstance(c, dict)} if isinstance(snapshot, dict) and isinstance(snapshot.get("claims", []), list) else set()
        if not snapshot_claims: errors.append("frozen synthesis snapshot must contain claim IDs before review")
        for handoff in handoffs:
            if not isinstance(handoff, dict) or handoff.get("agent_id") not in {"adversarial_reviewer", "evidence_verifier"}: continue
            binding = handoff.get("review", {})
            if binding.get("synthesis_revision") != revision.get("id"): errors.append(f"{handoff.get('agent_id')}: synthesis revision mismatch")
            if binding.get("synthesis_sha256") != digest or digest is None: errors.append(f"{handoff.get('agent_id')}: synthesis snapshot digest mismatch")
            if snapshot_claims and any(x not in snapshot_claims for x in binding.get("claim_ids", [])): errors.append(f"{handoff.get('agent_id')}: review references claim absent from snapshot")
    return errors


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("handoffs", nargs="+", type=pathlib.Path)
    ap.add_argument("--synthesis", type=pathlib.Path)
    args = ap.parse_args()
    docs, errors = [], []
    for path in args.handoffs:
        try: doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as e: errors.append(f"{path}: cannot read JSON: {e}"); continue
        errs = validate_handoff(doc); errors.extend(f"{path}: {e}" for e in errs); docs.append(doc)
    if args.synthesis:
        try: contract = json.loads(args.synthesis.read_text(encoding="utf-8")); errors.extend(validate_synthesis(contract, docs))
        except (OSError, UnicodeError, json.JSONDecodeError) as e: errors.append(f"synthesis: cannot read JSON: {e}")
    for error in errors: print("ERROR:", error)
    print(f"{'FAIL' if errors else 'PASS'}: {len(errors)} handoff/lifecycle error(s); factual truth is not verified")
    return int(bool(errors))


if __name__ == "__main__":
    sys.exit(main())
