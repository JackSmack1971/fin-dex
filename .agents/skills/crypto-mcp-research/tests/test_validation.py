import importlib.util
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

if __name__ == '__main__': unittest.main()
