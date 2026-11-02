"""Actual journal/CLI and authored engine-result fault cases; no authored kernel fact here qualifies Linux container execution."""
import asyncio
import base64
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from app.services.community.code_check_docker import DockerBoundaryError
from app.services.community.code_check_runtime import drive_allocated_check,reconcile_check,validate_report,run_current_check
from app.services.community.evaluations import canonical,digest
from app.services.community.candidate_git import CandidateCommit
from app.services.community.change_bundles import build_change_bundle
from app.services.community.change_store import CommunityChangeStore
from app.services.community.code_check_store import CommunityCodeCheckStore
from app.services.community.public_code import PublicCodeObservation,git_blob_id
from app.core.contracts.community_code_checks import CommunityCodeCheckInput
from app.core.contracts.community_queue import QueueFinishInput
from app.services.community.store import CommunityStoreConflict
from tests.test_community_code_check_store import checks,IMAGE,cleanup
from tests.test_community_change_store import proposal_store
from tests.test_community_change_bundles import prepared_change
from tests.test_community_queue_leases import factory,key,claim_body,lease_body
from tests.test_community_evaluations import CONFIG,proof
from tests.test_community_code_check_docker import record_for,CID


def report_for(frame):
    """Authored positive kernel/UID facts test parsing only; no actual OCI system is asserted by this fixture."""
    output=b'authored diagnostic text must remain private'
    return {'profile':'linux_pytest_check_v1','attempt_id':frame['attempt_id'],'bundle_id':frame['artifact']['bundle_id'],
        'scope_hash':frame['artifact']['bundle']['scope_hash'],'runner_sha256':frame['runner_sha256'],
        'base_manifest_hash':digest(frame['base_manifest']),'candidate_manifest_hash':digest(frame['candidate_manifest']),
        'status':'passed','reason':'candidate_tests_finished','code_executed':True,'kernel_limits_verified':True,
        'namespace_isolation_verified':True,'uid_separation_verified':True,'no_new_privileges':True,'seccomp_filter':True,'image_readonly':True,
        'independent_review_completed':False,'ci_qualified':False,'deployment_authorized':False,'external_model_calls':0,'paid_authorization':0,
        'supervisor_uid':0,'supervisor_cap_eff':'00000000000000e0','candidate_uid':10001,'candidate_gid':10001,
        'candidate_cap_eff':'0000000000000000','memory_bytes':536870912,'swap_bytes':0,'pids':64,'cpu_quota_ratio':1,'interfaces':['lo'],
        'diagnostic_output_base64':base64.b64encode(output).decode(),'output_sha256':hashlib.sha256(output).hexdigest(),
        'output_bytes':len(output),'process_exit_code':0}


class AuthoredBoundary:
    """Inject daemon acceptance/interruptions without executing candidate code or touching any Docker configuration."""
    def __init__(self,checks):
        """Keep owned/absent state separate from the real SQLite journal so interruption cases exercise actual reservation rules."""
        self.checks=checks; self.record=None; self.calls=[]; self.fail=None; self.cancel_on_create=False; self.block=False; self.unavailable=False

    def budget(self,_seconds):
        """Real time enforcement belongs to adapter/OCI integration; this fixture only observes control flow."""
        return nullcontext()

    def image(self,_identifier):
        """Image lookup is an authored fixture fact here; the real Docker boundary refuses mutable tags and secret-bearing environments."""
        self.calls.append('image')

    def create(self,attempt):
        """Optionally accept and lose the response, or make acceptance unknown while no resource is observed."""
        self.calls.append('create')
        if self.fail != 'unknown_absent': self.record=record_for(attempt)
        if self.cancel_on_create: self.checks.request_cancel(attempt['id'])
        if self.fail in {'accepted_unknown','unknown_absent'}: raise DockerBoundaryError('Authored lost response')
        return CID

    def find_owned(self,_attempt,*,expected_id=None):
        """Daemon availability is a positive fact, not implied by a missing returned object."""
        if self.unavailable: raise DockerBoundaryError('Authored unavailable daemon')
        return self.record

    def validate_created(self,_attempt,_record):
        """This fixture does not validate a real daemon; literal inspect contracts have their own tests."""
        self.calls.append('validate')

    async def attach(self,_identifier,raw,*,timeout):
        """Return authored stopped-supervisor facts, or stay pending until durable cancellation stops the owned client task."""
        self.calls.append('start')
        if self.fail == 'start_unknown': raise DockerBoundaryError('Authored unknown start')
        if self.block: await asyncio.Event().wait()
        frame=json.loads(raw); self.record['State'].update(Status='exited',ExitCode=0)
        report=report_for(frame)
        if self.fail == 'forged_success': report['candidate_uid']=0
        return canonical(report).encode(),0

    def remove_owned(self,attempt,*,expected_id=None):
        """Only a known owned fixture CID is removed; absence observation does not decide unacknowledged create acceptance."""
        if self.unavailable: raise DockerBoundaryError('Authored unavailable daemon')
        self.calls.append('remove'); self.record=None
        return cleanup(attempt,attempt['owner_token'],container_id=expected_id)


