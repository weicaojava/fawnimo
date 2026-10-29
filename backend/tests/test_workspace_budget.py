"""File-backed multi-project accounting tests; all costs are synthetic and no transport runs."""
import asyncio
from types import SimpleNamespace
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from app.api import workspace_budget as api
from app.api.video_runtime_setup import get_video_setup_factory
from app.controlplane.gateway import create_gateway
from app.core.contracts.execution_capabilities import Capability
from app.core.contracts.workspace_budget import WorkspaceBudgetWrite
from app.core.db import Base
from app.models.execution import ExecutionJob
from app.models.execution_budget import ExecutionBudget, ExecutionBudgetEntry
from app.models.studio import Project, ProjectStyle
from app.models.task import GenerationTask
from app.models.workspace_budget import WorkspaceBudgetChange
from app.services.worker.execution_admission import AdmissionPolicy, admit
from app.services.worker.execution_budget import configure, mark_unknown, reserve, release
from app.services.worker.execution_accounting import settle_actual
from app.services.worker.execution_queries import control_job
from app.services.worker.workspace_budget import read_workspace_budget, save_workspace_budget, list_workspace_entries
from app.services.worker.workspace_budget_schema import migrate_workspace_budget

ACTOR = 'usr_' + 'a'*32
POLICY = AdmissionPolicy(Capability('fixture-video', frozenset({5}), frozenset(), 0), 10, 'CNY', 100)
SNAPSHOT = {'model_id': 'fixture-video', 'duration': 5, 'prompt': 'isolated synthetic ledger fixture', 'references': []}


async def fixture(path, *, managed=True):
    """Use separate real SQLite connections with foreign keys and bounded writer contention."""
    engine = create_async_engine('sqlite+aiosqlite:///' + path.as_posix(), poolclass=NullPool)
    @event.listens_for(engine.sync_engine, 'connect')
    def pragmas(connection, _):
        """Match production referential constraints rather than an in-memory mock ledger."""
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA busy_timeout=10000')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    if managed:
        await migrate_workspace_budget(engine)
    async with factory() as db:
        db.add_all([Project(id=key, name=key, style=ProjectStyle.real_people_city) for key in ('one', 'two')])
        await db.commit()
    for project in ('one', 'two'):
        async with factory() as db:
            await configure(db, project, 'CNY', 1000)
    return engine, factory


async def summary(factory):
    """Read committed balances independently of the transaction that changed them."""
    async with factory() as db:
        return await read_workspace_budget(db)


async def ceiling(factory, value):
    """Choose the latest revision as an explicitly authorized test owner would."""
    current = await summary(factory)
    return await save_workspace_budget(factory, WorkspaceBudgetWrite(expected_revision=current.revision, limit_minor=value), ACTOR)


