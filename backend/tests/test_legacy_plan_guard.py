"""Author tests for legacy scope guards; all model constructors are forbidden sentinels."""
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from httpx import ASGITransport,AsyncClient
from sqlalchemy import func,select,update
from app.main import app
from app.dependencies import get_db
from app.models.studio import Actor,ActorImage,Character,CharacterPropLink,Costume,Prop,ProjectActorLink,ProjectCostumeLink,ProjectPropLink,ProjectSceneLink,Shot,ShotDetail,ShotFrameImage
from app.models.task import GenerationTask
from app.models.task_links import GenerationTaskLink
from app.services.shot_plan.legacy_guard import assert_legacy_allowed,assert_script_request_allowed
from app.services.script_preparation.service import create_draft
from app.core.contracts.script_preparation import ScriptCreate
from tests.test_script_preparation import fixture,document


async def guarded_fixture(tmp_path):
    """Share real globals between registered a and unregistered b, with frame history."""
    engine,factory=await fixture(tmp_path)
    async with factory() as db:
        db.add_all([Actor(id='shared-actor',name='shared',style='test'),Actor(id='isolated-actor',name='isolated',style='test'),Costume(id='shared-costume',name='shared',style='test'),Prop(id='shared-prop',name='shared',style='test')]);await db.flush()
        await db.execute(update(Character).where(Character.id=='hero').values(actor_id='shared-actor',costume_id='shared-costume'))
        db.add(CharacterPropLink(character_id='hero',prop_id='shared-prop',index=0))
        db.add_all([ProjectActorLink(project_id='b',actor_id='shared-actor'),ProjectCostumeLink(project_id='b',costume_id='shared-costume'),ProjectPropLink(project_id='b',prop_id='shared-prop'),ProjectSceneLink(project_id='b',scene_id='scene')])
        db.add(ShotDetail(id='s1',scene_id='scene',camera_shot='MS',angle='EYE_LEVEL',movement='STATIC'));await db.flush()
        frame=ShotFrameImage(shot_detail_id='s1',frame_type='first',file_id=None);db.add(frame)
        actor_image=ActorImage(actor_id='shared-actor');db.add(actor_image);await db.flush()
        frame_id,actor_image_id=frame.id,actor_image.id;await db.commit()
    await create_draft(factory,'a',ScriptCreate(chapter_id='c1',document=document()))
    return engine,factory,frame_id,actor_image_id


@pytest.mark.asyncio
async def test_all_real_scopes_and_known_isolated_global_policy(tmp_path):
    """A shared unregistered association cannot hide a registered role/project scope."""
    engine,factory,frame,actor_image=await guarded_fixture(tmp_path)
    try:
        async with factory() as db:
            for kind,identity in [('project','a'),('chapter','c1'),('shot','s1'),('character','hero'),('actor','shared-actor'),('costume','shared-costume'),('prop','shared-prop'),('scene','scene'),('shot_frame_image',str(frame)),('actor_image',str(actor_image)),('missing','s1'),('actor','missing')]:
                with pytest.raises(HTTPException) as error:await assert_legacy_allowed(db,kind,identity)
                assert error.value.status_code==409
            await assert_legacy_allowed(db,'project','b')
            await assert_legacy_allowed(db,'actor','isolated-actor')
            with pytest.raises(HTTPException):await assert_script_request_allowed(db,{'project_id':'b','chapter_id':'c1'})
            with pytest.raises(HTTPException):await assert_script_request_allowed(db,{'relation_entity_id':'actor:shared-actor'})
            await assert_script_request_allowed(db,{'script_text':'unscoped legacy tool'})
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_http_guards_precede_task_slot_or_model_side_effects(tmp_path,monkeypatch):
    """Direct HTTP bypasses reject before frame placeholders, task rows or any enqueue."""
    engine,factory,_,_=await guarded_fixture(tmp_path)
    async def dependency():
        """Keep each HTTP test transaction scoped to its own isolated database session."""
        async with factory() as db:
            try:yield db;await db.commit()
            except BaseException:await db.rollback();raise
    app.dependency_overrides[get_db]=dependency
    import app.api.v1.routes.studio.image_tasks as routes
    import app.api.v1.routes.film.tasks_images as prompts
    async def forbidden(*args,**kwargs):
        """Any post-guard model payload builder proves an ordering regression."""
        raise AssertionError('forbidden downstream side effect')
    monkeypatch.setattr(routes,'_build_actor_image_submission_payload_service',forbidden)
    monkeypatch.setattr(routes,'_build_asset_image_submission_payload_service',forbidden)
    monkeypatch.setattr(routes,'_build_character_image_submission_payload_service',forbidden)
    monkeypatch.setattr(prompts,'build_shot_frame_prompt_run_args',forbidden)
    try:
        async with factory() as db:
            guidance=await routes._load_frame_render_guidance(db=db,shot_id='s1',frame_type='first')
            assert guidance['director_command_summary']
            assert guidance['frame_specific_guidance']
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',trust_env=False) as client:
            entries=[('/studio/image-tasks/actors/shared-actor/image-tasks',{'prompt':'test'}),('/studio/image-tasks/characters/hero/image-tasks',{'prompt':'test'}),('/studio/image-tasks/assets/scene/scene/image-tasks',{'prompt':'test'}),('/studio/image-tasks/assets/prop/shared-prop/image-tasks',{'prompt':'test'}),('/studio/image-tasks/assets/costume/shared-costume/image-tasks',{'prompt':'test'}),('/studio/image-tasks/shot/s1/frame-image-tasks',{'prompt':'test','frame_type':'last','target_ratio':'16:9'}),('/film/tasks/shot-frame-prompts',{'shot_id':'s1','frame_type':'first'}),('/script-processing/divide-async',{'chapter_id':'c1','script_text':'test'}),('/script-processing/divide',{'chapter_id':'c1','script_text':'test'}),('/script-processing/analyze-prop-info',{'relation_entity_id':'prop:shared-prop','prop_description':'test'})]
            for path,body in entries:
                response=await client.post('/api/v1'+path,json=body)
                assert response.status_code==409,(path,response.status_code,response.text)
            preview=await client.post('/api/v1/studio/image-tasks/shot/s1/frame-render-prompt',json={'frame_type':'first','prompt':'local template'})
            assert preview.status_code==200,preview.text
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(GenerationTask))==0
            assert await db.scalar(select(func.count()).select_from(GenerationTaskLink))==0
            assert await db.scalar(select(func.count()).select_from(ShotFrameImage))==1
            assert (await db.get(Shot,'s1')).status=='pending'
    finally:app.dependency_overrides.clear();await engine.dispose()


