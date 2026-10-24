"""Exact original-frame previews for manually reviewing non-candidate boundaries."""
import asyncio
import uuid
import json
import os
import signal
import sys
from pathlib import Path
from fastapi import HTTPException

_capacity=asyncio.Semaphore(1)
MAX_MANUAL=64
MAX_ARTIFACT_BYTES=128*1024*1024
FRAME_RESERVATION=512000


def reserve_cache(directory,cache):
    """Reserve worst-case bytes, evicting only regular manual JPEGs in LRU order."""
    files=[];total=0
    for path in directory.rglob('*'):
        if path.is_symlink():raise HTTPException(413,'分析目录含有不安全文件')
        if path.is_file():
            total+=path.stat().st_size
            if path.parent==cache and path.name.startswith('frame-') and path.suffix=='.jpg':files.append(path)
    files.sort(key=lambda p:p.stat().st_mtime_ns)
    while files and (len(files)>=MAX_MANUAL or total+FRAME_RESERVATION>MAX_ARTIFACT_BYTES):
        victim=files.pop(0);size=victim.stat().st_size;victim.unlink();total-=size
    if len(files)>=MAX_MANUAL:raise HTTPException(429,'手动预览缓存已满')
    if total+FRAME_RESERVATION>MAX_ARTIFACT_BYTES:raise HTTPException(413,'分析产物已达到磁盘上限')


async def isolated_preview(source,temp,index,ffmpeg,*,deadline=45):
    """Kill and join the trusted decoder subtree on timeout or application cancellation."""
    child=Path(__file__).with_name('frame_preview_child.py')
    process=await asyncio.create_subprocess_exec(sys.executable,str(child),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL,start_new_session=os.name!='nt')
    try:
        data={'source':str(source),'output':str(temp),'index':index,'ffmpeg':str(ffmpeg),'deadline':deadline}
        await asyncio.wait_for(process.communicate(json.dumps(data).encode()),deadline+1)
        if process.returncode:raise HTTPException(409,'无法读取该原始帧')
    except asyncio.TimeoutError:raise HTTPException(503,'读取原始帧超时，请稍后重试') from None
    finally:
        if process.returncode is None:
            if os.name=='nt':
                killer=await asyncio.create_subprocess_exec('taskkill','/PID',str(process.pid),'/T','/F',stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
                await killer.wait()
            else:os.killpg(process.pid,signal.SIGKILL)
            if process.returncode is None:process.kill()
            await process.wait()


async def original_frame(source,directory,index,ffmpeg):
    """Decode by actual frame ordinal into a bounded private cache, never seconds*fps."""
    directory=Path(directory).resolve();source=Path(source).resolve();ffmpeg=Path(ffmpeg).resolve()
    if type(index) is not int or not 0<=index<7200:raise HTTPException(404,'原始帧不存在')
    if not source.is_file() or not ffmpeg.is_file():raise HTTPException(409,'原视频或本地工具不可用')
    cache=(directory/'manual-frames').resolve()
    if not cache.is_relative_to(directory):raise HTTPException(409,'缩略图路径无效')
    async with _capacity:
        cache.mkdir(parents=True,exist_ok=True)
        final=cache/f'frame-{index:05d}.jpg'
        if final.is_file():
            if final.is_symlink() or not 0<final.stat().st_size<=FRAME_RESERVATION:raise HTTPException(409,'缓存缩略图无效')
            final.touch();return final
        reserve_cache(directory,cache)
        temp=cache/f'{uuid.uuid4()}.jpg'
        try:
            await isolated_preview(source,temp,index,ffmpeg)
            if not temp.is_file() or not 0<temp.stat().st_size<=FRAME_RESERVATION:raise HTTPException(409,'无法读取该原始帧')
            if sum(p.stat().st_size for p in directory.rglob('*') if p.is_file())>MAX_ARTIFACT_BYTES:raise HTTPException(413,'分析产物已达到磁盘上限')
            temp.replace(final);return final
        finally:
            temp.unlink(missing_ok=True)
