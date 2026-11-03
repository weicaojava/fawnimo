"""Default-closed capability policy, original operation/actor audit and private worker header checks on owned data only."""
import json
import hashlib
import sqlite3
import os
import httpx
import pytest
from pydantic import ValidationError
from app.controlplane.execution_admission import ExecutionAdmissionPolicy,canonical
from app.controlplane.store import IdentityStore
from app.core.contracts.workspace_execution_admission import WorkspaceExecutionAdmissionConfiguration,WorkspaceExecutionAdmissionGrant,execution_submission_operation
from app.core.workspace_boundary import TenantBoundaryMiddleware

WA='ws_'+'a'*32
WB='ws_'+'b'*32
TEXT='/api/v1/agent-execution/agents/own-agent/runs'
SCRIPT='/api/v1/script-preparation/projects/own-project/chapters/own-chapter/candidates'


def policy_file(root,workspaces=(WA,),**updates):
    """Write a task-owned fixed policy with no credentials, money ceilings or external paths."""
    path=root/'private/execution-admission.json';path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(canonical({'enabled':True,'workspace_ids':list(workspaces),'operations':['agent_execution'],**updates}),'utf8')
    return path


@pytest.mark.parametrize('changes',[{'enabled':1},{'schema_version':True},{'workspace_ids':[WA,WA]},
    {'workspace_ids':['../elsewhere']},{'operations':['*']},{'operations':['asset_image']},{'operations':['agent_execution','agent_execution']},
    {'operations':[1]},{'api_key':'authored-only'},{'workspace_budget_minor':100}])
def test_ambiguous_scope_capabilities_credentials_and_money_fields_cannot_create_authority(changes):
    """Strict fields reject relabelling/coercion and keep the operator switch separate from actual model and project authorization."""
    with pytest.raises(ValidationError):WorkspaceExecutionAdmissionConfiguration.model_validate(changes)


def test_default_exact_operation_owner_and_file_change_keep_new_admission_closed(tmp_path):
    """Cached history grants remain stable while missing, empty, changed or mismatched configuration cannot authorize a new operation."""
    assert ExecutionAdmissionPolicy(tmp_path).current_ticket(WA) is None
    policy_file(tmp_path,enabled=False);assert ExecutionAdmissionPolicy(tmp_path).current_ticket(WA) is None
    policy_file(tmp_path,operations=[]);assert ExecutionAdmissionPolicy(tmp_path).current_ticket(WA) is None
    path=policy_file(tmp_path);policy=ExecutionAdmissionPolicy(tmp_path);ticket=policy.current_ticket(WA,'agent_execution')
    assert ticket is not None and policy.current_ticket(WB) is None and policy.current_ticket(WA,'script_preparation') is None
    assert ticket.grant.operations==('agent_execution',) and hashlib.sha256(ticket.checked_payload(WA).encode()).hexdigest()==ticket.grant.policy_sha256
    path.write_bytes(path.read_bytes()+b' ')
    assert policy.current_ticket(WA) is None and policy.startup_ticket(WA)==ticket


@pytest.mark.parametrize('raw',[b'{"enabled":false,"enabled":true}',b'{"enabled":NaN}',b'x'*16385,b'{"operations":["*"]}'])
def test_malformed_duplicate_nonfinite_or_oversized_policy_stays_closed(tmp_path,raw):
    """Invalid private input cannot acquire capability merely because a valid workspace ID is known."""
    path=policy_file(tmp_path);path.write_bytes(raw)
    assert ExecutionAdmissionPolicy(tmp_path).current_ticket(WA) is None


@pytest.mark.parametrize('method,path,expected',[
    ('POST',TEXT,'agent_execution'),('POST','/api/v1/agent-execution/models/m/connection-tests','agent_execution'),
    ('POST','/api/v1/agent-execution/runs/r/retry','agent_execution'),('POST',SCRIPT,'script_preparation'),
    ('POST','/api/v1/video-understanding/projects/p/jobs','video_understanding'),('POST','/api/v1/video-understanding/projects/p/jobs/j/retry','video_understanding'),
    ('POST','/api/v1/execution/projects/p/submissions','native_video'),('POST','/api/v1/shot-plans/projects/p/plans/l/shots/s/submissions','native_video'),
    ('GET',TEXT,None),('PUT',SCRIPT,None),('POST','/api/v1/execution/projects/p/callbacks',None),
    ('POST','/api/v1/film/tasks',None),('POST','/api/v1/asset-image-generation/jobs/j/retry',None),('POST',SCRIPT+'/availability',None)])
def test_only_named_submission_shapes_receive_an_operation(method,path,expected):
    """Callbacks, legacy/image routes, read actions and lookalike paths never borrow this policy's authority."""
    assert execution_submission_operation(method,path)==expected


