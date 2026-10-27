"""Independent actual SQLite planned execution tests; fixtures are not model output."""
import asyncio
from copy import deepcopy
import hashlib
import io
import json
import os
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from sqlalchemy import select,func,text,update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from PIL import Image
from httpx import ASGITransport,AsyncClient
from app.config import settings
from app.core.contracts.shot_plan import PlanCreate,PlanPatch,PlanCompile,AssetRegistration
from app.core.contracts.script_preparation import ScriptCreate,ScriptPublish
from app.core.contracts.execution_capabilities import Capability
from app.core.contracts.execution_binding import binding_fingerprint
from app.models.production import ProductionAsset,AssetVersion,VersionMedia,ProductionMedia,ProductionTake,ShotSelection
from app.models.studio import Shot,Character,FileItem
from app.models.execution import ExecutionJob,SubmissionAttempt
from app.models.execution_budget import ExecutionBudgetEntry
from app.models.shot_plan import ShotPlan,PlanJobLink
from app.schemas.studio.production import VersionWrite
from app.services.studio.production import add_version
from app.services.script_preparation.service import create_draft as create_script,publish_draft as publish_script
from app.services.shot_plan.schema import migrate_shot_plan
from app.services.shot_plan.service import create_draft,patch_draft,compile_draft,draft_view,plan_view,preflight_plan,admit_planned_shot,register_asset
from app.services.worker.execution_admission import AdmissionPolicy
from app.services.worker.durable_execution import DurableExecution
from app.services.worker.execution_accounting import recover_unaccepted_holds
from tests.test_script_preparation_acceptance import fixture as script_fixture,document
from tests.test_execution_admission import inspect
from tests.test_durable_execution import Adapter,Clock,fixture_publisher,read

POLICY=AdmissionPolicy(Capability('software-fixture',frozenset({5}),frozenset({'image'}),8),10,'TEST',10,binding_id='acceptance-fixture-v1')


def canonical(value):
    """Independently calculate compact sorted Unicode digest input."""
    return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)


async def rejected(awaitable,status=409):
    """Check exact refusal semantics, not a broad caught crash."""
    with pytest.raises(HTTPException) as error:
        await awaitable
    assert error.value.status_code==status


async def setup(tmp_path,monkeypatch,duration_ms=5000):
    """Build actual owned versions and a published two-shot chapter in file SQLite."""
    engine,factory=await script_fixture(tmp_path,monkeypatch)
    await migrate_shot_plan(engine)
    stream=io.BytesIO();Image.new('RGB',(16,16),'red').save(stream,format='PNG');png=stream.getvalue()
    (tmp_path/'real-reference.png').write_bytes(png)
    async with factory() as db:
        db.add(FileItem(id='real-reference',type='image',name='real-reference.png',storage_key='real-reference.png'))
        await db.flush()
        db.add(ProductionMedia(id='real-reference',project_id='a',name='real-reference.png',mime='image/png',sha256=hashlib.sha256(png).hexdigest(),size=len(png),relative_path='real-reference.png'))
        db.add(Shot(id='shot-a1b',chapter_id='chapter-a1',index=2,title='Second shot',status='ready',script_excerpt='second original'))
        await db.execute(update(Shot).where(Shot.id.in_(['shot-a1','shot-a2'])).values(status='ready'))
        for identity in ['shared-hero','shared-other']:
            db.add(ProductionAsset(id='asset-'+identity,project_id='a',kind='character',name=identity,entity_id=identity))
        await db.flush()
        await db.commit()
    versions=[]
    async with factory() as db:
        for identity in ['asset-shared-hero','asset-shared-other','asset-a']:
            asset=await db.get(ProductionAsset,identity)
            version=await add_version(db,asset,VersionWrite(expected_revision=0,description='FIXED V1 '+identity,media_ids=['real-reference']))
            versions.append(version.id)
        await db.commit()
    script=document();script['target_duration_ms']=duration_ms*2;script['bindings'][0]['duration_ms']=duration_ms
    second=deepcopy(script['bindings'][0]);second.update(shot_id='shot-a1b',beat_ids=[],dialogue_ids=[])
    script['bindings'].append(second)
    script_id=await create_script(factory,'a',ScriptCreate(chapter_id='chapter-a1',document=script))
    snapshot=await publish_script(factory,'a',script_id,ScriptPublish(expected_revision=1))
    data={'schema_version':1,'script_snapshot_id':snapshot,'model_id':POLICY.capability.model_id,
          'shots':[{'shot_id':s,'version_ids':versions,'reference_media_ids':['real-reference'],'prompt_addition':'Inert software fixture'} for s in ['shot-a1','shot-a1b']]}
    adapter=Adapter(factory);clock=Clock()
    worker=DurableExecution(adapter,factory=factory,clock=clock,publisher=fixture_publisher)
    worker.allowed_bindings=frozenset({binding_fingerprint(POLICY)})
    runtime=SimpleNamespace(policies={POLICY.capability.model_id:POLICY},running=True,allow_submission=True,worker=worker)
    return engine,factory,data,versions,runtime,worker,adapter,clock


