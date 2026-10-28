"""Project-owned Video DNA editing API; preparation/compare belong to a separate pure module."""
from fastapi import APIRouter,Depends
from fastapi.responses import Response
from app.core.db import async_session_maker
from app.core.contracts.video_dna import DnaCatalog,DnaCreate,DnaPatch,DnaFreeze,DnaImport,DnaDraft,DnaSnapshot,DnaPreflight
from app.schemas.common import ApiResponse,success_response
from app.services.video_dna import service
from app.services.script_preparation.service import canonical

router=APIRouter(prefix='/video-dna/projects/{project_id}',tags=['video-dna'])


def get_video_dna_factory():
    """Use the same application-owned SQLite factory while allowing isolated real tests."""
    return async_session_maker


@router.get('/catalog',response_model=ApiResponse[DnaCatalog])
async def get_video_dna_catalog(project_id:str,factory=Depends(get_video_dna_factory)):
    """List complete bounded actual source analyses, drafts and production versions."""
    async with factory() as db:return success_response(await service.catalog(db,project_id))


@router.post('/drafts',response_model=ApiResponse[DnaDraft],status_code=201)
async def create_video_dna_draft(project_id:str,body:DnaCreate,factory=Depends(get_video_dna_factory)):
    """Create an explicit editable hypothesis with fixed original source clocks."""
    identifier=await service.create_draft(factory,project_id,body)
    async with factory() as db:return success_response(await service.draft_view(db,project_id,identifier))


@router.get('/drafts/{draft_id}',response_model=ApiResponse[DnaDraft])
async def get_video_dna_draft(project_id:str,draft_id:str,factory=Depends(get_video_dna_factory)):
    """Read every saved field, with source drift and missing evidence kept explicit."""
    async with factory() as db:return success_response(await service.draft_view(db,project_id,draft_id))


@router.patch('/drafts/{draft_id}',response_model=ApiResponse[DnaDraft])
async def patch_video_dna_draft(project_id:str,draft_id:str,body:DnaPatch,factory=Depends(get_video_dna_factory)):
    """Save a full document CAS revision without changing its fixed source."""
    await service.patch_draft(factory,project_id,draft_id,body)
    async with factory() as db:return success_response(await service.draft_view(db,project_id,draft_id))


@router.post('/drafts/{draft_id}/freeze',response_model=ApiResponse[DnaSnapshot],status_code=201)
async def freeze_video_dna_draft(project_id:str,draft_id:str,body:DnaFreeze,factory=Depends(get_video_dna_factory)):
    """Freeze integer-only human declarations and real owned version/media evidence."""
    identifier=await service.freeze_draft(factory,project_id,draft_id,body)
    async with factory() as db:return success_response(await service.snapshot_view(db,project_id,identifier))


@router.get('/snapshots/{snapshot_id}',response_model=ApiResponse[DnaSnapshot])
async def get_video_dna_snapshot(project_id:str,snapshot_id:str,factory=Depends(get_video_dna_factory)):
    """Read immutable history without following a newer set of confirmed cuts."""
    async with factory() as db:return success_response(await service.snapshot_view(db,project_id,snapshot_id))


@router.get('/snapshots/{snapshot_id}/preflight',response_model=ApiResponse[DnaPreflight])
async def preflight_video_dna_snapshot(project_id:str,snapshot_id:str,factory=Depends(get_video_dna_factory)):
    """Separate byte availability from current-source differences and content quality."""
    async with factory() as db:return success_response(await service.preflight_snapshot(db,project_id,snapshot_id))


@router.get('/snapshots/{snapshot_id}/export')
async def export_video_dna_snapshot(project_id:str,snapshot_id:str,factory=Depends(get_video_dna_factory)):
    """Download exactly the portable schema, with no private path or execution credential."""
    async with factory() as db:data=await service.snapshot_view(db,project_id,snapshot_id)
    envelope=service.portable_snapshot(data['id'],data['payload'],data['content_hash'])
    content=canonical(envelope).encode('utf8')
    return Response(content,media_type='application/json',headers={'Content-Disposition':'attachment; filename="video-dna-v1.json"'})


@router.post('/imports',response_model=ApiResponse[DnaDraft],status_code=201)
async def import_video_dna_draft(project_id:str,body:DnaImport,factory=Depends(get_video_dna_factory)):
    """Import inert v1 content as a new draft; no snapshot or model task is imported."""
    identifier=await service.import_draft(factory,project_id,body)
    async with factory() as db:return success_response(await service.draft_view(db,project_id,identifier))
