"""Project-scoped configuration, explicit job intents and review-only DNA draft creation."""
import time
import uuid
from fastapi import HTTPException
from sqlalchemy import select, update
from app.core.contracts.video_dna import DnaCreate
from app.core.contracts.video_understanding import UnderstandingConnection
from app.models.video_understanding import UnderstandingAttempt, UnderstandingConfig, UnderstandingJob
from app.models.video_dna import DnaDraft
from app.models.understanding_charge import UnderstandingAttemptCharge
from app.services.script_preparation.service import content_hash
from app.services.studio.production import project_exists
from app.services.video_dna import service as dna
from app.services.video_dna.source import owned_analysis, source_from_analysis
from app.services.worker.execution_budget import transaction
from app.services.video_understanding.admission import complete_plan, setup_view
from app.services.video_understanding.charges import attempt_charge, bind_attempt, release_unsent
from app.services.video_understanding.model_connections import safe_binding
from app.services.worker.model_charges import actor
from app.services.video_understanding.errors import UnderstandingFailure
from app.services.video_understanding.source import checked_source, sample_indices, source_snapshot


async def owned_job(db, project_id: str, job_id: str):
    """A job identity never grants access to another project's source, candidate or connection."""
    row = await db.scalar(select(UnderstandingJob).where(UnderstandingJob.id == job_id, UnderstandingJob.project_id == project_id))
    if row is None:
        raise HTTPException(404, '本工程的视频理解任务不存在')
    return row


async def connection_view(db, project_id: str, worker, analysis_id=None) -> dict:
    """Show incomplete setup as actionable state, without any provider/network probe."""
    await project_exists(db, project_id)
    row = await db.get(UnderstandingConfig, project_id)
    source = source_snapshot(await owned_analysis(db, project_id, analysis_id)) if analysis_id else None
    return await setup_view(db, project_id, row.revision if row else 0, row.connection if row else UnderstandingConnection().model_dump(), worker, source)


async def save_connection(factory, project_id: str, body, worker) -> dict:
    """CAS explicit managed model IDs; saving neither probes a supplier nor loads a credential."""
    value = body.model_dump(exclude={'expected_revision'})
    for field in ('vision_model_id', 'audio_model_id'):
        value[field] = value[field].strip()
    if value['enabled'] and (not value['vision_model_id'] or (value['transcribe_audio'] and not value['audio_model_id'])):
        raise HTTPException(422, '启用前请选择模型管理中的画面理解和语音转写模型')
    # Historical endpoint/environment fields remain readable on old journals,
    # but no new configuration can carry a parallel unpriced execution path.
    for field in ('base_url', 'vision_model', 'audio_model', 'audio_base_url', 'credential_env', 'audio_credential_env'):
        value[field] = ''
    async with factory() as db:
        async with transaction(db):
            await project_exists(db, project_id)
            for field, kind in (('vision_model_id', 'video_vision'), ('audio_model_id', 'video_asr')):
                if value[field]:
                    await safe_binding(db, value[field], kind)
            row = await db.get(UnderstandingConfig, project_id)
            revision = row.revision if row else 0
            if body.expected_revision != revision:
                raise HTTPException(409, '理解服务配置已有新修订，请刷新后再保存；未覆盖当前配置')
            if row is None:
                db.add(UnderstandingConfig(project_id=project_id, revision=1, connection=value))
            elif row.connection != value:
                row.connection, row.revision = value, revision + 1
    async with factory() as db:
        return await connection_view(db, project_id, worker)


async def job_view(db, project_id: str, job_id: str) -> dict:
    """Expose safe persistent receipts and machine candidates, not private lease tokens or model secrets."""
    row = await owned_job(db, project_id, job_id)
    attempts = (await db.scalars(select(UnderstandingAttempt).where(UnderstandingAttempt.job_id == row.id, UnderstandingAttempt.project_id == project_id).order_by(UnderstandingAttempt.attempt.desc()))).all()
    return {key: getattr(row, key) for key in ('id', 'project_id', 'analysis_id', 'state', 'phase', 'attempt', 'progress', 'cancel_requested', 'reason', 'source', 'connection', 'document', 'transcripts', 'warnings', 'draft_id', 'created_at', 'updated_at')} | {
        'attempts': [{key: getattr(item, key) for key in ('attempt', 'state', 'phase', 'started_at', 'finished_at', 'reason', 'input_receipt', 'response_hash')} for item in attempts]}


async def list_jobs(db, project_id: str, limit: int = 30) -> list[dict]:
    """Bound the visible persistent task history without substituting demo successes."""
    await project_exists(db, project_id)
    ids = (await db.scalars(select(UnderstandingJob.id).where(UnderstandingJob.project_id == project_id).order_by(UnderstandingJob.created_at.desc()).limit(limit))).all()
    return [await job_view(db, project_id, identifier) for identifier in ids]


