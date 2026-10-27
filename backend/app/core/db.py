"""SQLAlchemy 异步引擎与会话。"""

from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import event

from app.config import settings


def _build_engine() -> AsyncEngine:
    """Create an engine with referential integrity enabled on every SQLite connection."""
    result = create_async_engine(
        settings.database_url,
        echo=settings.debug,
        future=True,
    )
    if result.dialect.name == "sqlite":
        @event.listens_for(result.sync_engine, "connect")
        def configure_sqlite(connection, _record) -> None:
            """Prevent orphan rows and bound contention waits in local mode."""
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()
    return result


def _build_session_maker(bind_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )


class _AsyncSessionMakerProxy:
    """可重绑定的 sessionmaker 代理。

    Celery prefork 模式下，worker 子进程不能继续复用父进程里初始化的
    async engine / sessionmaker。这里保持导入对象稳定，同时允许在子进程
    启动后重新绑定底层 sessionmaker。
    """

    def __init__(self, maker: async_sessionmaker[AsyncSession]) -> None:
        self._maker = maker

    def configure(self, maker: async_sessionmaker[AsyncSession]) -> None:
        self._maker = maker

    def __call__(self, *args: Any, **kwargs: Any) -> AsyncSession:
        return self._maker(*args, **kwargs)


engine = _build_engine()
async_session_maker = _AsyncSessionMakerProxy(_build_session_maker(engine))


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""

    pass


async def init_db() -> None:
    """创建所有表（开发/迁移用）。"""
    from app.core.sqlite_runtime import prepare_sqlite
    await prepare_sqlite(engine)
    # 确保 ORM 模型已导入，从而注册到 Base.metadata
    import app.models.llm  # noqa: F401  # pylint: disable=unused-import
    import app.models.studio  # noqa: F401
    import app.models.task  # noqa: F401
    import app.models.task_links  # noqa: F401
    import app.models.production  # noqa: F401
    import app.models.execution  # noqa: F401
    import app.models.execution_budget  # noqa: F401
    import app.models.execution_binding  # noqa: F401
    import app.models.reverse  # noqa: F401
    import app.models.benchmark  # noqa: F401
    import app.models.script_preparation  # noqa: F401
    import app.models.shot_plan  # noqa: F401
    import app.models.editing  # noqa: F401
    import app.models.editing_export  # noqa: F401

    async with engine.begin() as conn:
        if engine.dialect.name=='sqlite':
            from sqlalchemy import text
            if (await conn.execute(text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chapters'"))).first():
                await conn.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS chapter_id_project_scope ON chapters(id,project_id)'))
        await conn.run_sync(Base.metadata.create_all)
    if engine.dialect.name == "sqlite":
        from app.services.studio.production_schema import migrate_production
        await migrate_production()
        from app.services.worker.execution_schema import migrate_execution
        await migrate_execution(engine)
        from app.services.reverse.schema import migrate_reverse
        await migrate_reverse(engine)
        from app.services.benchmark.schema import migrate_benchmark
        await migrate_benchmark(engine)
        from app.services.script_preparation.schema import migrate_script_preparation
        await migrate_script_preparation(engine)
        from app.services.shot_plan.schema import migrate_shot_plan
        await migrate_shot_plan(engine)
        from app.services.editing.schema import migrate_editing
        await migrate_editing(engine)
        from app.services.editing.export_schema import migrate_editing_export
        await migrate_editing_export(engine)


async def close_db() -> None:
    """关闭数据库连接。"""
    await engine.dispose()


def reset_db_runtime() -> None:
    """在 Celery worker 子进程中重建 engine 与 sessionmaker。

    这样可以避免 prefork 继承父进程中的 async engine，导致连接对象和事件循环
    绑定错乱，触发 Future attached to a different loop。
    """

    global engine

    engine = _build_engine()
    async_session_maker.configure(_build_session_maker(engine))
