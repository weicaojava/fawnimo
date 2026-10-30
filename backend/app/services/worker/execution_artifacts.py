"""Publish validated bytes under a live execution fence and an immutable media ID."""
import asyncio
import hashlib
import uuid
from pathlib import Path
from fastapi import HTTPException
from sqlalchemy import select, update
from app.config import settings
from app.models.execution import ExecutionJob, ExecutionSlot
from app.models.task import GenerationTask
from app.models.production import ProductionMedia
from app.models.studio import FileItem
from app.services.studio.production_media import inspect_media, media_path, probe_media
from app.core.contracts.execution_artifact import DownloadedArtifact
from app.services.worker.database_operation import joined_database


async def publish_artifact(factory, clock, job_id: str, token: str, artifact: DownloadedArtifact) -> str:
    """Validate outside the write lock, then publish DB references under a CAS fence.

    Each attempt has an exclusive random path. A crash before DB commit may leave
    an unreferenced file, but cannot replace a committed artifact. A retry finds
    the preallocated media ID and verifies its bytes before declaring success.
    """
    if not isinstance(artifact,DownloadedArtifact) or len(artifact.data)>settings.production_upload_limit:
        raise HTTPException(400,'生成结果为空、无效或超过大小限制')
    mime=inspect_media(artifact.name,artifact.data)
    await asyncio.to_thread(probe_media,artifact.name,artifact.data)
    checksum=hashlib.sha256(artifact.data).hexdigest()
    return await _publish_validated(factory,clock,job_id,token,artifact,mime,checksum)


@joined_database
async def _publish_validated(factory,clock,job_id,token,artifact,mime,checksum):
    """Join the short fenced DB/file publication and its commit-ambiguity cleanup, after all decoding has finished."""
    path=None;committed=False;media_id=None
    try:
        async with factory() as db:
            fenced=await db.execute(update(ExecutionJob).where(ExecutionJob.id==job_id,ExecutionJob.lease_token==token,ExecutionJob.lease_until>clock(),ExecutionJob.state.in_({'downloading','ready'})).values(reason=''))
            if not fenced.rowcount:raise HTTPException(409,'任务执行权已过期，拒绝保存结果')
            job=await db.get(ExecutionJob,job_id)
            media_id=job.media_id
            existing=await db.get(ProductionMedia,job.media_id)
            if existing:
                if existing.project_id!=job.project_id or existing.sha256!=checksum or existing.size!=len(artifact.data):raise HTTPException(409,'生成结果与既有产物冲突')
                existing_path=media_path(existing.relative_path)
                if not existing_path.is_file() or existing_path.stat().st_size!=existing.size or hashlib.sha256(existing_path.read_bytes()).hexdigest()!=existing.sha256:raise HTTPException(409,'已保存的生成结果损坏')
                await db.commit();return existing.id
            relative=f'{hashlib.sha256(job.project_id.encode()).hexdigest()}/{uuid.uuid4()}{Path(artifact.name).suffix.lower()}'
            path=media_path(relative);path.parent.mkdir(parents=True,exist_ok=True)
            with path.open('xb') as stream:stream.write(artifact.data)
            db.add(FileItem(id=job.media_id,type=mime.split('/')[0],name=Path(artifact.name).name,thumbnail='',tags=['execution_output'],storage_key='local-production/'+relative))
            await db.flush()
            db.add(ProductionMedia(id=job.media_id,project_id=job.project_id,name=Path(artifact.name).name,mime=mime,sha256=checksum,size=len(artifact.data),relative_path=relative))
            job.state='ready'
            await db.execute(update(GenerationTask).where(GenerationTask.id==job.task_id).values(status='succeeded',progress=100,result={'media_id':job.media_id},error=''))
            await db.execute(update(ExecutionSlot).where(ExecutionSlot.id==1,ExecutionSlot.token==token,ExecutionSlot.job_id==job_id).values(job_id=None,token=None,lease_until=0))
            if job.lease_until<=clock():raise HTTPException(409,'保存期间执行权已过期')
            await db.commit();committed=True;return job.media_id
    finally:
        if path is not None and not committed:
            # A commit can succeed while its acknowledgement is lost. Never
            # delete bytes until a fresh database read proves they are unowned.
            try:
                async with factory() as check:
                    persisted=await check.get(ProductionMedia,media_id)
                    unowned=persisted is None or media_path(persisted.relative_path)!=path
                if unowned:path.unlink(missing_ok=True)
            except Exception:
                pass  # Retain a possible orphan for later audited recovery.
