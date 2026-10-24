"""Resumable provider orchestration. Transports are trusted adapters, never imported code."""
import hashlib
import asyncio
import json
import time
import uuid
from typing import Protocol
from fastapi import HTTPException
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from app.core.contracts.task_safety import validate_task_payload
from app.core.db import async_session_maker
from app.models.execution import ExecutionJob, ExecutionSlot, SubmissionAttempt
from app.models.task import GenerationTask
from app.services.worker.execution_artifacts import DownloadedArtifact, publish_artifact
from app.models.execution_budget import ExecutionBudgetEntry
from app.models.execution_binding import ExecutionBinding

TERMINAL={'ready','failed','cancelled'}


class RejectedSubmission(Exception):
    """Only a contractually definite non-acceptance may be classified as rejected."""


class LeaseLost(Exception):
    """An old owner must stop rather than perform another external side effect."""


class ExecutionTransport(Protocol):
    """An adapter receives frozen data and request identity, not endpoint overrides."""
    async def submit(self, snapshot: dict, request_id: str) -> str: ...
    async def reconcile(self, request_id: str) -> str | None: ...
    async def query(self, remote_id: str) -> dict: ...
    async def download(self, result: dict, media_id: str, project_id: str) -> DownloadedArtifact: ...
    async def cancel(self, remote_id: str) -> bool: ...


def digest(value: dict) -> str:
    """Hash the canonical snapshot; neither secrets nor imported endpoints may be present."""
    validate_task_payload(value)
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


