"""Exercise real source clocks and SQLite transactions across the DNA production handoff."""
import asyncio
from copy import deepcopy
from io import BytesIO
from PIL import Image
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from app.core.contracts.dna_production import DnaProductionWrite
from app.core.contracts.video_dna import DnaCreate, DnaPatch
from app.models.dna_production import DnaProductionImport
from app.models.production import ProductionAsset
from app.models.shot_plan import ShotPlanDraft
from app.models.studio import Actor, Chapter, Costume, PromptTemplate, Shot, ShotDetail
from app.services.video_dna import service
from app.services.video_dna.production import import_draft, preview
from app.services.studio.production_media import store_batch
from app.services.studio.project_summary import project_summaries
from tests.test_video_dna import fixture


async def source_draft(tmp_path, monkeypatch):
    """Use a genuinely decoded offset MP4 while keeping observations explicitly unreviewed."""
    engine, factory, analysis = await fixture(tmp_path, monkeypatch)
    identifier = await service.create_draft(factory, 'p', DnaCreate(analysis_id=analysis, expected_analysis_revision=2, request_key='production-source'))
    async with factory() as db:
        view = await service.draft_view(db, 'p', identifier)
    document = deepcopy(view['document'])
    document['title'] = 'Source-backed pending chapter'
    clothing = {'zh': '白色外袍', 'status': 'inferred', 'reason': 'Software test hypothesis, not a human signoff', 'evidence': [{'start_frame': 0, 'end_frame': 12}]}
    document['assets'] = [
        {'id': 'hero', 'kind': 'character', 'name_zh': '待确认角色', 'fields': {'costume': clothing}},
        {'id': 'room', 'kind': 'scene', 'name_zh': '待确认场景'},
        {'id': 'letter', 'kind': 'prop', 'name_zh': '待确认道具'},
    ]
    for shot in document['shots']:
        shot.update(character_ids=['hero'], scene_id='room', prop_ids=['letter'])
        for key in ('image_prompt', 'video_prompt'):
            shot[key] = {'zh': '原片参考推断草稿', 'en': 'An inferred draft', 'status': 'inferred', 'reason': 'Software hypothesis only', 'evidence': [{'start_frame': shot['start_frame'], 'end_frame': shot['end_frame']}]}
    await service.patch_draft(factory, 'p', identifier, DnaPatch(expected_revision=1, document=document))
    return engine, factory, identifier


async def row_count(factory, model):
    """Measure persisted rows after both successful commits and rejected imports."""
    async with factory() as db:
        return (await db.execute(select(func.count()).select_from(model))).scalar_one()


@pytest.mark.asyncio
async def test_actual_asset_prompt_plan_import_and_reload_preserve_pending(tmp_path, monkeypatch):
    """Global library, project versions, per-shot prompts and exact source evidence stay connected."""
    engine, factory, draft_id = await source_draft(tmp_path, monkeypatch)
    try:
        before = await row_count(factory, Shot)
        view = await preview(factory, 'p', draft_id)
        assert view['counts'] == {'actors': 1, 'characters': 1, 'costumes': 1, 'scenes': 1, 'props': 1, 'shots': 2, 'prompts': 8}
        result = await import_draft(factory, 'p', draft_id, DnaProductionWrite(expected_revision=2, request_key='actual-import'))
        assert result['status'] == 'pending_confirmation' and len(result['prompt_template_ids']) == 8
        assert await row_count(factory, Shot) == before + 2
        async with factory() as db:
            receipt = await db.get(DnaProductionImport, result['id'])
            assert receipt.document['human_reviewed'] is False
            assert receipt.source['frames'][0]['pts'] > 0
            assert receipt.request['shot_frame_bindings'][-1]['end_us'] is None
            assert receipt.request['shot_frame_bindings'][-1]['planned_duration_seconds'] == 0
            for shot_id in result['shot_ids']:
                assert (await db.get(Shot, shot_id)).status == 'pending'
            assert (await db.get(ShotDetail, result['shot_ids'][-1])).duration == 0
            binding = result['asset_bindings']['hero']
            assert (await db.get(Actor, binding['actor_id'])).prompt_template_id
            assert (await db.get(Costume, binding['costume_id'])).prompt_template_id
            plan = await db.get(ShotPlanDraft, result['plan_draft_id'])
            assert plan.document['script_snapshot_id'] is None and plan.document['model_id'] == ''
            assert all(shot['prompt_addition'] == '原片参考推断草稿' for shot in plan.document['shots'])
        assert (await preview(factory, 'p', draft_id))['already_imported'] == result
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_replay_creates_one_chapter_and_never_overwrites_new_revision(tmp_path, monkeypatch):
    """Concurrent imports converge; a later edited draft cannot silently masquerade as old output."""
    engine, factory, draft_id = await source_draft(tmp_path, monkeypatch)
    try:
        before = await row_count(factory, Chapter)
        body = DnaProductionWrite(expected_revision=2, request_key='parallel-import')
        first, second = await asyncio.gather(import_draft(factory, 'p', draft_id, body), import_draft(factory, 'p', draft_id, body))
        assert first == second and await row_count(factory, Chapter) == before + 1
        async with factory() as db:
            document = (await service.draft_view(db, 'p', draft_id))['document']
        document['title'] = 'A later user edit'
        await service.patch_draft(factory, 'p', draft_id, DnaPatch(expected_revision=2, document=document))
        assert await import_draft(factory, 'p', draft_id, body) == first
        with pytest.raises(HTTPException) as error:
            await import_draft(factory, 'p', draft_id, DnaProductionWrite(expected_revision=3, request_key='new-revision'))
        assert error.value.status_code == 409
        assert await row_count(factory, Chapter) == before + 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('problem', ['foreign_project', 'foreign_media', 'stale_revision', 'invalid_mapping'])