@pytest.mark.asyncio
async def test_cross_project_concurrent_admission_is_atomic_and_replay_does_not_charge(tmp_path):
    """Twenty project submissions can create only ten task/job/holds under one shared ceiling."""
    engine, factory = await fixture(tmp_path/'concurrent.db')
    try:
        await ceiling(factory, 100)
        outcomes = await asyncio.gather(*(admit(factory, 'one' if i % 2 else 'two', f'key-{i}', SNAPSHOT, POLICY) for i in range(20)), return_exceptions=True)
        accepted = [(i, value) for i, value in enumerate(outcomes) if isinstance(value, str)]
        assert len(accepted) == 10
        assert all(isinstance(value, HTTPException) and value.status_code == 409 for value in outcomes if not isinstance(value, str))
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(ExecutionJob)) == 10
            assert await db.scalar(select(func.count()).select_from(GenerationTask)) == 10
            assert await db.scalar(select(func.count()).select_from(ExecutionBudgetEntry)) == 10
        assert (await summary(factory)).reserved_minor == 100
        index, job = accepted[0]
        assert await admit(factory, 'one' if index % 2 else 'two', f'key-{index}', SNAPSHOT, POLICY) == job
        assert (await summary(factory)).reserved_minor == 100
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_default_zero_fails_closed_and_project_limit_still_applies(tmp_path):
    """A generous project ceiling cannot bypass a zero or smaller workspace ceiling."""
    engine, factory = await fixture(tmp_path/'zero.db')
    try:
        with pytest.raises(HTTPException):
            await admit(factory, 'one', 'default-zero', SNAPSHOT, POLICY)
        await ceiling(factory, 2000)
        expensive = AdmissionPolicy(POLICY.capability, 1001, 'CNY', 100)
        with pytest.raises(HTTPException):
            await admit(factory, 'one', 'project-ceiling', SNAPSHOT, expensive)
        assert (await summary(factory)).reserved_minor == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unknown_holds_survive_edits_and_only_trusted_settlement_frees_difference(tmp_path):
    """Unknown outcomes retain funds; stale edits, retries and lower ceilings cannot erase them."""
    engine, factory = await fixture(tmp_path/'unknown.db')
    try:
        before = await ceiling(factory, 100)
        job = await admit(factory, 'one', 'unknown', SNAPSHOT, POLICY)
        async with factory() as db:
            await mark_unknown(db, 'one', job)
        async with factory() as db:
            with pytest.raises(ValueError):
                await release(db, 'one', job)
        with pytest.raises(HTTPException) as stale:
            await save_workspace_budget(factory, WorkspaceBudgetWrite(expected_revision=before.revision, limit_minor=200), ACTOR)
        assert stale.value.status_code == 409
        with pytest.raises(HTTPException):
            await ceiling(factory, 9)
        held = await ceiling(factory, 10)
        assert (held.reserved_minor, held.uncertain_jobs, held.available_minor) == (10, 1, 0)
        await settle_actual('one', job, 7, factory=factory)
        await settle_actual('one', job, 7, factory=factory)
        current = await summary(factory)
        assert (current.reserved_minor, current.spent_minor, current.available_minor) == (0, 7, 3)
        with pytest.raises(ValueError):
            await settle_actual('one', job, 6, factory=factory)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unsent_cancel_and_rollback_update_shared_balance_without_reset(tmp_path):
    """The existing direct-SQL cancellation path releases the space hold exactly once."""
    engine, factory = await fixture(tmp_path/'cancel.db')
    try:
        await ceiling(factory, 10)
        job = await admit(factory, 'one', 'cancel', SNAPSHOT, POLICY)
        async with factory() as db:
            await control_job(db, 'one', job, 'cancel')
            await db.rollback()
        assert (await summary(factory)).reserved_minor == 10
        async with factory() as db:
            await control_job(db, 'one', job, 'cancel')
            await db.commit()
        async with factory() as db:
            await control_job(db, 'one', job, 'cancel')
            await db.commit()
        assert (await summary(factory)).available_minor == 10
        await admit(factory, 'two', 'freed', SNAPSHOT, POLICY)
        assert (await summary(factory)).available_minor == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_ceiling_edits_and_immutable_receipts(tmp_path):
    """One CAS wins and its actual owner receipt cannot be changed or removed."""
    engine, factory = await fixture(tmp_path/'receipts.db')
    try:
        current = await summary(factory)
        values = await asyncio.gather(*(save_workspace_budget(factory, WorkspaceBudgetWrite(expected_revision=current.revision, limit_minor=limit), ACTOR) for limit in (30, 50)), return_exceptions=True)
        assert sum(isinstance(value, HTTPException) and value.status_code == 409 for value in values) == 1
        async with factory() as db:
            changes = (await db.scalars(select(WorkspaceBudgetChange))).all()
            assert len(changes) == 1 and changes[0].actor_id == ACTOR
        for sql in ('DELETE FROM workspace_budget_changes', "UPDATE workspace_budget_changes SET actor_id='other'", 'DELETE FROM workspace_budget'):
            async with factory() as db:
                with pytest.raises(IntegrityError):
                    await db.execute(text(sql))
                await db.rollback()
        await migrate_workspace_budget(engine)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_raw_sql_guards_and_changed_schema_fail_closed(tmp_path):
    """Raw SQL cannot add spending past the ceiling or silently replace a required guard."""
    engine, factory = await fixture(tmp_path/'guards.db')
    try:
        for sql in ("UPDATE execution_budgets SET reserved_minor=1 WHERE project_id='one'", "INSERT INTO execution_budgets VALUES('extra','CNY',100,1,0,0)"):
            async with factory() as db:
                with pytest.raises(IntegrityError):
                    await db.execute(text(sql))
                await db.rollback()
        async with engine.begin() as connection:
            await connection.execute(text('DROP TRIGGER workspace_budget_project_update'))
            await connection.execute(text('CREATE TRIGGER workspace_budget_project_update BEFORE UPDATE ON execution_budgets BEGIN SELECT 1; END'))
        with pytest.raises(RuntimeError):
            await migrate_workspace_budget(engine)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_two_databases_have_independent_limits_and_ledger_pages(tmp_path):
    """One fixed worker's job IDs, ceiling and details never select or affect another database."""
    first, a = await fixture(tmp_path/'a.db')
    second, b = await fixture(tmp_path/'b.db')
    try:
        await ceiling(a, 50)
        for key in ('a', 'b', 'c'):
            await admit(a, 'one', key, SNAPSHOT, POLICY)
        assert (await summary(b)).reserved_minor == 0 and (await summary(b)).limit_minor == 0
        async with a() as db:
            page = await list_workspace_entries(db, after=None, limit=2)
            last = await list_workspace_entries(db, after=page.next_after, limit=2)
            assert len(page.items) == 2 and len(last.items) == 1 and last.next_after is None
            assert len({entry.job_id for entry in page.items+last.items}) == 3
            assert all(set(item.model_dump()) == {'job_id','project_id','project_name','state','quote_minor','actual_minor'} for item in page.items)
        async with b() as db:
            assert not (await list_workspace_entries(db, after=None, limit=20)).items
    finally:
        await first.dispose()
        await second.dispose()


