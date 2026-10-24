"""Project production API: explicit scope and typed inputs, with logic in services."""
from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.dependencies import get_db
from app.models.production import ProductionAsset, ProductionMedia, ProductionTake
from app.schemas.common import ApiResponse, success_response, created_response
from app.schemas.studio.production import ArchiveWrite, AssetWrite, TakeWrite, VersionWrite
from app.services.studio.production import add_version, adopt_take, asset_history, asset_in_project, create_asset, project_exists, read_take, select_take, serialize
from app.services.studio.production_media import import_archive, media_path, read_bounded, scoped_media, store_batch

router=APIRouter(prefix='/production/projects/{project_id}',tags=['production'])


@router.get('/assets',response_model=ApiResponse[list[dict]])
async def list_assets(project_id: str, db: AsyncSession=Depends(get_db)):
    """Read this project's versioned library; global legacy browsing is separate."""
    await project_exists(db,project_id)
    assets=(await db.execute(select(ProductionAsset).where(ProductionAsset.project_id==project_id).order_by(ProductionAsset.created_at).limit(200))).scalars()
    return success_response([await asset_history(db,project_id,a.id) for a in assets])


@router.post('/assets',response_model=ApiResponse[dict],status_code=201)
async def post_asset(project_id: str, body: AssetWrite, db: AsyncSession=Depends(get_db)):
    """Create a real project asset and initial snapshot."""
    return created_response(await create_asset(db,project_id,body))


@router.get('/assets/{asset_id}',response_model=ApiResponse[dict])
async def get_asset(project_id: str, asset_id: str, db: AsyncSession=Depends(get_db)):
    """Inspect previous revisions and their affected shots."""
    return success_response(await asset_history(db,project_id,asset_id))


@router.post('/assets/{asset_id}/versions',response_model=ApiResponse[dict],status_code=201)
async def post_version(project_id: str, asset_id: str, body: VersionWrite, db: AsyncSession=Depends(get_db)):
    """Append a revision without mutating history."""
    return created_response(serialize(await add_version(db,await asset_in_project(db,project_id,asset_id),body)))


@router.patch('/assets/{asset_id}/archive',response_model=ApiResponse[dict])
async def archive_asset(project_id: str, asset_id: str, body: ArchiveWrite, db: AsyncSession=Depends(get_db)):
    """Archive/recover the asset identity while preserving references."""
    asset=await asset_in_project(db,project_id,asset_id);asset.archived=body.archived;await db.flush()
    return success_response(await asset_history(db,project_id,asset_id))


@router.get('/media',response_model=ApiResponse[list[dict]])
async def list_media(project_id: str, db: AsyncSession=Depends(get_db)):
    """List only media owned by the selected project."""
    await project_exists(db,project_id)
    rows=(await db.execute(select(ProductionMedia).where(ProductionMedia.project_id==project_id).limit(200))).scalars()
    return success_response([serialize(row) for row in rows])


@router.post('/media',response_model=ApiResponse[list[dict]],status_code=201)
async def post_media(project_id: str, file: UploadFile=File(...), db: AsyncSession=Depends(get_db)):
    """Import bounded binary media; the supplied name is never a storage path."""
    rows=await store_batch(db,project_id,[(file.filename or '',await read_bounded(file))])
    return created_response([serialize(row) for row in rows])


@router.post('/media-bundle',response_model=ApiResponse[list[dict]],status_code=201)
async def post_bundle(project_id: str, file: UploadFile=File(...), db: AsyncSession=Depends(get_db)):
    """Validate a complete media-only bundle before writing any member."""
    return created_response([serialize(row) for row in await import_archive(db,project_id,await read_bounded(file))])


@router.get('/media/{media_id}/content')
async def get_content(project_id: str, media_id: str, db: AsyncSession=Depends(get_db)):
    """Serve hash-verified local content with explicit ownership checks."""
    row=await scoped_media(db,project_id,media_id)
    return FileResponse(media_path(row.relative_path),media_type=row.mime,filename=row.name)


@router.post('/takes',response_model=ApiResponse[dict],status_code=201)
async def post_take(project_id: str, body: TakeWrite, db: AsyncSession=Depends(get_db)):
    """Record imported adoption without inventing model provenance."""
    return created_response(await adopt_take(db,project_id,body))


@router.get('/takes',response_model=ApiResponse[list[dict]])
async def list_takes(project_id: str, db: AsyncSession=Depends(get_db)):
    """Inspect immutable take history in the selected project."""
    await project_exists(db,project_id)
    ids=(await db.execute(select(ProductionTake.id).where(ProductionTake.project_id==project_id).limit(200))).scalars()
    return success_response([await read_take(db,project_id,take_id) for take_id in ids])


@router.get('/takes/{take_id}',response_model=ApiResponse[dict])
async def get_take(project_id: str, take_id: str, db: AsyncSession=Depends(get_db)):
    """Read the exact adopted versions."""
    return success_response(await read_take(db,project_id,take_id))


@router.post('/takes/{take_id}/select',response_model=ApiResponse[dict])
async def post_selection(project_id: str, take_id: str, db: AsyncSession=Depends(get_db)):
    """Select a previous take without rewriting its snapshots."""
    return success_response(await select_take(db,project_id,take_id))
