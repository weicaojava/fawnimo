"""Actual authenticated gateway/two fixed workers with authored local GPT Image origins and per-project holds; no paid/real-provider/public qualification."""
import base64
from contextlib import closing,ExitStack,contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import time
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image
import pytest
import httpx
from app.controlplane.gateway import create_gateway
from app.controlplane.store import IdentityStore
from app.controlplane.image_admission import canonical
from app.core.contracts.asset_image_protocols import GPT_IMAGE_PROTOCOL
from app.core.contracts.model_accounting import ModelPriceConfiguration,ModelPriceEnvelope,ModelPriceRule
from tests.test_script_task_workspaces import ScriptSpaces

MODEL='gpt-image-2.5-sunburst-2026-09-08'
PROJECTS=('same-project','second-project')


def png(size,color):
    """Draw original fixture bytes with distinct per-actor colors rather than using user or remote media."""
    output=BytesIO();Image.new('RGB',size,color).save(output,'PNG');return output.getvalue()


@contextmanager
def image_origin(side):
    """Serve only a bounded authored image protocol on this fixture's owned IPv4 loopback socket."""
    state={'key':'identity-image-authored-'+side,'calls':[],'png':png((1024,1024),(160,30,20) if side=='a' else (20,40,160))}
    class Handler(BaseHTTPRequestHandler):
        """Count exact sent reference hashes without logging headers, prompts or credential values."""
        def log_message(self,*_args):
            """Keep fixture authentication out of terminal output."""
        def do_POST(self):
            """Return authored PNG bytes only after the exact own key and declared local route are observed."""
            raw=self.rfile.read(int(self.headers['Content-Length']));body=json.loads(raw)
            authenticated=self.headers.get('Authorization')=='Bearer '+state['key']
            refs=[hashlib.sha256(base64.b64decode(item['image_url'].split(',',1)[1],validate=True)).hexdigest() for item in body.get('images',[])]
            state['calls'].append({'path':self.path,'authenticated':authenticated,'references':refs})
            if not authenticated or self.path not in {'/v1/images/generations','/v1/images/edits'}:self.send_error(403);return
            data=json.dumps({'data':[{'b64_json':base64.b64encode(state['png']).decode()}]}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)))
            self.send_header('X-Request-Id','authored-identity-image-'+side+'-'+str(len(state['calls'])))
            self.end_headers();self.wfile.write(data)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    state.update(port=server.server_port,origin=f'http://127.0.0.1:{server.server_port}/v1')
    try:yield state
    finally:server.shutdown();server.server_close();thread.join(timeout=2)


