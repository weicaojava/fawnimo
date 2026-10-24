"""Additive SQLite registration and immutable manual preparation evidence."""
from sqlalchemy import text
from app.core.db import Base,engine
import app.models.script_preparation  # noqa: F401


async def migrate_script_preparation(target_engine=engine):
    """Keep all old project/source records while installing scope and journal guards."""
    if target_engine.dialect.name!='sqlite':raise RuntimeError('Script preparation requires SQLite')
    async with target_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS chapter_id_project_scope ON chapters(id,project_id)'))
        await connection.execute(text('CREATE TABLE IF NOT EXISTS script_preparation_schema(version INTEGER NOT NULL)'))
        versions=(await connection.execute(text('SELECT version FROM script_preparation_schema'))).scalars().all()
        if versions not in ([],[1]):raise RuntimeError('Unsupported script preparation schema')
        for name in ['script_preparation_drafts','script_preparation_snapshots']:
            actual={r[1] for r in (await connection.execute(text(f'PRAGMA table_info({name})'))).all()}
            if actual!={c.name for c in Base.metadata.tables[name].columns}:raise RuntimeError('Unsupported preparation columns')
        condition=' OR '.join(f'OLD.{key} IS NOT NEW.{key}' for key in ['id','project_id','chapter_id'])
        await connection.execute(text(f"CREATE TRIGGER IF NOT EXISTS script_draft_identity BEFORE UPDATE ON script_preparation_drafts WHEN {condition} BEGIN SELECT RAISE(ABORT,'Script identity immutable'); END"))
        await connection.execute(text("CREATE TRIGGER IF NOT EXISTS script_draft_no_delete BEFORE DELETE ON script_preparation_drafts BEGIN SELECT RAISE(ABORT,'Script registration cannot be deleted'); END"))
        for operation in ['UPDATE','DELETE']:
            await connection.execute(text(f"CREATE TRIGGER IF NOT EXISTS script_snapshot_{operation.lower()} BEFORE {operation} ON script_preparation_snapshots BEGIN SELECT RAISE(ABORT,'Script snapshot immutable'); END"))
        if not versions:await connection.execute(text('INSERT INTO script_preparation_schema VALUES(1)'))
        if (await connection.execute(text('PRAGMA foreign_key_check'))).first():raise RuntimeError('Preparation foreign key conflict')
