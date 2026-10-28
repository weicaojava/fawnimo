"""Independent S25A acceptance using actual loopback HTTP, file SQLite and authored MP4; zero models."""
import asyncio
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import socket
import subprocess
from threading import Thread
from types import SimpleNamespace
from urllib.parse import urlsplit,parse_qs
import uuid
import pytest
from fastapi import HTTPException
from sqlalchemy import select,func,update
from app.config import settings
from app.core.contracts.execution_capabilities import Capability
from app.core.contracts.execution_binding import binding_fingerprint
from app.models.execution import ExecutionJob,SubmissionAttempt
from app.models.execution_budget import ExecutionBudgetEntry,ExecutionBudget
from app.models.production import ProductionAsset,ProductionMedia,ProductionTake
from app.services.worker.execution_admission import AdmissionPolicy
from app.services.worker.durable_execution import DurableExecution
from app.services.worker.execution_artifacts import publish_artifact
from app.services.shot_plan.service import admit_planned_shot
from app.services.studio.production import add_version
from app.schemas.studio.production import VersionWrite

ROOT=Path(__file__).resolve().parents[5]
FFMPEG=ROOT/'work/ffmpeg.exe'
MODEL='doubao-seedance-2-5-260628'
CREATE='/api/v3/contents/generations/tasks'
REMOTE='fixture_seedance_001'


class FixtureHTTP:
    """Own two real local origins and record only non-secret request evidence."""
    def __init__(self,video):
        """Generate fake credential/capability sentinels independent of every user configuration."""
        self.video=video;self.key='FAKE-CONTROL-'+uuid.uuid4().hex;self.signature='FAKE-SIGNED-'+uuid.uuid4().hex
        self.create_mode='success';self.query_mode='succeeded';self.download_mode='video';self.retry_after='5'
        self.posts=0;self.queries=0;self.downloads=0;self.requests=[];self.control_port=0;self.artifact_port=0;self.separate_artifact=False

    def json_reply(self,handler,status,payload,headers=None):
        """Send bounded protocol fixture JSON, which is never a real provider response."""
        raw=json.dumps(payload).encode();self.bytes_reply(handler,status,raw,headers)

    def bytes_reply(self,handler,status,raw,headers=None):
        """Write exact bytes and explicit lengths without logging URLs or credential data."""
        handler.send_response(status);handler.send_header('Content-Length',str(len(raw)))
        for key,value in (headers or {}).items():handler.send_header(key,value)
        handler.end_headers()
        try:handler.wfile.write(raw)
        except (BrokenPipeError,ConnectionResetError,OSError):pass

    def handle(self,handler,role):
        """Implement only the frozen local create/query/download subset and configured faults."""
        path=urlsplit(handler.path).path
        auth=handler.headers.get('Authorization')
        length=int(handler.headers.get('Content-Length','0'));raw=handler.rfile.read(length) if length else b''
        try:body=json.loads(raw) if raw else None
        except ValueError:body=None
        self.requests.append({'role':('artifact' if path=='/video.mp4' else role),'method':handler.command,'path':path,'auth_present':auth is not None,
            'auth_matches':auth=='Bearer '+self.key,'cookie_present':handler.headers.get('Cookie') is not None,
            'idempotency_present':any(handler.headers.get(k) for k in ['Idempotency-Key','X-Request-ID']), 'query_capability_matches':parse_qs(urlsplit(handler.path).query).get('token')==[self.signature], 'body':body})
        if role=='artifact' or path=='/video.mp4':
            self.downloads+=1
            if self.download_mode=='expired':return self.bytes_reply(handler,403,self.signature.encode())
            if self.download_mode=='corrupt':return self.bytes_reply(handler,200,b'not a decodable MP4')
            if self.download_mode=='redirect-private':
                return self.bytes_reply(handler,302,b'',{'Location':'http://169.254.169.254/latest/meta-data'})
            return self.bytes_reply(handler,200,self.video,{'Content-Type':'video/mp4'})
        if handler.command=='POST' and path==CREATE:
            self.posts+=1
            if self.create_mode=='disconnect':
                handler.close_connection=True
                try:handler.connection.shutdown(socket.SHUT_RDWR)
                except OSError:pass
                handler.connection.close();return
            if self.create_mode=='429':return self.json_reply(handler,429,{'error':{'message':self.key}},{'Retry-After':self.retry_after})
            if self.create_mode=='missing-id':return self.json_reply(handler,200,{'message':self.key})
            if self.create_mode=='duplicate-json':return self.bytes_reply(handler,200,b'{"id":"one","id":"two"}')
            if self.create_mode=='redirect':return self.bytes_reply(handler,307,b'',{'Location':f'http://127.0.0.1:{self.artifact_port}/video.mp4'})
            return self.json_reply(handler,200,{'id':REMOTE})
        if handler.command=='GET' and path==CREATE+'/'+REMOTE:
            self.queries+=1
            if self.query_mode=='429':return self.json_reply(handler,429,{'error':{'message':self.key}},{'Retry-After':self.retry_after})
            if self.query_mode=='redirect':return self.bytes_reply(handler,302,b'',{'Location':f'http://127.0.0.1:{self.artifact_port}/video.mp4'})
            result={'id':REMOTE,'model':MODEL,'status':self.query_mode}
            if self.query_mode=='succeeded':result['content']={'video_url':f'http://127.0.0.1:{self.artifact_port if self.separate_artifact else self.control_port}/video.mp4?token={self.signature}'}
            if self.query_mode=='wrong-model':result.update(model='another-model',status='succeeded')
            if self.query_mode=='wrong-id':result.update(id='foreign_task',status='succeeded')
            if self.query_mode=='failed':result['error']={'message':self.key+' '+self.signature}
            return self.json_reply(handler,200,result)
        return self.json_reply(handler,404,{'error':'Fixture path not defined'})


