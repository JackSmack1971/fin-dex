"""Fail-closed contract for a future simulated venue connector; no network code."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any, Protocol


ALLOWED_OPERATIONS = frozenset({"get_paper_status", "submit_simulated_order", "reconcile_order"})


class AdapterRefusal(RuntimeError):
    pass


class PaperVenue(Protocol):
    def get_paper_status(self) -> dict[str, Any]: ...
    def submit_simulated_order(self, request: dict[str, Any]) -> dict[str, Any]: ...
    def reconcile_order(self, client_order_id: str) -> dict[str, Any]: ...


@dataclass
class PaperAdapter:
    """Policy wrapper. `venue` must come from a separately qualified runtime."""
    venue: PaperVenue
    tool_schemas: dict[str, str]
    expected_tool_schemas: dict[str, str]
    _submitted: set[str]

    def __init__(self, venue: PaperVenue, tool_schemas: dict[str, str], expected_tool_schemas: dict[str, str]):
        self.venue = venue
        self.tool_schemas = tool_schemas
        self.expected_tool_schemas = expected_tool_schemas
        self._submitted = set()
        if set(tool_schemas) != ALLOWED_OPERATIONS:
            raise AdapterRefusal("tool inventory contains missing or unknown operations")
        if tool_schemas != expected_tool_schemas:
            raise AdapterRefusal("tool schema differs from the qualified schema snapshot")

    def verify_paper_mode(self) -> dict[str, Any]:
        evidence = self.venue.get_paper_status()
        self._validate_evidence(evidence)
        return evidence

    def _validate_evidence(self, evidence: dict[str, Any]) -> None:
        required = {"mode", "account_id", "observed_at_utc", "retrieved_at_utc", "tool_schema_digest"}
        if not isinstance(evidence, dict) or not required <= evidence.keys():
            raise AdapterRefusal("simulation evidence is incomplete")
        if evidence["mode"] != "SIMULATED":
            raise AdapterRefusal("venue is not verified as simulated")
        if not all(isinstance(evidence[k], str) and evidence[k] for k in required):
            raise AdapterRefusal("simulation evidence contains missing or ambiguous values")
        try:
            observed = datetime.fromisoformat(evidence["observed_at_utc"].replace("Z", "+00:00"))
            retrieved = datetime.fromisoformat(evidence["retrieved_at_utc"].replace("Z", "+00:00"))
            age = datetime.now(timezone.utc) - retrieved
        except ValueError as exc:
            raise AdapterRefusal("simulation evidence timestamps are invalid") from exc
        if observed.utcoffset() != timedelta(0) or retrieved.utcoffset() != timedelta(0) or age < timedelta(0) or age > timedelta(seconds=60) or retrieved < observed:
            raise AdapterRefusal("simulation evidence is stale or time order is invalid")
        schema_digest = hashlib.sha256(json.dumps(self.expected_tool_schemas, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if evidence["tool_schema_digest"] != schema_digest:
            raise AdapterRefusal("simulation evidence does not bind the qualified tool schema")

    def submit_once(self, signal: dict[str, Any], evidence: dict[str, Any], client_order_id: str) -> dict[str, Any]:
        if client_order_id in self._submitted:
            raise AdapterRefusal("duplicate submission blocked; reconcile the existing client order ID")
        self._validate_evidence(evidence)
        try:
            decision = datetime.fromisoformat(signal["decision_time_utc"].replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError) as exc:
            raise AdapterRefusal("signal decision time is invalid") from exc
        signal_age = datetime.now(timezone.utc) - decision
        if decision.utcoffset() != timedelta(0) or signal_age < timedelta(0) or signal_age > timedelta(hours=24):
            raise AdapterRefusal("signal is stale or has a future decision time")
        if signal.get("gate_result") != "QUALIFIED":
            raise AdapterRefusal("rejected signal cannot be submitted")
        asset = signal.get("asset", {})
        if not all(asset.get(key) for key in ("venue", "instrument_id", "base", "quote", "instrument_type")):
            raise AdapterRefusal("ambiguous asset identity")
        order = signal.get("order", {})
        if order.get("side") not in {"buy", "sell"} or order.get("order_type") not in {"market", "limit"}:
            raise AdapterRefusal("invalid order parameters")
        if not isinstance(order.get("quantity"), (int, float)) or order["quantity"] <= 0:
            raise AdapterRefusal("invalid order quantity")
        if order["order_type"] == "limit" and not (isinstance(order.get("limit_price"), (int, float)) and order["limit_price"] > 0):
            raise AdapterRefusal("limit order requires a positive limit price")
        self._submitted.add(client_order_id)  # record before call: exceptions are uncertain, never retried
        try:
            return self.venue.submit_simulated_order({"client_order_id": client_order_id, "asset": asset, "order": order})
        except Exception as exc:
            raise AdapterRefusal("SUBMISSION_UNKNOWN; reconcile by client_order_id; do not retry") from exc

    def dispatch(self, operation: str, *args: Any, **kwargs: Any) -> Any:
        if operation not in ALLOWED_OPERATIONS:
            raise AdapterRefusal("unknown operation rejected")
        if operation == "get_paper_status":
            return self.verify_paper_mode()
        if operation == "submit_simulated_order":
            return self.submit_once(*args, **kwargs)
        if operation == "reconcile_order":
            return self.venue.reconcile_order(*args, **kwargs)
        raise AdapterRefusal("operation unavailable")


def evaluation_completeness(reconciliation: dict[str, Any]) -> dict[str, Any]:
    required = ("order_id", "fills", "fees", "funding", "account_equity", "equity_history")
    missing = [key for key in required if reconciliation.get(key) is None]
    complete = not missing
    return {"complete": complete, "missing_fields": missing, "metrics": None if not complete else {}}
