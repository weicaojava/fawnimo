"""Migration tests use a new isolated database; no user data is upgraded here."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from app.services.worker.execution_schema import migrate_execution
from tests.test_durable_execution import setup


@pytest.mark.asyncio
async def test_additive_migration_repeat_and_frozen_identity(tmp_path):
    """Repeated migration preserves requests and SQL cannot rewrite frozen inputs."""
    engine,factory,adapter,clock,worker,_=await setup(tmp_path)
    try:
        job_id=await worker.create('p','migration',{'prompt_revision':1})
        await migrate_execution(engine);await migrate_execution(engine)
        async with engine.begin() as connection:
            assert (await connection.execute(text('SELECT version FROM execution_schema'))).scalar_one()==1
        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(text("UPDATE execution_jobs SET snapshot='{}' WHERE id=:id"),{'id':job_id})
        async with engine.begin() as connection:
            assert (await connection.execute(text('SELECT count(*) FROM execution_jobs'))).scalar_one()==1
            await connection.execute(text('ALTER TABLE execution_slots ADD COLUMN incompatible TEXT'))
        with pytest.raises(RuntimeError,match='Unsupported columns'):await migrate_execution(engine)
    finally:await engine.dispose()