def test_original_actor_operation_and_policy_audit_are_atomic_and_immutable(tmp_path):
    """Real SQLite appends a bounded non-secret policy witness; an excluded operation rolls the original audit back and no row can be replaced."""
    store=IdentityStore(tmp_path);account=store.provision('execution-owner','Authored-execution-password')
    identity={'workspace_id':account['workspace_id'],'user':{'id':account['user_id']}}
    policy_file(tmp_path,(account['workspace_id'],));ticket=ExecutionAdmissionPolicy(tmp_path).current_ticket(account['workspace_id'])
    identifier=store.audit_start(identity,'POST',TEXT,b'{"request_key":"original"}',execution_admission=ticket)
    with store.connect() as db:
        saved=db.execute('SELECT * FROM execution_admission_audit WHERE request_id=?',(identifier,)).fetchone()
        assert saved['submitted_by']==account['user_id'] and saved['operation']=='agent_execution' and saved['policy_sha256']==ticket.grant.policy_sha256
    for statement in ('UPDATE execution_admission_audit SET operation=operation','DELETE FROM execution_admission_audit',
        'INSERT OR REPLACE INTO execution_admission_audit SELECT * FROM execution_admission_audit'):
        with store.connect() as db:
            with pytest.raises(sqlite3.IntegrityError):db.execute(statement)
    with pytest.raises(ValueError):store.audit_start(identity,'POST',SCRIPT,b'{}',execution_admission=ticket)
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM request_audit').fetchone()[0]==1
        with pytest.raises(sqlite3.OperationalError):db.execute('SELECT rowid FROM execution_admission_audit')


@pytest.mark.asyncio
@pytest.mark.parametrize('path,digest,expected',[(TEXT,None,503),(TEXT,'0'*64,503),(TEXT,'correct',200),(SCRIPT,'correct',503)])
async def test_fixed_worker_requires_original_capability_and_private_policy_header(path,digest,expected):
    """Even the trusted private token cannot relabel an operation or substitute another policy digest for a bound worker."""
    calls=[];grant=WorkspaceExecutionAdmissionGrant(workspace_id=WA,policy_sha256='a'*64,operations=['agent_execution'])
    async def app(scope,receive,send):
        """Count only authored endpoint admission; no model, user data or external service is involved."""
        calls.append(scope['path']);await send({'type':'http.response.start','status':200,'headers':[]});await send({'type':'http.response.body','body':b'{}'})
    guarded=TenantBoundaryMiddleware(app,workspace_id=WA,internal_token='t'*48,execution_admission_provider=lambda:grant)
    headers={'X-Workspace-Token':'t'*48}
    if digest is not None:headers['X-Execution-Admission-Policy']=grant.policy_sha256 if digest=='correct' else digest
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=guarded),base_url='http://owned.invalid') as client:
        response=await client.post(path,headers=headers)
        assert response.status_code==expected and len(calls)==(1 if expected==200 else 0)
        ready=await client.get('/__workspace/ready',headers={'X-Workspace-Token':'t'*48})
        assert ready.json()['execution_admission']==grant.model_dump(mode='json')


@pytest.mark.parametrize('correct_owner',[True,False])
def test_private_grant_never_unlocks_imported_execution_or_relabels_startup_owner(tmp_path,monkeypatch,correct_owner):
    """The actual fixed-worker configurator preserves import locks and refuses a different grant owner before mutating startup environment."""
    from tests.test_model_price_workspace import marked,startup
    from run_tenant_worker import configure_worker
    directory=marked(tmp_path,WA,imported=True);private=directory/'private';private.mkdir()
    for name in ('model-prices.json','local-models.json','native-video.json'):(private/name).write_text('{}','utf8')
    monkeypatch.setattr(os,'environ',{**os.environ,'MODEL_PRICE_CONFIG':'authored-original-before-rejection'})
    grant=WorkspaceExecutionAdmissionGrant(workspace_id=WA if correct_owner else WB,policy_sha256='a'*64,operations=['agent_execution'])
    message={**startup(tmp_path,WA),'execution_admission':grant.model_dump(mode='json')}
    if not correct_owner:
        with pytest.raises(ValueError):configure_worker(message)
        assert os.environ['MODEL_PRICE_CONFIG']=='authored-original-before-rejection'
    else:
        configure_worker(message)
        assert os.environ['WORKSPACE_RUNTIME_LOCKED']=='true' and os.environ['MODEL_PRICE_CONFIG']==''
        assert os.environ['WORKSPACE_LOCAL_MODELS_CONFIG']=='' and not os.environ.get('VIDEO_RUNTIME_CONFIG')
