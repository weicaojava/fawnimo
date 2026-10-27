"""Validate actual decoded clocks outside writer transactions in a killable child."""
import asyncio
import hashlib
import json
import os
import signal
import sys
from pathlib import Path

from fastapi import HTTPException
from app.config import settings
from app.models.production import ProductionMedia
from app.services.studio.production_media import media_path


def canonical_hash(value) -> str:
    """Bind JSON evidence with stable UTF8 serialization and no NaN values."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def stat_identity(path: Path) -> list[int]:
    """Capture file identity for the short writer-lock recheck, not a hash substitute."""
    item = path.stat()
    return [item.st_size, item.st_mtime_ns, item.st_ctime_ns, item.st_dev, item.st_ino]


def covers(segments, start_ms: int, end_ms: int) -> bool:
    """Check a half-open source interval against actual contiguous microsecond coverage."""
    return end_ms > start_ms and any(start <= start_ms * 1000 and end >= end_ms * 1000 for start, end in segments)


async def kill_child(process) -> None:
    """Immediately terminate and join this private helper and its encoder children."""
    if process.returncode is not None:
        return
    if os.name == 'nt':
        killer = await asyncio.create_subprocess_exec('taskkill', '/PID', str(process.pid), '/T', '/F', stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await killer.wait()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    await process.wait()


async def run_child(parameters: dict, deadline: int = 45) -> dict:
    """Run a database-free helper with an inner watchdog and an outer kill/join bound."""
    if deadline not in (45, 120):
        raise ValueError('Unsupported editing process deadline')
    child = Path(__file__).with_name('media_child.py')
    process = await asyncio.create_subprocess_exec(sys.executable, str(child), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, start_new_session=os.name != 'nt')
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(json.dumps({**parameters, 'deadline': deadline}, ensure_ascii=False).encode()), deadline + 1)
        if len(stdout) > 2 * 1024 * 1024:
            raise ValueError('Editing helper exceeded response limit')
        result = json.loads(stdout)
        if process.returncode or not isinstance(result, dict) or result.get('error'):
            raise ValueError(result.get('error', 'Local media processing failed'))
        return result
    except asyncio.CancelledError:
        raise
    except (ValueError, OSError, asyncio.TimeoutError, KeyError) as exc:
        raise HTTPException(409, '本地媒体校验或合成失败，素材可能不支持、损坏或超限') from exc
    finally:
        await kill_child(process)


async def probe_owned(db, project_id: str, media_id: str) -> dict:
    """Hash and completely decode one owned MP4, preserving its actual frame/sample clocks."""
    row = await db.get(ProductionMedia, media_id)
    if row is None or row.project_id != project_id:
        raise HTTPException(404, '本工程媒体不存在')
    if row.mime != 'video/mp4' or row.size > min(settings.production_upload_limit, 64 * 1024 * 1024):
        raise HTTPException(422, '剪辑仅支持上限内的MP4视频')
    path = media_path(row.relative_path)
    try:
        before = stat_identity(path)
        if before[0] != row.size:
            raise ValueError('Media size changed')
        facts = await run_child({'mode': 'probe', 'path': str(path), 'expected_hash': row.sha256})
        if before != stat_identity(path):
            raise ValueError('Media changed while decoding')
    except (OSError, ValueError) as exc:
        raise HTTPException(409, '媒体缺失或校验失败') from exc
    return {'id': row.id, 'mime': row.mime, 'sha256': row.sha256, 'size': row.size, 'relative_path': row.relative_path, 'stat': before, **facts}


async def recheck_media(db, project_id: str, evidence: dict) -> None:
    """Recheck immutable metadata and file identity without hashing inside a write lock."""
    row = await db.get(ProductionMedia, evidence['id'])
    if row is None or (row.project_id, row.sha256, row.size, row.mime, row.relative_path) != (project_id, evidence['sha256'], evidence['size'], evidence['mime'], evidence['relative_path']):
        raise HTTPException(409, '素材来源已改变')
    try:
        if stat_identity(media_path(row.relative_path)) != evidence['stat']:
            raise ValueError('Media identity changed')
    except (OSError, ValueError) as exc:
        raise HTTPException(409, '素材文件已改变，请重新校验') from exc


async def verify_publication_sources(factory, project_id: str, payload: dict) -> dict:
    """Revalidate historical locked bytes/clocks without following mutable selections."""
    verified = {}
    async with factory() as db:
        for clip in payload['clips']:
            source = clip['source']
            facts = verified.get(source['id'])
            if facts is None:
                facts = await probe_owned(db, project_id, source['id'])
                verified[source['id']] = facts
            # A restored file may have a different filesystem identity; immutable
            # bytes and decoded clocks determine historical validity.
            for key in ('id', 'sha256', 'size', 'mime', 'video', 'audio'):
                if facts[key] != source[key]:
                    raise HTTPException(409, '锁定素材或媒体时钟不匹配')
            if not covers(facts['video']['coverage_us'], clip['source_in_ms'], clip['source_out_ms']):
                raise HTTPException(409, '裁剪范围超出实际视频覆盖')
    return verified
