"""Build model inputs only from checked original bytes, rational clocks and exact decoded ordinals."""
import asyncio
import hashlib
import io
import wave
from fractions import Fraction
from app.core.contracts.video_dna import DnaFrame
from app.services.script_preparation.service import content_hash
from app.services.video_dna.source import owned_analysis, verify_source
from app.services.reverse.frame_preview import original_frame
from app.services.reverse.views import artifact_path
from app.services.studio.production_media import media_path, scoped_media
from app.services.video_understanding.errors import UnderstandingFailure


def source_snapshot(row) -> dict:
    """Keep detector boundaries labeled as candidates; never populate human-confirmed source fields."""
    if row.state not in {'ready', 'degraded'}:
        raise UnderstandingFailure('source_not_ready', '请先完成本地视频解析，再开始画面和对白理解')
    raw = (row.manifest or {}).get('video', {}).get('frames', [])
    try:
        frames = [DnaFrame.model_validate({key: frame[key] for key in ('index', 'pts', 'time_base_num', 'time_base_den', 'relative_us')}).model_dump() for frame in raw]
        if not 1 <= len(frames) <= 7200:
            raise ValueError('Invalid frame count')
        origin = Fraction(frames[0]['pts'] * frames[0]['time_base_num'], frames[0]['time_base_den'])
        previous = None
        for index, frame in enumerate(frames):
            point = Fraction(frame['pts'] * frame['time_base_num'], frame['time_base_den'])
            if frame['index'] != index or (previous is not None and point < previous) or frame['relative_us'] != round((point - origin) * 1000000):
                raise ValueError('Invalid rational source clock')
            previous = point
        confirmed = row.confirmed_cuts
        entries = confirmed if confirmed else [{'frame_index': 0, 'relative_us': 0}, *(row.manifest or {}).get('cuts', [])]
        cuts = []
        for entry in entries:
            index = entry['frame_index']
            if type(index) is not int or not 0 <= index < len(frames) or entry['relative_us'] != frames[index]['relative_us']:
                raise ValueError('Invalid original boundary')
            if index not in cuts:
                cuts.append(index)
        if not cuts or cuts[0] != 0 or cuts != sorted(cuts) or len(cuts) > 256:
            raise ValueError('Invalid boundary order')
        audio = (row.manifest or {}).get('audio', {})
        origin_us = audio.get('tracks', [{}])[0].get('origin_relative_us') if audio.get('present') else None
        if audio.get('present') and type(origin_us) is not int:
            raise ValueError('Missing original audio clock')
        return {'analysis_id': row.id, 'analysis_revision': row.revision, 'media_id': row.media_id,
            'sha256': row.source_hash, 'manifest_hash': content_hash(row.manifest),
            'frame_clock_hash': content_hash({'schema_version': 1, 'frames': frames}), 'frames': frames,
            'cut_indices': cuts, 'cut_origin': 'human_confirmed' if confirmed else 'detector_candidate',
            'audio_present': audio.get('present') is True, 'audio_origin_relative_us': origin_us,
            'terminal_time_known': False}
    except (ValueError, TypeError, KeyError, IndexError, ZeroDivisionError):
        raise UnderstandingFailure('source_clock_invalid', '原片帧时钟、音轨起点或切点不完整，不能用估算帧率代替；请重新解析视频') from None


def shot_intervals(source: dict) -> list[dict]:
    """Partition the original frame sequence, preserving the unknown timestamp at sentinel N."""
    cuts, frames = source['cut_indices'], source['frames']
    return [{'id': f'shot_{index + 1:03d}', 'start_frame': start,
        'end_frame': cuts[index + 1] if index + 1 < len(cuts) else len(frames),
        'start_us': frames[start]['relative_us'],
        'end_us': frames[cuts[index + 1]]['relative_us'] if index + 1 < len(cuts) else None}
        for index, start in enumerate(cuts)]


