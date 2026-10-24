"""SQLite ingestion state tests; fake probes prove no media-analysis quality claim."""
import asyncio
import hashlib

import pytest
from fastapi import HTTPException
from sqlalchemy import event,select,update
from sqlalchemy.ext.asyncio import async_sessionmaker,create_async_engine
from app.config import settings
from app.core.db import Base
import app.models.llm
from app.models.production import ProductionMedia
from app.models.reverse import ReverseAnalysis
from app.models.studio import FileItem,Project
from app.services.reverse.ingestion import create_analysis,edit_cuts,run_analysis,tick


async def setup(tmp_path):
    """Create isolated SQLite with real FK checks and source bytes owned by projects."""
    engine=create_async_engine('sqlite+aiosqlite:///'+(tmp_path/'reverse.db').as_posix())
    @event.listens_for(engine.sync_engine,'connect')
    def configure(connection,_):
        """Use production-like ownership enforcement and bounded SQLite lock waiting."""
        connection.execute('PRAGMA foreign_keys=ON');connection.execute('PRAGMA busy_timeout=5000')
    async with engine.begin() as conn:await conn.run_sync(Base.metadata.create_all)
    factory=async_sessionmaker(engine,expire_on_commit=False)
    async with factory() as db:
        db.add_all([Project(id=identity,name=identity,style='test') for identity in ['a','b']]);await db.flush()
        for identity,project,mime in [('a-video','a','video/mp4'),('a-other','a','video/mp4'),('b-video','b','video/mp4'),('a-image','a','image/png')]:
            data=('state-test-fixture '+identity).encode();(tmp_path/identity).write_bytes(data)
            db.add(FileItem(id=identity,type=mime.split('/')[0],name=identity,storage_key=identity));await db.flush()
            db.add(ProductionMedia(id=identity,project_id=project,name=identity,mime=mime,sha256=hashlib.sha256(data).hexdigest(),size=len(data),relative_path=identity))
        await db.commit()
    return engine,factory


async def read(factory,analysis_id):
    """Read durable task state without depending on worker return values."""
    async with factory() as db:return await db.get(ReverseAnalysis,analysis_id)


