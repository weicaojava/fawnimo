"""Additive SQLite editing schema; historical production and plans remain intact."""
from sqlalchemy import text
from app.core.db import Base
from app.models.editing import TakeOrigin,SelectionCursor,SelectionEvent,EditDraft,EditPublication


async def migrate_editing(target_engine):
    """Create editing tables and fail closed on unknown versions or broken ownership."""
    if target_engine.dialect.name!='sqlite':raise RuntimeError('Editing schema is qualified only for SQLite')
    async with target_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text('CREATE TABLE IF NOT EXISTS editing_schema(version INTEGER NOT NULL)'))
        versions=list((await conn.execute(text('SELECT version FROM editing_schema'))).scalars())
        if not versions:await conn.execute(text('INSERT INTO editing_schema VALUES(1)'))
        elif versions!=[1]:raise RuntimeError('Unknown editing schema version')
        for model in (TakeOrigin,SelectionCursor,SelectionEvent,EditDraft,EditPublication):
            columns={r[1] for r in (await conn.execute(text(f'PRAGMA table_info({model.__tablename__})'))).all()}
            if columns!={c.name for c in model.__table__.columns}:raise RuntimeError('Unknown editing table layout')
        for table in ('editing_take_origins','editing_selection_events','editing_publications'):
            for action in ('UPDATE','DELETE'):
                await conn.execute(text(f"CREATE TRIGGER IF NOT EXISTS {table}_{action.lower()}_immutable BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Immutable editing record'); END"))
        await conn.execute(text("CREATE TRIGGER IF NOT EXISTS editing_draft_identity BEFORE UPDATE ON editing_drafts WHEN NEW.id!=OLD.id OR NEW.project_id!=OLD.project_id OR NEW.chapter_id!=OLD.chapter_id BEGIN SELECT RAISE(ABORT,'Frozen editing identity'); END"))
        await conn.execute(text("CREATE TRIGGER IF NOT EXISTS editing_draft_no_delete BEFORE DELETE ON editing_drafts BEGIN SELECT RAISE(ABORT,'Editing history retained'); END"))
        conditions={
            'editing_take_origins':"NOT EXISTS(SELECT 1 FROM production_takes t JOIN shot_plans p ON p.id=NEW.plan_id WHERE t.id=NEW.take_id AND t.project_id=NEW.project_id AND t.shot_id=NEW.shot_id AND p.project_id=NEW.project_id AND EXISTS(SELECT 1 FROM json_each(p.payload,'$.entries') e WHERE json_extract(e.value,'$.shot_id')=NEW.shot_id AND json_extract(e.value,'$.entry_hash')=NEW.entry_hash)) OR (NEW.job_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM execution_jobs j JOIN shot_plan_job_links l ON l.job_id=j.id JOIN production_takes t ON t.id=NEW.take_id WHERE j.id=NEW.job_id AND j.project_id=NEW.project_id AND j.media_id=t.media_id AND l.plan_id=NEW.plan_id AND l.shot_id=NEW.shot_id AND l.entry_hash=NEW.entry_hash))",
            'editing_selection_cursors':"NEW.revision<0 OR NOT EXISTS(SELECT 1 FROM shots s JOIN chapters c ON c.id=s.chapter_id WHERE s.id=NEW.shot_id AND c.project_id=NEW.project_id)",
            'editing_selection_events':"NEW.revision<1 OR NOT EXISTS(SELECT 1 FROM production_takes WHERE id=NEW.take_id AND shot_id=NEW.shot_id AND project_id=NEW.project_id)",
            'editing_publications':"NOT EXISTS(SELECT 1 FROM editing_drafts d WHERE d.id=NEW.draft_id AND d.project_id=NEW.project_id AND d.chapter_id=NEW.chapter_id) OR json_extract(NEW.payload,'$.project_id') IS NOT NEW.project_id OR json_extract(NEW.payload,'$.chapter_id') IS NOT NEW.chapter_id",
        }
        for table,condition in conditions.items():
            for action in ('INSERT','UPDATE'):
                await conn.execute(text(f"CREATE TRIGGER IF NOT EXISTS {table}_scope_{action.lower()} BEFORE {action} ON {table} WHEN {condition} BEGIN SELECT RAISE(ABORT,'Editing scope mismatch'); END"))
        await conn.execute(text("CREATE TRIGGER IF NOT EXISTS editing_cursor_identity BEFORE UPDATE ON editing_selection_cursors WHEN NEW.shot_id!=OLD.shot_id OR NEW.project_id!=OLD.project_id OR NEW.revision!=OLD.revision+1 BEGIN SELECT RAISE(ABORT,'Editing cursor CAS'); END"))
        await conn.execute(text("CREATE TRIGGER IF NOT EXISTS editing_cursor_no_delete BEFORE DELETE ON editing_selection_cursors BEGIN SELECT RAISE(ABORT,'Editing cursor retained'); END"))
        if (await conn.execute(text('PRAGMA foreign_key_check'))).first():raise RuntimeError('Editing migration foreign key check failed')