@pytest.mark.asyncio
async def test_worker_guards_preserve_existing_results_and_shot_frames(tmp_path,monkeypatch):
    """Queued legacy restarts cannot call providers after story registration or erase history."""
    engine,factory,frame_id,_=await guarded_fixture(tmp_path)
    import app.services.studio.image_task_runner as images
    import app.services.film.shot_frame_prompt_tasks as prompts
    import app.services.script_processing_tasks as scripts
    import app.services.script_processing_worker as sync_scripts
    monkeypatch.setattr(images,'async_session_maker',factory)
    monkeypatch.setattr(prompts,'async_session_maker',factory)
    monkeypatch.setattr(scripts,'async_session_maker',factory)
    def forbidden(*args,**kwargs):
        """Do not construct even a fixture provider when a guard should reject."""
        raise AssertionError('provider construction forbidden')
    monkeypatch.setattr(images,'ImageGenerationTask',forbidden)
    monkeypatch.setattr(prompts,'build_default_text_llm_sync',forbidden)
    monkeypatch.setattr(scripts,'get_llm',forbidden)
    try:
        async with factory() as db:
            for identity in ['image','prompt','merge','variant','sync']:
                db.add(GenerationTask(id=identity,mode='async_polling',task_kind='legacy',status='pending',payload={'run_args':{}},result={'old':'preserve'}))
            await db.flush()
            for identity in ['merge','variant','sync']:db.add(GenerationTaskLink(task_id=identity,resource_type='task_link',relation_type='chapter_division',relation_entity_id='c1'))
            await db.commit()
        await images.run_image_generation_task('image',{'relation_type':'shot_frame_image','relation_entity_id':str(frame_id)})
        await prompts.run_shot_frame_prompt_task('prompt',{'shot_id':'s1','frame_type':'first'})
        await scripts.run_merge_task('merge');await scripts.run_variant_task('variant')
        executor=sync_scripts.DivideTaskExecutor();executor._generator=SimpleNamespace(generate=forbidden)
        async with factory() as db:
            with pytest.raises(HTTPException):await db.run_sync(lambda session:executor.execute(SimpleNamespace(db=session,task_id='sync'),{'chapter_id':'c1'}))
            for identity in ['image','prompt','merge','variant']:
                row=await db.get(GenerationTask,identity)
                assert row.result=={'old':'preserve'} and row.status=='failed'
            assert (await db.get(Shot,'s1')).status=='pending'
            assert await db.scalar(select(func.count()).select_from(ShotFrameImage))==1
    finally:await engine.dispose()