@contextmanager
def real_servers(video):
    """Start exact owned control/artifact endpoints and join only these fixture threads."""
    evidence=FixtureHTTP(video);servers=[];threads=[]
    def handler_for(role):
        """Build role-specific handlers with no ordinary access log or private data print."""
        class Handler(BaseHTTPRequestHandler):
            """A genuine HTTP handler, without replacing the product network client."""
            def do_GET(self):
                """Route one control query or artifact download."""
                evidence.handle(self,role)
            def do_POST(self):
                """Route one submit attempt, including acceptance-response-loss fixtures."""
                evidence.handle(self,role)
            def do_DELETE(self):
                """Record prohibited deletion attempts so an independent assertion can catch them."""
                evidence.handle(self,role)
            def log_message(self,*args):
                """Keep local fixture logs free of bearer headers and signed query strings."""
                return
        return Handler
    try:
        for role in ['artifact','control']:
            server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(role));server.daemon_threads=True
            setattr(evidence,role+'_port',server.server_port);thread=Thread(target=server.serve_forever,daemon=True)
            servers.append(server);threads.append(thread);thread.start()
        yield evidence
    finally:
        for server in servers:server.shutdown();server.server_close()
        for thread in threads:thread.join(timeout=3);assert not thread.is_alive()


def authored_mp4(tmp_path):
    """Create actual known video bytes, never model-generated or quality-qualified material."""
    path=tmp_path/'authored-fixture.mp4'
    subprocess.run([str(FFMPEG),'-nostdin','-v','error','-f','lavfi','-i','color=c=blue:s=64x96:r=24:d=1',
        '-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(path)],check=True,timeout=20)
    return path.read_bytes()


async def plan_fixture(tmp_path,monkeypatch):
    """Reuse actual M4B scope setup but explicitly add valid text-only immutable versions."""
    from tests.test_shot_plan_acceptance import setup
    monkeypatch.setattr(settings,'production_probe_executable',str(FFMPEG))
    engine,factory,data,oldversions,*_=await setup(tmp_path,monkeypatch)
    versions=[]
    async with factory() as db:
        for identity in ['asset-shared-hero','asset-shared-other','asset-a']:
            asset=await db.get(ProductionAsset,identity)
            version=await add_version(db,asset,VersionWrite(expected_revision=1,description='Explicit text-only software asset version',media_ids=[]))
            versions.append(version.id)
        await db.commit()
    data['model_id']=MODEL
    for shot in data['shots']:shot.update(version_ids=versions,reference_media_ids=[])
    return engine,factory,data


def expected_body(snapshot):
    """Independent full wire truth from the approved contract; no product builder is called."""
    return {'model':MODEL,'content':[{'type':'text','text':snapshot['prompt']}],'ratio':'9:16','resolution':'480p',
        'duration':snapshot['duration'],'output_format':'mp4','generate_audio':False,'watermark':True}


@pytest.mark.parametrize('duration',[4,5,30])
def test_wire_builder_preserves_exact_prompt_and_fixed_text_only_options(duration):
    """The builder must produce the entire approved wire shape with no inferred idempotency fields."""
    from app.core.integrations.volcengine.seedance25_payload import build_seedance25_body
    snapshot={'model_id':MODEL,'duration':duration,'references':[],'prompt':'人工文本 😄\nDo not truncate --ratio is inert text','prompt_revision':'local-only'}
    assert build_seedance25_body(snapshot)==expected_body(snapshot)


@pytest.mark.parametrize('patch',[{'duration':True},{'duration':5.0},{'duration':3},{'duration':31},{'model_id':'seedance-alias'},
    {'references':[{'media_id':'local-png','kind':'image'}]},{'prompt':' '},{'endpoint':'http://injected.invalid'},
    {'quote_minor':0},{'generate_audio':True}])
def test_wire_builder_rejects_expansion_coercion_and_unsupported_references(patch):
    """Local rejects may not silently drop media, coerce duration or accept imported provider options."""
    from app.core.integrations.volcengine.seedance25_payload import build_seedance25_body
    snapshot={'model_id':MODEL,'duration':5,'references':[],'prompt':'Software fixture'};snapshot.update(patch)
    with pytest.raises((ValueError,HTTPException)):build_seedance25_body(snapshot)



def configured_transport(evidence,clock):
    """Use only explicitly trusted loopback configuration; never resolve production settings/keys."""
    from app.core.contracts.seedance25 import Seedance25Configuration
    from app.core.integrations.volcengine.seedance25 import Seedance25DurableTransport
    config=Seedance25Configuration(api_key=evidence.key,service_version='independent_fixture_v1',account_version='fake_account_v1',
        base_url=f'http://127.0.0.1:{evidence.control_port}/api/v3',download_hosts=frozenset({'127.0.0.1'}),
        trusted_local_host='127.0.0.1',trusted_local_port=evidence.control_port)
    assert evidence.key not in repr(config)
    return config,Seedance25DurableTransport(config,clock=clock)


async def ready_to_submit(tmp_path,monkeypatch,evidence):
    """Compile genuine text-only M4B scope/versions and admit one TEST-budget job through the real service."""
    from tests.test_durable_execution import Clock
    from tests.test_shot_plan_acceptance import compile_plan
    from app.services.worker.execution_budget import configure
    engine,factory,data=await plan_fixture(tmp_path,monkeypatch)
    clock=Clock();config,transport=configured_transport(evidence,clock)
    policy=AdmissionPolicy(Capability(MODEL,frozenset(range(4,31)),frozenset(),0),10,'TEST',10,binding_id=config.binding_id)
    async with factory() as db:
        budget=await db.get(ExecutionBudget,'a')
        if budget is None:await configure(db,'a','TEST',1000)
        else:assert budget.currency=='TEST' and budget.limit_minor>=10
    worker=DurableExecution(transport,factory=factory,clock=clock,publisher=publish_artifact)
    worker.allowed_bindings=frozenset({binding_fingerprint(policy)})
    runtime=SimpleNamespace(policies={MODEL:policy},allow_submission=True,running=True,worker=worker)
    draft,plan=await compile_plan(factory,data,runtime)
    job=await admit_planned_shot(factory,'a',plan,'shot-a1','independent-create',runtime=runtime)
    return engine,factory,clock,config,transport,policy,worker,runtime,draft,plan,job


async def inspect_job(factory,job):
    """Read fresh durable state rather than trusting step() return values or cached objects."""
    async with factory() as db:return await db.get(ExecutionJob,job)


async def assert_no_persisted_sentinels(factory,evidence):
    """Search every actual SQLite table for fake credentials/signed capability values."""
    from sqlalchemy import text
    async with factory() as db:
        names=(await db.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))).scalars().all()
        for name in names:
            quoted='"'+name.replace('"','""')+'"'
            rows=(await db.execute(text('SELECT * FROM '+quoted))).all()
            serialized=repr(rows)
            assert evidence.key not in serialized and evidence.signature not in serialized,'A private sentinel reached a persistent table'


