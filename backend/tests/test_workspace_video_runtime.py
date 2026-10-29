"""Exercise the new opt-in runtime and project budget against isolated SQLite and real API responses."""
import asyncio
import json

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api import video_runtime_setup as api
from app.core.contracts.seedance25 import MODEL_ID
from app.core.contracts.video_runtime_setup import ProjectBudgetWrite
from app.core.db import Base
from app.models.execution_budget import ExecutionBudget, ExecutionBudgetEntry
from app.models.studio import Project, ProjectStyle
from app.services.worker.execution_budget import mark_unknown, reserve
from app.services.worker.workspace_video_runtime import build_workspace_video_runtime, configuration_template, read_project_budget, save_project_budget


class ForbiddenEnvironment(dict):
    """Fail if a disabled, invalid or unquoted setup even attempts to retrieve credentials."""
    def get(self, *args):
        raise AssertionError('Credential lookup is not allowed in this branch')


def write_config(tmp_path, **changes):
    """Write only a new test's synthetic configuration, never a user's runtime configuration."""
    body = {**configuration_template(), **changes}
    path = tmp_path / 'video.json'
    path.write_text(json.dumps(body), encoding='utf-8')
    return str(path)


def test_disabled_configuration_and_missing_quote_do_not_access_credentials(tmp_path):
    """Preparation and unsafe price assumptions must never enable network execution."""
    factory = lambda: None
    runtime, status = build_workspace_video_runtime(factory, None, environment=ForbiddenEnvironment())
    assert runtime is None and status['issue_code'] == 'not_configured'
    path = write_config(tmp_path)
    runtime, status = build_workspace_video_runtime(factory, path, environment=ForbiddenEnvironment())
    assert runtime is None and status['issue_code'] == 'disabled'
    path = write_config(tmp_path, enabled=True)
    runtime, status = build_workspace_video_runtime(factory, path, environment=ForbiddenEnvironment())
    assert runtime is None and status['issue_code'] == 'quote_required'


@pytest.mark.parametrize('extra', [{'api_key': 'never-reflect-this-secret'}, {'base_url': 'http://127.0.0.1:1'}, {'quote_minor': True}, {'enabled': 1}, {'schema_version': 2}])
def test_configuration_rejects_imported_credentials_origins_and_coercions(tmp_path, extra):
    """Executable routing and credentials cannot enter through a portable JSON profile."""
    path = write_config(tmp_path, **extra)
    runtime, status = build_workspace_video_runtime(lambda: None, path, environment=ForbiddenEnvironment())
    assert runtime is None and status['issue_code'] == 'invalid_configuration'
    assert 'never-reflect-this-secret' not in json.dumps(status)
    assert 'base_url' not in status


def test_enabled_setup_builds_bound_budgeted_runtime_without_network_or_secret_projection(tmp_path):
    """Explicit configuration composes the actual production worker but starting is owned by lifespan."""
    path = write_config(tmp_path, enabled=True, quote_minor=500, quote_note='Synthetic test reservation only')
    factory = lambda: None
    runtime, status = build_workspace_video_runtime(factory, path, environment={})
    assert runtime is None and status['issue_code'] == 'credential_required'
    runtime, status = build_workspace_video_runtime(factory, path, environment={'JELLYFISH_VIDEO_API_KEY': 'fake-local-fixture-key'})
    assert runtime is not None and runtime.allow_submission and not runtime.running
    assert runtime.worker.factory is factory
    assert runtime.worker.budget_required and runtime.worker.enforce_binding
    assert runtime.policies[MODEL_ID].quote_minor == 500
    assert runtime.policies[MODEL_ID].capability.max_references == 0
    assert status['credential_present'] and not status['real_model_qualified']
    assert 'fake-local-fixture-key' not in json.dumps(status)


async def fixture(tmp_path):
    """Build a fresh FK-enforced database with two actual projects and no model credentials."""
    engine = create_async_engine('sqlite+aiosqlite:///' + (tmp_path / 'budget.db').as_posix(), poolclass=NullPool)
    @event.listens_for(engine.sync_engine, 'connect')
    def configure(connection, _):
        """Enable referential checks and bounded waiting for actual writer concurrency."""
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA busy_timeout=10000')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add_all([Project(id=identity, name=identity, style=ProjectStyle.real_people_city) for identity in ('one', 'two')])
        await db.commit()
    return engine, factory


