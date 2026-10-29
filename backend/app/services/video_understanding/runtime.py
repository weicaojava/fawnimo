"""One bounded, cancellable multimodal worker; interrupted requests are never replayed automatically."""
import asyncio
import logging
import time
import uuid
from functools import wraps
from sqlalchemy import select, update
from fastapi import HTTPException
from app.core.contracts.model_accounting import ModelPriceEnvelope, QuotedModelCall
from app.models.model_charge import ModelCallCharge
from app.models.video_understanding import UnderstandingAttempt, UnderstandingJob
from app.services.worker.execution_budget import transaction
from app.services.video_understanding.admission import complete_plan
from app.services.video_understanding.charges import attempt_charge, release_unsent
from app.services.video_understanding.model_connections import material
from app.services.worker.model_prices import ModelPriceRegistry
from app.services.worker.model_charges import prepare_call, record_accepted_call
from app.services.worker.model_job_charges import binding_digest
from app.services.worker.model_billing import admission_review_block
from app.services.video_understanding.errors import UnderstandingFailure
from app.services.video_understanding.source import checked_source, prepare_inputs
from app.services.video_understanding.transport import execute

logger = logging.getLogger(__name__)


def joined_database(operation):
    """Finish a short DB unit and its physical connection cleanup before propagating worker cancellation.

    An aiosqlite statement runs on a separate thread. Cancelling its waiter
    does not stop an already queued BEGIN. Keep commit/rollback/session-close
    together and joined; HTTP and decoder work remain independently killable.
    """
    @wraps(operation)
    async def joined(*args, **kwargs):
        """Preserve cancellation, including repeated stop requests, after the owned DB task has actually closed."""
        task = asyncio.create_task(operation(*args, **kwargs))
        cancellation = None
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as error:
                cancellation = error
            except BaseException:
                break
        if cancellation is not None:
            # A prepared descriptor might have captured a key immediately
            # before cancellation; it must never escape to a new HTTP call.
            if not task.cancelled() and task.exception() is None:
                result = task.result()
                if isinstance(result, dict) and 'secret' in result:
                    result['secret'] = ''
            raise cancellation
        return task.result()
    return joined


@joined_database
async def recover_interrupted(factory, *, clock=time.time) -> int:
    """Fail expired running attempts without reissuing potentially billable external requests."""
    async with factory() as db:
        async with transaction(db):
            rows = (await db.scalars(select(UnderstandingJob).where(UnderstandingJob.state == 'running', UnderstandingJob.lease_until <= clock()))).all()
            for row in rows:
                row.state = 'cancelled' if row.cancel_requested else 'failed'
                row.phase = row.state
                row.reason = '服务中断，本次可能已提交模型请求；不会自动重试，请先核对服务任务与费用，再手动重试'
                row.lease_token, row.lease_until, row.updated_at = None, 0, clock()
                await db.execute(update(UnderstandingAttempt).where(UnderstandingAttempt.job_id == row.id, UnderstandingAttempt.attempt == row.attempt).values(state=row.state, phase=row.phase, reason=row.reason, finished_at=row.updated_at))
                await release_unsent(db, row.id, row.attempt, row.project_id)
            return len(rows)


@joined_database
async def claim_job(factory, job_id: str, *, clock=time.time, deadline_seconds=None, claimed=None):
    """Claim one queued lease and expose a private cancellation receipt only after commit and Session close."""
    token = str(uuid.uuid4())
    async with factory() as db:
        async with transaction(db):
            row = await db.get(UnderstandingJob, job_id)
            if row is None or row.state != 'queued' or row.cancel_requested:
                return None
            duration = row.connection['timeout_seconds'] * 2 + 180
            if deadline_seconds is not None:
                duration = min(duration, deadline_seconds)
            changed = await db.execute(update(UnderstandingJob).where(UnderstandingJob.id == job_id, UnderstandingJob.state == 'queued', UnderstandingJob.cancel_requested.is_(False)).values(state='running', phase='preparing', progress=10, lease_token=token, lease_until=clock() + duration + 30, updated_at=clock()))
            if not changed.rowcount:
                return None
            row = await db.get(UnderstandingJob, job_id)
            await db.execute(update(UnderstandingAttempt).where(UnderstandingAttempt.job_id == row.id, UnderstandingAttempt.attempt == row.attempt).values(state='running', phase='preparing', started_at=clock()))
            result = {'id': row.id, 'project_id': row.project_id, 'attempt': row.attempt,
                'source': row.source, 'connection': row.connection, 'token': token, 'deadline_seconds': duration}
    if claimed is not None:
        claimed.append(result)
    return result


