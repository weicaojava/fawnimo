"""Technical video analysis API; no language or generation model is invoked."""
from fastapi import APIRouter,Depends,HTTPException,Query,Request
from fastapi.responses import FileResponse
from pydantic import BaseModel,ConfigDict,Field,StrictInt
from sqlalchemy import select,update
from app.core.db import async_session_maker
from app.config import settings
from app.models.reverse import ReverseAnalysis
from app.schemas.common import ApiResponse,success_response
from app.services.reverse.ingestion import create_analysis,edit_cuts
from app.services.reverse.media_import import import_video_url
from app.services.reverse.views import analysis_view,artifact_path,owned_analysis
from app.services.studio.production import project_exists
from app.services.studio.production_media import media_path,scoped_media

router=APIRouter(prefix='/reverse/projects/{project_id}',tags=['reverse'])


def get_reverse_factory():
    """Supply the application's session factory without accepting database paths."""
    return async_session_maker


class AnalysisCreate(BaseModel):
    """Analyze an already imported project video using a stable request identity."""
    model_config=ConfigDict(extra='forbid')
    media_id:str=Field(min_length=1,max_length=64)
    request_key:str=Field(min_length=1,max_length=128)


class ConfirmedCut(BaseModel):
    """A human boundary references a real original frame and its normalized clock."""
    frame_index:int
    relative_us:int


class AnalysisRead(BaseModel):
    """Safe technical report with separate machine candidates and confirmed edits."""
    id:str
    media_id:str
    state:str
    source_hash:str
    revision:int
    manifest:dict|None
    confirmed_cuts:list[ConfirmedCut]|None
    reason:str


class CutsWrite(BaseModel):
    """Original decoded-frame boundaries, never nominal-fps timestamp guesses."""
    model_config=ConfigDict(extra='forbid')
    frame_indices:list[StrictInt]=Field(min_length=1,max_length=256)
    expected_revision:StrictInt=Field(ge=1)


class UrlImport(BaseModel):
    """Only the URL is client data; host trust remains server configuration."""
    model_config=ConfigDict(extra='forbid')
    url:str=Field(min_length=1,max_length=4096)


class ImportedMedia(BaseModel):
    """Return a project media identity for subsequent durable analysis."""
    media_id:str


@router.post('/import-url',response_model=ApiResponse[ImportedMedia],status_code=201)
async def post_reverse_url(project_id:str,body:UrlImport,factory=Depends(get_reverse_factory)):
    """Import a trusted public URL; local upload remains available without config."""
    async with factory() as db:return success_response({'media_id':await import_video_url(db,project_id,body.url)})


@router.get('/analyses',response_model=ApiResponse[list[AnalysisRead]])
async def get_reverse_analyses(project_id:str,limit:int=Query(50,ge=1,le=100),factory=Depends(get_reverse_factory)):
    """List lightweight technical analyses belonging to this project."""
    async with factory() as db:
        await project_exists(db,project_id)
        rows=(await db.execute(select(ReverseAnalysis).where(ReverseAnalysis.project_id==project_id).order_by(ReverseAnalysis.id).limit(limit))).scalars()
        return success_response([analysis_view(row,include_manifest=False) for row in rows])


@router.post('/analyses',response_model=ApiResponse[AnalysisRead],status_code=201)
async def post_reverse_analysis(project_id:str,body:AnalysisCreate,request:Request,factory=Depends(get_reverse_factory)):
    """Persist work before processing; the lifecycle scanner recovers it after restart."""
    scanner=getattr(request.app.state,'reverse_scanner',None)
    if scanner is None or scanner.task is None or scanner.task.done():raise HTTPException(503,'本地视频解析器尚未启用，请先配置媒体校验工具')
    analysis_id=await create_analysis(factory,project_id,body.media_id,body.request_key)
    async with factory() as db:return success_response(analysis_view(await owned_analysis(db,project_id,analysis_id)))


@router.get('/analyses/{analysis_id}',response_model=ApiResponse[AnalysisRead])
async def get_reverse_analysis(project_id:str,analysis_id:str,factory=Depends(get_reverse_factory)):
    """Read original frame clocks, proxy mapping and candidate evidence."""
    async with factory() as db:return success_response(analysis_view(await owned_analysis(db,project_id,analysis_id)))


@router.patch('/analyses/{analysis_id}/cuts',response_model=ApiResponse[AnalysisRead])
async def patch_reverse_cuts(project_id:str,analysis_id:str,body:CutsWrite,factory=Depends(get_reverse_factory)):
    """Confirm manually reviewed boundaries with version conflict protection."""
    await edit_cuts(factory,project_id,analysis_id,body.frame_indices,body.expected_revision)
    async with factory() as db:return success_response(analysis_view(await owned_analysis(db,project_id,analysis_id)))


@router.get('/analyses/{analysis_id}/proxy')
async def get_reverse_proxy(project_id:str,analysis_id:str,factory=Depends(get_reverse_factory)):
    """Serve only the owned normalized preview video."""
    async with factory() as db:
        row=await owned_analysis(db,project_id,analysis_id)
        path=artifact_path(row,(row.manifest or {}).get('proxy',{}).get('path',''))
        return FileResponse(path,media_type='video/mp4')


@router.get('/analyses/{analysis_id}/audio')
async def get_reverse_audio(project_id:str,analysis_id:str,factory=Depends(get_reverse_factory)):
    """Serve an extracted audio track without claiming any transcription."""
    async with factory() as db:
        row=await owned_analysis(db,project_id,analysis_id)
        audio=(row.manifest or {}).get('audio',{})
        if not audio.get('present'):raise HTTPException(404,'原视频没有音轨')
        return FileResponse(artifact_path(row,audio.get('wav_path','')),media_type='audio/wav')


@router.get('/analyses/{analysis_id}/frames/{frame_index}')
async def get_reverse_frame(project_id:str,analysis_id:str,frame_index:int,factory=Depends(get_reverse_factory)):
    """Serve exact original frames for candidate and manually added boundaries."""
    async with factory() as db:
        row=await owned_analysis(db,project_id,analysis_id)
        keyframe=next((item for item in (row.manifest or {}).get('keyframes',[]) if item['frame_index']==frame_index),None)
        if keyframe is not None:return FileResponse(artifact_path(row,keyframe['path']),media_type='image/jpeg')
        frames=(row.manifest or {}).get('video',{}).get('frames',[])
        if type(frame_index) is not int or not 0<=frame_index<len(frames):raise HTTPException(404,'原始帧不存在')
        from pathlib import Path
        if not Path(settings.production_probe_executable).is_file():raise HTTPException(503,'本地媒体工具尚未配置')
        from app.services.reverse.frame_preview import original_frame
        media=await scoped_media(db,project_id,row.media_id)
        directory=artifact_path(row,row.manifest['proxy']['path']).parent
        path=await original_frame(media_path(media.relative_path),directory,frame_index,settings.production_probe_executable)
        await scoped_media(db,project_id,row.media_id)
        return FileResponse(path,media_type='image/jpeg')
