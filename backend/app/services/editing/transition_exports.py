"""Independent transition-v2 local rendering: immutable sources, fenced publication, no model calls."""
import asyncio
import copy
import hashlib
import logging
import time
import uuid
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from app.config import settings
from app.models.editing_v2 import TransitionExportJob as EditExportJob, TransitionExportProduct as EditExportProduct, TransitionExportSlot as EditExportSlot
from app.models.production import ProductionMedia
from app.models.studio import FileItem
from app.models.task import GenerationTask
from app.models.task_links import GenerationTaskLink
from app.services.editing.media import canonical_hash, recheck_media, verify_publication_sources
from app.services.editing.transition_media import run_child
from app.services.studio.production_media import media_path
from app.services.worker.execution_budget import transaction

KINDS = {'video': ('video.mp4', 'video/mp4'), 'subtitles_srt': ('subtitles.srt', 'application/x-subrip'), 'subtitles_vtt': ('subtitles.vtt', 'text/vtt'), 'package': ('export.zip', 'application/zip')}
LOGGER = logging.getLogger(__name__)


def enabled(ffmpeg: str) -> bool:
    """A local trusted tool can be enabled independently of every model account."""
    return settings.editing_export_enabled and bool(ffmpeg) and Path(ffmpeg).is_file()


async def owned_job(db, project_id: str, identifier: str):
    """Resolve an owned job before revealing status or artifact identities."""
    job = await db.get(EditExportJob, identifier)
    if job is None or job.project_id != project_id:
        raise HTTPException(404, '本工程导出任务不存在')
    return job


def file_hash(path: Path) -> str:
    """Hash all output bytes while keeping memory bounded for downloads and publication."""
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(block)
    return checksum.hexdigest()


def verify_file(item: dict) -> Path:
    """Never expose missing/changed historical outputs as successful downloads."""
    try:
        path = media_path(item['relative_path'])
        if not path.is_file() or path.stat().st_size != item['size'] or file_hash(path) != item['sha256']:
            raise ValueError('Output hash mismatch')
        return path
    except (ValueError, OSError) as exc:
        raise HTTPException(409, '导出产物缺失或校验失败，请保留任务记录并检查备份') from exc


async def job_view(db, job) -> dict:
    """Return safe public status; ready files are verified rather than guessed from a path."""
    files = []
    if job.state == 'ready':
        product = await db.get(EditExportProduct, job.id)
        if product is None or product.project_id != job.project_id:
            raise HTTPException(409, '导出完成记录不完整')
        for kind, (name, _mime) in KINDS.items():
            item = product.artifacts[kind]
            await asyncio.to_thread(verify_file, item)
            files.append({'kind': kind, 'name': name, 'sha256': item['sha256'], 'size': item['size'], 'content_url': f'{settings.api_v1_prefix}/editing-v2/projects/{job.project_id}/exports/{job.id}/content/{kind}'})
    return {'id': job.id, 'project_id': job.project_id, 'chapter_id': job.chapter_id, 'publication_id': job.publication_id, 'publication_hash': job.publication_hash, 'task_id': job.task_id, 'state': job.state, 'reason': job.reason, 'attempts': job.attempts, 'files': files, 'model_calls': 0}


async def get_export(factory, project_id: str, identifier: str) -> dict:
    """Read old outcomes without requiring a currently configured renderer or model runtime."""
    async with factory() as db:
        return await job_view(db, await owned_job(db, project_id, identifier))


async def list_exports(factory, project_id: str, chapter_id: str, ffmpeg: str) -> dict:
    """List a bounded complete chapter history and the local tool's current availability."""
    from app.services.script_preparation.service import chapter_owned
    async with factory() as db:
        await chapter_owned(db, project_id, chapter_id)
        jobs = (await db.execute(select(EditExportJob).where(EditExportJob.project_id == project_id, EditExportJob.chapter_id == chapter_id).order_by(EditExportJob.created_at.desc()).limit(101))).scalars().all()
        if len(jobs) > 100:
            raise HTTPException(409, '导出历史超过本版100条上限，请分章管理')
        return {'enabled': enabled(ffmpeg), 'reason': '' if enabled(ffmpeg) else '尚未启用本地导出或配置受信媒体工具', 'exports': [await job_view(db, job) for job in jobs]}


