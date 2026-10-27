"""Additive local-export schema with immutable receipts and scoped job identities."""
from sqlalchemy import text
from app.core.db import Base, engine
import app.models.editing_export  # noqa: F401


async def migrate_editing_export(target_engine=engine):
    """Install local render journals without rewriting paid jobs, timelines or user media."""
    if target_engine.dialect.name != 'sqlite':
        raise RuntimeError('Local editing exports require SQLite')
    async with target_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(text('CREATE TABLE IF NOT EXISTS edit_export_schema(version INTEGER NOT NULL)'))
        versions = (await connection.execute(text('SELECT version FROM edit_export_schema'))).scalars().all()
        if versions not in ([], [1]):
            raise RuntimeError('Unsupported local export schema')
        for table in ('edit_export_jobs', 'edit_export_products', 'edit_export_slot'):
            actual = {row[1] for row in (await connection.execute(text(f'PRAGMA table_info({table})'))).all()}
            if actual != {column.name for column in Base.metadata.tables[table].columns}:
                raise RuntimeError('Unsupported local export columns')
        for operation in ('UPDATE', 'DELETE'):
            await connection.execute(text(f"CREATE TRIGGER IF NOT EXISTS edit_export_products_{operation.lower()}_immutable BEFORE {operation} ON edit_export_products BEGIN SELECT RAISE(ABORT,'Export receipt immutable'); END"))
        columns = ('id', 'project_id', 'chapter_id', 'publication_id', 'publication_hash', 'task_id', 'request_key', 'intent_hash')
        changed = ' OR '.join(f'OLD.{key} IS NOT NEW.{key}' for key in columns)
        await connection.execute(text(f"CREATE TRIGGER IF NOT EXISTS edit_export_job_identity BEFORE UPDATE ON edit_export_jobs WHEN {changed} BEGIN SELECT RAISE(ABORT,'Export job identity immutable'); END"))
        await connection.execute(text("CREATE TRIGGER IF NOT EXISTS edit_export_job_no_delete BEFORE DELETE ON edit_export_jobs BEGIN SELECT RAISE(ABORT,'Export history cannot be deleted'); END"))
        await connection.execute(text("CREATE TRIGGER IF NOT EXISTS edit_export_job_scope BEFORE INSERT ON edit_export_jobs WHEN NOT EXISTS(SELECT 1 FROM editing_publications p WHERE p.id=NEW.publication_id AND p.project_id=NEW.project_id AND p.chapter_id=NEW.chapter_id AND p.content_hash=NEW.publication_hash) BEGIN SELECT RAISE(ABORT,'Export publication scope mismatch'); END"))
        await connection.execute(text("CREATE TRIGGER IF NOT EXISTS edit_export_product_scope BEFORE INSERT ON edit_export_products WHEN NOT EXISTS(SELECT 1 FROM edit_export_jobs j JOIN production_media m ON m.id=NEW.media_id WHERE j.id=NEW.job_id AND j.project_id=NEW.project_id AND m.project_id=NEW.project_id AND m.mime='video/mp4' AND json_extract(NEW.artifacts,'$.video.sha256')=m.sha256 AND json_extract(NEW.artifacts,'$.video.relative_path')=m.relative_path) BEGIN SELECT RAISE(ABORT,'Export product scope mismatch'); END"))
        await connection.execute(text("INSERT OR IGNORE INTO edit_export_slot(id,lease_until) VALUES(1,0)"))
        if not versions:
            await connection.execute(text('INSERT INTO edit_export_schema VALUES(1)'))
        if (await connection.execute(text('PRAGMA foreign_key_check'))).first():
            raise RuntimeError('Export foreign key conflict')
