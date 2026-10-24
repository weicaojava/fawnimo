"""Real filesystem/SQLite publication tests; fixture bytes are not model output."""
import struct
import zlib
import pytest
from fastapi import HTTPException
from sqlalchemy import select, update
from app.config import settings
from app.models.execution import ExecutionJob
from app.models.production import ProductionMedia
from app.services.studio.production_media import media_path
from app.services.worker.execution_artifacts import DownloadedArtifact, publish_artifact
from tests.test_durable_execution import setup


def png():
    """Encode a real one-pixel image so the configured decoder checks actual bytes."""
    def chunk(kind,data):
        """Build a PNG chunk with its required integrity checksum."""
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\x00\xff\x00\x00'))+chunk(b'IEND',b'')


@pytest.mark.asyncio
async def test_publication_idempotence_stale_owner_and_corruption(tmp_path,monkeypatch):
    """A lost worker cannot publish; restart reuses the committed ID and checks bytes."""
    monkeypatch.setattr(settings,'production_media_root',str(tmp_path/'media'))
    from pathlib import Path
    import os
    probe=Path(os.environ.get('TEST_MEDIA_PROBE',str(Path(__file__).resolve().parents[4]/'ffmpeg.exe')))
    assert probe.is_file()
    monkeypatch.setattr(settings,'production_probe_executable',str(probe))
    engine,factory,adapter,clock,worker,_=await setup(tmp_path)
    try:
        job_id=await worker.create('p','artifact',{'prompt_revision':1})
        job,token=await worker._claim(job_id)
        assert await worker._update(job_id,token,state='downloading')
        artifact=DownloadedArtifact('fixture.png',png())
        with pytest.raises(HTTPException):await publish_artifact(factory,clock,job_id,'stale',artifact)
        assert not list((tmp_path/'media').rglob('*.png'))
        media_id=await publish_artifact(factory,clock,job_id,token,artifact)
        assert await publish_artifact(factory,clock,job_id,token,artifact)==media_id
        async with factory() as db:
            rows=(await db.execute(select(ProductionMedia))).scalars().all()
            assert len(rows)==1 and rows[0].project_id=='p'
            path=media_path(rows[0].relative_path)
        assert len(list((tmp_path/'media').rglob('*.png')))==1
        path.write_bytes(b'corrupt fixture')
        with pytest.raises(HTTPException):await publish_artifact(factory,clock,job_id,token,artifact)
        clock.advance()
        with pytest.raises(HTTPException):await publish_artifact(factory,clock,job_id,token,artifact)
    finally:await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('cancelled',[False,True])
async def test_committed_artifact_survives_lost_commit_ack(tmp_path,monkeypatch,cancelled):
    """Commit real SQLite changes, then lose the acknowledgement without deleting bytes."""
    import asyncio
    import hashlib
    import os
    from pathlib import Path
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.models.execution import ExecutionSlot
    from app.models.task import GenerationTask
    monkeypatch.setattr(settings,'production_media_root',str(tmp_path/'media'))
    probe=Path(os.environ.get('TEST_MEDIA_PROBE',str(Path(__file__).resolve().parents[4]/'ffmpeg.exe')))
    assert probe.is_file()
    monkeypatch.setattr(settings,'production_probe_executable',str(probe))
    engine,factory,adapter,clock,worker,_=await setup(tmp_path)
    try:
        job_id=await worker.create('p','lost-commit-ack',{'prompt_revision':1})
        job,token=await worker._claim(job_id)
        assert await worker._update(job_id,token,state='downloading')
        artifact=DownloadedArtifact('fixture.png',png())
        original_commit=AsyncSession.commit
        injected=False
        async def commit_then_lose_ack(session):
            """Raise only after the database has really committed the publication."""
            nonlocal injected
            await original_commit(session)
            if not injected:
                injected=True
                if cancelled:raise asyncio.CancelledError('commit acknowledgement cancelled')
                raise RuntimeError('commit acknowledgement lost')
        with monkeypatch.context() as patch:
            patch.setattr(AsyncSession,'commit',commit_then_lose_ack)
            with pytest.raises(asyncio.CancelledError if cancelled else RuntimeError):
                await publish_artifact(factory,clock,job_id,token,artifact)
        assert injected
        async with factory() as db:
            saved=await db.get(ExecutionJob,job_id)
            media=await db.get(ProductionMedia,saved.media_id)
            task=await db.get(GenerationTask,saved.task_id)
            slot=await db.get(ExecutionSlot,1)
            assert saved.state=='ready' and task.status=='succeeded'
            assert slot.job_id is None
            path=media_path(media.relative_path)
            assert path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==media.sha256
            expected_id=media.id
        assert await publish_artifact(factory,clock,job_id,token,artifact)==expected_id
        async with factory() as db:
            assert len((await db.execute(select(ProductionMedia))).scalars().all())==1
        assert len(list((tmp_path/'media').rglob('*.png')))==1
        assert adapter.sent==0
    finally:await engine.dispose()
