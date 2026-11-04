"""Actual isolated HTTP and native project accounting receive decoded self-authored media, never a vendor generation."""
import asyncio
import hashlib
from pathlib import Path

import pytest
from sqlalchemy import func,select,update

from app.config import settings
from app.core.contracts.model_accounting import ModelPriceConfiguration,ModelPriceRule
from app.core.contracts.wan30 import MODEL_ID
from app.models.execution import ExecutionJob,SubmissionAttempt
from app.models.execution_budget import ExecutionBudgetEntry
from app.models.llm import Provider
from app.models.model_charge import ModelCallCharge
from app.models.production import ProductionMedia,ProductionTake
from app.services.llm.connection_versions import connection_version
from app.services.worker.model_prices import ModelPriceRegistry
from app.services.worker.native_video_accounting import NativeVideoAccounting
from app.services.worker.workspace_video_runtime import build_managed_video_runtime
from tests.test_native_video_http_accounting import authored_video,assert_private_metadata
from tests.test_native_video_project_accounting import counters
from tests.test_video_understanding import FFMPEG
from tests.test_wan30_http import owned_wan_server
from tests.test_wan30_native import owned_native,accept


@pytest.mark.asyncio
@pytest.mark.parametrize('reference',[False,True],ids=['text','first-last-images'])
async def test_wan_actual_http_and_control_only_restart_publish_one_original_decoded_candidate(tmp_path,monkeypatch,reference):
    """A real POST is resumed only with original-account GETs after restart; the candidate retains its project and unknown hold without automatic adoption."""
    video=authored_video(tmp_path)
    with owned_wan_server() as(port,state):
        state.artifact=video
        async with owned_native(tmp_path,monkeypatch,reference=reference) as f:
            monkeypatch.setattr(settings,'production_probe_executable',str(FFMPEG))
            if not reference:monkeypatch.setattr(settings,'production_media_root',str(tmp_path/'output-media'))
            async with f.factory() as db:
                await db.execute(update(Provider).where(Provider.id=='p').values(base_url=f'http://127.0.0.1:{port}/api/v1',api_key='AUTHORED_WAN_CREDENTIAL'))
                await db.commit()
            async with f.factory() as db:version=await connection_version(db,'p')
            f.quote=f.quote.model_copy(update={'provider_connection_version':version})
            registry=ModelPriceRegistry(ModelPriceConfiguration(enabled=True,rules=[ModelPriceRule(kinds=['native_video'],quote=f.quote)]))
            # Use the production transport selection, not the unit fixture's factory.
            f.accountant=NativeVideoAccounting(f.factory,f.config,registry,trusted_local_host='127.0.0.1',trusted_local_port=port)
            f.accountant.allow_new_post=True;f.worker.native_accounting=f.accountant
            async with f.factory() as db:_connection,_quote,_profile,policy=await f.accountant.current(db)
            f.runtime.policies={MODEL_ID:policy}
            identifier=(await accept(f))['job_ids'][0];await f.worker.step(identifier)
            async with f.factory() as db:
                job=await db.get(ExecutionJob,identifier);charge=await db.get(ModelCallCharge,identifier)
                assert job.state=='running' and job.remote_id=='owned-wan-task'
                assert charge.provider_request_id=='owned-create-witness' and charge.dispatch_state=='accepted'
            post=next(row for row in state.calls if row['method']=='POST')
            assert post['body']['model']==MODEL_ID and post['body']['parameters']['audio'] is False
            assert post['body']['parameters']['watermark'] is True and post['body']['parameters']['prompt_extend'] is False
            assert ('media' in post['body']['input']) is reference
            if reference:assert [item['type'] for item in post['body']['input']['media']]==['first_frame','last_frame']
            path=tmp_path/'control-only.json';path.write_text(f.config.model_copy(update={'enabled':False}).model_dump_json(),'utf-8')
            registry.rules.clear()
            restarted,_view=await build_managed_video_runtime(f.factory,str(path),registry,trusted_local_host='127.0.0.1',trusted_local_port=port)
            assert not restarted.allow_submission and restarted.allow_original_control
            restarted.worker.clock=f.clock;restarted.dispatcher.clock=f.clock;restarted.dispatcher.interval=.02
            try:
                f.clock.advance(6);await restarted.start()
                for _ in range(200):
                    async with f.factory() as db:job=await db.get(ExecutionJob,identifier)
                    if job.state=='ready':break
                    await asyncio.sleep(.05)
                assert job.state=='ready' and state.posts==1
            finally:await restarted.stop()
            async with f.factory() as db:
                media=await db.get(ProductionMedia,job.media_id)
                assert media.project_id=='a' and media.sha256==hashlib.sha256(video).hexdigest()
                assert (Path(settings.production_media_root)/media.relative_path).read_bytes()==video
                assert await db.scalar(select(func.count()).select_from(SubmissionAttempt))==1
                assert await db.scalar(select(func.count()).select_from(ProductionTake))==0
                assert (await db.get(ExecutionBudgetEntry,identifier)).state=='unknown'
            assert await counters(f.factory)==(25,0,1,1) and await counters(f.factory,'b')==(0,0,0,0)
            downloads=[row for row in state.calls if row['path'].startswith('/result.mp4')]
            assert len(downloads)==1 and all(key.lower()!='authorization' for key in downloads[0]['headers'])
            assert all(row['method'] in ('POST','GET') for row in state.calls)
            await assert_private_metadata(f.factory,type('PrivateSentinels',(),{'key':'AUTHORED_WAN_CREDENTIAL','signature':'AUTHORED_RESULT'})())
