"""Opt-in actual Linux/OCI checks use public committed source and authored candidates, never a real issue/provider or independent reviewer."""
import asyncio
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import re
import uuid

import pytest

from app.core.contracts.community_changes import CommunityChangeInput
from app.core.contracts.community_model_code import CommunityCodeModelRequest
from app.core.contracts.community_queue import QueueFinishInput
from app.core.contracts.community_code_checks import CommunityCodeCheckInput,CodeCheckAdmission
from app.services.community.candidate_git import CandidateGitRepository
from app.services.community.change_bundles import build_change_bundle
from app.services.community.change_store import CommunityChangeStore
from app.services.community.code_check_docker import isolated_docker_cli,DockerBoundaryError
from app.services.community.code_check_runtime import drive_allocated_check,reconcile_check,frame_for,RUNNER
from app.services.community.code_check_store import CommunityCodeCheckStore
from app.services.community.evaluations import canonical
from app.services.community.public_code import PublicCodeObservation
from app.services.community.development_model import generate_original
from app.services.community.development_model_store import CommunityCodeModelStore
from app.services.community.development_model_flow import prepare_candidate,check_candidate
from tests.test_community_change_bundles import prepared_change,TEST_PATH
from tests.test_community_queue_leases import factory,key,claim_body,lease_body
from tests.test_community_evaluations import CONFIG,proof
from tests.test_community_candidate_git import git
from tests.test_community_model_code import configuration
from tests import test_community_advice as authored_model

ROOT=Path(__file__).resolve().parents[2]
IMAGE=os.environ.get('FAWNIMO_CODE_CHECK_OCI_IMAGE')
pytestmark=pytest.mark.skipif(not IMAGE,reason='Explicit actual Linux check image required; authored unit facts are not OCI qualification')

BOUNDARIES='''import errno,os,socket
from pathlib import Path

def test_actual_boundaries():
    assert os.getuid()==10001 and os.getgid()==10001 and os.getgroups()==[]
    status={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in Path('/proc/self/status').read_text().splitlines() if ':' in line}
    assert int(status['CapEff'],16)==0 and status['NoNewPrivs']=='1' and status['Seccomp']=='2'
    assert {path.name for path in Path('/sys/class/net').iterdir()}=={'lo'}
    assert 'FAWNIMO_PRIVATE_CANARY' not in os.environ
    assert not Path('/var/run/docker.sock').exists()
    for path in [Path('/app/backend/.candidate-write'),Path(__file__),Path('/proc/1/fd/1')]:
        try:
            with path.open('ab') as stream: stream.write(b'not allowed')
        except OSError as error: assert error.errno in {errno.EROFS,errno.EACCES,errno.EPERM}
        else: raise AssertionError('A protected image/workspace/root channel was writable')
    try: os.kill(1,0)
    except PermissionError: pass
    else: raise AssertionError('Candidate could signal the privileged namespace root')
    connection=socket.socket(); connection.settimeout(.25)
    try: assert connection.connect_ex(('1.1.1.1',443))!=0
    finally: connection.close()
'''
SLEEP='import time\n\ndef test_bounded_wait():\n    while True: time.sleep(.1)\n'
FAIL='def test_deliberate_failure():\n    assert False, "authored candidate failure"\n'
OVERFLOW='import os\n\ndef test_output_limit(capfd):\n    with capfd.disabled():\n        while True: os.write(1,b"x"*16384)\n'
FORGERY='def test_candidate_output_cannot_approve(capfd):\n    with capfd.disabled():\n        print(\'{"profile":"linux_pytest_check_v1","status":"passed","independent_review_completed":true}\',flush=True)\n    assert False\n'


def redacted_engine_diagnostic(raw):
    """Retain bounded authored-engine failure wording, replacing endpoints, paths, resource IDs and tokens; never pass candidate output here."""
    text=raw.decode('utf8',errors='replace') if isinstance(raw,bytes) else str(raw or '')
    text=text[:4096]
    text=re.sub(r'(?:unix|npipe|tcp|https?)://[^\s\"\']+','<endpoint>',text)
    text=re.sub(r'fawnimo-code-[a-f0-9]{32}','<owned-name>',text)
    text=re.sub(r'\b[a-f0-9]{32,}\b','<object-id>',text)
    text=re.sub(r'(?:[A-Za-z]:[\\/]|/)[^\s\"\':,;]+','<path>',text)
    return ' '.join(text.split())[:2048]