@pytest.mark.asyncio
async def test_real_http_and_file_sqlite_publish_candidate_after_worker_rebuild_without_second_post(tmp_path,monkeypatch,caplog):
    """The actual adapter/network child/worker/publisher pipeline receives a real authored MP4 once."""
    video=authored_mp4(tmp_path)
    with real_servers(video) as evidence:
        engine,factory,clock,config,transport,policy,worker,runtime,draft,plan,job=await ready_to_submit(tmp_path,monkeypatch,evidence)
        try:
            await worker.step(job)
            state=await inspect_job(factory,job)
            assert state.state=='running' and state.remote_id==REMOTE and evidence.posts==1
            recovered=DurableExecution(transport,factory=factory,clock=clock,publisher=publish_artifact)
            recovered.allowed_bindings=worker.allowed_bindings
            clock.advance(6);await recovered.step(job)
            state=await inspect_job(factory,job)
            assert state.state=='downloading' and state.result=={'status':'succeeded','locator':REMOTE,'name':'seedance25.mp4'}
            await assert_no_persisted_sentinels(factory,evidence)
            await recovered.step(job)
            state=await inspect_job(factory,job)
            assert state.state=='ready' and evidence.posts==1 and evidence.downloads==1
            async with factory() as db:
                media=await db.get(ProductionMedia,state.media_id)
                assert media.project_id=='a' and media.sha256==hashlib.sha256(video).hexdigest()
                path=Path(settings.production_media_root)/media.relative_path
                assert path.read_bytes()==video
                assert await db.scalar(select(func.count()).select_from(SubmissionAttempt))==1
                assert (await db.get(ExecutionBudgetEntry,job)).state=='unknown'
                assert await db.scalar(select(func.count()).select_from(ProductionTake))==0
            assert await admit_planned_shot(factory,'a',plan,'shot-a1','independent-create',runtime=None)==job
            artifact=[x for x in evidence.requests if x['role']=='artifact']
            assert artifact and all(not x['auth_present'] and not x['cookie_present'] for x in artifact)
            controls=[x for x in evidence.requests if x['role']=='control']
            assert all(x['auth_matches'] for x in controls)
            assert not any(x['idempotency_present'] or x['method']=='DELETE' for x in evidence.requests)
            posted=next(x['body'] for x in controls if x['method']=='POST')
            assert posted==expected_body(state.snapshot)
            await assert_no_persisted_sentinels(factory,evidence)
            assert evidence.key not in caplog.text and evidence.signature not in caplog.text
        finally:await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['disconnect','missing-id','429','duplicate-json','redirect'])
