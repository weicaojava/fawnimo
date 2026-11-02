"""Actual disposable SQLite/media installation and rollback; no provider, key or original user data is used."""
import asyncio
from pathlib import Path
import os
from types import SimpleNamespace

from fastapi import HTTPException
import pytest
import pytest_asyncio
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.core.contracts.editing import EditDraftPatch, EditFreezeWrite
from app.core.contracts.local_example import example_identity
from app.models.editing import EditDraft, TakeOrigin
from app.models.execution import ExecutionJob
from app.models.local_example import LocalExampleRequest, LocalExampleReady, LocalExampleMedia, LocalExampleTake
from app.models.production import ProductionMedia, ProductionTake
from app.models.production import ShotSelection
from app.models.studio import Project, Shot, Scene
from app.services.editing.timeline import draft_view, patch_draft, freeze_draft
from app.services.editing.selection import write_selection
from app.core.contracts.editing import SelectionWrite
from sqlalchemy.exc import IntegrityError
from app.services.editing.schema import migrate_editing
from app.services.script_preparation.schema import migrate_script_preparation
from app.services.shot_plan.schema import migrate_shot_plan
from app.services.shot_plan.service import preflight_plan
from app.services.shot_plan.admission import admit_planned_shot
from app.services.studio import production_schema
from app.services.studio import local_example as example
from app.services.studio.local_example_install import LOCAL_INTENT
from app.services.worker.execution_schema import migrate_execution

WORKSPACE = 'ws_'+'a'*32
ACTOR = 'fixture_actor'
KEY = 'local-original-request-01'


@pytest_asyncio.fixture
async def setup(tmp_path, monkeypatch):
    """Use all actual schema migrations on a fresh database, never the configured user's database."""
    executable = os.environ.get('FAWNIMO_TEST_FFMPEG') or os.environ.get('TEST_MEDIA_PROBE')
    if not executable or not Path(executable).is_file():
        pytest.skip('Trusted local FFmpeg required for real export availability')
    monkeypatch.setattr(settings,'workspace_id',WORKSPACE)
    monkeypatch.setattr(settings,'workspace_runtime_locked',False)
    monkeypatch.setattr(settings,'editing_export_enabled',True)
    monkeypatch.setattr(settings,'production_media_root',str(tmp_path/'media'))
    monkeypatch.setattr(settings,'production_probe_executable',executable)
    engine = create_async_engine('sqlite+aiosqlite:///'+(tmp_path/'original-example.db').as_posix())

    @event.listens_for(engine.sync_engine,'connect')
    def constraints(connection, _):
        """Ensure scope and rollback assertions rely on real SQLite foreign keys and bounded contention."""
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA busy_timeout=5000')

    monkeypatch.setattr(production_schema,'engine',engine)
    await migrate_execution(engine)
    await production_schema.migrate_production()
    await migrate_script_preparation(engine)
    await migrate_shot_plan(engine)
    await migrate_editing(engine)
    factory = async_sessionmaker(engine,expire_on_commit=False)
    async with factory() as db:
        db.add(Project(id='user-project',name='preserved user fixture',style='test'))
        await db.commit()
    try:
        yield engine, factory, example.LocalExampleService(factory,WORKSPACE)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_actual_complete_installation_manual_anchor_gate_and_readonly_replay(setup, monkeypatch):
    """Actual14 media,7 takes and validated script/plan/draft commit once; later edits survive replay."""
    _engine, factory, service = setup
    created = await service.create(ACTOR,KEY)
    assert not created['replayed'] and created['external_model_calls']==0 and not created['complete_film_qualified']
    identity = example_identity(WORKSPACE,ACTOR,KEY)
    async with factory() as db:
        row = await db.get(LocalExampleRequest,identity['id'])
        assert row.state=='ready' and row.attempts==1 and row.lease_token is None
        for model,count in ((LocalExampleReady,1),(LocalExampleMedia,14),(LocalExampleTake,7),(ProductionMedia,14),(ProductionTake,7),(TakeOrigin,7),(Shot,7),(ExecutionJob,0)):
            assert await db.scalar(select(func.count()).select_from(model))==count
        assert (await db.get(Project,'user-project')).name=='preserved user fixture'
        draft = await draft_view(db,created['project_id'],created['draft_id'])
        assert len(draft['document']['clips'])==len(draft['document']['anchors'])==7
        assert all(not anchor['confirmed'] for anchor in draft['document']['anchors'])
        assert not draft['readiness']['can_freeze']
        original = draft['document']
        runtime = SimpleNamespace(policies={LOCAL_INTENT:object()},running=True,allow_submission=True,worker=SimpleNamespace(factory=factory))
        check = await preflight_plan(db,created['project_id'],created['plan_id'],runtime)
        assert check['usable'] and not check['can_submit']
        assert 'model_unbound' in {item['code'] for item in check['runtime_issues']}
    with pytest.raises(HTTPException) as blocked:
        await admit_planned_shot(factory,created['project_id'],created['plan_id'],created['project_id']+'-shot-1','no-model-send',runtime)
    assert blocked.value.status_code==503
    with pytest.raises(HTTPException):
        await freeze_draft(factory,created['project_id'],created['draft_id'],EditFreezeWrite(expected_revision=1))
    changed = {**original,'title':'user changed this title'}
    await patch_draft(factory,created['project_id'],created['draft_id'],EditDraftPatch(expected_revision=1,document=changed))
    monkeypatch.setattr(settings,'workspace_runtime_locked',True)
    monkeypatch.setattr(settings,'production_probe_executable','missing-disabled-tool')
    replay = await service.create(ACTOR,KEY)
    assert replay['replayed'] and replay['project_id']==created['project_id']
    async with factory() as db:
        assert (await db.get(EditDraft,created['draft_id'])).document['title']=='user changed this title'
        assert await db.scalar(select(func.count()).select_from(Project))==2
        assert await db.scalar(select(func.count()).select_from(ExecutionJob))==0


