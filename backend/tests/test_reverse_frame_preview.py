"""Real preview bytes, quota eviction and isolated deadline/cancellation tests."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import pytest
from fastapi import HTTPException
from app.services.reverse import frame_preview as preview


def fixture(tmp_path):
    """Create a real three-second source and a separate immutable artifact directory."""
    ffmpeg=Path(os.environ.get('TEST_MEDIA_PROBE',str(Path(__file__).resolve().parents[4]/'ffmpeg.exe')))
    source=tmp_path/'source.mp4';directory=tmp_path/'artifacts';directory.mkdir()
    subprocess.run([str(ffmpeg),'-v','error','-f','lavfi','-i','testsrc2=s=64x48:r=30:d=3','-c:v','libx264','-threads','1','-y',str(source)],check=True,timeout=15)
    return source,directory,ffmpeg


@pytest.mark.asyncio
async def test_real_frame_lru_and_readonly(tmp_path):
    """Request actual ordinals after filling 64 cache entries; preserve machine files."""
    source,directory,ffmpeg=fixture(tmp_path);before=hashlib.sha256(source.read_bytes()).hexdigest()
    protected=directory/'keyframe-00000.jpg';protected.write_bytes(b'frozen machine evidence')
    frame=await preview.original_frame(source,directory,0,ffmpeg)
    cache=frame.parent
    for i in range(1,64):shutil.copyfile(frame,cache/f'frame-{i:05d}.jpg')
    frame.touch()
    result=await preview.original_frame(source,directory,70,ffmpeg)
    assert result.is_file() and len(list(cache.glob('frame-*.jpg')))==64
    assert frame.exists() and not (cache/'frame-00001.jpg').exists()
    assert protected.read_bytes()==b'frozen machine evidence'
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before
    from PIL import Image
    assert max(Image.open(result).size)<=480


@pytest.mark.asyncio
async def test_byte_reservation_evicts_only_manual_then_refuses(tmp_path):
    """Reserve 512KB before decoding and never evict proxy or frozen keyframes."""
    source,directory,ffmpeg=fixture(tmp_path)
    first=await preview.original_frame(source,directory,0,ffmpeg)
    protected=directory/'proxy.mp4'
    with protected.open('wb') as stream:stream.truncate(preview.MAX_ARTIFACT_BYTES-preview.FRAME_RESERVATION)
    result=await preview.original_frame(source,directory,1,ffmpeg)
    assert result.exists() and not first.exists()
    assert sum(p.stat().st_size for p in directory.rglob('*') if p.is_file())<=preview.MAX_ARTIFACT_BYTES
    with protected.open('ab') as stream:stream.write(b'x')
    with pytest.raises(HTTPException) as error:await preview.original_frame(source,directory,2,ffmpeg)
    assert error.value.status_code==413 and protected.exists()


@pytest.mark.asyncio
async def test_actual_child_watchdog_and_cancellation(tmp_path):
    """A tiny independent deadline exits without waiting 45 seconds; cancellation joins."""
    source,directory,ffmpeg=fixture(tmp_path)
    child=Path(preview.__file__).with_name('frame_preview_child.py')
    process=await asyncio.create_subprocess_exec(sys.executable,str(child),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
    parameters={'source':str(source),'output':str(directory/'deadline.jpg'),'index':89,'ffmpeg':str(ffmpeg),'deadline':0.001}
    await asyncio.wait_for(process.communicate(json.dumps(parameters).encode()),3)
    assert process.returncode==124
    task=asyncio.create_task(preview.isolated_preview(source,directory/'cancel.jpg',89,ffmpeg))
    await asyncio.sleep(.01);task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