async def test_actual_ambiguous_create_never_resubmits_or_releases_unknown_hold(tmp_path,monkeypatch,mode):
    """Every possibly accepted HTTP failure remains one attempt, one POST and unknown TEST hold."""
    with real_servers(authored_mp4(tmp_path)) as evidence:
        evidence.create_mode=mode
        engine,factory,clock,config,transport,policy,worker,runtime,draft,plan,job=await ready_to_submit(tmp_path,monkeypatch,evidence)
        try:
            await worker.step(job)
            assert (await inspect_job(factory,job)).state=='submission_unknown' and evidence.posts==1
            restarted=DurableExecution(transport,factory=factory,clock=clock,publisher=publish_artifact)
            restarted.allowed_bindings=worker.allowed_bindings
            for _ in range(3):clock.advance(6);await restarted.step(job)
            row=await inspect_job(factory,job)
            assert row.state=='paused' and row.pause_from=='submission_unknown'
            await restarted.resume(job);clock.advance(6);await restarted.step(job)
            assert evidence.posts==1 and evidence.queries==0 and evidence.downloads==0
            async with factory() as db:
                assert await db.scalar(select(func.count()).select_from(SubmissionAttempt))==1
                hold=await db.get(ExecutionBudgetEntry,job)
                assert hold.state=='unknown' and hold.actual_minor is None
                assert await db.scalar(select(func.count()).select_from(ProductionMedia).where(ProductionMedia.id==row.media_id))==0
            await assert_no_persisted_sentinels(factory,evidence)
        finally:await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase',['query','download','cancel'])
@pytest.mark.parametrize('resume_entry',['worker','http'])
async def test_real_429_pauses_durably_and_both_resume_paths_respect_not_before(tmp_path,monkeypatch,phase,resume_entry):
    """A long real Retry-After becomes immediate persistent pause, never a delayed active loop or early retry."""
    import httpx
    from fastapi import FastAPI
    from app.api.execution import router
    from app.dependencies import get_db
    from app.services.worker import execution_queries
    with real_servers(authored_mp4(tmp_path)) as evidence:
        engine,factory,clock,config,transport,policy,worker,runtime,draft,plan,job=await ready_to_submit(tmp_path,monkeypatch,evidence)
        monkeypatch.setattr(execution_queries,'time',SimpleNamespace(time=clock))
        async def actual_session():
            """Give actual ASGI controls the same isolated SQLite database and commit/rollback semantics."""
            async with factory() as db:
                try:yield db;await db.commit()
                except BaseException:await db.rollback();raise
        app=FastAPI();app.include_router(router);app.dependency_overrides[get_db]=actual_session
        try:
            await worker.step(job);clock.advance(6)
            if phase=='download':await worker.step(job)
            if phase=='cancel':await worker.request_cancel(job)
            evidence.query_mode='429';evidence.retry_after='86400'
            before=clock();await worker.step(job)
            row=await inspect_job(factory,job)
            expected='downloading' if phase=='download' else 'running'
            assert row.state=='paused' and row.pause_from==expected and row.reason=='provider_query_backoff'
            assert row.next_due>=before+86400 and row.cancel_requested==(phase=='cancel')
            queries=evidence.queries
            recovered=DurableExecution(transport,factory=factory,clock=clock,publisher=publish_artifact)
            recovered.allowed_bindings=worker.allowed_bindings
            await recovered.step(job)
            assert evidence.queries==queries and evidence.posts==1
            with pytest.raises(HTTPException) as error:await recovered.resume(job)
            assert error.value.status_code==409
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local',trust_env=False) as client:
                url='/execution/projects/a/jobs/'+job
                read=await client.get(url)
                assert read.status_code==200 and read.json()['data']['can_resume'] is False
                denied=await client.post(url+'/resume')
                assert denied.status_code==409
                clock.advance(86400)
                assert (await client.get(url)).json()['data']['can_resume'] is True
                if resume_entry=='http':assert (await client.post(url+'/resume')).status_code==200
                else:await recovered.resume(job)
            current=await inspect_job(factory,job)
            assert current.state==expected and current.remote_id==REMOTE and current.cancel_requested==(phase=='cancel')
            assert evidence.posts==1 and evidence.queries==queries
            async with factory() as db:
                assert (await db.get(ExecutionBudgetEntry,job)).state=='unknown'
                assert await db.scalar(select(func.count()).select_from(SubmissionAttempt))==1
        finally:await engine.dispose()


@pytest.mark.asyncio
async def test_large_backoff_deadline_never_rounds_before_requested_time(tmp_path):
    """Even the maximum legal safe integer must not round SQLite's absolute float deadline earlier."""
    from decimal import Decimal
    from tests.test_durable_execution import setup
    from app.core.contracts.execution_control import QueryBackoff
    engine,factory,adapter,clock,worker,_=await setup(tmp_path)
    try:
        job=await worker.create('p','large-backoff',{})
        await worker.step(job);clock.value=1006
        async def backoff(_remote):
            """Exercise the real worker control branch independently of network date parsing."""
            raise QueryBackoff(9007199254740991)
        adapter.query=backoff
        await worker.step(job)
        row=await inspect_job(factory,job)
        assert row.state=='paused'
        assert Decimal.from_float(float(row.next_due))>=Decimal(clock())+Decimal(9007199254740991)
    finally:await engine.dispose()