async def replay(db, project_id: str, publication, request_key: str):
    """Resolve an exact earlier intention before current tool/source eligibility checks."""
    job = (await db.execute(select(EditExportJob).where(EditExportJob.project_id == project_id, EditExportJob.request_key == request_key))).scalar_one_or_none()
    if job is None:
        return None
    expected = canonical_hash({'domain': 'transition-local-export-v2', 'publication_id': publication.id, 'publication_hash': publication.content_hash, 'profile': 'portrait-360p30-linear-crossfade-v2'})
    if (job.publication_id, job.publication_hash, job.intent_hash) != (publication.id, publication.content_hash, expected):
        raise HTTPException(409, '相同请求标识已用于另一个时间线版本')
    return job


async def create_export(factory, project_id: str, publication_id: str, request_key: str, ffmpeg: str) -> dict:
    """Persist one local intention after complete source verification and a short CAS transaction."""
    from app.services.editing.transitions import owned_publication
    if not isinstance(request_key, str) or not request_key.strip() or len(request_key) > 128:
        raise HTTPException(422, '请求标识必须为1至128字符')
    async with factory() as db:
        publication = await owned_publication(db, project_id, publication_id)
        previous = await replay(db, project_id, publication, request_key)
        if previous is not None:
            return await job_view(db, previous)
        if not enabled(ffmpeg):
            raise HTTPException(503, '尚未启用本地导出或配置受信媒体工具')
        payload_hash = publication.content_hash
        payload = copy.deepcopy(publication.payload)
    proof = await verify_publication_sources(factory, project_id, payload)
    async with factory() as db:
        async with transaction(db):
            publication = await owned_publication(db, project_id, publication_id)
            previous = await replay(db, project_id, publication, request_key)
            if previous is not None:
                identifier = previous.id
            else:
                if not enabled(ffmpeg) or publication.content_hash != payload_hash:
                    raise HTTPException(409, '时间线或本地工具配置已改变')
                for facts in proof.values():
                    await recheck_media(db, project_id, facts)
                identifier, task_id = str(uuid.uuid4()), str(uuid.uuid4())
                db.add(GenerationTask(id=task_id, mode='async_polling', task_kind='local_export', status='pending', progress=0, payload={'project_id': project_id, 'chapter_id': publication.chapter_id, 'export_id': identifier}, executor_type='local_editing_v2'))
                await db.flush()
                db.add(EditExportJob(id=identifier, project_id=project_id, chapter_id=publication.chapter_id, publication_id=publication.id, publication_hash=publication.content_hash, task_id=task_id, request_key=request_key, intent_hash=canonical_hash({'domain': 'transition-local-export-v2', 'publication_id': publication.id, 'publication_hash': publication.content_hash, 'profile': 'portrait-360p30-linear-crossfade-v2'}), state='queued', reason='', attempts=0, lease_until=0, cancel_requested=False))
                db.add(GenerationTaskLink(task_id=task_id, resource_type='local_export', relation_type='chapter', relation_entity_id=publication.chapter_id))
                await db.flush()
    return await get_export(factory, project_id, identifier)


async def cancel_export(factory, project_id: str, identifier: str) -> dict:
    """Persist cancellation; the live worker immediately terminates its helper tree."""
    async with factory() as db:
        async with transaction(db):
            job = await owned_job(db, project_id, identifier)
            if job.state in ('queued', 'running'):
                job.cancel_requested = True
                task = await db.get(GenerationTask, job.task_id)
                task.cancel_requested = True
                if job.state == 'queued':
                    job.state, job.reason = 'cancelled', '用户取消了本地导出'
                    task.status, task.error = 'cancelled', job.reason
    return await get_export(factory, project_id, identifier)


async def content_file(factory, project_id: str, identifier: str, kind: str):
    """Resolve a verified ready artifact using only an owned job and a fixed kind whitelist."""
    if kind not in KINDS:
        raise HTTPException(404, '导出文件类型不存在')
    async with factory() as db:
        job = await owned_job(db, project_id, identifier)
        if job.state != 'ready':
            raise HTTPException(409, '导出尚未完成')
        product = await db.get(EditExportProduct, job.id)
        if product is None or product.project_id != project_id:
            raise HTTPException(409, '导出产物记录不完整')
        path = await asyncio.to_thread(verify_file, product.artifacts[kind])
        name, mime = KINDS[kind]
        return path, name, mime


