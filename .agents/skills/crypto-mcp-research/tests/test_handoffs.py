import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('handoff_validator', ROOT/'scripts'/'validate_handoffs.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def specialist():
    return {
        'schema_version': 1, 'agent_id': 'market_analyst', 'task_id': 'task-1', 'status': 'PARTIAL',
        'asset_scope': [{'asset_id': 'chain:contract'}],
        'claims': [{'id': 'claim-1', 'statement': 'Observed price', 'status': 'SINGLE_SOURCE', 'observation_ids': ['obs-1']}],
        'observations': [{'id': 'obs-1', 'source': 'provider', 'tool': 'read_price', 'upstream_provenance': None,
                          'asset_id': 'chain:contract', 'metric': 'price', 'value': 1.0, 'unit': 'USD',
                          'observed_at_utc': '2026-10-15T00:00:00Z', 'retrieved_at_utc': '2026-10-15T00:01:00Z',
                          'methodology': None, 'coverage': None}],
        'conflicts': [], 'coverage_gaps': [],
        'tool_calls': [{'source': 'provider', 'tool': 'read_price', 'outcome': 'SUCCESS', 'call_count': 1}]
    }


class TestHandoffs(unittest.TestCase):
    def test_valid_partial_handoff(self):
        self.assertEqual(module.validate_handoff(specialist()), [])

    def test_missing_source_observation_time_is_explicitly_unknown(self):
        doc = specialist(); doc['observations'][0]['observed_at_utc'] = None
        self.assertEqual(module.validate_handoff(doc), [])
        doc['observations'][0]['retrieved_at_utc'] = None
        self.assertTrue(any('retrieved_at_utc' in e for e in module.validate_handoff(doc)))

    def test_source_observation_time_field_cannot_be_omitted(self):
        doc = specialist(); del doc['observations'][0]['observed_at_utc']
        self.assertTrue(any('observed_at_utc: required' in e for e in module.validate_handoff(doc)))

    def test_malformed_handoff(self):
        doc = specialist(); doc['status'] = 'DONE'; doc['observations'][0]['observed_at_utc'] = 'yesterday'
        self.assertTrue(module.validate_handoff(doc))

    def test_duplicate_ids(self):
        doc = specialist(); doc['coverage_gaps'] = [{'id': 'obs-1', 'description': 'x'}]
        self.assertTrue(any('duplicate id' in e for e in module.validate_handoff(doc)))

    def test_missing_observation_reference(self):
        doc = specialist(); doc['claims'][0]['observation_ids'] = ['absent']
        self.assertTrue(any('missing observation reference' in e for e in module.validate_handoff(doc)))

    def test_supported_claim_requires_observation(self):
        doc = specialist(); doc['claims'][0].update(status='SINGLE_SOURCE', observation_ids=[])
        self.assertTrue(any('requires observations' in e for e in module.validate_handoff(doc)))

    def test_review_requires_exact_claim_and_revision_binding(self):
        doc = specialist(); doc['agent_id'] = 'evidence_verifier'
        doc['review'] = {'synthesis_revision': 'rev-1', 'synthesis_sha256': 'a'*64, 'claim_ids': ['claim-1']}
        self.assertEqual(module.validate_handoff(doc), [])
        doc['review']['claim_ids'] = ['missing']
        self.assertTrue(any('missing claim id' in e for e in module.validate_handoff(doc)))

    def test_incomplete_agent_cannot_close_collection(self):
        contract = {'specialist_phase': {'state': 'CLOSED', 'expected_agent_ids': ['market_analyst', 'onchain_analyst']},
                    'reconciliation': {'completed': True}, 'reviews': []}
        self.assertTrue(any('incomplete specialist' in e for e in module.validate_synthesis(contract, [specialist()])))

    def test_review_cannot_start_before_reconciliation(self):
        contract = {'specialist_phase': {'state': 'OPEN', 'expected_agent_ids': ['market_analyst']},
                    'reconciliation': {'completed': False}, 'reviews': [{}]}
        self.assertTrue(any('before collection closes' in e for e in module.validate_synthesis(contract, [specialist()])))

    def test_snapshot_mismatch(self):
        contract = {'specialist_phase': {'state': 'CLOSED', 'expected_agent_ids': ['market_analyst']},
                    'reconciliation': {'completed': True}, 'revision': {'id': 'rev-1', 'snapshot': {'x': 1}, 'sha256': '0'*64}, 'reviews': []}
        self.assertTrue(any('digest mismatch' in e for e in module.validate_synthesis(contract, [specialist()])))

    def test_nonfinite_snapshot_is_rejected_without_crashing(self):
        contract = {'specialist_phase': {'state': 'CLOSED', 'expected_agent_ids': ['market_analyst']},
                    'reconciliation': {'completed': True}, 'revision': {'id': 'rev-1', 'snapshot': {'x': float('nan')}, 'sha256': '0'*64}, 'reviews': []}
        self.assertTrue(any('not canonical-JSON serializable' in e for e in module.validate_synthesis(contract, [specialist()])))

    def test_reviewer_must_match_frozen_snapshot(self):
        snapshot = {'claims': [{'id': 'claim-1'}]}
        digest = module.snapshot_digest(snapshot)
        reviewer = specialist(); reviewer['agent_id'] = 'adversarial_reviewer'
        reviewer['review'] = {'synthesis_revision': 'rev-1', 'synthesis_sha256': digest, 'claim_ids': ['claim-1']}
        contract = {'synthesis_owner': 'primary', 'specialist_phase': {'state': 'CLOSED', 'expected_agent_ids': ['market_analyst']},
                    'reconciliation': {'completed': True}, 'revision': {'id': 'rev-1', 'snapshot': snapshot, 'sha256': digest}, 'reviews': [{}]}
        self.assertEqual(module.validate_synthesis(contract, [specialist(), reviewer]), [])
        reviewer['review']['synthesis_revision'] = 'rev-0'
        self.assertTrue(any('revision mismatch' in e for e in module.validate_synthesis(contract, [specialist(), reviewer])))

    def test_concurrency_bound(self):
        contract = {'specialist_phase': {'state': 'OPEN', 'expected_agent_ids': ['market_analyst', 'liquidity_analyst', 'onchain_analyst', 'catalyst_analyst']},
                    'reconciliation': {'completed': False}, 'reviews': []}
        self.assertTrue(any('concurrency bound' in e for e in module.validate_synthesis(contract, [])))


if __name__ == '__main__': unittest.main()