@pytest.mark.asyncio
async def test_actual_status_mapping_is_allowlisted_and_cancel_never_issues_delete(tmp_path):
    """Map real fixture metadata into only approved durable keys and never persist raw provider errors."""
    with real_servers(authored_mp4(tmp_path)) as evidence:
        config,transport=configured_transport(evidence,lambda:1000)
        for status,expected in [('queued','running'),('running','running'),('failed','failed'),('expired','failed'),('cancelled','cancelled')]:
            evidence.query_mode=status
            assert await transport.query(REMOTE)=={'status':expected}
        evidence.query_mode='succeeded'
        assert await transport.query(REMOTE)=={'status':'succeeded','locator':REMOTE,'name':'seedance25.mp4'}
        assert await transport.cancel(REMOTE) is False
        evidence.query_mode='cancelled'
        assert await transport.cancel(REMOTE) is True
        assert evidence.posts==0 and evidence.downloads==0 and all(x['method']=='GET' for x in evidence.requests)
        assert evidence.key not in repr(config)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['wrong-id','wrong-model','not-a-state','redirect'])
async def test_wrong_or_redirected_query_never_reaches_download_or_ready(tmp_path,monkeypatch,mode):
    """Wrong task/model, unknown state or redirected control metadata cannot certify a candidate."""
    with real_servers(authored_mp4(tmp_path)) as evidence:
        engine,factory,clock,config,transport,policy,worker,runtime,draft,plan,job=await ready_to_submit(tmp_path,monkeypatch,evidence)
        try:
            await worker.step(job);evidence.query_mode=mode
            for _ in range(3):clock.advance(6);await worker.step(job)
            row=await inspect_job(factory,job)
            assert row.state=='paused' and row.pause_from=='running' and evidence.posts==1 and evidence.downloads==0
            await assert_no_persisted_sentinels(factory,evidence)
        finally:await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['corrupt','expired','redirect-private','oversize'])
async def test_actual_bad_download_never_publishes_or_releases_accepted_hold(tmp_path,monkeypatch,mode):
    """Real HTTP failure, invalid MP4, unsafe redirect and bounded bytes all fail before local ready."""
    with real_servers(authored_mp4(tmp_path)) as evidence:
        engine,factory,clock,config,transport,policy,worker,runtime,draft,plan,job=await ready_to_submit(tmp_path,monkeypatch,evidence)
        try:
            await worker.step(job);clock.advance(6);await worker.step(job)
            assert (await inspect_job(factory,job)).state=='downloading'
            if mode=='oversize':monkeypatch.setattr(settings,'production_upload_limit',128)
            else:evidence.download_mode=mode
            for _ in range(3):clock.advance(6);await worker.step(job)
            row=await inspect_job(factory,job)
            assert row.state=='paused' and row.pause_from=='downloading' and evidence.posts==1
            async with factory() as db:
                assert await db.get(ProductionMedia,row.media_id) is None
                assert (await db.get(ExecutionBudgetEntry,job)).state=='unknown'
            assert all(not x['auth_present'] and not x['cookie_present'] for x in evidence.requests if x['role']=='artifact')
            await assert_no_persisted_sentinels(factory,evidence)
        finally:await engine.dispose()


@pytest.mark.asyncio
async def test_download_restart_refreshes_ephemeral_signed_url_without_second_create(tmp_path,monkeypatch):
    """An expired download can refresh metadata in RAM on the original remote task after worker rebuild."""
    with real_servers(authored_mp4(tmp_path)) as evidence:
        engine,factory,clock,config,transport,policy,worker,runtime,draft,plan,job=await ready_to_submit(tmp_path,monkeypatch,evidence)
        try:
            await worker.step(job);clock.advance(6);await worker.step(job)
            evidence.download_mode='expired';await worker.step(job)
            row=await inspect_job(factory,job)
            assert row.state=='downloading' and row.download_retries==1
            await assert_no_persisted_sentinels(factory,evidence)
            previous=evidence.signature;evidence.signature='FAKE-REFRESHED-'+uuid.uuid4().hex;evidence.download_mode='video'
            rebuilt=DurableExecution(transport,factory=factory,clock=clock,publisher=publish_artifact);rebuilt.allowed_bindings=worker.allowed_bindings
            clock.advance(6);await rebuilt.step(job)
            assert (await inspect_job(factory,job)).state=='ready' and evidence.posts==1 and evidence.downloads==2
            assert all(x['query_capability_matches'] for x in evidence.requests if x['role']=='artifact')
            await assert_no_persisted_sentinels(factory,evidence)
            evidence.signature=previous;await assert_no_persisted_sentinels(factory,evidence)
        finally:await engine.dispose()


@pytest.mark.asyncio
async def test_preparation_runtime_never_acquires_credentials_or_execution_ability():
    """The default profile is immutable capability description, with unknown quote and no scheduler."""
    from app.services.worker.seedance25_runtime import build_preparation_runtime
    runtime=build_preparation_runtime(lambda:None)
    await runtime.start()
    assert runtime.running is False and runtime.allow_submission is False
    policy=runtime.policies[MODEL]
    assert policy.quote_minor is None and policy.capability.reference_kinds==frozenset() and policy.capability.max_references==0
    assert policy.capability.durations==frozenset(range(4,31))
    with pytest.raises((AttributeError,TypeError)):runtime.allow_submission=True
    with pytest.raises(TypeError):runtime.policies[MODEL]=None
    with pytest.raises(RuntimeError):await runtime.worker.step('inert')
    await runtime.stop()
    assert runtime.running is False



