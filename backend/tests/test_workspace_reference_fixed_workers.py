"""Two real fixed workers, real native HTTP and reentrant signed material reads; authored sources, never vendor-quality or paid qualification."""
import asyncio
from contextlib import closing,ExitStack,contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import threading
import time
from urllib.parse import urlsplit
import urllib.request

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker,create_async_engine
import uvicorn

from app.controlplane.gateway import create_gateway
from app.core.contracts.model_accounting import ModelPriceConfiguration,ModelPriceEnvelope,ModelPriceRule
from app.core.contracts.seedance25 import MODEL_ID
from app.core.contracts.seedance_references import REFERENCE_PROTOCOL
from app.core.contracts.video_runtime_setup import VideoRuntimeConfiguration
from app.models.production import ProductionAsset,ProductionMedia
from app.models.studio import Chapter,Character,Scene,Shot,FileItem
from app.schemas.studio.production import VersionWrite
from app.services.studio.production import add_version
from tests.test_script_preparation import document

PROJECT='same-reference-project'
SOURCE='same-source'


@contextmanager
def reference_model(side):
    """Require this space's authored credential and fetch its actual material during the native POST, before replying."""
    state={'key':'authored-native-reference-'+side,'posts':[],'queries':0,'origin':None,'source':None}
    class Handler(BaseHTTPRequestHandler):
        """No actual model exists here; only request/credential/material boundaries are verified."""
        def log_message(self,*args):
            """Never log source bodies or capability URLs."""
            return None
        def do_POST(self):
            """Perform a real cookie-free callback through the gateway after original-attempt preparation committed."""
            body=json.loads(self.rfile.read(int(self.headers['content-length'])))
            record={'authenticated':self.headers.get('Authorization')=='Bearer '+state['key'],'material_matches':False}
            state['posts'].append(record)
            try:
                assert self.path=='/api/v3/contents/generations/tasks' and record['authenticated']
                urls=[item['video_url']['url'] for item in body['content'] if item.get('type')=='video_url'];assert len(urls)==1
                url=urls[0];assert url.startswith(state['origin']+'/api/v1/workspace-reference-media/')
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(url,timeout=12) as response:content=response.read(32*1024*1024+1)
                assert content==state['source']
                record['material_matches']=True;state['url']=url
                value={'id':'authored-scoped-reference-'+side};status=200
            except Exception as error:
                record['error_type']=type(error).__name__;value={'error':'Authored local reference callback failed'};status=503
            data=json.dumps(value).encode();self.send_response(status);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(data)));self.send_header('X-Request-Id','authored-reference-request-'+side)
            self.end_headers();self.wfile.write(data)
        def do_GET(self):
            """Keep the already accepted fixture task running so the original finite material capability can be inspected."""
            good=self.headers.get('Authorization')=='Bearer '+state['key'] and self.path=='/api/v3/contents/generations/tasks/authored-scoped-reference-'+side
            state['queries']+=1
            data=json.dumps({'id':'authored-scoped-reference-'+side,'model':MODEL_ID,'status':'running'} if good else {'error':'Unavailable'}).encode()
            self.send_response(200 if good else 404);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)))
            self.end_headers();self.wfile.write(data)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    state['port']=server.server_port
    try:yield state
    finally:
        server.shutdown();server.server_close();thread.join(3);assert not thread.is_alive()


