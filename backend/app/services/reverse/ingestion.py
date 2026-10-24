"""Persistent local video analysis; original media is always read-only."""
import asyncio
import json
import time
import uuid
from fractions import Fraction
from pathlib import PurePosixPath

from fastapi import HTTPException
from sqlalchemy import select, text, update
from app.models.reverse import ReverseAnalysis
from app.services.studio.production import project_exists
from app.services.studio.production_media import media_path, scoped_media


async def create_analysis(factory, project_id: str, media_id: str, request_key: str) -> str:
    """Validate owned MP4 bytes and atomically deduplicate a durable queued analysis."""
    if not isinstance(request_key,str) or not request_key.strip() or len(request_key)>128:
        raise HTTPException(422,'分析请求标识不符合要求')
    async with factory() as db:
        try:
            await db.execute(text('BEGIN IMMEDIATE'))
            await project_exists(db,project_id)
            media=await scoped_media(db,project_id,media_id)
            if media.mime!='video/mp4':raise HTTPException(422,'视频分析仅接受本工程已校验的MP4素材')
            old=(await db.execute(select(ReverseAnalysis).where(ReverseAnalysis.project_id==project_id,ReverseAnalysis.request_key==request_key))).scalar_one_or_none()
            if old is not None:
                if old.media_id!=media_id or old.source_hash!=media.sha256:raise HTTPException(409,'分析请求标识已用于其他视频版本')
                return old.id
            analysis_id=str(uuid.uuid4())
            db.add(ReverseAnalysis(id=analysis_id,project_id=project_id,media_id=media_id,request_key=request_key,source_hash=media.sha256,state='queued'))
            await db.commit()
            return analysis_id
        except BaseException:
            await db.rollback();raise


def checked_manifest(result: dict, source_hash: str, source_size: int, output) -> dict:
    """Validate the trusted engine's bounded schema before exposing any artifact metadata."""
    if not isinstance(result,dict) or type(result.get('schema_version')) is not int or result['schema_version']!=1 or result.get('source',{}).get('sha256')!=source_hash or result.get('source',{}).get('bytes')!=source_size:
        raise ValueError('Invalid source identity')
    video=result.get('video',{})
    if any(type(video.get(field)) is not int or not 0<video[field]<=1920 for field in ['width','height']):raise ValueError('Invalid video dimensions')
    frames=video.get('frames')
    if not isinstance(frames,list) or not 1<=len(frames)<=7200:raise ValueError('Invalid decoded frame count')
    previous=-1
    for index,frame in enumerate(frames):
        if not isinstance(frame,dict) or type(frame.get('index')) is not int or frame['index']!=index:raise ValueError('Invalid original frame index')
        for field in ['pts','time_base_num','time_base_den','relative_us']:
            if type(frame.get(field)) is not int:raise ValueError('Invalid original frame clock')
        if frame['time_base_num']<=0 or frame['time_base_den']<=0 or frame['relative_us']<previous:raise ValueError('Invalid original frame clock')
        previous=frame['relative_us']
    if frames[0]['relative_us']!=0:raise ValueError('Invalid original frame origin')
    origin=Fraction(frames[0]['pts']*frames[0]['time_base_num'],frames[0]['time_base_den'])
    if any(round((Fraction(frame['pts']*frame['time_base_num'],frame['time_base_den'])-origin)*1000000)!=frame['relative_us'] for frame in frames):raise ValueError('Original frame time mapping is inconsistent')
    if previous>120000000 or video.get('decoded_span_us')!=previous:raise ValueError('Invalid decoded span')
    cuts=result.get('cuts');keyframes=result.get('keyframes');proxy=result.get('proxy');audio=result.get('audio')
    if not isinstance(cuts,list) or not isinstance(keyframes,list) or not isinstance(proxy,dict) or not isinstance(audio,dict):raise ValueError('Missing analysis artifacts')
    if result.get('cuts_are_ground_truth') is not False:raise ValueError('Machine cuts must remain candidates')
    for cut in cuts:
        if not isinstance(cut,dict) or type(cut.get('frame_index')) is not int or not 0<=cut['frame_index']<len(frames) or cut.get('relative_us')!=frames[cut['frame_index']]['relative_us']:raise ValueError('Invalid candidate cut')
    paths=[proxy.get('path')]
    mapping=proxy.get('frame_mapping')
    if not isinstance(mapping,list) or not 1<=len(mapping)<=7200:raise ValueError('Missing proxy mapping')
    for index,item in enumerate(mapping):
        if not isinstance(item,dict) or type(item.get('proxy_index')) is not int or item['proxy_index']!=index or type(item.get('source_index')) is not int or not 0<=item['source_index']<len(frames):raise ValueError('Invalid proxy frame index')
        if any(type(item.get(field)) is not int for field in ['relative_us','source_relative_us','error_us']):raise ValueError('Invalid proxy frame timing')
        if item['relative_us']<0 or item['source_relative_us']!=frames[item['source_index']]['relative_us'] or item['error_us']!=item['relative_us']-item['source_relative_us']:raise ValueError('Invalid proxy frame mapping')
    if proxy.get('max_error_us')!=max(abs(item['error_us']) for item in mapping):raise ValueError('Invalid proxy mapping error')
    if any(type(proxy.get(field)) is not int or proxy[field]<=0 for field in ['fps_num','fps_den']):raise ValueError('Invalid proxy frame rate')
    if type(audio.get('present')) is not bool or not isinstance(audio.get('tracks'),list):raise ValueError('Invalid audio metadata')
    for keyframe in keyframes:
        if not isinstance(keyframe,dict) or type(keyframe.get('frame_index')) is not int or not 0<=keyframe['frame_index']<len(frames):raise ValueError('Invalid keyframe')
        paths.append(keyframe.get('path'))
    if audio.get('present'):
        paths.append(audio.get('wav_path'))
    for name in paths:
        if not isinstance(name,str) or not name or '\\' in name or ':' in name or PurePosixPath(name).is_absolute() or any(part in {'','..','.'} for part in name.split('/')):
            raise ValueError('Invalid artifact path')
        target=(output/name).resolve()
        if not target.is_relative_to(output.resolve()) or not target.is_file():raise ValueError('Missing private artifact')
    encoded=json.dumps(result,ensure_ascii=False,allow_nan=False,separators=(',',':'))
    if len(encoded.encode('utf8'))>8*1024*1024:raise ValueError('Analysis manifest exceeds bound')
    return json.loads(encoded)


