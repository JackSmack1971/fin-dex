from pathlib import Path
import json, subprocess, sys, hashlib
root=Path('.tmp/aero-integration-test')
validator=Path('.agents/skills/crypto-mcp-research/scripts/validate_handoffs.py')
contract=json.loads((root/'synthesis_contract.json').read_text(encoding='utf-8-sig'))
expected=contract['revision']['sha256']
results=[]
for name,content in [('empty.json',''),('malformed.json','{ not valid json')]:
 p=root/name
 p.write_text(content,encoding='utf-8')
 proc=subprocess.run([sys.executable,str(validator),str(p)],capture_output=True,text=True)
 passed=proc.returncode != 0 and 'FAIL:' in proc.stdout
 results.append({'fixture':name,'validator_exit':proc.returncode,'expected_rejection':passed,'output':proc.stdout.strip()})
 assert passed, f'{name} was not rejected'
# Reconstructed original specialist handoff is parseable, schema-valid, and retains all observation IDs.
valid_proc=subprocess.run([sys.executable,str(validator),str(root/'catalyst_analyst.json')],capture_output=True,text=True)
assert valid_proc.returncode == 0 and 'PASS: 0 handoff/lifecycle error(s)' in valid_proc.stdout
cat=json.loads((root/'catalyst_analyst.json').read_text(encoding='utf-8-sig'))
assert [o['id'] for o in cat['observations']]==['gdelt-query-1','gdelt-query-2']
# Reviewer bindings point to the canonical frozen snapshot, not an earlier document.
raw=json.dumps(contract['revision']['snapshot'],sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')
digest=hashlib.sha256(raw).hexdigest()
assert digest==expected
for r in contract['reviews']:
 assert r['synthesis_revision']==contract['revision']['id'] and r['synthesis_sha256']==digest
report={'test':'artifact_persistence_regression','scope':'temporary local fixtures only; no repository source/configuration changed','cases':results,'reconstructed_handoff_valid':True,'reconstructed_observation_ids':[o['id'] for o in cat['observations']],'frozen_digest':digest,'review_bindings_match':True,'result':'PASS'}
(root/'artifact_persistence_regression_result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
