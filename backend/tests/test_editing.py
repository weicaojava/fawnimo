"""Actual SQLite/MP4 editing author tests; no provider or model calls."""
import asyncio
import subprocess
from pathlib import Path
import pytest
from fastapi import HTTPException
from sqlalchemy import select,text,update,func
from sqlalchemy.exc import IntegrityError
from app.config import settings
from app.core.contracts.editing import SelectionWrite,EditDraftCreate,EditDraftPatch,EditFreezeWrite
from app.core.contracts.shot_plan import PlanCompile
from app.models.editing import EditPublication,SelectionEvent,TakeOrigin
from app.models.production import ProductionTake,ShotSelection
from app.models.studio import Character
from app.schemas.studio.production import TakeWrite
from app.services.studio.production import adopt_take,select_take
from app.services.studio.production_media import store_batch
from app.services.shot_plan.service import compile_draft
from app.services.editing.schema import migrate_editing
from app.services.editing.service import write_selection,create_draft,patch_draft,freeze_draft,draft_view,publication_view,preflight_publication,catalog,selection_history
from tests.test_shot_plan import fixture as plan_fixture


async def fixture(tmp_path,monkeypatch,audio=True):
    """Use actual registered plans and decoded synthetic MP4 as explicit manual imports."""
    engine,factory,draft,value,runtime,_=await plan_fixture(tmp_path,monkeypatch)
    await migrate_editing(engine)
    executable=Path(__file__).resolve().parents[4]/'ffmpeg.exe'
    monkeypatch.setattr(settings,'production_probe_executable',str(executable))
    file=tmp_path/'synthetic.mp4'
    args=[str(executable),'-nostdin','-v','error','-f','lavfi','-i','color=blue:s=160x120:r=30:d=2']
    if audio:args+=['-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=2']
    args+=['-c:v','libx264','-pix_fmt','yuv420p']
    if audio:args+=['-c:a','aac']
    args+=['-y',str(file)]
    subprocess.run(args,check=True,timeout=20)
    async with factory() as db:media=(await store_batch(db,'a',[('synthetic.mp4',file.read_bytes())]))[0].id
    plan=await compile_draft(factory,'a',draft,PlanCompile(expected_revision=1))
    return engine,factory,plan,media


def intent(plan,media,key='adopt',revision=0):
    """A new explicit import, never a client claim of generated provenance."""
    return SelectionWrite(request_key=key,shot_id='s1',expected_revision=revision,media_id=media,plan_id=plan,change_note='synthetic manual test only')


def document(plan,take,audio=True):
    """All fixed original requirements are retained in an actual source interval."""
    return {'schema_version':1,'plan_id':plan,'title':'人工软件片段','clips':[{'shot_id':'s1','take_id':take,'source_in_ms':100,'source_out_ms':1500,'source_audio':audio}], 'anchors':[{'kind':'beat','item_id':identity,'take_id':take,'start_ms':100,'end_ms':300,'confirmed':True} for identity in ['conflict','development','resolution']]+[{'kind':'dialogue','item_id':'line','take_id':take,'start_ms':200,'end_ms':700,'confirmed':True}]}


