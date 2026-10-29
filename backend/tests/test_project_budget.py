"""Project spending isolation and nondestructive retirement of the previous workspace cap."""
import asyncio
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from app.api import video_runtime_setup as api
from app.controlplane.gateway import create_gateway
from app.core.contracts.execution_capabilities import Capability
from app.core.contracts.video_runtime_setup import ProjectBudgetWrite
from app.core.db import Base
from app.models.execution import ExecutionJob
from app.models.execution_budget import ExecutionBudgetEntry
from app.models.project_budget import ProjectBudgetChange
from app.models.studio import Project, ProjectStyle
from app.models.task import GenerationTask
from app.services.worker.execution_admission import AdmissionPolicy, admit
from app.services.worker.execution_budget import mark_unknown, release
from app.services.worker.execution_accounting import settle_actual
from app.services.worker.execution_queries import control_job
from app.services.worker.project_budget import list_project_entries
from app.services.worker.project_budget_schema import migrate_project_budget
from app.services.worker.workspace_video_runtime import read_project_budget, save_project_budget

ACTOR = 'usr_'+'a'*32
POLICY = AdmissionPolicy(Capability('fixture', frozenset({5}), frozenset(), 0), 10, 'CNY', 100)
SNAPSHOT = {'model_id':'fixture','duration':5,'prompt':'synthetic local fixture','references':[]}


async def fixture(path):
    """Use separate real SQLite connections and actual project records without model transport."""
    engine = create_async_engine('sqlite+aiosqlite:///' + path.as_posix(), poolclass=NullPool)
    @event.listens_for(engine.sync_engine, 'connect')
    def pragmas(connection, _):
        """Enable actual foreign keys and bounded lock contention."""
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA busy_timeout=10000')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await migrate_project_budget(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add_all([Project(id=key, name=key, style=ProjectStyle.real_people_city) for key in ('one','two')])
        await db.commit()
    return engine, factory


async def summary(factory, project='one'):
    """Read committed state independently from the transaction that changed it."""
    async with factory() as db:
        return await read_project_budget(db, project)


async def ceiling(factory, project, value):
    """Save an explicit ceiling using the project's current revision and trusted fixture owner."""
    current = await summary(factory, project)
    return await save_project_budget(factory, project, ProjectBudgetWrite(expected_revision=current.revision, limit_minor=value), actor_id=ACTOR)


