"""Additive SQLite schema and retained evidence identities for model-understanding journals."""
from sqlalchemy import text
from app.core.db import Base
from app.models.video_understanding import UnderstandingAttempt, UnderstandingConfig, UnderstandingJob


async def migrate_video_understanding(target_engine) -> None:
    """Create additive tables and reject unsupported layouts without rewriting historical records."""
    if target_engine.dialect.name != 'sqlite':
        raise RuntimeError('Video understanding is qualified only for SQLite')
    async with target_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(text('CREATE TABLE IF NOT EXISTS video_understanding_schema(version INTEGER NOT NULL)'))
        versions = (await connection.execute(text('SELECT version FROM video_understanding_schema'))).scalars().all()
        if not versions:
            await connection.execute(text('INSERT INTO video_understanding_schema VALUES(1)'))
        elif versions != [1]:
            raise RuntimeError('Unsupported video-understanding schema version')
        for model in (UnderstandingConfig, UnderstandingJob, UnderstandingAttempt):
            columns = {row[1] for row in (await connection.execute(text(f'PRAGMA table_info({model.__tablename__})'))).all()}
            if columns != {column.name for column in model.__table__.columns}:
                raise RuntimeError('Unsupported video-understanding table layout')
        await connection.execute(text("CREATE TRIGGER IF NOT EXISTS understanding_job_identity BEFORE UPDATE ON video_understanding_jobs WHEN NEW.id IS NOT OLD.id OR NEW.project_id IS NOT OLD.project_id OR NEW.analysis_id IS NOT OLD.analysis_id OR NEW.request_key IS NOT OLD.request_key OR NEW.request_hash IS NOT OLD.request_hash OR NEW.source IS NOT OLD.source OR NEW.connection IS NOT OLD.connection BEGIN SELECT RAISE(ABORT,'Frozen understanding request'); END"))
        await connection.execute(text("CREATE TRIGGER IF NOT EXISTS understanding_candidate_identity BEFORE UPDATE ON video_understanding_jobs WHEN OLD.document IS NOT NULL AND (NEW.document IS NOT OLD.document OR NEW.transcripts IS NOT OLD.transcripts OR NEW.warnings IS NOT OLD.warnings) BEGIN SELECT RAISE(ABORT,'Immutable understanding candidate'); END"))
        await connection.execute(text("CREATE TRIGGER IF NOT EXISTS understanding_attempt_identity BEFORE UPDATE ON video_understanding_attempts WHEN NEW.id IS NOT OLD.id OR NEW.job_id IS NOT OLD.job_id OR NEW.project_id IS NOT OLD.project_id OR NEW.attempt IS NOT OLD.attempt BEGIN SELECT RAISE(ABORT,'Frozen understanding attempt identity'); END"))
        await connection.execute(text("CREATE TRIGGER IF NOT EXISTS understanding_attempt_finished BEFORE UPDATE ON video_understanding_attempts WHEN OLD.finished_at IS NOT NULL BEGIN SELECT RAISE(ABORT,'Finished understanding attempt retained'); END"))
        for name in ('video_understanding_jobs', 'video_understanding_attempts'):
            await connection.execute(text(f"CREATE TRIGGER IF NOT EXISTS {name}_retained BEFORE DELETE ON {name} BEGIN SELECT RAISE(ABORT,'Understanding journal retained'); END"))
        if (await connection.execute(text('PRAGMA foreign_key_check'))).first():
            raise RuntimeError('Video understanding foreign key check failed')