async def compile_plan(factory,data,runtime):
    """Create one actual chapter draft and compile revision one."""
    identity=await create_draft(factory,'a',PlanCreate(chapter_id='chapter-a1',document=data))
    plan=await compile_draft(factory,'a',identity,PlanCompile(expected_revision=1),runtime=runtime)
    return identity,plan


@pytest.mark.asyncio
async def test_deterministic_hash_frozen_versions_and_database_immutability(tmp_path,monkeypatch):
    """Reordered set choices normalize; actual v2 cannot rewrite compiled v1."""
    engine,factory,data,versions,runtime,*_=await setup(tmp_path,monkeypatch)
    try:
        identity,plan=await compile_plan(factory,data,runtime)
        async with factory() as db:original=await plan_view(db,'a',plan)
        assert hashlib.sha256(canonical(original['payload']).encode()).hexdigest()==original['content_hash']
        changed=deepcopy(data);changed['shots'].reverse()
        for shot in changed['shots']:shot['version_ids'].reverse()
        await patch_draft(factory,'a',identity,PlanPatch(expected_revision=1,document=changed))
        second=await compile_draft(factory,'a',identity,PlanCompile(expected_revision=2),runtime=runtime)
        async with factory() as db:
            normalized=await plan_view(db,'a',second)
            assert normalized['content_hash']==original['content_hash']
            asset=await db.get(ProductionAsset,'asset-shared-hero')
            v2=await add_version(db,asset,VersionWrite(expected_revision=1,description='UNSELECTED V2',media_ids=[]))
            await db.commit()
        async with factory() as db:
            assert await plan_view(db,'a',plan)==original
            assert (await preflight_plan(db,'a',second,runtime=runtime))['usable']
        assert 'UNSELECTED V2' not in canonical(original['payload'])
        for statement in [f"UPDATE {ShotPlan.__tablename__} SET content_hash='bad' WHERE id=:id",f'DELETE FROM {ShotPlan.__tablename__} WHERE id=:id']:
            async with factory() as db:
                with pytest.raises(IntegrityError):await db.execute(text(statement),{'id':plan})
                await db.rollback()
    finally:await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['missing_shot','wrong_chapter','foreign_shot','foreign_version','missing_character_version','multiple_versions','corrupt_file','archived'])