@pytest.mark.asyncio
async def test_real_retry_after_seconds_dates_and_malformed_values_have_exact_pause_delays(tmp_path):
    """Read actual HTTP headers through the isolated client and preserve safe date rounding."""
    from datetime import datetime,timezone
    from email.utils import format_datetime
    from app.core.contracts.execution_control import QueryBackoff
    with real_servers(authored_mp4(tmp_path)) as evidence:
        _,transport=configured_transport(evidence,lambda:1000.25)
        evidence.query_mode='429'
        date=format_datetime(datetime.fromtimestamp(1009,timezone.utc),usegmt=True)
        for header,expected in [('0',5),('7',7),('86400',86400),('malformed',30),(date,9)]:
            evidence.retry_after=header
            with pytest.raises(QueryBackoff) as error:await transport.query(REMOTE)
            assert error.value.retry_after_seconds==expected
        assert evidence.posts==0 and evidence.downloads==0


@pytest.mark.asyncio
async def test_untrusted_artifact_port_and_invalid_remote_ids_are_rejected_before_cross_origin_download(tmp_path):
    """The second local server is untrusted and cannot receive credentials or a download request."""
    from app.core.integrations.volcengine.seedance25 import Seedance25ProtocolError
    with real_servers(authored_mp4(tmp_path)) as evidence:
        config,transport=configured_transport(evidence,lambda:1000)
        for remote in ['../other','https://private.invalid','x'*129,False,'']:
            before=evidence.queries
            with pytest.raises(Seedance25ProtocolError):await transport.query(remote)
            assert evidence.queries==before
        evidence.separate_artifact=True
        with pytest.raises(Seedance25ProtocolError):await transport.query(REMOTE)
        assert evidence.downloads==0 and not any(r['role']=='artifact' for r in evidence.requests)
        assert evidence.posts==0


@pytest.mark.asyncio
async def test_actual_default_off_http_preview_requires_recompile_not_paid_readiness_and_has_zero_task_side_effects(tmp_path,monkeypatch):
    """An owned current text-only plan can preview without credentials, quote or runnable worker."""
    import httpx
    from fastapi import FastAPI
    from app.api.shot_plan import router,get_shot_plan_factory
    from app.services.worker.seedance25_runtime import build_preparation_runtime
    from app.services.shot_plan.service import patch_draft,compile_draft
    from app.core.contracts.shot_plan import PlanPatch,PlanCompile
    from app.models.task import GenerationTask
    from app.models.studio import Character
    from tests.test_shot_plan_acceptance import compile_plan
    engine,factory,data=await plan_fixture(tmp_path,monkeypatch)
    app=FastAPI();app.include_router(router);app.dependency_overrides[get_shot_plan_factory]=lambda:factory
    runtime=build_preparation_runtime(factory);app.state.execution_runtime=runtime
    models=[GenerationTask,ExecutionJob,SubmissionAttempt,ExecutionBudgetEntry,ProductionTake]
    try:
        draft,legacy=await compile_plan(factory,data,None)
        async with factory() as db:baseline=[await db.scalar(select(func.count()).select_from(model)) for model in models]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local',trust_env=False) as client:
            base='/shot-plans/projects/a'
            profile=await client.get(base+'/seedance25-profile')
            assert profile.status_code==200 and profile.json()['data']['quote_minor'] is None
            assert profile.json()['data']['execution_enabled'] is False and profile.json()['data']['model_qualified'] is False
            denied=await client.get(base+'/plans/'+legacy+'/seedance25-preview')
            assert denied.status_code==200
            old=denied.json()['data'];assert not old['can_prepare'] and all(e['body'] is None for e in old['entries'])
            await patch_draft(factory,'a',draft,PlanPatch(expected_revision=1,document=data))
            prepared=await compile_draft(factory,'a',draft,PlanCompile(expected_revision=2),runtime=runtime)
            response=await client.get(base+'/plans/'+prepared+'/seedance25-preview')
            assert response.status_code==200,response.text
            first=response.json()['data']
            assert first['can_prepare'] and first['can_submit'] is False and first['execution_enabled'] is False
            assert all(entry['body']['model']==MODEL and entry['body']['duration']==5 for entry in first['entries'])
            again=(await client.get(base+'/plans/'+prepared+'/seedance25-preview')).json()['data']
            assert again==first
            submit=await client.post(base+'/plans/'+prepared+'/shots/shot-a1/submissions',json={'request_key':'default-off'})
            assert submit.status_code==503,submit.text
            foreign=await client.get('/shot-plans/projects/b/plans/'+prepared+'/seedance25-preview')
            assert foreign.status_code==404
            async with factory() as db:
                await db.execute(update(Character).where(Character.id=='shared-hero').values(description='Actual current source changed after preparation'))
                await db.commit()
            stale=(await client.get(base+'/plans/'+prepared+'/seedance25-preview')).json()['data']
            assert not stale['can_prepare'] and stale['wire_hash'] is None and all(e['body'] is None for e in stale['entries'])
        async with factory() as db:assert [await db.scalar(select(func.count()).select_from(model)) for model in models]==baseline
        assert runtime.running is False and runtime.allow_submission is False
    finally:await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('accepted',[False,True])