class TransitionExportWorker:
    """Single-slot durable renderer with bounded child lifetime and CAS publication fencing."""
    def __init__(self, factory, ffmpeg: str, clock=time.time):
        """Bind one database and a trusted executable; no model settings or budget are accepted."""
        self.factory, self.ffmpeg, self.clock = factory, ffmpeg, clock
        self.loop_task = None

    async def start(self):
        """Launch a scanner only when the explicitly local tool is available."""
        if self.loop_task is None and enabled(self.ffmpeg):
            self.loop_task = asyncio.create_task(self._loop())

    async def stop(self):
        """Cancel and join the scanner, which kills its currently active helper in finally."""
        if self.loop_task is not None:
            self.loop_task.cancel()
            try:
                await self.loop_task
            except asyncio.CancelledError:
                pass
            self.loop_task = None

    async def _loop(self):
        """Resume queued/expired local work without dispatching a model or concealing failures."""
        while True:
            try:
                worked = await self.step()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception('Local export scanner failed')
                worked = False
            await asyncio.sleep(0.1 if worked else 0.5)

    async def claim(self):
        """Claim one persisted intention under a global slot; expired attempts get a fresh token."""
        if not enabled(self.ffmpeg):
            return None
        async with self.factory() as db:
            async with transaction(db):
                slot = await db.get(EditExportSlot, 1)
                if slot is None:
                    raise RuntimeError('Run editing export migration first')
                now = self.clock()
                if slot.job_id is not None and slot.lease_until > now:
                    return None
                jobs = (await db.execute(select(EditExportJob).where(EditExportJob.state.in_(('queued', 'running')), EditExportJob.lease_until <= now).order_by(EditExportJob.created_at, EditExportJob.id).limit(100))).scalars().all()
                for job in jobs:
                    task = await db.get(GenerationTask, job.task_id)
                    if job.cancel_requested or task.cancel_requested:
                        job.state, job.reason, task.status = 'cancelled', '用户取消了本地导出', 'cancelled'
                        continue
                    if job.attempts >= 3:
                        job.state, job.reason, task.status = 'failed', '导出恢复次数已达3次，请检查本地工具后新建导出', 'failed'
                        task.error = job.reason
                        continue
                    token = str(uuid.uuid4())
                    job.state, job.reason = 'running', ''
                    job.lease_token, job.lease_until, job.attempts = token, now + 180, job.attempts + 1
                    slot.job_id, slot.token, slot.lease_until = job.id, token, job.lease_until
                    task.status, task.progress = 'running', 10
                    return job.id, token
                slot.job_id, slot.token, slot.lease_until = None, None, 0
        return None

    async def _fenced(self, db, identifier, token):
        """A live job and the matching global lease are both required for every writeback."""
        job = await db.get(EditExportJob, identifier)
        slot = await db.get(EditExportSlot, 1)
        if job is None or job.state != 'running' or job.lease_token != token or job.lease_until <= self.clock() or slot.job_id != identifier or slot.token != token or slot.lease_until <= self.clock():
            raise HTTPException(409, '导出执行权已过期')
        return job, slot

    async def finish_failure(self, identifier, token, reason, requeue=False):
        """Release only this attempt's slot, preserving newer work and immutable ready receipts."""
        async with self.factory() as db:
            async with transaction(db):
                try:
                    job, slot = await self._fenced(db, identifier, token)
                except HTTPException:
                    return
                task = await db.get(GenerationTask, job.task_id)
                cancelled = job.cancel_requested or task.cancel_requested
                job.state = 'cancelled' if cancelled else ('queued' if requeue else 'failed')
                job.reason = '用户取消了本地导出' if cancelled else reason
                task.status = {'cancelled': 'cancelled', 'queued': 'pending', 'failed': 'failed'}[job.state]
                task.error, task.progress = job.reason, 0
                job.lease_until, job.lease_token = 0, None
                slot.job_id, slot.token, slot.lease_until = None, None, 0

    async def publish(self, identifier, token, folder: Path, result: dict):
        """Publish verified private outputs atomically; uncertain commits never delete artifacts."""
        artifacts = {}
        root = Path(settings.production_media_root).resolve()
        for kind, (name, _mime) in KINDS.items():
            item = result['files'][name]
            target = (folder / name).resolve()
            if not target.is_relative_to(root):
                raise HTTPException(409, '导出产物路径无效')
            artifacts[kind] = {'relative_path': target.relative_to(root).as_posix(), 'sha256': item['sha256'], 'size': item['size']}
            await asyncio.to_thread(verify_file, artifacts[kind])
        async with self.factory() as db:
            async with transaction(db):
                job, slot = await self._fenced(db, identifier, token)
                task = await db.get(GenerationTask, job.task_id)
                if job.cancel_requested or task.cancel_requested:
                    raise HTTPException(409, '任务已请求取消，不发布产物')
                media_id = str(uuid.uuid4())
                video = artifacts['video']
                db.add(FileItem(id=media_id, type='video', name='video.mp4', thumbnail='', tags=['local_export'], storage_key='local-production/' + video['relative_path']))
                await db.flush()
                db.add(ProductionMedia(id=media_id, project_id=job.project_id, name='video.mp4', mime='video/mp4', sha256=video['sha256'], size=video['size'], relative_path=video['relative_path']))
                await db.flush()
                db.add(EditExportProduct(job_id=job.id, project_id=job.project_id, media_id=media_id, artifacts=artifacts, facts=result['facts']))
                await db.flush()
                if job.lease_until <= self.clock() or slot.lease_until <= self.clock():
                    raise HTTPException(409, '产物提交前执行权已过期')
                job.state, job.reason, job.lease_until, job.lease_token = 'ready', '', 0, None
                task.status, task.progress, task.result, task.error = 'succeeded', 100, {'export_id': job.id, 'media_id': media_id}, ''
                slot.job_id, slot.token, slot.lease_until = None, None, 0
        # Files stay at exclusive random paths even if commit acknowledgement is
        # lost. Never delete possible committed products as an exception cleanup.

    async def step(self):
        """Perform one attempt with immediate cancellation monitoring and bounded process cleanup."""
        from app.services.editing.transitions import owned_publication
        claimed = await self.claim()
        if claimed is None:
            return False
        identifier, token = claimed
        render_task = None
        try:
            async with self.factory() as db:
                job = await db.get(EditExportJob, identifier)
                publication = await owned_publication(db, job.project_id, job.publication_id)
                if publication.content_hash != job.publication_hash:
                    raise HTTPException(409, '锁定时间线校验失败')
                payload = copy.deepcopy(publication.payload)
                for clip in payload['clips']:
                    source = clip['source']
                    row = await db.get(ProductionMedia, source['id'])
                    if row is None or (row.project_id, row.sha256, row.size, row.mime) != (job.project_id, source['sha256'], source['size'], 'video/mp4'):
                        raise HTTPException(409, '锁定素材归属或校验信息不匹配')
                    clip['path'] = str(media_path(row.relative_path))
                parameters = {'mode': 'render', 'payload': payload, 'publication_id': publication.id, 'publication_hash': publication.content_hash, 'ffmpeg': str(Path(self.ffmpeg).resolve())}
            folder = media_path(f'transition-v2-attempts/{uuid.uuid4()}')
            folder.mkdir(parents=True, exist_ok=False)
            parameters['folder'] = str(folder)
            render_task = asyncio.create_task(run_child(parameters, 120))
            while not render_task.done():
                await asyncio.wait({render_task}, timeout=0.25)
                async with self.factory() as db:
                    job = await db.get(EditExportJob, identifier)
                    task = await db.get(GenerationTask, job.task_id)
                    if job.cancel_requested or task.cancel_requested or job.lease_token != token or job.lease_until <= self.clock():
                        render_task.cancel()
                        try:
                            await render_task
                        except asyncio.CancelledError:
                            pass
                        raise HTTPException(409, '任务取消或执行权过期')
            result = await render_task
            await self.publish(identifier, token, folder, result)
        except asyncio.CancelledError:
            if render_task is not None and not render_task.done():
                render_task.cancel()
                try:
                    await render_task
                except asyncio.CancelledError:
                    pass
            await self.finish_failure(identifier, token, '应用关闭，等待本地恢复', requeue=True)
            raise
        except Exception:
            await self.finish_failure(identifier, token, '导出失败：素材缺失、时钟不符、工具错误或处理超限；未发布半成品')
        return True
