"""Exercise real Agent configuration persistence, history and invalid input without providers."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
import httpx
from pydantic import ValidationError
import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1.routes.agents import router
from app.dependencies import get_db
from app.models.agent_config import AgentConfig, AgentConfigRevision


@asynccontextmanager
async def isolated_client(tmp_path):
    """Use an isolated on-disk SQLite DB and the exact production route/dependency boundary."""
    engine = create_async_engine('sqlite+aiosqlite:///' + (tmp_path / 'agents.db').as_posix())

    @event.listens_for(engine.sync_engine, 'connect')
    def configure(connection, _):
        """Enable the same foreign-key behavior as the supported local runtime."""
        connection.execute('PRAGMA foreign_keys=ON')

    async with engine.begin() as connection:
        await connection.run_sync(lambda conn: AgentConfig.__table__.create(conn))
        await connection.run_sync(lambda conn: AgentConfigRevision.__table__.create(conn))
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def database():
        """Commit successful requests and roll back failures exactly like the real app."""
        async with sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app = FastAPI()
    app.include_router(router, prefix='/api/v1')
    app.dependency_overrides[get_db] = database

    @app.exception_handler(ValidationError)
    async def invalid_config(_request, _error):
        """Mirror the production domain-validation status without exposing inputs."""
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=422, content={'message': 'Invalid configuration'})

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        yield client
    await engine.dispose()


@pytest.mark.asyncio
async def test_complete_draft_survives_requests_and_history_restore(tmp_path):
    """Every exposed editor field must survive save/reload and explicit history restore."""
    async with isolated_client(tmp_path) as client:
        assert (await client.get('/api/v1/agents')).json()['data']['items'] == []
        document = {'name': 'Actor extraction', 'type': 'character', 'prompt': 'Extract visible evidence only', 'temperature': .3, 'top_p': .8, 'parameters': {'plot_threshold': .4, 'character_limit': 7}}
        response = await client.post('/api/v1/agents', json=document)
        assert response.status_code == 201, response.text
        saved = response.json()['data']
        agent_id = saved['id']
        loaded = (await client.get(f'/api/v1/agents/{agent_id}')).json()['data']
        for field, value in document.items():
            assert loaded[field] == value
        graph = loaded['workflow']
        graph[1]['prompt'] = 'Saved node prompt'
        graph[1]['temperature'] = .1
        updated = await client.patch(f'/api/v1/agents/{agent_id}', json={'expected_version': 1, 'prompt': 'Changed', 'workflow': graph})
        assert updated.status_code == 200, updated.text
        assert updated.json()['data']['workflow'][1]['prompt'] == 'Saved node prompt'
        history = (await client.get(f'/api/v1/agents/{agent_id}/revisions')).json()['data']
        assert [row['version'] for row in history] == [2, 1]
        restored = await client.patch(f'/api/v1/agents/{agent_id}', json={**history[1]['document'], 'expected_version': 2})
        assert restored.status_code == 200
        assert restored.json()['data']['version'] == 3
        assert restored.json()['data']['prompt'] == document['prompt']
        stale = await client.patch(f'/api/v1/agents/{agent_id}', json={'expected_version': 1, 'name': 'Overwrite'})
        assert stale.status_code == 409
        assert (await client.get(f'/api/v1/agents/{agent_id}')).json()['data']['name'] == document['name']


@pytest.mark.asyncio
async def test_default_copy_export_import_favorite_and_delete(tmp_path):
    """Default, favorite, copy and portable import are real independent records."""
    async with isolated_client(tmp_path) as client:
        first = (await client.post('/api/v1/agents', json={'name': 'A', 'type': 'plot', 'is_default': True})).json()['data']
        second_response = await client.post('/api/v1/agents', json={'name': 'B', 'type': 'plot', 'is_default': True})
        assert second_response.status_code == 201, second_response.text
        second = second_response.json()['data']
        assert not (await client.get(f"/api/v1/agents/{first['id']}")).json()['data']['is_default']
        copied = (await client.post(f"/api/v1/agents/{second['id']}/copy")).json()['data']
        assert copied['id'] != second['id'] and not copied['is_default']
        favorited = await client.patch(f"/api/v1/agents/{copied['id']}", json={'is_favorite': True})
        assert favorited.json()['data']['is_favorite']
        exported = (await client.get(f"/api/v1/agents/{second['id']}/export")).json()['data']
        assert 'id' not in exported['config'] and 'created_by' not in exported['config']
        imported = (await client.post('/api/v1/agents/import', json=exported)).json()['data']
        assert imported['id'] not in {first['id'], second['id'], copied['id']} and not imported['is_default']
        assert (await client.delete(f"/api/v1/agents/{copied['id']}")).status_code == 200
        assert (await client.get(f"/api/v1/agents/{copied['id']}")).status_code == 404
        assert (await client.get(f"/api/v1/agents/{second['id']}")).status_code == 200


@pytest.mark.asyncio
async def test_invalid_config_never_creates_or_corrupts_a_record(tmp_path):
    """Invalid imports, graphs, temperatures and null updates fail before persistence."""
    async with isolated_client(tmp_path) as client:
        for invalid in [{'name': ' ', 'type': 'plot'}, {'name': 'A', 'type': 'bad'}, {'name': 'A', 'type': 'plot', 'temperature': 3}, {'name': 'A', 'type': 'plot', 'api_key': 'not-accepted'}]:
            assert (await client.post('/api/v1/agents', json=invalid)).status_code == 422
        assert (await client.get('/api/v1/agents')).json()['data']['pagination']['total'] == 0
        agent = (await client.post('/api/v1/agents', json={'name': 'A', 'type': 'plot'})).json()['data']
        assert (await client.patch(f"/api/v1/agents/{agent['id']}", json={'name': None})).status_code == 422
        broken = [{'id': 'same', 'label': 'Start', 'type': 'start'}, {'id': 'same', 'label': 'End', 'type': 'end'}]
        assert (await client.patch(f"/api/v1/agents/{agent['id']}", json={'workflow': broken})).status_code == 422
        assert (await client.get(f"/api/v1/agents/{agent['id']}")).json()['data']['version'] == 1
        assert (await client.post('/api/v1/agents/import', json={'schema_version': 2, 'config': {'name': 'A', 'type': 'plot'}})).status_code == 422
