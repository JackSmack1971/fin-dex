import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[4]
SKILL = ROOT / '.agents' / 'skills' / 'crypto-mcp-research'
spec = importlib.util.spec_from_file_location(
    'capability_validator', SKILL / 'scripts' / 'validate_capabilities.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class TestCapabilities(unittest.TestCase):
    def setUp(self):
        self.config = (ROOT / '.codex' / 'config.toml').read_text(encoding='utf-8')
        self.inventory = json.loads(
            (ROOT / '.codex' / 'provider-capabilities.json').read_text(encoding='utf-8'))
        self.temp = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp.name) / 'config.toml'
        self.inventory_path = Path(self.temp.name) / 'inventory.json'
        self.write()

    def tearDown(self):
        self.temp.cleanup()

    def write(self):
        self.config_path.write_text(self.config, encoding='utf-8')
        self.inventory_path.write_text(json.dumps(self.inventory), encoding='utf-8')

    def errors(self):
        return module.validate(self.config_path, self.inventory_path)

    def provider(self, name):
        return next(p for p in self.inventory['providers'] if p['id'] == name)

    def tool(self, provider, name):
        return next(t for t in self.provider(provider)['tools'] if t['name'] == name)

    def test_configured_allowlists_match_verified_read_only_methods(self):
        self.assertEqual(self.errors(), [])
        self.assertIn('getTickersById', self.provider('coinpaprika')['automatic_research_allowlist'])
        self.assertNotIn('execute', self.provider('coingecko')['automatic_research_allowlist'])

    def test_stateful_wallet_unknown_and_feedback_methods_are_prohibited(self):
        self.assertEqual(self.tool('coingecko', 'execute')['effect'], 'UNKNOWN')
        self.assertFalse(self.tool('coingecko', 'execute')['automatic_research'])
        self.assertFalse(self.tool('coinpaprika', 'submitFeedback')['automatic_research'])
        self.assertFalse(self.tool('dexpaprika', 'submitFeedback')['automatic_research'])
        self.assertFalse(self.tool('hive', 'invoke_stateful_endpoint')['automatic_research'])
        self.assertFalse(self.tool('hive', 'get_wallet_portfolio')['automatic_research'])
        self.provider('hive')['automatic_research_allowlist'].append('get_wallet_portfolio')
        self.config = self.config.replace(
            '"validate_task_result"]', '"validate_task_result", "get_wallet_portfolio"]')
        self.write()
        errors = self.errors()
        self.assertTrue(any('only verified, permitted READ_ONLY' in e for e in errors))
        self.assertTrue(any('eligibility contradicts provider allowlist' in e for e in errors))

    def test_unknown_discovered_tool_cannot_be_allowed(self):
        self.tool('coinpaprika', 'getTickersById')['effect'] = 'UNCLASSIFIED'
        self.write()
        self.assertTrue(any('invalid or missing effect' in e for e in self.errors()))

    def test_guessed_tool_identity_is_not_allowlisted(self):
        provider = self.provider('gdelt')
        provider['automatic_research_allowlist'].append('gdelt_search_magic')
        self.config = self.config.replace(
            '"gdelt_search_tv"]', '"gdelt_search_tv", "gdelt_search_magic"]')
        self.write()
        self.assertTrue(any('allowlisted tool was not discovered' in e for e in self.errors()))

    def test_malformed_effect_and_exact_identity_are_rejected_without_crashing(self):
        tool = self.tool('gdelt', 'gdelt_search_articles')
        tool['effect'] = ['READ_ONLY']
        tool['identity'] = 'guessed_tool_name'
        self.write()
        errors = self.errors()
        self.assertTrue(any('invalid or missing effect' in e for e in errors))
        self.assertTrue(any('exact discovered tool identity' in e for e in errors))

    def test_missing_provider_is_rejected(self):
        self.config = re.sub(r'\[mcp_servers\.gdelt\][\s\S]*?(?=\n\[|\Z)', '', self.config)
        self.write()
        self.assertTrue(any('provider set' in e for e in self.errors()))
        self.assertTrue(any('gdelt: configured provider missing' in e for e in self.errors()))

    def test_malformed_capability_metadata_and_contradictions_are_rejected(self):
        self.tool('gdelt', 'gdelt_search_articles')['effect'] = 'STATEFUL'
        self.write()
        errors = self.errors()
        self.assertTrue(any('prohibited or unknown-effect tool marked' in e for e in errors))
        self.assertTrue(any('only verified, permitted READ_ONLY' in e for e in errors))

    def test_current_discovered_status_does_not_claim_response(self):
        for provider in self.inventory['providers']:
            self.assertEqual(provider['status'], 'DISCOVERED')
            self.assertEqual([x['status'] for x in provider['status_history']],
                             ['CONFIGURED', 'DISCOVERED'])
            self.assertIsNone(provider['responded_evidence'])
            self.assertIsNone(provider['qualification_evidence'])

    def test_status_transitions_require_observation_evidence(self):
        self.assertIn('tool_metadata', module.transition_status('CONFIGURED', 'DISCOVERED'))
        self.assertIsNone(module.transition_status(
            'CONFIGURED', 'DISCOVERED', {'tool_metadata': {
                'observed_at_utc': '2026-10-16T11:03:05Z',
                'tool_identity': 'mcp__gdelt__gdelt_search_articles',
            }}))
        self.assertIn('successful_call', module.transition_status('DISCOVERED', 'RESPONDED'))
        self.assertIsNone(module.transition_status(
            'DISCOVERED', 'RESPONDED', {'successful_call': {
                'outcome': 'success', 'observed_at_utc': '2026-10-16T11:03:05Z',
                'tool_identity': 'mcp__gdelt__gdelt_search_articles',
            }}))
        self.assertIn('successful_call', module.transition_status(
            'RESPONDED', 'QUALIFIED', {'qualification_record': {'passed': True}}))
        self.assertIsNone(module.transition_status('RESPONDED', 'QUALIFIED', {
            'successful_call': {'outcome': 'success'},
            'qualification_record': {'passed': True},
        }))
        self.assertIn('invalid transition', module.transition_status('CONFIGURED', 'QUALIFIED', {
            'successful_call': True, 'qualification_record': True}))

    def test_missing_success_evidence_cannot_be_declared_responded_or_qualified(self):
        gdelt = self.provider('gdelt')
        gdelt['status'] = 'RESPONDED'
        self.write()
        self.assertTrue(any('RESPONDED requires successful-call evidence' in e for e in self.errors()))
        gdelt['status'] = 'QUALIFIED'
        self.write()
        errors = self.errors()
        self.assertTrue(any('QUALIFIED requires successful-call evidence' in e for e in errors))
        self.assertTrue(any('QUALIFIED requires qualification evidence' in e for e in errors))


if __name__ == '__main__':
    unittest.main()
