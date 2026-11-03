"""Actual local model HTTP/Git/SQLite/CLI delivery coordination with authored kernel/GitHub/reviewer facts only; no live writes or approval qualification."""
import asyncio
import base64
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
import pytest_asyncio
from app.core.contracts.community_publication import CommunityPublicationPolicy
from app.core.contracts.community_review_gates import CommunityReviewGatePolicy,TrustedWorkflowRule,WORKFLOW_REQUIREMENTS
from app.services.community.code_check_runtime import RUNNER
from app.services.community.code_check_store import CommunityCodeCheckStore
from app.services.community.development_model_flow import check_candidate,linked_artifact
from app.services.community.development_model_delivery import publication_input,review_input,publish_model_candidate,observe_model_review
from app.services.community.publication_objects import publication_objects
from app.services.community.publication_runtime import reconcile_publication
from app.services.community.publication_store import CommunityPublicationStore
from app.services.community.review_gate_store import CommunityReviewGateStore
from tests.test_community_model_flow import actual_local_flow
from tests.test_community_model_code import model_work
from tests.test_community_queue_leases import factory
from tests.test_community_change_bundles import prepared_change
from tests.test_community_review_gate_decisions import gate
from tests.test_community_code_check_runtime import AuthoredBoundary,IMAGE
from tests.test_community_candidate_git import assert_original
from tests.test_community_publication import AuthoredRemote

CHECK_KEY='delivery-original-check'
PUBLICATION_KEY='delivery-original-publication'


class AuthoredExactObjectsRemote(AuthoredRemote):
    """Check the entire actual Git projection while simulating only the fixed GitHub transport, never an external service."""
    def __init__(self,policy,candidate,objects):
        """Keep original object projections to detect whether the coordinator substitutes a different model proposal or Git revision."""
        super().__init__(policy,candidate);self.objects=objects

    def create_object(self,kind,body):
        """Validate complete actual serialized content before returning its authored remote identity."""
        self.calls.append(kind)
        if kind=='blobs':
            content=base64.b64decode(body['content'],validate=True)
            sha=hashlib.sha1(b'blob '+str(len(content)).encode()+b'\0'+content).hexdigest()
            assert {'sha':sha,'body':body} in self.objects['blobs']
            return {'sha':sha}
        assert kind in {'trees','commits'} and body==self.objects['tree' if kind=='trees' else 'commit']
        return {'sha':self.candidate.tree if kind=='trees' else self.candidate.commit}


