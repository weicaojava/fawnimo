"""Durable zero-fee local speech candidates, separate from adoption and all paid-model ledgers."""
import asyncio
import copy
import logging
from pathlib import Path
import time
import uuid
from fastapi import HTTPException
from sqlalchemy import select
from app.config import settings
from app.models.local_speech import LocalSpeechJob, LocalSpeechProduct, LocalSpeechSlot
from app.models.sound_timeline import SoundCandidate
from app.models.production import ProductionMedia
from app.models.studio import FileItem
from app.models.task import GenerationTask
from app.models.task_links import GenerationTaskLink
from app.services.editing.common import chapter_owned, owned_plan, original_items, plan_script
from app.services.editing.media import canonical_hash, stat_identity
from app.services.editing.local_speech_engine import LocalSpeechEngine, PROFILE, available
from app.services.editing.sound_timeline import candidate_view
from app.services.editing.sound_process import cancel_and_join
from app.services.studio.production_media import media_path
from app.services.worker.execution_budget import transaction

LOGGER = logging.getLogger(__name__)


async def owned_job(db, project_id, identifier):
    """Resolve the original project's journal before exposing any frozen dialogue or candidate."""
    row = await db.get(LocalSpeechJob, identifier)
    if row is None or row.project_id != project_id: raise HTTPException(404, '本工程本地配音任务不存在')
    await chapter_owned(db, project_id, row.chapter_id)
    if canonical_hash(row.intent) != row.intent_hash: raise HTTPException(409, '本地配音原始意图校验失败')
    return row


async def job_view(db, row):
    """A generated voice is only a candidate; this status never claims adoption or speech quality."""
    intent = row.intent
    candidate = None
    if row.state == 'ready':
        product = await db.get(LocalSpeechProduct, row.id)
        if product is None or (product.project_id, product.intent_hash) != (row.project_id, row.intent_hash): raise HTTPException(409, '本地配音结果记录不完整')
        actual = await db.get(SoundCandidate, product.candidate_id)
        if actual is None or (actual.project_id, actual.chapter_id, actual.origin) != (row.project_id, row.chapter_id, 'local_speech') or actual.evidence.get('local_speech') != intent:
            raise HTTPException(409, '本地配音候选来源记录不完整')
        candidate = candidate_view(actual)
    return {'id': row.id, 'project_id': row.project_id, 'chapter_id': row.chapter_id, 'task_id': row.task_id,
        'state': row.state, 'reason': row.reason, 'attempts': row.attempts, 'plan_id': intent['plan_id'], 'dialogue_id': intent['dialogue_id'],
        'text': intent['text'], 'speaker_id': intent['speaker_id'], 'voice': intent['voice'], 'rate': intent['rate'],
        'profile': PROFILE, 'external_fee_fen': 0, 'candidate': candidate}


async def get_job(factory, project_id, identifier):
    """Read historical work even if the selected system voice is later removed or execution disabled."""
    async with factory() as db: return await job_view(db, await owned_job(db, project_id, identifier))


async def history(factory, project_id, chapter_id, engine=None):
    """Return bounded actual chapter jobs and current explicit offline engine availability."""
    async with factory() as db:
        await chapter_owned(db, project_id, chapter_id)
        rows = (await db.execute(select(LocalSpeechJob).where(LocalSpeechJob.project_id == project_id, LocalSpeechJob.chapter_id == chapter_id).order_by(LocalSpeechJob.created_at.desc(), LocalSpeechJob.id).limit(201))).scalars().all()
        if len(rows) > 200: raise HTTPException(409, '本章本地配音任务超过200项')
        jobs = [await job_view(db, row) for row in rows]
    return {'catalog': await (engine or LocalSpeechEngine()).catalog(), 'jobs': jobs}


async def replay(db, project_id, body):
    """Reuse a prior original intent before probing current voices, files, plan state or tools."""
    row = await db.scalar(select(LocalSpeechJob).where(LocalSpeechJob.project_id == project_id, LocalSpeechJob.request_key == body.request_key))
    if row is None: return None
    if row.request_hash != canonical_hash({'domain': 'local-speech-request-v1', **body.model_dump()}): raise HTTPException(409, '相同请求标识已用于另一配音意图')
    return await owned_job(db, project_id, row.id)