@pytest.mark.asyncio
async def test_selection_cas_replay_and_legacy_guard(tmp_path,monkeypatch):
    """Concurrent intents select once; old replay never moves the later pointer back."""
    engine,factory,plan,media=await fixture(tmp_path,monkeypatch)
    try:
        result=await asyncio.gather(write_selection(factory,'a',intent(plan,media,'one')),write_selection(factory,'a',intent(plan,media,'two')),return_exceptions=True)
        good=[r for r in result if isinstance(r,dict)];bad=[r for r in result if isinstance(r,HTTPException)]
        assert len(good)==len(bad)==1 and bad[0].status_code==409
        first=good[0];key='one' if isinstance(result[0],dict) else 'two'
        second=await write_selection(factory,'a',intent(plan,media,'next',1))
        assert second['take_id']!=first['take_id']
        replay=await write_selection(factory,'a',intent(plan,media,key))
        assert replay['replayed'] and replay['take_id']==first['take_id']
        async with factory() as db:
            assert (await db.get(ShotSelection,'s1')).take_id==second['take_id']
            assert await db.scalar(select(func.count()).select_from(ProductionTake))==2
            assert await db.scalar(select(func.count()).select_from(SelectionEvent))==2
            with pytest.raises(HTTPException) as exc:await select_take(db,'a',first['take_id'])
            assert exc.value.status_code==409
            with pytest.raises(HTTPException):await adopt_take(db,'a',TakeWrite(shot_id='s1',media_id=media,version_ids=['placeholder']))
        with pytest.raises(HTTPException):await write_selection(factory,'b',intent(plan,media,'foreign'))
        with pytest.raises(HTTPException):await write_selection(factory,'a',SelectionWrite(request_key=key,shot_id='s1',expected_revision=0,take_id=first['take_id']))
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_freeze_real_source_and_original_anchor_clocks(tmp_path,monkeypatch):
    """Freeze actual decoded evidence, preserve original text and source-relative timing."""
    engine,factory,plan,media=await fixture(tmp_path,monkeypatch)
    try:
        adopted=await write_selection(factory,'a',intent(plan,media));doc=document(plan,adopted['take_id'])
        draft=await create_draft(factory,'a',EditDraftCreate(chapter_id='c1',document=doc))
        async with factory() as db:
            assert (await draft_view(db,'a',draft))['readiness']['can_freeze']
            options=await catalog(db,'a','c1',plan)
            assert options['clips'][0]['takes'][0]['eligible_for_plan']
        with pytest.raises(HTTPException):await freeze_draft(factory,'a',draft,EditFreezeWrite(expected_revision=1))
        body=EditFreezeWrite(expected_revision=1,acknowledge_changes=True,change_note='explicit synthetic shorter trim')
        publication=await freeze_draft(factory,'a',draft,body)
        assert await freeze_draft(factory,'a',draft,body)==publication
        async with factory() as db:
            view=await publication_view(db,'a',publication)
            assert view['payload']['duration_ms']==1400
            assert view['payload']['subtitles']==[{'dialogue_id':'line','speaker_id':'hero','text':'手工对白','start_ms':100,'end_ms':600}]
            assert 'relative_path' not in view['payload']['clips'][0]['source']
            assert 'stat' not in view['payload']['clips'][0]['source']
            with pytest.raises(IntegrityError):await db.execute(text('UPDATE editing_publications SET content_hash=:hash WHERE id=:id'),{'hash':'bad','id':publication})
            await db.rollback()
            await db.execute(update(Character).where(Character.id=='hero').values(description='later mutable drift'));await db.commit()
        second=await write_selection(factory,'a',SelectionWrite(request_key='switch',shot_id='s1',expected_revision=1,take_id=adopted['take_id']))
        async with factory() as db:
            assert (await preflight_publication(db,'a',publication))['usable']
            assert (await publication_view(db,'a',publication))==view
        with pytest.raises(HTTPException):await patch_draft(factory,'a',draft,EditDraftPatch(expected_revision=9,document=doc))
    finally:await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('problem',['silent','missing_anchor','trimmed_anchor','missing_shot'])
async def test_readiness_rejects_missing_real_coverage(tmp_path,monkeypatch,problem):
    """Neither missing story, absent audio nor missing clips can pass software readiness."""
    engine,factory,plan,media=await fixture(tmp_path,monkeypatch,audio=problem!='silent')
    try:
        adopted=await write_selection(factory,'a',intent(plan,media));doc=document(plan,adopted['take_id'])
        if problem=='missing_anchor':doc['anchors']=doc['anchors'][:-1]
        if problem=='trimmed_anchor':doc['anchors'][-1]['start_ms']=0
        if problem=='missing_shot':doc['clips']=[];doc['anchors']=[]
        draft=await create_draft(factory,'a',EditDraftCreate(chapter_id='c1',document=doc))
        async with factory() as db:assert not (await draft_view(db,'a',draft))['readiness']['can_freeze']
        with pytest.raises(HTTPException):await freeze_draft(factory,'a',draft,EditFreezeWrite(expected_revision=1,acknowledge_changes=True,change_note='cannot bypass missing content'))
        async with factory() as db:assert await db.scalar(select(func.count()).select_from(EditPublication))==0
    finally:await engine.dispose()

