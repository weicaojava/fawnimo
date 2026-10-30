"""Real SQLite plan contracts with explicit fixture transports, not model generation."""
from dataclasses import replace
from types import SimpleNamespace
from pathlib import Path
import asyncio
import os
import pytest
from fastapi import HTTPException
from sqlalchemy import select,update,func
from app.config import settings
from app.core.contracts.shot_plan import AssetRegistration,PlanCreate,PlanCompile,PlanPatch
from app.core.contracts.script_preparation import ScriptCreate,ScriptPublish
from app.core.contracts.execution_binding import binding_fingerprint
from app.models.execution import ExecutionJob,SubmissionAttempt
from app.models.execution_budget import ExecutionBudgetEntry
from app.models.studio import Character,Shot
from app.models.task_links import GenerationTaskLink
from app.models.production import ProductionAsset
from app.schemas.studio.production import VersionWrite
from app.services.studio.production import add_version
from app.services.script_preparation.service import create_draft as create_script,publish_draft as publish_script
from app.services.shot_plan.schema import migrate_shot_plan
from app.services.shot_plan.service import create_draft,compile_draft,patch_draft,plan_view,preflight_plan,register_asset,admit_planned_shot,list_jobs
from app.services.shot_plan.service import draft_view
from app.services.worker.durable_execution import DurableExecution
from app.services.worker.execution_artifacts import DownloadedArtifact
from tests.test_script_preparation import fixture as story_fixture,document
from tests.test_execution_admission import POLICY
from tests.test_execution_artifacts import png


class FixtureTransport:
    """A zero-account fixture records accepted work and returns real PNG artifact bytes."""
    def __init__(self):self.sent=0;self.remote={}
    async def submit(self,snapshot,request_id):
        """Record one software acceptance under the durable request identity."""
        self.sent+=1;self.remote[request_id]='fixture';return 'fixture'
    async def reconcile(self,request_id):
        """Resolve a known fixture acceptance without sending again."""
        return self.remote.get(request_id)
    async def query(self,remote_id):
        """Return an explicitly synthetic result, never real provider qualification."""
        return {'status':'succeeded','source_kind':'software_fixture'}
    async def download(self,result,media_id,project_id):
        """Return real decodable pixels to the production artifact fence."""
        return DownloadedArtifact('software-fixture.png',png())
    async def cancel(self,remote_id):
        """This fixture never claims remote cancellation confirmation."""
        return False


async def fixture(tmp_path,monkeypatch):
    """Publish an actual manual story, register existing entities and bind a fixture policy."""
    monkeypatch.setattr(settings,'production_media_root',str(tmp_path))
    probe=Path(os.environ.get('TEST_MEDIA_PROBE',str(Path(__file__).resolve().parents[4]/'ffmpeg.exe')))
    monkeypatch.setattr(settings,'production_probe_executable',str(probe))
    engine,factory=await story_fixture(tmp_path);await migrate_shot_plan(engine)
    async with factory() as db:
        await db.execute(update(Shot).where(Shot.id=='s1').values(status='ready'));await db.commit()
    async with factory() as db:
        await add_version(db,await db.get(ProductionAsset,'owned-scene'),VersionWrite(expected_revision=0,description='stable scene',media_ids=[]))
        await db.commit()
    script=await create_script(factory,'a',ScriptCreate(chapter_id='c1',document=document()))
    publication=await publish_script(factory,'a',script,ScriptPublish(expected_revision=1))
    char=await register_asset(factory,'a',AssetRegistration(kind='character',entity_id='hero'))
    scene=await register_asset(factory,'a',AssetRegistration(kind='scene',entity_id='scene'))
    policy=replace(POLICY,capability=replace(POLICY.capability,durations=frozenset({30})))
    transport=FixtureTransport();worker=DurableExecution(transport,factory=factory,poll_seconds=5)
    worker.allowed_bindings=frozenset({binding_fingerprint(policy)})
    runtime=SimpleNamespace(policies={'trusted-model':policy},worker=worker,allow_submission=True,running=True)
    value={'schema_version':1,'script_snapshot_id':publication,'model_id':'trusted-model','shots':[{'shot_id':'s1','version_ids':[char['version_id'],scene['version_id']],'reference_media_ids':[],'prompt_addition':''}]}
    draft=await create_draft(factory,'a',PlanCreate(chapter_id='c1',document=value))
    return engine,factory,draft,value,runtime,transport


