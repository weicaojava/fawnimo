"""Join the owned short-video decoder before producing any reference proof."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sys

from app.core.media_environment import media_environment
from app.core.owned_process import run_owned_process


@dataclass(frozen=True, slots=True)
class DecodedReferenceVideo:
    """Actual decoded clocks and original content identity; no path, external URL or model credential."""
    sha256: str
    byte_size: int
    width: int
    height: int
    duration_us: int
    frames: int
    fps: int
    frame_clock_sha256: str
    has_audio: bool
    audio_samples: int
    audio_channels: int
    mime: str = 'video/mp4'


async def verify_video_bytes(content: bytes) -> DecodedReferenceVideo:
    """Decode these exact bounded bytes outside writer ownership, with no redirected media or child lifetime left behind."""
    if not isinstance(content, bytes) or not 0 < len(content) <= 32 * 1024 * 1024:
        raise ValueError('参考视频字节为空或超过32MiB')
    try:
        raw, code = await run_owned_process([sys.executable, str(Path(__file__).with_name('verified_video_child.py'))],
            content, timeout=18, max_stdout_bytes=4096, environment=media_environment())
        proof = json.loads(raw)
        expected = {'sha256', 'byte_size', 'mime', 'width', 'height', 'duration_us', 'frames', 'fps',
                    'frame_clock_sha256', 'has_audio', 'audio_samples', 'audio_channels'}
        if code or not isinstance(proof, dict) or set(proof) != expected:
            raise ValueError()
        if (proof['sha256'] != hashlib.sha256(content).hexdigest() or proof['byte_size'] != len(content)
                or proof['mime'] != 'video/mp4' or type(proof['has_audio']) is not bool
                or any(type(proof[key]) is not int for key in ('byte_size', 'width', 'height', 'duration_us', 'frames', 'fps', 'audio_samples', 'audio_channels'))
                or not 300 <= proof['width'] <= 1920 or not 300 <= proof['height'] <= 1920
                or not 407696 <= proof['width']*proof['height'] <= 2073600
                or not 2_000_000 <= proof['duration_us'] <= 10_000_000 or not 1 <= proof['frames'] <= 600
                or proof['fps'] not in (24, 25, 30)
                or not isinstance(proof['frame_clock_sha256'], str)
                or re.fullmatch(r'[a-f0-9]{64}', proof['frame_clock_sha256']) is None):
            raise ValueError()
        return DecodedReferenceVideo(**proof)
    except Exception:
        raise ValueError('参考视频未通过完整解码；仅支持2至10秒、24/25/30fps的有界H264/HEVC MP4') from None
