"""Verify complete bounded video decoding using authored media and the trusted local encoder only."""
import hashlib
import os
from pathlib import Path
import subprocess

import pytest

from app.core.integrations.verified_video import verify_video_bytes
from app.core.integrations.verified_video_child import open_reference_video


@pytest.fixture
def authored_video(tmp_path):
    """Create original geometric MP4 bytes; no downloaded or user-owned media enters these tests."""
    binary = os.environ.get('FAWNIMO_TEST_FFMPEG')
    if not binary or not Path(binary).is_file():
        pytest.skip('An explicit trusted local FFmpeg is required')

    def make(*, fps=24, duration=2, size='720x1280', audio=False):
        """Bound every encoder process and output while exercising actual codec/frame clocks."""
        target = tmp_path/('authored-'+str(len(list(tmp_path.iterdir())))+'.mp4')
        args = [binary, '-hide_banner', '-loglevel', 'error', '-nostdin', '-f', 'lavfi',
                '-i', f'color=c=navy:s={size}:r={fps}:d={duration}']
        if audio:
            args += ['-f', 'lavfi', '-i', f'sine=frequency=440:sample_rate=24000:duration={duration}',
                     '-map', '0:v:0', '-map', '1:a:0', '-c:a', 'aac', '-ac', '1']
        else:
            args += ['-an']
        args += ['-c:v', 'libx264', '-threads', '1', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', str(target)]
        subprocess.run(args, check=True, timeout=20, capture_output=True)
        content = target.read_bytes()
        assert len(content) < 32*1024*1024
        return content
    return make


@pytest.mark.asyncio
@pytest.mark.parametrize('fps', [24, 25, 30])
async def test_actual_complete_video_decode_retains_hash_frames_and_clock(authored_video, fps):
    """Recorded duration and frame count come from decoded clocks rather than MP4 metadata alone."""
    content = authored_video(fps=fps)
    proof = await verify_video_bytes(content)
    assert proof.sha256 == hashlib.sha256(content).hexdigest() and proof.byte_size == len(content)
    assert proof.width == 720 and proof.height == 1280 and proof.duration_us == 2_000_000
    assert proof.fps == fps and proof.frames == 2*fps and len(proof.frame_clock_sha256) == 64
    assert not proof.has_audio and proof.audio_samples == proof.audio_channels == 0


@pytest.mark.asyncio
async def test_original_video_audio_is_explicitly_detected_and_decoded(authored_video):
    """A chosen MP4 may include source audio; the proof never mislabels it as a silent reference."""
    content = authored_video(audio=True)
    proof = await verify_video_bytes(content)
    assert proof.has_audio and proof.audio_channels == 1 and 48000 <= proof.audio_samples <= 50000
    assert proof.duration_us == 2_000_000


def test_actual_decoder_enforces_pixel_ceiling_before_accepting_any_frame(authored_video):
    """A real codec rejects a valid720p stream when the same opening function receives a deliberately tiny ceiling."""
    content = authored_video()
    with pytest.raises(Exception):
        with open_reference_video(content, pixel_limit=4) as container:
            next(container.decode(container.streams.video[0]))


@pytest.mark.asyncio
@pytest.mark.parametrize('options', [{'duration': 1}, {'duration': 11}, {'fps': 12}, {'size': '360x640'}])
async def test_unsupported_actual_duration_rate_or_dimensions_refuse(authored_video, options):
    """Overlong, too short, low-rate and undersized real video cannot obtain reference proof."""
    with pytest.raises(ValueError):
        await verify_video_bytes(authored_video(**options))


@pytest.mark.asyncio
@pytest.mark.parametrize('content', [b'', b'not-mp4', b'{"duration":2,"fps":24}', b'\x00\x00\x00\x18ftypisom'])
async def test_claimed_metadata_and_corrupt_bytes_cannot_become_actual_video_proofs(content):
    """A plausible container header or declared duration never substitutes for complete decoding."""
    with pytest.raises(ValueError):
        await verify_video_bytes(content)