async def create_job(factory, project_id, body, engine=None):
    """Freeze exact original text/speaker and an explicitly selected installed voice, at zero external fee."""
    async with factory() as db:
        previous = await replay(db, project_id, body)
        if previous: return await job_view(db, previous)
        if settings.workspace_runtime_locked or not settings.local_speech_enabled: raise HTTPException(503, '当前空间尚未启用本地配音执行')
        plan = await owned_plan(db, project_id, body.plan_id)
        chapter_id = plan.payload['chapter_id']
        await chapter_owned(db, project_id, chapter_id)
        lines = [item for item in original_items(plan, await plan_script(db, project_id, plan)) if item['kind'] == 'dialogue' and item['item_id'] == body.dialogue_id]
        if len(lines) != 1 or not lines[0]['text'].strip() or len(lines[0]['text']) > 1000: raise HTTPException(422, '请选择本章固定计划内1至1000字的原对白')
        line, plan_hash = copy.deepcopy(lines[0]), plan.content_hash
    catalog = await (engine or LocalSpeechEngine()).catalog()
    voices = [voice for voice in catalog['voices'] if voice['id'] == body.voice_id]
    if not catalog['enabled'] or catalog['profile'] != body.profile or catalog['external_fee_fen'] != 0 or len(voices) != 1: raise HTTPException(503, '所选本地声音不可用，请重新明确选择；不会改用云端服务')
    identifier, task_id = str(uuid.uuid4()), str(uuid.uuid4())
    intent = {'job_id': identifier, 'project_id': project_id, 'chapter_id': chapter_id, 'plan_id': body.plan_id, 'plan_hash': plan_hash,
        'dialogue_id': body.dialogue_id, 'text': line['text'], 'speaker_id': line['speaker_id'], 'voice': voices[0], 'rate': body.rate,
        'profile': PROFILE, 'external_fee_fen': 0}
    async with factory() as db:
        async with transaction(db):
            previous = await replay(db, project_id, body)
            if previous: return await job_view(db, previous)
            if settings.workspace_runtime_locked or not settings.local_speech_enabled: raise HTTPException(503, '当前空间尚未启用本地配音执行')
            plan = await owned_plan(db, project_id, body.plan_id)
            if plan.content_hash != plan_hash or plan.payload['chapter_id'] != chapter_id: raise HTTPException(409, '原对白计划在提交期间改变')
            count = len((await db.execute(select(LocalSpeechJob.id).where(LocalSpeechJob.project_id == project_id, LocalSpeechJob.chapter_id == chapter_id).limit(201))).all())
            candidates = len((await db.execute(select(SoundCandidate.id).where(SoundCandidate.project_id == project_id, SoundCandidate.chapter_id == chapter_id).limit(201))).all())
            if count >= 200 or candidates >= 200: raise HTTPException(409, '本章配音任务或声音候选已达200项上限')
            db.add(GenerationTask(id=task_id, mode='async_polling', task_kind='local_speech', status='pending', progress=0,
                payload={'project_id': project_id, 'chapter_id': chapter_id, 'speech_job_id': identifier}, executor_type='local_speech_v1'))
            await db.flush()
            row = LocalSpeechJob(id=identifier, project_id=project_id, chapter_id=chapter_id, task_id=task_id, request_key=body.request_key,
                request_hash=canonical_hash({'domain': 'local-speech-request-v1', **body.model_dump()}), intent_hash=canonical_hash(intent), intent=intent,
                state='queued', reason='', attempts=0, lease_until=0, cancel_requested=False)
            db.add(row)
            db.add(GenerationTaskLink(task_id=task_id, resource_type='local_speech', relation_type='chapter', relation_entity_id=chapter_id))
            await db.flush()
    return await get_job(factory, project_id, identifier)


async def cancel_job(factory, project_id, identifier):
    """Only this local job owns cancellation; no provider accounting or picture adoption is touched."""
    async with factory() as db:
        async with transaction(db):
            job = await owned_job(db, project_id, identifier)
            if job.state in ('queued', 'running'):
                job.cancel_requested = True
                task = await db.get(GenerationTask, job.task_id); task.cancel_requested = True
                if job.state == 'queued':
                    job.state, job.reason, task.status = 'cancelled', '用户取消了本地配音', 'cancelled'
                    task.error = job.reason
    return await get_job(factory, project_id, identifier)


