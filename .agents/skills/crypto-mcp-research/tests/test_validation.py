import importlib.util
import copy
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('evidence_validator', ROOT/'scripts'/'validate_evidence.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class TestEvidence(unittest.TestCase):
    def setUp(self):
        self.obs = dict(provider='coingecko', tool='actual_tool', asset_id='asset', metric='price', value=1, unit='USD', observed_at_utc='2026-10-15T00:00:00Z', retrieved_at_utc='2026-10-15T00:01:00Z')
        self.doc = dict(as_of_utc='2026-10-15T00:01:00Z', claims=[dict(id='x', statement='price', status='SINGLE_SOURCE', observations=[self.obs])])
    def test_valid(self): self.assertEqual(module.validate(self.doc), [])
    def test_bad_timestamp(self):
        self.obs['observed_at_utc']='2026-10-15T01:00:00+01:00'
        self.assertTrue(module.validate(self.doc))
    def test_no_false_corroboration(self):
        self.doc['claims'][0]['status']='CORROBORATED'
        self.assertTrue(module.validate(self.doc))
    def test_missing_value(self):
        self.obs.pop('value')
        self.assertTrue(module.validate(self.doc))
    def test_missing_claim(self):
        self.doc['claims']=[]
        self.assertTrue(module.validate(self.doc))

    def test_corroboration_requires_compatible_observations(self):
        second = copy.deepcopy(self.obs)
        second.update(provider='coinpaprika', tool='other_tool', chain_id='base:8453',
                      upstream_source='independent-origin', measurement_method='spot ticker')
        self.obs.update(chain_id='base:8453', upstream_source='provider-origin', measurement_method='spot ticker')
        self.doc['claims'][0].update(status='CORROBORATED', claim_type='TOKEN_CONTRACT_IDENTITY', observations=[self.obs, second])
        self.assertEqual(module.validate(self.doc), [])
        self.assertTrue(module.validate_with_warnings(self.doc)[1])  # truth remains manually qualified

    def test_same_contract_without_chain_and_independent_origins_stays_single_source(self):
        second = copy.deepcopy(self.obs)
        second.update(provider='coinpaprika', tool='other_tool')
        self.obs['asset_id'] = second['asset_id'] = '0x940181a94A35A4569E4529A3CDfB74e38FD98631'
        self.doc['claims'][0].update(status='SINGLE_SOURCE', claim_type='TOKEN_CONTRACT_IDENTITY', observations=[self.obs, second])
        self.assertEqual(module.validate(self.doc), [])
        self.doc['claims'][0]['status'] = 'CORROBORATED'
        errors = module.validate(self.doc)
        self.assertTrue(any('chain-qualified' in e for e in errors))
        self.assertTrue(any('upstream_source' in e for e in errors))

    def test_non_token_market_claim_does_not_require_chain_qualification(self):
        second = copy.deepcopy(self.obs)
        self.obs.update(provider='coingecko', upstream_source='origin-a', measurement_method='aggregate spot')
        second.update(provider='coinpaprika', tool='other_tool', upstream_source='origin-b', measurement_method='aggregate spot')
        self.doc['claims'][0].update(status='CORROBORATED', observations=[self.obs, second])
        self.assertEqual(module.validate(self.doc), [])

    def test_non_finite_numbers_rejected(self):
        for value in (float('nan'), float('inf'), -float('inf')):
            with self.subTest(value=value):
                self.obs['value'] = value
                self.assertTrue(any('finite' in error for error in module.validate(self.doc)))

    def test_impossible_time_order_rejected(self):
        self.obs['observed_at_utc'] = '2026-10-15T00:02:00Z'
        self.assertTrue(any('after retrieved_at_utc' in error for error in module.validate(self.doc)))

    def test_retrieval_after_as_of_rejected(self):
        self.obs['retrieved_at_utc'] = '2026-10-15T00:02:00Z'
        self.assertTrue(any('after as_of_utc' in error for error in module.validate(self.doc)))

    def test_duplicate_claim_ids_rejected(self):
        self.doc['claims'].append(copy.deepcopy(self.doc['claims'][0]))
        self.assertTrue(any('duplicate' in error for error in module.validate(self.doc)))

    def test_duplicate_observation_identity_rejected(self):
        self.doc['claims'][0]['observations'].append(copy.deepcopy(self.obs))
        self.assertTrue(any('duplicate observation identity' in error for error in module.validate(self.doc)))

    def test_corroboration_identity_metric_unit_and_window_mismatch_rejected(self):
        second = copy.deepcopy(self.obs)
        second['provider'] = 'coinpaprika'
        self.doc['claims'][0].update(status='CORROBORATED', observations=[self.obs, second])
        for field, value in (('asset_id', 'other'), ('metric', 'volume'), ('unit', 'BTC')):
            second[field] = value
            self.assertTrue(any('incompatible' in error for error in module.validate(self.doc)))
            second[field] = self.obs[field]
        self.obs.update(window_start_utc='2026-10-14T00:00:00Z', window_end_utc='2026-10-15T00:00:00Z')
        second.update(window_start_utc='2026-10-13T00:00:00Z', window_end_utc='2026-10-15T00:00:00Z')
        self.assertTrue(any('incompatible windows' in error for error in module.validate(self.doc)))

    def test_malformed_records_and_utc_rejected(self):
        self.doc['claims'][0]['observations'] = [None]
        self.assertTrue(any('expected object' in error for error in module.validate(self.doc)))
        self.doc['claims'][0]['observations'] = [self.obs]
        self.obs['observed_at_utc'] = '2026-02-30T00:00:00Z'
        self.assertTrue(module.validate(self.doc))

    def test_legacy_optional_provenance_absence_is_warning_only(self):
        second = copy.deepcopy(self.obs)
        second.update(provider='coinpaprika', tool='other_tool', chain_id='base:8453',
                      upstream_source='independent-origin', measurement_method='spot ticker')
        self.obs.update(chain_id='base:8453', upstream_source='provider-origin', measurement_method='spot ticker')
        self.doc['claims'][0].update(status='CORROBORATED', observations=[self.obs, second])
        self.assertEqual(module.validate(self.doc), [])
        warnings = module.validate_with_warnings(self.doc)[1]
        self.assertTrue(any('unverifiable' in warning for warning in warnings))

if __name__ == '__main__': unittest.main()