@pytest_asyncio.fixture
async def delivery(actual_local_flow,gate,tmp_path,monkeypatch):
    """Continue an actual original local model proposal through real Git/check journals; kernel, remote and independent actors remain authored data."""
    preparation,changes,journal,request,repository,protected,_original=actual_local_flow
    checks=CommunityCodeCheckStore(changes,initialize=True);engine=AuthoredBoundary(checks)
    checked=await check_candidate(preparation,changes,journal,request,checks,CHECK_KEY,IMAGE,repository=repository,boundary=engine)
    _saved,artifact=linked_artifact(changes,journal,request);candidate=repository.recover(artifact)
    pairs=repository.workflow_blobs(candidate.parent,candidate.tree)
    rules=tuple(TrustedWorkflowRule(path=path,blob_sha=pairs[path]['base']) for path in WORKFLOW_REQUIREMENTS)
    runner=hashlib.sha256(RUNNER.read_bytes()).hexdigest()
    policy=CommunityPublicationPolicy(repository_id=17,repository_name='authored/fawnimo',enabled=True,
        trusted_workflows=rules,trusted_check_image_ids=(IMAGE,),trusted_runner_sha256=runner)
    publications=CommunityPublicationStore(tmp_path/'delivery-publications',policy,initialize=True,
        isolated_from=(preparation.intake.root,changes.root,journal.root))
    objects=publication_objects(repository,artifact);remote=AuthoredExactObjectsRemote(policy,candidate,objects);created=[]
    def remote_factory(_policy):
        """Credential/transport discovery must happen only after admission and outside original-source writers."""
        assert _policy==policy
        with preparation.intake.connect() as db:db.execute('BEGIN IMMEDIATE');db.rollback()
        created.append(1);return remote
    from app.services.community import change_preparation,publication_admission,review_gate_admission
    for module in (publication_admission,review_gate_admission):
        monkeypatch.setattr(module,'verify_public_issue',change_preparation.verify_public_issue)
        monkeypatch.setattr(module,'verify_public_code',change_preparation.verify_public_code)
    seed_policy,_body,_artifact,_facts,observed=gate
    gate_policy=seed_policy.model_copy(update={'trusted_workflows':rules,'trusted_check_image_ids':(IMAGE,),'trusted_runner_sha256':runner})
    gates=CommunityReviewGateStore(tmp_path/'delivery-gates',gate_policy,initialize=True,
        isolated_from=(preparation.intake.root,changes.root))
    state={'draft':True,'reads':0}
    async def pr(_config,number):
        """Provide explicitly authored exact-head PR/CI/reviewer facts; a published draft must first remain blocked."""
        state['reads']+=1;assert number==7
        now=time.time_ns()//1000
        pull={**observed.pull_request,'head_ref':candidate.branch,'head_sha':candidate.commit,'base_sha':candidate.parent,'draft':state['draft']}
        reviews=tuple({**review,'commit_id':candidate.commit,'submitted_at_us':now-10} for review in observed.reviews)
        runs=tuple({**run,'head_sha':candidate.commit,'pull_request':{**run['pull_request'],'head_sha':candidate.commit,'base_sha':candidate.parent}} for run in observed.workflows)
        return replace(observed,checked_at_us=now,main_commit=candidate.parent,pull_request=pull,reviews=reviews,workflows=runs,
            tested_merge={'sha':pull['merge_commit_sha'],'tree':candidate.tree,'parents':[candidate.parent,candidate.commit]})
    monkeypatch.setattr(review_gate_admission,'verify_public_pr',pr)
    assert checked['check']['outcome']=='passed'
    return preparation,changes,journal,request,checks,publications,gates,repository,protected,remote,remote_factory,created,state


async def publish(values,*,preparation=True,repository=True,transport=True):
    """Call the shipped coordinator with original saved identities and selectively disconnected fresh dependencies for recovery cases."""
    prep,changes,journal,request,checks,publications,_gates,repo,_protected,_remote,factory,_created,_state=values
    return await publish_model_candidate(prep if preparation else None,changes,journal,request,checks,publications,CHECK_KEY,IMAGE,PUBLICATION_KEY,
        repository=repo if repository else None,remote_factory=factory if transport else None)


async def observe(values,key,*,fresh=True):
    """Observe only the PR derived from the original positively confirmed publication, never a caller-selected PR number."""
    prep,changes,journal,request,checks,publications,gates,repo,_protected,_remote,_factory,_created,_state=values
    return await observe_model_review(prep if fresh else None,changes,journal,request,checks,publications,gates,CHECK_KEY,IMAGE,PUBLICATION_KEY,key,
        repository=repo if fresh else None)


@pytest.mark.asyncio
async def test_actual_model_git_projection_to_authored_draft_and_review_stays_separately_gated(delivery):
    """Join actual HTTP/model journal/Git/check store/object projection and existing publication/review stores without claiming any real remote or independent approval."""
    prep,changes,journal,request,checks,publications,gates,repo,protected,remote,_factory,created,state=delivery
    saved=await publish(delivery)
    assert saved['result']['phase']=='published' and saved['new_model_requests']==0 and created==[1]
    assert remote.calls==['blobs','blobs','trees','commits','ref','pr'] and remote.pr[0]['draft'] is True
    blocked=await observe(delivery,'delivery-draft-observation')
    assert not blocked['result']['observation']['decision']['eligible_at_observation'] and state['reads']==1
    state['draft']=False
    diagnostic=await observe(delivery,'delivery-ready-observation')
    assert diagnostic['result']['observation']['decision']['eligible_at_observation'] and state['reads']==2
    assert not diagnostic['independent_review_completed'] and not diagnostic['merge_authorized'] and not diagnostic['deployment_authorized']
    assert_original(repo,protected)
    old=await observe(delivery,'delivery-draft-observation',fresh=False)
    assert old['replayed'] and old['result']['observation']==blocked['result']['observation'] and state['reads']==2


