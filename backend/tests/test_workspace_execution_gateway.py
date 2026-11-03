"""Public authenticated gateway execution across six authored protocol phases, separate fixed workers and original project charges; no real provider or paid authorization."""
from contextlib import closing,ExitStack
import os
from pathlib import Path
import shutil
import sqlite3
from unittest.mock import AsyncMock,patch
import httpx
import pytest
from fastapi.testclient import TestClient
from app.controlplane.gateway import create_gateway
from app.controlplane.store import IdentityStore
from app.controlplane.execution_admission import canonical
from tests.test_workspace_local_model_execution import LocalExecutionSpaces,model_origin,PROJECT
from tests.test_workspace_local_understanding_execution import LocalUnderstandingSpaces
from tests.test_workspace_local_native_execution import LocalNativeSpaces

OPERATIONS=['agent_execution','script_preparation','video_understanding','native_video']


class PublicExecutionSpaces(LocalExecutionSpaces):
    """Preserve the real private-file operator setup but send every model submission through the public cookie gateway, never a private token."""
    def submit_local(self,side,kind,path,body):
        """Require actual accepted public requests, remembering their original key/body and checking replays return the same row."""
        response=self.request(side,'POST',path,json=body)
        assert response.status_code==201,response.text
        row=response.json()['data']
        if kind in self.jobs[side]:assert row['id']==self.jobs[side][kind]['id']
        else:self.jobs[side][kind]=row;self.intents[side][kind]=(path,body)
        return row


class PublicUnderstandingSpaces(LocalUnderstandingSpaces,PublicExecutionSpaces):
    """Use the actual local video, ASR/vision adapters and fixed worker while requiring the public original-account admission path."""
    def understanding_submit(self,side,body):
        """Submit only through the cookie-bound original project, without accessing any internal worker token."""
        response=self.request(side,'POST',f'/video-understanding/projects/{PROJECT}/jobs',json=body)
        assert response.status_code==201,response.text
        return response.json()['data']['id']


class PublicNativeSpaces(LocalNativeSpaces,PublicExecutionSpaces):
    """Use the existing strict native-video account/quote/runtime fixture while keeping public caller ownership through admission and querying."""
    def native_submit(self,side,body):
        """Reach the real public project submission API rather than supplying a private worker token."""
        response=self.request(side,'POST',f'/execution/projects/{PROJECT}/submissions',json=body)
        assert response.status_code==201,response.text
        return response.json()['data']['job_id']


@pytest.fixture(params=['understanding','native'],scope='module')
def execution_spaces(request,tmp_path_factory):
    """Own all identities, private data, model origins, actual worker trees and media; close them at the end of each independent protocol group."""
    root=tmp_path_factory.mktemp('public-execution-'+request.param);store=IdentityStore(root)
    accounts={side:store.provision('local-execution-'+side,'authored-local-execution-password') for side in ('a','b')}
    workspaces=[accounts[side]['workspace_id'] for side in ('a','b')]
    private=root/'private';private.mkdir()
    (private/'asset-image-admission.json').write_text(canonical({'enabled':True,'workspace_ids':workspaces}),'utf8')
    (private/'execution-admission.json').write_text(canonical({'enabled':True,'workspace_ids':workspaces,'operations':OPERATIONS}),'utf8')
    probe=os.environ.get('FAWNIMO_TEST_FFMPEG') or shutil.which('ffmpeg')
    assert probe and Path(probe).is_file(),'An owned local media probe is required'
    with ExitStack() as stack:
        origins={side:stack.enter_context(model_origin(side)) for side in ('a','b')}
        app=create_gateway(root,secure_cookies=False,public_origin='http://script-task.local',capacity=2,probe=Path(probe))
        with TestClient(app,base_url='http://script-task.local') as client:
            spaces=(PublicUnderstandingSpaces(app,client,root,origins,Path(probe),accounts=accounts) if request.param=='understanding' else
                PublicNativeSpaces(app,client,root,origins,accounts=accounts))
            spaces.group=request.param
            yield spaces


