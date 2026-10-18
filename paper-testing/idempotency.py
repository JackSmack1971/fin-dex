"""Durable, atomic, fail-closed registration of one immutable signal intent."""
from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class DuplicateIntent(RuntimeError):
    pass


class ReservationStore:
    """SQLite registry. A reservation is permanent, including after an uncertain send."""

    def __setattr__(self, name, value):
        if name in {"path", "_path"} and hasattr(self, "_path"):
            raise AttributeError("the durable reservation path is immutable")
        object.__setattr__(self, name, value)

    def __init__(self, path: str | Path):
        self._path = Path(path).expanduser().resolve()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._database() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS paper_intents (
                signal_id TEXT PRIMARY KEY,
                request_digest TEXT NOT NULL,
                client_order_id TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL CHECK(state IN ('RESERVED','UNKNOWN','ACKNOWLEDGED')),
                created_at_utc TEXT NOT NULL,
                receipt_json TEXT,
                receipt_digest TEXT,
                intent_json TEXT NOT NULL DEFAULT '{}',
                reconciliation_json TEXT
            )""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(paper_intents)")}
            if "intent_json" not in columns:
                db.execute("ALTER TABLE paper_intents ADD COLUMN intent_json TEXT NOT NULL DEFAULT '{}' ")
            if "reconciliation_json" not in columns:
                db.execute("ALTER TABLE paper_intents ADD COLUMN reconciliation_json TEXT")
            if "receipt_digest" not in columns:
                db.execute("ALTER TABLE paper_intents ADD COLUMN receipt_digest TEXT")

    @property
    def path(self) -> Path:
        return self._path

    def _connect(self, busy_timeout_ms: int = 30000):
        db = sqlite3.connect(self.path, timeout=busy_timeout_ms / 1000, isolation_level=None)
        db.execute("PRAGMA synchronous=FULL")
        db.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        return db

    @contextmanager
    def _database(self, busy_timeout_ms: int = 30000):
        db = self._connect(busy_timeout_ms)
        try:
            yield db
        finally:
            db.close()

    @staticmethod
    def client_order_id(signal_id: str, request_digest: str) -> str:
        token = hashlib.sha256(f"{signal_id}\0{request_digest}".encode()).hexdigest()[:32]
        return f"fdx-{token}"

    def reserve(self, signal_id: str, request_digest: str, created_at_utc: str, intent_json: str) -> str:
        client_id = self.client_order_id(signal_id, request_digest)
        with self._database() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                existing = db.execute("SELECT request_digest, client_order_id, state FROM paper_intents WHERE signal_id=?", (signal_id,)).fetchone()
                if existing:
                    if existing[0] != request_digest:
                        raise DuplicateIntent("signal ID already reserved with a different immutable request")
                    raise DuplicateIntent(f"signal ID already reserved ({existing[2]}); reconcile, never resubmit")
                db.execute("INSERT INTO paper_intents (signal_id, request_digest, client_order_id, state, created_at_utc, receipt_json, intent_json) VALUES (?, ?, ?, 'RESERVED', ?, NULL, ?)",
                           (signal_id, request_digest, client_id, created_at_utc, intent_json))
                db.execute("COMMIT")
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        return client_id

    def mark(self, signal_id: str, state: str, receipt_json: str | None = None) -> None:
        if state not in {"UNKNOWN", "ACKNOWLEDGED"}:
            raise ValueError("invalid reservation state transition")
        with self._database() as db:
            db.execute("BEGIN IMMEDIATE")
            digest = hashlib.sha256(receipt_json.encode("utf-8")).hexdigest() if state == "ACKNOWLEDGED" and receipt_json is not None else None
            cursor = db.execute("UPDATE paper_intents SET state=?, receipt_json=?, receipt_digest=? WHERE signal_id=? AND state='RESERVED'",
                                (state, receipt_json, digest, signal_id))
            if cursor.rowcount != 1:
                db.execute("ROLLBACK")
                raise DuplicateIntent("intent is not in RESERVED state; refusing state change")
            db.execute("COMMIT")

    def update_intent(self, signal_id: str, intent_json: str) -> None:
        """Append final dispatch evidence without allowing a lock wait to stale it."""
        with self._database(busy_timeout_ms=0) as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute("UPDATE paper_intents SET intent_json=? WHERE signal_id=? AND state='RESERVED'",
                                (intent_json, signal_id))
            if cursor.rowcount != 1:
                db.execute("ROLLBACK")
                raise DuplicateIntent("intent is not RESERVED; refusing evidence update")
            db.execute("COMMIT")

    def get(self, signal_id: str):
        with self._database() as db:
            row = db.execute("SELECT signal_id, request_digest, client_order_id, state, created_at_utc, receipt_json, intent_json, reconciliation_json, receipt_digest FROM paper_intents WHERE signal_id=?", (signal_id,)).fetchone()
        if row is None:
            return None
        return dict(zip(("signal_id", "request_digest", "client_order_id", "state", "created_at_utc", "receipt_json", "intent_json", "reconciliation_json", "receipt_digest"), row))

    def get_by_client_order_id(self, client_order_id: str):
        with self._database() as db:
            row = db.execute("SELECT signal_id, request_digest, client_order_id, state, created_at_utc, receipt_json, intent_json, reconciliation_json, receipt_digest FROM paper_intents WHERE client_order_id=?", (client_order_id,)).fetchone()
        if row is None:
            return None
        return dict(zip(("signal_id", "request_digest", "client_order_id", "state", "created_at_utc", "receipt_json", "intent_json", "reconciliation_json", "receipt_digest"), row))

    def record_reconciliation(self, client_order_id: str, reconciliation_json: str) -> None:
        with self._database() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute("UPDATE paper_intents SET reconciliation_json=? WHERE client_order_id=?", (reconciliation_json, client_order_id))
            if cursor.rowcount != 1:
                db.execute("ROLLBACK")
                raise DuplicateIntent("cannot persist reconciliation for an unknown client order ID")
            db.execute("COMMIT")
