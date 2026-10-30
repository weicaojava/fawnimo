"""Opt-in database scanner; no import-time scheduling or transient queue dependency."""
import asyncio
import logging
import time
from sqlalchemy import select
from app.models.execution import ExecutionJob, ExecutionSlot
from app.models.execution_binding import ExecutionBinding
from app.models.execution_budget import ExecutionBudgetEntry
from app.services.worker.execution_accounting import recover_unaccepted_holds

logger = logging.getLogger(__name__)


class ExecutionDispatcher:
    """Discover committed jobs after restarts; worker CAS owns all execution claims."""

    def __init__(self, worker, *, factory, clock=time.time, batch_size=20, interval_seconds=1.0):
        """Inject trusted worker/session factory; bound each scan and avoid busy loops."""
        if type(batch_size) is not int or not 1 <= batch_size <= 100:
            raise ValueError('batch_size must be an integer between 1 and 100')
        if not 0.01 <= interval_seconds <= 3600:
            raise ValueError('interval must be between 0.01 and 3600 seconds')
        self.worker, self.factory, self.clock = worker, factory, clock
        self.batch_size, self.interval = batch_size, interval_seconds
        self._runner = None
        self._inflight = set()

    async def tick(self):
        """Read a bounded due batch and await isolated steps; never claim outside worker."""
        now = self.clock()
        await recover_unaccepted_holds(factory=self.factory,limit=self.batch_size)
        async with self.factory() as db:
            statement=select(ExecutionJob.id).where(
                ExecutionJob.state.not_in(['ready', 'failed', 'cancelled', 'paused']),
                ExecutionJob.next_due <= now, ExecutionJob.lease_until <= now,
            )
            slot=await db.get(ExecutionSlot,1)
            # The reserved owner may sort after many queued jobs. Those jobs
            # cannot claim the slot and must not starve its next poll forever.
            if slot is not None and slot.job_id is not None:
                statement=statement.where(ExecutionJob.id==slot.job_id)
            elif getattr(self.worker,'budget_required',False):
                # Ineligible old-account jobs and missing spending holds must
                # not monopolize every bounded batch ahead of eligible work.
                if getattr(self.worker,'native_accounting',None) is not None:
                    from app.models.native_video_charge import NativeVideoJobCharge
                    statement=statement.where(ExecutionJob.id.in_(select(NativeVideoJobCharge.job_id)))
                    if not self.worker.native_accounting.allow_new_post:statement=statement.where(ExecutionJob.state!='queued')
                elif getattr(self.worker,'enforce_binding',False):
                    bound=select(ExecutionBinding.job_id).where(ExecutionBinding.binding_hash.in_(self.worker.allowed_bindings))
                    statement=statement.where(ExecutionJob.id.in_(bound))
                reserved=select(ExecutionBudgetEntry.job_id).where(ExecutionBudgetEntry.state=='reserved')
                statement=statement.where((ExecutionJob.state!='queued') | ExecutionJob.id.in_(reserved))
            ids=list((await db.execute(statement.order_by(ExecutionJob.next_due,ExecutionJob.id).limit(self.batch_size))).scalars())
        tasks = [asyncio.create_task(self._step(job_id)) for job_id in ids]
        self._inflight.update(tasks)
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._inflight.difference_update(tasks)
        return len(ids)

    async def _step(self, job_id):
        """Log one job failure while allowing unrelated batch members to progress."""
        try:
            await self.worker.step(job_id)
        except Exception as exc:
            logger.error('Execution dispatcher step failed for job %s (%s)',job_id,type(exc).__name__)

    async def run(self):
        """Retry database scans after transient failures until explicit lifecycle stop."""
        current = asyncio.current_task()
        if self._runner is not None and not self._runner.done():
            raise RuntimeError('Dispatcher already running')
        self._runner = current
        try:
            while True:
                try:
                    await self.tick()
                except Exception as exc:
                    logger.error('Execution dispatcher scan failed (%s)',type(exc).__name__)
                await asyncio.sleep(self.interval)
        finally:
            if self._runner is current:
                self._runner = None

    async def stop(self):
        """Cancel and join scanner and all tracked steps, including transport cleanup."""
        runner = self._runner
        if runner is not None:
            runner.cancel()
        tasks = list(self._inflight)
        # Cancelling the parent gather already cancels children; do not inject a
        # second cancellation while their asynchronous cleanup is in progress.
        if runner is None:
            for task in tasks:
                task.cancel()
        await asyncio.gather(*([runner] if runner is not None else []), *tasks, return_exceptions=True)
        self._inflight.difference_update(tasks)
        self._runner = None