@pytest.mark.asyncio
async def test_compile_determinism_null_binding_and_scope(tmp_path,monkeypatch):
    """Preparation works without accounts, but an unbound historical plan cannot submit."""
    engine,factory,draft,value,runtime,_=await fixture(tmp_path,monkeypatch)
    try:
        first=await compile_draft(factory,'a',draft,PlanCompile(expected_revision=1))
        assert await compile_draft(factory,'a',draft,PlanCompile(expected_revision=1),runtime)==first
        async with factory() as db:
            original=await plan_view(db,'a',first)
            assert original['payload']['binding'] is None
            assert not (await preflight_plan(db,'a',first,runtime))['can_submit']
        with pytest.raises(HTTPException):await admit_planned_shot(factory,'a',first,'s1','null',runtime)
        async with factory() as db:
            check=(await draft_view(db,'a',draft,runtime))['readiness']
            assert not check['can_compile'] and any(i['code']=='save_new_binding_revision' for i in check['issues'])
        await patch_draft(factory,'a',draft,PlanPatch(expected_revision=1,document=value))
        second=await compile_draft(factory,'a',draft,PlanCompile(expected_revision=2))
        async with factory() as db:assert (await plan_view(db,'a',second))['content_hash']==original['content_hash']
        wrong={**value,'shots':[{**value['shots'][0],'shot_id':'s2'}]}
        with pytest.raises(HTTPException):await patch_draft(factory,'a',draft,PlanPatch(expected_revision=2,document=wrong))
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_atomic_planned_admission_and_presend_drift_releases_hold(tmp_path,monkeypatch):
    """Concurrent replay reserves once; drift before prepared cancels without external calls."""
    engine,factory,draft,value,runtime,transport=await fixture(tmp_path,monkeypatch)
    try:
        plan=await compile_draft(factory,'a',draft,PlanCompile(expected_revision=1),runtime)
        jobs=await asyncio.gather(*(admit_planned_shot(factory,'a',plan,'s1','same-key',runtime) for _ in range(3)))
        assert len(set(jobs))==1
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(GenerationTaskLink))==1
            await db.execute(update(Character).where(Character.id=='hero').values(description='drift'));await db.commit()
        await runtime.worker.step(jobs[0])
        async with factory() as db:
            job=await db.get(ExecutionJob,jobs[0]);hold=await db.get(ExecutionBudgetEntry,jobs[0])
            assert job.state=='cancelled' and job.reason=='plan_stale_unsent' and hold.state=='released'
            assert await db.scalar(select(func.count()).select_from(SubmissionAttempt))==0
        assert transport.sent==0
        async with factory() as db:
            displayed=await list_jobs(db,'a',plan)
            assert 'plan_stale_unsent' not in displayed[0]['reason'] and '提交前取消' in displayed[0]['reason']
        assert await admit_planned_shot(factory,'a',plan,'s1','same-key',None)==jobs[0]
        with pytest.raises(HTTPException):await admit_planned_shot(factory,'a',plan,'s1','new-key',runtime)
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_prepared_job_recovers_after_fact_drift_and_produces_owned_candidate(tmp_path,monkeypatch):
    """After acceptance, source drift never causes a new submission or automatic adoption."""
    engine,factory,draft,value,runtime,transport=await fixture(tmp_path,monkeypatch)
    try:
        plan=await compile_draft(factory,'a',draft,PlanCompile(expected_revision=1),runtime)
        job=await admit_planned_shot(factory,'a',plan,'s1','accepted',runtime)
        await runtime.worker.step(job)
        async with factory() as db:
            await db.execute(update(Character).where(Character.id=='hero').values(description='later drift'))
            await db.execute(update(ExecutionJob).where(ExecutionJob.id==job).values(next_due=0));await db.commit()
        await runtime.worker.step(job);await runtime.worker.step(job)
        async with factory() as db:
            row=await db.get(ExecutionJob,job)
            assert row.state=='ready' and transport.sent==1
            provenance=await list_jobs(db,'a',plan)
            assert provenance[0]['media_available'] and provenance[0]['shot_id']=='s1'
            assert (await db.get(Shot,'s1')).generated_video_file_id is None
    finally:await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid',['blank_model','duplicate_character','duplicate_scene'])
async def test_compile_requires_model_intention_and_exact_entity_version_count(tmp_path,monkeypatch,invalid):
    """Distinct assets for the same entity cannot satisfy an exact-one version requirement."""
    engine,factory,draft,value,runtime,_=await fixture(tmp_path,monkeypatch)
    try:
        if invalid=='blank_model':value['model_id']='  '
        else:
            kind='character' if invalid=='duplicate_character' else 'scene'
            entity='hero' if kind=='character' else 'scene'
            async with factory() as db:
                asset=ProductionAsset(id='duplicate-identity-asset',project_id='a',kind=kind,name='duplicate identity',entity_id=entity)
                db.add(asset);await db.flush()
                version=await add_version(db,asset,VersionWrite(expected_revision=0,description='second asset same entity',media_ids=[]))
                await db.commit();value['shots'][0]['version_ids'].append(version.id)
        await patch_draft(factory,'a',draft,PlanPatch(expected_revision=1,document=value))
        with pytest.raises(HTTPException) as error:await compile_draft(factory,'a',draft,PlanCompile(expected_revision=2),runtime)
        assert error.value.status_code==409
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_readiness_aggregates_owned_shot_missing_fields(tmp_path,monkeypatch):
    """The UI gets all basic shot deficiencies rather than a first generic failure."""
    engine,factory,draft,value,runtime,_=await fixture(tmp_path,monkeypatch)
    try:
        value['shots'][0]['version_ids']=[]
        await patch_draft(factory,'a',draft,PlanPatch(expected_revision=1,document=value))
        async with factory() as db:
            await db.execute(update(Shot).where(Shot.id=='s1').values(status='pending'));await db.commit()
        async with factory() as db:
            view=await draft_view(db,'a',draft,runtime)
            codes={item['code'] for item in view['readiness']['issues']}
            assert {'shot_not_ready','character_version_required','scene_version_required'}<=codes
            assert all(item['shot_id']=='s1' for item in view['readiness']['issues'])
            assert not view['readiness']['can_compile']
    finally:await engine.dispose()


