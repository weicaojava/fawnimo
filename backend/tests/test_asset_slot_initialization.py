"""Exercise atomic empty-slot intent on real independent SQLite connections, not a caught 409."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request
import httpx
import pytest
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.v1.routes.studio.entities import router
from app.core.db import Base
from app.dependencies import get_db
from app.models.studio import Actor, ActorImage, Character, CharacterImage, Costume, CostumeImage, FileItem, Project, Prop, PropImage, Scene, SceneImage

KINDS = {'actor': (Actor, ActorImage, 'actor_id'), 'costume': (Costume, CostumeImage, 'costume_id'), 'scene': (Scene, SceneImage, 'scene_id'), 'prop': (Prop, PropImage, 'prop_id')}


@asynccontextmanager
async def isolated(tmp_path):
    """Own a file DB and force two request sessions to acquire distinct connections before insertion."""
    engine = create_async_engine('sqlite+aiosqlite:///' + (tmp_path / 'slot-race.sqlite').as_posix(), poolclass=NullPool)
    @event.listens_for(engine.sync_engine, 'connect')
    def configure(connection, _):
        """Keep FK enforcement and bounded busy waiting identical on every fresh connection."""
        connection.execute('PRAGMA foreign_keys=ON'); connection.execute('PRAGMA busy_timeout=5000')
    async with engine.begin() as connection:
        assert await connection.scalar(text('PRAGMA journal_mode=WAL')) == 'wal'
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as db:
        db.add_all([FileItem(id='original', type='image', name='Original.png', storage_key='files/original.png'), FileItem(id='replacement', type='image', name='Replacement.png', storage_key='files/replacement.png')])
        for kind, (parent, _image, _owner) in KINDS.items():
            db.add(parent(id=kind + '-asset', name='Fixture ' + kind, style='真人都市'))
        await db.commit()
    barrier = {'enabled': False, 'arrived': 0, 'ready': asyncio.Event(), 'connections': []}

    async def database(request: Request):
        """Commit before success; no IntegrityError handler is installed to conceal race failures."""
        async with factory() as db:
            try:
                if barrier['enabled'] and request.method == 'POST':
                    connection = await db.connection()
                    barrier['connections'].append(id(connection.sync_connection.connection.dbapi_connection))
                    barrier['arrived'] += 1
                    if barrier['arrived'] == 2: barrier['ready'].set()
                    await asyncio.wait_for(barrier['ready'].wait(), 3)
                yield db
                await db.commit()
            except BaseException:
                await db.rollback(); raise
    app = FastAPI(); app.include_router(router, prefix='/api/v1/studio/entities'); app.dependency_overrides[get_db] = database
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://isolated') as client:
            yield client, factory, barrier
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', list(KINDS))
async def test_two_connections_ensure_exactly_one_empty_slot(tmp_path, kind):
    """Two simultaneous raw identity-only requests both return the same row, without 409/500."""
    async with isolated(tmp_path) as (client, factory, barrier):
        barrier['enabled'] = True
        path = f'/api/v1/studio/entities/{kind}/{kind}-asset/images'
        responses = await asyncio.gather(client.post(path, json={'view_angle': 'FRONT'}), client.post(path, json={'view_angle': 'FRONT', 'quality_level': 'LOW'}))
        assert [response.status_code for response in responses] == [201, 201]
        assert responses[0].json()['data']['id'] == responses[1].json()['data']['id']
        assert len(set(barrier['connections'])) == 2
        async with factory() as db:
            model = KINDS[kind][1]
            rows = (await db.scalars(select(model))).all()
            assert len(rows) == 1 and rows[0].file_id is None and rows[0].width is None


@pytest.mark.asyncio
async def test_identity_ensure_never_overwrites_existing_file_and_explicit_intent_conflicts(tmp_path):
    """A slot ensure may resolve a filled identity, but supplied file/size/primary/format cannot be swallowed."""
    async with isolated(tmp_path) as (client, factory, barrier):
        path = '/api/v1/studio/entities/actor/actor-asset/images'
        first = await client.post(path, json={'view_angle': 'FRONT', 'file_id': 'original', 'width': 128, 'height': 256, 'format': 'png'})
        saved = first.json()['data']; assert first.status_code == 201
        barrier['enabled'] = True
        repeated = await asyncio.gather(client.post(path, json={'view_angle': 'FRONT'}), client.post(path, json={}))
        assert all(response.status_code == 201 and response.json()['data'] == saved for response in repeated)
        assert len(set(barrier['connections'])) == 2
        barrier['enabled'] = False
        for intent in ({'file_id': 'replacement'}, {'file_id': None}, {'is_primary': True}, {'width': 1}, {'height': 2}, {'format': 'jpeg'}):
            response = await client.post(path, json={'view_angle': 'FRONT', **intent})
            assert response.status_code == 409, response.text
        async with factory() as db:
            original = await db.get(ActorImage, saved['id'])
            assert original.file_id == 'original' and original.width == 128 and original.height == 256 and original.format == 'png'
            assert (await db.scalars(select(ActorImage))).all() == [original]
            assert await db.get(FileItem, 'original') is not None


@pytest.mark.asyncio
async def test_two_explicit_image_creations_have_one_winner_without_overwrite(tmp_path):
    """The same race with real image intent remains a conflict instead of pretending both files were accepted."""
    async with isolated(tmp_path) as (client, factory, barrier):
        barrier['enabled'] = True
        path = '/api/v1/studio/entities/scene/scene-asset/images'
        responses = await asyncio.gather(*[client.post(path, json={'view_angle': 'LEFT', 'file_id': file_id}) for file_id in ('original', 'replacement')])
        assert sorted(response.status_code for response in responses) == [201, 409]
        winner = next(response.json()['data'] for response in responses if response.status_code == 201)
        async with factory() as db:
            rows = (await db.scalars(select(SceneImage))).all()
            assert len(rows) == 1 and rows[0].file_id == winner['file_id']


@pytest.mark.asyncio
async def test_quality_angle_are_distinct_identities_and_scope_is_required(tmp_path):
    """Ensuring another quality may create its own slot; missing parent never creates an orphan."""
    async with isolated(tmp_path) as (client, _, _):
        path = '/api/v1/studio/entities/prop/prop-asset/images'
        low = await client.post(path, json={'view_angle': 'FRONT'})
        high = await client.post(path, json={'view_angle': 'FRONT', 'quality_level': 'HIGH'})
        assert low.status_code == high.status_code == 201 and low.json()['data']['id'] != high.json()['data']['id']
        assert (await client.post('/api/v1/studio/entities/prop/missing-asset/images', json={'view_angle': 'FRONT'})).status_code == 404


@pytest.mark.asyncio
async def test_character_ensure_does_not_reapply_primary_or_touch_other_slot_clocks(tmp_path):
    """Resolving an existing primary identity must not rewrite any other character image timestamp."""
    async with isolated(tmp_path) as (client, factory, _):
        preserved_clock = datetime(2001, 2, 3, 4, 5, 6)
        async with factory() as db:
            db.add(Project(id='fixture-project', name='Fixture project', style='真人都市')); await db.flush()
            db.add(Character(id='fixture-role', project_id='fixture-project', name='Fixture role', style='真人都市')); await db.flush()
            front = CharacterImage(character_id='fixture-role', view_angle='FRONT', quality_level='LOW', file_id='original', is_primary=True)
            left = CharacterImage(character_id='fixture-role', view_angle='LEFT', quality_level='LOW', file_id='replacement', is_primary=False, updated_at=preserved_clock)
            db.add_all([front, left]); await db.commit()
            front_id, left_id = front.id, left.id
        response = await client.post('/api/v1/studio/entities/character/fixture-role/images', json={'view_angle': 'FRONT'})
        assert response.status_code == 201 and response.json()['data']['id'] == front_id
        async with factory() as db:
            other = await db.get(CharacterImage, left_id)
            assert not other.is_primary and other.updated_at == preserved_clock and other.file_id == 'replacement'
