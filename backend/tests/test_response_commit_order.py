"""A successful HTTP header must imply that a following request can see the saved row."""
import sqlite3
from contextlib import closing
import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.api.v1.routes.studio.prompts import router
from app.dependencies import get_db
from app.models.studio import PromptTemplate


@pytest.mark.asyncio
async def test_actual_prompt_commit_precedes_http_response_start(tmp_path):
    """Inspect an independent connection at ASGI response-start, before client cleanup can hide races."""
    database = tmp_path / 'response-order.db'
    engine = create_async_engine('sqlite+aiosqlite:///' + database.as_posix())
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: PromptTemplate.__table__.create(sync))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    events, visible = [], []

    async def session():
        """Use a real session and record the same after-yield commit as the production dependency."""
        async with factory() as db:
            try:
                yield db
                await db.commit()
                events.append('committed')
            except Exception:
                await db.rollback()
                raise

    app = FastAPI()
    app.include_router(router, prefix='/prompts')
    app.dependency_overrides[get_db] = session

    async def probe(scope, receive, send):
        """Observe durability exactly when the client is first told the operation succeeded."""
        async def observe(message):
            """Query through another connection rather than the writer's uncommitted identity map."""
            if message['type'] == 'http.response.start' and message['status'] == 201:
                events.append('http_success')
                with closing(sqlite3.connect(database)) as reader:
                    visible.append(reader.execute("SELECT COUNT(*) FROM prompt_templates WHERE name='commit-probe'").fetchone()[0])
            await send(message)
        await app(scope, receive, observe)

    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=probe), base_url='http://test') as client:
            response = await client.post('/prompts', json={'category': 'video_prompt', 'name': 'commit-probe', 'content': 'A persisted prompt'})
        assert response.status_code == 201
        assert events == ['committed', 'http_success'] and visible == [1]
    finally:
        await engine.dispose()