async def retry_view(db, project_id, job_id, worker):
    """Preview only the original attempt's connections and current complete quote before explicit retry confirmation."""
    job = await owned_job(db, project_id, job_id)
    view = await setup_view(db, project_id, 1, job.connection, worker, job.source)
    try:
        link = await attempt_charge(db, job_id, job.attempt, project_id, required=True)
        await complete_plan(db, job.connection, job.source, worker, original_bindings=link.bindings)
    except HTTPException as error:
        view.update(available=False, message=str(error.detail), next_steps=[str(error.detail)],
                    price_fingerprints={}, max_reservation_minor=None)
    return view


async def create_job(factory, project_id: str, body, worker, *, submitted_by='local_operator') -> str:
    """Atomically bind the actual source, complete quote plan, actor and original project before media leaves."""
    if not body.request_key.strip():
        raise HTTPException(422, '理解请求标识不能为空')
    actor(submitted_by)
    digest = content_hash({'domain': 'video-understanding-priced-v2', 'submitted_by': submitted_by, **body.model_dump()})
    now, identifier = time.time(), str(uuid.uuid4())
    async with factory() as db:
        async with transaction(db):
            old = await db.scalar(select(UnderstandingJob).where(UnderstandingJob.project_id == project_id, UnderstandingJob.request_key == body.request_key))
            if old:
                if old.request_hash != digest:
                    raise HTTPException(409, '理解请求标识冲突')
                link = await attempt_charge(db, old.id, 1, project_id, required=True)
                if link.submitted_by != submitted_by or link.request_hash != digest:
                    raise HTTPException(409, '原理解请求已由其他身份或内容绑定')
                return old.id
            config = await db.get(UnderstandingConfig, project_id)
            if config is None or config.revision != body.expected_connection_revision:
                raise HTTPException(409, '理解服务配置已变化，请刷新后重新开始')
            try:
                analysis, source = await checked_source(db, project_id, body.analysis_id)
                if analysis.revision != body.expected_analysis_revision:
                    raise HTTPException(409, '视频解析修订已变化，请刷新后再建立理解任务')
                sample_indices(source, config.connection['max_frames'])
            except UnderstandingFailure as error:
                raise HTTPException(409, error.message) from None
            connection = dict(config.connection)
            bindings, quotes = await complete_plan(db, connection, source, worker, expected=body.expected_price_fingerprints)
            job = UnderstandingJob(id=identifier, project_id=project_id, analysis_id=body.analysis_id,
                request_key=body.request_key, request_hash=digest, source=source, connection=connection,
                state='queued', phase='queued', attempt=1, progress=0, created_at=now, updated_at=now)
            db.add(job)
            # These journal models deliberately have no ORM cascade relationship; establish the FK parent first.
            await db.flush()
            db.add(UnderstandingAttempt(id=str(uuid.uuid4()), job_id=identifier, project_id=project_id, attempt=1, state='queued', phase='queued'))
            await db.flush()
            await bind_attempt(db, job, submitted_by=submitted_by, request_key=body.request_key,
                               request_hash=digest, bindings=bindings, quotes=quotes)
    return identifier


async def cancel_job(factory, project_id: str, job_id: str, expected_attempt: int) -> None:
    """Cancellation is attempt-scoped and fences result publication before stopping an active HTTP request."""
    async with factory() as db:
        async with transaction(db):
            row = await owned_job(db, project_id, job_id)
            if row.attempt != expected_attempt:
                raise HTTPException(409, '任务已有新的尝试，请刷新后再操作')
            if row.state == 'cancelled' or row.cancel_requested:
                return
            if row.state not in {'queued', 'running'}:
                raise HTTPException(409, '本次任务已结束，不能取消')
            row.cancel_requested, row.updated_at = True, time.time()
            row.reason = '用户取消，本次未提交模型请求' if row.state == 'queued' else '已取消本地结果采用；已发出的模型请求可能仍在服务侧执行或计费，请核对服务状态。本次不生成草稿。'
            row.state, row.phase = 'cancelled', 'cancelled'
            await db.execute(update(UnderstandingAttempt).where(UnderstandingAttempt.job_id == job_id, UnderstandingAttempt.attempt == row.attempt).values(state='cancelled', phase='cancelled', finished_at=row.updated_at, reason=row.reason))
            await release_unsent(db, row.id, row.attempt, project_id)


