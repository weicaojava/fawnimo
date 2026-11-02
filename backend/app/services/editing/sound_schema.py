"""Additive sound-v3 scope, CAS and append-only history guards."""
from sqlalchemy import text
from app.core.db import Base
import app.models.sound_timeline  # noqa: F401

TABLES = ('sound_candidates', 'sound_drafts', 'sound_publications', 'sound_export_jobs', 'sound_export_products', 'sound_export_slot')


async def migrate_sound(engine):
    """Create independent tables without changing any old v1/v2 payload, clock or receipt."""
    if engine.dialect.name != 'sqlite': return
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        for table in TABLES:
            actual = {row[1] for row in (await connection.execute(text(f'PRAGMA table_info({table})'))).all()}
            # The fixed-workspace binder adds this one known non-ORM ownership column.
            if actual - {'workspace_id'} != {column.name for column in Base.metadata.tables[table].columns} - {'workspace_id'}:
                raise RuntimeError('Unsupported sound-v3 schema')
        statements = {
            'sound_draft_initial': "BEFORE INSERT ON sound_drafts WHEN NEW.revision!=1 OR EXISTS(SELECT 1 FROM sound_drafts d WHERE d.id=NEW.id OR (d.project_id=NEW.project_id AND d.chapter_id=NEW.chapter_id)) BEGIN SELECT RAISE(ABORT,'Sound draft initial scope'); END",
            'sound_draft_cas': "BEFORE UPDATE ON sound_drafts WHEN NEW.id IS NOT OLD.id OR NEW.project_id IS NOT OLD.project_id OR NEW.chapter_id IS NOT OLD.chapter_id OR NEW.revision!=OLD.revision+1 BEGIN SELECT RAISE(ABORT,'Sound draft CAS'); END",
            'sound_draft_no_delete': "BEFORE DELETE ON sound_drafts BEGIN SELECT RAISE(ABORT,'Sound draft history'); END",
            'sound_candidate_scope': "BEFORE INSERT ON sound_candidates WHEN NOT EXISTS(SELECT 1 FROM production_media m JOIN chapters c ON c.id=NEW.chapter_id WHERE m.id=NEW.media_id AND m.project_id=NEW.project_id AND c.project_id=NEW.project_id AND m.mime='audio/wav' AND json_extract(NEW.evidence,'$.id')=m.id AND json_extract(NEW.evidence,'$.sha256')=m.sha256 AND json_extract(NEW.evidence,'$.size')=m.size AND json_extract(NEW.evidence,'$.mime')=m.mime AND json_extract(NEW.evidence,'$.relative_path')=m.relative_path) BEGIN SELECT RAISE(ABORT,'Sound candidate source scope'); END",
            'sound_publication_scope': "BEFORE INSERT ON sound_publications WHEN NOT EXISTS(SELECT 1 FROM sound_drafts d WHERE d.id=NEW.draft_id AND d.project_id=NEW.project_id AND d.chapter_id=NEW.chapter_id AND d.revision=NEW.draft_revision AND json_extract(NEW.payload,'$.schema_version')=3 AND json_extract(NEW.payload,'$.project_id')=d.project_id AND json_extract(NEW.payload,'$.chapter_id')=d.chapter_id AND json(json_extract(NEW.payload,'$.document'))=json(d.document)) BEGIN SELECT RAISE(ABORT,'Sound publication scope'); END",
            'sound_export_job_scope': "BEFORE INSERT ON sound_export_jobs WHEN NOT EXISTS(SELECT 1 FROM sound_publications p WHERE p.id=NEW.publication_id AND p.project_id=NEW.project_id AND p.chapter_id=NEW.chapter_id AND p.content_hash=NEW.publication_hash) OR EXISTS(SELECT 1 FROM sound_export_jobs j WHERE j.id=NEW.id OR (j.project_id=NEW.project_id AND j.request_key=NEW.request_key)) BEGIN SELECT RAISE(ABORT,'Sound export scope or replacement'); END",
            'sound_export_job_no_delete': "BEFORE DELETE ON sound_export_jobs BEGIN SELECT RAISE(ABORT,'Sound export history'); END",
            'sound_export_product_scope': "BEFORE INSERT ON sound_export_products WHEN NOT EXISTS(SELECT 1 FROM sound_export_jobs j JOIN production_media m ON m.id=NEW.media_id WHERE j.id=NEW.job_id AND j.project_id=NEW.project_id AND m.project_id=NEW.project_id AND m.mime='video/mp4' AND json_extract(NEW.artifacts,'$.video.sha256')=m.sha256 AND json_extract(NEW.artifacts,'$.video.relative_path')=m.relative_path) BEGIN SELECT RAISE(ABORT,'Sound export product scope'); END",
        }
        columns = ('id', 'project_id', 'chapter_id', 'publication_id', 'publication_hash', 'task_id', 'request_key', 'intent_hash')
        changed = ' OR '.join(f'OLD.{key} IS NOT NEW.{key}' for key in columns)
        statements['sound_export_job_identity'] = f"BEFORE UPDATE ON sound_export_jobs WHEN {changed} BEGIN SELECT RAISE(ABORT,'Sound export identity immutable'); END"
        for table in ('sound_candidates', 'sound_publications', 'sound_export_products'):
            for action in ('UPDATE', 'DELETE'):
                statements[f'{table}_{action.lower()}_immutable'] = f"BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Immutable sound receipt'); END"
            key = 'job_id' if table == 'sound_export_products' else 'id'
            extra = " OR (d.project_id=NEW.project_id AND d.request_key=NEW.request_key)" if table == 'sound_candidates' else " OR (d.draft_id=NEW.draft_id AND d.draft_revision=NEW.draft_revision)" if table == 'sound_publications' else ''
            statements[f'{table}_no_replace'] = f"BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} d WHERE d.{key}=NEW.{key}{extra}) BEGIN SELECT RAISE(ABORT,'Sound receipt replacement'); END"
        for name, sql in statements.items():
            expected = 'CREATE TRIGGER '+name+' '+sql
            existing = (await connection.execute(text("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=:name"), {'name': name})).scalar_one_or_none()
            if existing is None: await connection.execute(text(expected))
            elif ' '.join(existing.split()) != ' '.join(expected.split()): raise RuntimeError('Sound-v3 guard changed')
        await connection.execute(text('INSERT OR IGNORE INTO sound_export_slot(id,lease_until) VALUES(1,0)'))
        if (await connection.execute(text('PRAGMA foreign_key_check'))).first(): raise RuntimeError('Sound-v3 foreign key conflict')