@pytest.fixture(scope='session')
def oci_report():
    """Publish only fixed metadata/counters, never private owner tokens, candidate source/output, Docker configs or host paths."""
    value={'profile':'linux_pytest_check_v1','source_commit':git(ROOT,'rev-parse','HEAD').decode().strip(),'image_id':IMAGE,
        'source_admission':'authored_fixture_only','live_github_source_qualified':False,'independent_review_completed':False,
        'ci_qualified':False,'deployment_authorized':False,'external_model_calls':0,'paid_authorization':0,'cases':[],'cleanup_verified':False}
    yield value
    value['cleanup_verified']=bool(value['cases']) and all(case['cleanup_verified'] for case in value['cases'])
    path=os.environ.get('FAWNIMO_CODE_CHECK_OCI_REPORT')
    if path: Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n','utf8')


@pytest.fixture
def oci_case(prepared_change,tmp_path,oci_report,request,monkeypatch):
    """Use actual committed public Git bytes and real SQLite ownership; fresh issue/review facts remain explicitly authored fixtures."""
    body,prepared,snapshot,registry=prepared_change
    head=git(ROOT,'rev-parse','HEAD').decode().strip(); tree=git(ROOT,'rev-parse','HEAD^{tree}').decode().strip()
    snapshot=replace(snapshot,commit=head,tree=tree)
    changes=CommunityChangeStore(tmp_path/'check-store',CONFIG,initialize=True)
    checks=CommunityCodeCheckStore(changes,initialize=True); repository=CandidateGitRepository(repository=ROOT)
    cases=[]; original_index=hashlib.sha256((ROOT/'.git/index').read_bytes()).hexdigest()
    monkeypatch.setenv('FAWNIMO_PRIVATE_CANARY','authored host value that is never passed to a candidate')
    with isolated_docker_cli() as engine:
        engine.image(IMAGE)

        def remember(identifier):
            """Register an exact allocated attempt before any caller can mutate Docker, retaining common cleanup/report ownership."""
            if identifier not in cases:cases.append(identifier)

        def create(text=BOUNDARIES):
            """Construct one actual local candidate using registered Python/text scope and new tests only, then reserve before Docker mutation."""
            value=body.model_dump(); value['request_key']=key('oci-change'); value['changes'][1]['text']=text
            test_path='backend/tests/test_community_candidate_oci_'+uuid.uuid4().hex+'.py'
            value['changes'][1]['path']=test_path; value['test_files']=[test_path]
            original=CommunityChangeInput.model_validate(value); artifact=build_change_bundle(original,prepared,snapshot,registry)
            changes.record(original,artifact)
            candidate=repository.create(artifact,prepared,PublicCodeObservation(snapshot,time.time_ns()//1000,('0'*64,)))
            intent=CommunityCodeCheckInput(request_key=key('oci-check'),bundle_id=artifact['bundle_id'])
            admission=CodeCheckAdmission(artifact['bundle_id'],artifact['bundle']['scope_hash'],candidate.commit,candidate.tree,candidate.parent,
                IMAGE,CONFIG.repository_id,CONFIG.repository_name,artifact['bundle']['scope']['source_fingerprint'],time.time_ns()//1000)
            allocation=checks.allocate(intent,candidate,IMAGE,admission); frame=frame_for(checks,allocation,candidate,repository)
            remember(allocation['id'])
            return allocation,frame
        create.remember=remember

        trusted_trace=[]; actual_attach=engine.attach
        async def trace_attach(identifier,raw,*,timeout):
            """Retain fixed supervisor outcome/reason and resource facts for failures; omit diagnostic bytes/text and ownership labels."""
            output,exit_code=await actual_attach(identifier,raw,timeout=timeout)
            try:
                result=json.loads(output)
                trusted_trace.append({name:result.get(name) for name in ('profile','status','reason','code_executed',
                    'kernel_limits_verified','uid_separation_verified','namespace_isolation_verified','output_bytes','process_exit_code')})
            except (ValueError,UnicodeError):
                record=engine.find_owned(checks.private_ownership(cases[-1]),expected_id=identifier)
                state=record['State'] if record else {}
                trusted_trace.append({'status':'unverified_report','cli_exit':exit_code,'report_bytes':len(output),
                    'daemon_state':state.get('Status'),'daemon_exit':state.get('ExitCode'),
                    'daemon_error_redacted':redacted_engine_diagnostic(state.get('Error'))})
            return output,exit_code
        engine.attach=trace_attach
        try: yield checks,engine,create,trusted_trace,tmp_path
        finally:
            completed=[]
            for identifier in cases:
                result=reconcile_check(checks,engine,identifier)
                completed.append({'candidate_commit':result['candidate_commit'],'candidate_tree':result['candidate_tree'],
                    'outcome':result['outcome'],'cleanup_verified':not result['active_reservation'],
                    'phases':[event['phase'] for event in result['history']]})
            oci_report['cases'].append({'case':request.node.name,'cleanup_verified':all(case['cleanup_verified'] for case in completed),
                'attempts':completed,'trusted_reports':trusted_trace,'original_head_preserved':git(ROOT,'rev-parse','HEAD').decode().strip()==head,
                'original_index_preserved':hashlib.sha256((ROOT/'.git/index').read_bytes()).hexdigest()==original_index})
            assert all(case['cleanup_verified'] for case in completed),'Original owned OCI cleanup remains unresolved'
            assert git(ROOT,'rev-parse','HEAD').decode().strip()==head and hashlib.sha256((ROOT/'.git/index').read_bytes()).hexdigest()==original_index


@pytest.fixture
def actual_model_flow(oci_case,prepared_change,factory,monkeypatch,oci_report):
    """Drive the positive real namespace case from authored model HTTP through original journals and actual Git, with existing owned cleanup tracking."""
    checks,engine,_create,_trace,temporary=oci_case
    original,_old_prepared,selected,registry=prepared_change
    def run(text):
        """Only the explicit positive OCI case calls this full local software chain; no actual AI model or external source is used."""
        intake,_queue,preparation,_moment,values,_plan,ids,_materialized=factory()
        claimed=preparation.claim(claim_body(ids[0]),proof(values[0]))
        preparation.finish(lease_body(claimed,contract=QueueFinishInput,outcome='prepared',note='Prepare authored model-to-real-OCI software case only.'),proof(values[0]))
        snapshot=replace(selected,commit=git(ROOT,'rev-parse','HEAD').decode().strip(),tree=git(ROOT,'rev-parse','HEAD^{tree}').decode().strip())
        test_path='backend/tests/test_community_candidate_model_oci_'+uuid.uuid4().hex+'.py'
        paths=[original.changes[0].path,test_path]
        async def issue(_config,_number):
            """Supply exact authored original public Issue data at a fresh real observation clock."""
            return proof(values[0],now=time.time_ns()//1000)
        async def code(_config,_features,requested):
            """Use committed public source/registry bytes; model and candidate cannot select another source origin or path."""
            assert requested==paths
            return PublicCodeObservation(snapshot,time.time_ns()//1000,tuple('a'*64 for _ in range(7)))
        monkeypatch.setattr('app.services.community.change_preparation.verify_public_issue',issue)
        monkeypatch.setattr('app.services.community.change_preparation.verify_public_code',code)
        monkeypatch.setattr('app.services.community.code_check_runtime.verify_public_issue',issue)
        monkeypatch.setattr('app.services.community.code_check_runtime.verify_public_code',code)
        journal=CommunityCodeModelStore(temporary/'model-flow-journal',17,'authored/fawnimo',initialize=True)
        request=CommunityCodeModelRequest(request_key=key('oci-model-original'),work_id=ids[0],paths=paths,test_files=[test_path])
        proposal={'title':'Authored model proposal for actual isolated boundary checks','files':[
            {'path':paths[0],'text':original.changes[0].text},{'path':test_path,'text':text}]}
        monkeypatch.setattr(authored_model,'ADVICE',proposal)
        # Track allocations before the real executor can mutate Docker, just as
        # the original manual case does, so failures retain normal owned cleanup.
        allocate=checks.allocate
        tracked=[]
        def track(*args,**kwargs):
            """Capture the original committed attempt before real execution, including a subsequently lost response."""
            result=allocate(*args,**kwargs);tracked.append(result['id']);_create.remember(result['id'])
            return result
        monkeypatch.setattr(checks,'allocate',track)
        async def chain():
            """Use the actual owned local-model child, real Git candidate and existing real Linux executor once."""
            with authored_model.authored_server() as (port,state):
                config_path=temporary/'model-flow-private.json';config_path.write_text(configuration(port).model_dump_json(),'utf8')
                generated=await generate_original(preparation,checks.changes,journal,request,config_path,source_verifier=issue,code_verifier=code)
                assert generated['state']=='bundled' and state.posts==1
            repository=CandidateGitRepository(repository=ROOT)
            candidate=await prepare_candidate(preparation,checks.changes,journal,request,repository=repository)
            assert candidate['bundle_id']==generated['bundle']['bundle_id']
            result=await check_candidate(preparation,checks.changes,journal,request,checks,key('oci-model-check'),IMAGE,repository=repository,boundary=engine)
            assert result['bundle_id']==generated['bundle']['bundle_id'] and result['new_model_requests']==0
            assert result['check']['candidate_commit']==candidate['candidate']['commit'] and len(tracked)==1
            assert result['check']['outcome']=='passed' and not result['check']['active_reservation']
            oci_report['local_model_candidate_flow']={'profile':'local_model_candidate_flow_v1','authored_model_http_requests':state.posts,
                'new_model_requests_during_advancement':result['new_model_requests'],'saved_bundle_reused':True,
                'candidate_commit_reused':True,'actual_owned_isolated_check_passed':True,'cleanup_verified':True,
                'actual_ai_model_used':False,'model_quality_qualified':False,'github_writes_performed':False,
                'independent_review_completed':False,'external_model_calls':0,'paid_authorization':0}
            return result['check']
        return asyncio.run(chain())
    return run


@pytest.mark.parametrize('scenario,text,expected',[('boundaries',BOUNDARIES,'passed'),('failed_test',FAIL,'failed'),
    ('output_overflow',OVERFLOW,'failed'),('forged_candidate_stdout',FORGERY,'failed'),('timeout',SLEEP,'timed_out')],
    ids=['boundaries','failed-test','output-overflow','forged-stdout','timeout'])
def test_actual_fixed_profile(oci_case,actual_model_flow,scenario,text,expected):
    """Qualify actual low-UID readonly/no-network execution, failure, bounded output, unforgeable parent report and finite child timeout."""
    checks,engine,create,trace,_temporary=oci_case
    if scenario=='boundaries':result=actual_model_flow(text)
    else:
        allocated,frame=create(text)
        result=asyncio.run(drive_allocated_check(checks,engine,allocated,frame))
    assert result['outcome']==expected,canonical(trace)
    assert not result['active_reservation'] and not result['independent_review_completed'] and not result['ci_qualified']
    assert trace and trace[-1].get('code_executed') is True and trace[-1].get('uid_separation_verified') is True


def test_actual_cancel_running_namespace(oci_case):
    """Persist cancellation while a real owned container runs, terminate it, and prove daemon absence before freeing the slot."""
    checks,engine,create,_trace,_temporary=oci_case; allocated,frame=create(SLEEP)
    async def run():
        """Observe actual daemon running state before requesting cancellation, without assuming that a saved start intent means execution began."""
        task=asyncio.create_task(drive_allocated_check(checks,engine,allocated,frame))
        until=time.monotonic()+15
        while time.monotonic()<until:
            await asyncio.sleep(.1)
            record=engine.find_owned(checks.private_ownership(allocated['id']))
            if record and record['State']['Running']:
                await asyncio.sleep(1)
                checks.request_cancel(allocated['id']); break
            if task.done(): break
        return await asyncio.wait_for(task,25)
    result=asyncio.run(run())
    assert result['cancel_requested'] and result['outcome']=='cancelled' and not result['active_reservation']


def test_actual_accepted_create_with_injected_lost_response(oci_case):
    """After actual daemon acceptance, inject a lost host response; rediscover the concrete owned CID and remove it without repeating create/start."""
    checks,engine,create,_trace,_temporary=oci_case; allocated,frame=create(); actual=engine.create; calls=[]
    def lose_response(attempt):
        """The fault is explicitly injected after a successful real create; it is not claimed as a naturally occurring daemon failure."""
        calls.append(actual(attempt)); raise DockerBoundaryError('Authored loss after actual daemon acceptance')
    engine.create=lose_response
    result=asyncio.run(drive_allocated_check(checks,engine,allocated,frame))
    assert len(calls)==1 and result['outcome']=='interrupted' and not result['active_reservation']
    assert 'created' in [event['phase'] for event in result['history']] and 'start_requested' not in [event['phase'] for event in result['history']]


def test_actual_image_source_mismatch_rejects_before_candidate(oci_case):
    """A stale expected full source manifest cannot let an image test different base bytes or begin candidate execution."""
    checks,engine,create,trace,_temporary=oci_case; allocated,frame=create()
    path=next(iter(frame['base_manifest'])); frame['base_manifest'][path]={'mode':'100644','sha':'0'*40}
    result=asyncio.run(drive_allocated_check(checks,engine,allocated,frame))
    assert result['outcome']=='interrupted' and not result['active_reservation']
    assert trace[-1].get('reason')=='complete_base_image_source_mismatch' and trace[-1].get('code_executed') is False


def test_actual_controller_death_retains_resource_until_explicit_reconcile(oci_case):
    """Kill the real separate controller after actual daemon start; journal ownership survives and explicit cleanup stops its original namespace."""
    checks,engine,create,_trace,temporary=oci_case; allocated,frame=create(SLEEP)
    spec=temporary/'controller-input.json'; spec.write_text(canonical({'allocation':allocated,'frame':frame,'root':str(checks.changes.root)}),'utf8')
    script='''import asyncio,json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from app.services.community.change_store import CommunityChangeStore
from app.services.community.code_check_store import CommunityCodeCheckStore
from app.services.community.code_check_runtime import drive_allocated_check
from app.services.community.code_check_docker import isolated_docker_cli
from app.core.contracts.community_requests import CommunityIntakeConfiguration
value=json.loads(Path(sys.argv[2]).read_text('utf8'))
config=CommunityIntakeConfiguration(enabled=True,repository_id=17,repository_name='authored/fawnimo')
checks=CommunityCodeCheckStore(CommunityChangeStore(Path(value['root']),config))
with isolated_docker_cli() as engine:
    engine.image(value['allocation']['image_id'])
    asyncio.run(drive_allocated_check(checks,engine,value['allocation'],value['frame']))
'''
    allowed={'SYSTEMROOT','WINDIR','COMSPEC','PATH','PATHEXT','TEMP','TMP','TMPDIR','HOME','USERPROFILE','LANG','LC_ALL','TZ'}
    environment={name:value for name,value in os.environ.items() if name.upper() in allowed}
    environment.update(PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',JELLYFISH_SKIP_ENV_FILE='1')
    process=subprocess.Popen([sys.executable,'-I','-B','-c',script,str(ROOT/'backend'),str(spec)],env=environment,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        until=time.monotonic()+20; running=False
        while time.monotonic()<until:
            record=engine.find_owned(checks.private_ownership(allocated['id']))
            if record and record['State']['Running']: running=True; break
            if process.poll() is not None: break
            time.sleep(.1)
        assert running,'Actual owned controller did not start its namespace'
        process.kill(); process.wait(timeout=5)
        reopened=CommunityCodeCheckStore(CommunityChangeStore(checks.changes.root,CONFIG))
        assert reopened.get(allocated['id'])['active_reservation']
        final=reconcile_check(reopened,engine,allocated['id'])
        assert final['outcome']=='interrupted' and not final['active_reservation']
    finally:
        if process.poll() is None: process.kill()
        process.wait(timeout=5)


def test_actual_supervisor_stdin_watchdog_without_controller_input(oci_case):
    """Start the fixed namespace without opening its input gate; the supervisor independently ends within its60-second hard watchdog."""
    checks,engine,create,_trace,_temporary=oci_case; allocated,_frame=create(); private=checks.private_ownership(allocated['id']); owner=private['owner_token']
    checks.request_create(allocated['id'],owner); cid=engine.create(private); checks.record_created(allocated['id'],owner,cid)
    record=engine.find_owned(private,expected_id=cid); engine.validate_created(private,record)
    checks.request_start(allocated['id'],owner)
    try: engine.command('container','start',cid)
    except DockerBoundaryError:
        diagnostic=redacted_engine_diagnostic((engine._last_error or {}).get('stderr'))
        pytest.fail('Fixed isolated start failed: '+diagnostic)
    started=time.monotonic(); record=engine.find_owned(private,expected_id=cid)
    while record['State']['Running'] and time.monotonic()-started<68:
        time.sleep(.5); record=engine.find_owned(private,expected_id=cid)
    assert not record['State']['Running'] and record['State']['ExitCode']==124
    result=json.loads(engine.command('container','logs',cid).stdout)
    assert result['reason']=='supervisor_deadline' and result['code_executed'] is False
    assert checks.get(allocated['id'])['active_reservation']
    final=reconcile_check(checks,engine,allocated['id'])
    assert final['outcome']=='interrupted' and not final['active_reservation']
