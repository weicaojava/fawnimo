"""Regression for independently reproduced cancellation windows; only disposable SQLite/tasks, no media or models."""
import asyncio
import threading

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.controlplane import demo
from app.controlplane import local_example as control
from app.controlplane.store import IdentityStore
from app.core.contracts.local_example import example_identity
from app.core.owned_tasks import join_tasks
from app.models.local_example import LocalExampleRequest
from app.services.studio import local_example as example
from tests.test_local_example import ACTOR, KEY, WORKSPACE, setup


@pytest.mark.asyncio
async def test_cancel_after_worker_claim_commit_releases_only_its_committed_owner(setup, monkeypatch):
    """Cancellation hiding a committed claim return still releases its witnessed owner after the real Session closes."""
    engine, factory, _service = setup
    committed, release = asyncio.Event(), asyncio.Event()

    class ClaimSession(AsyncSession):
        """Gate the actual durable SQLite commit before the Session is allowed to close."""
        async def commit(self):
            """A real inserted intent is committed before the caller receives repeated stop requests."""
            claimed = any(isinstance(row,LocalExampleRequest) for row in self.new)
            await super().commit()
            if claimed:
                committed.set()
                await release.wait()

    async def no_render(*_arguments):
        """A cancelled claim must not advance to even a local renderer."""
        raise AssertionError('Cancelled claim entered media preparation')

    service = example.LocalExampleService(async_sessionmaker(engine,class_=ClaimSession,expire_on_commit=False),WORKSPACE)
    monkeypatch.setattr(example,'prepare_media',no_render)
    task = asyncio.create_task(service.create(ACTOR,KEY))
    try:
        await asyncio.wait_for(committed.wait(),5)
        for _ in range(4):
            task.cancel(); await asyncio.sleep(0)
        assert not task.done() and service.lane.locked()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with factory() as db:
            row = await db.get(LocalExampleRequest,example_identity(WORKSPACE,ACTOR,KEY)['id'])
            assert row.state=='failedunknown' and row.lease_token is None and row.attempts==1
        # Recovery requires no60s wait; a new owned attempt can be claimed now.
        assert await service.claim(example_identity(WORKSPACE,ACTOR,KEY))
        assert not service.lane.locked()
    finally:
        release.set()
        await join_tasks(task,cancel=True)


@pytest.mark.asyncio
async def test_first_cancel_while_normal_preparation_finishes_never_enters_final_writer(setup, monkeypatch):
    """A first stop during heartbeat cleanup is propagated after joining and before creating any project."""
    _engine, factory, service = setup
    closing, release = asyncio.Event(), asyncio.Event()
    final_writes = []

    async def prepared(*_arguments):
        """No real media is needed to test the stage boundary immediately before the final writer."""
        return [],{},{}

    async def closing_heartbeat(_identifier, _token, stop):
        """Represent an owned heartbeat Session still closing after preparation has normally finished."""
        await stop.wait(); closing.set(); await release.wait()

    async def forbidden_finish(*_arguments):
        """Record any illegal transition to final installation after caller cancellation."""
        final_writes.append(True)
        raise AssertionError('Cancelled preparation entered final writer')

    monkeypatch.setattr(example,'prepare_media',prepared)
    monkeypatch.setattr(service,'heartbeat',closing_heartbeat)
    monkeypatch.setattr(service,'finish',forbidden_finish)
    task = asyncio.create_task(service.create(ACTOR,KEY))
    try:
        await asyncio.wait_for(closing.wait(),5)
        task.cancel(); await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not final_writes
        async with factory() as db:
            assert (await db.get(LocalExampleRequest,example_identity(WORKSPACE,ACTOR,KEY)['id'])).state=='failedunknown'
    finally:
        release.set()
        await join_tasks(task,cancel=True)


@pytest.mark.asyncio
async def test_cancelled_committed_control_claim_is_recoverable_without_worker_start(tmp_path, monkeypatch):
    """The control thread's post-close witness releases only its own token even when its return is cancelled."""
    store = IdentityStore(tmp_path/'control')
    store.provision('cancellation.qa','cancellation-disposable-fixture-password')
    current = store.current(store.login('cancellation.qa','cancellation-disposable-fixture-password','fixture'))
    committed, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    original_claim = demo._claim

    def gated_claim(*arguments, **options):
        """Return only after a real committed-and-closed control claim and the test's release gate."""
        row = original_claim(*arguments,**options)
        loop.call_soon_threadsafe(committed.set)
        assert release.wait(8)
        return row

    class NoPool:
        """No runtime may start after cancellation in the control claim return window."""
        async def get(self, _workspace):
            """Trap any progression past the cancelled claim."""
            raise AssertionError('Cancelled control claim started a worker')

    monkeypatch.setattr(demo,'_claim',gated_claim)
    task = asyncio.create_task(control.create_exportable_demo(store,NoPool(),current,KEY))
    try:
        await asyncio.wait_for(committed.wait(),5)
        for _ in range(4):
            task.cancel(); await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        with store.connect() as db:
            row = dict(db.execute('SELECT * FROM demo_requests WHERE request_key=?',(KEY,)).fetchone())
            assert row['state']=='failedunknown' and row['lease_token'] is None
        identity = example_identity(current['workspace_id'],current['user']['id'],KEY)
        recovered = original_claim(store,current['workspace_id'],current['user']['id'],KEY,identity['request_hash'],identity['project_id'],template_version=identity['template_version'])
        assert recovered['state']=='pending' and recovered['lease_token']
        demo._uncertain(store,recovered)
    finally:
        release.set()
        await join_tasks(task,cancel=True)


@pytest.mark.asyncio
async def test_intentionally_cancelled_child_does_not_cancel_its_owner():
    """Normal cleanup consumes a child's cancellation outcome rather than manufacturing a caller cancellation."""
    child = asyncio.create_task(asyncio.sleep(60))
    results = await join_tasks(child,cancel=True)
    assert child.cancelled() and len(results)==1 and isinstance(results[0],asyncio.CancelledError)


@pytest.mark.asyncio
async def test_repeated_owner_cancellation_joins_child_cleanup_then_propagates():
    """An actual pending child finally block remains owned through repeated caller stop requests."""
    started, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def child_operation():
        """Hold a real asynchronous cleanup instead of pretending an already-cancelled child has closed."""
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            closing.set()
            await release.wait()

    child = asyncio.create_task(child_operation())
    await started.wait()
    owner = asyncio.create_task(join_tasks(child,cancel=True))
    try:
        await asyncio.wait_for(closing.wait(),5)
        for _ in range(4):
            owner.cancel(); await asyncio.sleep(0)
        assert not owner.done() and not child.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await owner
        assert child.done()
    finally:
        release.set()
        await join_tasks(owner,child,cancel=True)