async def test_scope_and_fixed_input_fail_closed(tmp_path,monkeypatch,failure):
    """Incomplete, mis-scoped or damaged real inputs never become executable plans."""
    engine,factory,data,versions,runtime,*_=await setup(tmp_path,monkeypatch)
    try:
        if failure=='missing_shot':data['shots'].pop()
        if failure=='wrong_chapter':data['shots'][0]['shot_id']='shot-a2'
        if failure=='foreign_shot':data['shots'][0]['shot_id']='shot-b'
        if failure=='foreign_version':
            async with factory() as db:
                asset=await db.get(ProductionAsset,'asset-b')
                foreign=await add_version(db,asset,VersionWrite(expected_revision=0,description='foreign scene version',media_ids=[]))
                await db.commit();data['shots'][0]['version_ids'][-1]=foreign.id
        if failure=='missing_character_version':data['shots'][0]['version_ids'].remove(versions[0])
        if failure=='multiple_versions':
            async with factory() as db:
                asset=await db.get(ProductionAsset,'asset-shared-hero')
                newer=await add_version(db,asset,VersionWrite(expected_revision=1,description='second',media_ids=[]))
                await db.commit();data['shots'][0]['version_ids'].append(newer.id)
        if failure=='corrupt_file':
            path=tmp_path/'real-reference.png';stamp=path.stat()
            path.write_bytes(b'X'*stamp.st_size)
            os.utime(path,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
        if failure=='archived':
            async with factory() as db:
                await db.execute(update(ProductionAsset).where(ProductionAsset.id=='asset-shared-hero').values(archived=True));await db.commit()
        try:
            identity=await create_draft(factory,'a',PlanCreate(chapter_id='chapter-a1',document=data))
        except HTTPException as exc:
            assert exc.status_code in {409,422}
        else:
            with pytest.raises(HTTPException) as error:
                await compile_draft(factory,'a',identity,PlanCompile(expected_revision=1),runtime=runtime)
            assert error.value.status_code in {409,422}
        async with factory() as db:assert await db.scalar(select(func.count()).select_from(ShotPlan))==0
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_fractional_seconds_never_rounded(tmp_path,monkeypatch):
    """A valid manual script with 5001ms shots must not compile as 5s by rounding."""
    engine,factory,data,_,runtime,*_=await setup(tmp_path,monkeypatch,duration_ms=5001)
    try:
        identity=await create_draft(factory,'a',PlanCreate(chapter_id='chapter-a1',document=data))
        await rejected(compile_draft(factory,'a',identity,PlanCompile(expected_revision=1),runtime=runtime))
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_unbound_plan_is_not_silently_upgraded_and_facts_drift_blocks_new_jobs(tmp_path,monkeypatch):
    """Null binding persists and current facts are checked after compilation."""
    engine,factory,data,_,runtime,*_=await setup(tmp_path,monkeypatch)
    try:
        identity,plan=await compile_plan(factory,data,None)
        baseline=await inspect(factory)
        await rejected(admit_planned_shot(factory,'a',plan,'shot-a1','null-binding',runtime=runtime),503)
        await patch_draft(factory,'a',identity,PlanPatch(expected_revision=1,document=data))
        bound=await compile_draft(factory,'a',identity,PlanCompile(expected_revision=2),runtime=runtime)
        async with factory() as db:
            await db.execute(update(Character).where(Character.id=='shared-hero').values(description='changed after compile'));await db.commit()
        await rejected(admit_planned_shot(factory,'a',bound,'shot-a1','stale-facts',runtime=runtime))
        assert await inspect(factory)==baseline
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_same_key_atomic_link_and_cross_plan_conflict(tmp_path,monkeypatch):
    """Concurrent retries commit one job/hold/link and reject a changed plan identity."""
    engine,factory,data,_,runtime,*_=await setup(tmp_path,monkeypatch)
    try:
        identity,plan=await compile_plan(factory,data,runtime)
        jobs=await asyncio.gather(*(admit_planned_shot(factory,'a',plan,'shot-a1','one-intent',runtime=runtime) for _ in range(8)))
        assert len(set(jobs))==1
        counts,held=await inspect(factory)
        assert counts==[1,1,1,0] and held==10
        async with factory() as db:assert await db.scalar(select(func.count()).select_from(PlanJobLink))==1
        await rejected(admit_planned_shot(factory,'a',plan,'shot-a1b','one-intent',runtime=runtime))
        await patch_draft(factory,'a',identity,PlanPatch(expected_revision=1,document=data))
        newer=await compile_draft(factory,'a',identity,PlanCompile(expected_revision=2),runtime=runtime)
        await rejected(admit_planned_shot(factory,'a',newer,'shot-a1','one-intent',runtime=runtime))
        assert await admit_planned_shot(factory,'a',plan,'shot-a1','one-intent',runtime=None)==jobs[0]
        assert await inspect(factory)==(counts,held)
        for operation in ['UPDATE','DELETE']:
            sql=f"UPDATE {PlanJobLink.__tablename__} SET entry_hash='bad' WHERE job_id=:id" if operation=='UPDATE' else f'DELETE FROM {PlanJobLink.__tablename__} WHERE job_id=:id'
            async with factory() as db:
                with pytest.raises(IntegrityError):await db.execute(text(sql),{'id':jobs[0]})
                await db.rollback()
    finally:await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('accepted',[False,True])
async def test_prepared_boundary_facts_change_and_accounting(tmp_path,monkeypatch,accepted):
    """Unsent drift releases only reserved funds; accepted work continues its old job."""
    engine,factory,data,_,runtime,worker,adapter,clock=await setup(tmp_path,monkeypatch)
    try:
        _,plan=await compile_plan(factory,data,runtime)
        job=await admit_planned_shot(factory,'a',plan,'shot-a1','prepared-boundary',runtime=runtime)
        if accepted:
            await worker.step(job)
            assert adapter.sent==1 and (await read(factory,job)).state=='running'
        async with factory() as db:
            await db.execute(update(Character).where(Character.id=='shared-hero').values(description='source drift'));await db.commit()
        if accepted:
            clock.advance();await worker.step(job);await worker.step(job)
            assert (await read(factory,job)).state=='ready' and adapter.sent==1
            async with factory() as db:
                assert (await db.get(ExecutionBudgetEntry,job)).state=='unknown'
                assert await db.scalar(select(func.count()).select_from(ProductionTake))==0
                assert await db.scalar(select(func.count()).select_from(ShotSelection))==0
                assert (await db.get(Shot,'shot-a1')).generated_video_file_id is None
        else:
            await worker.step(job)
            state=await read(factory,job)
            assert state.state=='cancelled' and 'plan_stale' in state.reason and adapter.sent==0
            await recover_unaccepted_holds(factory=factory)
            async with factory() as db:
                assert await db.scalar(select(func.count()).select_from(SubmissionAttempt))==0
                assert (await db.get(ExecutionBudgetEntry,job)).state=='released'
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_existing_entity_registration_is_idempotent_under_concurrency(tmp_path,monkeypatch):
    """Legacy identities become fixed versions once, without duplicating the character."""
    engine,factory,data,_,runtime,*_=await setup(tmp_path,monkeypatch)
    try:
        body=AssetRegistration(kind='character',entity_id='shared-hero')
        before=None
        async with factory() as db:before=await db.scalar(select(func.count()).select_from(AssetVersion))
        results=await asyncio.gather(*(register_asset(factory,'a',body) for _ in range(8)))
        assert len({r['asset_id'] for r in results})==1 and len({r['version_id'] for r in results})==1
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(AssetVersion))==before
            assert await db.scalar(select(func.count()).select_from(Character).where(Character.project_id=='a'))==2
            db.add(Character(id='legacy-no-version',project_id='a',name='Existing unregistered identity',style='真人古装',visual_style='现实'))
            await db.commit()
        newbody=AssetRegistration(kind='character',entity_id='legacy-no-version')
        created=await asyncio.gather(*(register_asset(factory,'a',newbody) for _ in range(8)))
        assert len({r['asset_id'] for r in created})==1 and len({r['version_id'] for r in created})==1
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(AssetVersion))==before+1
            assert await db.scalar(select(func.count()).select_from(Character).where(Character.project_id=='a'))==3
        await rejected(register_asset(factory,'b',body),422)
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_distinct_keys_share_project_budget_and_iteration_ceiling(tmp_path,monkeypatch):
    """Separate shot requests do not acquire separate spending or iteration budgets."""
    engine,factory,data,_,runtime,*_=await setup(tmp_path,monkeypatch)
    try:
        _,plan=await compile_plan(factory,data,runtime)
        results=await asyncio.gather(*(admit_planned_shot(factory,'a',plan,'shot-a1','intent-'+str(i),runtime=runtime) for i in range(12)),return_exceptions=True)
        jobs=[r for r in results if isinstance(r,str)]
        failures=[r for r in results if isinstance(r,HTTPException)]
        assert len(jobs)==10 and len(failures)==2 and all(e.status_code==409 for e in failures)
        counts,held=await inspect(factory)
        assert counts==[10,10,10,0] and held==100
        async with factory() as db:assert await db.scalar(select(func.count()).select_from(PlanJobLink))==10
    finally:await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed',[False,True])