async def legacy_plan_parent_database(path):
    """Author a populated pre-plan schema without private recovery files or new child DDL.

    These four parent tables predate the plan journals. Copying them to separate
    metadata deliberately omits the new child's before-create index hooks, so
    every supported additive entrypoint must install the missing composite keys.
    """
    from sqlalchemy import MetaData,insert
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.core.db import Base
    legacy=MetaData()
    for name in ('projects','chapters','generation_tasks','execution_jobs'):
        Base.metadata.tables[name].to_metadata(legacy)
    # The old schema also predates composite indexes registered on parent models.
    for table in legacy.tables.values():
        for index in list(table.indexes):
            if index.name in ('chapter_id_project_scope','execution_id_project_scope','native_execution_job_scope'):
                table.indexes.remove(index)
    engine=create_async_engine('sqlite+aiosqlite:///'+path.as_posix())
    try:
        async with engine.begin() as connection:
            await connection.run_sync(legacy.create_all)
            for suffix in ('a','b'):
                project='legacy-project-'+suffix;task='legacy-task-'+suffix
                await connection.execute(insert(legacy.tables['projects']),{'id':project,'name':'Authored legacy '+suffix,'style':'drama'})
                await connection.execute(insert(legacy.tables['chapters']),{'id':'legacy-chapter-'+suffix,'project_id':project,'index':1,'title':'Preserved authored chapter'})
                await connection.execute(insert(legacy.tables['generation_tasks']),{'id':task,'mode':'async_polling','task_kind':'video_generation','status':'succeeded'})
                await connection.execute(insert(legacy.tables['execution_jobs']),{
                    'id':'legacy-job-'+suffix,'task_id':task,'project_id':project,'request_key':'legacy-request',
                    'request_hash':suffix*64,'snapshot':{'authored_fixture':True,'project_id':project},'state':'succeeded'})
    finally:await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('entrypoint',['metadata','benchmark','init_db'])
async def test_legacy_plan_upgrade_all_supported_entrypoints(tmp_path,monkeypatch,entrypoint):
    """Every public entrypoint upgrades populated pre-plan parents and preserves all rows.

    Historical actual M4A recovery acceptance remains separate private evidence;
    this portable software test contains only newly authored local rows.
    """
    import hashlib
    import sqlite3
    from sqlalchemy import event,text
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.core.db import Base
    import app.core.db as database
    import app.services.studio.production_schema as production_schema
    from app.services.benchmark.schema import migrate_benchmark
    source=tmp_path/'authored-pre-plan.db'
    await legacy_plan_parent_database(source)
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    parents=('projects','chapters','generation_tasks','execution_jobs')
    copy=tmp_path/'upgrade-copy.db'
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as original,sqlite3.connect(copy) as target:
        assert not original.execute("SELECT 1 FROM sqlite_master WHERE name IN ('chapter_id_project_scope','execution_id_project_scope','shot_plan_job_links')").fetchone()
        rows={name:original.execute(f'SELECT * FROM {name} ORDER BY id').fetchall() for name in parents}
        assert all(len(value)==2 for value in rows.values())
        original.backup(target)
    engine=create_async_engine('sqlite+aiosqlite:///'+copy.as_posix())
    @event.listens_for(engine.sync_engine,'connect')
    def foreign_keys(connection,_):
        """Validate actual composite keys rather than bypassing the legacy migration path."""
        connection.execute('PRAGMA foreign_keys=ON')
    try:
        if entrypoint=='metadata':
            async with engine.begin() as connection:await connection.run_sync(Base.metadata.create_all)
        elif entrypoint=='benchmark':await migrate_benchmark(engine)
        else:
            monkeypatch.setattr(database,'engine',engine);monkeypatch.setattr(production_schema,'engine',engine)
            await database.init_db()
        async with engine.connect() as connection:
            assert not (await connection.execute(text('PRAGMA foreign_key_check'))).first()
            assert (await connection.execute(text("SELECT 1 FROM sqlite_master WHERE name='execution_id_project_scope'"))).first()
            assert (await connection.execute(text("SELECT 1 FROM sqlite_master WHERE name='chapter_id_project_scope'"))).first()
            assert (await connection.execute(text("SELECT 1 FROM sqlite_master WHERE name='shot_plan_job_links'"))).first()
            for name in parents:
                assert [tuple(row) for row in (await connection.execute(text(f'SELECT * FROM {name} ORDER BY id'))).all()]==rows[name]
        assert hashlib.sha256(source.read_bytes()).hexdigest()==before
    finally:await engine.dispose()