class ImageSpaces(ScriptSpaces):
    """Use the public cookie-authenticated gateway for every image request, with equal project/model/actor IDs in two actual private databases."""
    def __init__(self,app,client,root,origins,accounts):
        """Prepare separate model connections/quotes/refs and consume each original project's exact15-unit authored ceiling independently."""
        self.app,self.client,self.root,self.origins=app,client,root,origins
        self.identities,self.tokens,self.quotes,self.jobs,self.intents,self.references={},{},{},{},{},{}
        self.policy_file=root/'private/asset-image-admission.json'
        for side in ('a','b'):
            store=app.state.identity_store
            token=store.login(accounts[side]['account'],'Authored-image-gateway-password','authored-local')
            self.tokens[side]=token;self.identities[side]=store.current(token)
            self.jobs[side]={};self.intents[side]={};self.references[side]={}
            for index,project in enumerate(PROJECTS):
                self.ok(side,'POST','/studio/projects',json={'id':project,'name':'Authored '+side+' '+project,'style':'真人都市'})
                self.ok(side,'PUT',f'/video-runtime/projects/{project}/budget',json={'expected_revision':0,'limit_minor':15})
                content=png((128,160),(170,30+index,20) if side=='a' else (20,30+index,170))
                uploaded=self.ok(side,'POST',f'/studio/production/projects/{project}/media',files={'file':('authored-'+side+'.png',content,'image/png')})[0]
                self.references[side][project]={'media_id':uploaded['id'],'expected_sha256':uploaded['sha256'],'purpose':'appearance'}
            self.ok(side,'POST','/llm/providers',json={'id':'same-provider','name':'OpenAI','base_url':origins[side]['origin'],'api_key':origins[side]['key'],'status':'testing'})
            self.ok(side,'POST','/llm/models',json={'id':'same-image-model','provider_id':'same-provider','name':MODEL,'category':'image','params':{'asset_image_protocol':GPT_IMAGE_PROTOCOL}})
            self.ok(side,'POST','/studio/entities/actor',json={'id':'same-actor','project_id':PROJECTS[0],'name':'Authored '+side})
            slot=self.ok(side,'POST','/studio/entities/actor/same-actor/images',json={})['id']
            directory=self.directory(side)
            with closing(sqlite3.connect(directory/'studio.db')) as db:
                revision=db.execute("SELECT revision FROM provider_connection_versions WHERE provider_id='same-provider'").fetchone()[0]
            quote=ModelPriceEnvelope(model_id='same-image-model',model_name=MODEL,provider_id='same-provider',provider_connection_version=revision,
                account_version='authored-image-'+side,max_call_minor=15,source_note='Authored local protocol fixture; no supplier tariff or paid authorization')
            self.quotes[side]=quote
            private=directory/'private';private.mkdir(exist_ok=True)
            (private/'model-prices.json').write_text(ModelPriceConfiguration(enabled=True,rules=[ModelPriceRule(quote=quote,kinds=['asset_image'])]).model_dump_json(),'utf8')
            (private/'local-models.json').write_text(canonical({'schema_version':1,'workspace_id':self.identities[side]['workspace_id'],'enabled':True,'asset_image_ports':[origins[side]['port']]}),'utf8')
            client.portal.call(self.restart,self.identities[side]['workspace_id'])
            availability=self.ok(side,'GET','/asset-image-generation/availability',params={'model_id':'same-image-model'})
            assert availability['available'] and availability['models'][0]['protocol']==GPT_IMAGE_PROTOCOL
            for project in PROJECTS:
                path=f'/asset-image-generation/assets/actor/same-actor/slots/{slot}/jobs'
                body={'request_key':'same-image-'+project,'project_id':project,'model_id':'same-image-model','prompt':'Authored identity image '+side,
                    'expected_price_fingerprint':quote.fingerprint,'size':'1024x1024','references':[self.references[side][project]],'confirm_model_call':True}
                self.intents[side][project]=(path,body);self.jobs[side][project]=self.ok(side,'POST',path,json=body)
                # The previous accepted attempt owns bounded reference validation.
                # Wait for its actual candidate before another positive setup request;
                # no busy rejection is ignored and no request key is replaced.
                for _ in range(200):
                    prepared=self.ok(side,'GET','/asset-image-generation/jobs/'+self.jobs[side][project]['id'])
                    if prepared['status'] not in {'queued','running'}:break
                    time.sleep(.05)
                assert prepared['status']=='candidate',(side,project,prepared)
                self.jobs[side][project]=prepared
        for side in ('a','b'):
            for project in PROJECTS:
                identity=self.jobs[side][project]['id']
                for _ in range(200):
                    row=self.ok(side,'GET','/asset-image-generation/jobs/'+identity)
                    if row['status'] not in {'queued','running'}:break
                    time.sleep(.05)
                assert row['status']=='candidate',(side,project,row)
                self.jobs[side][project]=row

    def directory(self,side):
        """Address only this fixture's fixed private worker root, never a caller path or original workspace."""
        return self.root/'workspaces'/self.identities[side]['workspace_id']

    def counts(self):
        """Read only bounded counters from the two owned authored servers."""
        return [len(self.origins[side]['calls']) for side in ('a','b')]


@pytest.fixture(scope='module')
def image_spaces(tmp_path_factory):
    """Own two real worker process trees, two synthetic model origins and a new control/data root, closing every resource at fixture end."""
    root=tmp_path_factory.mktemp('identity-image-gateway');store=IdentityStore(root)
    accounts={side:store.provision('image-admission-'+side,'Authored-image-gateway-password') for side in ('a','b')}
    private=root/'private';private.mkdir()
    (private/'asset-image-admission.json').write_text(canonical({'enabled':True,'workspace_ids':[accounts[side]['workspace_id'] for side in ('a','b')]}),'utf8')
    probe=os.environ.get('TEST_MEDIA_PROBE') or shutil.which('ffmpeg')
    assert probe and Path(probe).is_file(),'Trusted local decoder required for actual project-reference upload'
    with ExitStack() as stack:
        origins={side:stack.enter_context(image_origin(side)) for side in ('a','b')}
        app=create_gateway(root,secure_cookies=False,public_origin='http://script-task.local',capacity=2,probe=Path(probe))
        with TestClient(app,base_url='http://script-task.local') as client:yield ImageSpaces(app,client,root,origins,accounts)


