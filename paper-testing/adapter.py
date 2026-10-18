"""Fail-closed policy wrapper. This module contains no Liquid or network client."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import copy
from pathlib import Path
from typing import Any, Protocol

from idempotency import DuplicateIntent, ReservationStore
from validate_paper_record import validate_signal


REQUIRED_TOOL_SCHEMAS = frozenset({"get_paper_status", "get_instrument_metadata", "submit_simulated_order", "reconcile_order"})
def _default_registry_path() -> Path:
    """One registry per checked-out installation; never caller-selected."""
    return Path(__file__).resolve().parent / "runs" / "paper-idempotency.sqlite3"


class AdapterRefusal(RuntimeError):
    pass


class PaperVenue(Protocol):
    """The runtime connector must implement only qualified, exact operations."""
    def get_tool_schemas(self) -> dict[str, Any]: ...
    def get_paper_status(self) -> dict[str, Any]: ...
    def get_instrument_metadata(self, instrument_id: str) -> dict[str, Any]: ...
    def submit_simulated_order(self, request: dict[str, Any]) -> dict[str, Any]: ...
    def reconcile_order(self, client_order_id: str) -> dict[str, Any]: ...


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AdapterRefusal("request is not canonical JSON") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise AdapterRefusal(f"{label} must be UTC ISO timestamp ending Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise AdapterRefusal(f"{label} is invalid") from exc
    if parsed.utcoffset() != timedelta(0):
        raise AdapterRefusal(f"{label} must be UTC")
    return parsed


def _fresh_pair(observed_value: Any, retrieved_value: Any, maximum_age: timedelta, label: str) -> None:
    observed = _timestamp(observed_value, f"{label}.observed_at_utc")
    retrieved = _timestamp(retrieved_value, f"{label}.retrieved_at_utc")
    age = datetime.now(timezone.utc) - retrieved
    if retrieved < observed or age < timedelta(0) or age > maximum_age:
        raise AdapterRefusal(f"{label} evidence is stale or time order is invalid")


def _decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise AdapterRefusal(f"{label} must be a finite decimal")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise AdapterRefusal(f"{label} must be a finite decimal") from exc
    if not result.is_finite():
        raise AdapterRefusal(f"{label} must be finite")
    return result


class PaperAdapter:
    venue: PaperVenue
    expected_tool_schemas: dict[str, Any]
    expected_account_id: str

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"reservation_store", "_reservation_store"} and hasattr(self, "_reservation_store"):
            raise AttributeError("the durable reservation registry is fixed for this adapter")
        object.__setattr__(self, name, value)

    def __init__(self, venue: PaperVenue, expected_tool_schemas: dict[str, Any], expected_account_id: str):
        if not isinstance(expected_tool_schemas, dict) or set(expected_tool_schemas) != REQUIRED_TOOL_SCHEMAS:
            raise AdapterRefusal("qualified schema snapshot must contain the exact four supported operations")
        if not isinstance(expected_account_id, str) or not expected_account_id.strip():
            raise AdapterRefusal("a qualified account ID is required")
        self.venue = venue
        self.expected_tool_schemas = copy.deepcopy(expected_tool_schemas)
        self.expected_account_id = expected_account_id
        self._reservation_store = ReservationStore(_default_registry_path())

    @property
    def reservation_store(self) -> ReservationStore:
        """Read-only registry handle for reconciliation and audit inspection."""
        return self._reservation_store

    def _verify_tool_schemas(self) -> tuple[str, dict[str, Any]]:
        actual = self.venue.get_tool_schemas()
        if not isinstance(actual, dict) or actual != self.expected_tool_schemas or set(actual) != REQUIRED_TOOL_SCHEMAS:
            raise AdapterRefusal("runtime tool inventory or schema differs from the qualified snapshot")
        return _digest(actual), copy.deepcopy(actual)

    def _verify_paper_status(self, schema_digest: str) -> dict[str, Any]:
        evidence = self.venue.get_paper_status()
        required = {"mode", "account_id", "observed_at_utc", "retrieved_at_utc", "tool_schema_digest"}
        if not isinstance(evidence, dict) or not required <= evidence.keys():
            raise AdapterRefusal("simulation status evidence is incomplete")
        if evidence["mode"] != "SIMULATED":
            raise AdapterRefusal("venue is not verified as simulated")
        if evidence["account_id"] != self.expected_account_id:
            raise AdapterRefusal("runtime account identity differs from qualified account")
        _fresh_pair(evidence["observed_at_utc"], evidence["retrieved_at_utc"], timedelta(seconds=60), "simulation status")
        if evidence["tool_schema_digest"] != schema_digest:
            raise AdapterRefusal("simulation status is not bound to the current tool schema")
        return evidence

    def verify_paper_mode(self) -> dict[str, Any]:
        """Read-only qualification probe; result is informational and cannot authorize a later submit."""
        schema_digest, _ = self._verify_tool_schemas()
        return self._verify_paper_status(schema_digest)

    def _verify_instrument(self, signal: dict[str, Any]) -> dict[str, Any]:
        asset = signal["asset"]
        metadata = self.venue.get_instrument_metadata(asset["instrument_id"])
        if not isinstance(metadata, dict):
            raise AdapterRefusal("instrument metadata is unavailable")
        _fresh_pair(metadata.get("observed_at_utc"), metadata.get("retrieved_at_utc"), timedelta(days=1), "instrument metadata")
        for key in ("venue", "instrument_id", "base", "quote", "instrument_type"):
            if metadata.get(key) != asset[key]:
                raise AdapterRefusal(f"signal {key} does not match venue instrument metadata")
        for key in ("min_quantity", "max_quantity", "quantity_step", "price_tick"):
            if key not in metadata:
                raise AdapterRefusal(f"instrument metadata missing {key}")
        min_qty = _decimal(metadata["min_quantity"], "min_quantity")
        max_qty = _decimal(metadata["max_quantity"], "max_quantity")
        step = _decimal(metadata["quantity_step"], "quantity_step")
        tick = _decimal(metadata["price_tick"], "price_tick")
        if min_qty <= 0 or max_qty < min_qty or step <= 0 or tick <= 0:
            raise AdapterRefusal("instrument precision constraints are invalid")
        quantity = _decimal(signal["order"]["quantity"], "quantity")
        if quantity < min_qty or quantity > max_qty or quantity % step != 0:
            raise AdapterRefusal("quantity is outside venue limits or precision")
        order_type = signal["order"]["order_type"]
        price = signal["order"].get("limit_price")
        if order_type == "limit":
            limit = _decimal(price, "limit_price")
            if limit <= 0 or limit % tick != 0:
                raise AdapterRefusal("limit price is outside venue tick precision")
        elif price is not None:
            raise AdapterRefusal("market order must not include limit_price")
        return metadata

    def submit_once(self, signal: dict[str, Any]) -> dict[str, Any]:
        """Submit only after fresh runtime checks and an atomic durable intent reservation."""
        dispatch_started = False
        try:
            validate_signal(signal)
        except (ValueError, TypeError, KeyError) as exc:
            raise AdapterRefusal(f"invalid frozen signal: {exc}") from exc
        if signal["gate_result"] != "QUALIFIED":
            raise AdapterRefusal("rejected signal cannot be submitted")
        decision = _timestamp(signal["decision_time_utc"], "signal.decision_time_utc")
        signal_age = datetime.now(timezone.utc) - decision
        if signal_age < timedelta(0) or signal_age > timedelta(hours=24):
            raise AdapterRefusal("signal is stale or has a future decision time")
        if signal["direction"] == "flat":
            raise AdapterRefusal("flat signals cannot create an entry order")
        expected_side = "buy" if signal["direction"] == "long" else "sell"
        if signal["order"]["side"] != expected_side:
            raise AdapterRefusal("direction and order side are inconsistent")

        # All evidence is fetched in this call; previously returned/caller-supplied status is not accepted.
        schema_digest, _ = self._verify_tool_schemas()
        metadata = self._verify_instrument(signal)
        # Recheck tool schemas and paper mode after metadata resolution, immediately before durable reservation.
        schema_digest, schema_snapshot = self._verify_tool_schemas()
        evidence = self._verify_paper_status(schema_digest)
        instrument_contract = {key: value for key, value in metadata.items() if key not in {"observed_at_utc", "retrieved_at_utc"}}
        request = {"account_id": evidence["account_id"], "asset": signal["asset"], "order": signal["order"],
                   "signal_id": signal["signal_id"], "signal_digest": _digest(signal), "instrument_metadata_digest": _digest(instrument_contract),
                   "tool_schema_digest": schema_digest}
        request_digest = _digest(request)
        intent_journal = {"signal": signal, "venue_evidence": evidence, "instrument_metadata": metadata,
                          "tool_schemas": schema_snapshot, "request": request, "request_digest": request_digest}
        try:
            client_order_id = self.reservation_store.reserve(signal["signal_id"], request_digest,
                                                              datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                                                              _canonical(intent_journal).decode("utf-8"))
        except DuplicateIntent as exc:
            raise AdapterRefusal(str(exc)) from exc

        try:
            # SQLite reservation may block. Recheck every dispatch-critical
            # runtime fact after that wait, immediately before the network call.
            dispatch_schema_digest, dispatch_schemas = self._verify_tool_schemas()
            if dispatch_schema_digest != schema_digest:
                raise AdapterRefusal("tool schema changed after intent reservation")
            dispatch_metadata = self._verify_instrument(signal)
            dispatch_contract = {key: value for key, value in dispatch_metadata.items()
                                 if key not in {"observed_at_utc", "retrieved_at_utc"}}
            if _digest(dispatch_contract) != request["instrument_metadata_digest"]:
                raise AdapterRefusal("instrument metadata changed after intent reservation")
            dispatch_evidence = self._verify_paper_status(dispatch_schema_digest)
            intent_journal["dispatch_check"] = {"venue_evidence": dispatch_evidence,
                                                 "instrument_metadata": dispatch_metadata,
                                                 "tool_schemas": dispatch_schemas}
            self.reservation_store.update_intent(signal["signal_id"], _canonical(intent_journal).decode("utf-8"))
            dispatch_started = True
            receipt = self.venue.submit_simulated_order({**request, "client_order_id": client_order_id})
            if not isinstance(receipt, dict) or receipt.get("acknowledged") is not True or not isinstance(receipt.get("order_id"), str) or not receipt["order_id"]:
                raise AdapterRefusal("submission response is not a stable acknowledgement")
            persisted = {**receipt, "outcome": "ACKNOWLEDGED", "client_order_id": client_order_id,
                         "request_digest": request_digest, "acknowledged_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
            self.reservation_store.mark(signal["signal_id"], "ACKNOWLEDGED", _canonical(persisted).decode("utf-8"))
            return persisted
        except Exception as exc:
            if dispatch_started:
                try:
                    self.reservation_store.mark(signal["signal_id"], "UNKNOWN")
                except Exception:
                    # The initial RESERVED row is itself durable and blocks a retry.
                    pass
            if isinstance(exc, AdapterRefusal):
                if not dispatch_started:
                    raise AdapterRefusal(f"DISPATCH_BLOCKED; reservation consumed; do not retry ({exc})") from exc
                raise AdapterRefusal(f"SUBMISSION_UNKNOWN; reconcile by {client_order_id}; do not retry") from exc
            if not dispatch_started:
                raise AdapterRefusal(f"DISPATCH_BLOCKED; reservation consumed; do not retry ({exc})") from exc
            raise AdapterRefusal(f"SUBMISSION_UNKNOWN; reconcile by {client_order_id}; do not retry") from exc

    def reconcile_order(self, client_order_id: str) -> dict[str, Any]:
        if not isinstance(client_order_id, str) or not client_order_id.startswith("fdx-"):
            raise AdapterRefusal("unknown client order ID")
        if self.reservation_store.get_by_client_order_id(client_order_id) is None:
            raise AdapterRefusal("client order ID is not present in the durable intent registry")
        schema_digest, _ = self._verify_tool_schemas()
        self._verify_paper_status(schema_digest)
        result = self.venue.reconcile_order(client_order_id)
        self.reservation_store.record_reconciliation(client_order_id, _canonical(result).decode("utf-8"))
        return result


def evaluation_completeness(reconciliation: dict[str, Any]) -> dict[str, Any]:
    required = ("order_id", "fills", "fees", "funding", "account_equity", "equity_history")
    missing = [key for key in required if reconciliation.get(key) is None]
    complete = not missing
    return {"complete": complete, "missing_fields": missing, "metrics": None if not complete else {}}
