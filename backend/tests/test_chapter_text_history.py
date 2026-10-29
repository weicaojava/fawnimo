"""Real isolated SQLite/API acceptance for chapter revisions and restore semantics."""
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.api.v1.routes.studio.chapters import router
from app.core.db import Base
from app.dependencies import get_db
from app.models.chapter_text_history import ChapterTextRevision
from app.models.studio import Chapter, Project, Shot


@asynccontextmanager
async def api(tmp_path):
    """Run only chapter routes against a disposable DB, with no workers or model adapters."""
    engine = create_async_engine('sqlite+aiosqlite:///' + (tmp_path / 'history.db').as_posix())

    @event.listens_for(engine.sync_engine, 'connect')
    def configure(connection, _record):
        """Enable foreign keys and bounded locking on each real SQLite connection."""
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA busy_timeout=5000')
        connection.execute('PRAGMA journal_mode=WAL')

    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[Project.__table__, Chapter.__table__, Shot.__table__, ChapterTextRevision.__table__]))
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as db:
        db.add(Project(id='project', name='隔离测试', style='真人古装', visual_style='现实'))
        await db.commit()
    app = FastAPI()
    app.include_router(router, prefix='/chapters')

    async def database():
        """Commit exactly one API request, rolling every failed operation back."""
        async with factory() as db:
            try:
                yield db
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    app.dependency_overrides[get_db] = database
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            yield client, factory
    finally:
        await engine.dispose()


async def create(client, chapter_id='chapter'):
    """Create a real text-bearing chapter through the supported route."""
    response = await client.post('/chapters', json={'id': chapter_id, 'project_id': 'project', 'index': 1 if chapter_id == 'chapter' else 2,
        'title': '第一章', 'raw_text': '原始正文', 'condensed_text': '原始精简'})
    assert response.status_code == 201, response.text


def test_saved_history_restore_and_noop(tmp_path):
    """Editing and restoring retain the old rows and restore as a new current revision."""
    async def scenario():
        """Exercise actual API data after every save rather than trusting a status response."""
        async with api(tmp_path) as (client, _factory):
            await create(client)
            first = (await client.get('/chapters/chapter/text-history')).json()['data']
            assert first['current_revision'] == 1 and first['items'][0]['raw_text'] == '原始正文'
            saved = await client.post('/chapters/chapter/text-history', json={'expected_revision': 1, 'raw_text': '修改后的正文'})
            assert saved.status_code == 200 and saved.json()['data']['current_revision'] == 2
            assert saved.json()['data']['condensed_text'] == '原始精简'
            again = await client.post('/chapters/chapter/text-history', json={'expected_revision': 2, 'raw_text': '修改后的正文'})
            assert again.json()['data']['current_revision'] == 2
            restored = await client.post('/chapters/chapter/text-history/1/restore', json={'expected_revision': 2})
            assert restored.json()['data']['current_revision'] == 3
            chapter = (await client.get('/chapters/chapter')).json()['data']
            assert chapter['raw_text'] == '原始正文' and chapter['condensed_text'] == '原始精简'
            history = (await client.get('/chapters/chapter/text-history')).json()['data']
            assert [item['revision'] for item in history['items']] == [3, 2, 1]
            assert history['items'][0]['origin'] == 'restore' and history['items'][0]['restored_from_revision'] == 1
            assert history['items'][1]['raw_text'] == '修改后的正文'
            assert history['items'][2]['content_hash'] == first['items'][0]['content_hash']
    asyncio.run(scenario())


def test_legacy_patch_records_original_and_new_text(tmp_path):
    """Legacy chapter rows have no invented past; the first real save preserves their baseline."""
    async def scenario():
        """Seed one legacy row directly and exercise the unchanged public PATCH contract."""
        async with api(tmp_path) as (client, factory):
            async with factory() as db:
                db.add(Chapter(id='legacy', project_id='project', index=1, title='旧章节', raw_text='既有正文', condensed_text='既有精简'))
                await db.commit()
            initial = (await client.get('/chapters/legacy/text-history')).json()['data']
            assert initial['current_revision'] == 0 and initial['items'] == []
            response = await client.patch('/chapters/legacy', json={'raw_text': '新正文'})
            assert response.status_code == 200, response.text
            history = (await client.get('/chapters/legacy/text-history')).json()['data']
            assert history['current_revision'] == 2
            assert [item['raw_text'] for item in history['items']] == ['新正文', '既有正文']
            assert all(item['condensed_text'] == '既有精简' for item in history['items'])
    asyncio.run(scenario())


def test_stale_editor_and_cross_chapter_restore_are_rejected(tmp_path):
    """Conflicts cannot overwrite newer text or restore a revision from another chapter."""
    async def scenario():
        """Check stored text and history after rejected requests."""
        async with api(tmp_path) as (client, _factory):
            await create(client)
            await create(client, 'other')
            await client.post('/chapters/chapter/text-history', json={'expected_revision': 1, 'raw_text': '第一位编辑'})
            stale = await client.post('/chapters/chapter/text-history', json={'expected_revision': 1, 'raw_text': '过期覆盖'})
            assert stale.status_code == 409
            restore = await client.post('/chapters/chapter/text-history/1/restore', json={'expected_revision': 1})
            assert restore.status_code == 409
            foreign = await client.post('/chapters/other/text-history/2/restore', json={'expected_revision': 1})
            assert foreign.status_code == 404
            assert (await client.get('/chapters/chapter')).json()['data']['raw_text'] == '第一位编辑'
            assert (await client.get('/chapters/other/text-history')).json()['data']['total'] == 1
    asyncio.run(scenario())


def test_parallel_saves_one_winner_and_invalid_input(tmp_path):
    """A real SQLite write race yields one winner, and invalid input never creates history."""
    async def scenario():
        """Use separate request sessions so stale-revision protection is exercised under contention."""
        async with api(tmp_path) as (client, _factory):
            await create(client)
            responses = await asyncio.gather(*[client.post('/chapters/chapter/text-history', json={'expected_revision': 1, 'raw_text': value}) for value in ['并发甲', '并发乙']])
            assert sorted(r.status_code for r in responses) == [200, 409]
            assert (await client.get('/chapters/chapter/text-history')).json()['data']['total'] == 2
            for body in [{'expected_revision': True, 'raw_text': 'bad'}, {'expected_revision': 2}, {'expected_revision': 2, 'raw_text': None}, {'expected_revision': 2, 'raw_text': 'x' * 200001}]:
                assert (await client.post('/chapters/chapter/text-history', json=body)).status_code == 422
            assert (await client.get('/chapters/chapter/text-history')).json()['data']['total'] == 2
    asyncio.run(scenario())
