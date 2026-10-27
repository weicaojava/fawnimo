"""Additive scoped plan journals, safe from all supported metadata entrypoints."""
from sqlalchemy import text
from app.core.db import Base,engine
import app.models.shot_plan  # noqa: F401


async def migrate_shot_plan(target_engine=engine):
    """Install immutable plan/link guards without altering existing jobs or evidence."""
    if target_engine.dialect.name!='sqlite':raise RuntimeError('Shot plans require SQLite')
    async with target_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(text('CREATE TABLE IF NOT EXISTS shot_plan_schema(version INTEGER NOT NULL)'))
        versions=(await connection.execute(text('SELECT version FROM shot_plan_schema'))).scalars().all()
        if versions not in ([],[1]):raise RuntimeError('Unsupported shot-plan schema')
        for table in ['shot_plan_drafts','shot_plans','shot_plan_job_links']:
            actual={r[1] for r in (await connection.execute(text(f'PRAGMA table_info({table})'))).all()}
            if actual!={c.name for c in Base.metadata.tables[table].columns}:raise RuntimeError('Unsupported shot-plan columns')
        for table in ['shot_plans','shot_plan_job_links']:
            for operation in ['UPDATE','DELETE']:
                await connection.execute(text(f"CREATE TRIGGER IF NOT EXISTS {table}_{operation.lower()}_immutable BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'Plan journal immutable'); END"))
        condition=' OR '.join(f'OLD.{key} IS NOT NEW.{key}' for key in ['id','project_id','chapter_id'])
        await connection.execute(text(f"CREATE TRIGGER IF NOT EXISTS shot_plan_draft_identity BEFORE UPDATE ON shot_plan_drafts WHEN {condition} BEGIN SELECT RAISE(ABORT,'Plan draft identity immutable'); END"))
        await connection.execute(text("CREATE TRIGGER IF NOT EXISTS shot_plan_draft_no_delete BEFORE DELETE ON shot_plan_drafts BEGIN SELECT RAISE(ABORT,'Plan draft cannot be deleted'); END"))
        condition="NOT EXISTS(SELECT 1 FROM shot_plans p JOIN shots s ON s.id=NEW.shot_id JOIN chapters c ON c.id=s.chapter_id JOIN execution_jobs j ON j.id=NEW.job_id,json_each(p.payload,'$.entries') e WHERE p.id=NEW.plan_id AND p.project_id=NEW.project_id AND c.project_id=NEW.project_id AND s.chapter_id=json_extract(p.payload,'$.chapter_id') AND j.project_id=NEW.project_id AND json_extract(e.value,'$.shot_id')=NEW.shot_id AND json_extract(e.value,'$.entry_hash')=NEW.entry_hash AND json_extract(j.snapshot,'$.prompt_revision')=NEW.entry_hash)"
        await connection.execute(text(f"CREATE TRIGGER IF NOT EXISTS shot_plan_link_scope BEFORE INSERT ON shot_plan_job_links WHEN {condition} BEGIN SELECT RAISE(ABORT,'Plan job scope mismatch'); END"))
        await connection.execute(text("CREATE TRIGGER IF NOT EXISTS shot_plan_payload_scope BEFORE INSERT ON shot_plans WHEN json_extract(NEW.payload,'$.project_id') IS NOT NEW.project_id OR NOT EXISTS(SELECT 1 FROM shot_plan_drafts d WHERE d.id=NEW.draft_id AND d.project_id=NEW.project_id AND d.chapter_id=json_extract(NEW.payload,'$.chapter_id')) BEGIN SELECT RAISE(ABORT,'Plan payload scope mismatch'); END"))
        if not versions:await connection.execute(text('INSERT INTO shot_plan_schema VALUES(1)'))
        if (await connection.execute(text('PRAGMA foreign_key_check'))).first():raise RuntimeError('Shot-plan reference conflict')
