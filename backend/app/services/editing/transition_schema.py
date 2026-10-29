"""Add independent v2 state guards; existing v1 schema and frozen payloads are never rewritten."""
from sqlalchemy import text
from app.core.db import Base
import app.models.editing_v2  # noqa: F401


async def migrate_transitions(engine):
    """Create v2 documents/journals and enforce scope, CAS and immutable publication/receipt identities."""
    if engine.dialect.name != 'sqlite': return
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        for table in ('transition_drafts', 'transition_publications', 'transition_export_jobs', 'transition_export_products', 'transition_export_slot'):
            actual = {row[1] for row in (await connection.execute(text(f'PRAGMA table_info({table})'))).all()}
            if actual != {column.name for column in Base.metadata.tables[table].columns}: raise RuntimeError('Unsupported transition-v2 schema')
        await connection.execute(text("CREATE TRIGGER IF NOT EXISTS transition_draft_scope BEFORE INSERT ON transition_drafts WHEN NEW.revision!=1 OR NOT EXISTS(SELECT 1 FROM editing_publications p WHERE p.id=NEW.base_publication_id AND p.project_id=NEW.project_id AND p.chapter_id=NEW.chapter_id) BEGIN SELECT RAISE(ABORT,'Transition base scope'); END"))
        await connection.execute(text("CREATE TRIGGER IF NOT EXISTS transition_draft_cas BEFORE UPDATE ON transition_drafts WHEN NEW.project_id IS NOT OLD.project_id OR NEW.chapter_id IS NOT OLD.chapter_id OR NEW.base_publication_id IS NOT OLD.base_publication_id OR NEW.id IS NOT OLD.id OR NEW.revision!=OLD.revision+1 BEGIN SELECT RAISE(ABORT,'Transition draft CAS'); END"))
        await connection.execute(text("CREATE TRIGGER IF NOT EXISTS transition_publication_scope BEFORE INSERT ON transition_publications WHEN NOT EXISTS(SELECT 1 FROM transition_drafts d WHERE d.id=NEW.draft_id AND d.project_id=NEW.project_id AND d.chapter_id=NEW.chapter_id AND d.revision=NEW.draft_revision AND json_extract(NEW.payload,'$.base.publication_id')=d.base_publication_id) BEGIN SELECT RAISE(ABORT,'Transition publication scope'); END"))
        for table in ('transition_publications', 'transition_export_products'):
            for action in ('UPDATE', 'DELETE'):
                await connection.execute(text(f"CREATE TRIGGER IF NOT EXISTS {table}_{action.lower()}_immutable BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Immutable transition-v2 receipt'); END"))
        columns = ('id', 'project_id', 'chapter_id', 'publication_id', 'publication_hash', 'task_id', 'request_key', 'intent_hash')
        changed = ' OR '.join(f'OLD.{key} IS NOT NEW.{key}' for key in columns)
        await connection.execute(text(f"CREATE TRIGGER IF NOT EXISTS transition_export_job_identity BEFORE UPDATE ON transition_export_jobs WHEN {changed} BEGIN SELECT RAISE(ABORT,'Transition export identity immutable'); END"))
        await connection.execute(text("CREATE TRIGGER IF NOT EXISTS transition_export_job_no_delete BEFORE DELETE ON transition_export_jobs BEGIN SELECT RAISE(ABORT,'Transition export history immutable'); END"))
        await connection.execute(text("CREATE TRIGGER IF NOT EXISTS transition_export_job_scope BEFORE INSERT ON transition_export_jobs WHEN NOT EXISTS(SELECT 1 FROM transition_publications p WHERE p.id=NEW.publication_id AND p.project_id=NEW.project_id AND p.chapter_id=NEW.chapter_id AND p.content_hash=NEW.publication_hash) BEGIN SELECT RAISE(ABORT,'Transition export publication scope'); END"))
        await connection.execute(text("CREATE TRIGGER IF NOT EXISTS transition_export_product_scope BEFORE INSERT ON transition_export_products WHEN NOT EXISTS(SELECT 1 FROM transition_export_jobs j JOIN production_media m ON m.id=NEW.media_id WHERE j.id=NEW.job_id AND j.project_id=NEW.project_id AND m.project_id=NEW.project_id AND m.mime='video/mp4' AND json_extract(NEW.artifacts,'$.video.sha256')=m.sha256 AND json_extract(NEW.artifacts,'$.video.relative_path')=m.relative_path) BEGIN SELECT RAISE(ABORT,'Transition output source scope'); END"))
        await connection.execute(text('INSERT OR IGNORE INTO transition_export_slot(id,lease_until) VALUES(1,0)'))
        if (await connection.execute(text('PRAGMA foreign_key_check'))).first(): raise RuntimeError('Transition-v2 foreign key conflict')