@pytest.fixture
def execution(checks):
    """Allocate an actual immutable SQLite attempt, then supply in-memory authored supervisor input for runtime tests."""
    store,changes,_moment,candidate,body,admission=checks
    allocated=store.allocate(body,candidate,IMAGE,admission)
    frame={'profile':'linux_pytest_check_v1','attempt_id':allocated['id'],'deadline_us':allocated['deadline_us'],
        'runner_sha256':'1'*64,'artifact':changes.get(body.bundle_id),'base_manifest':{},'candidate_manifest':{}}
    return store,allocated,frame,AuthoredBoundary(store)


def test_one_new_winner_drives_once_and_public_history_contains_no_diagnostic_or_owner(execution):
    """A repeated allocation response never repeats create/start, and hashing result text does not publish its contents."""
    checks,allocated,frame,boundary=execution
    result=asyncio.run(drive_allocated_check(checks,boundary,allocated,frame))
    assert result['outcome'] == 'passed' and not result['active_reservation']
    before=list(boundary.calls)
    replay=asyncio.run(drive_allocated_check(checks,boundary,{**allocated,'replayed':True},frame))
    assert boundary.calls == before and replay == checks.get(allocated['id'])
    public=canonical(result); private=checks.private_ownership(allocated['id'])
    assert 'authored diagnostic' not in public and 'diagnostic_output_base64' not in public and private['owner_token'] not in public
    assert not result['independent_review_completed'] and not result['ci_qualified'] and not result['deployment_authorized']


@pytest.mark.parametrize('failure,terminal',[('accepted_unknown',True),('unknown_absent',False),('start_unknown',True),('forged_success',True)])
def test_unknown_mutations_or_untrusted_success_never_auto_replay_or_release_unproved_acceptance(execution,failure,terminal):
    """Rediscovery can resolve a concrete owned CID, while successful absence after a lost create response cannot release its slot."""
    checks,allocated,frame,boundary=execution; boundary.fail=failure
    result=asyncio.run(drive_allocated_check(checks,boundary,allocated,frame))
    assert result['active_reservation'] is not terminal and result['outcome'] != 'passed'
    assert boundary.calls.count('create') == 1 and boundary.calls.count('start') <= 1
    again=reconcile_check(checks,boundary,allocated['id'])
    assert again['active_reservation'] is not terminal and boundary.calls.count('create') == 1


def test_cancel_between_create_and_ack_never_starts_candidate(execution):
    """Cancellation committed during an in-flight create keeps ownership, records the late acknowledgement, then removes without start."""
    checks,allocated,frame,boundary=execution; boundary.cancel_on_create=True
    result=asyncio.run(drive_allocated_check(checks,boundary,allocated,frame))
    assert result['outcome'] == 'cancelled' and 'start' not in boundary.calls and not result['active_reservation']


def test_pending_attach_observes_durable_cancellation_and_cleans_only_original(execution):
    """Cancellation of a waiting client is followed by separate verified daemon cleanup before the journal becomes terminal."""
    checks,allocated,frame,boundary=execution; boundary.block=True
    async def run():
        """Wait for the fixed attach to begin, then simulate a separate maintainer's durable cancel request."""
        task=asyncio.create_task(drive_allocated_check(checks,boundary,allocated,frame))
        while 'start' not in boundary.calls: await asyncio.sleep(.01)
        checks.request_cancel(allocated['id'])
        return await asyncio.wait_for(task,2)
    result=asyncio.run(run())
    assert result['outcome'] == 'cancelled' and not result['active_reservation'] and boundary.calls.count('remove') == 1