class FixedReferenceSpaces:
    """Own only the newly provisioned fixture accounts, files, quotes, workers, native jobs and original links."""
    def __init__(self,app,origin,root,loop,upstreams,ffmpeg):
        """Keep every resource owned before any setup that can fail."""
        self.app,self.origin,self.root,self.loop,self.upstreams,self.ffmpeg=app,origin,root,loop,upstreams,ffmpeg
        self.clients={};self.identities={};self.quotes={};self.plans={};self.jobs={};self.bodies={}

    def ok(self,side,method,path,**kwargs):
        """Use actual authenticated TCP APIs with the exact visible identity/workspace headers."""
        response=self.clients[side].request(method,'/api/v1'+path,**kwargs)
        assert response.is_success,(method,path,response.status_code,response.text)
        return response.json()['data']

    def run(self,coroutine):
        """Execute pool ownership operations only on the actual gateway event loop."""
        return asyncio.run_coroutine_threadsafe(coroutine,self.loop).result(timeout=60)

    async def stop(self,workspace):
        """Join only this fixture's fixed worker before changing its own private operator files."""
        pool=self.app.state.workers
        async with pool.lock:
            worker=pool.workers.pop(workspace);await pool.stop_worker(worker)

    def source_rows(self,directory,side):
        """Generate two different original clips and seed only new project-owned metadata under the checked fixed database."""
        target=directory/'media'/'same-source.mp4';assert not target.exists()
        result=subprocess.run([str(self.ffmpeg),'-hide_banner','-loglevel','error','-f','lavfi','-i',
            f'color=c={"red" if side=="a" else "blue"}:s=480x854:r=24:d=2','-c:v','libx264','-pix_fmt','yuv420p','-an','-movflags','+faststart',str(target)],
            stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        assert result.returncode==0,'Trusted authored clip generation failed'
        raw=target.read_bytes();self.upstreams[side]['source']=raw
        async def seed():
            """Keep synthetic preparation facts separate from the production application session factory."""
            engine=create_async_engine('sqlite+aiosqlite:///'+(directory/'studio.db').as_posix())
            try:
                factory=async_sessionmaker(engine,expire_on_commit=False)
                async with factory() as db:
                    db.add(Chapter(id='c1',project_id=PROJECT,index=1,title='Authored chapter '+side,raw_text='original'))
                    db.add(Character(id='hero',project_id=PROJECT,name='Authored character '+side,description='identity',style='test'))
                    db.add(Scene(id='scene',name='Authored scene '+side,description='stable',style='test'));await db.flush()
                    asset=ProductionAsset(id='owned-scene',project_id=PROJECT,kind='scene',name='Authored scene '+side,entity_id='scene')
                    db.add(asset);await db.flush()
                    await add_version(db,asset,VersionWrite(expected_revision=0,description='Authored stable scene',media_ids=[]))
                    db.add(Shot(id='s1',chapter_id='c1',index=0,title='Authored shot '+side,status='ready'))
                    db.add(FileItem(id=SOURCE,type='video',name='Authored source '+side,storage_key='local-production/same-source.mp4'));await db.flush()
                    db.add(ProductionMedia(id=SOURCE,project_id=PROJECT,name='Authored source '+side,mime='video/mp4',size=len(raw),
                        sha256=hashlib.sha256(raw).hexdigest(),relative_path='same-source.mp4'));await db.commit()
            finally:await engine.dispose()
        asyncio.run(seed())

    def prepare(self):
        """Prepare complete manual plans through the actual APIs before two explicit private fixture submissions."""
        for side in ('a','b'):
            store=self.app.state.identity_store;store.provision('scoped-reference-'+side,'authored-scoped-reference-password')
            token=store.login('scoped-reference-'+side,'authored-scoped-reference-password','authored-local');identity=store.current(token);self.identities[side]=identity
            self.clients[side]=httpx.Client(base_url=self.origin,trust_env=False,follow_redirects=False,timeout=45,headers={
                'Cookie':'studio_session='+token,'X-Workspace-Id':identity['workspace_id'],'X-Identity-Id':identity['user']['id'],'Origin':self.origin})
            self.ok(side,'POST','/studio/projects',json={'id':PROJECT,'name':'Authored reference '+side,'style':'真人都市','visual_style':'现实'})
            self.ok(side,'PUT',f'/video-runtime/projects/{PROJECT}/budget',json={'expected_revision':0,'limit_minor':100})
            self.ok(side,'POST','/llm/providers',json={'id':'native-provider','name':'volcengine','base_url':f"http://127.0.0.1:{self.upstreams[side]['port']}/api/v3",
                'api_key':self.upstreams[side]['key'],'status':'testing'})
            self.ok(side,'POST','/llm/models',json={'id':'native-model','provider_id':'native-provider','name':MODEL_ID,'category':'video','params':{'native_video_protocol':REFERENCE_PROTOCOL}})
            directory=self.root/'workspaces'/identity['workspace_id'];self.run(self.stop(identity['workspace_id']))
            self.source_rows(directory,side)
            with closing(sqlite3.connect(directory/'studio.db')) as db:revision=db.execute("SELECT revision FROM provider_connection_versions WHERE provider_id='native-provider'").fetchone()[0]
            quote=ModelPriceEnvelope(model_id='native-model',model_name=MODEL_ID,provider_id='native-provider',provider_connection_version=revision,
                account_version='authored-reference-'+side,input_protocol=REFERENCE_PROTOCOL,max_call_minor=25,source_note='Authored full reference protocol upper bound; not a real supplier tariff')
            self.quotes[side]=quote;private=directory/'private';private.mkdir(exist_ok=True)
            (private/'model-prices.json').write_text(ModelPriceConfiguration(enabled=True,rules=[ModelPriceRule(quote=quote,kinds=['native_video'])]).model_dump_json(),'utf8')
            (private/'local-models.json').write_text(json.dumps({'schema_version':1,'workspace_id':identity['workspace_id'],'enabled':True,'native_video_port':self.upstreams[side]['port']}),'utf8')
            (private/'native-video.json').write_text(VideoRuntimeConfiguration(enabled=True,model_id=quote.model_id,service_version='authored-scoped-reference',
                account_version=quote.account_version,reference_media_origin=self.origin).model_dump_json(),'utf8')
            setup=self.ok(side,'GET','/video-runtime/setup');assert setup['enabled'] and setup['running'] and setup['reference_video_delivery_available'],setup
            script=self.ok(side,'POST',f'/script-preparation/projects/{PROJECT}/drafts',json={'chapter_id':'c1','document':document().model_dump(mode='json')})
            published=self.ok(side,'POST',f"/script-preparation/projects/{PROJECT}/drafts/{script['id']}/publish",json={'expected_revision':1})
            versions=[self.ok(side,'POST',f'/shot-plans/projects/{PROJECT}/asset-registrations',json={'kind':kind,'entity_id':entity})['version_id'] for kind,entity in [('character','hero'),('scene','scene')]]
            selected={'schema_version':1,'mode':'reference','references':[{'media_id':SOURCE,'role':'reference_video','expected_sha256':hashlib.sha256(self.upstreams[side]['source']).hexdigest()}]}
            draft=self.ok(side,'POST',f'/shot-plans/projects/{PROJECT}/drafts',json={'chapter_id':'c1','document':{'schema_version':1,'script_snapshot_id':published['id'],
                'model_id':MODEL_ID,'shots':[{'shot_id':'s1','version_ids':versions,'reference_media_ids':[],'video_reference':selected}]}})
            plan=self.ok(side,'POST',f"/shot-plans/projects/{PROJECT}/drafts/{draft['id']}/compile",json={'expected_revision':1})
            assert '?token=' not in json.dumps(plan) and self.upstreams[side]['key'] not in json.dumps(plan)
            self.plans[side]=plan['id'];self.bodies[side]={'request_key':'same-reference-request','confirm_project_charges':True,'expected_price_fingerprint':quote.fingerprint}
        for side in ('a','b'):
            self.jobs[side]=self.private_submit(side)
        deadline=time.monotonic()+35
        while time.monotonic()<deadline:
            states={side:self.ok(side,'GET',f'/execution/projects/{PROJECT}/jobs/{self.jobs[side]}') for side in ('a','b')}
            if all(row['state']=='running' and self.upstreams[side].get('url') for side,row in states.items()):return
            assert all(row['state'] not in {'failed','cancelled'} for row in states.values()),states
            time.sleep(.05)
        raise AssertionError('Actual scoped reference fixture did not reach running')

    def submission_path(self,side):
        """Use the supported per-shot prepared-plan contract; direct reference submission is not added implicitly."""
        return f'/shot-plans/projects/{PROJECT}/plans/{self.plans[side]}/shots/s1/submissions'

    def private_submit(self,side):
        """A trusted test supervisor exercises one explicitly confirmed worker action; the public gate stays closed."""
        identity=self.identities[side];worker=self.app.state.workers.workers[identity['workspace_id']]
        with httpx.Client(trust_env=False,follow_redirects=False,timeout=30) as client:
            response=client.post(f'http://127.0.0.1:{worker.port}/api/v1'+self.submission_path(side),json=self.bodies[side],
                headers={'X-Workspace-Token':worker.token,'X-Identity-Id':identity['user']['id']})
        assert response.status_code==201,response.text
        return response.json()['data']['job_id']


@pytest.fixture(scope='module')
def reference_spaces(tmp_path_factory):
    """Own one actual gateway thread/loop, two native worker trees and two bounded local protocol servers, joining all on every exit."""
    ffmpeg=Path(os.environ.get('FAWNIMO_TEST_FFMPEG',''))
    if not ffmpeg.is_file():pytest.skip('Trusted local FFmpeg is required for actual source verification')
    root=tmp_path_factory.mktemp('workspace-reference-fixed');listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(128)
    origin=f'http://127.0.0.1:{listener.getsockname()[1]}'
    app=create_gateway(root,probe=ffmpeg,secure_cookies=False,public_origin=origin,reference_media_origin=origin,capacity=2)
    server=uvicorn.Server(uvicorn.Config(app,log_level='warning',proxy_headers=False,timeout_graceful_shutdown=5,timeout_keep_alive=2))
    holder={}
    def serve():
        """Capture the only gateway event loop before serving its owned socket."""
        async def run():
            """Bind every pool/request/cleanup operation to the same application lifespan."""
            holder['loop']=asyncio.get_running_loop();await server.serve(sockets=[listener])
        asyncio.run(run())
    with ExitStack() as stack:
        upstreams={side:stack.enter_context(reference_model(side)) for side in ('a','b')}
        for state in upstreams.values():state['origin']=origin
        thread=threading.Thread(target=serve,daemon=True);thread.start();value=None
        try:
            deadline=time.monotonic()+10
            while not server.started and thread.is_alive() and time.monotonic()<deadline:time.sleep(.01)
            assert server.started and thread.is_alive()
            value=FixedReferenceSpaces(app,origin,root,holder['loop'],upstreams,ffmpeg);value.prepare();yield value
        finally:
            if value is not None:
                for client in value.clients.values():client.close()
            server.should_exit=True;thread.join(20)
            if thread.is_alive():server.force_exit=True;thread.join(5)
            listener.close()
            assert not thread.is_alive() and not app.state.workers.workers


@pytest.mark.parametrize('side',['a','b'])
def test_actual_fixed_worker_prepares_original_reference_and_cookie_free_supplier_read(reference_spaces,side):
    """Real native POST callbacks read the original space's exact clip and leave one unknown25 hold only there."""
    value=reference_spaces;upstream=value.upstreams[side]
    assert len(upstream['posts'])==1 and upstream['posts'][0]['authenticated'] and upstream['posts'][0]['material_matches']
    url=upstream['url'];assert value.identities[side]['workspace_id'] in url
    with httpx.Client(trust_env=False,follow_redirects=False) as external:
        response=external.get(url);head=external.head(url);partial=external.get(url,headers={'Range':'bytes=2-6'})
    assert response.status_code==200 and response.content==upstream['source']
    assert head.status_code==200 and head.content==b'' and int(head.headers['content-length'])==len(upstream['source'])
    assert partial.status_code==206 and partial.content==upstream['source'][2:7]
    budget=value.ok(side,'GET',f'/video-runtime/projects/{PROJECT}/budget')
    assert budget['reserved_minor']==25 and budget['spent_minor']==0 and budget['limit_minor']==100 and not budget['provider_bill_verified']
    assert value.private_submit(side)==value.jobs[side] and len(upstream['posts'])==1
    assert value.clients[side].post('/api/v1'+value.submission_path(side),json=value.bodies[side]).status_code==503
    assert len(upstream['posts'])==1


def test_actual_provider_link_cannot_change_to_another_colliding_space_or_browse_its_data(reference_spaces):
    """Both spaces have the same business IDs; changing only scope invalidates the original capability before private dispatch."""
    value=reference_spaces
    assert value.upstreams['a']['source']!=value.upstreams['b']['source']
    url=value.upstreams['a']['url'];forged=url.replace(value.identities['a']['workspace_id'],value.identities['b']['workspace_id'])
    with httpx.Client(trust_env=False,follow_redirects=False) as external:
        assert external.get(forged).status_code==404
        assert external.get(value.origin+'/api/v1/studio/projects?'+urlsplit(url).query).status_code==401
    assert value.ok('b','GET',f'/execution/projects/{PROJECT}/jobs/{value.jobs["b"]}')['id']==value.jobs['b']


def test_real_ready_worker_material_read_stays_available_during_unrelated_startup_admission(reference_spaces):
    """Hold the real pool's creation lock while an existing worker serves its original signed file over actual TCP."""
    value=reference_spaces;entered=threading.Event();release=threading.Event()
    async def startup_owner():
        """Represent only ownership of startup admission, without launching or killing an unrelated process."""
        async with value.app.state.workers.lock:
            entered.set()
            while not release.is_set():await asyncio.sleep(.01)
    future=asyncio.run_coroutine_threadsafe(startup_owner(),value.loop)
    try:
        assert entered.wait(3)
        with httpx.Client(trust_env=False,follow_redirects=False,timeout=4) as client:
            response=client.get(value.upstreams['a']['url'])
        assert response.status_code==200 and response.content==value.upstreams['a']['source']
    finally:
        release.set();future.result(timeout=5)


def test_expired_gateway_capability_does_not_consume_an_actual_worker_read_or_release_fees(reference_spaces):
    """An explicitly advanced trusted fixture clock rejects before dispatch; no real hour or supplier refund is claimed."""
    value=reference_spaces;identity=value.identities['a'];database=value.root/'workspaces'/identity['workspace_id']/'studio.db'
    def count():
        """Read only the original owned attempt's access counter through a fresh SQLite snapshot."""
        with closing(sqlite3.connect(database)) as db:return db.execute('SELECT access_count FROM execution_reference_deliveries').fetchone()[0]
    before=count();authority=value.app.state.reference_delivery_authority;clock=authority.clock
    authority.clock=lambda:clock()+3601
    try:
        with httpx.Client(trust_env=False,follow_redirects=False) as client:assert client.get(value.upstreams['a']['url']).status_code==404
    finally:authority.clock=clock
    assert count()==before
    budget=value.ok('a','GET',f'/video-runtime/projects/{PROJECT}/budget')
    assert budget['reserved_minor']==25 and budget['spent_minor']==0 and len(value.upstreams['a']['posts'])==1


def test_original_actual_attempt_allows_only_sixteen_reads_and_keeps_its_unknown_charge(reference_spaces):
    """Exhaust the real SQLite capability counter; a seventeenth read cannot release or submit any model work."""
    value=reference_spaces;database=value.root/'workspaces'/value.identities['a']['workspace_id']/'studio.db'
    with closing(sqlite3.connect(database)) as db:before=db.execute('SELECT access_count FROM execution_reference_deliveries').fetchone()[0]
    assert 0<before<16
    with httpx.Client(trust_env=False,follow_redirects=False) as client:
        for _ in range(16-before):
            response=client.get(value.upstreams['a']['url']);assert response.status_code==200 and response.content==value.upstreams['a']['source']
        assert client.get(value.upstreams['a']['url']).status_code==404
    with closing(sqlite3.connect(database)) as db:assert db.execute('SELECT access_count FROM execution_reference_deliveries').fetchone()[0]==16
    budget=value.ok('a','GET',f'/video-runtime/projects/{PROJECT}/budget')
    assert budget['reserved_minor']==25 and budget['spent_minor']==0 and len(value.upstreams['a']['posts'])==1


def test_original_reference_never_serves_replacement_bytes_from_the_other_space(reference_spaces):
    """Replace only the authored B file with A bytes; its old proof cannot authorize the new bytes or change either fee record."""
    value=reference_spaces;target=value.root/'workspaces'/value.identities['b']['workspace_id']/'media'/'same-source.mp4'
    original=target.read_bytes();target.write_bytes(value.upstreams['a']['source'])
    try:
        with httpx.Client(trust_env=False,follow_redirects=False) as client:response=client.get(value.upstreams['b']['url'])
        assert response.status_code!=200 and response.content!=value.upstreams['a']['source']
    finally:target.write_bytes(original)
    for side in ('a','b'):
        budget=value.ok(side,'GET',f'/video-runtime/projects/{PROJECT}/budget')
        assert budget['reserved_minor']==25 and budget['spent_minor']==0 and len(value.upstreams[side]['posts'])==1
