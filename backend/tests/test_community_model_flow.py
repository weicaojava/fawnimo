"""Actual temporary Git and inert source/store integration, with authored model/source/executor data only; no real AI or kernel/App/release qualification."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
import time

import pytest
import pytest_asyncio
from app.core.contracts.community_model_code import CommunityCodeModelRequest
from app.services.community.advice_store import digest
from app.services.community.change_bundles import build_change_bundle,bytes_hash
from app.services.community.change_store import CommunityChangeStore
from app.services.community.development_model_store import CommunityCodeModelStore
from app.services.community.development_model_protocol import change_input
from app.services.community.development_model_flow import prepare_candidate,linked_artifact,check_candidate
from app.services.community.development_model import generate_original
from app.services.community.candidate_git import CandidateGitRepository
from app.services.community.code_check_store import CommunityCodeCheckStore
from tests.fixtures.code_model_attach_v1 import legacy_attach_bundle
from tests.test_community_model_code import configuration,generated_result,model_work,SOURCE,TEST
from tests.test_community_change_bundles import prepared_change
from tests.test_community_queue_leases import factory
from tests.test_community_evaluations import CONFIG
from tests.test_community_candidate_git import git,assert_original
from tests.test_community_code_check_runtime import AuthoredBoundary,IMAGE
from tests import test_community_advice as authored


def reserve_proposal(prepared_change,root):
    """Store an original generated response using real prepared provenance, without an actual model or proposed-code execution."""
    body,prepared,snapshot,registry=prepared_change;entry=prepared['work']['entry']
    request=CommunityCodeModelRequest(request_key='flow-original-model',work_id=prepared['id'],paths=[item.path for item in body.changes],test_files=body.test_files)
    context={'scope':{'work_id':request.work_id,'version_id':entry['version_id'],'feature_ids':entry['feature_ids'],'acceptance':entry['acceptance']},
        'source':{'title':'原创公开需求'},'paths':request.paths,'test_files':request.test_files,
        'files':[{'path':path,'text':snapshot.files[path].decode('utf8') if path in snapshot.files else None,
            'before_sha256':bytes_hash(snapshot.files[path]) if path in snapshot.files else None} for path in request.paths]}
    provenance={'base_commit':snapshot.commit,'base_tree':snapshot.tree,'prepared_work_hash':digest(prepared['work']),
        'issue':{'fingerprint':entry['source_fingerprint']}}
    journal=CommunityCodeModelStore(root,17,'authored/fawnimo',initialize=True)
    journal.reserve(request,configuration(),context,provenance)
    proposal={'title':body.title,'files':[{'path':item.path,'text':item.text} for item in body.changes]}
    saved=journal.record_result(request,generated_result(proposal))
    return journal,request,change_input(saved['intent'],saved['model_result'])


@pytest.mark.parametrize('drift',['commit','tree','version','fingerprint','features','acceptance'])
@pytest.mark.asyncio
async def test_exact_legacy_link_counterexample_rejects_new_association_and_advancement(prepared_change,tmp_path,drift):
    """The exact published v1 method accepts mismatched association metadata; current linking and advancement reject it without touching Git or erasing history."""
    old,request,body=reserve_proposal(prepared_change,tmp_path/'legacy-model')
    current,current_request,current_body=reserve_proposal(prepared_change,tmp_path/'current-model')
    assert body==current_body
    _body,prepared,snapshot,registry=prepared_change;changed=deepcopy(prepared)
    if drift=='commit':snapshot=replace(snapshot,commit='c'*40)
    if drift=='tree':snapshot=replace(snapshot,tree='d'*40)
    if drift=='version':changed['work']['entry']['version_id']='authored-other-version'
    if drift=='fingerprint':changed['work']['entry']['source_fingerprint']='f'*64
    if drift=='features':changed['work']['entry']['feature_ids'].append(next(row['id'] for row in registry['features'] if row['id']!='F-015.01'))
    if drift=='acceptance':changed['work']['entry']['acceptance'].append('不同的原准备验收条件')
    artifact=build_change_bundle(body,changed,snapshot,registry)
    original=legacy_attach_bundle(old,request,artifact)
    assert original['state']=='bundled'
    with pytest.raises(ValueError):current.attach_bundle(current_request,artifact)
    assert current.replay(current_request)['state']=='generated'
    changes=CommunityChangeStore(tmp_path/'legacy-changes',CONFIG,initialize=True);changes.record(body,artifact)
    class NoGit:
        """A scope mismatch must be refused before original Git metadata or any executor is accessed."""
        def recover(self,_artifact):
            """Raise if a rejected model/bundle association reaches Git inspection."""
            raise AssertionError('Unexpected Git access for mismatched original model scope')
    with pytest.raises(ValueError):await prepare_candidate(None,changes,old,request,repository=NoGit())
    assert old.replay(request)==original and changes.get(artifact['bundle_id'])==artifact


@pytest_asyncio.fixture
async def actual_local_flow(model_work,tmp_path,monkeypatch):
    """Use actual owned model HTTP and real Git objects, with dirty authored local files that must stay outside the generated candidate."""
    _intake,_queue,preparation,changes,journal,request,issue,code,snapshots,_calls,config_path=model_work
    root=tmp_path/'public-clone';root.mkdir()
    git(root,'init','--initial-branch=main')
    for path,raw in snapshots[0].files.items():
        target=root/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    git(root,'add','--',*snapshots[0].files.keys());git(root,'commit','-m','authored public model-flow base')
    head=git(root,'rev-parse','HEAD').decode().strip();tree=git(root,'rev-parse','HEAD^{tree}').decode().strip()
    snapshots[0]=replace(snapshots[0],commit=head,tree=tree)
    working=root/SOURCE;working.write_bytes(b'# authored staged marker stays local\n');git(root,'add','--',SOURCE)
    working.write_bytes(b'# authored unstaged marker stays local\n')
    private=root/'private-authored-note.txt';private.write_text('Authored untracked data remains outside every candidate.','utf8')
    protected={'head':head,'index_sha256':hashlib.sha256((root/'.git/index').read_bytes()).hexdigest(),'working':working.read_bytes(),'private':private.read_bytes()}
    proposal={'title':'原创可审阅本地模型提案','files':[{'path':SOURCE,'text':'value = 2\n'},
        {'path':TEST,'text':'def test_authored_pipeline():\n    assert True\n'}]}
    monkeypatch.setattr(authored,'ADVICE',proposal)
    with authored.authored_server() as (port,state):
        config_path.write_text(configuration(port).model_dump_json(),'utf8')
        result=await generate_original(preparation,changes,journal,request,config_path,source_verifier=issue,code_verifier=code)
        assert result['state']=='bundled' and state.posts==1
    monkeypatch.setattr('app.services.community.code_check_runtime.verify_public_issue',issue)
    monkeypatch.setattr('app.services.community.code_check_runtime.verify_public_code',code)
    return preparation,changes,journal,request,CandidateGitRepository(repository=root),protected,result


@pytest.mark.asyncio
async def test_actual_model_bundle_to_local_candidate_preserves_original_git_and_replay(actual_local_flow):
    """The coordinator creates the exact model-derived local commit once using the original private-index/CAS implementation, then recovers offline."""
    preparation,changes,journal,request,repository,protected,original=actual_local_flow
    saved=await prepare_candidate(preparation,changes,journal,request,repository=repository)
    assert saved['stage']=='local_candidate' and not saved['replayed'] and saved['new_model_requests']==0
    commit=saved['candidate']['commit']
    assert git(repository.root,'show',commit+':'+SOURCE)==b'value = 2\n'
    assert saved['bundle_id']==original['bundle']['bundle_id'] and not saved['development_complete'] and not saved['github_writes_performed']
    assert_original(repository,protected)
    recovered=await prepare_candidate(None,changes,journal,request,repository=repository)
    assert recovered['replayed'] and recovered['candidate']==saved['candidate']
    assert_original(repository,protected)


@pytest.mark.asyncio
async def test_linked_candidate_uses_existing_check_admission_and_recovery_without_git_or_engine(actual_local_flow):
    """Use real original Git/store/source control flow and an explicitly authored executor report; no actual namespace/code execution is claimed."""
    preparation,changes,journal,request,repository,protected,_original=actual_local_flow
    checks=CommunityCodeCheckStore(changes,initialize=True);engine=AuthoredBoundary(checks)
    result=await check_candidate(preparation,changes,journal,request,checks,'original-model-linked-check',IMAGE,repository=repository,boundary=engine)
    assert result['stage']=='isolated_check' and result['check']['outcome']=='passed' and not result['check']['active_reservation']
    assert engine.calls.count('create')==engine.calls.count('start')==1 and result['new_model_requests']==0
    assert_original(repository,protected)
    recovered=await check_candidate(None,changes,journal,request,checks,'original-model-linked-check',IMAGE)
    assert recovered['replayed'] and recovered['check']['id']==result['check']['id']
    assert engine.calls.count('create')==engine.calls.count('start')==1 and not recovered['independent_review_completed']
    with pytest.raises(ValueError):await check_candidate(None,changes,journal,request,checks,'original-model-linked-check','sha256:'+'f'*64)
    assert engine.calls.count('create')==engine.calls.count('start')==1


def run_flow_cli(journal,changes,request,extra):
    """Run the actual operator CLI with only owned root paths and a deliberately unavailable daemon; recovery must not use it."""
    request_file=journal.root.parent/'original-flow-request.json';request_file.write_text(request.model_dump_json(),'utf8')
    allowed={'SYSTEMROOT','WINDIR','COMSPEC','PATH','PATHEXT','TEMP','TMP','TMPDIR','LANG','LC_ALL','TZ'}
    environment={name:value for name,value in os.environ.items() if name.upper() in allowed}
    environment.update(PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',JELLYFISH_SKIP_ENV_FILE='1',DOCKER_HOST='tcp://authored.invalid:2375')
    result=subprocess.run([sys.executable,str(Path(__file__).resolve().parents[1]/'advance_community.py'),
        '--model-root',str(journal.root),'--changes-root',str(changes.root),'--repository-id','17',
        '--repository-name','authored/fawnimo','--request-file',str(request_file),*extra],env=environment,capture_output=True,timeout=15)
    assert result.returncode==0,result.stderr.decode('utf8')
    return json.loads(result.stdout)


@pytest.mark.asyncio
async def test_actual_cli_recovers_original_candidate_without_preparation_root(actual_local_flow):
    """A shipped CLI call returns the same original candidate while preserving the actual dirty clone, with no source or model setup."""
    preparation,changes,journal,request,repository,protected,_original=actual_local_flow
    first=await prepare_candidate(preparation,changes,journal,request,repository=repository)
    recovered=run_flow_cli(journal,changes,request,['--prepare-candidate','--repository',str(repository.root)])
    assert recovered['replayed'] and recovered['candidate']==first['candidate'] and recovered['new_model_requests']==0
    assert_original(repository,protected)


@pytest.mark.asyncio
async def test_actual_cli_recovers_original_check_without_git_source_or_engine(actual_local_flow):
    """An existing check returns through the actual CLI before disconnected Docker, source or Git access can repeat execution."""
    preparation,changes,journal,request,repository,protected,_original=actual_local_flow
    checks=CommunityCodeCheckStore(changes,initialize=True);engine=AuthoredBoundary(checks)
    first=await check_candidate(preparation,changes,journal,request,checks,'cli-original-model-check',IMAGE,repository=repository,boundary=engine)
    recovered=run_flow_cli(journal,changes,request,['--run-check','--image-id',IMAGE,'--check-key','cli-original-model-check'])
    assert recovered['replayed'] and recovered['check']['id']==first['check']['id'] and engine.calls.count('create')==1
    assert not recovered['github_writes_performed'] and not recovered['development_complete']
    assert_original(repository,protected)


@pytest.mark.parametrize('image',[None,'latest','sha256:'+'a'*63])
@pytest.mark.asyncio
async def test_invalid_image_is_rejected_before_any_candidate_or_executor_mutation(actual_local_flow,image):
    """A malformed operator image cannot cause a preliminary candidate creation or executor action."""
    preparation,changes,journal,request,repository,protected,_original=actual_local_flow
    checks=CommunityCodeCheckStore(changes,initialize=True);engine=AuthoredBoundary(checks)
    _model,artifact=linked_artifact(changes,journal,request)
    assert repository.recover(artifact) is None
    with pytest.raises(ValueError):await check_candidate(preparation,changes,journal,request,checks,'invalid-image-model-check',image,repository=repository,boundary=engine)
    assert repository.recover(artifact) is None and engine.calls==[]
    assert_original(repository,protected)