@pytest.mark.asyncio
async def test_budget_holds_uncertainty_and_stale_revisions_cannot_be_erased(tmp_path):
    """An actual unknown acceptance retains its reservation through ceiling changes and conflicts."""
    engine, factory = await fixture(tmp_path)
    try:
        created = await save_project_budget(factory, 'one', ProjectBudgetWrite(expected_revision=0, limit_minor=1999))
        assert created.revision == 1 and created.available_minor == 1999
        async with factory() as db:
            await reserve(db, 'one', 'held-job', 500, 'CNY')
        async with factory() as db:
            await mark_unknown(db, 'one', 'held-job')
        async with factory() as db:
            held = await read_project_budget(db, 'one')
            other = await read_project_budget(db, 'two')
        assert held.reserved_minor == 500 and held.uncertain_jobs == 1 and held.unsettled_jobs == 1
        assert not other.exists and other.available_minor == 0
        with pytest.raises(HTTPException) as stale:
            await save_project_budget(factory, 'one', ProjectBudgetWrite(expected_revision=1, limit_minor=1999))
        assert stale.value.status_code == 409
        with pytest.raises(HTTPException) as too_low:
            await save_project_budget(factory, 'one', ProjectBudgetWrite(expected_revision=held.revision, limit_minor=499))
        assert too_low.value.status_code == 409
        changed = await save_project_budget(factory, 'one', ProjectBudgetWrite(expected_revision=held.revision, limit_minor=1500))
        assert changed.reserved_minor == 500 and changed.available_minor == 1000 and changed.uncertain_jobs == 1
        async with factory() as db:
            assert (await db.get(ExecutionBudgetEntry, 'held-job')).state == 'unknown'
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_competing_budget_edits_one_cas_wins(tmp_path):
    """Two independent connections must not silently overwrite a ceiling selected from one revision."""
    engine, factory = await fixture(tmp_path)
    try:
        current = await save_project_budget(factory, 'one', ProjectBudgetWrite(expected_revision=0, limit_minor=1000))
        results = await asyncio.gather(*[
            save_project_budget(factory, 'one', ProjectBudgetWrite(expected_revision=current.revision, limit_minor=limit))
            for limit in (1500, 1999)
        ], return_exceptions=True)
        assert sum(not isinstance(item, BaseException) for item in results) == 1
        assert sum(isinstance(item, HTTPException) and item.status_code == 409 for item in results) == 1
    finally:
        await engine.dispose()


def test_budget_api_persists_and_template_is_inert_without_exposing_keys(tmp_path):
    """Exercise the HTTP contract and independent subsequent reads, not only service return values."""
    engine, factory = asyncio.run(fixture(tmp_path))
    app = FastAPI()
    app.include_router(api.router, prefix='/api/v1')
    app.dependency_overrides[api.get_video_setup_factory] = lambda: factory
    try:
        with TestClient(app) as client:
            template = client.get('/api/v1/video-runtime/configuration-template')
            assert template.status_code == 200 and 'attachment' in template.headers['content-disposition']
            assert template.json()['enabled'] is False and template.json()['quote_minor'] is None
            assert 'api_key' not in template.json()
            missing = client.get('/api/v1/video-runtime/projects/missing/budget')
            assert missing.status_code == 404
            saved = client.put('/api/v1/video-runtime/projects/one/budget', json={'expected_revision': 0, 'limit_minor': 1999})
            assert saved.status_code == 200 and saved.json()['data']['available_minor'] == 1999
            read = client.get('/api/v1/video-runtime/projects/one/budget')
            assert read.json()['data'] == saved.json()['data']
            assert client.put('/api/v1/video-runtime/projects/one/budget', json={'expected_revision': 1, 'limit_minor': 1.5}).status_code == 422
            assert client.get('/api/v1/video-runtime/setup').json()['data']['running'] is False
    finally:
        asyncio.run(engine.dispose())
