import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

life = load("native_lifecycle")
handoffs = load("validate_handoffs")
evidence = load("validate_evidence")

class LifecycleV2(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.lifecycle_id = "life-1"
        self.snapshot = {"schema_version": 2, "lifecycle_id": self.lifecycle_id,
                         "artifact_id": "snapshot-1", "task_id": "task-1", "claims": []}
        snap_digest = hashlib.sha256(life.canonical_json_bytes(self.snapshot)).hexdigest()
        self.docs = [self.snapshot,
            {"schema_version": 2, "lifecycle_id": self.lifecycle_id, "artifact_id": "handoff-1",
             "task_id": "task-1", "agent_id": "market_analyst", "status": "COMPLETE",
             "asset_scope": [], "claims": [], "observations": [], "conflicts": [],
             "coverage_gaps": [], "tool_calls": []},
            {"schema_version": 2, "lifecycle_id": self.lifecycle_id, "artifact_id": "eligible-1",
             "task_id": "task-1", "result": "REVIEW_ELIGIBLE", "snapshot_sha256": snap_digest}]
        self.paths = []
        for i, doc in enumerate(self.docs):
            path = self.root / f"{i}.json"; path.write_bytes(life.canonical_json_bytes(doc)); self.paths.append(path)
        self.lifecycle = {"schema_version": 2, "lifecycle_id": self.lifecycle_id, "task_id": "task-1",
            "expected_artifacts": [{"artifact_id": x["artifact_id"], "role": x.get("agent_id"), "status": x.get("status")}
                                   for x in self.docs],
            "snapshot_artifact_id": "snapshot-1", "snapshot_sha256": snap_digest,
            "eligibility_artifact_id": "eligible-1"}

    def tearDown(self): self.tmp.cleanup()
    def test_valid_collection_and_snapshot_binding(self):
        self.assertEqual(life.validate_lifecycle_v2(self.lifecycle, self.paths), [])
    def test_duplicate_artifact_id_across_paths_rejected(self):
        doc = copy.deepcopy(self.docs[1]); doc["artifact_id"] = "snapshot-1"
        self.paths[1].write_bytes(life.canonical_json_bytes(doc))
        self.assertTrue(any("duplicate artifact_id" in e for e in life.validate_lifecycle_v2(self.lifecycle, self.paths)))
    def test_retained_history_collision_rejected(self):
        self.assertTrue(any("replay" in e for e in life.validate_lifecycle_v2(self.lifecycle, self.paths,
            [{"artifact_id": "handoff-1", "lifecycle_id": "old-life"}])))
    def test_missing_and_unexpected_receipts_rejected(self):
        self.assertTrue(any("missing expected artifact receipt" in e for e in life.validate_lifecycle_v2(self.lifecycle, self.paths[:2])))
        extra = copy.deepcopy(self.docs[1]); extra["artifact_id"] = "extra"
        path = self.root / "extra.json"; path.write_bytes(life.canonical_json_bytes(extra))
        self.assertTrue(any("unexpected artifact receipts" in e for e in life.validate_lifecycle_v2(self.lifecycle, self.paths + [path])))
    def test_task_lifecycle_role_status_and_snapshot_mismatch_rejected(self):
        for field, value in (("task_id", "wrong"), ("lifecycle_id", "wrong"), ("agent_id", "onchain_analyst"), ("status", "PARTIAL")):
            doc = copy.deepcopy(self.docs[1]); doc[field] = value; self.paths[1].write_bytes(life.canonical_json_bytes(doc))
            self.assertTrue(life.validate_lifecycle_v2(self.lifecycle, self.paths), field)
            self.paths[1].write_bytes(life.canonical_json_bytes(self.docs[1]))
        bad = copy.deepcopy(self.docs[2]); bad["snapshot_sha256"] = "0" * 64
        self.paths[2].write_bytes(life.canonical_json_bytes(bad))
        self.assertTrue(any("snapshot binding" in e for e in life.validate_lifecycle_v2(self.lifecycle, self.paths)))
    def test_v2_handoff_requires_correlation_and_timestamp_validation_stays_strict(self):
        self.assertEqual(handoffs.validate_handoff(self.docs[1]), [])
        doc = copy.deepcopy(self.docs[1]); doc.pop("artifact_id")
        self.assertTrue(handoffs.validate_handoff(doc))
        ledger = {"schema_version": 2, "lifecycle_id": "life", "artifact_id": "e", "task_id": "t",
                  "as_of_utc": "2026-10-18T00:00:00Z", "claims": [{"id": "c", "statement": "x", "status": "UNVERIFIED", "observations": []}]}
        self.assertEqual(evidence.validate(ledger), [])
        ledger["as_of_utc"] = "bad"
        self.assertTrue(evidence.validate(ledger))
    def test_identical_v2_retry_is_read_only_and_conflict_is_preserved(self):
        doc = copy.deepcopy(self.docs[1]); path = self.root / "persisted.json"
        first = life.persist_handoff(doc, path); original = path.read_bytes(); second = life.persist_handoff(doc, path)
        self.assertEqual(original, path.read_bytes()); self.assertEqual(first["stored_bytes_sha256"], second["stored_bytes_sha256"])
        path.write_bytes(b"{")
        with self.assertRaises(ValueError): life.persist_handoff(doc, path)
        self.assertEqual(path.read_bytes(), b"{")
    def test_persistence_rejects_id_reuse_at_another_path(self):
        doc = copy.deepcopy(self.docs[1]); path = self.root / "new.json"
        with self.assertRaisesRegex(ValueError, "another path"):
            life.persist_handoff(doc, path, [{"artifact_id": doc["artifact_id"], "path": str(self.root / "old.json")}])
        self.assertFalse(path.exists())
    def test_malformed_nonfinite_v2_artifact_fails_without_exception(self):
        path = self.paths[1]
        path.write_text('{"x":NaN}', encoding="utf-8")
        self.assertTrue(life.validate_lifecycle_v2(self.lifecycle, self.paths))

if __name__ == "__main__": unittest.main()
