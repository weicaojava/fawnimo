"""Real source/preparation/check/gate databases fence authored observer facts; no test supplies real independent approval or model credentials."""
import asyncio
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from app.core.contracts.community_queue import QueueFinishInput
from app.core.contracts.community_code_checks import CommunityCodeCheckInput,CodeCheckAdmission,CodeCheckObservation,CodeCheckCleanup
from app.core.contracts.community_review_gates import CommunityPRGateInput
from app.services.community.candidate_git import CandidateCommit
from app.services.community.change_bundles import build_change_bundle
from app.services.community.change_store import CommunityChangeStore
from app.services.community.code_check_store import CommunityCodeCheckStore
from app.services.community.evaluations import canonical
from app.services.community.public_code import PublicCodeObservation
from app.services.community.review_gate_admission import observe_current_pr_gate,RUNNER
from app.services.community.review_gate_store import CommunityReviewGateStore
from app.services.community.store import CommunityStoreConflict
from tests.test_community_review_gate_decisions import gate
from tests.test_community_change_bundles import prepared_change
from tests.test_community_queue_leases import factory,claim_body,lease_body,key
from tests.test_community_evaluations import CONFIG,proof


@pytest.fixture
def current_gate(factory,prepared_change,gate,tmp_path,monkeypatch):
    """Use real durable preparation/check/cleanup history; observer identities and kernel facts remain explicitly authored inputs, never actual OCI proof."""
    intake,queue,preparation,_moment,values,_plan,ids,_materialized=factory()
    claimed=preparation.claim(claim_body(ids[0]),proof(values[0]))
    preparation.finish(lease_body(claimed,contract=QueueFinishInput,outcome='prepared',note='Prepared source only, not execution authority.'),proof(values[0]))
    original,_old_prepared,snapshot,registry=prepared_change
    change=original.model_copy(update={'work_id':ids[0],'request_key':key('gate-change')})
    artifact=build_change_bundle(change,preparation.get_work(ids[0]),snapshot,registry)
    changes=CommunityChangeStore(tmp_path/'current-changes',CONFIG,initialize=True); changes.record(change,artifact)
    checks=CommunityCodeCheckStore(changes,initialize=True)
    candidate=CandidateCommit(artifact['bundle_id'],artifact['branch'],'c'*40,'d'*40,snapshot.commit,17,'authored/fawnimo')
    image='sha256:'+'e'*64; runner=hashlib.sha256(RUNNER.read_bytes()).hexdigest()
    request=CommunityCodeCheckInput(request_key=key('kernel-check'),bundle_id=artifact['bundle_id'])
    admission=CodeCheckAdmission(artifact['bundle_id'],artifact['bundle']['scope_hash'],candidate.commit,candidate.tree,candidate.parent,image,
        17,'authored/fawnimo',artifact['bundle']['scope']['source_fingerprint'],time.time_ns()//1000)
    allocated=checks.allocate(request,candidate,image,admission); owner=checks.private_ownership(allocated['id'])['owner_token']
    checks.request_create(allocated['id'],owner); checks.record_created(allocated['id'],owner,'f'*64); checks.request_start(allocated['id'],owner)
    checks.observe(allocated['id'],owner,CodeCheckObservation('passed',0,runner,'2'*64,20,True,True,True))
    checks.finish(allocated['id'],owner,'passed',CodeCheckCleanup(allocated['id'],allocated['container_name'],image,owner,'f'*64,True,True))
    seed_policy,_seed_body,_seed_artifact,seed_facts,seed_pr=gate
    policy=seed_policy.model_copy(update={'trusted_runner_sha256':runner})
    gates=CommunityReviewGateStore(tmp_path/'current-gates',policy,initialize=True,isolated_from=(intake.root,changes.root))
    body=CommunityPRGateInput(request_key=key('pr-gate'),bundle_id=artifact['bundle_id'],check_attempt_id=allocated['id'],pr_number=7)
    mode={'value':None}; calls=[]

    class AuthoredRepository:
        """Use explicit immutable Git identities for source-race tests; actual committed-workflow reading is separately tested in temporary Git repositories."""
        def recover(self,value):
            """No new candidate/ref is created when observing review eligibility."""
            assert value==artifact; return candidate

        def workflow_blobs(self,base,tree):
            """The fixture's pinned blobs are not an assertion that GitHub ran those actual workflow files."""
            assert base==snapshot.tree and tree==candidate.tree; return seed_facts.workflow_blobs

    async def issue(_config,_number):
        """Read authored current public source, or simulate a now-withdrawn requirement without changing stored old records."""
        calls.append('issue'); value=json.loads(json.dumps(values[0]))
        if mode['value']=='withdrawn': value['issue']['state']='closed'
        return proof(value,now=time.time_ns()//1000)

    async def code(_config,_features,_paths):
        """Selection may change while source I/O is in flight; the final short writer must recheck it before any separate gate append."""
        calls.append('code'); selected=snapshot; moment=time.time_ns()//1000
        if mode['value']=='selection': queue.activate(None,key('deselect'),1)
        elif mode['value']=='expired': moment-=60000001
        elif mode['value']=='main': selected=replace(snapshot,commit='b'*40)
        elif mode['value']=='catalog':
            files=dict(snapshot.files); catalog=json.loads(files['capabilities/features.json']); catalog['changed_gate_catalog']=True
            files['capabilities/features.json']=canonical(catalog).encode(); selected=replace(snapshot,files=files)
        return PublicCodeObservation(selected,moment,('a'*64,))

    async def pr(_config,_number):
        """A current independent reviewer/CI snapshot is authored; draft mode demonstrates policy denial after otherwise valid original source admission."""
        calls.append('pr'); value={**seed_pr.pull_request,'head_ref':candidate.branch}
        if mode['value']=='draft': value['draft']=True
        return replace(seed_pr,pull_request=value,checked_at_us=time.time_ns()//1000)

    monkeypatch.setattr('app.services.community.review_gate_admission.verify_public_issue',issue)
    monkeypatch.setattr('app.services.community.review_gate_admission.verify_public_code',code)
    monkeypatch.setattr('app.services.community.review_gate_admission.verify_public_pr',pr)
    return preparation,checks,gates,body,AuthoredRepository(),mode,calls


def test_new_observation_binds_current_prepared_source_then_original_recovers_after_withdrawal(current_gate):
    """The full service appends once under current-source fencing, then recovers the exact old observation without requiring new source/check roots."""
    prep,checks,gates,body,repository,_mode,calls=current_gate
    saved=asyncio.run(observe_current_pr_gate(prep,checks,gates,body,repository=repository))
    assert saved['observation']['decision']['eligible_at_observation'] and sorted(calls)==['code','issue','pr']
    prep.queue.activate(None,key('withdraw-plan'),1)
    recovered=asyncio.run(observe_current_pr_gate(None,None,gates,body))
    assert recovered['replayed'] and recovered['observation']==saved['observation'] and recovered['fresh_action_authorized'] is False
    assert len(calls)==3


@pytest.mark.parametrize('bad',['selection','expired','main','catalog','withdrawn'])
def test_source_or_selection_changes_after_outer_reads_do_not_save_gate_proof(current_gate,bad):
    """Fresh public reads run outside writers and cannot certify an outdated requirement/selection/catalog after returning."""
    prep,checks,gates,body,repository,mode,_calls=current_gate; mode['value']=bad
    with pytest.raises(CommunityStoreConflict): asyncio.run(observe_current_pr_gate(prep,checks,gates,body,repository=repository))
    assert gates.replay(body) is None


def test_valid_source_with_draft_pr_is_recorded_as_blocked_not_permission(current_gate):
    """Source/current check validity does not override an unfinished real PR review state."""
    prep,checks,gates,body,repository,mode,_calls=current_gate; mode['value']='draft'
    result=asyncio.run(observe_current_pr_gate(prep,checks,gates,body,repository=repository))
    assert not result['observation']['decision']['eligible_at_observation'] and not result['fresh_action_authorized']


def test_untrusted_policy_image_is_rejected_before_any_public_read(current_gate,tmp_path):
    """A passed local check is insufficient when it used an image outside the maintainer's pinned trusted set."""
    prep,checks,gates,body,repository,_mode,calls=current_gate
    policy=gates.policy.model_copy(update={'trusted_check_image_ids':('sha256:'+'a'*64,)})
    other=CommunityReviewGateStore(tmp_path/'wrong-image-gates',policy,initialize=True,isolated_from=(prep.intake.root,checks.changes.root))
    with pytest.raises(CommunityStoreConflict): asyncio.run(observe_current_pr_gate(prep,checks,other,body,repository=repository))
    assert calls==[] and other.replay(body) is None


def test_one_failed_source_cancels_and_joins_other_readers_before_return(current_gate,monkeypatch):
    """A persistent caller must not inherit still-running sibling network helpers after the first collector rejects its source."""
    prep,checks,gates,body,repository,_mode,_calls=current_gate; stopped=[]
    async def fail(_config,_number):
        """An early fixed-host failure is a normal unavailable-proof outcome, not permission to leave the remaining helpers detached."""
        raise CommunityStoreConflict('Authored early source failure')
    async def pending_code(*_args):
        """Stay pending until the orchestration cancels this owned reader and joins its cleanup."""
        try: await asyncio.Event().wait()
        finally: stopped.append('code')
    async def pending_pr(*_args):
        """Stay pending until the orchestration cancels this owned reader and joins its cleanup."""
        try: await asyncio.Event().wait()
        finally: stopped.append('pr')
    monkeypatch.setattr('app.services.community.review_gate_admission.verify_public_issue',fail)
    monkeypatch.setattr('app.services.community.review_gate_admission.verify_public_code',pending_code)
    monkeypatch.setattr('app.services.community.review_gate_admission.verify_public_pr',pending_pr)
    async def run():
        """Assert cleanup while the event loop remains alive, so asyncio.run shutdown cannot hide a detached-reader defect."""
        with pytest.raises(CommunityStoreConflict): await observe_current_pr_gate(prep,checks,gates,body,repository=repository)
        assert sorted(stopped)==['code','pr']
        assert gates.replay(body) is None
    asyncio.run(run())


def test_actual_cli_disabled_observation_and_original_recovery_need_no_source_git_or_network(gate,tmp_path):
    """Four real CLI processes initialize a separate closed root, save a denial and recover it without any current sources or policy file."""
    body=gate[1]; script=Path(__file__).resolve().parents[1]/'observe_community_pr.py'; root=tmp_path/'cli-gates'
    request=tmp_path/'pr-request.json'; request.write_text(canonical(body.model_dump()),'utf8')
    allowed={'SYSTEMROOT','WINDIR','COMSPEC','PATH','PATHEXT','TEMP','TMP','TMPDIR','LANG','LC_ALL','TZ'}
    env={name:value for name,value in os.environ.items() if name.upper() in allowed}; env.update(PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',JELLYFISH_SKIP_ENV_FILE='1')
    base=[sys.executable,str(script),'--gates-root',str(root)]
    actions=[['--initialize-gates','--repository-id','17','--repository-name','authored/fawnimo','--root',str(tmp_path/'source-ref'),'--changes-root',str(tmp_path/'change-ref')],
        ['--evaluate-file',str(request)]]
    for action in actions:
        result=subprocess.run(base+action,capture_output=True,env=env,timeout=15)
        assert result.returncode==0,result.stderr.decode()
        value=json.loads(result.stdout)
    saved=value['observation']; assert saved['decision']['blocking_reasons']==['review_gate_disabled']
    for action in [['--get-observation',saved['id']],['--evaluate-file',str(request),'--repository',str(tmp_path/'unavailable-public-clone')]]:
        result=subprocess.run(base+action,capture_output=True,env=env,timeout=15)
        assert result.returncode==0,result.stderr.decode()
        value=json.loads(result.stdout); assert value['observation']==saved and value['historical_only'] and value['fresh_action_authorized'] is False
    assert not (tmp_path/'source-ref').exists() and not (tmp_path/'change-ref').exists()


def test_actual_original_requirement_review_cli_keeps_its_history_interface(current_gate):
    """Adding a PR gate must not overwrite the existing requirement-evaluation command or its durable original-source history interface."""
    preparation,checks,_gates,body,_repository,_mode,_calls=current_gate
    artifact=checks.changes.get(body.bundle_id)
    version=preparation.get_work(artifact['bundle']['scope']['work_id'])['work']['entry']['version_id']
    script=Path(__file__).resolve().parents[1]/'review_community.py'
    allowed={'SYSTEMROOT','WINDIR','COMSPEC','PATH','PATHEXT','TEMP','TMP','TMPDIR','LANG','LC_ALL','TZ'}
    environment={name:value for name,value in os.environ.items() if name.upper() in allowed}
    environment.update(PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',JELLYFISH_SKIP_ENV_FILE='1')
    result=subprocess.run([sys.executable,str(script),'--root',str(preparation.intake.root),'--repository-id','17',
        '--repository-name','authored/fawnimo','--history',version],capture_output=True,env=environment,timeout=15)
    assert result.returncode==0,result.stderr.decode()
    value=json.loads(result.stdout); assert isinstance(value,list) and len(value)==1 and 'F-015.01' in result.stdout.decode()
