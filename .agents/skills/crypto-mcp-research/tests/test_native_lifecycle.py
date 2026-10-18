import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
SPEC = importlib.util.spec_from_file_location(
    "native_lifecycle", ROOT / "scripts" / "native_lifecycle.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def handoff(agent_id="market_analyst", status="PARTIAL"):
    return {
        "schema_version": 1, "agent_id": agent_id,
        "task_id": f"task-{agent_id}", "status": status,
        "asset_scope": [{"asset_id": "base:0x1111111111111111111111111111111111111111"}],
        "claims": [{"id": "C1", "statement": "One candidate is present.",
                    "status": "UNVERIFIED", "observation_ids": []}],
        "observations": [], "conflicts": [], "coverage_gaps": [],
        "tool_calls": [{"tool": "provider tools", "source": "pilot restriction",
                        "outcome": "NOT_CALLED", "call_count": 0}],
    }


class TestNativeLifecycle(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.handoff_path = self.root / "market.json"
        self.snapshot_path = self.root / "snapshot.json"
        self.contract_path = self.root / "synthesis.json"
        self.execution_path = self.root / "execution.json"
        self.permit_path = self.root / "review-permit.json"
        self.snapshot = {"claims": [{"id": "C1", "statement": "One candidate is present.",
                                     "status": "UNVERIFIED", "observation_ids": []}],
                         "observations": []}
        self.handoffs = [handoff()]
        self.write_json(self.snapshot_path, self.snapshot)
        self.write_json(self.handoff_path, self.handoffs[0])
        self.write_json(self.execution_path, {"specialists": [{
            "agent_id": "market_analyst", "native_thread_id": "thread-1",
            "terminal": True, "execution_status": "PARTIAL"}]})
        self.write_json(self.contract_path, self.contract())

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def write_json(path, value):
        Path(path).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def contract(self):
        digest = module.sha256(module.canonical_json_bytes(self.snapshot))
        return {"synthesis_owner": "primary", "specialist_phase": {
                    "state": "CLOSED", "expected_agent_ids": ["market_analyst"]},
                "reconciliation": {"completed": True}, "revision": {
                    "id": "rev-1", "snapshot": copy.deepcopy(self.snapshot), "sha256": digest},
                "reviews": []}

    def gate(self, handoff_paths=None):
        return module.prepare_review_dispatch(
            self.contract_path, self.snapshot_path, self.execution_path,
            handoff_paths or [self.handoff_path], self.permit_path)

    def assert_blocked(self, *args, **kwargs):
        with self.assertRaises((ValueError, OSError)):
            module.prepare_review_dispatch(*args, **kwargs)
        self.assertFalse(self.permit_path.exists())

    def test_persist_handoff_validates_file_and_records_logical_and_byte_digests(self):
        source = self.root / "returned.json"
        self.write_json(source, handoff())
        output = self.root / "persisted.json"
        receipt = module.persist_handoff(json.loads(source.read_text(encoding="utf-8")), output)
        self.assertTrue(output.exists())
        self.assertGreater(output.stat().st_size, 0)
        self.assertEqual(receipt["validator"]["exit_code"], 0)
        self.assertEqual(receipt["logical_sha256"], module.sha256(module.canonical_json_bytes(handoff())))
        self.assertEqual(receipt["stored_bytes_sha256"], module.sha256(output.read_bytes()))
        self.assertNotEqual(receipt["logical_sha256"], receipt["stored_bytes_sha256"])

    def local_response_handoff(self):
        doc = handoff()
        response = {"provider": "dexscreener", "endpoint": "GET /token-pairs/v1/{chainId}/{tokenAddress}",
                    "source_url": "https://api.dexscreener.com/token-pairs/v1/base/0x1111111111111111111111111111111111111111",
                    "retrieved_at_utc": "2026-10-18T05:00:00Z", "data": {"pairs": []}}
        raw = json.dumps(response, separators=(",", ":")).encode("utf-8")
        artifact = self.root / "provider-response.json"
        artifact.write_bytes(raw)
        doc["observations"] = [{
            "id": "local-pairs", "source": "DEX Screener local REST adapter",
            "tool": "LOCAL REST: python .agents/skills/crypto-mcp-research/scripts/dexscreener_public.py token-pairs --chain base --token 0x1111111111111111111111111111111111111111",
            "asset_id": "base:0x1111111111111111111111111111111111111111",
            "metric": "token pairs", "value": {"pair_count": 0}, "unit": "pairs",
            "observed_at_utc": "2026-10-18T05:00:00Z", "retrieved_at_utc": "2026-10-18T05:00:00Z",
            "evidence_artifacts": [{"artifact": artifact.name,
                                    "sha256": hashlib.sha256(raw).hexdigest(),
                                    "source_url": response["source_url"]}],
        }]
        return doc, artifact

    def test_local_rest_observation_requires_a_retained_response_reference(self):
        doc, _ = self.local_response_handoff()
        doc["observations"][0].pop("evidence_artifacts")
        self.write_json(self.handoff_path, doc)
        with self.assertRaisesRegex(ValueError, "lacks a retained raw response reference"):
            self.gate()
        self.assertFalse(self.permit_path.exists())

    def test_case_variant_local_rest_label_cannot_skip_response_reference(self):
        doc, _ = self.local_response_handoff()
        observation = doc["observations"][0]
        observation["tool"] = observation["tool"].replace("LOCAL REST:", "local rest:")
        observation["source"] = "DEX Screener"
        observation.pop("evidence_artifacts")
        self.write_json(self.handoff_path, doc)
        with self.assertRaisesRegex(ValueError, "lacks a retained raw response reference"):
            self.gate()
        self.assertFalse(self.permit_path.exists())

    def test_compact_provider_alias_with_generic_tool_cannot_skip_response_reference(self):
        doc, _ = self.local_response_handoff()
        observation = doc["observations"][0]
        observation["tool"] = "token-pairs"
        observation["source"] = "DexScreener"
        observation.pop("evidence_artifacts")
        self.write_json(self.handoff_path, doc)
        with self.assertRaisesRegex(ValueError, "lacks a retained raw response reference"):
            self.gate()
        self.assertFalse(self.permit_path.exists())

    def test_local_rest_response_reference_is_hash_and_metadata_bound_into_permit(self):
        doc, artifact = self.local_response_handoff()
        self.write_json(self.handoff_path, doc)
        permit = self.gate()
        receipt = permit["handoff_artifacts"][0]["evidence_artifacts"][0]
        self.assertEqual(receipt["observation_id"], "local-pairs")
        self.assertEqual(receipt["sha256"], hashlib.sha256(artifact.read_bytes()).hexdigest())
        self.assertEqual(receipt["reported_value_derivation"], "NOT_AUTOMATICALLY_VERIFIED")
        artifact.write_text('{"changed":true}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "raw evidence changed after reviewer dispatch"):
            module._verify_permitted_evidence_artifacts(permit["handoff_artifacts"])

    def test_local_rest_evidence_rejects_retrieval_mismatch_and_path_escape(self):
        doc, _ = self.local_response_handoff()
        doc["observations"][0]["retrieved_at_utc"] = "2026-10-18T05:01:00Z"
        self.write_json(self.handoff_path, doc)
        with self.assertRaisesRegex(ValueError, "retrieval time differs"):
            self.gate()
        self.permit_path.unlink(missing_ok=True)
        doc["observations"][0]["retrieved_at_utc"] = "2026-10-18T05:00:00Z"
        doc["observations"][0]["evidence_artifacts"][0]["artifact"] = "../provider-response.json"
        self.write_json(self.handoff_path, doc)
        with self.assertRaisesRegex(ValueError, "must be run-relative and contained"):
            self.gate()

    def test_changed_response_and_updated_permit_do_not_match_dispatcher_review_binding(self):
        doc, artifact = self.local_response_handoff()
        self.write_json(self.handoff_path, doc)
        permit = self.gate()
        old_permit_digest = permit["permit_payload_sha256"]
        reviewer_paths, review_entries = [], []
        for role in ("adversarial_reviewer", "evidence_verifier"):
            reviewer = handoff(role, "COMPLETE")
            reviewer["claims"] = copy.deepcopy(self.snapshot["claims"])
            reviewer["review"] = {
                "synthesis_revision": permit["synthesis_revision"],
                "synthesis_sha256": permit["synthesis_sha256"],
                "dispatch_permit_sha256": old_permit_digest,
                "claim_ids": ["C1"],
            }
            path = self.root / f"{role}.json"
            self.write_json(path, reviewer)
            reviewer_paths.append(path)
            review_entries.append({"agent_id": role, "handoff": path.name,
                                   "synthesis_revision": permit["synthesis_revision"],
                                   "synthesis_sha256": permit["synthesis_sha256"],
                                   "dispatch_permit_sha256": old_permit_digest})
        contract = self.contract()
        contract["reviews"] = review_entries
        self.write_json(self.contract_path, contract)

        response = json.loads(artifact.read_text(encoding="utf-8"))
        response["data"] = {"pairs": [{"pairAddress": "changed after dispatch"}]}
        self.write_json(artifact, response)
        new_bytes = artifact.read_bytes()
        permit["handoff_artifacts"][0]["evidence_artifacts"][0]["sha256"] = hashlib.sha256(new_bytes).hexdigest()
        permit = module._seal_permit({key: value for key, value in permit.items()
                                      if key != "permit_payload_sha256"})
        self.write_json(self.permit_path, permit)
        with self.assertRaisesRegex(ValueError, "reviewer dispatch permit digest mismatch"):
            module.validate_review_handoffs(self.permit_path, self.contract_path, reviewer_paths)

    def test_failed_write_does_not_overwrite_existing_artifact(self):
        self.handoff_path.write_text("keep this probe record", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            module.persist_handoff(handoff(), self.handoff_path)
        self.assertEqual(self.handoff_path.read_text(encoding="utf-8"), "keep this probe record")

    def test_io_failure_during_durable_write_is_reported(self):
        output = self.root / "write-failure.json"
        with patch.object(module.os, "fsync", side_effect=OSError("simulated disk failure")):
            with self.assertRaisesRegex(OSError, "simulated disk failure"):
                module.persist_handoff(handoff(), output)

    def test_missing_handoff_blocks_dispatch(self):
        self.handoff_path.unlink()
        self.assert_blocked(self.contract_path, self.snapshot_path, self.execution_path,
                            [self.handoff_path], self.permit_path)

    def test_empty_handoff_blocks_dispatch(self):
        self.handoff_path.write_bytes(b"")
        self.assert_blocked(self.contract_path, self.snapshot_path, self.execution_path,
                            [self.handoff_path], self.permit_path)

    def test_malformed_handoff_blocks_dispatch(self):
        self.handoff_path.write_text("{not json", encoding="utf-8")
        self.assert_blocked(self.contract_path, self.snapshot_path, self.execution_path,
                            [self.handoff_path], self.permit_path)

    def test_valid_json_but_failed_schema_validation_blocks_dispatch(self):
        self.write_json(self.handoff_path, {"agent_id": "market_analyst"})
        self.assert_blocked(self.contract_path, self.snapshot_path, self.execution_path,
                            [self.handoff_path], self.permit_path)

    def test_non_object_handoff_root_blocks_dispatch(self):
        self.write_json(self.handoff_path, [handoff()])
        self.assert_blocked(self.contract_path, self.snapshot_path, self.execution_path,
                            [self.handoff_path], self.permit_path)

    def test_nonterminal_specialist_blocks_dispatch(self):
        self.write_json(self.execution_path, {"specialists": [{
            "agent_id": "market_analyst", "native_thread_id": "thread-1",
            "terminal": False, "execution_status": "PARTIAL"}]})
        self.assert_blocked(self.contract_path, self.snapshot_path, self.execution_path,
                            [self.handoff_path], self.permit_path)

    def test_missing_selected_specialist_blocks_dispatch(self):
        execution = {"specialists": []}
        self.write_json(self.execution_path, execution)
        self.assert_blocked(self.contract_path, self.snapshot_path, self.execution_path,
                            [self.handoff_path], self.permit_path)

    def test_terminal_receipt_without_native_thread_id_blocks_dispatch(self):
        execution = {"specialists": [{
            "agent_id": "market_analyst", "terminal": True,
            "execution_status": "PARTIAL"}]}
        self.write_json(self.execution_path, execution)
        with self.assertRaisesRegex(ValueError, "native thread ID is missing: market_analyst"):
            self.gate()
        self.assertFalse(self.permit_path.exists())

    def test_missing_persisted_handoff_is_an_explicit_pre_review_blocker(self):
        missing = self.root / "market-returned-handoff.json"
        with self.assertRaises(FileNotFoundError):
            module.prepare_review_dispatch(self.contract_path, self.snapshot_path,
                self.execution_path, [missing], self.permit_path)
        self.assertFalse(self.permit_path.exists())

    def test_aero_regression_fixture_preserves_open_and_missing_identity_blocks(self):
        fixture = json.loads((FIXTURES / "aero_review_gate_incomplete.json").read_text(encoding="utf-8"))
        contract = self.contract()
        contract["specialist_phase"] = fixture["specialist_phase"]
        self.write_json(self.contract_path, contract)
        self.write_json(self.execution_path, {"specialists": fixture["specialists"]})
        with self.assertRaisesRegex(ValueError, "collection is not closed"):
            self.gate()

        # Close only the synthetic fixture phase to exercise the next independent gate.
        contract["specialist_phase"]["state"] = "CLOSED"
        self.write_json(self.contract_path, contract)
        with self.assertRaisesRegex(ValueError, "native thread ID is missing: market_analyst"):
            self.gate()
        self.assertFalse(self.permit_path.exists())

    def test_invalid_synthesis_blocks_dispatch(self):
        contract = self.contract(); contract["specialist_phase"]["state"] = "OPEN"
        self.write_json(self.contract_path, contract)
        self.assert_blocked(self.contract_path, self.snapshot_path, self.execution_path,
                            [self.handoff_path], self.permit_path)

    def test_incomplete_reconciliation_blocks_dispatch(self):
        contract = self.contract(); contract["reconciliation"]["completed"] = False
        self.write_json(self.contract_path, contract)
        self.assert_blocked(self.contract_path, self.snapshot_path, self.execution_path,
                            [self.handoff_path], self.permit_path)

    def test_missing_synthesis_digest_blocks_dispatch(self):
        contract = self.contract(); contract["revision"].pop("sha256")
        self.write_json(self.contract_path, contract)
        self.assert_blocked(self.contract_path, self.snapshot_path, self.execution_path,
                            [self.handoff_path], self.permit_path)

    def test_digest_mismatch_blocks_dispatch(self):
        contract = self.contract(); contract["revision"]["sha256"] = "0" * 64
        self.write_json(self.contract_path, contract)
        self.assert_blocked(self.contract_path, self.snapshot_path, self.execution_path,
                            [self.handoff_path], self.permit_path)

    def test_snapshot_contract_mismatch_blocks_dispatch(self):
        contract = self.contract(); contract["revision"]["snapshot"]["claims"] = []
        self.write_json(self.contract_path, contract)
        self.assert_blocked(self.contract_path, self.snapshot_path, self.execution_path,
                            [self.handoff_path], self.permit_path)

    def test_attempted_early_dispatch_never_calls_native_dispatch_callback(self):
        calls = []
        with self.assertRaises(ValueError):
            module.dispatch_reviewers_after_gate(self.contract_path, self.snapshot_path,
                self.execution_path, [self.handoff_path], self.permit_path,
                lambda permit: calls.append(permit))
        self.assertEqual(calls, [])

    def test_valid_lifecycle_creates_review_permit_bound_to_frozen_snapshot(self):
        permit = self.gate()
        self.assertEqual(permit["state"], "REVIEW_DISPATCH_ALLOWED")
        self.assertEqual(permit["synthesis_revision"], "rev-1")
        self.assertEqual(permit["synthesis_sha256"], module.sha256(module.canonical_json_bytes(self.snapshot)))
        self.assertEqual(permit["claim_ids"], ["C1"])
        self.assertEqual(permit["lifecycle_validator"]["exit_code"], 0)
        observed = []
        def dispatch(value):
            observed.append(value)
            return value
        result = module.dispatch_reviewers_after_gate(self.contract_path, self.snapshot_path,
            self.execution_path, [self.handoff_path], self.permit_path, dispatch)
        self.assertEqual(result["synthesis_revision"], "rev-1")
        self.assertEqual(len(observed), 1)

    def test_fabricated_or_stale_permit_does_not_dispatch(self):
        permit = self.gate()
        permit["claim_ids"] = ["FORGED"]
        self.write_json(self.permit_path, permit)
        calls = []
        with self.assertRaisesRegex(ValueError, "permit payload digest"):
            module.dispatch_reviewers_after_gate(self.contract_path, self.snapshot_path,
                self.execution_path, [self.handoff_path], self.permit_path,
                lambda value: calls.append(value))
        self.assertEqual(calls, [])

    def test_non_object_permit_root_does_not_dispatch(self):
        self.write_json(self.permit_path, [])
        calls = []
        with self.assertRaisesRegex(ValueError, "permit root"):
            module.dispatch_reviewers_after_gate(self.contract_path, self.snapshot_path,
                self.execution_path, [self.handoff_path], self.permit_path,
                lambda value: calls.append(value))
        self.assertEqual(calls, [])

    def test_changed_source_after_gate_does_not_dispatch(self):
        self.gate()
        snapshot = copy.deepcopy(self.snapshot)
        snapshot["claims"][0]["statement"] = "changed after gate"
        self.write_json(self.snapshot_path, snapshot)
        calls = []
        with self.assertRaises(ValueError):
            module.dispatch_reviewers_after_gate(self.contract_path, self.snapshot_path,
                self.execution_path, [self.handoff_path], self.permit_path,
                lambda value: calls.append(value))
        self.assertEqual(calls, [])

    def test_malformed_selected_specialists_are_reported_as_blocked(self):
        for expected in ([ ["market_analyst"] ], ["market_analyst", "market_analyst"],
                         ["market_analyst", 4], []):
            contract = self.contract()
            contract["specialist_phase"]["expected_agent_ids"] = expected
            self.write_json(self.contract_path, contract)
            self.assert_blocked(self.contract_path, self.snapshot_path, self.execution_path,
                                [self.handoff_path], self.permit_path)

    def test_recovery_after_blocked_attempt_succeeds(self):
        self.handoff_path.write_text("{bad", encoding="utf-8")
        self.assert_blocked(self.contract_path, self.snapshot_path, self.execution_path,
                            [self.handoff_path], self.permit_path)
        self.handoff_path.unlink()
        self.write_json(self.handoff_path, handoff())
        self.assertEqual(self.gate()["state"], "REVIEW_DISPATCH_ALLOWED")

    def test_both_reviewers_must_bind_exact_frozen_revision_digest_and_claims(self):
        permit = self.gate()
        reviewer_paths = []
        review_entries = []
        for role in ("adversarial_reviewer", "evidence_verifier"):
            path = self.root / f"{role}.json"
            doc = handoff(role, "COMPLETE")
            doc["claims"] = copy.deepcopy(self.snapshot["claims"])
            doc["review"] = {"synthesis_revision": permit["synthesis_revision"],
                             "synthesis_sha256": permit["synthesis_sha256"],
                             "dispatch_permit_sha256": permit["permit_payload_sha256"], "claim_ids": ["C1"]}
            self.write_json(path, doc)
            reviewer_paths.append(path)
            review_entries.append({"agent_id": role, "handoff": path.name,
                                   "synthesis_revision": permit["synthesis_revision"],
                                   "synthesis_sha256": permit["synthesis_sha256"],
                                   "dispatch_permit_sha256": permit["permit_payload_sha256"]})
        contract = self.contract()
        contract["reviews"] = review_entries
        self.write_json(self.contract_path, contract)
        result = module.validate_review_handoffs(self.permit_path, self.contract_path, reviewer_paths)
        self.assertEqual(result["state"], "REVIEW_BINDINGS_VALIDATED")
        reviewer_paths[0].write_text(reviewer_paths[0].read_text(encoding="utf-8").replace(
            permit["synthesis_sha256"], "0" * 64), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            module.validate_review_handoffs(self.permit_path, self.contract_path, reviewer_paths)

    def test_non_object_review_contract_and_permit_roots_block_validation(self):
        self.gate()
        self.write_json(self.contract_path, [])
        with self.assertRaisesRegex(ValueError, "must be JSON objects"):
            module.validate_review_handoffs(self.permit_path, self.contract_path, [])

    def test_non_object_reviewer_artifact_blocks_validation(self):
        permit = self.gate()
        path = self.root / "reviewer.json"
        self.write_json(path, [])
        with self.assertRaisesRegex(ValueError, "reviewer handoff root"):
            module.validate_review_handoffs(self.permit_path, self.contract_path, [path])
        permit["handoff_artifacts"] = [None]
        permit = module._seal_permit({key: value for key, value in permit.items()
                                      if key != "permit_payload_sha256"})
        self.write_json(self.permit_path, permit)
        with self.assertRaisesRegex(ValueError, "bindings are malformed"):
            module.validate_review_handoffs(self.permit_path, self.contract_path, [path])

    def test_non_object_review_binding_blocks_validation(self):
        permit = self.gate()
        paths, reviews = [], []
        for role in ("adversarial_reviewer", "evidence_verifier"):
            doc = handoff(role, "COMPLETE")
            doc["review"] = {"synthesis_revision": permit["synthesis_revision"],
                             "synthesis_sha256": permit["synthesis_sha256"],
                             "dispatch_permit_sha256": permit["permit_payload_sha256"], "claim_ids": ["C1"]}
            path = self.root / f"{role}.json"
            self.write_json(path, doc)
            paths.append(path)
            reviews.append({"agent_id": role, "handoff": path.name,
                            "synthesis_revision": permit["synthesis_revision"],
                            "synthesis_sha256": permit["synthesis_sha256"],
                            "dispatch_permit_sha256": permit["permit_payload_sha256"]})
        doc = json.loads(paths[0].read_text(encoding="utf-8")); doc["review"] = []
        self.write_json(paths[0], doc)
        contract = self.contract(); contract["reviews"] = reviews
        self.write_json(self.contract_path, contract)
        with self.assertRaisesRegex(ValueError, "review binding must be a JSON object"):
            module.validate_review_handoffs(self.permit_path, self.contract_path, paths)

    def test_invalid_declared_review_path_blocks_validation(self):
        permit = self.gate()
        paths, reviews = [], []
        for role in ("adversarial_reviewer", "evidence_verifier"):
            doc = handoff(role, "COMPLETE")
            doc["review"] = {"synthesis_revision": permit["synthesis_revision"],
                             "synthesis_sha256": permit["synthesis_sha256"],
                             "dispatch_permit_sha256": permit["permit_payload_sha256"], "claim_ids": ["C1"]}
            path = self.root / f"{role}.json"
            self.write_json(path, doc)
            paths.append(path)
            reviews.append({"agent_id": role, "handoff": path.name,
                            "synthesis_revision": permit["synthesis_revision"],
                            "synthesis_sha256": permit["synthesis_sha256"],
                            "dispatch_permit_sha256": permit["permit_payload_sha256"]})
        reviews[0]["handoff"] = None
        contract = self.contract(); contract["reviews"] = reviews
        self.write_json(self.contract_path, contract)
        with self.assertRaisesRegex(ValueError, "handoff path is invalid"):
            module.validate_review_handoffs(self.permit_path, self.contract_path, paths)
        self.write_json(self.contract_path, self.contract())
        self.write_json(self.permit_path, [])
        with self.assertRaisesRegex(ValueError, "must be JSON objects"):
            module.validate_review_handoffs(self.permit_path, self.contract_path, [])


if __name__ == "__main__":
    unittest.main()