async def test_real_protocol_preserves_m4b_first_send_facts_and_accepted_job_history(tmp_path,monkeypatch,accepted):
    """Unsent stale sources cancel without network; accepted work keeps its original task and TEST hold."""
    from app.models.studio import Character
    with real_servers(authored_mp4(tmp_path)) as evidence:
        engine,factory,clock,config,transport,policy,worker,runtime,draft,plan,job=await ready_to_submit(tmp_path,monkeypatch,evidence)
        try:
            if accepted:await worker.step(job)
            async with factory() as db:
                await db.execute(update(Character).where(Character.id=='shared-hero').values(description='Changed actual first-send fact'))
                await db.commit()
            if accepted:
                clock.advance(6);await worker.step(job);await worker.step(job)
                assert (await inspect_job(factory,job)).state=='ready' and evidence.posts==1
                async with factory() as db:assert (await db.get(ExecutionBudgetEntry,job)).state=='unknown'
            else:
                await worker.step(job)
                row=await inspect_job(factory,job)
                assert row.state=='cancelled' and evidence.posts==0
                async with factory() as db:
                    assert (await db.get(ExecutionBudgetEntry,job)).state=='released'
                    assert await db.scalar(select(func.count()).select_from(SubmissionAttempt))==0
        finally:await engine.dispose()


@pytest.mark.asyncio
async def test_competing_real_workers_and_new_account_binding_do_not_duplicate_creation(tmp_path,monkeypatch):
    """Current trusted account identity gates the old job, and two valid workers still submit it once."""
    from app.core.integrations.volcengine.seedance25 import Seedance25DurableTransport
    with real_servers(authored_mp4(tmp_path)) as evidence:
        engine,factory,clock,config,transport,policy,worker,runtime,draft,plan,job=await ready_to_submit(tmp_path,monkeypatch,evidence)
        try:
            newconfig=replace(config,account_version='different_account_v2')
            newpolicy=replace(policy,binding_id=newconfig.binding_id)
            assert newconfig.profile_hash!=config.profile_hash and evidence.key not in newconfig.profile_hash
            wrong=DurableExecution(Seedance25DurableTransport(newconfig,clock=clock),factory=factory,clock=clock,publisher=publish_artifact)
            wrong.allowed_bindings=frozenset({binding_fingerprint(newpolicy)})
            await wrong.step(job)
            assert evidence.posts==0 and (await inspect_job(factory,job)).reason=='runtime_binding_unavailable'
            peer=DurableExecution(transport,factory=factory,clock=clock,publisher=publish_artifact);peer.allowed_bindings=worker.allowed_bindings
            await asyncio.gather(worker.step(job),peer.step(job))
            assert evidence.posts==1 and (await inspect_job(factory,job)).state=='running'
            with pytest.raises(HTTPException) as error:await admit_planned_shot(factory,'b',plan,'shot-a1','foreign-creation',runtime=runtime)
            assert error.value.status_code==404
            async with factory() as db:assert await db.scalar(select(func.count()).select_from(SubmissionAttempt))==1
        finally:await engine.dispose()