async def test_bad_scope_or_input_has_no_partial_assets(tmp_path, monkeypatch, problem):
    """Reject invalid handoffs before an actor, prompt or chapter can partially appear."""
    engine, factory, draft_id = await source_draft(tmp_path, monkeypatch)
    try:
        before = [await row_count(factory, model) for model in (Actor, Costume, PromptTemplate, ProductionAsset, Chapter)]
        body = DnaProductionWrite(expected_revision=2, request_key='bad-input')
        project_id = 'p'
        if problem == 'foreign_project': project_id = 'other-project'
        elif problem == 'foreign_media': body.asset_media_ids = {'hero': 'foreign-media'}
        elif problem == 'stale_revision': body.expected_revision = 1
        else: body.shot_media_ids = {'not-a-source-shot': 'not-a-real-image'}
        with pytest.raises(HTTPException) as error:
            await import_draft(factory, project_id, draft_id, body)
        assert error.value.status_code in (404, 409, 422)
        assert [await row_count(factory, model) for model in (Actor, Costume, PromptTemplate, ProductionAsset, Chapter)] == before
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_late_validation_failure_rolls_back_already_created_assets(tmp_path, monkeypatch):
    """An invalid later asset must roll back earlier actor, costume, version and prompt inserts."""
    engine, factory, draft_id = await source_draft(tmp_path, monkeypatch)
    try:
        async with factory() as db:
            document = (await service.draft_view(db, 'p', draft_id))['document']
        document['assets'][-1]['name_zh'] = 'x' * 121
        await service.patch_draft(factory, 'p', draft_id, DnaPatch(expected_revision=2, document=document))
        models = (Actor, Costume, PromptTemplate, ProductionAsset, Chapter, DnaProductionImport)
        before = [await row_count(factory, model) for model in models]
        with pytest.raises(HTTPException):
            await import_draft(factory, 'p', draft_id, DnaProductionWrite(expected_revision=3, request_key='rollback'))
        assert [await row_count(factory, model) for model in models] == before
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_import_receipt_cannot_be_updated(tmp_path, monkeypatch):
    """Editable production records cannot erase their original source/claim receipt."""
    engine, factory, draft_id = await source_draft(tmp_path, monkeypatch)
    try:
        result = await import_draft(factory, 'p', draft_id, DnaProductionWrite(expected_revision=2, request_key='immutable'))
        with pytest.raises(Exception, match='Immutable DNA import receipt'):
            async with engine.begin() as connection:
                await connection.execute(text('UPDATE dna_production_imports SET document_hash=:digest WHERE id=:id'), {'digest': 'changed', 'id': result['id']})
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_source_reference_images_bind_to_actor_and_costume_without_cross_project_escape(tmp_path, monkeypatch):
    """Project-linked global assets may use their source images while foreign actors stay blocked."""
    from app.models.studio import ActorImage, CostumeImage, Project
    from app.services.studio import production_schema
    from app.services.studio.entity_crud import create_entity
    engine, factory, draft_id = await source_draft(tmp_path, monkeypatch)
    try:
        monkeypatch.setattr(production_schema, 'engine', engine)
        await production_schema.migrate_production()
        data = BytesIO()
        Image.new('RGB', (32, 32), 'blue').save(data, format='PNG')
        async with factory() as db:
            media_id = (await store_batch(db, 'p', [('reference.png', data.getvalue())]))[0].id
            document = (await service.draft_view(db, 'p', draft_id))['document']
        result = await import_draft(factory, 'p', draft_id, DnaProductionWrite(expected_revision=2, request_key='actual-images', asset_media_ids={'hero': media_id}, shot_media_ids={document['shots'][0]['id']: media_id}))
        async with factory() as db:
            binding = result['asset_bindings']['hero']
            assert (await db.execute(select(ActorImage).where(ActorImage.actor_id == binding['actor_id']))).scalars().first().file_id == media_id
            assert (await db.execute(select(CostumeImage).where(CostumeImage.costume_id == binding['costume_id']))).scalars().first().file_id == media_id
            db.add(Project(id='foreign-global-library', name='Other project', style='真人都市', visual_style='现实'))
            await db.flush()
            await create_entity(db, entity_type='actor', body={'id': 'foreign-actor', 'project_id': 'foreign-global-library', 'name': 'Foreign actor'})
            await db.commit()
        with pytest.raises(Exception, match='Cross-project reference rejected'):
            async with factory() as db:
                image = (await db.execute(select(ActorImage).where(ActorImage.actor_id == 'foreign-actor'))).scalars().first()
                image.file_id = media_id
                await db.flush()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_project_summary_uses_saved_entities_instead_of_stale_client_counts(tmp_path, monkeypatch):
    """The restored project's actual assets must be visible in its lobby and overview totals."""
    from app.models.studio import Project
    engine, factory, draft_id = await source_draft(tmp_path, monkeypatch)
    try:
        await import_draft(factory, 'p', draft_id, DnaProductionWrite(expected_revision=2, request_key='summary'))
        async with factory() as db:
            project = await db.get(Project, 'p')
            project.stats = {'roles': 999, 'scenes': 999, 'custom_note': 'preserved'}
            result = (await project_summaries(db, [project]))[0]
            assert result.stats['roles'] != 999 and result.stats['scenes'] != 999
            assert result.stats['actors'] == 1 and result.stats['costumes'] == 1
            assert result.stats['custom_note'] == 'preserved'
    finally:
        await engine.dispose()