def test_cleanup_failure_is_recoverable_without_replacing_first_audit_reason(execution):
    """Repeated interruption reasons retain the first immutable audit event and the unresolved reservation, rather than failing a second append."""
    checks,allocated,_frame,boundary=execution; boundary.unavailable=True
    private=checks.private_ownership(allocated['id'])
    checks.cleanup_required(allocated['id'],private['owner_token'],'controller_interrupted')
    result=reconcile_check(checks,boundary,allocated['id'])
    assert result['active_reservation'] and result['current_receipt']['details']['reason'] == 'controller_interrupted'
    boundary.unavailable=False
    final=reconcile_check(checks,boundary,allocated['id'])
    assert final['outcome'] == 'interrupted' and not final['active_reservation']


@pytest.mark.parametrize('bad',['uid','caps','running','oom','exit','code','bundle','manifest','runner','output','size','fee','approval','bool_limit','json_duplicate','candidate_forgery'])
def test_supervisor_result_requires_exact_stopped_identity_and_kernel_facts(execution,bad):
    """A candidate's fabricated success line, stale source, weak UID policy or daemon OOM cannot become a passed trusted observation."""
    _checks,_allocated,frame,_boundary=execution; report=report_for(frame); record=record_for(); record['State']['Status']='exited'
    if bad == 'uid': report['candidate_uid']=0
    elif bad == 'caps': report['candidate_cap_eff']='00000000000000e0'
    elif bad == 'running': record['State']['Running']=True
    elif bad == 'oom': record['State']['OOMKilled']=True
    elif bad == 'exit': record['State']['ExitCode']=1
    elif bad == 'code': report['code_executed']=False
    elif bad == 'bundle': report['bundle_id']='a'*64
    elif bad == 'manifest': report['candidate_manifest_hash']='a'*64
    elif bad == 'runner': report['runner_sha256']='a'*64
    elif bad == 'output': report['output_sha256']='a'*64
    elif bad == 'size': report['output_bytes']=65537
    elif bad == 'fee': report['external_model_calls']=1
    elif bad == 'approval': report['independent_review_completed']=True
    elif bad == 'bool_limit': report['cpu_quota_ratio']=True
    raw=canonical(report).encode()
    if bad == 'json_duplicate': raw=raw[:-1]+b',"status":"passed"}'
    elif bad == 'candidate_forgery': raw=b'{"status":"passed","independent_review_completed":true}\n'
    with pytest.raises(DockerBoundaryError): validate_report(raw,0,record,frame)


def test_original_check_replay_precedes_source_git_docker_after_restart(execution,checks):
    """An original pending request is readable with no prepared source, Git constructor or daemon contact, and grants no operation replay."""
    store,allocated,_frame,_boundary=execution; _store,_changes,_clock,_candidate,body,_admission=checks
    result=asyncio.run(run_current_check(None,store,body,IMAGE))
    assert result['id'] == allocated['id'] and result['replayed'] and result['active_reservation']


