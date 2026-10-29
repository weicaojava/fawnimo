"""Separate image candidates and explicit adoption; no legacy Film/Celery dispatch is used."""
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.utils import paginate
from app.core.contracts.asset_image_generation import AssetImageAdopt, AssetImageAvailability, AssetImageJobRead, AssetImageKind, AssetImageRetry, AssetImageSlotRead, AssetImageSubmit
from app.core.db import async_session_maker
from app.dependencies import get_db
from app.models.asset_image_generation import AssetImageJob
from app.schemas.common import ApiResponse, PaginatedData, created_response, paginated_response, success_response
from app.services import asset_image_generation as service
from app.api.workspace_actor import model_submitted_by

router = APIRouter(prefix='/asset-image-generation', tags=['asset-image-generation'])


def get_asset_image_factory():
    """Allow adoption tests to inject a fresh isolated transaction factory."""
    return async_session_maker


@router.get('/availability', response_model=ApiResponse[AssetImageAvailability], operation_id='get_asset_image_availability')
async def get_asset_image_availability(request: Request, model_id: str | None = None, db: AsyncSession = Depends(get_db, scope='function')):
    """Read only configuration readiness, never testing or invoking any provider."""
    return success_response(await service.availability(db, getattr(request.app.state, 'asset_image_generation_runtime', None), model_id))


@router.get('/assets/{kind}/{asset_id}/slots', response_model=ApiResponse[list[AssetImageSlotRead]], operation_id='list_asset_image_slots')
async def list_asset_image_slots(kind: AssetImageKind, asset_id: str, db: AsyncSession = Depends(get_db, scope='function')):
    """Read exact current slot snapshots for later confirmed CAS adoption."""
    return success_response(await service.slots(db, kind, asset_id))


@router.post('/assets/{kind}/{asset_id}/slots/{slot_id}/jobs', response_model=ApiResponse[AssetImageJobRead], status_code=201, operation_id='create_asset_image_job')
async def create_asset_image_job(kind: AssetImageKind, asset_id: str, slot_id: int, body: AssetImageSubmit, request: Request, db: AsyncSession = Depends(get_db, scope='function')):
    """Admit an explicitly confirmed single text-to-image request without changing an image slot."""
    return created_response(await service.job_read(db,await service.admit(db, getattr(request.app.state, 'asset_image_generation_runtime', None), kind, asset_id, slot_id, body,submitted_by=model_submitted_by(request))))


@router.get('/assets/{kind}/{asset_id}/jobs', response_model=ApiResponse[PaginatedData[AssetImageJobRead]], operation_id='list_asset_image_jobs')
async def list_asset_image_jobs(kind: AssetImageKind, asset_id: str, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), db: AsyncSession = Depends(get_db, scope='function')):
    """List persistent candidates and failures belonging to the exact global asset."""
    await service.slots(db, kind, asset_id)
    rows, total = await paginate(db, stmt=select(AssetImageJob).where(AssetImageJob.asset_kind == kind, AssetImageJob.asset_id == asset_id).order_by(AssetImageJob.created_at.desc(), AssetImageJob.id), page=page, page_size=page_size)
    return paginated_response([await service.job_read(db,row) for row in rows], page=page, page_size=page_size, total=total)


@router.get('/jobs/{job_id}', response_model=ApiResponse[AssetImageJobRead], operation_id='get_asset_image_job')
async def get_asset_image_job(job_id: str, db: AsyncSession = Depends(get_db, scope='function')):
    """Load actual persistent job state after a refresh or runtime restart."""
    return success_response(await service.job_read(db,await service.get_job(db, job_id)))


@router.post('/jobs/{job_id}/cancel', response_model=ApiResponse[AssetImageJobRead], operation_id='cancel_asset_image_job')
async def cancel_asset_image_job(job_id: str, db: AsyncSession = Depends(get_db, scope='function')):
    """Cancel queued/in-flight work; immutable completed candidates remain available."""
    return success_response(await service.job_read(db,await service.cancel(db, job_id)))


@router.post('/jobs/{job_id}/retry', response_model=ApiResponse[AssetImageJobRead], status_code=201, operation_id='retry_asset_image_job')
async def retry_asset_image_job(job_id: str, body: AssetImageRetry, request: Request, db: AsyncSession = Depends(get_db, scope='function')):
    """Create a separate explicit attempt; never silently retry a possibly charged image call."""
    return created_response(await service.job_read(db,await service.retry(db,getattr(request.app.state,'asset_image_generation_runtime',None),job_id,body,
        submitted_by=model_submitted_by(request))))


@router.get('/jobs/{job_id}/content', operation_id='get_asset_image_candidate_content', responses={200: {'content': {'image/png': {'schema': {'type': 'string', 'format': 'binary'}}, 'image/jpeg': {'schema': {'type': 'string', 'format': 'binary'}}}}})
async def get_asset_image_candidate_content(job_id: str, db: AsyncSession = Depends(get_db, scope='function')):
    """Serve only local, hash-verified candidate bytes; never redirect to an upstream URL."""
    job = await service.get_job(db, job_id)
    return Response(await service.verified_bytes(job), media_type=job.mime, headers={'Cache-Control': 'private, no-cache', 'ETag': '"' + job.sha256 + '"', 'X-Content-Type-Options': 'nosniff'})


@router.post('/jobs/{job_id}/adopt', response_model=ApiResponse[AssetImageJobRead], operation_id='adopt_asset_image_candidate')
async def adopt_asset_image_candidate(job_id: str, body: AssetImageAdopt, factory=Depends(get_asset_image_factory)):
    """Change one asset-owned slot only after explicit confirmation and current snapshot CAS."""
    return success_response(await service.adopt(factory, job_id, body))
