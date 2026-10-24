"""File-backed SQLite scanner recovery, competing worker CAS and stop semantics."""
import asyncio
import pytest
from sqlalchemy import update
from app.models.execution import ExecutionJob
from app.services.worker.execution_dispatcher import ExecutionDispatcher
from tests.test_durable_execution import setup, read


@pytest.mark.asyncio
async def test_old_binding_prefix_does_not_starve_later_eligible_job(tmp_path):
    """A full batch of retired-account jobs must not hide a due current-account job."""
    from dataclasses import replace
    from app.core.contracts.execution_capabilities import Capability
    from app.core.contracts.execution_binding import binding_fingerprint
    from app.services.worker.execution_admission import AdmissionPolicy,admit
    from app.services.worker.execution_budget import configure
    from app.services.worker.durable_execution import DurableExecution
    from tests.test_durable_execution import fixture_publisher
    engine,factory,adapter,clock,_,_=await setup(tmp_path)
    try:
        async with factory() as db:await configure(db,'p','TEST',100)
        old=AdmissionPolicy(Capability('fixture',frozenset({5}),frozenset(),0),1,'TEST',20,binding_id='retired-account')
        current=replace(old,binding_id='current-account')
        snapshot={'model_id':'fixture','duration':5,'prompt':'test fixture'}
        old_ids=[await admit(factory,'p','retired-'+str(i),snapshot,old) for i in range(4)]
        eligible=await admit(factory,'p','current',snapshot,current)
        async with factory() as db:
            # Every retired job sorts before the current job regardless of UUID.
            await db.execute(update(ExecutionJob).where(ExecutionJob.id==eligible).values(next_due=1))
            await db.commit()
        worker=DurableExecution(adapter,factory=factory,clock=clock,publisher=fixture_publisher)
        worker.allowed_bindings=frozenset({binding_fingerprint(current)})
        dispatcher=ExecutionDispatcher(worker,factory=factory,clock=clock,batch_size=2)
        assert await dispatcher.tick()==1
        assert adapter.sent==1 and (await read(factory,eligible)).state=='running'
        clock.advance()
        assert await dispatcher.tick()==1
        assert (await read(factory,eligible)).state=='downloading'
        assert await dispatcher.tick()==1
        assert (await read(factory,eligible)).state=='ready'
        for identity in old_ids:
            assert (await read(factory,identity)).state=='queued'
        assert adapter.sent==1
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_reserved_owner_is_not_starved_by_queued_batch(tmp_path):
    """A reserved owner's next poll stays reachable beyond the queued batch limit."""
    engine,factory,adapter,clock,worker,_=await setup(tmp_path)
    try:
        jobs=[await worker.create('p','backlog-'+str(i),{}) for i in range(5)]
        owner=max(jobs)
        await worker.step(owner)
        clock.advance()
        dispatcher=ExecutionDispatcher(worker,factory=factory,clock=clock,batch_size=1)
        assert await dispatcher.tick()==1
        assert (await read(factory,owner)).state=='downloading'
        assert await dispatcher.tick()==1
        assert (await read(factory,owner)).state=='ready' and adapter.sent==1
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_restart_finds_committed_unpublished_job(tmp_path):
    """A fresh dispatcher finds a committed job without any enqueue notification."""
    engine, factory, adapter, clock, worker, _ = await setup(tmp_path)
    try:
        job = await worker.create('p', 'committed-only', {})
        previous = ExecutionDispatcher(worker, factory=factory, clock=clock)
        await previous.stop()
        replacement = ExecutionDispatcher(worker, factory=factory, clock=clock)
        assert adapter.sent == 0
        assert await replacement.tick() == 1
        assert adapter.sent == 1 and (await read(factory, job)).state == 'running'
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_two_dispatchers_only_one_submission(tmp_path):
    """Independent scanners race the same durable job but worker CAS admits one send."""
    engine, factory, adapter, clock, worker, _ = await setup(tmp_path)
    try:
        await worker.create('p', 'competing', {})
        a = ExecutionDispatcher(worker, factory=factory, clock=clock)
        b = ExecutionDispatcher(worker, factory=factory, clock=clock)
        await asyncio.gather(a.tick(), b.tick())
        assert adapter.sent == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_job_error_isolation_and_scan_bounds(tmp_path):
    """One bad job cannot block another; future, leased, paused and terminal stay out."""
    engine, factory, _, clock, worker, _ = await setup(tmp_path)
    seen = []
    class Observer:
        """Record due steps while deliberately failing one without external effects."""
        async def step(self, job):
            """Fail the first due ID while allowing peers to finish."""
            seen.append(job)
            if job == due[0]:
                raise RuntimeError('fixture job failure')
    try:
        due = [await worker.create('p', str(i), {}) for i in range(3)]
        for i, values in enumerate([{'state':'paused'}, {'state':'ready'}, {'next_due':clock()+100}, {'lease_until':clock()+100}]):
            job = await worker.create('p', 'excluded'+str(i), {})
            async with factory() as db:
                await db.execute(update(ExecutionJob).where(ExecutionJob.id==job).values(**values))
                await db.commit()
        dispatcher = ExecutionDispatcher(Observer(), factory=factory, clock=clock, batch_size=2)
        assert await dispatcher.tick() == 2
        assert len(seen) == 2 and set(seen).issubset(due)
        # Force the failing ID to be scanned with its peers regardless of UUID ordering.
        dispatcher.batch_size = 3
        await dispatcher.tick()
        assert set(seen) == set(due)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stop_cancels_and_joins_inflight(tmp_path):
    """Lifecycle stop waits until the worker's cancellation cleanup has finished."""
    engine, factory, _, clock, worker, _ = await setup(tmp_path)
    entered, cleaned = asyncio.Event(), asyncio.Event()
    class Blocking:
        """An in-flight fixture operation with observable asynchronous cleanup."""
        async def step(self, _job):
            """Block until cancellation then join cleanup before reporting stopped."""
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned.set()
    dispatcher = ExecutionDispatcher(Blocking(), factory=factory, clock=clock, interval_seconds=.01)
    try:
        await worker.create('p', 'stop', {})
        task = asyncio.create_task(dispatcher.run())
        assert not task.done()
        await asyncio.wait_for(entered.wait(), 2)
        await dispatcher.stop()
        assert cleaned.is_set() and task.done() and not dispatcher._inflight
        await dispatcher.stop()
    finally:
        await dispatcher.stop()
        await engine.dispose()