@pytest.mark.parametrize('side',['a','b'])
def test_public_gateway_executes_only_original_actor_models_refs_and_project_holds(image_spaces,side):
    """Actual public cookie/header routing reaches the correct separate worker/model, and each15-unit project succeeds without consuming its sibling's ceiling."""
    spaces=image_spaces;other='b' if side=='a' else 'a'
    for project in PROJECTS:
        row=spaces.jobs[side][project]
        assert row['project_id']==project and row['status']=='candidate' and len(row['references'])==1
        assert row['references'][0]['sha256']==spaces.references[side][project]['expected_sha256']
        content=spaces.request(side,'GET',row['content_url'].removeprefix('/api/v1'))
        assert content.status_code==200 and content.content==spaces.origins[side]['png']
        assert spaces.request(other,'GET','/asset-image-generation/jobs/'+row['id']).status_code==404
        assert spaces.request(other,'GET',row['content_url'].removeprefix('/api/v1')).status_code==404
        budget=spaces.ok(side,'GET',f'/video-runtime/projects/{project}/budget')
        assert budget['limit_minor']==15 and budget['reserved_minor']==15 and budget['spent_minor']==0
        old=spaces.ok(side,'POST',spaces.intents[side][project][0],json=spaces.intents[side][project][1])
        assert old['id']==row['id']
    assert spaces.counts()==[2,2]
    assert all(call['authenticated'] and call['path']=='/v1/images/edits' for call in spaces.origins[side]['calls'])
    assert {call['references'][0] for call in spaces.origins[side]['calls']}=={spaces.references[side][project]['expected_sha256'] for project in PROJECTS}
    with closing(sqlite3.connect(spaces.directory(side)/'studio.db')) as db:
        assert db.execute('SELECT DISTINCT submitted_by FROM model_charge_batches').fetchall()==[(spaces.identities[side]['user']['id'],)]
        assert db.execute('SELECT count(*) FROM model_call_charges').fetchone()[0]==2


@pytest.mark.parametrize('side',['a','b'])
def test_same_project_over_limit_refuses_a_new_request_and_preserves_other_project(image_spaces,side):
    """A positive operator policy never overrides the selected project's already fully reserved ceiling."""
    spaces=image_spaces;path,body=spaces.intents[side][PROJECTS[0]]
    response=spaces.request(side,'POST',path,json={**body,'request_key':'over-original-project-limit'})
    assert response.status_code==409 and spaces.counts()==[2,2]
    for project in PROJECTS:assert spaces.ok(side,'GET',f'/video-runtime/projects/{project}/budget')['reserved_minor']==15


@pytest.mark.parametrize('side',['a','b'])
def test_forged_scope_and_policy_headers_cannot_select_another_worker(image_spaces,side):
    """Cookie ownership, not caller-supplied scope/policy/internal headers, determines the actual worker and immutable original key."""
    spaces=image_spaces;other='b' if side=='a' else 'a';path,body=spaces.intents[side][PROJECTS[0]]
    wrong=spaces.request(side,'POST',path,json=body,headers={'X-Workspace-Id':spaces.identities[other]['workspace_id']})
    assert wrong.status_code==409
    old=spaces.ok(side,'POST',path,json=body,headers={'X-Image-Admission-Policy':'0'*64,'X-Workspace-Token':'browser-forgery','X-Workspace-Role':'owner'})
    assert old['id']==spaces.jobs[side][PROJECTS[0]]['id'] and spaces.counts()==[2,2]
    worker=spaces.app.state.workers.workers[spaces.identities[side]['workspace_id']]
    with httpx.Client(trust_env=False,follow_redirects=False) as private:
        rejected=private.post(f'http://127.0.0.1:{worker.port}/api/v1'+path,json=body,
            headers={'X-Workspace-Token':worker.token,'X-Identity-Id':spaces.identities[side]['user']['id'],'X-Image-Admission-Policy':'0'*64})
    assert rejected.status_code==503 and spaces.counts()==[2,2]


