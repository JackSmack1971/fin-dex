import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import proposal_workflow
from proposal_workflow import ProposalError, ProposalStore
from export_proposals import export_proposals


def details():
    return {"account_id":"paper-account", "instrument":{"venue":"Example","instrument_id":"BTC-USD","base":"BTC","quote":"USD","instrument_type":"spot"},
            "side":"buy", "quantity":"0.25", "quantity_unit":"BTC", "order_type":"limit", "research_provenance":["research-runs/run/observations.json"]}


class ProposalWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.db = self.root / "proposals.sqlite3"
        self.store = ProposalStore(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def create_validated(self, proposal_id="proposal-1"):
        self.store.create(proposal_id, details())
        return self.store.validate(proposal_id)

    def test_create_validate_export_is_separate_and_immutable(self):
        row = self.store.create("proposal-1", details())
        self.assertEqual((row["state"], row["external_attempt_status"]), ("DRAFT", "NOT_ATTEMPTED"))
        validated = self.store.validate("proposal-1")
        out = self.root / "exports"
        self.assertEqual(export_proposals(self.db, out), 0)
        record = json.loads((out / "proposal-1.json").read_text())
        self.assertEqual(record["details_digest"], validated["details_digest"])
        self.assertIsNone(record["approval_metadata"])
        self.assertEqual(record["external_attempt_status"], "NOT_ATTEMPTED")
        self.assertFalse((self.root / "paper-idempotency.sqlite3").exists())

    def test_missing_account_and_instrument_metadata_fail_validation(self):
        for missing, proposal_id in (("account_id", "p-account"), ("instrument", "p-instrument")):
            item = details()
            item.pop(missing)
            self.store.create(proposal_id, item)
            with self.assertRaises(ProposalError):
                self.store.validate(proposal_id)

    def test_approval_metadata_is_not_runtime_authorization(self):
        row = self.create_validated("p-approval")
        with self.assertRaisesRegex(ProposalError, "digest does not match"):
            self.store.record_approval_metadata("p-approval", "0" * 64, "suggest_order", "user", "approved")
        with self.assertRaisesRegex(ProposalError, "operation is invalid"):
            self.store.record_approval_metadata("p-approval", row["details_digest"], " ", "user", "approved")
        recorded = self.store.record_approval_metadata("p-approval", row["details_digest"], "suggest_order", "user", "explicit caller supplied text")
        approval = json.loads(recorded["approval_metadata_json"])
        self.assertEqual(approval["source"], "CALLER_SUPPLIED")
        self.assertIs(approval["runtime_authorized"], False)
        self.assertEqual(recorded["external_attempt_status"], "NOT_ATTEMPTED")
        with self.assertRaises(ProposalError):
            self.store.record_approval_metadata("p-approval", row["details_digest"], "suggest_order", "user", "second record")

    def test_invalid_caller_supplied_authorization_evidence_cannot_be_forged_as_runtime(self):
        row = self.create_validated("p-forged")
        self.store.record_approval_metadata("p-forged", row["details_digest"], "suggest_order", "user", "text")
        db = sqlite3.connect(self.db)
        try:
            approval = json.loads(db.execute("SELECT approval_metadata_json FROM proposals WHERE proposal_id='p-forged'").fetchone()[0])
            approval["runtime_authorized"] = True
            raw = json.dumps(approval, sort_keys=True, separators=(",", ":"))
            db.execute("UPDATE proposals SET approval_metadata_json=? WHERE proposal_id='p-forged'", (raw,))
            db.commit()
        finally:
            db.close()
        self.assertEqual(export_proposals(self.db, self.root / "forged"), 1)

    def test_malicious_and_noncanonical_proposal_ids_are_rejected(self):
        for value in ("../escape", "..", "a/b", "a\\b", "UPPER", "has.dot", "has space", "é", "-start", "end-", "a" * 65, "con", "nul", "com1"):
            with self.subTest(value=value), self.assertRaises(ProposalError):
                self.store.create(value, details())
        self.assertFalse((self.root.parent / "escape.json").exists())

    def test_missing_database_is_not_created_and_incompatible_database_is_unchanged(self):
        missing = self.root / "missing.sqlite3"
        missing_out = self.root / "missing-output"
        self.assertEqual(export_proposals(missing, missing_out), 1)
        self.assertFalse(missing.exists())
        self.assertFalse(missing_out.exists())

        incompatible = self.root / "incompatible.sqlite3"
        db = sqlite3.connect(incompatible)
        db.execute("CREATE TABLE unrelated (value TEXT)")
        db.commit()
        before = db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        db.close()
        data_before = incompatible.read_bytes()
        self.assertEqual(export_proposals(incompatible, self.root / "incompatible-output"), 1)
        self.assertEqual(incompatible.read_bytes(), data_before)
        db = sqlite3.connect(incompatible)
        try:
            self.assertEqual(db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall(), before)
        finally:
            db.close()

    def test_export_is_read_only_and_does_not_mutate_schema_or_records(self):
        self.create_validated("readonly")
        db = sqlite3.connect(self.db)
        try:
            before_schema = db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
            before_version = db.execute("PRAGMA user_version").fetchone()[0]
            before_row = db.execute("SELECT * FROM proposals").fetchall()
        finally:
            db.close()
        before_bytes = self.db.read_bytes()
        self.assertEqual(export_proposals(self.db, self.root / "readonly-output"), 0)
        self.assertEqual(self.db.read_bytes(), before_bytes)
        db = sqlite3.connect(self.db)
        try:
            self.assertEqual(db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall(), before_schema)
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], before_version)
            self.assertEqual(db.execute("SELECT * FROM proposals").fetchall(), before_row)
        finally:
            db.close()

    def test_conflicting_export_artifacts_and_case_collisions_fail(self):
        self.create_validated("proposal-1")
        out = self.root / "exports"
        self.assertEqual(export_proposals(self.db, out), 0)
        target = out / "proposal-1.json"
        target.write_text("conflict", encoding="utf-8")
        self.assertEqual(export_proposals(self.db, out), 1)
        target.unlink()
        (out / "Proposal-1.json").write_text("other", encoding="utf-8")
        self.assertEqual(export_proposals(self.db, out), 1)

    def test_concurrent_duplicate_proposal_creation_has_one_winner(self):
        def create():
            try:
                ProposalStore(self.db).create("concurrent", details())
                return "created"
            except ProposalError:
                return "duplicate"
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(lambda _: create(), range(8)))
        self.assertEqual(outcomes.count("created"), 1)
        self.assertEqual(outcomes.count("duplicate"), 7)

    def test_changed_proposal_details_are_blocked_by_sqlite_immutability_guard(self):
        row = self.store.create("immutable", details())
        db = sqlite3.connect(self.db)
        try:
            changed = {**details(), "quantity":"0.5"}
            with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                db.execute("UPDATE proposals SET details_json=?,details_digest=? WHERE proposal_id=?", (proposal_workflow.canonical(changed).decode(), proposal_workflow.digest(changed), row["proposal_id"]))
            with self.assertRaisesRegex(sqlite3.IntegrityError, "cannot be deleted or reused"):
                db.execute("DELETE FROM proposals WHERE proposal_id=?", (row["proposal_id"],))
            with self.assertRaisesRegex(sqlite3.IntegrityError, "cannot be reused"):
                db.execute("INSERT OR REPLACE INTO proposals SELECT proposal_id, ?, ?, state, external_attempt_status, created_at_utc, updated_at_utc, approval_metadata_json, operation_json, response_json FROM proposals WHERE proposal_id=?", (proposal_workflow.canonical(changed).decode(), proposal_workflow.digest(changed), row["proposal_id"]))
        finally:
            db.close()
        self.assertEqual(self.store.get("immutable")["details_digest"], row["details_digest"])
        with self.assertRaisesRegex(ProposalError, "duplicate proposal_id"):
            self.store.create("immutable", {**details(), "quantity":"0.5"})
        changed = self.store.create("immutable-v2", {**details(), "quantity":"0.5"})
        self.assertNotEqual(row["details_digest"], changed["details_digest"])

    def test_invalid_lifecycle_transitions_and_persisted_states_fail_closed(self):
        self.create_validated("lifecycle")
        with self.assertRaisesRegex(ProposalError, "invalid lifecycle transition"):
            self.store.validate("lifecycle")
        with self.assertRaises(ProposalError):
            self.store.record_approval_metadata("lifecycle", "0" * 64, "suggest_order", "user", "evidence")

        db = sqlite3.connect(self.db)
        try:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "invalid proposal lifecycle"):
                db.execute("UPDATE proposals SET state='REVIEW_LINK_CREATED',external_attempt_status='SUCCEEDED' WHERE proposal_id='lifecycle'")
            db.execute("DROP TRIGGER proposals_valid_lifecycle")
            db.execute("UPDATE proposals SET state='REVIEW_LINK_CREATED',external_attempt_status='SUCCEEDED' WHERE proposal_id='lifecycle'")
            db.execute(proposal_workflow._LIFECYCLE_TRIGGER_SQL)
            db.commit()
        finally:
            db.close()
        self.assertEqual(export_proposals(self.db, self.root / "unsupported"), 1)
        self.assertFalse((self.root / "unsupported" / "lifecycle.json").exists())

    def test_invalid_timestamps_and_approval_digest_relationship_rejected_by_export(self):
        row = self.create_validated("integrity")
        self.store.record_approval_metadata("integrity", row["details_digest"], "suggest_order", "user", "evidence")
        db = sqlite3.connect(self.db)
        try:
            db.execute("UPDATE proposals SET updated_at_utc='not-a-time' WHERE proposal_id='integrity'")
            db.commit()
        finally:
            db.close()
        self.assertEqual(export_proposals(self.db, self.root / "invalid-timestamp"), 1)

        other = self.root / "bad-approval.sqlite3"
        store = ProposalStore(other)
        good = store.create("bad-approval", details())
        good = store.validate("bad-approval")
        store.record_approval_metadata("bad-approval", good["details_digest"], "suggest_order", "user", "evidence")
        db = sqlite3.connect(other)
        try:
            approval = json.loads(db.execute("SELECT approval_metadata_json FROM proposals").fetchone()[0])
            approval["details_digest"] = "0" * 64
            db.execute("UPDATE proposals SET approval_metadata_json=?", (json.dumps(approval, sort_keys=True, separators=(",", ":")),))
            db.commit()
        finally:
            db.close()
        self.assertEqual(export_proposals(other, self.root / "bad-approval-out"), 1)

    def test_no_external_invocation_capability_or_mutable_enable_flag(self):
        self.assertFalse(hasattr(self.store, "reserve_dispatch"))
        self.assertFalse(hasattr(self.store, "submit"))
        self.assertFalse(hasattr(self.store, "create_review_link"))
        self.assertFalse(hasattr(proposal_workflow, "EXTERNAL_REVIEW_LINK_ENABLED"))
        for state in proposal_workflow.STATES - {"DRAFT", "VALIDATED"}:
            self.assertIn(state, proposal_workflow.STATES)

    def test_export_rejects_database_missing_immutability_guards(self):
        self.create_validated("unguarded")
        db = sqlite3.connect(self.db)
        try:
            db.execute("DROP TRIGGER proposals_immutable_details")
            db.commit()
        finally:
            db.close()
        self.assertEqual(export_proposals(self.db, self.root / "unguarded-output"), 1)
        self.assertFalse((self.root / "unguarded-output" / "unguarded.json").exists())


if __name__ == "__main__":
    unittest.main()
