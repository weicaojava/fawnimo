"""Own bounded WAV decoder processes and compare real historical sample facts."""
import asyncio
import json
from pathlib import Path
import sys
from fastapi import HTTPException
from app.core.owned_process import run_owned_process
from app.models.production import ProductionMedia
from app.services.editing.media import recheck_media, stat_identity, verify_publication_sources
from app.services.studio.production_media import media_path
from app.services.editing.sound_process import child_environment


async def probe_wave(db, project_id, media_id, intervals=()):
    """Decode actual complete project PCM outside every writer lock, retaining file witnesses."""
    row = await db.get(ProductionMedia, media_id)
    if row is None or row.project_id != project_id: raise HTTPException(404, '本工程声音素材不存在')
    if row.mime != 'audio/wav' or not 44 <= row.size <= 32 * 1024 * 1024: raise HTTPException(422, '声音素材须为32MiB以内的PCM WAV')
    path = media_path(row.relative_path)
    try:
        before = stat_identity(path)
        if before[0] != row.size: raise ValueError('WAV size changed')
        raw, code = await run_owned_process([sys.executable, str(Path(__file__).with_name('sound_probe_child.py'))],
            json.dumps({'path': str(path), 'expected_hash': row.sha256, 'intervals': intervals}).encode(), timeout=20, max_stdout_bytes=4096, environment=child_environment())
        facts = json.loads(raw)
        if code or not isinstance(facts, dict) or 'error' in facts or before != stat_identity(path): raise ValueError('WAV decode failed')
    except asyncio.CancelledError:
        raise
    except (OSError, ValueError, asyncio.TimeoutError) as error:
        raise HTTPException(409, '声音素材缺失、改变或不能完整解码；仅支持24/48kHz单声道或双声道PCM16 WAV') from error
    return {'id': row.id, 'mime': row.mime, 'sha256': row.sha256, 'size': row.size, 'relative_path': row.relative_path,
        'stat': before, 'audio': facts}


async def _verify_sources(factory, project_id, payload):
    """Reopen locked historical bytes without following later candidate or selection choices."""
    proof = await verify_publication_sources(factory, project_id, payload)
    intervals = {}
    for track in payload['tracks']:
        intervals.setdefault(track['source']['id'], []).append((track['source_in_ms'], track['source_out_ms']))
    async with factory() as db:
        for track in payload['tracks']:
            source = track['source']
            if source['id'] not in proof:
                actual = await probe_wave(db, project_id, source['id'], intervals[source['id']])
                actual['audio'].pop('interval_peaks_pcm16')
                proof[source['id']] = actual
            for key in ('id', 'mime', 'sha256', 'size', 'audio'):
                if source[key] != proof[source['id']][key]: raise HTTPException(409, '锁定声音素材或真实样本时钟已改变')
    return proof


async def verify_sources(factory, project_id, payload):
    """Bound the entire historical source check, joining any decoder before a timeout can return."""
    try: return await asyncio.wait_for(_verify_sources(factory, project_id, payload), timeout=120)
    except asyncio.TimeoutError as error: raise HTTPException(409, '历史声音来源完整校验超过120秒，请检查本地资源') from error


async def run_child(parameters, deadline=120):
    """A private sound render owns its entire encoder tree, including cancellation cleanup."""
    if deadline != 120: raise ValueError('Sound render requires a 120 second deadline')
    try:
        raw, code = await run_owned_process([sys.executable, str(Path(__file__).with_name('sound_child.py'))],
            json.dumps({**parameters, 'deadline': deadline}, ensure_ascii=False).encode(), timeout=deadline + 1, environment=child_environment())
        result = json.loads(raw)
        if code or not isinstance(result, dict) or result.get('error'): raise ValueError('Sound render rejected')
        return result
    except asyncio.CancelledError:
        raise
    except (OSError, ValueError, asyncio.TimeoutError) as error:
        raise HTTPException(409, '声音合成未通过真实素材、样本或输出校验') from error