@pytest.mark.asyncio
async def test_actual_planned_candidate_reuses_one_take_without_source_claim(tmp_path,monkeypatch):
    """Run the existing fixture worker to a real MP4 candidate, then adopt from its journal."""
    from app.models.execution import ExecutionJob
    from app.models.production import ProductionMedia
    from app.services.shot_plan.service import admit_planned_shot
    from app.core.contracts.execution_artifact import DownloadedArtifact
    engine,factory,draft,value,runtime,transport=await plan_fixture(tmp_path,monkeypatch)
    await migrate_editing(engine)
    file=tmp_path/'actual-fixture.mp4'
    executable=Path(__file__).resolve().parents[4]/'ffmpeg.exe'
    monkeypatch.setattr(settings,'production_probe_executable',str(executable))
    subprocess.run([str(executable),'-nostdin','-v','error','-f','lavfi','-i','color=green:s=160x120:r=30:d=1','-c:v','libx264','-pix_fmt','yuv420p','-y',str(file)],check=True,timeout=20)
    async def actual_download(result,media_id,project_id):
        """Fixture bytes contain actual video but confer no model qualification."""
        return DownloadedArtifact('actual-fixture.mp4',file.read_bytes())
    transport.download=actual_download
    try:
        plan=await compile_draft(factory,'a',draft,PlanCompile(expected_revision=1),runtime)
        job=await admit_planned_shot(factory,'a',plan,'s1','candidate',runtime)
        await runtime.worker.step(job)
        async with factory() as db:await db.execute(update(ExecutionJob).where(ExecutionJob.id==job).values(next_due=0));await db.commit()
        await runtime.worker.step(job);await runtime.worker.step(job)
        first=await write_selection(factory,'a',SelectionWrite(request_key='candidate-adopt',shot_id='s1',expected_revision=0,job_id=job))
        second=await write_selection(factory,'a',SelectionWrite(request_key='candidate-new-intent',shot_id='s1',expected_revision=1,job_id=job))
        assert first['take_id']==second['take_id'] and second['origin_kind']=='execution_candidate_unqualified'
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(ProductionTake))==1
            origin=await db.get(TakeOrigin,first['take_id'])
            assert origin.job_id==job and transport.sent==1
            assert (await db.get(ExecutionJob,job)).state=='ready'
        assert (await write_selection(factory,'a',SelectionWrite(request_key='candidate-adopt',shot_id='s1',expected_revision=0,job_id=job)))['replayed']
    finally:await engine.dispose()

@pytest.mark.asyncio
@pytest.mark.parametrize('running',[False,True])
async def test_task_center_cancel_dispatches_to_real_export_owner(tmp_path,monkeypatch,running):
    """Task-center HTTP records the export's real cancellation rather than revoking a UI row."""
    import httpx
    from fastapi import FastAPI
    from app.api.v1.routes.film import task_status
    from app.dependencies import get_db
    from app.services.editing.export_schema import migrate_editing_export
    from app.services.editing.exports import create_export,LocalExportWorker
    from app.models.editing_export import EditExportJob
    from app.models.task import GenerationTask
    engine,factory,plan,media=await fixture(tmp_path,monkeypatch)
    await migrate_editing_export(engine)
    monkeypatch.setattr(settings,'editing_export_enabled',True)
    called=[]
    monkeypatch.setattr(task_status,'revoke_task_execution',lambda identifier:called.append(identifier))
    async def session():
        """Give actual HTTP requests the same isolated database as the export service."""
        async with factory() as db:yield db
    app=FastAPI();app.include_router(task_status.router);app.dependency_overrides[get_db]=session
    try:
        adopted=await write_selection(factory,'a',intent(plan,media))
        draft=await create_draft(factory,'a',EditDraftCreate(chapter_id='c1',document=document(plan,adopted['take_id'])))
        publication=await freeze_draft(factory,'a',draft,EditFreezeWrite(expected_revision=1,acknowledge_changes=True,change_note='fixture'))
        job=await create_export(factory,'a',publication,'cancel-test',settings.production_probe_executable)
        if running:assert await LocalExportWorker(factory,settings.production_probe_executable).claim()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            response=await client.post('/tasks/'+job['task_id']+'/cancel',json={'reason':'stop'})
            assert response.status_code==200,response.text
            result=response.json()['data']
            assert result['cancel_requested'] and result['effective_immediately']==(not running)
        async with factory() as db:
            actual=await db.get(EditExportJob,job['id']);projection=await db.get(GenerationTask,job['task_id'])
            assert actual.cancel_requested and projection.cancel_requested
            assert actual.state==('running' if running else 'cancelled')
            assert str(projection.status)==('running' if running else 'cancelled')
        assert not called
    finally:await engine.dispose()

