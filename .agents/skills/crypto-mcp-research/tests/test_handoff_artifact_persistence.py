import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('artifact_validator', ROOT/'scripts'/'validate_handoff_artifact.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def valid_handoff():
    return {'schema_version': 1, 'agent_id': 'catalyst_analyst', 'task_id': 't', 'status': 'PARTIAL',
            'asset_scope': [{'asset_id': 'project:AERO'}], 'claims': [], 'observations': [], 'conflicts': [],
            'coverage_gaps': [], 'tool_calls': []}


class TestHandoffArtifactPersistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'handoff.json'

    def tearDown(self): self.tmp.cleanup()

    def test_missing_empty_malformed_and_nonconforming_artifacts_fail_persistence(self):
        self.assertTrue(any('persistence failed' in e for e in module.validate_artifact(self.path)))
        self.path.write_bytes(b'')
        self.assertTrue(any('empty' in e for e in module.validate_artifact(self.path)))
        self.path.write_text('{bad json', encoding='utf-8')
        self.assertTrue(any('malformed JSON' in e for e in module.validate_artifact(self.path)))
        self.path.write_text(json.dumps({'agent_id': 'catalyst_analyst'}), encoding='utf-8')
        self.assertTrue(any('nonconforming' in e for e in module.validate_artifact(self.path)))

    def test_valid_persisted_artifact_is_distinct_from_lifecycle_acceptance(self):
        self.path.write_text(json.dumps(valid_handoff()), encoding='utf-8')
        self.assertEqual(module.validate_artifact(self.path), [])


if __name__ == '__main__': unittest.main()
