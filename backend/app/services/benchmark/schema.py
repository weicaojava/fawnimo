"""Explicit additive SQLite benchmark preparation migration."""
from sqlalchemy import text
from app.core.db import Base,engine
import app.models.benchmark  # noqa: F401


async def migrate_benchmark(target_engine=engine):
    """Install immutable snapshots and fixed draft identity without changing old evidence."""
    if target_engine.dialect.name!='sqlite':raise RuntimeError('Benchmark preparation requires SQLite')
    async with target_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(text('CREATE TABLE IF NOT EXISTS benchmark_schema(version INTEGER NOT NULL)'))
        versions=(await connection.execute(text('SELECT version FROM benchmark_schema'))).scalars().all()
        if versions not in ([],[1]):raise RuntimeError('Unsupported benchmark schema')
        for name in ['benchmark_drafts','benchmark_snapshots']:
            actual={row[1] for row in (await connection.execute(text(f'PRAGMA table_info({name})'))).all()}
            if actual!={column.name for column in Base.metadata.tables[name].columns}:raise RuntimeError('Unsupported benchmark table columns')
        condition=' OR '.join(f'OLD.{name} IS NOT NEW.{name}' for name in ['id','project_id','analysis_id','request_key','initial_hash'])
        await connection.execute(text(f"CREATE TRIGGER IF NOT EXISTS benchmark_draft_identity BEFORE UPDATE ON benchmark_drafts WHEN {condition} BEGIN SELECT RAISE(ABORT,'Benchmark identity frozen'); END"))
        for operation in ['UPDATE','DELETE']:
            await connection.execute(text(f"CREATE TRIGGER IF NOT EXISTS benchmark_snapshot_{operation.lower()} BEFORE {operation} ON benchmark_snapshots BEGIN SELECT RAISE(ABORT,'Benchmark snapshot immutable'); END"))
        await connection.execute(text("CREATE TRIGGER IF NOT EXISTS benchmark_draft_no_delete BEFORE DELETE ON benchmark_drafts BEGIN SELECT RAISE(ABORT,'Benchmark draft journal cannot be deleted'); END"))
        if not versions:await connection.execute(text('INSERT INTO benchmark_schema VALUES(1)'))
        if (await connection.execute(text('PRAGMA foreign_key_check'))).first():raise RuntimeError('Benchmark migration reference conflict')
