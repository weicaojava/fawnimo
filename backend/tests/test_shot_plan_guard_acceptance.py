"""Independent guard checks against actual scoped SQLite rows; no model access."""
from copy import deepcopy
import pytest
from fastapi import HTTPException
from httpx import ASGITransport,AsyncClient
from sqlalchemy import select,func
from app.main import app
from app.dependencies import get_db
from app.models.studio import Actor,Scene,Prop,Costume,ProjectActorLink,ProjectSceneLink,ProjectPropLink,ProjectCostumeLink,Shot,ShotDetail
from app.models.task import GenerationTask
from app.models.task_links import GenerationTaskLink
from app.core.contracts.script_preparation import ScriptCreate
from app.services.script_preparation.service import create_draft
from app.services.shot_plan.legacy_guard import projects_for_subject,assert_legacy_allowed,assert_script_task_allowed
from tests.test_script_preparation_acceptance import fixture as script_fixture,document


async def setup(tmp_path,monkeypatch):
    """Create registered A, unregistered B and shared globals with B linked first."""
    engine,factory=await script_fixture(tmp_path,monkeypatch)
    await create_draft(factory,'a',ScriptCreate(chapter_id='chapter-a1',document=document()))
    models={'actor':Actor,'scene':Scene,'prop':Prop,'costume':Costume}
    links={'actor':(ProjectActorLink,'actor_id'),'scene':(ProjectSceneLink,'scene_id'),
           'prop':(ProjectPropLink,'prop_id'),'costume':(ProjectCostumeLink,'costume_id')}
    async with factory() as db:
        for kind,model in models.items():
            db.add(model(id='shared-'+kind,name='shared-'+kind,style='真人古装',visual_style='现实'))
            db.add(model(id='isolated-'+kind,name='isolated-'+kind,style='真人古装',visual_style='现实'))
        await db.flush()
        for kind,(link,field) in links.items():
            for project in ['b','a']:db.add(link(project_id=project,**{field:'shared-'+kind}))
        await db.commit()
    return engine,factory


async def state(factory):
    """Count side-effect records and preserve actual shot fields before refusal."""
    async with factory() as db:
        counts=[await db.scalar(select(func.count()).select_from(model)) for model in [GenerationTask,GenerationTaskLink,ShotDetail]]
        shot=await db.get(Shot,'shot-a1')
        return counts,(shot.status,shot.generated_video_file_id,shot.script_excerpt)


@pytest.mark.asyncio
async def test_shared_all_project_union_known_isolated_and_missing_subject(tmp_path,monkeypatch):
    """A first unregistered association cannot hide a registered one; orphan globals work."""
    engine,factory=await setup(tmp_path,monkeypatch)
    try:
        baseline=await state(factory)
        async with factory() as db:
            for kind in ['actor','scene','prop','costume']:
                actual=await db.run_sync(lambda sync:projects_for_subject(sync,kind,'shared-'+kind))
                assert actual=={'a','b'}
                with pytest.raises(HTTPException) as denied:
                    await assert_legacy_allowed(db,kind,'shared-'+kind)
                assert denied.value.status_code==409
                await assert_legacy_allowed(db,kind,'isolated-'+kind)
            for kind,identity in [('actor','missing'),('unknown','shared-actor'),('actor','')]:
                with pytest.raises(HTTPException) as denied:await assert_legacy_allowed(db,kind,identity)
                assert denied.value.status_code==409
        assert await state(factory)==baseline
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_http_refuses_before_model_task_enqueue_and_shot_mutation(tmp_path,monkeypatch):
    """Exercise actual API routing, not a replacement guard, before all side effects."""
    import app.services.studio.image_task_runner as images
    import app.api.v1.routes.film.tasks_images as film
    engine,factory=await setup(tmp_path,monkeypatch)
    touched=[]
    async def forbidden_async(*args,**kwargs):
        """Model resolution must remain unreachable for registered legacy requests."""
        touched.append('model');raise AssertionError('model resolution reached')
    def forbidden_enqueue(*args,**kwargs):
        """No transient delivery may happen before the registration guard."""
        touched.append('enqueue');raise AssertionError('enqueue reached')
    async def dependency():
        """Use one real session per request, committing only successful requests."""
        async with factory() as db:
            try:yield db;await db.commit()
            except BaseException:await db.rollback();raise
    old=app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db]=dependency
    monkeypatch.setattr(images,'resolve_image_model',forbidden_async)
    monkeypatch.setattr(film,'enqueue_task_execution',forbidden_enqueue)
    try:
        baseline=await state(factory)
        requests=[('/api/v1/studio/image-tasks/actors/shared-actor/image-tasks',{'prompt':'inert fixture'}),
                  ('/api/v1/studio/image-tasks/characters/shared-hero/image-tasks',{'prompt':'inert fixture'}),
                  ('/api/v1/studio/image-tasks/shot/shot-a1/frame-image-tasks',{'frame_type':'first','target_ratio':'16:9','prompt':'inert fixture'}),
                  ('/api/v1/film/tasks/shot-frame-prompts',{'shot_id':'shot-a1','frame_type':'first'})]
        requests += [('/api/v1/studio/image-tasks/assets/'+kind+'/shared-'+kind+'/image-tasks',{'prompt':'inert fixture'}) for kind in ['scene','prop','costume']]
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',trust_env=False) as client:
            for path,body in requests:
                response=await client.post(path,json=body)
                assert response.status_code==409,(path,response.status_code,response.text)
        assert touched==[] and await state(factory)==baseline
    finally:
        if old is None:app.dependency_overrides.pop(get_db,None)
        else:app.dependency_overrides[get_db]=old
        await engine.dispose()