@pytest.mark.asyncio
async def test_original_publication_recovery_uses_no_source_git_credential_or_model(delivery):
    """The exact saved association recovers before any disconnected fresh dependency and cannot create a second draft."""
    saved=await publish(delivery);remote=delivery[9];before=list(remote.calls)
    recovered=await publish(delivery,preparation=False,repository=False,transport=False)
    assert recovered['replayed'] and recovered['result']==saved['result'] and remote.calls==before and delivery[11]==[1]


@pytest.mark.parametrize('drift',['check-key','image','model-request','repo-identity'])
@pytest.mark.asyncio
async def test_wrong_original_association_blocks_before_any_remote_factory(delivery,drift):
    """A different request/check/image/repository cannot piggyback on an unrelated passed check or expose a credential."""
    prep,changes,journal,request,checks,publications,_gates,repo,_protected,remote,factory,created,_state=delivery
    check_key=CHECK_KEY;image=IMAGE
    if drift=='check-key':check_key='other-original-check'
    elif drift=='image':image='sha256:'+'f'*64
    elif drift=='model-request':request=request.model_copy(update={'request_key':'other-original-model'})
    else:journal.identity={**journal.identity,'repository_id':18}
    with pytest.raises(ValueError):
        await publish_model_candidate(prep,changes,journal,request,checks,publications,check_key,image,PUBLICATION_KEY,repository=repo,remote_factory=factory)
    assert created==[] and remote.calls==[]


@pytest.mark.asyncio
async def test_disabled_publication_blocks_before_fresh_source_clone_or_credential(delivery,tmp_path):
    """Initializing the old publication policy stays default closed even for an exact saved model and passed authored check."""
    _prep,changes,journal,request,checks,_pubs,_gates,_repo,_protected,_remote,_factory,_created,_state=delivery
    closed=CommunityPublicationStore(tmp_path/'closed-model-pub',CommunityPublicationPolicy(repository_id=17,repository_name='authored/fawnimo'),initialize=True)
    with pytest.raises(ValueError):await publish_model_candidate(None,changes,journal,request,checks,closed,CHECK_KEY,IMAGE,PUBLICATION_KEY)


@pytest.mark.asyncio
async def test_unknown_draft_write_must_reconcile_before_review_and_never_post_again(delivery):
    """Accepted-but-lost draft responses retain original state; recovery never repeats the POST and only positive read-only reconciliation enables a review target."""
    remote=delivery[9];remote.failure='pr-lost'
    saved=await publish(delivery);assert saved['result']['phase']=='attention'
    with pytest.raises(ValueError):await observe(delivery,'unknown-model-review')
    before=list(remote.calls)
    old=await publish(delivery,preparation=False,repository=False,transport=False)
    assert old['replayed'] and remote.calls==before and remote.calls.count('pr')==1
    reconciled=reconcile_publication(delivery[5],saved['result']['id'],remote)
    assert reconciled['phase']=='reconciled' and reconciled['history'][-1]['details']['pr_number']==7
    diagnostic=await observe(delivery,'reconciled-model-review')
    assert not diagnostic['result']['observation']['decision']['eligible_at_observation'] and remote.calls==before


@pytest.mark.asyncio
async def test_observation_cannot_start_without_original_published_draft(delivery):
    """An otherwise valid saved model/check is not authority to select any arbitrary remote PR."""
    with pytest.raises(ValueError):await observe(delivery,'unpublished-model-review')
    assert delivery[12]['reads']==0 and delivery[11]==[]