@pytest.mark.parametrize('side',['a','b'])
@pytest.mark.parametrize('kind',['workflow','connection','script','image'])
def test_public_original_request_replay_uses_only_own_model_and_history(execution_spaces,side,kind):
    """Actual public submissions/replays preserve original IDs and do not borrow the other account's equal business IDs, HTTP origin or charge records."""
    spaces=execution_spaces;other='b' if side=='a' else 'a';row=spaces.jobs[side][kind]
    prefix='/asset-image-generation/jobs/' if kind=='image' else '/agent-execution/runs/'
    assert spaces.ok(side,'GET',prefix+row['id'])['status']==('candidate' if kind=='image' else 'succeeded')
    assert spaces.request(other,'GET',prefix+row['id']).status_code==404
    counts=[len(spaces.origins[key]['calls']) for key in ('a','b')]
    assert spaces.submit_local(side,kind,*spaces.intents[side][kind])['id']==row['id']
    assert [len(spaces.origins[key]['calls']) for key in ('a','b')]==counts
    assert all(call['authenticated'] for call in spaces.origins[side]['calls'])


@pytest.mark.parametrize('side',['a','b'])
def test_public_understanding_or_native_keeps_original_actor_project_and_private_history(execution_spaces,side):
    """Actual local ASR/vision succeeds or native acceptance/polling runs, with the original caller and conservative charge held solely in its private project."""
    spaces=execution_spaces;other='b' if side=='a' else 'a'
    counts=[len(spaces.origins[key]['calls']) for key in ('a','b')]
    if spaces.group=='understanding':
        identifier=spaces.understanding_jobs[side];path=f'/video-understanding/projects/{PROJECT}/jobs/'+identifier
        assert spaces.ok(side,'GET',path)['state']=='succeeded'
        assert spaces.understanding_submit(side,spaces.understanding_bodies[side])==identifier
        reserved=73;operation='video_understanding'
    else:
        identifier=spaces.native_jobs[side];path=f'/execution/projects/{PROJECT}/jobs/'+identifier
        assert spaces.ok(side,'GET',path)['state']=='running'
        assert spaces.native_submit(side,spaces.native_bodies[side])==identifier
        assert spaces.origins[side]['native_queries']
        reserved=80;operation='native_video'
    assert spaces.request(other,'GET',path).status_code==404
    assert [len(spaces.origins[key]['calls']) for key in ('a','b')]==counts
    budget=spaces.ok(side,'GET',f'/video-runtime/projects/{PROJECT}/budget')
    assert budget['reserved_minor']==reserved and budget['spent_minor']==0
    directory=spaces.root/'workspaces'/spaces.identities[side]['workspace_id']
    with closing(sqlite3.connect(directory/'studio.db')) as db:
        assert db.execute('SELECT DISTINCT submitted_by FROM model_charge_batches').fetchall()==[(spaces.identities[side]['user']['id'],)]
    with spaces.app.state.identity_store.connect() as db:
        operations={row[0] for row in db.execute('SELECT DISTINCT operation FROM execution_admission_audit WHERE workspace_id=?',(spaces.identities[side]['workspace_id'],))}
        assert operations=={'agent_execution','script_preparation',operation}


@pytest.mark.parametrize('side',['a','b'])
def test_browser_scope_and_internal_headers_cannot_override_fixed_execution_grant(execution_spaces,side):
    """The cookie owner selects the worker, browser internal headers are dropped, and the real private endpoint separately refuses a wrong policy digest."""
    spaces=execution_spaces;other='b' if side=='a' else 'a';path,body=spaces.intents[side]['workflow']
    counts=[len(spaces.origins[key]['calls']) for key in ('a','b')]
    assert spaces.request(side,'POST',path,json=body,headers={'X-Workspace-Id':spaces.identities[other]['workspace_id']}).status_code==409
    old=spaces.ok(side,'POST',path,json=body,headers={'X-Execution-Admission-Policy':'0'*64,'X-Workspace-Token':'browser-forgery','X-Workspace-Role':'owner'})
    assert old['id']==spaces.jobs[side]['workflow']['id']
    worker=spaces.app.state.workers.workers[spaces.identities[side]['workspace_id']]
    with httpx.Client(trust_env=False,follow_redirects=False) as internal:
        rejected=internal.post(f'http://127.0.0.1:{worker.port}/api/v1'+path,json=body,
            headers={'X-Workspace-Token':worker.token,'X-Identity-Id':spaces.identities[side]['user']['id'],'X-Execution-Admission-Policy':'0'*64})
    assert rejected.status_code==503 and [len(spaces.origins[key]['calls']) for key in ('a','b')]==counts