@pytest.mark.asyncio
@pytest.mark.parametrize('entrypoint',['metadata','editing'])
async def test_actual_frozen_m4b_database_additive_editing_upgrade(tmp_path,entrypoint):
    """Upgrade real M4B backup copies without rewriting the accepted source database."""
    import hashlib,json,sqlite3
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.core.db import Base
    evidence=json.loads((Path(__file__).resolve().parents[5]/'outputs/m4b/recovery-result.json').read_text(encoding='utf8'))
    source=Path(evidence['run_dir'])/'restored.db';before=hashlib.sha256(source.read_bytes()).hexdigest();copy=tmp_path/'m4b-copy.db'
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as original,sqlite3.connect(copy) as destination:original.backup(destination)
    engine=create_async_engine('sqlite+aiosqlite:///'+copy.as_posix())
    @event.listens_for(engine.sync_engine,'connect')
    def enforce_fk(connection,_):
        """Use the real FK boundary even through generic metadata migration."""
        connection.execute('PRAGMA foreign_keys=ON')
    try:
        if entrypoint=='editing':await migrate_editing(engine)
        else:
            async with engine.begin() as connection:await connection.run_sync(Base.metadata.create_all)
        async with engine.connect() as connection:
            assert not (await connection.execute(text('PRAGMA foreign_key_check'))).first()
            assert (await connection.execute(text("SELECT 1 FROM sqlite_master WHERE name='editing_publications'"))).first()
        assert hashlib.sha256(source.read_bytes()).hexdigest()==before
    finally:await engine.dispose()

@pytest.mark.asyncio
@pytest.mark.parametrize('legacy',[False,True])
async def test_historical_take_switch_in_other_current_plan_does_not_grant_origin(tmp_path,monkeypatch,legacy):
    """Restore legacy/other-plan history with viewing context but never relabel its provenance."""
    from app.models.production import TakeVersion
    from app.services.shot_plan.service import owned_plan,patch_draft as patch_plan
    from app.core.contracts.shot_plan import PlanPatch
    engine,factory,plan,media=await fixture(tmp_path,monkeypatch)
    try:
        adopted=await write_selection(factory,'a',intent(plan,media))
        take_id=adopted['take_id']
        async with factory() as db:
            source=await db.get(ProductionTake,take_id)
            versions=list(source.version_ids)
            if legacy:
                take_id='actual-legacy-take'
                db.add(ProductionTake(id=take_id,project_id='a',shot_id='s1',media_id=media,provenance='user_import',version_ids=versions));await db.flush()
                for version in versions:db.add(TakeVersion(take_id=take_id,version_id=version,project_id='a'))
                await db.commit()
            original=await owned_plan(db,'a',plan)
            plan_draft=original.draft_id;original_doc=original.payload['document']
        await patch_plan(factory,'a',plan_draft,PlanPatch(expected_revision=1,document=original_doc))
        other=await compile_draft(factory,'a',plan_draft,PlanCompile(expected_revision=2))
        switched=await write_selection(factory,'a',SelectionWrite(request_key='history-switch',shot_id='s1',take_id=take_id,plan_id=other,expected_revision=1))
        assert switched['take_id']==take_id and switched['revision']==2
        async with factory() as db:
            assert (await db.get(ShotSelection,'s1')).take_id==take_id
            origin=await db.get(TakeOrigin,take_id)
            assert origin is None if legacy else origin.plan_id==plan
            options=await catalog(db,'a','c1',other)
            option=next(t for t in options['clips'][0]['takes'] if t['id']==take_id)
            assert not option['eligible_for_plan']
        draft=await create_draft(factory,'a',EditDraftCreate(chapter_id='c1',document=document(other,take_id)))
        async with factory() as db:
            result=await draft_view(db,'a',draft)
            assert not result['readiness']['can_freeze'] and any(i['code']=='take_origin' for i in result['readiness']['issues'])
        with pytest.raises(HTTPException):await freeze_draft(factory,'a',draft,EditFreezeWrite(expected_revision=1,acknowledge_changes=True,change_note='cannot override provenance'))
        with pytest.raises(HTTPException):await write_selection(factory,'a',SelectionWrite(request_key='stale-history-switch',shot_id='s1',take_id=take_id,plan_id=other,expected_revision=1))
    finally:await engine.dispose()