@joined_database
async def phase_update(factory, job: dict, phase: str, progress: int, receipt=None) -> None:
    """Persist receipt metadata before its associated request, while cancellation fences remain active."""
    async with factory() as db:
        async with transaction(db):
            changed = await db.execute(update(UnderstandingJob).where(UnderstandingJob.id == job['id'], UnderstandingJob.state == 'running', UnderstandingJob.lease_token == job['token'], UnderstandingJob.cancel_requested.is_(False), UnderstandingJob.lease_until > time.time()).values(phase=phase, progress=progress, updated_at=time.time()))
            if not changed.rowcount:
                raise asyncio.CancelledError()
            values = {'phase': phase}
            if receipt is not None:
                values['input_receipt'] = receipt
            await db.execute(update(UnderstandingAttempt).where(UnderstandingAttempt.job_id == job['id'], UnderstandingAttempt.attempt == job['attempt']).values(**values))


@joined_database
async def finish_job(factory, job: dict, state: str, reason: str = '', result=None) -> bool:
    """Publish one validated result atomically; cancelled or superseded leases never create a candidate."""
    async with factory() as db:
        async with transaction(db):
            row = await db.get(UnderstandingJob, job['id'])
            if row is None or row.state != 'running' or row.lease_token != job['token'] or row.lease_until <= time.time():
                return False
            if row.cancel_requested:
                state, reason, result = 'cancelled', '用户已取消；已发出的服务请求可能已计费，请在服务侧核对。本次不采用候选，也不生成草稿。', None
            row.state, row.phase, row.reason = state, state, reason[:255]
            row.lease_token, row.lease_until, row.updated_at = None, 0, time.time()
            if result is not None and state == 'succeeded':
                row.document, row.transcripts, row.warnings, row.progress = result['document'], result['transcripts'], result['warnings'], 100
            await db.execute(update(UnderstandingAttempt).where(UnderstandingAttempt.job_id == row.id, UnderstandingAttempt.attempt == job['attempt']).values(state=state, phase=state, reason=row.reason, finished_at=row.updated_at,
                **({'input_receipt': result['receipt'], 'response_hash': result['response_hash']} if result is not None else {})))
            return True


