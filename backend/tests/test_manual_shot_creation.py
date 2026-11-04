"""Exercise manual creation through real ASGI routes and SQLite transactions, without model services."""
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.db import Base
from app.dependencies import get_db
from app.main import app
from app.models.studio import Chapter, Project, ProjectStyle, Shot, ShotDetail


@asynccontextmanager
async def manual_client(tmp_path):
    """Keep route transactions and failure rollbacks on a new test-owned database."""
    engine = create_async_engine('sqlite+aiosqlite:///' + (tmp_path / 'manual.sqlite3').as_posix())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory.begin() as db:
        db.add(Project(id='project', name='Manual workflow', style=ProjectStyle.real_people_city))
        await db.flush()
        db.add(Chapter(id='chapter', project_id='project', index=1, title='Chapter'))

    async def owned_db():
        """Use the same commit-on-success contract as the application dependency."""
        async with factory.begin() as db:
            yield db

    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = owned_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url='http://test', trust_env=False) as client:
            yield client, factory, engine
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous
        await engine.dispose()


def shot_body(**changes):
    """Submit explicit pending information; creating details never declares generation readiness."""
    return dict(id='manual', chapter_id='chapter', index=1, title='Manual shot', script_excerpt='Original local prose.', create_detail=True, **changes)


@pytest.mark.asyncio
async def test_manual_creation_opens_preparation_without_model_or_second_write(tmp_path):
    """A single creation must make both editor reads available and keep information pending."""
    async with manual_client(tmp_path) as (client, factory, _engine):
        created = await client.post('/api/v1/studio/shots', json=shot_body())
        assert created.status_code == 201, created.text
        assert created.json()['data']['status'] == 'pending'
        detail = await client.get('/api/v1/studio/shot-details/manual')
        preparation = await client.get('/api/v1/studio/shots/manual/preparation-state')
        assert detail.status_code == preparation.status_code == 200, preparation.text
        assert preparation.json()['data']['shot']['status'] == 'pending'
        assert not preparation.json()['data']['shot']['skip_extraction']
        async with factory() as db:
            assert (await db.get(Shot, 'manual')).script_excerpt == 'Original local prose.'
            assert (await db.get(ShotDetail, 'manual')).id == 'manual'


@pytest.mark.asyncio
async def test_legacy_two_step_creation_remains_available(tmp_path):
    """Omitted opt-in preserves the old explicit detail-creation API and duplicate protection."""
    async with manual_client(tmp_path) as (client, factory, _engine):
        body = shot_body(); body.pop('create_detail')
        assert (await client.post('/api/v1/studio/shots', json=body)).status_code == 201
        async with factory() as db:
            assert await db.get(ShotDetail, 'manual') is None
        detail = dict(id='manual', camera_shot='MS', angle='EYE_LEVEL', movement='STATIC')
        assert (await client.post('/api/v1/studio/shot-details', json=detail)).status_code == 201
        assert (await client.post('/api/v1/studio/shot-details', json=detail)).status_code == 400


@pytest.mark.asyncio
async def test_detail_insert_failure_rolls_back_the_new_shot(tmp_path):
    """A real SQLite insert failure cannot leave a shot that the editor cannot open."""
    async with manual_client(tmp_path) as (client, factory, engine):
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TRIGGER fail_detail BEFORE INSERT ON shot_details BEGIN SELECT RAISE(ABORT, 'authored detail failure'); END"))
        response = await client.post('/api/v1/studio/shots', json=shot_body())
        assert response.status_code == 409
        async with factory() as db:
            assert await db.get(Shot, 'manual') is None
            assert await db.get(ShotDetail, 'manual') is None


@pytest.mark.asyncio
async def test_duplicate_creation_cannot_overwrite_existing_preparation(tmp_path):
    """A repeated creation stays a conflict and preserves already edited detail and identity."""
    async with manual_client(tmp_path) as (client, factory, _engine):
        assert (await client.post('/api/v1/studio/shots', json=shot_body())).status_code == 201
        async with factory.begin() as db:
            detail = await db.get(ShotDetail, 'manual')
            detail.first_frame_prompt = 'Keep this original prompt.'
        assert (await client.post('/api/v1/studio/shots', json=shot_body())).status_code == 400
        async with factory() as db:
            assert (await db.get(ShotDetail, 'manual')).first_frame_prompt == 'Keep this original prompt.'
            assert len((await db.scalars(select(Shot))).all()) == 1