async def test_admission_commit_failure_or_ack_loss_keeps_atomic_link(tmp_path,monkeypatch,committed):
    """A commit error rolls everything back; a lost success ack replays the full job."""
    engine,factory,data,_,runtime,*_=await setup(tmp_path,monkeypatch)
    try:
        _,plan=await compile_plan(factory,data,runtime)
        original=AsyncSession.commit
        observed=[]
        async def commit_failure(db):
            """Inject only at the actual accounting transaction's commit boundary."""
            await db.flush()
            observed.append(await db.scalar(select(func.count()).select_from(PlanJobLink)))
            if committed:await original(db)
            raise RuntimeError('test-only commit acknowledgment loss' if committed else 'test-only before commit failure')
        with monkeypatch.context() as scoped:
            scoped.setattr(AsyncSession,'commit',commit_failure)
            with pytest.raises(RuntimeError):
                await admit_planned_shot(factory,'a',plan,'shot-a1','commit-boundary',runtime=runtime)
        assert observed==[1]
        counts,held=await inspect(factory)
        assert counts==([1,1,1,0] if committed else [0,0,0,0]) and held==(10 if committed else 0)
        if committed:
            recovered=await admit_planned_shot(factory,'a',plan,'shot-a1','commit-boundary',runtime=None)
            async with factory() as db:assert await db.get(PlanJobLink,recovered) is not None
            assert await inspect(factory)==(counts,held)
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_http_original_job_replays_with_runtime_off_and_stale_facts(tmp_path,monkeypatch):
    """HTTP dependencies cannot reject recovery merely because current eligibility changed."""
    from app.main import app
    from app.api.shot_plan import get_shot_plan_factory
    engine,factory,data,_,runtime,*_=await setup(tmp_path,monkeypatch)
    old_runtime=getattr(app.state,'execution_runtime',None)
    old_dependency=app.dependency_overrides.get(get_shot_plan_factory)
    app.dependency_overrides[get_shot_plan_factory]=lambda:factory
    try:
        _,plan=await compile_plan(factory,data,runtime)
        original=await admit_planned_shot(factory,'a',plan,'shot-a1','http-recovery',runtime=runtime)
        baseline=await inspect(factory)
        async with factory() as db:
            await db.execute(update(Character).where(Character.id=='shared-hero').values(description='actual drift after admission'));await db.commit()
        app.state.execution_runtime=None
        path='/api/v1/shot-plans/projects/a/plans/'+plan+'/shots/shot-a1/submissions'
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',trust_env=False) as client:
            replay=await client.post(path,json={'request_key':'http-recovery'})
            assert replay.status_code==201,replay.text
            assert replay.json()['data']['job_id']==original
            new=await client.post(path,json={'request_key':'new-when-off'})
            assert new.status_code==503,new.text
            injected=await client.post(path,json={'request_key':'no-bypass','trusted':True,'quote_minor':0})
            assert injected.status_code==422
        assert await inspect(factory)==baseline
    finally:
        app.state.execution_runtime=old_runtime
        if old_dependency is None:app.dependency_overrides.pop(get_shot_plan_factory,None)
        else:app.dependency_overrides[get_shot_plan_factory]=old_dependency
        await engine.dispose()
