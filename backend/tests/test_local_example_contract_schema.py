"""Exercise real SQLite reservation and attempt fences; successful installation is tested separately."""
import asyncio
import time

from pydantic import ValidationError
import pytest
import pytest_asyncio
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.db import Base
from app.core.contracts.local_example import LocalExampleInput
from app.models.local_example import LocalExampleRequest
from app.models.studio import Project, Chapter


@pytest_asyncio.fixture
async def database(tmp_path):
    """Create a disposable schema with actual foreign keys, independent of configured application data."""
    engine = create_async_engine('sqlite+aiosqlite:///' + (tmp_path / 'example.db').as_posix())

    @event.listens_for(engine.sync_engine, 'connect')
    def constraints(connection, _):
        """Use real SQLite foreign-key and context guards for every fixture connection."""
        connection.execute('PRAGMA foreign_keys=ON')

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add(Project(id='existing', name='preserve', style='test'))
        await db.flush()
        db.add(Chapter(id='existing-chapter', project_id='existing', index=1, title='preserve'))
        await db.commit()
    try:
        yield factory
    finally:
        await engine.dispose()


def intent(**changes):
    """Reserve absent fixture IDs; no project is visible merely because a request exists."""
    return {**dict(id='example', project_id='new', chapter_id='new-chapter', submitted_by='fixture_actor',
        request_key='example-request', request_hash='a'*64, template_version='original-light-island-geometry-v1',
        state='running', attempts=1, lease_token='c'*64, lease_until=int(time.time())+120,
        reason='', created_at=1, updated_at=1), **changes}


@pytest.mark.parametrize('extra', ['project_id', 'chapter_id', 'ffmpeg', 'output', 'duration', 'model_quality_qualified'])
def test_request_cannot_select_tools_paths_target_or_qualification(extra):
    """Public and private actions share the same closed key-only input."""
    with pytest.raises(ValidationError):
        LocalExampleInput.model_validate({'request_key': 'example-request', extra: 'fixture'})


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [
    {'state':'ready','lease_token':None,'lease_until':None},
    {'state':'failedunknown','lease_token':None,'lease_until':None},
    {'attempts':2}, {'attempts':1.5}, {'lease_token':None}, {'lease_until':1.5},
    {'project_id':'existing'}, {'chapter_id':'existing-chapter'},
])
async def test_initial_request_requires_new_target_and_first_owned_integer_attempt(database, change):
    """No completed, fractional, ownerless or existing-target request can be inserted, including direct SQL affinity."""
    async with database() as db:
        db.add(LocalExampleRequest(**intent(**change)))
        with pytest.raises(IntegrityError):
            await db.commit()


@pytest.mark.asyncio
async def test_reservation_does_not_create_a_project_and_cannot_change_identity(database):
    """A live reservation protects its original actor/key and cannot be stolen by another renderer."""
    async with database() as db:
        db.add(LocalExampleRequest(**intent())); await db.commit()
        assert await db.get(Project, 'new') is None
        for update in ("submitted_by='other'", "attempts=2,lease_token='"+'d'*64+"'",
                       "attempts=1.5", "lease_token=NULL", "state='ready',lease_token=NULL,lease_until=NULL"):
            with pytest.raises(IntegrityError):
                await db.execute(text('UPDATE local_example_requests SET '+update))
            await db.rollback()
        assert (await db.get(Project, 'existing')).name == 'preserve'


@pytest.mark.asyncio
async def test_only_bounded_retry_can_leave_unknown_and_ready_cannot_be_fabricated(database):
    """An unknown outcome requires a new owner and next integer attempt, never a direct Ready transition."""
    async with database() as db:
        db.add(LocalExampleRequest(**intent())); await db.commit()
        for attempt in (2, 3):
            await db.execute(text("UPDATE local_example_requests SET state='failedunknown',lease_token=NULL,lease_until=NULL")); await db.commit()
            for update in ("state='ready'", "state='running',attempts=1.5,lease_token='"+'d'*64+"',lease_until=:until"):
                with pytest.raises(IntegrityError):
                    await db.execute(text('UPDATE local_example_requests SET '+update), {'until':int(time.time())+120})
                await db.rollback()
            await db.execute(text("UPDATE local_example_requests SET state='running',attempts=:attempt,lease_token=:token,lease_until=:until"),
                {'attempt':attempt,'token':str(attempt)*64,'until':int(time.time())+120}); await db.commit()
        await db.execute(text("UPDATE local_example_requests SET state='failedunknown',lease_token=NULL,lease_until=NULL")); await db.commit()
        with pytest.raises(IntegrityError):
            await db.execute(text("UPDATE local_example_requests SET state='running',attempts=4,lease_token=:token,lease_until=:until"),
                {'token':'e'*64,'until':int(time.time())+120})


@pytest.mark.asyncio
async def test_expired_owner_cannot_heartbeat_or_finish_and_new_attempt_is_fenced(database):
    """Real SQLite wall time expires the first owner; reclaim cannot preserve its old attempt or token."""
    async with database() as db:
        db.add(LocalExampleRequest(**intent(lease_until=int(time.time())+1))); await db.commit()
    await asyncio.sleep(1.2)
    async with database() as db:
        for update in ("lease_until=:until", "state='ready',lease_token=NULL,lease_until=NULL"):
            with pytest.raises(IntegrityError):
                await db.execute(text('UPDATE local_example_requests SET '+update), {'until':int(time.time())+120})
            await db.rollback()
        await db.execute(text("UPDATE local_example_requests SET attempts=2,lease_token=:token,lease_until=:until"),
            {'token':'d'*64,'until':int(time.time())+120}); await db.commit()
        assert (await db.get(LocalExampleRequest,'example')).attempts == 2


@pytest.mark.asyncio
async def test_intent_cannot_be_deleted_or_replaced(database):
    """SQLite INSERT OR REPLACE cannot bypass retained identity even when recursive triggers are off."""
    async with database() as db:
        db.add(LocalExampleRequest(**intent())); await db.commit()
        for sql in ('DELETE FROM local_example_requests', 'INSERT OR REPLACE INTO local_example_requests SELECT * FROM local_example_requests'):
            with pytest.raises(IntegrityError):
                await db.execute(text(sql))
            await db.rollback()
        assert (await db.execute(select(LocalExampleRequest.id))).scalars().all() == ['example']