@pytest.mark.asyncio
async def test_each_project_can_use_its_full_ceiling_under_concurrency(tmp_path):
    """Forty submissions fill both independent 100-unit budgets, with no workspace total cap."""
    engine, factory = await fixture(tmp_path/'concurrent.db')
    try:
        for project in ('one','two'):
            await ceiling(factory, project, 100)
        outcomes = await asyncio.gather(*(admit(factory, 'one' if i%2 else 'two', str(i), SNAPSHOT, POLICY) for i in range(40)), return_exceptions=True)
        assert sum(isinstance(value,str) for value in outcomes) == 20
        assert sum(isinstance(value,HTTPException) and value.status_code==409 for value in outcomes) == 20
        assert (await summary(factory,'one')).reserved_minor == (await summary(factory,'two')).reserved_minor == 100
        async with factory() as db:
            for model in (ExecutionJob, GenerationTask, ExecutionBudgetEntry):
                assert await db.scalar(select(func.count()).select_from(model)) == 20
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_empty_project_default_and_replays_do_not_consume_another_project(tmp_path):
    """New projects have zero allowance; same-key retries debit once and do not change a sibling."""
    engine, factory = await fixture(tmp_path/'replay.db')
    try:
        assert not (await summary(factory)).exists
        with pytest.raises(HTTPException):
            await admit(factory,'one','zero',SNAPSHOT,POLICY)
        await ceiling(factory,'one',10)
        results = await asyncio.gather(*(admit(factory,'one','retry',SNAPSHOT,POLICY) for _ in range(8)))
        assert len(set(results)) == 1
        assert (await summary(factory,'one')).reserved_minor == 10
        assert (await summary(factory,'two')).reserved_minor == 0
        assert not (await summary(factory,'two')).exists
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unknown_retained_and_trusted_settlement_only_affects_owned_project(tmp_path):
    """A ceiling edit or cancellation intent cannot erase unknown fees or affect another project."""
    engine, factory = await fixture(tmp_path/'unknown.db')
    try:
        before = await ceiling(factory,'one',100)
        await ceiling(factory,'two',100)
        job = await admit(factory,'one','unknown',SNAPSHOT,POLICY)
        async with factory() as db:
            await mark_unknown(db,'one',job)
        async with factory() as db:
            with pytest.raises(ValueError):
                await release(db,'one',job)
        with pytest.raises(HTTPException):
            await save_project_budget(factory,'one',ProjectBudgetWrite(expected_revision=before.revision,limit_minor=200),actor_id=ACTOR)
        with pytest.raises(HTTPException):
            await ceiling(factory,'one',9)
        assert (await summary(factory,'one')).uncertain_jobs == 1
        await settle_actual('one',job,7,factory=factory)
        await settle_actual('one',job,7,factory=factory)
        current = await summary(factory,'one')
        assert (current.reserved_minor,current.spent_minor,current.available_minor) == (0,7,93)
        assert (await summary(factory,'two')).available_minor == 100
        with pytest.raises(ValueError):
            await settle_actual('two',job,7,factory=factory)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unsent_cancel_rollback_and_project_change_receipts(tmp_path):
    """Rolled-back cancellation keeps holds; committed cancellation and its retry release once."""
    engine, factory = await fixture(tmp_path/'cancel.db')
    try:
        await ceiling(factory,'one',10)
        job = await admit(factory,'one','cancel',SNAPSHOT,POLICY)
        async with factory() as db:
            await control_job(db,'one',job,'cancel')
            await db.rollback()
        assert (await summary(factory)).reserved_minor == 10
        for _ in range(2):
            async with factory() as db:
                await control_job(db,'one',job,'cancel')
                await db.commit()
        assert (await summary(factory)).available_minor == 10
        async with factory() as db:
            change = (await db.scalars(select(ProjectBudgetChange))).one()
            assert change.project_id == 'one' and change.actor_id == ACTOR and change.previous_limit_minor is None
        for sql in ('DELETE FROM project_budget_changes', "UPDATE project_budget_changes SET actor_id='other'"):
            async with factory() as db:
                with pytest.raises(IntegrityError):
                    await db.execute(text(sql))
                await db.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ledger_filters_before_pagination_and_missing_project_is_404(tmp_path):
    """Neither a sibling's ID nor its cursor can select that sibling's accounting records."""
    engine, factory = await fixture(tmp_path/'pages.db')
    try:
        for project in ('one','two'):
            await ceiling(factory,project,100)
            for index in range(3):
                await admit(factory,project,str(index),SNAPSHOT,POLICY)
        async with factory() as db:
            page = await list_project_entries(db,'one',after=None,limit=2)
            last = await list_project_entries(db,'one',after=page.next_after,limit=2)
            assert len(page.items)==2 and len(last.items)==1 and last.next_after is None
            assert all(row.project_id=='one' for row in page.items+last.items)
            with pytest.raises(HTTPException) as missing:
                await list_project_entries(db,'missing',after=None,limit=20)
            assert missing.value.status_code == 404
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_workspace_controls_are_archived_without_changing_holds(tmp_path):
    """Retiring a zero aggregate cap preserves project holds and historical receipts verbatim."""
    engine, factory = await fixture(tmp_path/'upgrade.db')
    try:
        await ceiling(factory,'one',100)
        await ceiling(factory,'two',100)
        job = await admit(factory,'one','held',SNAPSHOT,POLICY)
        async with factory() as db:
            await mark_unknown(db,'one',job)
        original = (await summary(factory)).model_dump()
        async with engine.begin() as connection:
            await connection.execute(text('CREATE TABLE workspace_budget(id INTEGER PRIMARY KEY,currency TEXT,limit_minor INTEGER,revision INTEGER)'))
            await connection.execute(text("INSERT INTO workspace_budget VALUES(1,'CNY',10,4)"))
            await connection.execute(text('CREATE TABLE workspace_budget_changes(id TEXT PRIMARY KEY,actor_id TEXT,limit_minor INTEGER)'))
            await connection.execute(text("INSERT INTO workspace_budget_changes VALUES('history','original-actor',10)"))
            await connection.execute(text("CREATE TRIGGER workspace_budget_project_update BEFORE UPDATE ON execution_budgets BEGIN SELECT RAISE(ABORT,'Old space limit'); END"))
        await migrate_project_budget(engine)
        await migrate_project_budget(engine)
        assert (await summary(factory)).model_dump() == original
        # The old cap is already exhausted, but project two remains independently usable.
        await admit(factory,'two','independent',SNAPSHOT,POLICY)
        async with engine.connect() as connection:
            assert (await connection.execute(text('SELECT * FROM retired_workspace_budget'))).all() == [(1,'CNY',10,4)]
            assert (await connection.execute(text('SELECT * FROM retired_workspace_budget_changes'))).all() == [('history','original-actor',10)]
            assert not await connection.scalar(text("SELECT name FROM sqlite_master WHERE name='workspace_budget_project_update'"))
        async with factory() as db:
            with pytest.raises(IntegrityError):
                await db.execute(text('DELETE FROM retired_workspace_budget_changes'))
            await db.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_archive_conflict_aborts_before_dropping_old_guards(tmp_path):
    """Ambiguous history refuses migration without partially removing the prior policy."""
    engine, factory = await fixture(tmp_path/'conflict.db')
    try:
        async with engine.begin() as connection:
            await connection.execute(text('CREATE TABLE workspace_budget(id INTEGER)'))
            await connection.execute(text('CREATE TABLE retired_workspace_budget(id INTEGER)'))
            await connection.execute(text('CREATE TRIGGER workspace_budget_project_update BEFORE UPDATE ON execution_budgets BEGIN SELECT 1; END'))
        with pytest.raises(RuntimeError):
            await migrate_project_budget(engine)
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT name FROM sqlite_master WHERE name='workspace_budget_project_update'"))
    finally:
        await engine.dispose()