async def fixture_probe(path,output_dir,*,trusted_ffmpeg,deadline_seconds):
    """Return deterministic state-test artifacts; these bytes are not decoded media."""
    (output_dir/'proxy.mp4').write_bytes(b'not-real-video: state test only')
    (output_dir/'frame.jpg').write_bytes(b'not-real-image: state test only')
    frames=[{'index':i,'pts':pts,'time_base_num':1,'time_base_den':1000,'relative_us':pts*1000} for i,pts in enumerate([0,40,100,140])]
    mapping=[{'proxy_index':0,'relative_us':0,'source_index':0,'source_relative_us':0,'error_us':0}]
    return {'schema_version':1,'source':{'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size},'video':{'width':16,'height':16,'frames':frames,'decoded_span_us':140000},'cuts':[{'frame_index':2,'relative_us':100000,'kind':'candidate'}],'cuts_are_ground_truth':False,'keyframes':[{'frame_index':0,'relative_us':0,'path':'frame.jpg'}],'proxy':{'path':'proxy.mp4','frame_mapping':mapping,'max_error_us':0,'fps_num':30,'fps_den':1},'audio':{'present':False,'tracks':[],'wav_path':None},'test_fixture_only':True}


def test_create_is_owned_idempotent_and_validated(tmp_path,monkeypatch):
    """One logical source request creates one persisted task and never crosses projects."""
    monkeypatch.setattr(settings,'production_media_root',str(tmp_path))
    async def case():
        """Exercise duplicate, conflicting source and invalid source prerequisites."""
        engine,factory=await setup(tmp_path)
        try:
            first=await create_analysis(factory,'a','a-video','key')
            assert await create_analysis(factory,'a','a-video','key')==first
            for project,media,key,status in [('a','a-other','key',409),('a','b-video','new',404),('missing','a-video','x',404),('a','a-image','image',422)]:
                with pytest.raises(HTTPException) as error:await create_analysis(factory,project,media,key)
                assert error.value.status_code==status
            (tmp_path/'a-video').write_bytes(b'corrupted')
            with pytest.raises(HTTPException):await create_analysis(factory,'a','a-video','corrupt')
            async with factory() as db:assert len((await db.execute(select(ReverseAnalysis))).scalars().all())==1
        finally:await engine.dispose()
    asyncio.run(case())


def test_ready_artifacts_and_frame_edit_cas_keep_candidates_separate(tmp_path,monkeypatch):
    """Publish a manifest, then edit by original frame index with optimistic concurrency."""
    monkeypatch.setattr(settings,'production_media_root',str(tmp_path))
    async def case():
        """Verify original bytes unchanged and source candidates untouched by confirmation."""
        engine,factory=await setup(tmp_path);before=(tmp_path/'a-video').read_bytes()
        try:
            analysis=await create_analysis(factory,'a','a-video','edit')
            assert await run_analysis(factory,analysis,'unused-test-only',probe_fn=fixture_probe,clock=lambda:1000)
            row=await read(factory,analysis)
            assert row.state=='ready' and row.manifest['test_fixture_only'] and row.confirmed_cuts is None
            assert row.artifact_dir.startswith('_reverse/'+analysis+'/')
            edited=await edit_cuts(factory,'a',analysis,[0,2],1)
            assert edited['revision']==2 and edited['confirmed_cuts'][1]['relative_us']==100000
            with pytest.raises(HTTPException) as stale:await edit_cuts(factory,'a',analysis,[0],1)
            assert stale.value.status_code==409
            for cuts in [[],[1],[0,0],[0,-1],[0,4],[0,True],[0,2,1]]:
                with pytest.raises(HTTPException):await edit_cuts(factory,'a',analysis,cuts,2)
            with pytest.raises(HTTPException) as foreign:await edit_cuts(factory,'b',analysis,[0],2)
            assert foreign.value.status_code==404
            row=await read(factory,analysis)
            assert row.manifest['cuts']==[{'frame_index':2,'relative_us':100000,'kind':'candidate'}]
            assert row.confirmed_cuts==edited['confirmed_cuts']
            assert (tmp_path/'a-video').read_bytes()==before
        finally:await engine.dispose()
    asyncio.run(case())


def test_concurrent_workers_claim_one_and_recovery_uses_new_attempt(tmp_path,monkeypatch):
    """An active lease excludes peers; a killed worker is retryable after lease expiry."""
    monkeypatch.setattr(settings,'production_media_root',str(tmp_path))
    async def case():
        """Cancel an isolated fake operation, then advance the clock and resume safely."""
        engine,factory=await setup(tmp_path);entered=asyncio.Event();finish=asyncio.Event();outputs=[];now=[1000]
        async def blocking(path,output_dir,**kwargs):
            """Expose the private attempt directory before simulated worker death."""
            outputs.append(output_dir);entered.set();await finish.wait()
            return await fixture_probe(path,output_dir,**kwargs)
        try:
            analysis=await create_analysis(factory,'a','a-video','recover')
            first=asyncio.create_task(run_analysis(factory,analysis,'test',probe_fn=blocking,clock=lambda:now[0]))
            await asyncio.wait_for(entered.wait(),3)
            assert await run_analysis(factory,analysis,'test',probe_fn=fixture_probe,clock=lambda:now[0]) is False
            first.cancel()
            with pytest.raises(asyncio.CancelledError):await first
            assert (await read(factory,analysis)).state=='analyzing'
            assert await tick(factory,'test',probe_fn=fixture_probe,clock=lambda:now[0]) is False
            now[0]+=121
            assert await tick(factory,'test',probe_fn=fixture_probe,clock=lambda:now[0])
            row=await read(factory,analysis)
            assert row.state=='ready' and tmp_path/row.artifact_dir!=outputs[0]
            assert outputs[0].is_dir() and not list(outputs[0].iterdir())
        finally:await engine.dispose()
    asyncio.run(case())


@pytest.mark.parametrize('failure',['probe-error','source-changed','wrong-source','bad-manifest','path-escape','lease-expired'])
def test_failed_or_stale_probe_never_publishes_partial_manifest(tmp_path,monkeypatch,failure):
    """Do not publish unsafe engine output, changed sources or results from expired leases."""
    monkeypatch.setattr(settings,'production_media_root',str(tmp_path))
    async def case():
        """Inject local failures and assert fixed safe errors or fenced stale state."""
        engine,factory=await setup(tmp_path);now=[1000]
        async def broken(path,output_dir,**kwargs):
            """Return malformed data or fail with secrets that must not be persisted."""
            if failure=='probe-error':raise RuntimeError('SECRET C:/private ffmpeg --token secret')
            result=await fixture_probe(path,output_dir,**kwargs)
            if failure=='source-changed':path.write_bytes(b'changed source')
            elif failure=='wrong-source':result['source']['sha256']='f'*64
            elif failure=='bad-manifest':result['video']['frames'][1]['index']=99
            elif failure=='path-escape':result['proxy']['path']='../outside.mp4'
            elif failure=='lease-expired':now[0]+=121
            return result
        try:
            analysis=await create_analysis(factory,'a','a-video','failure')
            result=await run_analysis(factory,analysis,'test',probe_fn=broken,clock=lambda:now[0])
            row=await read(factory,analysis)
            assert row.manifest is None and row.artifact_dir is None
            assert 'SECRET' not in row.reason and 'C:/' not in row.reason and 'ffmpeg' not in row.reason
            assert row.state==('analyzing' if failure=='lease-expired' else 'failed')
            assert result is (failure!='lease-expired')
        finally:await engine.dispose()
    asyncio.run(case())


def test_lost_fence_does_not_overwrite_newer_worker(tmp_path,monkeypatch):
    """A late old attempt cannot overwrite a newer lease's terminal result."""
    monkeypatch.setattr(settings,'production_media_root',str(tmp_path))
    async def case():
        """Replace ownership during probing and check that stale publication is rejected."""
        engine,factory=await setup(tmp_path)
        analysis=await create_analysis(factory,'a','a-video','fence')
        async def replaced(path,output_dir,**kwargs):
            """Simulate another fenced publisher committing a newer finished analysis."""
            result=await fixture_probe(path,output_dir,**kwargs)
            async with factory() as db:
                await db.execute(update(ReverseAnalysis).where(ReverseAnalysis.id==analysis).values(state='ready',manifest={'newer':True},lease_token=None,lease_until=0));await db.commit()
            return result
        try:
            assert await run_analysis(factory,analysis,'test',probe_fn=replaced,clock=lambda:1000) is False
            assert (await read(factory,analysis)).manifest=={'newer':True}
        finally:await engine.dispose()
    asyncio.run(case())