async def retry_job(factory, project_id: str, job_id: str, body, worker, *, submitted_by='local_operator') -> None:
    """Confirm a separately priced complete attempt, replaying its original recovery key before balance/state checks."""
    actor(submitted_by)
    digest = content_hash({'domain': 'video-understanding-retry-v2', 'job_id': job_id,
                           'submitted_by': submitted_by, **body.model_dump()})
    async with factory() as db:
        async with transaction(db):
            row = await owned_job(db, project_id, job_id)
            old = await db.scalar(select(UnderstandingAttemptCharge).where(UnderstandingAttemptCharge.job_id==job_id,
                UnderstandingAttemptCharge.request_key==body.request_key, UnderstandingAttemptCharge.project_id==project_id))
            if old:
                if old.submitted_by != submitted_by or old.request_hash != digest:
                    raise HTTPException(409, '本次重试标识已绑定其他内容或身份，未重复提交')
                await attempt_charge(db, job_id, old.attempt, project_id, required=True)
                return
            if row.attempt != body.expected_attempt:
                raise HTTPException(409, '任务已有新的尝试，请刷新，未重复提交')
            if row.state not in {'failed', 'cancelled'}:
                raise HTTPException(409, '仅失败或已取消的任务可以按原配置重试')
            original = await attempt_charge(db, job_id, row.attempt, project_id, required=True)
            bindings, quotes = await complete_plan(db, row.connection, row.source, worker,
                expected=body.expected_price_fingerprints, original_bindings=original.bindings)
            # Decode only the same source identity; a retry never switches to
            # a new config, analysis or another project's connection.
            _analysis, current = await checked_source(db, project_id, row.analysis_id)
            if any(current[field] != row.source[field] for field in ('sha256','media_id','manifest_hash','frame_clock_hash')):
                raise HTTPException(409, '原片证据已变化，请新建理解任务；未重新发送')
            row.attempt += 1
            row.state, row.phase, row.progress, row.cancel_requested, row.reason = 'queued', 'queued', 0, False, ''
            row.lease_token, row.lease_until, row.updated_at = None, 0, time.time()
            db.add(UnderstandingAttempt(id=str(uuid.uuid4()), job_id=row.id, project_id=project_id, attempt=row.attempt, state='queued', phase='queued'))
            await db.flush()
            await bind_attempt(db, row, submitted_by=submitted_by, request_key=body.request_key,
                               request_hash=digest, bindings=bindings, quotes=quotes)


async def create_candidate_draft(factory, project_id: str, job_id: str, expected_analysis_revision: int) -> str:
    """Create or recover exactly one new unreviewed DNA draft; failures and cancellations cannot enter DNA."""
    key = 'understanding:' + job_id
    async with factory() as db:
        row = await owned_job(db, project_id, job_id)
        if row.state != 'succeeded' or row.document is None:
            raise HTTPException(409, '只有成功且通过结构检查的候选可以建立DNA草稿；失败或取消没有生成草稿')
        if row.draft_id:
            await dna.owned_draft(db, project_id, row.draft_id)
            return row.draft_id
        # Recover a crash between the existing DNA service's commit and our link commit.
        previous = await db.scalar(select(DnaDraft).where(DnaDraft.project_id == project_id, DnaDraft.request_key == key))
        if previous:
            intent = DnaCreate(analysis_id=row.analysis_id, expected_analysis_revision=previous.source['analysis_revision'], request_key=key, document=row.document)
            if previous.initial_hash != dna.request_hash(intent, 'r2a-create-v1'):
                raise HTTPException(409, '已有DNA导入意图与本候选不一致，未覆盖草稿')
            identifier = previous.id
        else:
            analysis = await owned_analysis(db, project_id, row.analysis_id)
            if analysis.revision != expected_analysis_revision:
                raise HTTPException(409, '原分析版本已改变，请刷新后再建立草稿')
            current = source_from_analysis(analysis)
            if any(current[field] != row.source[field] for field in ('sha256', 'media_id', 'manifest_hash', 'frame_clock_hash')) or current['confirmed_cut_indices'] != row.source['cut_indices']:
                raise HTTPException(409, '请先人工确认与本次候选完全相同的原始帧切点；不会把自动候选冒充已确认切点')
            intent = DnaCreate(analysis_id=row.analysis_id, expected_analysis_revision=analysis.revision, request_key=key, document=row.document)
            identifier = None
    if identifier is None:
        identifier = await dna.create_draft(factory, project_id, intent)
    async with factory() as db:
        async with transaction(db):
            row = await owned_job(db, project_id, job_id)
            if row.state != 'succeeded':
                raise HTTPException(409, '候选状态已改变')
            if row.draft_id is not None and row.draft_id != identifier:
                raise HTTPException(409, '本候选已有另一个DNA草稿，未覆盖')
            row.draft_id, row.updated_at = identifier, time.time()
    return identifier
