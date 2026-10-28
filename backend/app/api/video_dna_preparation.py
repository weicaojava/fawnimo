"""Pure owned DNA preparation and impact reports, without provider execution routes."""
from fastapi import APIRouter, Depends
from app.core.db import async_session_maker
from app.core.contracts.video_dna_preparation import DnaPreparationRead, DnaCompareRequest, DnaCompareRead
from app.schemas.common import ApiResponse, success_response
from app.services.video_dna.compiler import preparation_view
from app.services.video_dna.comparison import compare_snapshots

router = APIRouter(prefix='/video-dna/projects/{project_id}', tags=['video-dna'])


def get_video_dna_preparation_factory():
    """Use the same owned application database as the semantic DNA journal."""
    return async_session_maker


@router.get('/snapshots/{snapshot_id}/preparation', response_model=ApiResponse[DnaPreparationRead], operation_id='prepare_video_dna_snapshot')
async def prepare_video_dna_snapshot(project_id: str, snapshot_id: str, factory=Depends(get_video_dna_preparation_factory)):
    """Return seven hypothetical layers from one immutable snapshot, with execution always disabled."""
    from app.services.video_dna.service import owned_snapshot
    async with factory() as db:
        return success_response(preparation_view(await owned_snapshot(db, project_id, snapshot_id)))


@router.post('/compare', response_model=ApiResponse[DnaCompareRead], operation_id='compare_video_dna_snapshots')
async def compare_video_dna_snapshots(project_id: str, body: DnaCompareRequest, factory=Depends(get_video_dna_preparation_factory)):
    """Compare old and new dependency users without modifying either saved version."""
    from app.services.video_dna.service import owned_snapshot
    async with factory() as db:
        old = await owned_snapshot(db, project_id, body.base_snapshot_id)
        new = await owned_snapshot(db, project_id, body.other_snapshot_id)
        return success_response(compare_snapshots(old, new))
