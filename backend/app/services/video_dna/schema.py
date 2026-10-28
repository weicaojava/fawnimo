"""Additive SQLite DNA scope and immutable history protections."""
from sqlalchemy import text
from app.core.db import Base
from app.models.video_dna import DnaDraft,DnaSnapshot


async def migrate_video_dna(target_engine):
    """Install only additive tables and fail closed on unknown layouts or damaged references."""
    if target_engine.dialect.name!='sqlite':raise RuntimeError('Video DNA is qualified only for SQLite')
    async with target_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text('CREATE TABLE IF NOT EXISTS video_dna_schema(version INTEGER NOT NULL)'))
        versions=list((await conn.execute(text('SELECT version FROM video_dna_schema'))).scalars())
        if not versions:await conn.execute(text('INSERT INTO video_dna_schema VALUES(1)'))
        elif versions!=[1]:raise RuntimeError('Unknown Video DNA schema version')
        for model in (DnaDraft,DnaSnapshot):
            columns={r[1] for r in (await conn.execute(text(f'PRAGMA table_info({model.__tablename__})'))).all()}
            if columns!={c.name for c in model.__table__.columns}:raise RuntimeError('Unknown Video DNA table layout')
        for action in ('UPDATE','DELETE'):
            await conn.execute(text(f"CREATE TRIGGER IF NOT EXISTS video_dna_snapshots_{action.lower()}_immutable BEFORE {action} ON video_dna_snapshots BEGIN SELECT RAISE(ABORT,'Immutable DNA snapshot'); END"))
        await conn.execute(text("CREATE TRIGGER IF NOT EXISTS video_dna_draft_identity BEFORE UPDATE ON video_dna_drafts WHEN NEW.id!=OLD.id OR NEW.project_id!=OLD.project_id OR NEW.analysis_id!=OLD.analysis_id OR NEW.source IS NOT OLD.source OR NEW.request_key!=OLD.request_key OR NEW.initial_hash!=OLD.initial_hash BEGIN SELECT RAISE(ABORT,'Frozen DNA source identity'); END"))
        await conn.execute(text("CREATE TRIGGER IF NOT EXISTS video_dna_draft_no_delete BEFORE DELETE ON video_dna_drafts BEGIN SELECT RAISE(ABORT,'DNA history retained'); END"))
        await conn.execute(text("CREATE TRIGGER IF NOT EXISTS video_dna_draft_source_scope BEFORE INSERT ON video_dna_drafts WHEN json_extract(NEW.source,'$.analysis_id') IS NOT NEW.analysis_id OR NOT EXISTS(SELECT 1 FROM reverse_analyses a WHERE a.id=NEW.analysis_id AND a.project_id=NEW.project_id AND a.media_id=json_extract(NEW.source,'$.media_id') AND a.source_hash=json_extract(NEW.source,'$.sha256')) BEGIN SELECT RAISE(ABORT,'DNA source scope mismatch'); END"))
        await conn.execute(text("CREATE TRIGGER IF NOT EXISTS video_dna_snapshot_source_scope BEFORE INSERT ON video_dna_snapshots WHEN NOT EXISTS(SELECT 1 FROM video_dna_drafts d WHERE d.id=NEW.draft_id AND d.project_id=NEW.project_id AND json(d.source)=json(json_extract(NEW.payload,'$.source'))) BEGIN SELECT RAISE(ABORT,'DNA snapshot source mismatch'); END"))
        if (await conn.execute(text('PRAGMA foreign_key_check'))).first():raise RuntimeError('Video DNA migration foreign key check failed')
