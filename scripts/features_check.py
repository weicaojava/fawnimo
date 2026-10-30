"""Exercise register boundary failures without touching private application data."""
import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('features',ROOT/'scripts/features.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
data=json.loads((ROOT/'capabilities/features.json').read_text('utf8'))
module.validate(data)
results=[dict(name='valid_register',passed=True)]


def reject(name, mutate):
    """Require an invalid claim to fail validation without writing its content."""
    candidate=copy.deepcopy(data)
    mutate(candidate)
    try:
        module.validate(candidate)
    except ValueError:
        results.append(dict(name=name,passed=True))
        return
    raise AssertionError(f'Invalid register accepted: {name}')


def find(candidate,fid):
    """Resolve a feature by stable identity for independent corruption cases."""
    return next(item for item in candidate['features'] if item['id']==fid)


def planned(candidate):
    """Choose an actually planned feature so state transitions do not invalidate governance corruption fixtures."""
    return next(item for item in candidate['features'] if item['implementation']=='planned')



reject('duplicate_feature_id',lambda d:d['features'].append(copy.deepcopy(d['features'][0])))
reject('unknown_requirement',lambda d:find(d,'F-002.01').update(requirements=['missing']))
reject('dependency_cycle',lambda d:find(d,'F-001.01').update(depends_on=['F-001.02']))
reject('unknown_dependency',lambda d:find(d,'F-001.01').update(depends_on=['F-999.99']))
reject('missing_code',lambda d:find(d,'F-002.01').update(code=['backend/not-present.py']))
reject('path_traversal',lambda d:find(d,'F-002.01').update(code=['../private.db']))
reject('absolute_private_path',lambda d:find(d,'F-002.01').update(code=['C:/private.db']))
reject('unscoped_evidence',lambda d:find(d,'F-002.01')['validation'].update(evidence=['EV-REGISTER-CHECK']))
reject('model_qualification_without_live_evidence',lambda d:find(d,'F-007.03')['validation'].update(model='verified'))
reject('software_qualification_without_current_evidence',lambda d:find(d,'F-007.03')['validation'].update(software='verified',evidence=['EV-LOCAL-20261029']))
reject('planned_feature_cannot_be_accepted',lambda d:planned(d)['validation'].update(software='verified'))
reject('planned_feature_cannot_be_deployed',lambda d:planned(d)['deployment'].update(status='local_only'))
reject('public_deployment_without_evidence',lambda d:find(d,'F-002.01')['deployment'].update(status='public_verified',url='https://studio.example.org'))


def fake_deployment(candidate,url):
    """Keep a deployment receipt in scope while independently testing URL boundaries."""
    item=find(candidate,'F-002.01')
    candidate['evidence'].append(dict(id='FAKE-DEPLOYMENT',kind='deployment',visibility='private_external',location='test',scope='test-only',features=[item['id']]))
    item['validation']['evidence'].append('FAKE-DEPLOYMENT')
    item['deployment'].update(status='public_verified',url=url)


for name,url in [('loopback','https://127.0.0.1'),('private_ip','https://192.168.1.2'),('localhost','https://localhost'),('local_domain','https://studio.local'),('embedded_credentials','https://secret:secret@studio.example.org')]:
    reject('public_deployment_rejects_'+name,lambda d,url=url:fake_deployment(d,url))


def fake_software(candidate):
    """A register receipt cannot qualify arbitrary application behavior even if mis-scoped."""
    feature=find(candidate,'F-007.03')
    candidate['evidence'][2]['features'].append(feature['id'])
    feature['validation'].update(software='verified',evidence=['EV-REGISTER-CHECK'])


reject('register_receipt_cannot_qualify_application',fake_software)

cmd=[sys.executable,str(ROOT/'scripts/features.py'),'--check']
subprocess.run(cmd,cwd=ROOT,check=True)
results.append(dict(name='generated_views_current',passed=True))
target=ROOT/'site/content/docs/reference/feature-catalog.md'
original=target.read_bytes()
try:
    target.write_bytes(original+b'\n<!-- intentionally stale for validation -->\n')
    stale=subprocess.run(cmd,cwd=ROOT,text=True,capture_output=True)
    assert stale.returncode==1 and 'stale' in stale.stderr
    results.append(dict(name='generated_drift_rejected',passed=True))
finally:
    target.write_bytes(original)
subprocess.run(cmd,cwd=ROOT,check=True)
results.append(dict(name='restored_views_current',passed=True))
record=dict(date='2026-10-29',scope='feature register integrity and generated document consistency only',status='passed',checks=results,
    passed=len(results),failed=0,groups=len(data['groups']),features=len(data['features']),
    source_sha256=hashlib.sha256((ROOT/'capabilities/features.json').read_bytes()).hexdigest(),
    generator_sha256=hashlib.sha256((ROOT/'scripts/features.py').read_bytes()).hexdigest(),
    command='python scripts/features_check.py',
    application_qualified=False,model_qualified=False,deployment_qualified=False,external_model_calls=0,private_database_access=False,
    independent_review='pending')
(ROOT/'capabilities/register-validation.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
print(json.dumps(dict(passed=len(results),failed=0,scope=record['scope'])))

