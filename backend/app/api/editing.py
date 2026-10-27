"""Owned editing preparation and explicit selection API, separate from local exports."""
from fastapi import APIRouter,Depends
from sqlalchemy import select
from app.core.db import async_session_maker
from app.core.contracts.editing import SelectionWrite,SelectionRead,SelectionHistoryRead,EditCatalog,EditDraftCreate,EditDraftPatch,EditFreezeWrite,EditDraftRead,EditPublicationRead,EditPreflightRead
from app.models.editing import EditDraft
from app.schemas.common import ApiResponse,success_response
from app.services.editing import service
from app.services.editing.common import chapter_owned

router=APIRouter(prefix='/editing/projects/{project_id}',tags=['editing'])


def get_editing_factory():
    """Use the same owned application DB while allowing real SQLite isolation in tests."""
    return async_session_maker


@router.get('/catalog',response_model=ApiResponse[EditCatalog])
async def get_edit_catalog(project_id:str,chapter_id:str,plan_id:str|None=None,factory=Depends(get_editing_factory)):
    """List bounded actual plan, Take and owned MP4 choices."""
    async with factory() as db:return success_response(await service.catalog(db,project_id,chapter_id,plan_id))


@router.post('/selections',response_model=ApiResponse[SelectionRead],status_code=201)
async def write_edit_selection(project_id:str,body:SelectionWrite,factory=Depends(get_editing_factory)):
    """Explicitly adopt or switch once, preserving old events on replay."""
    return success_response(await service.write_selection(factory,project_id,body))


@router.get('/selections/history',response_model=ApiResponse[SelectionHistoryRead])
async def get_edit_selection_history(project_id:str,shot_id:str,factory=Depends(get_editing_factory)):
    """Read historical pointers without claiming present selection from an old event."""
    async with factory() as db:return success_response(await service.selection_history(db,project_id,shot_id))


@router.get('/drafts',response_model=ApiResponse[list[EditDraftRead]])
async def list_edit_drafts(project_id:str,chapter_id:str,factory=Depends(get_editing_factory)):
    """Return the unique chapter editing draft, if one exists."""
    async with factory() as db:
        await chapter_owned(db,project_id,chapter_id)
        rows=(await db.execute(select(EditDraft.id).where(EditDraft.project_id==project_id,EditDraft.chapter_id==chapter_id))).scalars().all()
        return success_response([await service.draft_view(db,project_id,i) for i in rows])


@router.post('/drafts',response_model=ApiResponse[EditDraftRead],status_code=201)
async def create_edit_draft(project_id:str,body:EditDraftCreate,factory=Depends(get_editing_factory)):
    """Save an incomplete controlled assembly without exporting or changing selection."""
    identifier=await service.create_draft(factory,project_id,body)
    async with factory() as db:return success_response(await service.draft_view(db,project_id,identifier))


@router.get('/drafts/{draft_id}',response_model=ApiResponse[EditDraftRead])
async def get_edit_draft(project_id:str,draft_id:str,factory=Depends(get_editing_factory)):
    """Read complete form fields and current content readiness."""
    async with factory() as db:return success_response(await service.draft_view(db,project_id,draft_id))


@router.patch('/drafts/{draft_id}',response_model=ApiResponse[EditDraftRead])
async def patch_edit_draft(project_id:str,draft_id:str,body:EditDraftPatch,factory=Depends(get_editing_factory)):
    """Save a full new CAS revision while retaining immutable history."""
    await service.patch_draft(factory,project_id,draft_id,body)
    async with factory() as db:return success_response(await service.draft_view(db,project_id,draft_id))


@router.post('/drafts/{draft_id}/freeze',response_model=ApiResponse[EditPublicationRead],status_code=201)
async def freeze_edit_draft(project_id:str,draft_id:str,body:EditFreezeWrite,factory=Depends(get_editing_factory)):
    """Lock complete real source facts and human retention declarations."""
    identifier=await service.freeze_draft(factory,project_id,draft_id,body)
    async with factory() as db:return success_response(await service.publication_view(db,project_id,identifier))


@router.get('/publications/{publication_id}',response_model=ApiResponse[EditPublicationRead])
async def get_edit_publication(project_id:str,publication_id:str,factory=Depends(get_editing_factory)):
    """Read a portable display view without private filesystem data."""
    async with factory() as db:return success_response(await service.publication_view(db,project_id,publication_id))


@router.get('/publications/{publication_id}/preflight',response_model=ApiResponse[EditPreflightRead])
async def preflight_edit_publication(project_id:str,publication_id:str,factory=Depends(get_editing_factory)):
    """Recheck historical source integrity without following later current selections."""
    async with factory() as db:return success_response(await service.preflight_publication(db,project_id,publication_id))
