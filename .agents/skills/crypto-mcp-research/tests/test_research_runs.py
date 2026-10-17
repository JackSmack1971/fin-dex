import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('run_validator', ROOT/'scripts'/'validate_research_run.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class TestResearchRuns(unittest.TestCase):
    def test_retained_defillama_ethereum_history_replays_offline(self):
        manifest = ROOT.parents[2]/'research-runs'/'2026-10-17-defillama-ethereum-tvl-replay'/'manifest.json'
        self.assertEqual(module.validate(manifest, replay=True), [])

    def test_retained_defillama_ethereum_stablecoin_supply_replays_offline(self):
        manifest = ROOT.parents[2]/'research-runs'/'2026-10-17-defillama-ethereum-stablecoin-supply-replay'/'manifest.json'
        self.assertEqual(module.validate(manifest, replay=True), [])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root/'observations.json').write_text('{"observations":[{"id":"a","value":10,"unit":"USD","observed_at_utc":"2026-10-14T00:00:00Z"},{"id":"b","value":12,"unit":"USD","observed_at_utc":"2026-10-15T00:00:00Z"}]}', encoding='utf-8')
        digest = hashlib.sha256((self.root/'observations.json').read_bytes()).hexdigest()
        self.doc = {
            'schema_version': 1, 'run_id': 'run-1', 'task_specification': 'measure change',
            'timestamps': {'started_at_utc':'2026-10-15T00:00:00Z','completed_at_utc':'2026-10-15T00:05:00Z'},
            'asset_identity': {'canonical_id':'chain:contract','identity_status':'RESOLVED','chain':'chain','contract':'contract','instrument':None},
            'research_horizon': {'start_utc':'2026-10-14T00:00:00Z','end_utc':'2026-10-15T00:00:00Z','decision_horizon':'24h'},
            'repository': {'status':'UNAVAILABLE','revision':None,'reason':'Git metadata unavailable'},
            'methodology_version':'1.0','configuration_references':[], 'specialists':[],
            'lifecycle':{'collection_state':'CLOSED','reconciliation_completed':True}, 'evidence_ledgers':[],
            'synthesis':None,'reviews':[],'verdict':'NO_VERIFIED_CONCLUSION',
            'artifacts':[{'path':'observations.json','sha256':digest,'kind':'observations'}],
            'calculations':[{'id':'return','formula':'simple_return.v1','formula_version':'simple_return.v1','status':'REPLAYABLE','inputs':['a','b'],'input_timestamps':{'a':'2026-10-14T00:00:00Z','b':'2026-10-15T00:00:00Z'},'source_observations':{'a':'observations.json','b':'observations.json'},'unit':'percent','sampling_window':'matched endpoints','rounding':'python_round_half_even.v1','rounding_digits':15,'missing_data':'error','expected_value':20.0,'tolerance':1e-12}]
        }
        self.path = self.root/'manifest.json'
        self.save()

    def tearDown(self): self.tmp.cleanup()
    def save(self): self.path.write_text(json.dumps(self.doc), encoding='utf-8')

    def test_canonical_json_and_digest_are_stable(self):
        self.assertEqual(module.canonical_json({'b':2,'a':'é'}), module.canonical_json({'a':'é','b':2}))
        self.assertEqual(module.digest_bytes(module.canonical_json({'x':1})), hashlib.sha256(b'{"x":1}').hexdigest())

    def test_valid_run_and_formula_replay(self): self.assertEqual(module.validate(self.path, replay=True), [])

    def test_nan_rejected_in_canonical_serialization(self):
        with self.assertRaises(ValueError): module.canonical_json({'bad': float('nan')})

    def test_physical_and_logical_digest_are_distinct(self):
        raw = (self.root/'observations.json').read_bytes()
        logical = module.canonical_json(json.loads(raw))
        self.assertNotEqual(module.digest_bytes(raw), module.digest_bytes(logical))

    def test_declared_logical_digest_detects_json_tampering(self):
        raw = (self.root/'observations.json').read_bytes()
        self.doc['artifacts'][0]['logical_sha256'] = module.digest_bytes(module.canonical_json(json.loads(raw)))
        self.save()
        self.assertFalse(module.validate(self.path))
        (self.root/'observations.json').write_text('{"observations":[]}', encoding='utf-8')
        self.assertTrue(any('artifact digest mismatch' in e or 'logical digest mismatch' in e for e in module.validate(self.path)))

    def test_timestamp_and_snapshot_bindings(self):
        self.doc['calculations'][0]['input_timestamps']['a']='2026-10-13T00:00:00Z'; self.save()
        self.assertTrue(any('timestamp mismatch' in e for e in module.validate(self.path, replay=True)))

    def test_replay_rejects_invalid_timestamp_mapping_and_rounding(self):
        calc=self.doc['calculations'][0]
        calc['input_timestamps']={'a':'2026-10-14T00:00:00+00:00','b':'2026-10-15T00:00:00Z'}
        calc['rounding']='no rounding'
        self.save(); errors=module.validate(self.path, replay=True)
        self.assertTrue(any('timestamp mismatch' in e for e in errors))
        self.assertTrue(any('unsupported rounding' in e for e in errors))

    def test_replay_rejects_incompatible_input_units(self):
        (self.root/'observations.json').write_text('{"observations":[{"id":"a","value":10,"unit":"USD","observed_at_utc":"2026-10-14T00:00:00Z"},{"id":"b","value":12,"unit":"BTC","observed_at_utc":"2026-10-15T00:00:00Z"}]}', encoding='utf-8')
        self.doc['artifacts'][0]['sha256']=hashlib.sha256((self.root/'observations.json').read_bytes()).hexdigest(); self.save()
        self.assertTrue(any('input units are incompatible' in e for e in module.validate(self.path, replay=True)))

    def test_replay_enforces_semantic_output_unit(self):
        self.doc['calculations'][0]['unit']='USD'; self.save()
        self.assertTrue(any('output unit must be percent' in e for e in module.validate(self.path, replay=True)))

    def test_extrema_rejects_nonfinite_expected_values(self):
        self.doc['calculations']=[{'id':'range','formula':'sampled_extrema.v1','formula_version':'sampled_extrema.v1','status':'REPLAYABLE','inputs':['a','b'],'input_timestamps':{'a':'2026-10-14T00:00:00Z','b':'2026-10-15T00:00:00Z'},'source_observations':{'a':'observations.json','b':'observations.json'},'unit':'USD','sampling_window':'two samples','rounding':'python_round_half_even.v1','rounding_digits':2,'missing_data':'error','expected_value':{'minimum':float('nan'),'maximum':12.0},'tolerance':0}]
        self.save()
        self.assertTrue(any('expected extrema must contain finite numeric' in e for e in module.validate(self.path, replay=True)))

    def test_missing_observation_timestamp_and_unit_fail_replay(self):
        (self.root/'observations.json').write_text('{"observations":[{"id":"a","value":10},{"id":"b","value":12}]}', encoding='utf-8')
        self.doc['artifacts'][0]['sha256']=hashlib.sha256((self.root/'observations.json').read_bytes()).hexdigest(); self.save()
        errors=module.validate(self.path, replay=True)
        self.assertTrue(any('missing unit' in e for e in errors))
        self.assertTrue(any('missing observation timestamp' in e for e in errors))

    def test_not_replayable_reports_exact_missing_inputs_as_blocked(self):
        self.doc['calculations']=[{'id':'historic','formula':'sampled_extrema.v1','inputs':[],'unit':'USD','sampling_window':'22 daily points','rounding':'none','missing_data':'NOT_REPLAYABLE','status':'NOT_REPLAYABLE','missing_inputs':['all 22 dated AERO USD observations']}]
        self.save(); result=module.validate(self.path, replay=True)
        self.assertTrue(any('BLOCKED:' in e and 'all 22 dated AERO' in e for e in result))

    def test_supported_formula_replay_calculators(self):
        self.assertAlmostEqual(module.formula('benchmark_relative_return.v1',[100,110,200,220]),0)
        self.assertAlmostEqual(module.formula('percent_change.v1',[100,110]),10)
        self.assertAlmostEqual(module.formula('bid_ask_spread.v1',[99,101]),2)
        self.assertEqual(module.formula('sampled_extrema.v1',[3,1,4]),{'minimum':1,'maximum':4})
        with self.assertRaisesRegex(ValueError, 'benchmark gross return is zero'):
            module.formula('benchmark_relative_return.v1',[100,110,200,0])

    def test_tampered_and_missing_artifacts(self):
        (self.root/'observations.json').write_text('tampered', encoding='utf-8')
        self.assertTrue(any('digest mismatch' in e for e in module.validate(self.path)))
        (self.root/'observations.json').unlink()
        self.assertTrue(any('artifact missing' in e for e in module.validate(self.path)))

    def test_bad_calculation_reference_and_missing_inputs(self):
        self.doc['calculations'][0]['inputs']=['absent','b']; self.save()
        self.assertTrue(any('missing calculation inputs' in e for e in module.validate(self.path)))

    def test_missing_git_metadata_is_explicit(self):
        self.doc['repository']={'status':'UNAVAILABLE','revision':None,'reason':'No .git directory'}; self.save()
        self.assertEqual(module.validate(self.path, replay=True), [])

    def test_partial_run_is_valid_but_explicitly_in_progress(self):
        self.doc['specialists']=[]; self.doc['lifecycle']={'collection_state':'OPEN','reconciliation_completed':False}; self.doc['verdict']='IN_PROGRESS'; self.doc['calculations']=[]; self.save()
        self.assertEqual(module.validate(self.path), [])

    def test_incorrect_claim_observation_reference_in_handoff(self):
        handoff={'schema_version':1,'agent_id':'market_analyst','task_id':'t','status':'PARTIAL','asset_scope':[],
                 'claims':[{'id':'c','statement':'claim','status':'UNVERIFIED','observation_ids':['absent']}],
                 'observations':[],'conflicts':[],'coverage_gaps':[],'tool_calls':[]}
        raw=json.dumps(handoff); (self.root/'handoff.json').write_text(raw,encoding='utf-8')
        self.doc['artifacts'].append({'path':'handoff.json','sha256':hashlib.sha256(raw.encode()).hexdigest(),'kind':'handoff'})
        self.doc['specialists']=[{'agent_id':'market_analyst','task_id':'t','execution_status':'PARTIAL','handoff':'handoff.json'}]
        self.save()
        self.assertTrue(any('missing observation reference' in e for e in module.validate(self.path)))

    def test_review_requires_closed_reconciled_lifecycle(self):
        self.doc['reviews']=[{'agent_id':'evidence_verifier','handoff':'x','synthesis_revision':'r','synthesis_sha256':'0'*64}]
        self.doc['lifecycle']={'collection_state':'OPEN','reconciliation_completed':False}; self.save()
        self.assertTrue(any('before collection closure' in e for e in module.validate(self.path)))

    def test_snapshot_digest_and_review_binding(self):
        self.doc['synthesis']={'artifact':'observations.json','revision':'r1','sha256':'0'*64}
        self.doc['reviews']=[{'agent_id':'evidence_verifier','handoff':'absent.json','synthesis_revision':'r0','synthesis_sha256':'0'*64}]
        self.save(); errors=module.validate(self.path)
        self.assertTrue(any('synthesis canonical snapshot digest mismatch' in e for e in errors))
        self.assertTrue(any('synthesis binding mismatch' in e for e in errors))

    def test_invalid_temporal_order(self):
        self.doc['timestamps']['completed_at_utc']='2026-10-14T00:00:00Z'; self.save()
        self.assertTrue(any('precedes' in e for e in module.validate(self.path)))

    def test_zero_baseline_fails_replay(self):
        (self.root/'observations.json').write_text('{"observations":[{"id":"a","value":0},{"id":"b","value":12}]}', encoding='utf-8')
        self.doc['artifacts'][0]['sha256']=hashlib.sha256((self.root/'observations.json').read_bytes()).hexdigest(); self.save()
        self.assertTrue(any('zero baseline' in e for e in module.validate(self.path, replay=True)))

    def test_specialist_limit_and_partial_status_are_recorded(self):
        self.doc['specialists']=[{'agent_id':'market_analyst','task_id':'t','execution_status':'PARTIAL','handoff':'missing.json'}]
        self.doc['verdict']='IN_PROGRESS'; self.save()
        errors=module.validate(self.path)
        self.assertTrue(any('missing artifact reference' in e for e in errors))
        self.doc['specialists'] *= 4; self.save()
        self.assertTrue(any('limit of three' in e for e in module.validate(self.path)))

    def test_blocked_failed_runs_remain_explicit_records(self):
        for status in ('BLOCKED','FAILED'):
            self.doc['specialists']=[{'agent_id':'market_analyst','task_id':'t','execution_status':status,'handoff':'handoff.json'}]
            (self.root/'handoff.json').write_text(json.dumps({'agent_id':'market_analyst','task_id':'t','status':status}), encoding='utf-8')
            self.doc['artifacts'].append({'path':'handoff.json','sha256':hashlib.sha256((self.root/'handoff.json').read_bytes()).hexdigest(),'kind':'handoff'})
            self.save(); self.assertFalse(any('handoff identity/status mismatch' in e for e in module.validate(self.path)))
            self.doc['artifacts'].pop()

    def test_legacy_handoff_stays_compatible(self):
        handoff_spec=importlib.util.spec_from_file_location('handoff_validator', ROOT/'scripts'/'validate_handoffs.py')
        handoff=importlib.util.module_from_spec(handoff_spec); handoff_spec.loader.exec_module(handoff)
        sample={'schema_version':1,'agent_id':'market_analyst','task_id':'t','status':'PARTIAL','asset_scope':[], 'claims':[], 'observations':[], 'conflicts':[], 'coverage_gaps':[], 'tool_calls':[]}
        self.assertEqual(handoff.validate_handoff(sample), [])


if __name__ == '__main__': unittest.main()