def test_budget_http_role_strict_inputs_and_legacy_mode(tmp_path):
    """HTTP owner checks and strict amounts cannot be replaced by a caller-supplied identity body."""
    engine, factory = asyncio.run(fixture(tmp_path/'api.db'))
    app = FastAPI()
    app.include_router(api.router, prefix='/api/v1')
    app.dependency_overrides[get_video_setup_factory] = lambda: factory
    try:
        with TestClient(app) as client:
            endpoint = '/api/v1/video-runtime/workspace-budget'
            current = client.get(endpoint).json()['data']
            body = {'expected_revision': current['revision'], 'limit_minor': 100}
            assert client.put(endpoint, json=body, headers={'x-workspace-role':'editor','x-identity-id':ACTOR}).status_code == 403
            headers = {'x-workspace-role':'owner','x-identity-id':ACTOR}
            for invalid in (True, -1, 1.5, '10', 100_000_001):
                assert client.put(endpoint, json={**body, 'limit_minor':invalid}, headers=headers).status_code == 422
            assert client.put(endpoint, json={**body,'actor_id':ACTOR}, headers=headers).status_code == 422
            assert client.put(endpoint, json=body, headers=headers).status_code == 200
            assert client.get(endpoint+'/entries?limit=101').status_code == 422
    finally:
        asyncio.run(engine.dispose())
    engine, factory = asyncio.run(fixture(tmp_path/'legacy.db', managed=False))
    try:
        assert not asyncio.run(summary(factory)).managed
        with pytest.raises(HTTPException) as unavailable:
            asyncio.run(ceiling(factory, 100))
        assert unavailable.value.status_code == 503
        async def original_project_budget():
            """Verify unchanged standalone project accounting still works without managed guards."""
            async with factory() as db:
                await reserve(db, 'one', 'legacy-hold', 10, 'CNY')
        asyncio.run(original_project_budget())
    finally:
        asyncio.run(engine.dispose())


def test_gateway_ignores_forged_roles_and_rejects_stale_context(tmp_path):
    """Editor cannot forge owner headers and old-tab identifiers cannot edit the active space."""
    class NoNetworkPool:
        """Fail this test if a denied budget edit reaches worker selection."""
        async def get(self, workspace):
            """Denied writes must stop before network or worker selection."""
            raise AssertionError('Unauthorized request reached worker')
        async def close(self):
            """This fixture owns no processes."""
    app = create_gateway(tmp_path/'gateway', workers=NoNetworkPool(), secure_cookies=False)
    store = app.state.identity_store
    user = store.provision('owner', 'synthetic-fixture-password-2026')
    with TestClient(app) as client:
        identity = client.post('/api/v1/identity/login', json={'account':'owner','password':'synthetic-fixture-password-2026'}).json()['data']
        headers = {'x-workspace-id':identity['workspace_id'], 'x-identity-id':identity['user']['id'], 'x-workspace-role':'owner'}
        with store.connect() as db:
            db.execute("UPDATE memberships SET role='editor' WHERE user_id=?", (user['user_id'],))
        endpoint = '/api/v1/video-runtime/workspace-budget'
        body = {'expected_revision':0, 'limit_minor':100}
        assert client.put(endpoint, json=body, headers=headers).status_code == 403
        assert client.put(endpoint, json=body, headers={**headers,'x-workspace-id':'ws_'+'b'*32}).status_code == 409
        assert client.put(endpoint, json=body, headers={**headers,'origin':'https://other.invalid'}).status_code == 403