async def run_analysis(factory, analysis_id: str, trusted_ffmpeg: str, *, probe_fn=None, clock=time.time, lease_seconds=120) -> bool:
    """CAS a lease, analyze in a unique attempt directory, and publish only while fenced.

    A cancelled or killed worker leaves analyzing state until its lease expires;
    another worker can retry into a different private directory. Failed runs expose
    only a fixed Chinese message, never subprocess errors, commands or local paths.
    """
    if type(lease_seconds) not in (int,float) or lease_seconds<120:raise ValueError('Analysis lease must be at least 120 seconds')
    if probe_fn is None:
        from app.services.reverse.video_probe import probe
        probe_fn=probe
    token=str(uuid.uuid4())
    async with factory() as db:
        claimed=await db.execute(update(ReverseAnalysis).where(ReverseAnalysis.id==analysis_id,ReverseAnalysis.state.in_({'queued','analyzing'}),ReverseAnalysis.lease_until<=clock()).values(state='analyzing',lease_token=token,lease_until=clock()+lease_seconds,reason=''))
        if not claimed.rowcount:await db.rollback();return False
        await db.commit()
        job=await db.get(ReverseAnalysis,analysis_id)
        project_id,media_id,source_hash=job.project_id,job.media_id,job.source_hash
    try:
        uuid.UUID(analysis_id)
        relative=f'_reverse/{analysis_id}/{uuid.uuid4()}'
        output=media_path(relative);output.mkdir(parents=True,exist_ok=False)
        async with factory() as db:
            media=await scoped_media(db,project_id,media_id)
            if media.sha256!=source_hash or media.mime!='video/mp4':raise ValueError('Source identity changed')
            source=media_path(media.relative_path)
        result=await asyncio.wait_for(probe_fn(source,output,trusted_ffmpeg=trusted_ffmpeg,deadline_seconds=90),timeout=95)
        manifest=checked_manifest(result,source_hash,media.size,output)
        async with factory() as db:
            media=await scoped_media(db,project_id,media_id)
            if media.sha256!=source_hash:raise ValueError('Source identity changed')
            state='degraded' if manifest.get('degraded') is True or manifest.get('proxy',{}).get('degraded') is True else 'ready'
            changed=await db.execute(update(ReverseAnalysis).where(ReverseAnalysis.id==analysis_id,ReverseAnalysis.state=='analyzing',ReverseAnalysis.source_hash==source_hash,ReverseAnalysis.lease_token==token,ReverseAnalysis.lease_until>clock()).values(state=state,manifest=manifest,artifact_dir=relative,reason='分析已完成，部分证据需复核' if state=='degraded' else '',lease_token=None,lease_until=0))
            await db.commit()
            return bool(changed.rowcount)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        detail=str(error).lower()
        reason='视频分析失败，请检查素材或稍后重试'
        if 'exceed' in detail:reason='视频超过当前时长、尺寸、帧数或产物大小上限'
        elif isinstance(error,asyncio.TimeoutError):reason='视频分析超时，可重新分析原视频'
        async with factory() as db:
            failed=await db.execute(update(ReverseAnalysis).where(ReverseAnalysis.id==analysis_id,ReverseAnalysis.state=='analyzing',ReverseAnalysis.lease_token==token,ReverseAnalysis.lease_until>clock()).values(state='failed',reason=reason,lease_token=None,lease_until=0))
            await db.commit()
            return bool(failed.rowcount)


