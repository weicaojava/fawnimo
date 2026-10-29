"""Expose the explicit DNA-to-pending-production handoff through the real API."""
from fastapi import APIRouter, Depends
from app.api.video_dna import get_video_dna_factory
from app.core.contracts.dna_production import DnaProductionRead, DnaProductionWrite
from app.schemas.common import ApiResponse, success_response, created_response
from app.services.video_dna.production import import_draft, preview

router = APIRouter(prefix='/video-dna/projects/{project_id}/drafts/{draft_id}', tags=['video-dna-production'])


@router.get('/production-preview', response_model=ApiResponse[dict])
async def preview_dna_production(project_id: str, draft_id: str, factory=Depends(get_video_dna_factory)):
    """Show exact counts and prior import identity before a user writes any records."""
    return success_response(await preview(factory, project_id, draft_id))


@router.post('/production-imports', response_model=ApiResponse[DnaProductionRead], status_code=201)
async def import_dna_production(project_id: str, draft_id: str, body: DnaProductionWrite, factory=Depends(get_video_dna_factory)):
    """Import one revision atomically, while leaving all production states pending."""
    return created_response(await import_draft(factory, project_id, draft_id, body))