def test_unlisted_workspace_and_anonymous_requests_stop_before_worker_discovery(image_spaces):
    """Another valid login cannot borrow listed spaces; even a guessed policy header is refused before worker/model discovery."""
    spaces=image_spaces;store=spaces.app.state.identity_store
    account=store.provision('image-admission-unlisted','Authored-image-unlisted-password')
    token=store.login(account['account'],'Authored-image-unlisted-password','authored-local')
    identity=store.current(token);path,body=spaces.intents['a'][PROJECTS[0]]
    async def forbidden(_workspace):
        """Make a false positive from a full worker pool impossible: unlisted admission must stop before calling this method."""
        pytest.fail('Unlisted image request reached worker discovery')
    with patch.object(spaces.app.state.workers,'get',new=forbidden):
        response=spaces.client.post('/api/v1'+path,json=body,headers={'Cookie':'studio_session='+token,'X-Workspace-Id':identity['workspace_id'],
            'X-Identity-Id':identity['user']['id'],'Origin':'http://script-task.local','X-Image-Admission-Policy':'0'*64})
        assert response.status_code==503
        anonymous=spaces.client.post('/api/v1'+path,json=body,headers={'Origin':'http://script-task.local'})
        assert anonymous.status_code==401
    assert spaces.counts()==[2,2]


def test_current_policy_change_blocks_new_admission_but_preserves_history_and_holds(image_spaces):
    """A changed private file cannot keep authorizing cached workers; old GET history and original project fees remain available."""
    spaces=image_spaces;original=spaces.policy_file.read_bytes();path,body=spaces.intents['a'][PROJECTS[0]]
    try:
        spaces.policy_file.write_bytes(original+b'\n')
        assert spaces.request('a','POST',path,json=body).status_code==503
        availability=spaces.ok('a','GET','/asset-image-generation/availability',params={'model_id':'same-image-model'})
        assert availability['available'] is False
        assert spaces.ok('a','GET','/asset-image-generation/jobs/'+spaces.jobs['a'][PROJECTS[0]]['id'])['status']=='candidate'
        assert spaces.ok('a','GET',f'/video-runtime/projects/{PROJECTS[0]}/budget')['reserved_minor']==15
        assert spaces.counts()==[2,2]
    finally:spaces.policy_file.write_bytes(original)


def test_viewer_cannot_use_operator_policy_to_submit_or_spoof_role(image_spaces):
    """Current membership and origin gates still precede the new optional image admission."""
    spaces=image_spaces;who=spaces.identities['a'];path,body=spaces.intents['a'][PROJECTS[0]];store=spaces.app.state.identity_store
    with store.connect() as db:db.execute("UPDATE memberships SET role='viewer' WHERE user_id=? AND workspace_id=?",(who['user']['id'],who['workspace_id']))
    try:assert spaces.request('a','POST',path,json=body,headers={'X-Workspace-Role':'owner'}).status_code==403
    finally:
        with store.connect() as db:db.execute("UPDATE memberships SET role='owner' WHERE user_id=? AND workspace_id=?",(who['user']['id'],who['workspace_id']))
    assert spaces.counts()==[2,2]


def test_other_paid_models_callbacks_and_original_actor_policy_audit_stay_separate(image_spaces):
    """Image opt-in does not authorize text/video/callback paths, and each accepted image-route request has one immutable trusted policy witness."""
    spaces=image_spaces
    for path in ('/agent-execution/models/model/connection-tests','/execution/projects/same-project/submissions','/asset-image-generation/jobs/job/callbacks'):
        assert spaces.request('a','POST',path,json={}).status_code==503
    with spaces.app.state.identity_store.connect() as db:
        rows=db.execute('SELECT a.workspace_id,a.submitted_by,a.body_sha256,p.policy_sha256,p.policy_payload FROM request_audit a JOIN image_admission_audit p ON p.request_id=a.id').fetchall()
        assert rows and {row['workspace_id'] for row in rows}=={spaces.identities[side]['workspace_id'] for side in ('a','b')}
        for row in rows:
            side=next(side for side in ('a','b') if spaces.identities[side]['workspace_id']==row['workspace_id'])
            assert row['submitted_by']==spaces.identities[side]['user']['id'] and hashlib.sha256(row['policy_payload'].encode()).hexdigest()==row['policy_sha256']
    assert spaces.counts()==[2,2]
