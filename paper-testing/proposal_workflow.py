"""Local-only immutable proposal workflow. No Liquid/network client is defined here."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from contextlib import contextmanager
from typing import Any


STATES = {"DRAFT", "VALIDATED", "REVIEW_REQUESTED", "REVIEW_LINK_CREATED", "EXTERNAL_CONFIRMATION_UNKNOWN"}
ATTEMPTS = {"NOT_ATTEMPTED", "AUTHORIZED_PENDING", "DISPATCH_RESERVED", "SUCCEEDED", "FAILED", "UNKNOWN"}
EXTERNAL_REVIEW_LINK_ENABLED = False


class ProposalError(ValueError):
    pass


def canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProposalError("proposal details must be canonical JSON") from exc


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_details(details: dict[str, Any]) -> None:
    required = {"account_id", "instrument", "side", "quantity", "quantity_unit", "order_type", "research_provenance"}
    if not isinstance(details, dict) or set(details) != required:
        raise ProposalError("proposal details have missing or unknown fields")
    if not isinstance(details["account_id"], str) or not details["account_id"].strip():
        raise ProposalError("explicit account_id is required")
    inst = details["instrument"]
    if not isinstance(inst, dict) or set(inst) != {"venue", "instrument_id", "base", "quote", "instrument_type"} or not all(isinstance(v, str) and v.strip() for v in inst.values()):
        raise ProposalError("complete explicit instrument metadata is required")
    if inst["instrument_type"] not in {"spot", "perpetual"}:
        raise ProposalError("instrument_type must be spot or perpetual")
    if details["side"] not in {"buy", "sell"} or details["order_type"] not in {"market", "limit"}:
        raise ProposalError("invalid side or requested order type")
    q = details["quantity"]
    if isinstance(q, bool) or not isinstance(q, (int, float, str)) or not str(q).strip():
        raise ProposalError("quantity is required")
    try:
        if float(q) <= 0 or not float(q) < float("inf"):
            raise ProposalError("quantity must be finite and positive")
    except (ValueError, OverflowError):
        raise ProposalError("quantity must be finite and positive")
    if not isinstance(details["quantity_unit"], str) or not details["quantity_unit"].strip():
        raise ProposalError("quantity_unit is required; no unit conversion is inferred")
    if not isinstance(details["research_provenance"], list) or not all(isinstance(x, str) and x for x in details["research_provenance"]):
        raise ProposalError("research_provenance must be an array of references")


class ProposalStore:
    """Independent SQLite store with immutable proposal identity and detail digest."""
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else Path(__file__).resolve().parent / "runs" / "proposals.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS proposals (
                proposal_id TEXT PRIMARY KEY, details_json TEXT NOT NULL, details_digest TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('DRAFT','VALIDATED','REVIEW_REQUESTED','REVIEW_LINK_CREATED','EXTERNAL_CONFIRMATION_UNKNOWN')),
                external_attempt_status TEXT NOT NULL CHECK(external_attempt_status IN ('NOT_ATTEMPTED','AUTHORIZED_PENDING','DISPATCH_RESERVED','SUCCEEDED','FAILED','UNKNOWN')),
                created_at_utc TEXT NOT NULL, updated_at_utc TEXT NOT NULL,
                authorization_json TEXT, operation_json TEXT, response_json TEXT
            )""")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
        finally:
            db.close()

    def create(self, proposal_id: str, details: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(proposal_id, str) or not proposal_id.strip():
            raise ProposalError("proposal_id is required")
        raw = canonical(details).decode()
        dg = digest(details)
        stamp = _now()
        with self._db() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                db.execute("INSERT INTO proposals VALUES (?,?,?,'DRAFT','NOT_ATTEMPTED',?,?,NULL,NULL,NULL)", (proposal_id, raw, dg, stamp, stamp))
                db.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                if db.in_transaction: db.execute("ROLLBACK")
                raise ProposalError("duplicate proposal_id; create a new identity for changed details") from exc
            except Exception:
                if db.in_transaction: db.execute("ROLLBACK")
                raise
        return self.get(proposal_id)

    def validate(self, proposal_id: str) -> dict[str, Any]:
        row = self.get(proposal_id)
        if not row: raise ProposalError("unknown proposal")
        details = json.loads(row["details_json"])
        if digest(details) != row["details_digest"]: raise ProposalError("stored proposal details digest mismatch")
        validate_details(details)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            cur = db.execute("UPDATE proposals SET state='VALIDATED', updated_at_utc=? WHERE proposal_id=? AND state='DRAFT' AND details_digest=?", (_now(), proposal_id, row["details_digest"]))
            if cur.rowcount != 1:
                db.execute("ROLLBACK")
                raise ProposalError("proposal is immutable and cannot be revalidated from this state")
            db.execute("COMMIT")
        return self.get(proposal_id)

    def authorize(self, proposal_id: str, details_digest: str, operation: str, approving_authority: str, approval_evidence: str) -> dict[str, Any]:
        """Record explicit user authorization. This does not enable or invoke an operation."""
        row = self.get(proposal_id)
        if not row or row["state"] != "VALIDATED": raise ProposalError("only validated proposals can be authorized")
        if details_digest != row["details_digest"]: raise ProposalError("authorization digest does not match proposal")
        if not all(isinstance(x, str) and x.strip() for x in (operation, approving_authority, approval_evidence)):
            raise ProposalError("exact operation, approving authority, and explicit approval evidence are required")
        auth = {"proposal_id": proposal_id, "details_digest": details_digest, "operation": operation,
                "approving_authority": approving_authority, "approval_evidence": approval_evidence, "authorized_at_utc": _now(), "consumed": False}
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            cur = db.execute("UPDATE proposals SET external_attempt_status='AUTHORIZED_PENDING', authorization_json=?, updated_at_utc=? WHERE proposal_id=? AND state='VALIDATED' AND external_attempt_status='NOT_ATTEMPTED' AND details_digest=?", (canonical(auth).decode(), _now(), proposal_id, details_digest))
            if cur.rowcount != 1:
                db.execute("ROLLBACK")
                raise ProposalError("authorization absent, mismatched, or already consumed")
            db.execute("COMMIT")
        return self.get(proposal_id)

    def reserve_dispatch(self, proposal_id: str, details_digest: str, operation: str, approving_authority: str) -> dict[str, Any]:
        """Atomically consume a matching authorization; invocation remains disabled pending qualification."""
        if not EXTERNAL_REVIEW_LINK_ENABLED:
            raise ProposalError("EXTERNAL_OPERATION_DISABLED: no operation-specific compatibility qualification")
        # Deliberately unreachable until the constant is changed through a separately reviewed qualification.
        row = self.get(proposal_id)
        auth = json.loads(row["authorization_json"] or "{}")
        if auth.get("details_digest") != details_digest or auth.get("operation") != operation or auth.get("approving_authority") != approving_authority or auth.get("consumed"):
            raise ProposalError("authorization absent, mismatched, or already consumed")
        auth["consumed"] = True
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            cur = db.execute("UPDATE proposals SET state='REVIEW_REQUESTED', external_attempt_status='DISPATCH_RESERVED', authorization_json=?, operation_json=?, updated_at_utc=? WHERE proposal_id=? AND state='VALIDATED' AND external_attempt_status='AUTHORIZED_PENDING'", (canonical(auth).decode(), canonical({"operation": operation}).decode(), _now(), proposal_id))
            if cur.rowcount != 1: db.execute("ROLLBACK"); raise ProposalError("authorization already consumed")
            db.execute("COMMIT")
        return self.get(proposal_id)

    def get(self, proposal_id: str) -> dict[str, Any] | None:
        with self._db() as db:
            row = db.execute("SELECT proposal_id,details_json,details_digest,state,external_attempt_status,created_at_utc,updated_at_utc,authorization_json,operation_json,response_json FROM proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
        if row is None: return None
        return dict(zip(("proposal_id","details_json","details_digest","state","external_attempt_status","created_at_utc","updated_at_utc","authorization_json","operation_json","response_json"), row))