@pytest.mark.asyncio
async def test_new_directory_sqlite_backup_and_media_restore_resume_original_download_without_post(tmp_path,monkeypatch):
    """Restore an actual downloading job/binding/hold to a new DB and media root, then let the real scanner finish."""
    from contextlib import closing
    import shutil,sqlite3
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
    from app.services.worker.execution_dispatcher import ExecutionDispatcher
    with real_servers(authored_mp4(tmp_path)) as evidence:
        engine,factory,clock,config,transport,policy,worker,runtime,draft,plan,job=await ready_to_submit(tmp_path,monkeypatch,evidence)
        restored_engine=None
        try:
            await worker.step(job);clock.advance(6);await worker.step(job)
            before=await inspect_job(factory,job)
            assert before.state=='downloading' and evidence.posts==1
            async with factory() as db:
                files=(await db.execute(select(ProductionMedia.relative_path,ProductionMedia.sha256))).all()
                hold_before=(await db.get(ExecutionBudgetEntry,job)).state
                attempts=await db.scalar(select(func.count()).select_from(SubmissionAttempt))
            database=Path(engine.url.database);await engine.dispose()
            original_sha=hashlib.sha256(database.read_bytes()).hexdigest()
            restored=tmp_path/'new-directory';restored.mkdir();newdb=restored/'restored.db';media_root=restored/'media';media_root.mkdir()
            with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(newdb)) as target:
                source.backup(target)
                assert target.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
                assert not target.execute('PRAGMA foreign_key_check').fetchall()
            for relative,sha in files:
                path=media_root/relative;path.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(tmp_path/relative,path)
                assert hashlib.sha256(path.read_bytes()).hexdigest()==sha
            restored_engine=create_async_engine('sqlite+aiosqlite:///'+newdb.as_posix())
            @event.listens_for(restored_engine.sync_engine,'connect')
            def restore_constraints(connection,_record):
                """Keep actual restored scope FKs enabled rather than weakening SQLite for publication."""
                connection.execute('PRAGMA foreign_keys=ON');connection.execute('PRAGMA busy_timeout=5000')
            restored_factory=async_sessionmaker(restored_engine,expire_on_commit=False)
            monkeypatch.setattr(settings,'production_media_root',str(media_root))
            recovered=DurableExecution(transport,factory=restored_factory,clock=clock,publisher=publish_artifact)
            recovered.allowed_bindings=worker.allowed_bindings
            scanner=ExecutionDispatcher(recovered,factory=restored_factory,clock=clock,interval_seconds=.01)
            assert await scanner.tick()==1
            after=await inspect_job(restored_factory,job)
            assert after.state=='ready' and after.id==before.id and after.request_hash==before.request_hash and after.remote_id==REMOTE
            assert evidence.posts==1
            async with restored_factory() as db:
                assert (await db.get(ExecutionBudgetEntry,job)).state==hold_before=='unknown'
                assert await db.scalar(select(func.count()).select_from(SubmissionAttempt))==attempts==1
                media=await db.get(ProductionMedia,after.media_id)
                assert (media_root/media.relative_path).is_file()
            assert hashlib.sha256(database.read_bytes()).hexdigest()==original_sha
            await assert_no_persisted_sentinels(restored_factory,evidence)
        finally:
            if restored_engine is not None:await restored_engine.dispose()
            await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('entry',['worker','http'])
async def test_stale_resume_cannot_clear_a_new_concurrent_real_429_pause(tmp_path,monkeypatch,entry):
    """Delay only an actual old DB read, let another owner hit real429, then require stale UPDATE to lose CAS."""
    import httpx
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.api.execution import router
    from app.dependencies import get_db
    from app.services.worker import execution_queries
    with real_servers(authored_mp4(tmp_path)) as evidence:
        engine,factory,clock,config,transport,policy,worker,runtime,draft,plan,job=await ready_to_submit(tmp_path,monkeypatch,evidence)
        monkeypatch.setattr(execution_queries,'time',SimpleNamespace(time=clock))
        async def actual_session():
            """Use a fresh actual transaction for each HTTP control request."""
            async with factory() as db:
                try:yield db;await db.commit()
                except BaseException:await db.rollback();raise
        app=FastAPI();app.include_router(router);app.dependency_overrides[get_db]=actual_session
        read_old=asyncio.Event();release_old=asyncio.Event();stale_task=None;owners=set()
        original_get=AsyncSession.get;original_scoped=execution_queries.scoped_job
        async def delayed_get(db,entity,identity,*args,**kwargs):
            """Keep the real loaded row intact and suspend only the chosen stale worker reader."""
            row=await original_get(db,entity,identity,*args,**kwargs)
            if entry=='worker' and entity is ExecutionJob and identity==job and asyncio.current_task() in owners:
                read_old.set();await release_old.wait()
            return row
        async def delayed_scoped(db,project,identity):
            """Keep the actual HTTP scoped SELECT result, delaying only its old reader."""
            row=await original_scoped(db,project,identity)
            if entry=='http' and identity==job and asyncio.current_task() in owners:
                read_old.set();await release_old.wait()
            return row
        monkeypatch.setattr(AsyncSession,'get',delayed_get)
        monkeypatch.setattr(execution_queries,'scoped_job',delayed_scoped)
        try:
            await worker.step(job);clock.advance(6)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local',trust_env=False) as client:
                url='/execution/projects/a/jobs/'+job
                assert (await client.post(url+'/pause')).status_code==200
                async def stale_resume():
                    """Exercise one real resume endpoint while identifying only its delay seam."""
                    owners.add(asyncio.current_task())
                    if entry=='http':return (await client.post(url+'/resume')).status_code
                    try:await worker.resume(job);return 200
                    except HTTPException as error:return error.status_code
                stale_task=asyncio.create_task(stale_resume())
                await asyncio.wait_for(read_old.wait(),10)
                peer=DurableExecution(transport,factory=factory,clock=clock,publisher=publish_artifact)
                peer.allowed_bindings=worker.allowed_bindings
                await peer.resume(job)
                evidence.query_mode='429';evidence.retry_after='86400'
                await peer.step(job)
                current=await inspect_job(factory,job)
                assert current.state=='paused' and current.reason=='provider_query_backoff' and current.next_due>clock()
                deadline=current.next_due
                release_old.set()
                assert await asyncio.wait_for(stale_task,10)==409
                after=await inspect_job(factory,job)
                assert after.state=='paused' and after.reason=='provider_query_backoff' and after.next_due==deadline
                assert after.pause_from=='running' and evidence.posts==1 and evidence.queries==1
                assert (await client.get(url)).json()['data']['can_resume'] is False
        finally:
            release_old.set()
            if stale_task is not None and not stale_task.done():stale_task.cancel();await asyncio.gather(stale_task,return_exceptions=True)
            await engine.dispose()