@pytest.mark.asyncio
async def test_target_created_during_render_rolls_back_all_installer_business_rows(setup, monkeypatch):
    """A real concurrent creator owns its target; the example cannot recognise or overwrite it as an old stage."""
    _engine, factory, service = setup
    original_prepare = example.prepare_media
    identity = example_identity(WORKSPACE,ACTOR,KEY)

    async def collide_after_actual_render(identifier, token):
        """Create real conflicting user content only after all original media proofs have completed."""
        result = await original_prepare(identifier,token)
        async with factory() as db:
            db.add(Project(id=identity['project_id'],name='concurrent owner content',style='test'))
            await db.commit()
        return result

    monkeypatch.setattr(example,'prepare_media',collide_after_actual_render)
    with pytest.raises(HTTPException) as collision:
        await service.create(ACTOR,KEY)
    assert collision.value.status_code==409
    async with factory() as db:
        assert (await db.get(Project,identity['project_id'])).name=='concurrent owner content'
        assert (await db.get(LocalExampleRequest,identity['id'])).state=='failedunknown'
        for model in (ProductionMedia,ProductionTake,LocalExampleReady,Shot,EditDraft):
            assert await db.scalar(select(func.count()).select_from(model))==0


@pytest.mark.asyncio
async def test_repeated_cancel_at_real_commit_joins_and_recovers_committed_ready(setup, monkeypatch):
    """Cancellation at commit cannot free the lane, close SQLite early, mark a committed Ready failed or reseed it."""
    engine, factory, _service = setup
    entered, release = asyncio.Event(), asyncio.Event()
    original_install = example.install

    class GatedSession(AsyncSession):
        """Hold only the final real commit while retaining the actual connection and business writer."""
        async def commit(self):
            """Keep the actual commit pending until repeated cancellation has been observed."""
            if self.info.get('final_example_commit'):
                entered.set()
                await release.wait()
            await super().commit()

    async def mark_final_commit(db, *arguments):
        """Run the real complete installer, then mark only its fresh session for the commit gate."""
        result = await original_install(db,*arguments)
        db.info['final_example_commit'] = True
        return result

    joined_factory = async_sessionmaker(engine,class_=GatedSession,expire_on_commit=False)
    service = example.LocalExampleService(joined_factory,WORKSPACE)
    monkeypatch.setattr(example,'install',mark_final_commit)
    task = asyncio.create_task(service.create(ACTOR,KEY))
    try:
        await asyncio.wait_for(entered.wait(),timeout=30)
        for _ in range(4):
            task.cancel()
            await asyncio.sleep(0)
        assert not task.done() and service.lane.locked()
        async with factory() as db:
            assert await db.get(Project,example_identity(WORKSPACE,ACTOR,KEY)['project_id']) is None
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not service.lane.locked()
        replay = await service.create(ACTOR,KEY)
        assert replay['replayed']
        async with factory() as db:
            assert (await db.get(LocalExampleRequest,replay['project_id'])).state=='ready'
            assert await db.scalar(select(func.count()).select_from(ProductionTake))==7
            assert await db.scalar(select(func.count()).select_from(Project))==2
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await example.join_task(task)