async def edit_cuts(factory, project_id: str, analysis_id: str, frame_indices: list[int], expected_revision: int) -> dict:
    """CAS human-confirmed original-frame boundaries without rewriting candidates."""
    if type(expected_revision) is not int or not isinstance(frame_indices,list) or not frame_indices or any(type(index) is not int for index in frame_indices) or frame_indices[0]!=0 or any(left>=right for left,right in zip(frame_indices,frame_indices[1:])):
        raise HTTPException(422,'切点须从第0帧开始，按原始帧序号严格递增')
    async with factory() as db:
        job=(await db.execute(select(ReverseAnalysis).where(ReverseAnalysis.id==analysis_id,ReverseAnalysis.project_id==project_id))).scalar_one_or_none()
        if job is None:raise HTTPException(404,'本工程视频分析不存在')
        if job.state not in {'ready','degraded'} or not job.manifest:raise HTTPException(409,'分析尚未完成，不能确认切点')
        frames=job.manifest['video']['frames']
        if frame_indices[-1]>=len(frames):raise HTTPException(422,'切点超出原视频帧范围')
        confirmed=[{'frame_index':index,'relative_us':frames[index]['relative_us']} for index in frame_indices]
        changed=await db.execute(update(ReverseAnalysis).where(ReverseAnalysis.id==analysis_id,ReverseAnalysis.project_id==project_id,ReverseAnalysis.revision==expected_revision,ReverseAnalysis.state.in_({'ready','degraded'})).values(confirmed_cuts=confirmed,revision=ReverseAnalysis.revision+1))
        if not changed.rowcount:await db.rollback();raise HTTPException(409,'分析版本已改变，请刷新后再确认')
        await db.commit()
        return {'id':analysis_id,'revision':expected_revision+1,'confirmed_cuts':confirmed}


async def tick(factory, trusted_ffmpeg: str, *, probe_fn=None, clock=time.time) -> bool:
    """Process at most one queued or expired analysis per lifecycle scanner tick."""
    async with factory() as db:
        analysis_id=(await db.execute(select(ReverseAnalysis.id).where(ReverseAnalysis.state.in_({'queued','analyzing'}),ReverseAnalysis.lease_until<=clock()).order_by(ReverseAnalysis.lease_until,ReverseAnalysis.id).limit(1))).scalar_one_or_none()
    return await run_analysis(factory,analysis_id,trusted_ffmpeg,probe_fn=probe_fn,clock=clock) if analysis_id else False