def sample_indices(source: dict, maximum: int) -> list[int]:
    """Use first, middle and last actual frames inside each shot; a next-shot frame is never its end image."""
    result = set()
    for shot in shot_intervals(source):
        start, end = shot['start_frame'], shot['end_frame']
        result.update((start, start + (end - start - 1) // 2, end - 1))
    if len(result) > maximum:
        raise UnderstandingFailure('sample_limit', f'本视频需{len(result)}张逐镜证据，超过已设置的{maximum}张上限；请调高证据帧上限或分段导入，不能省略未覆盖镜头')
    return sorted(result)


async def checked_source(db, project_id: str, analysis_id: str) -> tuple:
    """Resolve project ownership and hash the immutable original before any model request."""
    row = await owned_analysis(db, project_id, analysis_id)
    source = source_snapshot(row)
    await verify_source(db, project_id, source)
    return row, source


async def prepare_inputs(factory, project_id: str, source: dict, connection: dict, ffmpeg: str) -> dict:
    """Decode the submitted original ordinals and hash every image and WAV sent to a provider."""
    async with factory() as db:
        row, current = await checked_source(db, project_id, source['analysis_id'])
        for field in ('sha256', 'media_id', 'manifest_hash', 'frame_clock_hash'):
            if current[field] != source[field]:
                raise UnderstandingFailure('source_changed', '原片或实际解析证据已改变，本次没有发送模型请求；请重新建立任务')
        media = await scoped_media(db, project_id, source['media_id'])
        original = media_path(media.relative_path)
        directory = artifact_path(row, row.manifest['proxy']['path']).parent
        audio_path = artifact_path(row, row.manifest['audio']['wav_path']) if source['audio_present'] and connection['transcribe_audio'] else None
    selected = sample_indices(source, connection['max_frames'])
    images, receipt_frames, total = [], [], 0
    for index in selected:
        path = await original_frame(original, directory, index, ffmpeg)
        data = await asyncio.to_thread(path.read_bytes)
        total += len(data)
        if not data.startswith(b'\xff\xd8') or len(data) > 512000 or total > 12 * 1024 * 1024:
            raise UnderstandingFailure('frame_bytes_invalid', '原始帧证据缺失或超出本次请求大小上限，请缩短视频')
        frame = {**source['frames'][index], 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data), 'mime': 'image/jpeg'}
        images.append((frame, data)); receipt_frames.append(frame)
    audio, audio_receipt = None, None
    if audio_path is not None:
        audio = await asyncio.to_thread(audio_path.read_bytes)
        try:
            if len(audio) > 4 * 1024 * 1024:
                raise ValueError('Oversized audio')
            with wave.open(io.BytesIO(audio)) as decoded:
                if decoded.getnchannels() != 1 or decoded.getsampwidth() != 2 or decoded.getframerate() != 16000 or decoded.getnframes() > 16000 * 121:
                    raise ValueError('Unsupported audio extraction')
                audio_receipt = {'sha256': hashlib.sha256(audio).hexdigest(), 'bytes': len(audio),
                    'sample_rate': 16000, 'sample_count': decoded.getnframes(), 'origin_relative_us': source['audio_origin_relative_us']}
        except (ValueError, wave.Error, EOFError):
            raise UnderstandingFailure('audio_bytes_invalid', '提取音轨缺失或不是已校验的16kHz单声道音频，请重新解析') from None
    receipt = {'schema_version': 1, 'source_sha256': source['sha256'], 'manifest_hash': source['manifest_hash'],
        'frame_clock_hash': source['frame_clock_hash'], 'cut_origin': source['cut_origin'], 'cut_indices': source['cut_indices'],
        'frames': receipt_frames, 'audio': audio_receipt, 'transcription_enabled': connection['transcribe_audio'],
        'terminal_time_known': False}
    return {'images': images, 'audio': audio, 'receipt': receipt, 'shots': shot_intervals(source)}
