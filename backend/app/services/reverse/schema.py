"""Additive reverse-analysis migration with frozen source and machine evidence."""
from sqlalchemy import text
from app.core.db import Base,engine
import app.models.reverse  # noqa: F401


async def migrate_reverse(target_engine=engine):
    """Install local analysis tables without rewriting accepted production records."""
    if target_engine.dialect.name!='sqlite':raise RuntimeError('Reverse analysis currently requires SQLite')
    async with target_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        columns={row[1] for row in (await connection.execute(text('PRAGMA table_info(reverse_analyses)'))).all()}
        if columns!={column.name for column in Base.metadata.tables['reverse_analyses'].columns}:raise RuntimeError('Unsupported reverse-analysis schema')
        await connection.execute(text('CREATE TABLE IF NOT EXISTS reverse_schema(version INTEGER NOT NULL)'))
        versions=(await connection.execute(text('SELECT version FROM reverse_schema'))).scalars().all()
        if versions not in ([],[1]):raise RuntimeError('Unsupported reverse-analysis version')
        frozen=' OR '.join(f'OLD.{field} IS NOT NEW.{field}' for field in ['id','project_id','media_id','request_key','source_hash'])
        await connection.execute(text(f"CREATE TRIGGER IF NOT EXISTS reverse_source_frozen BEFORE UPDATE ON reverse_analyses WHEN {frozen} BEGIN SELECT RAISE(ABORT,'Reverse source identity is frozen'); END"))
        await connection.execute(text("CREATE TRIGGER IF NOT EXISTS reverse_evidence_frozen BEFORE UPDATE ON reverse_analyses WHEN OLD.manifest IS NOT NULL AND (OLD.manifest IS NOT NEW.manifest OR OLD.artifact_dir IS NOT NEW.artifact_dir) BEGIN SELECT RAISE(ABORT,'Machine analysis evidence is frozen'); END"))
        await connection.execute(text("CREATE TRIGGER IF NOT EXISTS reverse_analysis_no_delete BEFORE DELETE ON reverse_analyses BEGIN SELECT RAISE(ABORT,'Reverse analysis journal cannot be deleted'); END"))
        if not versions:await connection.execute(text('INSERT INTO reverse_schema VALUES(1)'))

