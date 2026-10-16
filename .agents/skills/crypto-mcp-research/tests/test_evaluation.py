import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('workflow_evaluator', ROOT/'scripts'/'evaluate_workflow.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
EVALUATION = ROOT/'evaluation'


class TestEvaluationCorpus(unittest.TestCase):
    def setUp(self):
        self.corpus = module.load_json(EVALUATION/'corpus-v1.json')
        self.protocol = module.load_json(EVALUATION/'protocol-v1.json')

    def test_frozen_corpus_exact_counts_and_case_contract(self):
        self.assertEqual(module.validate_corpus(self.corpus), [])
        self.assertEqual(self.corpus['case_count'], 65)
        self.assertEqual(self.corpus['fixture_count'], len(self.corpus['fixtures']))
        for case in self.corpus['cases']:
            self.assertTrue(case['case_id'])
            self.assertTrue(case['inputs'])
            self.assertTrue(case['expected_behavior'])
            self.assertTrue(case['evidence_requirements'])
            self.assertTrue(case['scoring_rules'])
            self.assertTrue(case['failure_conditions'])
            self.assertIn(case['fixture_id'], self.corpus['fixtures'])

    def test_checked_in_digests_bind_corpus_fixtures_and_protocol(self):
        digests = module.load_json(EVALUATION/'corpus-digests-v1.json')
        protocol = module.load_json(EVALUATION/'protocol-v1.json')
        fixture_artifact = module.load_json(EVALUATION/'fixtures-v1.json')
        self.assertEqual(digests['corpus_sha256'], module.canonical_digest(self.corpus))
        self.assertEqual(digests['fixtures_sha256'], module.canonical_digest(self.corpus['fixtures']))
        self.assertEqual(digests['protocol_sha256'], module.canonical_digest(protocol))
        self.assertEqual(fixture_artifact['fixtures'], self.corpus['fixtures'])

    def test_invalid_counts_duplicate_ids_and_missing_fixture_fail(self):
        bad = copy.deepcopy(self.corpus)
        bad['cases'][0]['case_id'] = bad['cases'][1]['case_id']
        bad['cases'][0]['fixture_id'] = 'absent'
        bad['case_count'] -= 1
        errors = module.validate_corpus(bad)
        self.assertTrue(any('duplicate case_id' in error for error in errors))
        self.assertTrue(any('missing fixture' in error for error in errors))
        self.assertTrue(any('case_count' in error for error in errors))

    def test_positive_and_negative_category_counts_are_fixed(self):
        self.assertEqual(module.validate_protocol(self.protocol), [])
        bad = copy.deepcopy(self.protocol)
        bad['routing_repetitions'] = 2
        self.assertTrue(any('routing_repetitions must be 3' in e for e in module.validate_protocol(bad)))


class TestEvaluationScoring(unittest.TestCase):
    def setUp(self):
        self.corpus = module.load_json(EVALUATION/'corpus-v1.json')
        self.protocol_artifact = module.load_json(EVALUATION/'protocol-v1.json')
        self.protocol = {
            'routing_repetitions': self.protocol_artifact['routing_repetitions'],
            'scenario_repetitions_per_condition': self.protocol_artifact['scenario_repetitions_per_condition'],
            'baseline_condition': self.protocol_artifact['baseline_condition'],
            'treatment_condition': self.protocol_artifact['treatment_condition'],
            'rubric_dimensions': self.protocol_artifact['rubric_dimensions'],
            'relative_error_reduction': self.protocol_artifact['relative_error_reduction'],
        }
        self.manifest = {
            'schema_version': 1, 'evaluation_id': 'test-evaluation',
            'evaluation_mode': 'FIXTURE_REPLAY', 'evidence_state': 'FIXTURE_REPLAY',
            'corpus_sha256': module.canonical_digest(self.corpus),
            'fixtures_sha256': module.canonical_digest(self.corpus['fixtures']),
            'protocol_sha256': module.canonical_digest(self.protocol_artifact),
            'protocol': copy.deepcopy(self.protocol),
            'baseline_provenance': {'description': 'Synthetic retained test trial provenance; not an agent run.'},
            'trials': [],
        }

    def _trial(self, case, condition, replicate, activated=False, error=0):
        result = {'skill_activated': activated} if case['category'] in {'routing_positive', 'routing_negative', 'neighbor'} else {
            'unsupported_consequential_claims': error,
            'unsupported_claim_basis': ['synthetic-claim-1'] if error else [],
            'asset_identity_errors': 0, 'timestamp_unit_mismatches': 0,
            'source_independence_errors': 0, 'premature_completion': False,
            'tool_calls': 'UNMEASURED', 'latency_ms': None,
            'unauthorized_external_operations': 0,
            'human_judgment': {'rater_ids': ['synthetic-rater-a', 'synthetic-rater-b'], 'scores': {
                dimension: {'rater_1': 'PASS', 'rater_2': 'PASS', 'adjudicated': 'PASS', 'adjudication_note': ''}
                for dimension in ('access_fee_honesty', 'conflict_handling', 'completion_calibration')},
                'task_success': {'rater_1': 'PASS', 'rater_2': 'PASS', 'adjudicated': 'PASS', 'adjudication_note': ''}},
        }
        if case['category'] == 'provider_failure':
            result['recovered'] = True
        scenario = case['category'] in {'research', 'provider_failure'}
        setup = {'fin_dex_skill_available': condition == 'FIN_DEX',
                 'multi_agent_review_enabled': condition == 'FIN_DEX' and scenario,
                 'selected_specialists': ['market_analyst'] if condition == 'FIN_DEX' and scenario else [],
                 'reviewer_ids': ['adversarial_reviewer', 'evidence_verifier'] if condition == 'FIN_DEX' and scenario else []}
        context = {'input_sha256': module.canonical_digest(case['inputs']),
                   'fixture_sha256': module.canonical_digest(self.corpus['fixtures'][case['fixture_id']]),
                   'model_runtime_id': 'synthetic-model-runtime-v1',
                   'base_instructions_sha256': '1'*64, 'tool_permissions_sha256': '2'*64}
        return {'case_id': case['case_id'], 'condition': condition, 'replicate': replicate,
                'fixture_id': case['fixture_id'], 'evidence_state': 'FIXTURE_REPLAY',
                'status': 'COMPLETE', 'provenance': 'retained_synthetic_trial',
                'paired_context': context, 'condition_setup': setup, 'result': result}

    def _complete_manifest_trials(self):
        for case in self.corpus['cases']:
            repeats = 3 if case['category'] in {'routing_positive', 'routing_negative', 'neighbor'} else 5
            for condition in ('BASELINE', 'FIN_DEX'):
                for replicate in range(1, repeats+1):
                    if case['category'] == 'routing_positive':
                        activated = True
                    else:
                        activated = False
                    error = 1 if condition == 'BASELINE' and case['category'] in {'research', 'provider_failure'} else 0
                    self.manifest['trials'].append(self._trial(case, condition, replicate, activated, error))

    def test_complete_paired_fixture_replay_scores_without_live_claim(self):
        self._complete_manifest_trials()
        score = module.score(self.corpus, self.manifest)
        self.assertEqual(score['errors'], [])
        self.assertFalse(score['live_agent_claim_permitted'])
        self.assertEqual(score['conditions']['FIN_DEX']['routing']['precision'], 1.0)
        self.assertEqual(score['conditions']['FIN_DEX']['routing']['recall'], 1.0)
        self.assertEqual(score['conditions']['FIN_DEX']['routing']['neighbor_false_activation_rate'], 0.0)
        self.assertEqual(score['relative_error_reduction']['paired_trial_denominator'], 75)
        self.assertEqual(score['relative_error_reduction']['value'], 1.0)
        self.assertEqual(score['conditions']['FIN_DEX']['task_success_rate'], 1.0)
        self.assertEqual(score['conditions']['BASELINE']['unsupported_consequential_claim_trial_rate'], 1.0)
        self.assertEqual(score['conditions']['FIN_DEX']['tool_call_measurement_status'], 'UNMEASURED')
        self.assertEqual(score['conditions']['FIN_DEX']['completion_latency_status'], 'UNMEASURED')

    def test_prepare_manifest_contains_full_unvalidated_paired_matrix(self):
        manifest = module.prepare_manifest(self.corpus, self.protocol_artifact)
        self.assertEqual(len(manifest['trials']), 450)
        self.assertEqual(manifest['protocol_sha256'], module.canonical_digest(self.protocol_artifact))
        self.assertTrue(all(t['status'] == 'UNVALIDATED' for t in manifest['trials']))
        pairs = {(t['case_id'], t['replicate']): t['fixture_id'] for t in manifest['trials'] if t['condition'] == 'BASELINE'}
        treated = {(t['case_id'], t['replicate']): t['fixture_id'] for t in manifest['trials'] if t['condition'] == 'FIN_DEX'}
        self.assertEqual(pairs, treated)

    def test_empty_result_manifest_reports_every_required_trial_unvalidated(self):
        manifest = module.load_json(EVALUATION/'result-manifest-template-v1.json')
        result = module.score(self.corpus, manifest)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['required_trial_slots'], 450)
        self.assertEqual(result['completed_required_trial_slots'], 0)
        self.assertEqual(result['incomplete_or_unvalidated_trials'], 450)
        self.assertFalse(result['live_agent_claim_permitted'])

    def test_incomplete_trial_does_not_shrink_required_denominator(self):
        self._complete_manifest_trials()
        self.manifest['trials'] = [t for t in self.manifest['trials'] if not (
            t['case_id'] == 'RES-01' and t['condition'] == 'BASELINE' and t['replicate'] == 5)]
        score = module.score(self.corpus, self.manifest)
        self.assertEqual(score['errors'], [])
        self.assertEqual(score['relative_error_reduction']['required_paired_trial_denominator'], 75)
        self.assertEqual(score['relative_error_reduction']['paired_trial_denominator'], 70)
        self.assertEqual(score['relative_error_reduction']['status'], 'UNMEASURED')
        self.assertEqual(score['conditions']['BASELINE']['routing']['status'], 'MEASURED')

    def test_missing_routing_repeat_keeps_metrics_unvalidated_and_null(self):
        self._complete_manifest_trials()
        self.manifest['trials'] = [t for t in self.manifest['trials'] if not (
            t['case_id'] == 'POS-01' and t['condition'] == 'FIN_DEX' and t['replicate'] == 3)]
        score = module.score(self.corpus, self.manifest)
        routing = score['conditions']['FIN_DEX']['routing']
        self.assertEqual(routing['status'], 'UNVALIDATED')
        self.assertIsNone(routing['recall'])

    def test_routing_uses_two_of_three_majority(self):
        self._complete_manifest_trials()
        positive = [t for t in self.manifest['trials'] if t['case_id'] == 'POS-01' and t['condition'] == 'FIN_DEX']
        positive[0]['result']['skill_activated'] = False
        result = module.score(self.corpus, self.manifest)
        self.assertEqual(result['conditions']['FIN_DEX']['routing']['recall'], 1.0)
        positive[1]['result']['skill_activated'] = False
        result = module.score(self.corpus, self.manifest)
        self.assertEqual(result['conditions']['FIN_DEX']['routing']['recall'], 0.95)

    def test_fixture_mismatch_and_corpus_digest_change_fail(self):
        trial = self._trial(self.corpus['cases'][0], 'BASELINE', 1, activated=True)
        trial['fixture_id'] = 'different-fixture'
        self.manifest['trials'] = [trial]
        errors = module.score(self.corpus, self.manifest)['errors']
        self.assertTrue(any('fixture mismatch' in error for error in errors))
        self.manifest['trials'] = []
        self.manifest['corpus_sha256'] = '0'*64
        self.assertTrue(any('corpus_sha256' in error for error in module.score(self.corpus, self.manifest)['errors']))

    def test_paired_context_mismatch_invalidates_comparison(self):
        self._complete_manifest_trials()
        treated = next(t for t in self.manifest['trials'] if t['case_id'] == 'RES-01' and t['condition'] == 'FIN_DEX' and t['replicate'] == 1)
        treated['paired_context']['base_instructions_sha256'] = '3'*64
        result = module.score(self.corpus, self.manifest)
        self.assertEqual(result['errors'], ['paired context mismatch: RES-01 replicate 1'])

    def test_routing_replicates_cannot_exceed_frozen_three(self):
        trial = self._trial(self.corpus['cases'][0], 'BASELINE', 4, activated=True)
        self.manifest['trials'] = [trial]
        self.assertTrue(any('exactly three replicates' in e for e in module.score(self.corpus, self.manifest)['errors']))

    def test_relative_reduction_unmeasured_for_zero_baseline_or_unmeasured_errors(self):
        self._complete_manifest_trials()
        for trial in self.manifest['trials']:
            if trial['condition'] == 'BASELINE' and 'unsupported_consequential_claims' in trial['result']:
                trial['result']['unsupported_consequential_claims'] = 0
                trial['result']['unsupported_claim_basis'] = []
        result = module.score(self.corpus, self.manifest)
        self.assertEqual(result['relative_error_reduction']['status'], 'UNMEASURED')
        self.assertIsNone(result['relative_error_reduction']['value'])
        for trial in self.manifest['trials']:
            if trial['condition'] == 'BASELINE' and 'unsupported_consequential_claims' in trial['result']:
                trial['result']['unsupported_consequential_claims'] = 'UNMEASURED'
        result = module.score(self.corpus, self.manifest)
        self.assertEqual(result['relative_error_reduction']['status'], 'UNMEASURED')
        self.assertFalse(result['relative_error_reduction']['all_error_dimensions_measured'])

    def test_fixture_replay_cannot_claim_native_execution_and_incomplete_requires_no_result(self):
        trial = self._trial(self.corpus['cases'][0], 'BASELINE', 1, activated=True)
        trial['provenance'] = 'recorded_native_agent_execution'
        self.manifest['trials'] = [trial]
        self.assertTrue(any('fixture/static evidence cannot claim native execution' in e for e in module.score(self.corpus, self.manifest)['errors']))
        trial['status'] = 'INCOMPLETE'
        trial.pop('result')
        trial.pop('provenance')
        self.assertEqual(module.score(self.corpus, self.manifest)['errors'], [])

    def test_human_judgment_requires_independent_raters_and_adjudication(self):
        case = next(c for c in self.corpus['cases'] if c['category'] == 'research')
        trial = self._trial(case, 'BASELINE', 1)
        trial['result']['human_judgment']['rater_ids'] = ['same-rater', 'same-rater']
        record = trial['result']['human_judgment']['scores']['conflict_handling']
        record.update(rater_1='PASS', rater_2='FAIL', adjudicated='FAIL', adjudication_note='Resolved against case rubric.')
        self.manifest['trials'] = [trial]
        self.assertTrue(any('two distinct raters' in e for e in module.score(self.corpus, self.manifest)['errors']))
        trial['result']['human_judgment']['rater_ids'] = ['rater-a', 'rater-b']
        record['adjudication_note'] = ''
        self.assertTrue(any('requires documented adjudication' in e for e in module.score(self.corpus, self.manifest)['errors']))


if __name__ == '__main__':
    unittest.main()
