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
    return True


def validate_run(value):
    if not isinstance(value, dict) or value.get("schema_version") != "paper-run.v1": fail("expected paper-run.v1 object")
    required = {"schema_version", "run_id", "status", "signal", "venue_evidence", "submission", "reconciliation", "evaluation"}
    if set(value) != required: fail("run has missing or unknown fields")
    validate_signal(value["signal"])
    if not isinstance(value["run_id"], str) or not value["run_id"]: fail("run_id required")
    if value["status"] not in {"PREPARED", "MODE_VERIFIED", "SUBMISSION_UNKNOWN", "ACKNOWLEDGED", "RECONCILED", "REJECTED"}: fail("invalid run status")
    rec = value["reconciliation"]
    rec_keys = {"order_id", "fills", "fees", "funding", "account_equity", "equity_history"}
    if not isinstance(rec, dict) or set(rec) != rec_keys: fail("reconciliation fields missing or unknown")
    ev = value["evaluation"]
    if not isinstance(ev, dict) or set(ev) != {"complete", "missing_fields", "metrics"}: fail("invalid evaluation shape")
    missing = [key for key in rec_keys if rec[key] is None]
    if missing and (ev["complete"] is not False or ev["metrics"] is not None): fail("missing reconciliation inputs cannot yield complete metrics")
    if ev["complete"] is True and (missing or not isinstance(ev["metrics"], dict)): fail("complete evaluation requires all reconciliation inputs and metrics")
    if not isinstance(ev["missing_fields"], list) or set(ev["missing_fields"]) != set(missing): fail("missing_fields does not match reconciliation")
    evidence = value["venue_evidence"]
    if value["status"] in {"MODE_VERIFIED", "ACKNOWLEDGED", "RECONCILED"} and (not isinstance(evidence, dict) or evidence.get("mode") != "SIMULATED"): fail("status requires retained simulated mode evidence")
    if value["status"] in {"ACKNOWLEDGED", "RECONCILED"} and not isinstance(value["submission"], dict): fail("acknowledged status requires submission receipt")
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
