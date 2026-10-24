"""Explicit additive SQLite execution migration; never runs provider requests."""
from sqlalchemy import text
from app.core.db import Base, engine
import app.models.execution  # noqa: F401
import app.models.execution_budget  # noqa: F401
import app.models.execution_binding  # noqa: F401
import app.models.llm  # noqa: F401
import app.models.studio  # noqa: F401
import app.models.task  # noqa: F401
import app.models.production  # noqa: F401


async def migrate_execution(target_engine=engine) -> None:
    """Create execution tables and protect frozen request identities from mutation."""
    if target_engine.dialect.name!='sqlite':raise RuntimeError('Execution migration requires SQLite')
    async with target_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(text('CREATE TABLE IF NOT EXISTS execution_schema(version INTEGER NOT NULL)'))
        versions=(await connection.execute(text('SELECT version FROM execution_schema'))).scalars().all()
        if versions not in ([],[1]):raise RuntimeError('Unsupported execution schema')
        for table in ['execution_jobs','submission_attempts','execution_events','execution_slots','execution_budgets','execution_budget_entries','execution_bindings']:
            actual={row[1] for row in (await connection.execute(text(f'PRAGMA table_info({table})'))).all()}
            expected={column.name for column in Base.metadata.tables[table].columns}
            if actual!=expected:raise RuntimeError(f'Unsupported columns in {table}; migration refused')
        for table,fields in [('execution_jobs',['id','task_id','project_id','request_key','request_hash','snapshot','media_id']),('submission_attempts',['id','job_id','request_hash']),('execution_budget_entries',['job_id','project_id','quote_minor']),('execution_bindings',['job_id','binding_hash'])]:
            condition=' OR '.join(f'OLD.{field} IS NOT NEW.{field}' for field in fields)
            await connection.execute(text(f"CREATE TRIGGER IF NOT EXISTS {table}_frozen_identity BEFORE UPDATE ON {table} WHEN {condition} BEGIN SELECT RAISE(ABORT,'Frozen execution identity'); END"))
            await connection.execute(text(f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT,'Execution journal cannot be deleted'); END"))
        if not versions:await connection.execute(text('INSERT INTO execution_schema(version) VALUES(1)'))
        if (await connection.execute(text('PRAGMA foreign_key_check'))).first():raise RuntimeError('Execution migration foreign-key check failed')
