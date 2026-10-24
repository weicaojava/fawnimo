"""SQLAlchemy 同步引擎与会话。

给 Celery worker 使用，避免在同步 worker 进程里承载 async DB runtime。
"""

from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.core.db import Base


def _to_sync_database_url(url: str) -> str:
    if url.startswith("mysql+aiomysql://"):
        return "mysql+pymysql://" + url.removeprefix("mysql+aiomysql://")
    if url.startswith("sqlite+aiosqlite:///"):
        return "sqlite:///" + url.removeprefix("sqlite+aiosqlite:///")
    return url


def _build_sync_engine() -> Engine:
    """Apply the same local referential constraints in worker connections as in API connections."""
    result = create_engine(
        _to_sync_database_url(settings.database_url),
        echo=settings.debug,
        future=True,
        pool_pre_ping=True,
    )
    if result.dialect.name == 'sqlite':
        @event.listens_for(result, 'connect')
        def configure_sqlite(connection, _record) -> None:
            """Ensure a background writer cannot bypass foreign-key existence checks."""
            cursor=connection.cursor()
            cursor.execute('PRAGMA foreign_keys=ON')
            cursor.execute('PRAGMA busy_timeout=5000')
            cursor.close()
    return result


engine_sync = _build_sync_engine()
sync_session_maker = sessionmaker(
    bind=engine_sync,
    class_=Session,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)

__all__ = [
    "Base",
    "engine_sync",
    "sync_session_maker",
]
