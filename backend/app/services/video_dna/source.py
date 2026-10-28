"""Preserve exact R1 frame identity and privately verify owned immutable bytes."""
import asyncio
import hashlib
import json
from fractions import Fraction
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from app.core.contracts.video_dna import DnaSource,DnaFrame,safe_json
from app.models.reverse import ReverseAnalysis
from app.models.production import ProductionMedia
from app.services.studio.production_media import media_path
from app.services.script_preparation.service import canonical,content_hash


def issue(code,message,path=None,shot_id=None):
    """Expose stable issues without exposing private filenames or server traces."""
    return {'code':code,'message':message,'path':path,'shot_id':shot_id}


async def owned_analysis(db,project_id,analysis_id):
    """Check DB ownership without hashing inside a writer transaction."""
    row=(await db.execute(select(ReverseAnalysis).where(ReverseAnalysis.id==analysis_id,ReverseAnalysis.project_id==project_id))).scalar_one_or_none()
    if row is None:raise HTTPException(404,'本工程原视频分析不存在')
    media=await db.get(ProductionMedia,row.media_id)
    if media is None or media.project_id!=project_id or media.sha256!=row.source_hash:raise HTTPException(409,'分析原媒体归属或哈希不一致')
    return row


def source_from_analysis(row):
    """Build the only trusted portable clock preimage from R1 originals, never nominal fps."""
    try:
        if row.state not in ('ready','degraded'):raise ValueError('Analysis incomplete')
        raw=(row.manifest or {}).get('video',{}).get('frames',[])
        frames=[DnaFrame.model_validate({key:frame[key] for key in ('index','pts','time_base_num','time_base_den','relative_us')}).model_dump() for frame in raw]
        if not frames:raise ValueError('Missing original frames')
        origin=Fraction(frames[0]['pts']*frames[0]['time_base_num'],frames[0]['time_base_den']);previous=None
        for index,frame in enumerate(frames):
            stamp=Fraction(frame['pts']*frame['time_base_num'],frame['time_base_den'])
            if frame['index']!=index or (previous is not None and stamp<previous) or frame['relative_us']!=round((stamp-origin)*1000000):raise ValueError('Invalid original frame clock')
            previous=stamp
        confirmed=row.confirmed_cuts
        if not isinstance(confirmed,list) or not confirmed:raise ValueError('Cuts not confirmed')
        cuts=[]
        for cut in confirmed:
            index=cut['frame_index']
            if type(index) is not int or index<0 or index>=len(frames) or cut['relative_us']!=frames[index]['relative_us']:raise ValueError('Invalid confirmed cut')
            cuts.append(index)
        if cuts[0]!=0 or any(a>=b for a,b in zip(cuts,cuts[1:])):raise ValueError('Invalid confirmed cuts')
        source={'analysis_id':row.id,'analysis_revision':row.revision,'media_id':row.media_id,'sha256':row.source_hash,'manifest_hash':content_hash(row.manifest),'frame_clock_hash':content_hash({'schema_version':1,'frames':frames}),'frames':frames,'confirmed_cut_indices':cuts,'audio_present':(row.manifest or {}).get('audio',{}).get('present',False),'terminal_time_known':False}
        return DnaSource.model_validate(source).model_dump()
    except (ValueError,TypeError,KeyError,ZeroDivisionError,ValidationError):raise HTTPException(409,'原分析尚无有效确认切点，或整数/帧时钟不适合本版DNA') from None


def stat_identity(path):
    """Private immutable-byte proof, never part of portable canonical snapshots."""
    info=path.stat()
    return [info.st_size,info.st_mtime_ns,info.st_ctime_ns,info.st_dev,info.st_ino]


def file_hash(path):
    """Hash immutable bytes in bounded chunks instead of loading an entire archive."""
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()


async def media_evidence(db,project_id,media_id,verified=None):
    """Hash outside locks or cheaply recheck the exact prior DB/stat proof under a short lock."""
    row=await db.get(ProductionMedia,media_id)
    if row is None or row.project_id!=project_id:raise HTTPException(409,'引用媒体不属于本工程或已缺失')
    metadata={'media_id':row.id,'sha256':row.sha256,'size':row.size,'mime':row.mime}
    private={'metadata':metadata,'relative_path':row.relative_path}
    try:
        safe_json(metadata)
        path=media_path(row.relative_path);before=stat_identity(path)
        if before[0]!=row.size:raise ValueError('Size changed')
        if verified is None:
            if await asyncio.to_thread(file_hash,path)!=row.sha256 or stat_identity(path)!=before:raise ValueError('Hash changed')
        elif verified!={**private,'stat':before}:raise ValueError('Reference changed')
        return metadata,{**private,'stat':before}
    except (OSError,ValueError):raise HTTPException(409,'引用原媒体缺失、改变或完整哈希校验失败') from None


async def verify_source(db,project_id,source,verified=None):
    """Check fixed source bytes independently of whether the current cuts have moved on."""
    public,proof=await media_evidence(db,project_id,source['media_id'],verified)
    if public['sha256']!=source['sha256'] or public['mime']!='video/mp4':raise HTTPException(409,'原片身份或类型与固定来源不一致')
    return proof


async def source_current(db,project_id,source):
    """A current-source hint is separate from immutable historical availability."""
    try:return source_from_analysis(await owned_analysis(db,project_id,source['analysis_id']))==source
    except HTTPException:return False