def test_actual_cli_recovers_original_and_durable_cancel_without_a_docker_engine(execution,checks,tmp_path):
    """Real separate CLI processes read/recover/cancel the fixture journal; invalid Docker context selection cannot erase the saved stop request."""
    _store,allocated,_frame,_boundary=execution; _checks,changes,_clock,_candidate,body,_admission=checks
    script=Path(__file__).resolve().parents[1]/'check_community.py'; request=tmp_path/'check.json'; request.write_text(canonical(body.model_dump()),'utf8')
    allowed={'SYSTEMROOT','WINDIR','COMSPEC','PATH','PATHEXT','TEMP','TMP','TMPDIR','LANG','LC_ALL','TZ'}
    env={name:value for name,value in os.environ.items() if name.upper() in allowed}
    env.update(PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',JELLYFISH_SKIP_ENV_FILE='1',DOCKER_HOST='tcp://authored.invalid:2375')
    args=[sys.executable,str(script),'--changes-root',str(changes.root),'--repository-id','17','--repository-name','authored/fawnimo']
    for extra in [['--get-check',allocated['id']],['--image-id',IMAGE,'--check-file',str(request)],['--cancel-check',allocated['id']]]:
        result=subprocess.run(args+extra,capture_output=True,env=env,timeout=15)
        assert result.returncode == 0,result.stderr.decode()
        value=json.loads(result.stdout); assert value['id'] == allocated['id'] and value['active_reservation']
    assert value['cancel_requested'] and value['current_phase'] == 'cleanup_required'


@pytest.mark.parametrize('bad',[None,'expired','commit','tree','repository','selection','withdrawal','catalog'],
    ids=['current','expired','commit','tree','repository','selection','withdrawal','catalog'])
def test_fresh_execution_admission_rechecks_exact_source_selection_and_catalog(factory,prepared_change,tmp_path,monkeypatch,bad):
    """Real source/queue/proposal/check stores fence admission after authored public reads; rejected provenance creates no daemon reservation."""
    intake,queue,preparation,_moment,values,_plan,ids,_materialized=factory()
    claimed=preparation.claim(claim_body(ids[0]),proof(values[0]))
    preparation.finish(lease_body(claimed,contract=QueueFinishInput,outcome='prepared',note='Authored scope is prepared, not an execution permission.'),proof(values[0]))
    original,_old_prepared,snapshot,_registry=prepared_change
    body=original.model_copy(update={'work_id':ids[0],'request_key':key('source-check-change')})
    artifact=build_change_bundle(body,preparation.get_work(ids[0]),snapshot,queue.catalog())
    changes=CommunityChangeStore(tmp_path/'source-check-changes',CONFIG,initialize=True); changes.record(body,artifact)
    checks=CommunityCodeCheckStore(changes,initialize=True); boundary=AuthoredBoundary(checks)
    candidate=CandidateCommit(artifact['bundle_id'],artifact['branch'],'c'*40,'d'*40,snapshot.commit,17,'authored/fawnimo')

    class AuthoredRepository:
        """Supply fixed immutable local candidate identities for source-admission control flow; actual Git object tests are separate."""
        def recover(self,_artifact):
            """The preexisting inert candidate is returned without authorizing execution or changing any ref."""
            return candidate

        def code_manifest(self,_tree):
            """These subset bytes are fixtures, not an assertion of a real image's complete committed source."""
            return {path:{'mode':'100644','sha':git_blob_id(raw)} for path,raw in snapshot.files.items()}

    async def issue(_config,_number):
        """Use positive authored public proof at the current wall clock, or simulate a now-withdrawn issue."""
        value=deepcopy(values[0])
        if bad=='withdrawal': value['issue']['state']='closed'
        return proof(value,now=time.time_ns()//1000)

    async def code(_config,_features,_paths):
        """Change one exact provenance field after the outer read and before the final transactional recheck."""
        selected=snapshot; checked=time.time_ns()//1000
        if bad=='expired': checked-=60000001
        elif bad=='commit': selected=replace(snapshot,commit='f'*40)
        elif bad=='tree': selected=replace(snapshot,tree='f'*40)
        elif bad=='repository': selected=replace(snapshot,repository_id=18)
        elif bad=='selection': queue.activate(None,key('deselect'),1)
        elif bad=='catalog':
            files=dict(snapshot.files); value=json.loads(files['capabilities/features.json']); value['authored_changed_catalog']=True
            files['capabilities/features.json']=canonical(value).encode(); selected=replace(snapshot,files=files)
        return PublicCodeObservation(selected,checked,('0'*64,))

    monkeypatch.setattr('app.services.community.code_check_runtime.verify_public_issue',issue)
    monkeypatch.setattr('app.services.community.code_check_runtime.verify_public_code',code)
    intent=CommunityCodeCheckInput(request_key=key('source-check'),bundle_id=artifact['bundle_id'])
    if bad is None:
        result=asyncio.run(run_current_check(preparation,checks,intent,IMAGE,repository=AuthoredRepository(),boundary=boundary))
        assert result['outcome']=='passed' and not result['active_reservation'] and boundary.calls.count('create')==1
    else:
        with pytest.raises(CommunityStoreConflict):
            asyncio.run(run_current_check(preparation,checks,intent,IMAGE,repository=AuthoredRepository(),boundary=boundary))
        assert checks.replay(intent,IMAGE) is None and 'create' not in boundary.calls and 'start' not in boundary.calls