@pytest.mark.asyncio
async def test_typed_ready_history_is_retained_and_later_user_selection_is_preserved(setup):
    """Raw SQL cannot replace the receipt, while normal explicit adoption remains editable and survives old replay."""
    _engine, factory, service = setup
    created = await service.create(ACTOR,KEY)
    async with factory() as db:
        draft = await db.get(EditDraft,created['draft_id'])
        clip = draft.document['clips'][0]
        take = await db.get(ProductionTake,clip['take_id'])
        media_id = take.media_id
        for table in ('local_example_requests','local_example_ready','local_example_media','local_example_takes'):
            for sql in (f'UPDATE {table} SET '+('reason=reason' if table=='local_example_requests' else 'request_id=request_id'),
                        f'DELETE FROM {table}',f'INSERT OR REPLACE INTO {table} SELECT * FROM {table}'):
                with pytest.raises(IntegrityError):
                    await db.execute(text(sql))
                await db.rollback()
    selected = await write_selection(factory,created['project_id'],SelectionWrite(request_key='explicit-user-later-adoption',
        shot_id=clip['shot_id'],expected_revision=1,media_id=media_id,plan_id=created['plan_id'],change_note='fixture explicitly adopts same original clip as a later user intent'))
    assert selected['take_id']!=clip['take_id']
    replay = await service.create(ACTOR,KEY)
    assert replay['replayed']
    async with factory() as db:
        assert (await db.get(ShotSelection,clip['shot_id'])).take_id==selected['take_id']
        assert (await db.get(EditDraft,created['draft_id'])).document['clips'][0]['take_id']==clip['take_id']
        assert await db.scalar(select(func.count()).select_from(LocalExampleTake))==7
        assert await db.scalar(select(func.count()).select_from(ProductionTake))==8


@pytest.mark.asyncio
async def test_missing_ready_file_is_a_conflict_without_repair_or_new_attempt(setup, monkeypatch):
    """Completed recovery cannot silently regenerate a deleted file or claim a later user's edits were reset."""
    _engine, factory, service = setup
    created = await service.create(ACTOR,KEY)
    async with factory() as db:
        media = (await db.execute(select(ProductionMedia))).scalars().first()
        path = Path(settings.production_media_root).resolve()/media.relative_path
        assert path.resolve().is_relative_to(Path(settings.production_media_root).resolve())
        path.unlink()

    async def refuse_render(*_arguments):
        """No renderer may run during a completed history replay, including after byte loss."""
        raise AssertionError('Completed replay attempted to repair media')

    monkeypatch.setattr(example,'prepare_media',refuse_render)
    with pytest.raises(HTTPException) as missing:
        await service.create(ACTOR,KEY)
    assert missing.value.status_code==409 and not path.exists()
    async with factory() as db:
        row = await db.get(LocalExampleRequest,created['project_id'])
        assert row.state=='ready' and row.attempts==1
        assert await db.scalar(select(func.count()).select_from(Project))==2


