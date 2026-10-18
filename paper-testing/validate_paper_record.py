#!/usr/bin/env python3
"""Validate paper signal/run JSON records without connecting to a venue."""
import argparse
import datetime as dt
import json
import math
import pathlib
import sys


def fail(message):
    raise ValueError(message)


def validate_signal(value):
    if not isinstance(value, dict) or value.get("schema_version") != "signal.v1":
        fail("expected signal.v1 object")
    required = {"schema_version", "signal_id", "decision_time_utc", "asset", "direction", "horizon_days", "rationale", "evidence_refs", "gate_result", "order"}
    if set(value) != required: fail("signal has missing or unknown fields")
    if not isinstance(value["signal_id"], str) or not value["signal_id"]: fail("signal_id required")
    try:
        stamp = value["decision_time_utc"]
        parsed = dt.datetime.fromisoformat(stamp[:-1] + "+00:00")
        if not stamp.endswith("Z") or parsed.utcoffset() != dt.timedelta(0): fail("decision_time_utc must be UTC ending Z")
    except (TypeError, ValueError): fail("invalid decision_time_utc")
    asset = value["asset"]
    if not isinstance(asset, dict) or set(asset) != {"venue", "instrument_id", "base", "quote", "instrument_type"} or not all(isinstance(asset[k], str) and asset[k] for k in asset): fail("asset identity is incomplete or ambiguous")
    if asset["instrument_type"] not in {"spot", "perpetual"}: fail("invalid instrument_type")
    if value["direction"] not in {"long", "short", "flat"}: fail("invalid direction")
    if type(value["horizon_days"]) is not int or not 1 <= value["horizon_days"] <= 365: fail("invalid horizon_days")
    if not isinstance(value["rationale"], str) or not value["rationale"]: fail("rationale required")
    if not isinstance(value["evidence_refs"], list) or not all(isinstance(x, str) and x for x in value["evidence_refs"]): fail("evidence_refs must be strings")
    if value["gate_result"] not in {"QUALIFIED", "REJECTED"}: fail("invalid gate_result")
    order = value["order"]
    if not isinstance(order, dict) or set(order) - {"side", "order_type", "quantity", "limit_price", "time_in_force"} or not {"side", "order_type", "quantity", "time_in_force"} <= order.keys(): fail("invalid order shape")
    if order["side"] not in {"buy", "sell"} or order["order_type"] not in {"market", "limit"} or order["time_in_force"] not in {"IOC", "GTC", "DAY"}: fail("invalid order enum")
    if isinstance(order["quantity"], bool) or not isinstance(order["quantity"], (int, float)) or not math.isfinite(order["quantity"]) or order["quantity"] <= 0: fail("quantity must be finite and positive")
    if order["order_type"] == "limit" and (isinstance(order.get("limit_price"), bool) or not isinstance(order.get("limit_price"), (int, float)) or not math.isfinite(order["limit_price"]) or order["limit_price"] <= 0): fail("limit order requires positive limit_price")
    if order["order_type"] == "market" and order.get("limit_price") is not None: fail("market order cannot include limit_price")
    if value["direction"] == "flat" and value["gate_result"] == "QUALIFIED": fail("flat direction cannot be qualified for entry")
    if value["direction"] == "long" and order["side"] != "buy": fail("long direction requires buy side")
    if value["direction"] == "short" and order["side"] != "sell": fail("short direction requires sell side")
    return True