@pytest.mark.asyncio
async def test_image_worker_refuses_shared_scope_before_provider_and_retains_old_result(tmp_path,monkeypatch):
    """A delayed old image worker may report refusal but cannot erase prior media results."""
    import app.services.studio.image_task_runner as images
    engine,factory=await setup(tmp_path,monkeypatch)
    touched=[]
    def forbidden(*args,**kwargs):
        """Any provider task construction after denial would be a new external-call risk."""
        touched.append('provider');raise AssertionError('provider construction reached')
    monkeypatch.setattr(images,'async_session_maker',factory)
    monkeypatch.setattr(images,'ImageGenerationTask',forbidden)
    old_result={'file_id':'historical-file','must_remain':True}
    try:
        async with factory() as db:
            db.add(GenerationTask(id='legacy-worker',mode='async_polling',task_kind='image_generation',status='succeeded',progress=100,payload={},result=deepcopy(old_result),error=''))
            await db.commit()
        baseline=await state(factory)
        await images.run_image_generation_task('legacy-worker',{'relation_type':'actor','relation_entity_id':'shared-actor'})
        async with factory() as db:
            task=await db.get(GenerationTask,'legacy-worker')
            assert task.result==old_result and '对账' in task.error
        assert touched==[] and await state(factory)==baseline
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_persisted_prefixed_relation_cannot_hide_behind_unregistered_payload_project(tmp_path,monkeypatch):
    """A linked shared identity participates in scope even when payload names safe B."""
    engine,factory=await setup(tmp_path,monkeypatch)
    try:
        async with factory() as db:
            db.add(GenerationTask(id='linked-script-task',mode='async_polling',task_kind='script',status='succeeded',progress=100,payload={},result={'preserve':True},error=''))
            await db.flush()
            db.add(GenerationTaskLink(task_id='linked-script-task',resource_type='text',relation_type='actor',relation_entity_id='actor:shared-actor'))
            await db.commit()
        baseline=await state(factory)
        async with factory() as db:
            with pytest.raises(HTTPException) as error:
                await assert_script_task_allowed(db,'linked-script-task',{'project_id':'b'})
            assert error.value.status_code==409
            with pytest.raises(HTTPException) as missing:
                await assert_script_task_allowed(db,'no-actual-task-link',{})
            assert missing.value.status_code==409
        assert await state(factory)==baseline
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_shot_prompt_worker_does_not_construct_llm_or_erase_old_result(tmp_path,monkeypatch):
    """Background prompt generation has the same guard as HTTP before LLM construction."""
    import app.services.film.shot_frame_prompt_tasks as prompts
    engine,factory=await setup(tmp_path,monkeypatch)
    calls=[]
    def forbidden(*args,**kwargs):
        """Prove no text-model construction occurs before the scope refusal."""
        calls.append('llm');raise AssertionError('LLM construction reached')
    monkeypatch.setattr(prompts,'async_session_maker',factory)
    monkeypatch.setattr(prompts,'build_default_text_llm_sync',forbidden)
    previous={'prompt':'existing result must remain'}
    try:
        async with factory() as db:
            db.add(GenerationTask(id='old-shot-prompt',mode='async_polling',task_kind='shot_frame_prompt',status='succeeded',progress=100,payload={},result=previous,error=''))
            await db.commit()
        baseline=await state(factory)
        await prompts.run_shot_frame_prompt_task('old-shot-prompt',{'shot_id':'shot-a1','frame_type':'first'})
        async with factory() as db:
            task=await db.get(GenerationTask,'old-shot-prompt')
            assert task.result==previous and '对账' in task.error
        assert calls==[] and await state(factory)==baseline
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_registered_local_preview_preserves_real_director_and_frame_guidance(tmp_path,monkeypatch):
    """Registration blocks paid tasks, not the pure builder's substantive local guidance."""
    import app.api.v1.routes.studio.image_tasks as routes
    engine,factory=await setup(tmp_path,monkeypatch)
    try:
        async with factory() as db:
            db.add(ShotDetail(id='shot-a1',scene_id='scene-a',camera_shot='MS',angle='EYE_LEVEL',movement='STATIC'))
            await db.commit()
        baseline=await state(factory)
        async with factory() as db:
            guidance=await routes._load_frame_render_guidance(db=db,shot_id='shot-a1',frame_type='first')
            assert guidance['director_command_summary'].strip()
            assert guidance['frame_specific_guidance'].strip()
            with pytest.raises(HTTPException) as denied:
                await assert_legacy_allowed(db,'shot','shot-a1')
            assert denied.value.status_code==409
        assert await state(factory)==baseline
    finally:await engine.dispose()