class LocalSpeechWorker:
    """One fenced offline synthesis lane with bounded recovery and owned child cleanup."""
    def __init__(self, factory, engine=None, clock=time.time):
        """Inject only a trusted application-owned engine; HTTP cannot select a command or adapter."""
        self.factory, self.engine, self.clock = factory, engine or LocalSpeechEngine(), clock
        self.loop_task = None

    async def start(self):
        """Start only on an explicitly enabled supported offline host."""
        if self.loop_task is None and available(): self.loop_task = asyncio.create_task(self._loop())

    async def stop(self):
        """Cancel and join the scanner; step joins its active process before requeueing."""
        if self.loop_task is not None:
            self.loop_task.cancel()
            try: await self.loop_task
            except asyncio.CancelledError: pass
            self.loop_task = None

    async def _loop(self):
        """Recover queued or expired local jobs; never create a cloud request."""
        while True:
            try: worked = await self.step()
            except asyncio.CancelledError: raise
            except Exception:
                LOGGER.exception('Local speech scanner failed'); worked = False
            await asyncio.sleep(0.1 if worked else 0.5)

    async def claim(self):
        """CAS one original local job under the global speech lease, with at most three attempts."""
        if not settings.local_speech_enabled or settings.workspace_runtime_locked: return None
        async with self.factory() as db:
            async with transaction(db):
                slot = await db.get(LocalSpeechSlot, 1)
                if slot is None: raise RuntimeError('Run local speech migration first')
                now = self.clock()
                if slot.job_id is not None and slot.lease_until > now: return None
                jobs = (await db.execute(select(LocalSpeechJob).where(LocalSpeechJob.state.in_(('queued', 'running')), LocalSpeechJob.lease_until <= now).order_by(LocalSpeechJob.created_at, LocalSpeechJob.id).limit(200))).scalars().all()
                for job in jobs:
                    task = await db.get(GenerationTask, job.task_id)
                    if job.cancel_requested or task.cancel_requested:
                        job.state, job.reason, task.status = 'cancelled', '用户取消了本地配音', 'cancelled'; task.error = job.reason
                        continue
                    if job.attempts >= 3:
                        job.state, job.reason, task.status = 'failed', '本地配音恢复已达3次，请检查声音后新建任务', 'failed'; task.error = job.reason
                        continue
                    if canonical_hash(job.intent) != job.intent_hash: raise HTTPException(409, '本地配音意图校验失败')
                    token = str(uuid.uuid4())
                    job.state, job.reason, job.lease_token, job.lease_until, job.attempts = 'running', '', token, now + 120, job.attempts + 1
                    slot.job_id, slot.token, slot.lease_until = job.id, token, job.lease_until
                    task.status, task.progress = 'running', 10
                    return job.id, token
                slot.job_id, slot.token, slot.lease_until = None, None, 0
        return None

    async def fenced(self, db, identifier, token):
        """Require both the original job and global slot to retain live execution authority."""
        job, slot = await db.get(LocalSpeechJob, identifier), await db.get(LocalSpeechSlot, 1)
        if job is None or slot is None or job.state != 'running' or job.lease_token != token or job.lease_until <= self.clock() or (slot.job_id, slot.token) != (identifier, token) or slot.lease_until <= self.clock():
            raise HTTPException(409, '本地配音执行权已过期')
        return job, slot

    async def finish_failure(self, identifier, token, reason, requeue=False):
        """Release this attempt only, preserving a newer lease or a committed ready candidate."""
        async with self.factory() as db:
            async with transaction(db):
                try: job, slot = await self.fenced(db, identifier, token)
                except HTTPException: return
                task = await db.get(GenerationTask, job.task_id)
                cancelled = job.cancel_requested or task.cancel_requested
                job.state = 'cancelled' if cancelled else 'queued' if requeue else 'failed'
                job.reason = '用户取消了本地配音' if cancelled else reason
                task.status, task.error, task.progress = {'cancelled': 'cancelled', 'queued': 'pending', 'failed': 'failed'}[job.state], job.reason, 0
                job.lease_token, job.lease_until, slot.job_id, slot.token, slot.lease_until = None, 0, None, None, 0

    async def publish(self, identifier, token, folder, facts):
        """Publish actual decoded bytes and one candidate atomically; uncertain commits never delete media."""
        path = Path(folder)/'voice.wav'
        root = Path(settings.production_media_root).resolve()
        if not path.resolve().is_relative_to(root) or stat_identity(path) != facts['stat'] or facts['size'] != facts['stat'][0]: raise HTTPException(409, '本地配音产物已改变')
        async with self.factory() as db:
            async with transaction(db):
                job, slot = await self.fenced(db, identifier, token)
                task = await db.get(GenerationTask, job.task_id)
                if job.cancel_requested or task.cancel_requested: raise HTTPException(409, '本地配音已请求取消')
                await chapter_owned(db, job.project_id, job.chapter_id)
                if canonical_hash(job.intent) != job.intent_hash: raise HTTPException(409, '本地配音原始意图不符')
                count = len((await db.execute(select(SoundCandidate.id).where(SoundCandidate.project_id == job.project_id, SoundCandidate.chapter_id == job.chapter_id).limit(201))).all())
                if count >= 200: raise HTTPException(409, '本章声音候选已达200项上限')
                if stat_identity(path) != facts['stat']: raise HTTPException(409, '产物在提交期间改变')
                media_id, candidate_id = str(uuid.uuid4()), str(uuid.uuid4())
                relative = path.resolve().relative_to(root).as_posix()
                db.add(FileItem(id=media_id, type='audio', name='voice.wav', thumbnail='', tags=['local_speech'], storage_key='local-production/'+relative))
                await db.flush()
                db.add(ProductionMedia(id=media_id, project_id=job.project_id, name='voice.wav', mime='audio/wav', sha256=facts['sha256'], size=facts['size'], relative_path=relative))
                await db.flush()
                evidence = {'id': media_id, 'mime': 'audio/wav', 'sha256': facts['sha256'], 'size': facts['size'], 'relative_path': relative,
                    'stat': facts['stat'], 'audio': facts['audio'], 'local_speech': copy.deepcopy(job.intent)}
                candidate = SoundCandidate(id=candidate_id, project_id=job.project_id, chapter_id=job.chapter_id, media_id=media_id,
                    request_key='local-speech:'+job.id, intent_hash=job.intent_hash, kind='voice', name=('系统配音 · '+job.intent['voice']['name'])[:120], origin='local_speech', evidence=evidence)
                db.add(candidate); await db.flush()
                db.add(LocalSpeechProduct(job_id=job.id, project_id=job.project_id, candidate_id=candidate_id, intent_hash=job.intent_hash)); await db.flush()
                if job.lease_until <= self.clock() or slot.lease_until <= self.clock(): raise HTTPException(409, '提交前本地配音执行权已过期')
                job.state, job.reason, job.lease_token, job.lease_until = 'ready', '', None, 0
                task.status, task.progress, task.error = 'succeeded', 100, ''
                task.result = {'speech_job_id': job.id, 'candidate_id': candidate_id}
                slot.job_id, slot.token, slot.lease_until = None, None, 0

    async def step(self):
        """Run one bounded offline attempt while monitoring cancellation and original lease ownership."""
        claimed = await self.claim()
        if claimed is None: return False
        identifier, token = claimed
        render_task = None
        try:
            async with self.factory() as db:
                job = await db.get(LocalSpeechJob, identifier)
                intent = copy.deepcopy(job.intent)
            folder = media_path('local-speech-attempts/'+str(uuid.uuid4()))
            folder.mkdir(parents=True, exist_ok=False)
            render_task = asyncio.create_task(self.engine.render(intent, folder))
            while not render_task.done():
                await asyncio.wait({render_task}, timeout=0.25)
                async with self.factory() as db:
                    job, slot = await self.fenced(db, identifier, token)
                    task = await db.get(GenerationTask, job.task_id)
                    if job.cancel_requested or task.cancel_requested or settings.workspace_runtime_locked or not settings.local_speech_enabled:
                        raise HTTPException(409, '本地配音取消或执行已暂停')
            facts = await render_task
            await self.publish(identifier, token, folder, facts)
        except asyncio.CancelledError:
            await cancel_and_join(render_task)
            await self.finish_failure(identifier, token, '应用关闭，等待本地恢复', requeue=True)
            raise
        except Exception:
            await cancel_and_join(render_task)
            await self.finish_failure(identifier, token, '本地配音失败：声音不可用、文件校验失败或处理超限；未采用任何结果')
        return True