def validate_run(value):
    if not isinstance(value, dict) or value.get("schema_version") != "paper-run.v1": fail("expected paper-run.v1 object")
    required = {"schema_version", "run_id", "status", "signal", "venue_evidence", "submission", "reconciliation", "evaluation"}
    if set(value) != required: fail("run has missing or unknown fields")
    validate_signal(value["signal"])
    if not isinstance(value["run_id"], str) or not value["run_id"]: fail("run_id required")
    if value["status"] not in {"PREPARED", "MODE_VERIFIED", "SUBMISSION_UNKNOWN", "ACKNOWLEDGED", "RECONCILED", "REJECTED"}: fail("invalid run status")
    rec = value["reconciliation"]
    rec_order = ("order_id", "fills", "fees", "funding", "account_equity", "equity_history")
    rec_keys = set(rec_order)
    if not isinstance(rec, dict) or set(rec) != rec_keys: fail("reconciliation fields missing or unknown")
    if rec["order_id"] is not None and (not isinstance(rec["order_id"], str) or not rec["order_id"]): fail("order_id must be a nonempty string or null")
    if rec["fills"] is not None and not isinstance(rec["fills"], list): fail("fills must be an array or null")
    if rec["equity_history"] is not None and not isinstance(rec["equity_history"], list): fail("equity_history must be an array or null")
    for key in ("fees", "funding", "account_equity"):
        if rec[key] is not None and (isinstance(rec[key], bool) or not isinstance(rec[key], (int, float)) or not math.isfinite(rec[key])): fail(f"{key} must be finite or null")
    ev = value["evaluation"]
    if not isinstance(ev, dict) or set(ev) != {"complete", "missing_fields", "metrics"}: fail("invalid evaluation shape")
    missing = [key for key in rec_order if rec[key] is None]
    if missing and (ev["complete"] is not False or ev["metrics"] is not None): fail("missing reconciliation inputs cannot yield complete metrics")
    if ev["complete"] is True and (missing or not isinstance(ev["metrics"], dict)): fail("complete evaluation requires all reconciliation inputs and metrics")
    if not isinstance(ev["complete"], bool): fail("evaluation.complete must be boolean")
    if not isinstance(ev["missing_fields"], list) or ev["missing_fields"] != missing: fail("missing_fields does not match reconciliation")
    evidence = value["venue_evidence"]
    status = value["status"]
    submission = value["submission"]
    if status == "PREPARED" and (evidence is not None or submission is not None): fail("PREPARED cannot contain venue evidence or submission receipt")
    if status in {"MODE_VERIFIED", "SUBMISSION_UNKNOWN", "ACKNOWLEDGED", "RECONCILED"}:
        evidence_keys = {"mode", "account_id", "observed_at_utc", "retrieved_at_utc", "tool_schema_digest"}
        if not isinstance(evidence, dict) or not evidence_keys <= evidence.keys() or evidence.get("mode") != "SIMULATED": fail("status requires complete retained simulated mode evidence")
        if not isinstance(evidence["account_id"], str) or not evidence["account_id"]: fail("venue evidence requires account identity")
        for key in ("observed_at_utc", "retrieved_at_utc"):
            stamp = evidence[key]
            if not isinstance(stamp, str) or not stamp.endswith("Z"): fail(f"venue evidence {key} must be UTC ending Z")
            try: dt.datetime.fromisoformat(stamp[:-1] + "+00:00")
            except ValueError: fail(f"venue evidence {key} is invalid")
        if not isinstance(evidence["tool_schema_digest"], str) or len(evidence["tool_schema_digest"]) != 64 or any(c not in "0123456789abcdef" for c in evidence["tool_schema_digest"]): fail("invalid tool schema digest")
    if status == "MODE_VERIFIED" and submission is not None: fail("MODE_VERIFIED cannot have a submission receipt")
    if status == "REJECTED" and (value["signal"]["gate_result"] != "REJECTED" or evidence is not None or submission is not None): fail("REJECTED is reserved for a gate-rejected signal before venue interaction")
    if status in {"SUBMISSION_UNKNOWN", "ACKNOWLEDGED", "RECONCILED"} and value["signal"]["gate_result"] != "QUALIFIED": fail("submission lifecycle requires a qualified signal")
    if status == "SUBMISSION_UNKNOWN" and (not isinstance(submission, dict) or submission.get("outcome") != "UNKNOWN" or not submission.get("client_order_id") or not submission.get("request_digest") or any(key in submission for key in ("order_id", "acknowledged", "acknowledged_at_utc"))): fail("SUBMISSION_UNKNOWN requires an unacknowledged durable intent receipt")
    if status in {"ACKNOWLEDGED", "RECONCILED"}:
        if not isinstance(submission, dict) or submission.get("outcome") != "ACKNOWLEDGED" or submission.get("acknowledged") is not True or not submission.get("order_id") or not submission.get("client_order_id") or not submission.get("request_digest") or not submission.get("acknowledged_at_utc"): fail("acknowledged status requires a stable persisted receipt")
    if isinstance(submission, dict):
        if not isinstance(submission.get("client_order_id"), str) or not submission["client_order_id"].startswith("fdx-"): fail("invalid client order ID")
        digest = submission.get("request_digest")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest): fail("invalid request digest")
    if status == "RECONCILED" and rec["order_id"] != submission["order_id"]: fail("reconciled order ID must match the acknowledgement")
    if status in {"PREPARED", "MODE_VERIFIED", "SUBMISSION_UNKNOWN", "ACKNOWLEDGED"} and rec["order_id"] is not None: fail("order ID is not allowed before successful reconciliation")
    if status in {"PREPARED", "MODE_VERIFIED", "SUBMISSION_UNKNOWN", "ACKNOWLEDGED", "REJECTED"} and any(rec[key] is not None for key in ("fills", "fees", "funding", "account_equity", "equity_history")): fail("reconciliation data is not allowed before RECONCILED")
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("--kind", choices=("signal", "run"), required=True)
    args = parser.parse_args()
    try:
        value = json.loads(pathlib.Path(args.path).read_text(encoding="utf-8"), parse_constant=lambda x: fail(f"invalid number {x}"))
        (validate_signal if args.kind == "signal" else validate_run)(value)
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"PASS: {args.kind} record valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