@pytest.mark.parametrize('failure',['forged_success','unknown_absent'])
@pytest.mark.asyncio
async def test_nonpassing_or_unresolved_original_check_never_discovers_credentials(delivery,failure):
    """Another passed check for the same candidate cannot substitute for the specifically selected failed or unresolved original attempt."""
    prep,changes,journal,request,checks,publications,_gates,repo,_protected,remote,factory,created,_state=delivery
    engine=AuthoredBoundary(checks);engine.fail=failure
    key='nonpassing-delivery-check'
    checked=await check_candidate(prep,changes,journal,request,checks,key,IMAGE,repository=repo,boundary=engine)
    assert checked['check']['outcome']!='passed'
    with pytest.raises(ValueError):await publish_model_candidate(prep,changes,journal,request,checks,publications,key,IMAGE,PUBLICATION_KEY,repository=repo,remote_factory=factory)
    assert created==[] and remote.calls==[]


@pytest.mark.asyncio
async def test_original_observation_recovers_after_later_publication_becomes_unconfirmed(delivery):
    """An original immutable observation remains readable while a new observation is blocked by later unresolved publication facts."""
    published=await publish(delivery);old=await observe(delivery,'before-publication-uncertain')
    remote=delivery[9];remote.pr=[]
    reconciled=reconcile_publication(delivery[5],published['result']['id'],remote)
    assert reconciled['phase']=='reconciled' and 'pr_number' not in reconciled['history'][-1]['details']
    recovered=await observe(delivery,'before-publication-uncertain',fresh=False)
    assert recovered['replayed'] and recovered['result']['observation']==old['result']['observation']
    with pytest.raises(ValueError):await observe(delivery,'after-publication-uncertain')
    assert delivery[12]['reads']==1


def cli(values,action,extra=()):
    """Run the actual new maintainer CLI with disconnected fresh resources; only original local journal recovery can succeed."""
    _prep,changes,journal,request,_checks,publications,gates,_repo,_protected,_remote,_factory,_created,_state=values
    file=journal.root.parent/'delivery-original.json';file.write_text(request.model_dump_json(),'utf8')
    allowed={'SYSTEMROOT','WINDIR','COMSPEC','PATH','PATHEXT','TEMP','TMP','TMPDIR','LANG','LC_ALL','TZ'}
    env={name:value for name,value in os.environ.items() if name.upper() in allowed}
    env.update(PYTHONUTF8='1',JELLYFISH_SKIP_ENV_FILE='1',PYTHONDONTWRITEBYTECODE='1')
    return subprocess.run([sys.executable,str(Path(__file__).resolve().parents[1]/'deliver_community.py'),
        '--model-root',str(journal.root),'--changes-root',str(changes.root),'--publication-root',str(publications.root),
        '--repository-id','17','--repository-name','authored/fawnimo','--request-file',str(file),
        '--check-key',CHECK_KEY,'--image-id',IMAGE,'--publication-key',PUBLICATION_KEY,action,*extra],
        env=env,capture_output=True,timeout=20)


@pytest.mark.asyncio
async def test_actual_cli_recovers_original_publication_and_review_without_source_or_credentials(delivery):
    """Both user-facing actions recover exact original records through separate Python processes with no Git clone/source or credential argument."""
    saved=await publish(delivery);blocked=await observe(delivery,'cli-original-delivery-review')
    published=cli(delivery,'--publish-draft')
    assert published.returncode==0,published.stderr.decode()
    recovered=json.loads(published.stdout)
    assert recovered['replayed'] and recovered['result']==saved['result']
    observed=cli(delivery,'--observe-review',['--gates-root',str(delivery[6].root),'--observation-key','cli-original-delivery-review'])
    assert observed.returncode==0,observed.stderr.decode()
    recovered=json.loads(observed.stdout)
    assert recovered['replayed'] and recovered['result']['observation']==blocked['result']['observation']
    assert delivery[9].calls.count('pr')==1 and delivery[12]['reads']==1