class DurableExecution:
    """Database-coordinated one-step worker with fenced leases and no submission retry.

    Adapter qualification, network trust and real-provider capability validation
    remain separate requirements; a passing state-machine test is not live access.
    """
    def __init__(self, transport: ExecutionTransport, *, factory=async_session_maker, clock=time.time, lease_seconds=90, poll_seconds=5, wait_seconds=1800, retry_limit=3, publisher=publish_artifact, budget_required=True, enforce_binding=True):
        if lease_seconds<90:raise ValueError('Lease must outlive the 60-second hard transport deadline with margin')
        self.transport=transport;self.factory=factory;self.clock=clock
        self.lease_seconds=lease_seconds;self.poll_seconds=max(5,poll_seconds)
        self.wait_seconds=wait_seconds;self.retry_limit=retry_limit
        self.publisher=publisher
        self.budget_required=budget_required
        self.enforce_binding=enforce_binding
        self.allowed_bindings=frozenset()

    async def create(self, project_id: str, request_key: str, snapshot: dict) -> str:
        """Deduplicate logical requests and refuse reusing an identity with different inputs."""
        request_hash=digest(snapshot)
        async with self.factory() as db:
            existing=(await db.execute(select(ExecutionJob).where(ExecutionJob.project_id==project_id,ExecutionJob.request_key==request_key))).scalars().first()
            if existing:
                if existing.request_hash!=request_hash:raise HTTPException(409,'请求标识已被其他版本使用')
                return existing.id
            job_id=str(uuid.uuid4());task_id=str(uuid.uuid4())
            db.add(GenerationTask(id=task_id,mode='async_polling',task_kind='durable_execution',status='pending',progress=0,payload={'execution_job_id':job_id,'snapshot':snapshot},error=''))
            await db.flush()
            db.add(ExecutionJob(id=job_id,task_id=task_id,project_id=project_id,request_key=request_key,request_hash=request_hash,snapshot=snapshot,media_id=str(uuid.uuid4())))
            try:await db.commit()
            except IntegrityError:
                await db.rollback()
                winner=(await db.execute(select(ExecutionJob).where(ExecutionJob.project_id==project_id,ExecutionJob.request_key==request_key))).scalars().first()
                if winner is None or winner.request_hash!=request_hash:raise HTTPException(409,'请求冲突') from None
                return winner.id
            return job_id

    async def _update(self, job_id: str, token: str, **values) -> bool:
        """Fence stale workers; an expired owner cannot overwrite a newer worker's state."""
        async with self.factory() as db:
            changed=await db.execute(update(ExecutionJob).where(ExecutionJob.id==job_id,ExecutionJob.lease_token==token,ExecutionJob.lease_until>self.clock()).values(**values))
            if changed.rowcount and 'state' in values:
                job=await db.get(ExecutionJob,job_id)
                state=values['state']
                status={'ready':'succeeded','failed':'failed','cancelled':'cancelled','queued':'pending','paused':'pending'}.get(state,'running')
                await db.execute(update(GenerationTask).where(GenerationTask.id==job.task_id).values(status=status,progress=100 if state=='ready' else 0,result={'execution_job_id':job.id,'execution_state':state},error=values.get('reason','') if state=='failed' else ''))
            if changed.rowcount and values.get('state') in TERMINAL:
                # Release in the same transaction: a crash after terminal commit
                # must not leave the single execution slot permanently occupied.
                await db.execute(update(ExecutionSlot).where(ExecutionSlot.id==1,ExecutionSlot.token==token,ExecutionSlot.job_id==job_id).values(job_id=None,token=None,lease_until=0))
            await db.commit();return bool(changed.rowcount)

    async def _claim(self, job_id: str) -> tuple[ExecutionJob,str] | None:
        """CAS the lease and commit it before any provider operation."""
        now=self.clock();token=str(uuid.uuid4())
        async with self.factory() as db:
            await db.execute(sqlite_insert(ExecutionSlot).values(id=1,lease_until=0).on_conflict_do_nothing())
            slot=await db.execute(update(ExecutionSlot).where(ExecutionSlot.id==1,ExecutionSlot.lease_until<=now,or_(ExecutionSlot.job_id.is_(None),ExecutionSlot.job_id==job_id)).values(token=token,job_id=job_id,lease_until=now+self.lease_seconds))
            if not slot.rowcount:await db.rollback();return None
            changed=await db.execute(update(ExecutionJob).where(ExecutionJob.id==job_id,ExecutionJob.lease_until<=now,ExecutionJob.next_due<=now,ExecutionJob.state.not_in(TERMINAL|{'paused'})).values(lease_token=token,lease_until=now+self.lease_seconds))
            if not changed.rowcount:await db.rollback();return None
            await db.commit();job=await db.get(ExecutionJob,job_id)
            return job,token

    async def _renew(self, job_id: str, token: str) -> bool:
        """Renew both fences together; an expired lease cannot be resurrected."""
        now=self.clock()
        async with self.factory() as db:
            job=await db.execute(update(ExecutionJob).where(ExecutionJob.id==job_id,ExecutionJob.lease_token==token,ExecutionJob.lease_until>now).values(lease_until=now+self.lease_seconds))
            slot=await db.execute(update(ExecutionSlot).where(ExecutionSlot.id==1,ExecutionSlot.token==token,ExecutionSlot.job_id==job_id,ExecutionSlot.lease_until>now).values(lease_until=now+self.lease_seconds))
            if not job.rowcount or not slot.rowcount:await db.rollback();return False
            await db.commit();return True

    async def _external(self, job_id: str, token: str, operation):
        """Fence before an external call and keep its lease alive until it finishes.

        Concrete HTTP adapters must use request_isolated_async: its child dies within
        60 seconds even when the worker disappears, before a 90-second lease can
        expire. The execution slot stays reserved by this job until its terminal
        state, including during unknown acceptance and resumable pauses.
        """
        if not await self._renew(job_id,token):raise LeaseLost()
        async def heartbeat():
            """Cancel this owner when another fence or deadline supersedes it."""
            while True:
                await asyncio.sleep(10)
                if not await self._renew(job_id,token):return
        task=asyncio.create_task(operation());pulse=asyncio.create_task(heartbeat())
        try:
            done,_=await asyncio.wait({task,pulse},timeout=60,return_when=asyncio.FIRST_COMPLETED)
            if task in done:return await task
            task.cancel();await asyncio.gather(task,return_exceptions=True)
            if pulse in done:raise LeaseLost()
            raise TimeoutError('External operation deadline')
        finally:
            if not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
            pulse.cancel();await asyncio.gather(pulse,return_exceptions=True)

    async def request_cancel(self, job_id: str) -> None:
        """Record intent; remote cancellation is confirmed only by the adapter's result."""
        async with self.factory() as db:
            await db.execute(update(ExecutionJob).where(ExecutionJob.id==job_id,ExecutionJob.state.not_in(TERMINAL)).values(cancel_requested=True))
            await db.commit()

    async def resume(self, job_id: str) -> None:
        """Resume query/download after a bounded pause, never reset to submission."""
        async with self.factory() as db:
            job=await db.get(ExecutionJob,job_id)
            if job is None or job.state!='paused':raise HTTPException(409,'任务不处于可恢复暂停状态')
            job.state=job.pause_from or ('running' if job.remote_id else 'submission_unknown')
            job.query_retries=0;job.download_retries=0;job.started_at=self.clock();job.next_due=0
            await db.commit()

    async def _pause(self, job: ExecutionJob, token: str, reason: str) -> None:
        """Limits are resumable pauses, not assertions that a model cannot succeed."""
        await self._update(job.id,token,state='paused',pause_from=job.state,reason=reason)

    async def step(self, job_id: str) -> None:
        """Execute at most one external operation, with a durable state transition first."""
        if self.budget_required:
            async with self.factory() as db:
                job=await db.get(ExecutionJob,job_id)
                hold=await db.get(ExecutionBudgetEntry,job_id)
                if job is None:return
                if self.enforce_binding:
                    binding=await db.get(ExecutionBinding,job_id)
                    if binding is None or binding.binding_hash not in self.allowed_bindings:
                        # A changed service/account/policy cannot take over an
                        # old request. Restoring its trusted binding can resume it.
                        await db.execute(update(ExecutionJob).where(ExecutionJob.id==job_id,ExecutionJob.state.not_in(TERMINAL)).values(reason='runtime_binding_unavailable'))
                        await db.commit();return
                if job.state=='queued' and (hold is None or hold.project_id!=job.project_id or hold.state!='reserved'):
                    # An absent quote/reservation is never permission to send.
                    return
        claimed=await self._claim(job_id)
        if claimed is None:return
        job,token=claimed
        try:
            if job.state=='queued':
                if job.cancel_requested:
                    await self._update(job.id,token,state='cancelled');return
                async with self.factory() as db:
                    # An attempt exists before the first byte is sent. A unique job
                    # constraint also protects against accidental future retry code.
                    if self.budget_required:
                        held=await db.execute(update(ExecutionBudgetEntry).where(ExecutionBudgetEntry.job_id==job.id,ExecutionBudgetEntry.project_id==job.project_id,ExecutionBudgetEntry.state=='reserved').values(state='unknown'))
                        if not held.rowcount:await db.rollback();return
                    attempt=SubmissionAttempt(id=str(uuid.uuid4()),job_id=job.id,request_hash=job.request_hash,state='prepared')
                    db.add(attempt)
                    changed=await db.execute(update(ExecutionJob).where(ExecutionJob.id==job.id,ExecutionJob.lease_token==token,ExecutionJob.lease_until>self.clock(),ExecutionJob.state=='queued').values(state='submitting',started_at=self.clock()))
                    if not changed.rowcount:await db.rollback();return
                    await db.commit();attempt_id=attempt.id
                try:
                    remote_id=await self._external(job.id,token,lambda:self.transport.submit(job.snapshot,attempt_id))
                    if not remote_id:raise RuntimeError('missing provider identity')
                except RejectedSubmission:
                    async with self.factory() as db:
                        await db.execute(update(SubmissionAttempt).where(SubmissionAttempt.id==attempt_id).values(state='rejected'));await db.commit()
                    await self._update(job.id,token,state='failed',reason='provider_rejected');return
                except Exception:
                    await self._update(job.id,token,state='submission_unknown',reason='submission_acceptance_unknown');return
                async with self.factory() as db:
                    # Always preserve a late provider ID in the journal, even if
                    # this owner's lease expired while the server was answering.
                    await db.execute(update(SubmissionAttempt).where(SubmissionAttempt.id==attempt_id).values(state='accepted',remote_id=remote_id));await db.commit()
                await self._update(job.id,token,state='running',remote_id=remote_id,next_due=self.clock()+self.poll_seconds)
                return
            if job.state in {'submitting','submission_unknown'}:
                async with self.factory() as db:
                    attempt=(await db.execute(select(SubmissionAttempt).where(SubmissionAttempt.job_id==job.id))).scalars().one()
                if attempt.state=='rejected':
                    # The rejection journal may commit just before a process dies.
                    # Recover its terminal state without calling submit/reconcile.
                    await self._update(job.id,token,state='failed',reason='provider_rejected');return
                try:remote_id=attempt.remote_id or await self._external(job.id,token,lambda:self.transport.reconcile(attempt.id))
                except Exception:remote_id=None
                if remote_id:await self._update(job.id,token,state='running',remote_id=remote_id,next_due=self.clock()+self.poll_seconds,reason='')
                else:
                    retries=job.query_retries+1
                    await self._update(job.id,token,state='submission_unknown',query_retries=retries,reason='manual_reconciliation_required',next_due=self.clock()+self.poll_seconds)
                    if retries>=self.retry_limit:
                        await self._update(job.id,token,state='paused',pause_from='submission_unknown',reason='unknown_acceptance_requires_manual_resolution')
                return
            if job.started_at is not None and self.clock()-job.started_at>self.wait_seconds:
                await self._pause(job,token,'wait_limit_reached');return
            if job.cancel_requested and job.remote_id:
                if await self._external(job.id,token,lambda:self.transport.cancel(job.remote_id)):
                    await self._update(job.id,token,state='cancelled');return
                # False is an unconfirmed request; keep the remote task identity.
            if job.state=='running':
                try:result=await self._external(job.id,token,lambda:self.transport.query(job.remote_id or ''))
                except Exception:
                    retries=job.query_retries+1
                    await self._update(job.id,token,query_retries=retries,next_due=self.clock()+self.poll_seconds)
                    if retries>=self.retry_limit:await self._pause(job,token,'query_retry_limit')
                    return
                status=result.get('status')
                validate_task_payload(result)
                if status=='cancelled':await self._update(job.id,token,state='cancelled');return
                if status=='failed':await self._update(job.id,token,state='failed',reason='provider_failed');return
                if status=='succeeded':await self._update(job.id,token,state='downloading',result=result,query_retries=0,next_due=0)
                else:await self._update(job.id,token,next_due=self.clock()+max(self.poll_seconds,float(result.get('retry_after',0))))
                return
            if job.state=='downloading':
                try:
                    artifact=await self._external(job.id,token,lambda:self.transport.download(job.result or {},job.media_id or '',job.project_id))
                    media_id=await self.publisher(self.factory,self.clock,job.id,token,artifact)
                except Exception:
                    retries=job.download_retries+1
                    await self._update(job.id,token,download_retries=retries,next_due=self.clock()+self.poll_seconds)
                    if retries>=self.retry_limit:await self._pause(job,token,'download_retry_limit')
                    return
                await self._update(job.id,token,state='ready',media_id=media_id,reason='')
        finally:
            await self._update(job.id,token,lease_until=0)
            async with self.factory() as db:
                current=await db.get(ExecutionJob,job.id)
                values={'lease_until':0}
                if current and current.state in TERMINAL:values['job_id']=None
                await db.execute(update(ExecutionSlot).where(ExecutionSlot.id==1,ExecutionSlot.token==token).values(**values))
                await db.commit()