def test_project_budget_http_and_retired_gateway_endpoint(tmp_path):
    """HTTP exposes only project ledger paths and the retired workspace URL cannot mutate anything."""
    engine, factory = asyncio.run(fixture(tmp_path/'api.db'))
    app = FastAPI()
    app.include_router(api.router,prefix='/api/v1')
    app.dependency_overrides[api.get_video_setup_factory] = lambda: factory
    try:
        with TestClient(app) as client:
            endpoint='/api/v1/video-runtime/projects/one/budget'
            assert client.get(endpoint).json()['data']['project_id']=='one'
            assert client.put(endpoint,json={'expected_revision':0,'limit_minor':100}).status_code==200
            assert client.get(endpoint+'/entries?limit=101').status_code==422
            assert client.get('/api/v1/video-runtime/projects/missing/budget/entries').status_code==404
            assert client.get('/api/v1/video-runtime/workspace-budget').status_code==404
    finally:
        asyncio.run(engine.dispose())
    class NoWorker:
        """Unauthorized or removed routes must stop before worker/network selection."""
        async def get(self, workspace):
            """Fail if a denied request reaches worker startup."""
            raise AssertionError('Denied request reached worker')
        async def close(self):
            """There are no processes in this fixture."""
    gateway=create_gateway(tmp_path/'gateway',workers=NoWorker(),secure_cookies=False)
    store=gateway.state.identity_store
    account=store.provision('owner','synthetic-owner-password-2026')
    with TestClient(gateway) as client:
        identity=client.post('/api/v1/identity/login',json={'account':'owner','password':'synthetic-owner-password-2026'}).json()['data']
        headers={'X-Identity-Id':identity['user']['id'],'X-Workspace-Id':identity['workspace_id'],'X-Workspace-Role':'owner'}
        assert client.put('/api/v1/video-runtime/workspace-budget',json={},headers=headers).status_code==410
        with store.connect() as db:
            db.execute("UPDATE memberships SET role='editor' WHERE user_id=?",(account['user_id'],))
        endpoint='/api/v1/video-runtime/projects/one/budget'
        assert client.put(endpoint,json={'expected_revision':0,'limit_minor':100},headers=headers).status_code==403
        assert client.put(endpoint,json={},headers={**headers,'X-Workspace-Id':'ws_'+'b'*32}).status_code==409