class UnderstandingWorker:
    """Scan explicit persistent jobs, with local-only defaults and no access to old provider secrets."""
    def __init__(self, factory, trusted_ffmpeg: str, *, enabled: bool = True, allow_remote: bool = False,
                 deadline_seconds=None, price_registry=None, trusted_local_host=None, trusted_local_ports=None):
        if deadline_seconds is not None and (type(deadline_seconds) is not int or not 1 <= deadline_seconds <= 780):
            raise ValueError('Understanding total deadline must be within 1..780 seconds')
        self.factory, self.ffmpeg = factory, trusted_ffmpeg
        self.enabled, self.allow_remote = enabled, allow_remote
        self.price_registry = price_registry or ModelPriceRegistry()
        self.trusted_local_host = trusted_local_host
        self.trusted_local_ports = frozenset(trusted_local_ports or [])
        if trusted_local_host not in {None, 'localhost', '127.0.0.1', '::1'} or any(type(port) is not int or not 1 <= port <= 65535 for port in self.trusted_local_ports):
            raise ValueError('Understanding local network policy must contain an explicit loopback host and bounded ports')
        self.deadline_seconds = deadline_seconds
        self.task = None

    async def checked_plan(self, db, job):
        """Fence the lease and validate every original unit, current binding and named price before a prefix can send."""
        row = await db.scalar(select(UnderstandingJob).where(UnderstandingJob.id==job['id']).execution_options(populate_existing=True))
        if row is None or row.project_id!=job['project_id'] or row.attempt!=job['attempt'] or row.state!='running' or row.lease_token!=job['token'] or row.cancel_requested or row.lease_until<=time.time():
            raise asyncio.CancelledError()
        link = await attempt_charge(db, row.id, row.attempt, row.project_id, required=True)
        calls = (await db.scalars(select(ModelCallCharge).where(ModelCallCharge.batch_id==link.batch_id))).all()
        # ASR and vision have distinct quote fingerprints. A newly arrived
        # breach for an earlier unit must fence the complete attempt rather
        # than checking only the next unit's different quote.
        await admission_review_block(db, row.project_id, [QuotedModelCall(unit_key=call.unit_key,
            kind=call.kind,binding_hash=call.binding_hash,quote=call.price_snapshot) for call in calls])
        expected = {call.kind: ModelPriceEnvelope.model_validate(call.price_snapshot).fingerprint for call in calls}
        await complete_plan(db, row.connection, row.source, self, expected=expected, original_bindings=link.bindings)
        return link, {call.kind: call for call in calls}

    @joined_database
    async def before_request(self, job, kind, phase, progress, receipt):
        """Capture one coherent private key and commit the unit's unknown hold with its lease/receipt before HTTP."""
        async with self.factory() as db:
            async with transaction(db):
                link, calls = await self.checked_plan(db, job)
                if kind not in calls:
                    raise HTTPException(409, '本次理解未授权此调用单元，未发送素材')
                binding, secret = await material(db, calls[kind].model_id, kind)
                if binding != link.bindings[kind]:
                    raise HTTPException(409, '原理解连接已变化，未发送素材')
                await prepare_call(db, project_id=link.project_id, charge_id=calls[kind].id, binding_hash=binding_digest(binding))
                await db.execute(update(UnderstandingJob).where(UnderstandingJob.id==job['id']).values(phase=phase, progress=progress, updated_at=time.time()))
                await db.execute(update(UnderstandingAttempt).where(UnderstandingAttempt.job_id==job['id'], UnderstandingAttempt.attempt==job['attempt']).values(phase=phase, input_receipt=dict(receipt)))
                return {**binding, 'secret': secret, 'charge_id': calls[kind].id}

    @joined_database
    async def record_response(self, job, charge_id, request_id):
        """Late private request evidence remains attached to its original unit without altering a finished business journal."""
        if request_id is None:
            return
        async with self.factory() as db:
            async with transaction(db):
                link = await attempt_charge(db, job['id'], job['attempt'], job['project_id'], required=True)
                charge = await db.get(ModelCallCharge, charge_id)
                if charge is None or charge.batch_id != link.batch_id:
                    raise HTTPException(409, '理解响应不属于本次原费用计划')
                await record_accepted_call(db, project_id=link.project_id, charge_id=charge_id, provider_request_id=request_id)

    @joined_database
    async def connection_for_job(self, job):
        """Read the complete original plan under a joined writer boundary before any media preparation or HTTP."""
        async with self.factory() as db:
            async with transaction(db):
                link, _calls = await self.checked_plan(db, job)
                return {**job['connection'], 'vision_model': link.bindings['video_vision']['model_name'],
                        'audio_model': link.bindings.get('video_asr', {}).get('model_name', '')}

    @joined_database
    async def observe_job(self, job):
        """Close one lease-monitor read before its monitor task may be cancelled."""
        async with self.factory() as db:
            return (await db.execute(select(UnderstandingJob.state, UnderstandingJob.lease_token,
                UnderstandingJob.cancel_requested, UnderstandingJob.lease_until).where(UnderstandingJob.id==job['id']))).first()

    @joined_database
    async def release_job(self, job):
        """Join cleanup of only this original attempt's untouched units even during application shutdown."""
        async with self.factory() as db:
            async with transaction(db):
                await release_unsent(db, job['id'], job['attempt'], job['project_id'])

    @joined_database
    async def cleanup_job(self, job, pipeline, guard):
        """Join cancelled children, close any surviving owned attempt and release untouched units despite repeated shutdown."""
        pipeline.cancel();guard.cancel()
        await asyncio.gather(pipeline,guard,return_exceptions=True)
        # A second owner cancellation can interrupt the normal exception
        # handler. This only closes a still-owned running attempt; successful,
        # cancelled or superseded journals remain untouched.
        await finish_job(self.factory,job,'failed','服务执行已中断，可能已有模型请求发出；未自动重试，请核对后再操作')
        await self.release_job(job)

    @joined_database
    async def close_cancelled_claim(self, job):
        """Close a real committed claim cancelled before its return, without waiting for lease expiry or sending any HTTP."""
        await finish_job(self.factory,job,'failed','服务在认领后中断，尚未发送模型请求；已释放未发送费用，未自动重试')
        await self.release_job(job)

    async def guard(self, job: dict) -> None:
        """Observe cancellation without extending a lease beyond the absolute execution deadline."""
        while True:
            current = await self.observe_job(job)
            if current is None or current.state != 'running' or current.lease_token != job['token'] or current.cancel_requested or current.lease_until <= time.time():
                return
            await asyncio.sleep(0.25)

    async def pipeline(self, job: dict) -> dict:
        """Prepare real source evidence, call configured models and verify bytes again before publication."""
        connection = await self.connection_for_job(job)
        async with asyncio.timeout(job['deadline_seconds']):
            inputs = await prepare_inputs(self.factory, job['project_id'], job['source'], connection, self.ffmpeg)
            await phase_update(self.factory, job, 'prepared', 35, inputs['receipt'])
            async def stage(phase, progress, receipt):
                """Record exactly which bounded inputs belong to this in-flight request."""
                await phase_update(self.factory, job, phase, progress, receipt)
            async def prepare(kind, phase, progress, receipt):
                """Use a fresh writer transaction for each real source-transmission boundary."""
                return await self.before_request(job, kind, phase, progress, receipt)
            async def response(charge_id, request_id):
                """Keep request IDs private, including unsuccessful or malformed responses."""
                await self.record_response(job, charge_id, request_id)
            result = await execute(connection, job['source'], inputs, before_request=prepare, on_response=response,
                trusted_local_host=self.trusted_local_host, trusted_local_ports=self.trusted_local_ports, on_phase=stage)
            async with self.factory() as db:
                _row, current = await checked_source(db, job['project_id'], job['source']['analysis_id'])
                if any(current[field] != job['source'][field] for field in ('sha256', 'media_id', 'manifest_hash', 'frame_clock_hash')):
                    raise UnderstandingFailure('source_changed', '原片或解析证据在执行中发生变化，本次候选未采用')
                if current['cut_indices'] != job['source']['cut_indices']:
                    result['warnings'].append('当前人工切点已变化，本候选仍固定使用提交时的切点；不会覆盖新切点。')
            return result

    async def run_job(self, job_id: str) -> bool:
        """Race network execution with cancellation, joining both paths before updating persistent state."""
        if not self.enabled:
            return False
        claimed = []
        try:
            job = await claim_job(self.factory, job_id, deadline_seconds=self.deadline_seconds, claimed=claimed)
        except asyncio.CancelledError:
            if claimed:
                await self.close_cancelled_claim(claimed[0])
            raise
        if job is None:
            return False
        pipeline = asyncio.create_task(self.pipeline(job))
        guard = asyncio.create_task(self.guard(job))
        try:
            done, _pending = await asyncio.wait({pipeline, guard}, return_when=asyncio.FIRST_COMPLETED)
            if pipeline not in done:
                if guard.exception() is not None:
                    raise UnderstandingFailure('lease_monitor_failed', '执行保护暂时不可用，本次请求已中止；不会自动重试，请先核对服务任务状态')
                pipeline.cancel()
                await asyncio.gather(pipeline, return_exceptions=True)
                return await finish_job(self.factory, job, 'cancelled', '任务取消或执行租约已失效，本次结果未采用')
            result = await pipeline
            return await finish_job(self.factory, job, 'succeeded', result=result)
        except asyncio.CancelledError:
            pipeline.cancel(); guard.cancel()
            await asyncio.gather(pipeline, guard, return_exceptions=True)
            await finish_job(self.factory, job, 'failed', '服务执行已中断，可能已有模型请求发出；未自动重试，请核对后再操作')
            # Cancelling one child request must not stop the scanner; only application shutdown cancels this owner task.
            if asyncio.current_task().cancelling():
                raise
            return False
        except UnderstandingFailure as error:
            return await finish_job(self.factory, job, 'failed', error.message)
        except HTTPException as error:
            return await finish_job(self.factory, job, 'failed', str(error.detail))
        except TimeoutError:
            return await finish_job(self.factory, job, 'failed', '理解流程超过执行时限；可能已有请求发出，未自动重试，请核对服务后再操作')
        except Exception:
            return await finish_job(self.factory, job, 'failed', '来源证据不可用或理解流程执行失败；未采用候选，也未创建DNA草稿')
        finally:
            await self.cleanup_job(job,pipeline,guard)

    async def tick(self) -> bool:
        """Recover interrupted attempts, then execute at most one explicitly queued job."""
        await recover_interrupted(self.factory)
        if not self.enabled:
            return False
        async with self.factory() as db:
            identifier = await db.scalar(select(UnderstandingJob.id).where(UnderstandingJob.state == 'queued', UnderstandingJob.cancel_requested.is_(False)).order_by(UnderstandingJob.created_at).limit(1))
        return await self.run_job(identifier) if identifier else False

    async def run(self) -> None:
        """Keep the scanner alive without logging provider content, media paths or credentials."""
        while True:
            try:
                await self.tick()
            except Exception as error:
                logger.error('Video understanding scanner failed (%s)', type(error).__name__)
            await asyncio.sleep(1)

    async def start(self) -> None:
        """Start scanning after application schema initialization and runtime policy selection."""
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run(), name='video-understanding-scanner')

    async def stop(self) -> None:
        """Cancel and join HTTP requests and exact-frame decoder children before database shutdown."""
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

