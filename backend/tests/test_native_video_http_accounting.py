"""Real loopback HTTP, killable transport children and actual authored video publication verify managed native accounting."""
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess

import pytest
from sqlalchemy import func,select,text,update

from app.config import settings
from app.core.contracts.model_accounting import ModelPriceConfiguration,ModelPriceRule
from app.core.contracts.video_runtime_setup import VideoRuntimeConfiguration
from app.models.execution import ExecutionJob,SubmissionAttempt
from app.models.execution_budget import ExecutionBudgetEntry
from app.models.llm import Provider
from app.models.model_charge import ModelCallCharge
from app.models.production import ProductionMedia,ProductionTake
from app.services.llm.connection_versions import connection_version
from app.services.worker.model_prices import ModelPriceRegistry
from app.services.worker.native_video_accounting import NativeVideoAccounting
from app.services.worker.workspace_video_runtime import build_managed_video_runtime
from tests.test_native_video_project_accounting import setup,admit,counters
from tests.test_seedance25_independent import CREATE,REMOTE,expected_body,real_servers
from tests.test_video_understanding import FFMPEG


def authored_video(tmp_path):
    """Create four seconds of exact known pixels with trusted local FFmpeg; no user media is read."""
    target=tmp_path/'authored-native-fixture.mp4'
    subprocess.run([str(FFMPEG),'-nostdin','-v','error','-f','lavfi','-i','color=c=blue:s=108x192:r=24:d=4',
        '-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(target)],check=True,timeout=20)
    return target.read_bytes()


async def local_native(tmp_path,evidence,monkeypatch):
    """Compose an exact independently trusted fixture origin and a fresh named connection quote, using the production adapter."""
    engine,factory,old_quote,_old_accountant,worker,runtime,_response,clock=await setup(tmp_path)
    monkeypatch.setattr(settings,'production_media_root',str(tmp_path/'output-media'))
    monkeypatch.setattr(settings,'production_probe_executable',str(FFMPEG))
    async with factory() as db:
        await db.execute(update(Provider).where(Provider.id=='p').values(base_url=f'http://127.0.0.1:{evidence.control_port}/api/v3',api_key=evidence.key));await db.commit()
    async with factory() as db:version=await connection_version(db,'p')
    quote=old_quote.model_copy(update={'provider_connection_version':version})
    registry=ModelPriceRegistry(ModelPriceConfiguration(enabled=True,rules=[ModelPriceRule(kinds=['native_video'],quote=quote)]))
    config=VideoRuntimeConfiguration(enabled=True,model_id='m',service_version='local-native-accounting-v1')
    accountant=NativeVideoAccounting(factory,config,registry,trusted_local_host='127.0.0.1',trusted_local_port=evidence.control_port)
    accountant.allow_new_post=True;worker.native_accounting=accountant
    async with factory() as db:_connection,_quote,_profile,policy=await accountant.current(db)
    runtime.policies={policy.capability.model_id:policy}
    original=evidence.bytes_reply
    def receipt(handler,status,raw,headers=None):
        """Add a distinct synthetic bill header before the actual socket response, including malformed/error responses."""
        headers=dict(headers or {})
        if handler.command=='POST' and handler.path==CREATE:headers['x-request-id']='native-real-http-bill'
        return original(handler,status,raw,headers)
    evidence.bytes_reply=receipt
    return engine,factory,quote,accountant,worker,runtime,clock


async def assert_private_metadata(factory,evidence):
    """A key lives only in its intended private Provider field; operational journals never retain it or signed URLs."""
    async with factory() as db:
        names=(await db.scalars(text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))).all()
        for name in names:
            quoted='"'+name.replace('"','""')+'"'
            columns='*'
            if name==Provider.__tablename__:
                fields=[row[1] for row in (await db.execute(text('PRAGMA table_info('+quoted+')'))).all() if row[1]!='api_key']
                columns=','.join('"'+field.replace('"','""')+'"' for field in fields)
            serialized=repr((await db.execute(text('SELECT '+columns+' FROM '+quoted))).all())
            assert evidence.key not in serialized and evidence.signature not in serialized,name


@pytest.mark.asyncio
async def test_actual_native_post_bill_header_and_control_only_restart_receive_authored_video_without_second_post(tmp_path,monkeypatch):
    """A disabled-new-submit runtime resumes the exact old account, publishes real bytes and retains its unknown fee until a bill."""
    video=authored_video(tmp_path)
    with real_servers(video) as evidence:
        engine,factory,quote,accountant,worker,runtime,clock=await local_native(tmp_path,evidence,monkeypatch)
        restarted=None
        try:
            identifier=(await admit(accountant,runtime,quote))['job_ids'][0];await worker.step(identifier)
            async with factory() as db:
                charge=await db.get(ModelCallCharge,identifier);job=await db.get(ExecutionJob,identifier)
                assert charge.provider_request_id=='native-real-http-bill' and job.remote_id==REMOTE
                assert charge.provider_request_id!=job.remote_id and job.state=='running'
                assert evidence.requests[0]['body']==expected_body(job.snapshot)
            # Preserve the original native service identity while changing only
            # permission for new POSTs and the current iteration setting.
            path=tmp_path/'control-only.json'
            path.write_text(json.dumps(accountant.config.model_copy(update={'enabled':False,'iteration_limit':99}).model_dump()),'utf-8')
            accountant.registry.rules.clear()
            restarted,_view=await build_managed_video_runtime(factory,str(path),accountant.registry,
                trusted_local_host='127.0.0.1',trusted_local_port=evidence.control_port)
            assert restarted.allow_original_control and not restarted.allow_submission
            restarted.worker.clock=clock;restarted.dispatcher.clock=clock;restarted.dispatcher.interval=.02
            clock.advance(6);await restarted.start()
            for _ in range(200):
                async with factory() as db:job=await db.get(ExecutionJob,identifier)
                if job.state=='ready':break
                await asyncio.sleep(.05)
            assert job.state=='ready' and evidence.posts==1 and evidence.downloads==1
            await restarted.stop()
            async with factory() as db:
                media=await db.get(ProductionMedia,job.media_id)
                assert media.project_id=='a' and media.sha256==hashlib.sha256(video).hexdigest()
                assert (Path(settings.production_media_root)/media.relative_path).read_bytes()==video
                assert await db.scalar(select(func.count()).select_from(SubmissionAttempt))==1
                assert await db.scalar(select(func.count()).select_from(ProductionTake))==0
                assert (await db.get(ExecutionBudgetEntry,identifier)).state=='unknown'
            assert await counters(factory)==(25,0,1,1) and await counters(factory,'b')==(0,0,0,0)
            assert all(request['auth_matches'] for request in evidence.requests if request['role']=='control')
            assert all(not request['auth_present'] and not request['cookie_present'] for request in evidence.requests if request['role']=='artifact')
            assert not any(request['method']=='DELETE' or request['idempotency_present'] for request in evidence.requests)
            await assert_private_metadata(factory,evidence)
        finally:
            if restarted is not None:await restarted.stop()
            await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode,has_receipt',[('disconnect',False),('duplicate-json',True),('429',True)])
async def test_actual_ambiguous_native_response_retains_original_fee_and_never_resubmits(tmp_path,monkeypatch,mode,has_receipt):
    """Real loss/error/malformed sockets retain one unknown hold, with a safe header only when a response actually arrived."""
    with real_servers(authored_video(tmp_path)) as evidence:
        evidence.create_mode=mode
        engine,factory,quote,accountant,worker,runtime,clock=await local_native(tmp_path,evidence,monkeypatch)
        try:
            identifier=(await admit(accountant,runtime,quote))['job_ids'][0];await worker.step(identifier)
            for _ in range(3):clock.advance(6);await worker.step(identifier)
            async with factory() as db:
                charge=await db.get(ModelCallCharge,identifier);job=await db.get(ExecutionJob,identifier)
                assert charge.provider_request_id==('native-real-http-bill' if has_receipt else None)
                assert charge.dispatch_state==('accepted' if has_receipt else 'prepared')
                assert job.state=='paused' and job.pause_from=='submission_unknown'
            assert evidence.posts==1 and evidence.queries==0 and evidence.downloads==0
            assert await counters(factory)==(25,0,1,1)
            await assert_private_metadata(factory,evidence)
        finally:await engine.dispose()
