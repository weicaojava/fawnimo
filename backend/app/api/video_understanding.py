"""Project-owned opt-in vision/ASR jobs producing unreviewed Video DNA candidates."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from app.core.db import async_session_maker
from app.core.contracts.video_understanding import (
    UnderstandingAttemptControl, UnderstandingConnectionRead, UnderstandingConnectionWrite,
    UnderstandingCreate, UnderstandingDraftCreate, UnderstandingDraftRead, UnderstandingJobRead,
)
from app.schemas.common import ApiResponse, success_response
from app.services.video_understanding import service

router = APIRouter(prefix='/video-understanding/projects/{project_id}', tags=['video-understanding'])


def get_understanding_factory():
    """Allow isolated database acceptance without accepting filesystem paths from API input."""
    return async_session_maker


def worker_for(request: Request):
    """Read only the explicitly installed application runtime, with no hidden provider fallback."""
    return getattr(request.app.state, 'understanding_worker', None)


def active_worker(request: Request):
    """Persistent submissions require a live scanner rather than silently queued work that never runs."""
    worker = worker_for(request)
    if worker is None or worker.task is None or worker.task.done() or not worker.enabled:
        raise HTTPException(503, '视频理解运行器尚未启用，请在本地服务运行配置中启用后刷新；未提交模型请求')
    return worker


@router.get('/connection', response_model=ApiResponse[UnderstandingConnectionRead], operation_id='getVideoUnderstandingConnection')
async def get_connection(project_id: str, request: Request, factory=Depends(get_understanding_factory)):
    """Explain model setup and dedicated authorization state without making network requests."""
    async with factory() as db:
        return success_response(await service.connection_view(db, project_id, worker_for(request)))


@router.put('/connection', response_model=ApiResponse[UnderstandingConnectionRead], operation_id='saveVideoUnderstandingConnection')
async def put_connection(project_id: str, body: UnderstandingConnectionWrite, request: Request, factory=Depends(get_understanding_factory)):
    """Save only this project's explicit model connection; credentials are never returned or imported."""
    return success_response(await service.save_connection(factory, project_id, body, worker_for(request)))


@router.get('/jobs', response_model=ApiResponse[list[UnderstandingJobRead]], operation_id='listVideoUnderstandingJobs')
async def get_jobs(project_id: str, limit: int = Query(30, ge=1, le=50), factory=Depends(get_understanding_factory)):
    """Read actual persisted attempts and candidates without simulated success records."""
    async with factory() as db:
        return success_response(await service.list_jobs(db, project_id, limit))


@router.post('/jobs', response_model=ApiResponse[UnderstandingJobRead], status_code=201, operation_id='createVideoUnderstandingJob')
async def post_job(project_id: str, body: UnderstandingCreate, request: Request, factory=Depends(get_understanding_factory)):
    """Store source/config intent before the worker samples or sends any media."""
    identifier = await service.create_job(factory, project_id, body, active_worker(request))
    async with factory() as db:
        return success_response(await service.job_view(db, project_id, identifier))


@router.get('/jobs/{job_id}', response_model=ApiResponse[UnderstandingJobRead], operation_id='getVideoUnderstandingJob')
async def get_job(project_id: str, job_id: str, factory=Depends(get_understanding_factory)):
    """Reopen task progress, real evidence receipts, unreviewed transcripts and the final candidate."""
    async with factory() as db:
        return success_response(await service.job_view(db, project_id, job_id))


@router.post('/jobs/{job_id}/cancel', response_model=ApiResponse[UnderstandingJobRead], operation_id='cancelVideoUnderstandingJob')
async def post_cancel(project_id: str, job_id: str, body: UnderstandingAttemptControl, factory=Depends(get_understanding_factory)):
    """Fence publication first, then let the worker close its in-flight HTTP request."""
    await service.cancel_job(factory, project_id, job_id, body.expected_attempt)
    async with factory() as db:
        return success_response(await service.job_view(db, project_id, job_id))


@router.post('/jobs/{job_id}/retry', response_model=ApiResponse[UnderstandingJobRead], operation_id='retryVideoUnderstandingJob')
async def post_retry(project_id: str, job_id: str, body: UnderstandingAttemptControl, request: Request, factory=Depends(get_understanding_factory)):
    """A human explicitly retries the original saved connection; old attempts remain visible."""
    await service.retry_job(factory, project_id, job_id, body.expected_attempt, active_worker(request))
    async with factory() as db:
        return success_response(await service.job_view(db, project_id, job_id))


@router.post('/jobs/{job_id}/draft', response_model=ApiResponse[UnderstandingDraftRead], status_code=201, operation_id='createVideoUnderstandingDraft')
async def post_draft(project_id: str, job_id: str, body: UnderstandingDraftCreate, factory=Depends(get_understanding_factory)):
    """Create one editable, unreviewed DNA draft only after original human-confirmed cuts match."""
    identifier = await service.create_candidate_draft(factory, project_id, job_id, body.expected_analysis_revision)
    return success_response({'draft_id': identifier})
