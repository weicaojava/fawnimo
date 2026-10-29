"""Check persisted project timestamps and complete pagination on an independent SQLite database."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.v1.routes.studio.projects import router
from app.core.db import Base
from app.dependencies import get_db
from app.models.studio import Project, ProjectStyle, ProjectVisualStyle
from app.schemas.studio.projects import ProjectRead


def test_project_read_restores_sqlite_utc_and_normalizes_aware_time() -> None:
    """A SQL UTC value must never become browser-local time or a newly fabricated current time."""
    naive = datetime(2026, 10, 28, 16, 30, 12)
    aware = datetime(2026, 10, 29, 0, 30, 12, tzinfo=timezone(timedelta(hours=8)))
    row = ProjectRead(id="timestamp-only", name="Timestamp test", style=ProjectStyle.real_people_city,
                      created_at=naive, updated_at=aware)
    assert row.created_at == row.updated_at == naive.replace(tzinfo=UTC)
    encoded = row.model_dump(mode="json")
    assert encoded["created_at"] == encoded["updated_at"] == "2026-10-28T16:30:12Z"


def test_project_pagination_discovers_all_records_with_real_stable_timestamps(tmp_path) -> None:
    """Traverse more than ten real pages and confirm the API clock is the stored SQLite clock."""
    engine = create_async_engine("sqlite+aiosqlite:///" + (tmp_path / "discovery.db").as_posix(), poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    start = datetime(2026, 10, 1, 1, 2, 3)

    async def prepare() -> None:
        """Seed this test's database only, with distinct clocks for deterministic newest-first ordering."""
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            session.add_all([
                Project(id=f"project-{index:03}", name=f"Project {index}",
                        style=ProjectStyle.real_people_city, visual_style=ProjectVisualStyle.live_action,
                        created_at=start, updated_at=start + timedelta(minutes=index))
                for index in range(105)
            ])
            await session.commit()

    async def database():
        """Route reads to the isolated test database rather than the user's workspace."""
        async with factory() as session:
            yield session

    asyncio.run(prepare())
    app = FastAPI()
    app.include_router(router, prefix="/projects")
    app.dependency_overrides[get_db] = database
    try:
        with TestClient(app) as client:
            items = []
            for page in range(1, 12):
                response = client.get("/projects", params={"page": page, "page_size": 10, "order": "updated_at", "is_desc": True})
                assert response.status_code == 200, response.text
                data = response.json()["data"]
                assert data["pagination"]["total"] == 105
                assert data["pagination"]["max_page"] == 11
                items.extend(data["items"])
            assert [item["id"] for item in items] == [f"project-{index:03}" for index in reversed(range(105))]
            assert len({item["id"] for item in items}) == 105
            expected = (start + timedelta(minutes=104)).replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")
            assert items[0]["created_at"] == "2026-10-01T01:02:03Z"
            assert items[0]["updated_at"] == expected
            first = client.get("/projects/project-104").json()["data"]
            second = client.get("/projects/project-104").json()["data"]
            assert first["updated_at"] == second["updated_at"] == expected
    finally:
        asyncio.run(engine.dispose())
