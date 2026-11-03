"""Operator policy/audit/private-worker checks on owned SQLite and authored ASGI endpoints; no real model, bill or public qualification."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3

import httpx
import pytest
from pydantic import ValidationError
from app.core.contracts.workspace_image_admission import WorkspaceImageAdmissionConfiguration,WorkspaceImageAdmissionGrant,image_submission_path
from app.controlplane.image_admission import ImageAdmissionPolicy,ImageAdmissionTicket,canonical
from app.controlplane.store import IdentityStore
from app.core.workspace_boundary import TenantBoundaryMiddleware

WA='ws_'+'a'*32
WB='ws_'+'b'*32
IMAGE='/api/v1/asset-image-generation/assets/actor/authored-actor/slots/1/jobs'


def policy_file(root,workspaces=(WA,),**updates):
    """Write only the fixed private file in a task-owned root, without provider names, endpoints, prices or credentials."""
    path=root/'private/asset-image-admission.json';path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(canonical({'enabled':True,'workspace_ids':list(workspaces),**updates}),'utf8')
    return path


@pytest.mark.parametrize('changes',[{'enabled':1},{'schema_version':True},{'schema_version':2},{'workspace_ids':[WA,WA]},
    {'workspace_ids':['../private']},{'workspace_ids':[1]},{'api_key':'authored-no-key'},{'workspace_budget_minor':100}])
def test_policy_cannot_coerce_authority_scope_or_create_a_workspace_money_limit(changes):
    """The operator grant never accepts credential/endpoint/money fields or ambiguous version/scope values."""
    with pytest.raises(ValidationError):WorkspaceImageAdmissionConfiguration.model_validate(changes)


def test_default_missing_disabled_and_exact_owner_scope(tmp_path):
    """Missing/default/disabled policies stay closed; a valid policy covers only exact named workspace identities."""
    missing=ImageAdmissionPolicy(tmp_path);assert missing.current() and missing.current_ticket(WA) is None
    policy_file(tmp_path,enabled=False);disabled=ImageAdmissionPolicy(tmp_path)
    assert disabled.issue=='disabled' and disabled.current_ticket(WA) is None
    policy_file(tmp_path);enabled=ImageAdmissionPolicy(tmp_path);ticket=enabled.current_ticket(WA)
    assert isinstance(ticket,ImageAdmissionTicket) and enabled.current_ticket(WB) is None
    assert json.loads(ticket.checked_payload(WA))['workspace_ids']==[WA]
    assert ticket.grant.workspace_id==WA


@pytest.mark.parametrize('mode',['modified','removed','added-after-start','malformed','duplicate'])
def test_policy_changes_close_new_admission_without_relabelling_original_startup_history(tmp_path,mode):
    """Cached worker identity remains stable for history while byte changes cannot keep or newly enable public image admission."""
    if mode!='added-after-start':path=policy_file(tmp_path)
    else:path=tmp_path/'private/asset-image-admission.json'
    original=ImageAdmissionPolicy(tmp_path);ticket=original.startup_ticket(WA)
    if mode=='removed':path.unlink()
    elif mode=='modified':policy_file(tmp_path,(WB,))
    elif mode=='malformed':path.write_text('{"enabled":NaN}','utf8')
    elif mode=='duplicate':path.write_text('{"enabled":false,"enabled":true}','utf8')
    else:policy_file(tmp_path)
    assert not original.current() and original.current_ticket(WA) is None
    assert original.startup_ticket(WA)==ticket


@pytest.mark.parametrize('raw',[b'{"enabled":false,"enabled":true}',b'{"enabled":NaN}',b'{"enabled":true,"workspace_ids":["ws_bad"]}',b'x'*16385])
def test_invalid_bounded_operator_file_never_grants_or_reflects_private_input(tmp_path,raw):
    """Malformed, duplicated and oversized fields fail closed before any worker, model configuration or secret discovery."""
    path=policy_file(tmp_path);path.write_bytes(raw)
    policy=ImageAdmissionPolicy(tmp_path)
    assert policy.issue=='invalid_configuration' and policy.current_ticket(WA) is None


@pytest.mark.parametrize('method,path,allowed',[
    ('POST',IMAGE,True),('POST','/api/v1/asset-image-generation/jobs/original-job/retry',True),
    ('PUT',IMAGE,False),('POST','/api/v1/asset-image-generation/assets/arbitrary/id/slots/1/jobs',False),
    ('POST','/api/v1/execution/projects/project/submissions',False),('POST','/api/v1/asset-image-generation/jobs/id/callbacks',False)])
def test_image_admission_does_not_open_other_routes(method,path,allowed):
    """Only the two explicit image POST shapes are covered; callbacks, unsupported asset kinds and other model actions remain separate."""
    assert image_submission_path(method,path) is allowed


def test_original_actor_and_policy_audit_commit_atomically_and_cannot_be_replaced(tmp_path):
    """Real SQLite preserves the original actor/body digest and non-secret policy with no hidden rowid or mutable companion receipt."""
    store=IdentityStore(tmp_path);account=store.provision('admission-owner','Authored-password-0042')
    workspace=account['workspace_id'];policy_file(tmp_path,(workspace,));policy=ImageAdmissionPolicy(tmp_path)
    identity={'workspace_id':workspace,'user':{'id':account['user_id']}}
    body=b'{"request_key":"authored-original"}'
    identifier=store.audit_start(identity,'POST',IMAGE,body,image_admission=policy.current_ticket(workspace))
    with store.connect() as db:
        audit=db.execute('SELECT * FROM request_audit WHERE id=?',(identifier,)).fetchone()
        saved=db.execute('SELECT * FROM image_admission_audit WHERE request_id=?',(identifier,)).fetchone()
        assert audit['body_sha256']==hashlib.sha256(body).hexdigest() and saved['workspace_id']==workspace and saved['submitted_by']==identity['user']['id']
        assert saved['policy_sha256']==policy.current_ticket(workspace).grant.policy_sha256
        assert hashlib.sha256(saved['policy_payload'].encode('utf8')).hexdigest()==saved['policy_sha256']
    store.audit_finish(identifier,201)
    for statement in ('UPDATE image_admission_audit SET policy_sha256=policy_sha256','DELETE FROM image_admission_audit',
        'INSERT OR REPLACE INTO image_admission_audit SELECT * FROM image_admission_audit'):
        with store.connect() as db:
            with pytest.raises(sqlite3.IntegrityError):db.execute(statement)
    with store.connect() as db:
        with pytest.raises(sqlite3.OperationalError):db.execute('SELECT rowid FROM image_admission_audit')
        before=db.execute('SELECT count(*) FROM request_audit').fetchone()[0]
    with pytest.raises(ValueError):store.audit_start(identity,'POST',IMAGE,body,image_admission=replace(policy.current_ticket(workspace),grant=WorkspaceImageAdmissionGrant(workspace_id=WB,policy_sha256='0'*64)))
    with store.connect() as db:assert db.execute('SELECT count(*) FROM request_audit').fetchone()[0]==before
    IdentityStore(tmp_path)


@pytest.mark.parametrize('method,path',[('PUT',IMAGE),('POST','/api/v1/execution/projects/project/submissions')])
def test_image_policy_witness_cannot_label_other_action_and_rolls_back_original_audit(tmp_path,method,path):
    """The durable policy journal independently rejects other actions, even if a trusted caller passes an image ticket to the wrong audit call."""
    store=IdentityStore(tmp_path);account=store.provision('scope-audit-owner','Authored-scope-audit-password')
    policy_file(tmp_path,(account['workspace_id'],));ticket=ImageAdmissionPolicy(tmp_path).current_ticket(account['workspace_id'])
    identity={'workspace_id':account['workspace_id'],'user':{'id':account['user_id']}}
    with pytest.raises(ValueError):store.audit_start(identity,method,path,b'{}',image_admission=ticket)
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM request_audit').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM image_admission_audit').fetchone()[0]==0


@pytest.mark.asyncio
@pytest.mark.parametrize('digest',[None,'0'*64,'correct'])
async def test_private_worker_requires_original_policy_header_after_fixed_token(digest):
    """An explicitly bound worker verifies both its original private token and exact policy digest before any authored image endpoint runs."""
    calls=[];grant=WorkspaceImageAdmissionGrant(workspace_id=WA,policy_sha256='a'*64)
    async def app(scope,receive,send):
        """Count only authored ASGI endpoint admission; no model, network service or candidate code executes."""
        calls.append(scope['path']);await send({'type':'http.response.start','status':200,'headers':[]});await send({'type':'http.response.body','body':b'{}'})
    bound=TenantBoundaryMiddleware(app,workspace_id=WA,internal_token='t'*48,image_admission_provider=lambda:grant)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=bound),base_url='http://worker.invalid') as client:
        ready=await client.get('/__workspace/ready',headers={'X-Workspace-Token':'t'*48})
        assert ready.status_code==200 and ready.json()['image_admission']==grant.model_dump(mode='json')
        headers={'X-Workspace-Token':'t'*48}
        if digest is not None:headers['X-Image-Admission-Policy']=grant.policy_sha256 if digest=='correct' else digest
        response=await client.post(IMAGE,headers=headers)
        assert response.status_code==(200 if digest=='correct' else 503) and len(calls)==(1 if digest=='correct' else 0)
        rejected=await client.post(IMAGE,headers={'X-Workspace-Token':'wrong','X-Image-Admission-Policy':grant.policy_sha256})
        assert rejected.status_code==403