def test_viewer_unlisted_and_anonymous_fail_before_worker_discovery(execution_spaces):
    """Explicit operator capabilities do not grant member write permission or allow another valid login to consume worker/model capacity."""
    spaces=execution_spaces;store=spaces.app.state.identity_store;who=spaces.identities['a'];path,body=spaces.intents['a']['workflow']
    with store.connect() as db:db.execute("UPDATE memberships SET role='viewer' WHERE user_id=? AND workspace_id=?",(who['user']['id'],who['workspace_id']))
    try:
        with patch.object(spaces.app.state.workers,'get',AsyncMock(side_effect=AssertionError('Refused request must not discover a worker'))):
            assert spaces.request('a','POST',path,json=body,headers={'X-Workspace-Role':'owner'}).status_code==403
            store.provision('unlisted-execution','Authored-unlisted-execution-password')
            token=store.login('unlisted-execution','Authored-unlisted-execution-password','authored-local')
            spaces.tokens['c']=token;spaces.identities['c']=store.current(token)
            assert spaces.request('c','POST',path,json=body).status_code==503
            assert spaces.client.post('/api/v1'+path,json=body).status_code==401
    finally:
        with store.connect() as db:db.execute("UPDATE memberships SET role='owner' WHERE user_id=? AND workspace_id=?",(who['user']['id'],who['workspace_id']))


def test_changed_policy_stops_new_posts_keeps_original_holds_and_callbacks_closed(execution_spaces):
    """A cached authorized worker cannot continue using changed policy bytes, while original histories/uncertain holds remain readable without more model POSTs."""
    spaces=execution_spaces;path,body=spaces.intents['a']['workflow'];policy=spaces.root/'private/execution-admission.json';original=policy.read_bytes()
    counts=[len(spaces.origins[key]['calls']) for key in ('a','b')];before=spaces.ok('a','GET',f'/video-runtime/projects/{PROJECT}/budget')
    try:
        policy.write_bytes(original+b' ')
        assert spaces.request('a','POST',path,json=body).status_code==503
        assert spaces.ok('a','GET','/agent-execution/runs/'+spaces.jobs['a']['workflow']['id'])['status']=='succeeded'
        assert spaces.ok('a','GET',f'/video-runtime/projects/{PROJECT}/budget')==before
        assert spaces.request('a','POST',f'/execution/projects/{PROJECT}/callbacks',json={}).status_code==503
        assert [len(spaces.origins[key]['calls']) for key in ('a','b')]==counts
    finally:policy.write_bytes(original)


def test_operator_capability_never_bypasses_an_exhausted_original_project_budget(execution_spaces):
    """A valid enabled policy cannot create another hold or model POST when the selected project's remaining capacity is zero."""
    spaces=execution_spaces;path,body=spaces.intents['a']['workflow']
    before=spaces.ok('a','GET',f'/video-runtime/projects/{PROJECT}/budget')
    spaces.ok('a','PUT',f'/video-runtime/projects/{PROJECT}/budget',json={'expected_revision':before['revision'],'limit_minor':before['reserved_minor']})
    counts=[len(spaces.origins[key]['calls']) for key in ('a','b')]
    assert spaces.request('a','POST',path,json={**body,'request_key':'new-after-project-exhaustion'}).status_code==409
    assert [len(spaces.origins[key]['calls']) for key in ('a','b')]==counts
    after=spaces.ok('a','GET',f'/video-runtime/projects/{PROJECT}/budget')
    assert after['reserved_minor']==before['reserved_minor'] and after['spent_minor']==0