@pytest.mark.asyncio
async def test_stale_installer_cannot_commit_or_release_a_newer_owner(setup, monkeypatch):
    """A new lease established after actual decoding fences the old transaction and its failure cleanup."""
    _engine, factory, service = setup
    original_prepare = example.prepare_media
    identity = example_identity(WORKSPACE,ACTOR,KEY)
    new_token = None

    async def hand_over_after_render(identifier, token):
        """Represent a separately recovered owner without running another renderer or mutating user content."""
        nonlocal new_token
        result = await original_prepare(identifier,token)
        await service.uncertain(identifier,token)
        new_token = await service.claim(identity)
        return result

    monkeypatch.setattr(example,'prepare_media',hand_over_after_render)
    with pytest.raises(HTTPException) as lost:
        await service.create(ACTOR,KEY)
    assert lost.value.status_code==409
    async with factory() as db:
        row = await db.get(LocalExampleRequest,identity['id'])
        assert row.state=='running' and row.attempts==2 and row.lease_token==new_token
        assert await db.get(Project,identity['project_id']) is None
        assert await db.scalar(select(func.count()).select_from(ProductionMedia))==0


@pytest.mark.asyncio
async def test_worker_lane_rejects_other_intent_without_consuming_attempt(setup, monkeypatch):
    """Per-key limits cannot multiply render concurrency, and a busy refusal leaves no second request row."""
    _engine, factory, service = setup
    original_prepare = example.prepare_media
    entered, release = asyncio.Event(), asyncio.Event()

    async def gated_prepare(identifier, token):
        """Keep the first actual lane occupied until the concurrent admission assertion is complete."""
        entered.set(); await release.wait()
        return await original_prepare(identifier,token)

    monkeypatch.setattr(example,'prepare_media',gated_prepare)
    first = asyncio.create_task(service.create(ACTOR,KEY))
    try:
        await asyncio.wait_for(entered.wait(),timeout=10)
        with pytest.raises(HTTPException) as busy:
            await service.create(ACTOR,'second-local-intent')
        assert busy.value.status_code==429
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(LocalExampleRequest))==1
        release.set()
        assert (await first)['state']=='ready'
    finally:
        release.set()
        await example.join_task(first,cancel=True)


@pytest.mark.asyncio
async def test_two_fresh_examples_use_distinct_legacy_scene_names_and_preserve_first_edits(setup):
    """Reset creates a second complete project without colliding with or claiming the first global scene identity."""
    _engine, factory, service = setup
    first = await service.create(ACTOR,KEY)
    async with factory() as db:
        draft = await db.get(EditDraft,first['draft_id'])
        document = {**draft.document,'title':'first example user edit retained'}
    await patch_draft(factory,first['project_id'],first['draft_id'],EditDraftPatch(expected_revision=1,document=document))
    second = await service.create(ACTOR,'second-complete-example')
    assert first['project_id']!=second['project_id'] and not second['replayed']
    async with factory() as db:
        assert (await db.get(EditDraft,first['draft_id'])).document['title']==document['title']
        names = (await db.execute(select(Scene.name))).scalars().all()
        assert set(names)=={'透明几何小岛','透明几何小岛（示例2）'}
        assert await db.scalar(select(func.count()).select_from(ProductionMedia))==28
        assert await db.scalar(select(func.count()).select_from(LocalExampleReady))==2
        assert await db.scalar(select(func.count()).select_from(ExecutionJob))==0
