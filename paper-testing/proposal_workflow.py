"""Local-only proposal records. This module has no Liquid or network invocation capability."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any


STATES = {"DRAFT", "VALIDATED", "REVIEW_REQUESTED", "REVIEW_LINK_CREATED", "EXTERNAL_CONFIRMATION_UNKNOWN"}
ATTEMPTS = {"NOT_ATTEMPTED", "AUTHORIZED_PENDING", "DISPATCH_RESERVED", "SUCCEEDED", "FAILED", "UNKNOWN"}
_PROPOSAL_ID = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?\Z", re.ASCII)
SCHEMA_VERSION = 4
_IMMUTABLE_TRIGGER = "proposals_immutable_details"
_LIFECYCLE_TRIGGER = "proposals_valid_lifecycle"
_NO_DELETE_TRIGGER = "proposals_no_delete"
_NO_REUSE_TRIGGER = "proposals_no_identity_reuse"
SCHEMA_SQL = """CREATE TABLE proposals (
    proposal_id TEXT PRIMARY KEY,
    details_json TEXT NOT NULL,
    details_digest TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('DRAFT','VALIDATED','REVIEW_REQUESTED','REVIEW_LINK_CREATED','EXTERNAL_CONFIRMATION_UNKNOWN')),
    external_attempt_status TEXT NOT NULL CHECK(external_attempt_status IN ('NOT_ATTEMPTED','AUTHORIZED_PENDING','DISPATCH_RESERVED','SUCCEEDED','FAILED','UNKNOWN')),
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    approval_metadata_json TEXT,
    operation_json TEXT,
    response_json TEXT
)"""
_COLUMNS = {"proposal_id", "details_json", "details_digest", "state", "external_attempt_status", "created_at_utc", "updated_at_utc", "approval_metadata_json", "operation_json", "response_json"}
_IMMUTABLE_TRIGGER_SQL = "CREATE TRIGGER proposals_immutable_details BEFORE UPDATE OF proposal_id, details_json, details_digest ON proposals BEGIN SELECT RAISE(ABORT, 'proposal identity and details are immutable'); END"
_LIFECYCLE_TRIGGER_SQL = "CREATE TRIGGER proposals_valid_lifecycle BEFORE UPDATE OF state, external_attempt_status ON proposals WHEN NOT ((NEW.state = OLD.state AND NEW.external_attempt_status = OLD.external_attempt_status) OR (OLD.state = 'DRAFT' AND NEW.state = 'VALIDATED' AND OLD.external_attempt_status = 'NOT_ATTEMPTED' AND NEW.external_attempt_status = 'NOT_ATTEMPTED')) BEGIN SELECT RAISE(ABORT, 'invalid proposal lifecycle transition'); END"
_NO_DELETE_TRIGGER_SQL = "CREATE TRIGGER proposals_no_delete BEFORE DELETE ON proposals BEGIN SELECT RAISE(ABORT, 'proposal records cannot be deleted or reused'); END"
_NO_REUSE_TRIGGER_SQL = "CREATE TRIGGER proposals_no_identity_reuse BEFORE INSERT ON proposals WHEN EXISTS (SELECT 1 FROM proposals WHERE proposal_id = NEW.proposal_id) BEGIN SELECT RAISE(ABORT, 'proposal identity already exists and cannot be reused'); END"
_WINDOWS_RESERVED_IDS = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


class ProposalError(ValueError):
    pass


def validate_proposal_id(proposal_id: Any) -> str:
    if not isinstance(proposal_id, str) or not _PROPOSAL_ID.fullmatch(proposal_id):
        raise ProposalError("proposal_id must be 1-64 lowercase ASCII letters, digits, or hyphens, with alphanumeric ends")
    if proposal_id in _WINDOWS_RESERVED_IDS:
        raise ProposalError("proposal_id is a reserved Windows device name")
    return proposal_id


def canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProposalError("proposal data must be canonical JSON") from exc


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _timestamp(value: Any, name: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ProposalError(f"{name} must be a UTC ISO timestamp ending Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ProposalError(f"{name} is invalid") from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ProposalError(f"{name} must be UTC")
    return parsed


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
        number = float(q)
    except (ValueError, OverflowError):
        raise ProposalError("quantity must be finite and positive")
    if number <= 0 or number == float("inf") or number != number:
        raise ProposalError("quantity must be finite and positive")
    if not isinstance(details["quantity_unit"], str) or not details["quantity_unit"].strip():
        raise ProposalError("quantity_unit is required; no unit conversion is inferred")
    if not isinstance(details["research_provenance"], list) or not all(isinstance(x, str) and x for x in details["research_provenance"]):
        raise ProposalError("research_provenance must be an array of references")


def _normalized_sql(sql: str | None) -> str:
    return " ".join((sql or "").split()).casefold()


def check_schema(db: sqlite3.Connection) -> None:
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    columns = {r[1] for r in db.execute("PRAGMA table_info(proposals)")}
    table_sql = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='proposals'").fetchone()
    triggers = {r[0]: r[1] for r in db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name='proposals'")}
    if (tables != {"proposals"} or columns != _COLUMNS or db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION
            or not table_sql or _normalized_sql(table_sql[0]) != _normalized_sql(SCHEMA_SQL)
            or _normalized_sql(triggers.get(_IMMUTABLE_TRIGGER)) != _normalized_sql(_IMMUTABLE_TRIGGER_SQL)
            or _normalized_sql(triggers.get(_LIFECYCLE_TRIGGER)) != _normalized_sql(_LIFECYCLE_TRIGGER_SQL)
            or _normalized_sql(triggers.get(_NO_DELETE_TRIGGER)) != _normalized_sql(_NO_DELETE_TRIGGER_SQL)
            or _normalized_sql(triggers.get(_NO_REUSE_TRIGGER)) != _normalized_sql(_NO_REUSE_TRIGGER_SQL)
            or set(triggers) != {_IMMUTABLE_TRIGGER, _LIFECYCLE_TRIGGER, _NO_DELETE_TRIGGER, _NO_REUSE_TRIGGER}):
        raise ProposalError("proposal database schema is missing or incompatible")


def _validate_approval(raw: str | None, proposal_id: str, details_digest: str) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        approval = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (TypeError, json.JSONDecodeError, ValueError) as exc:
        raise ProposalError("approval metadata is invalid JSON") from exc
    required = {"proposal_id", "details_digest", "operation", "approving_authority", "evidence_text", "recorded_at_utc", "source", "runtime_authorized"}
    if not isinstance(approval, dict) or set(approval) != required:
        raise ProposalError("approval metadata has missing or unknown fields")
    if canonical(approval).decode("utf-8") != raw:
        raise ProposalError("approval metadata is not canonical JSON")
    if approval["proposal_id"] != proposal_id or approval["details_digest"] != details_digest:
        raise ProposalError("approval metadata is not bound to this proposal")
    for key in ("operation", "approving_authority", "evidence_text"):
        if not isinstance(approval[key], str) or not approval[key].strip() or len(approval[key]) > 2000:
            raise ProposalError(f"approval metadata {key} is invalid")
    _timestamp(approval["recorded_at_utc"], "approval recorded_at_utc")
    if approval["source"] != "CALLER_SUPPLIED" or approval["runtime_authorized"] is not False:
        raise ProposalError("caller-supplied approval metadata cannot establish runtime authorization")
    return approval


def validate_persisted_record(row: dict[str, Any]) -> dict[str, Any]:
    """Validate a complete row; unsupported external lifecycle claims fail closed."""
    proposal_id = validate_proposal_id(row.get("proposal_id"))
    details_raw = row.get("details_json")
    try:
        details = json.loads(details_raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (TypeError, json.JSONDecodeError, ValueError) as exc:
        raise ProposalError("details_json is invalid JSON") from exc
    if not isinstance(details, dict) or canonical(details).decode("utf-8") != details_raw:
        raise ProposalError("details_json must be a canonical object")
    details_digest = row.get("details_digest")
    if not isinstance(details_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", details_digest) or digest(details) != details_digest:
        raise ProposalError("details digest mismatch")
    state, attempt = row.get("state"), row.get("external_attempt_status")
    if state not in STATES or attempt not in ATTEMPTS:
        raise ProposalError("unknown proposal state or external-attempt status")
    created = _timestamp(row.get("created_at_utc"), "created_at_utc")
    updated = _timestamp(row.get("updated_at_utc"), "updated_at_utc")
    if updated < created:
        raise ProposalError("updated_at_utc precedes created_at_utc")
    approval = _validate_approval(row.get("approval_metadata_json"), proposal_id, details_digest)
    operation_raw, response_raw = row.get("operation_json"), row.get("response_json")

    # This workflow has no qualified runtime authorization or external call path.
    # Consequently only local states with no external attempt are exportable.
    if state not in {"DRAFT", "VALIDATED"} or attempt != "NOT_ATTEMPTED":
        raise ProposalError("external lifecycle state is unsupported; no qualified invocation path exists")
    if operation_raw is not None or response_raw is not None:
        raise ProposalError("unattempted proposal cannot contain operation or response evidence")
    if state == "DRAFT" and approval is not None:
        raise ProposalError("draft proposal cannot contain approval metadata")
    if approval is not None:
        approved_at = _timestamp(approval["recorded_at_utc"], "approval recorded_at_utc")
        if approved_at < created or approved_at > updated:
            raise ProposalError("approval metadata timestamp is outside the proposal record interval")
    if state == "VALIDATED":
        validate_details(details)
    return {"schema_version": "proposal.v1", "proposal_id": proposal_id, "details": details,
            "details_digest": details_digest, "state": state, "external_attempt_status": attempt,
            "created_at_utc": row["created_at_utc"], "updated_at_utc": row["updated_at_utc"],
            "approval_metadata": approval}


class ProposalStore:
    """Independent SQLite store. Schema creation occurs only on this write-side API."""
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else Path(__file__).resolve().parent / "runs" / "proposals.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_or_validate()

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
        finally:
            db.close()

    def _initialize_or_validate(self) -> None:
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if not tables:
                db.execute(SCHEMA_SQL)
                db.execute(_IMMUTABLE_TRIGGER_SQL)
                db.execute(_LIFECYCLE_TRIGGER_SQL)
                db.execute(_NO_DELETE_TRIGGER_SQL)
                db.execute(_NO_REUSE_TRIGGER_SQL)
                db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            check_schema(db)
            db.execute("COMMIT")

    def create(self, proposal_id: str, details: dict[str, Any]) -> dict[str, Any]:
        proposal_id = validate_proposal_id(proposal_id)
        if not isinstance(details, dict):
            raise ProposalError("proposal details must be an object")
        raw, details_digest, stamp = canonical(details).decode("utf-8"), digest(details), _now()
        with self._db() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                db.execute("INSERT INTO proposals VALUES (?,?,?,'DRAFT','NOT_ATTEMPTED',?,?,NULL,NULL,NULL)", (proposal_id, raw, details_digest, stamp, stamp))
                db.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                if db.in_transaction: db.execute("ROLLBACK")
                raise ProposalError("duplicate proposal_id; create a new identity for changed details") from exc
            except Exception:
                if db.in_transaction: db.execute("ROLLBACK")
                raise
        return self.get(proposal_id)

    def validate(self, proposal_id: str) -> dict[str, Any]:
        proposal_id = validate_proposal_id(proposal_id)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._get(db, proposal_id)
            if row is None:
                db.execute("ROLLBACK")
                raise ProposalError("unknown proposal")
            details = json.loads(row["details_json"])
            if canonical(details).decode("utf-8") != row["details_json"] or digest(details) != row["details_digest"]:
                db.execute("ROLLBACK")
                raise ProposalError("stored proposal details digest mismatch")
            validate_details(details)
            cur = db.execute("UPDATE proposals SET state='VALIDATED', updated_at_utc=? WHERE proposal_id=? AND state='DRAFT' AND external_attempt_status='NOT_ATTEMPTED' AND details_digest=?", (_now(), proposal_id, row["details_digest"]))
            if cur.rowcount != 1:
                db.execute("ROLLBACK")
                raise ProposalError("invalid lifecycle transition from current proposal state")
            db.execute("COMMIT")
        return self.get(proposal_id)

    def record_approval_metadata(self, proposal_id: str, details_digest: str, operation: str, approving_authority: str, evidence_text: str) -> dict[str, Any]:
        """Persist caller-supplied metadata only; this does not authorize any runtime operation."""
        proposal_id = validate_proposal_id(proposal_id)
        metadata = {"proposal_id": proposal_id, "details_digest": details_digest, "operation": operation,
                    "approving_authority": approving_authority, "evidence_text": evidence_text,
                    "recorded_at_utc": _now(), "source": "CALLER_SUPPLIED", "runtime_authorized": False}
        approval_raw = canonical(metadata).decode("utf-8")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._get(db, proposal_id)
            if row is None or row["state"] != "VALIDATED" or row["external_attempt_status"] != "NOT_ATTEMPTED":
                db.execute("ROLLBACK")
                raise ProposalError("approval metadata can only be recorded for a validated, unattempted proposal")
            if details_digest != row["details_digest"]:
                db.execute("ROLLBACK")
                raise ProposalError("approval metadata digest does not match proposal")
            _validate_approval(approval_raw, proposal_id, details_digest)
            cur = db.execute("UPDATE proposals SET approval_metadata_json=?, updated_at_utc=? WHERE proposal_id=? AND state='VALIDATED' AND external_attempt_status='NOT_ATTEMPTED' AND details_digest=? AND approval_metadata_json IS NULL", (approval_raw, _now(), proposal_id, details_digest))
            if cur.rowcount != 1:
                db.execute("ROLLBACK")
                raise ProposalError("approval metadata already recorded or proposal changed")
            db.execute("COMMIT")
        return self.get(proposal_id)

    @staticmethod
    def _get(db: sqlite3.Connection, proposal_id: str) -> dict[str, Any] | None:
        columns = ("proposal_id", "details_json", "details_digest", "state", "external_attempt_status", "created_at_utc", "updated_at_utc", "approval_metadata_json", "operation_json", "response_json")
        row = db.execute("SELECT " + ",".join(columns) + " FROM proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
        return dict(zip(columns, row)) if row else None

    def get(self, proposal_id: str) -> dict[str, Any] | None:
        proposal_id = validate_proposal_id(proposal_id)
        with self._db() as db:
            check_schema(db)
            return self._get(db, proposal_id)
