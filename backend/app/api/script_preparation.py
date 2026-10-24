"""Manual script preparation API, independent of paid models or final generation plans."""
from fastapi import APIRouter,Depends,HTTPException
from sqlalchemy import select
from app.core.db import async_session_maker
from app.core.contracts.script_preparation import ScriptCreate,ScriptPatch,ScriptPublish,ScriptRead,ScriptSnapshot,ScriptPreflight,ScriptCatalog
from app.models.script_preparation import ScriptDraft
from app.schemas.common import ApiResponse,success_response
from app.services.script_preparation.service import catalog,create_draft,draft_view,patch_draft,publish_draft,snapshot_view,preflight_snapshot
from app.services.studio.production import project_exists

router=APIRouter(prefix='/script-preparation/projects/{project_id}',tags=['script-preparation'])


def get_script_factory():
    """Resolve the server-owned session factory, never an imported database path."""
    return async_session_maker


@router.get('/catalog',response_model=ApiResponse[ScriptCatalog])
async def get_script_catalog(project_id:str,factory=Depends(get_script_factory)):
    """List complete actual project character and scene options with explicit bounds."""
    async with factory() as db:return success_response(await catalog(db,project_id))


@router.get('/drafts',response_model=ApiResponse[list[ScriptRead]])
async def list_script_drafts(project_id:str,factory=Depends(get_script_factory)):
    """Read a bounded project draft list with realtime fact usability."""
    async with factory() as db:
        await project_exists(db,project_id)
        ids=(await db.execute(select(ScriptDraft.id).where(ScriptDraft.project_id==project_id).order_by(ScriptDraft.id).limit(201))).scalars().all()
        if len(ids)>200:raise HTTPException(409,'工程剧本草稿超过200章，请缩小工程范围后编辑')
        return success_response([await draft_view(db,project_id,identifier) for identifier in ids])


@router.post('/drafts',response_model=ApiResponse[ScriptRead],status_code=201)
async def create_script_draft(project_id:str,body:ScriptCreate,factory=Depends(get_script_factory)):
    """Register one chapter's manual script without overwriting original novel text."""
    identifier=await create_draft(factory,project_id,body)
    async with factory() as db:return success_response(await draft_view(db,project_id,identifier))


@router.get('/drafts/{draft_id}',response_model=ApiResponse[ScriptRead])
async def get_script_draft(project_id:str,draft_id:str,factory=Depends(get_script_factory)):
    """Read original required denominator and current mapping deficiencies."""
    async with factory() as db:return success_response(await draft_view(db,project_id,draft_id))


@router.patch('/drafts/{draft_id}',response_model=ApiResponse[ScriptRead])
async def patch_script_draft(project_id:str,draft_id:str,body:ScriptPatch,factory=Depends(get_script_factory)):
    """Save a new draft revision with CAS conflict protection."""
    await patch_draft(factory,project_id,draft_id,body)
    async with factory() as db:return success_response(await draft_view(db,project_id,draft_id))


@router.post('/drafts/{draft_id}/publish',response_model=ApiResponse[ScriptSnapshot])
async def publish_script_draft(project_id:str,draft_id:str,body:ScriptPublish,factory=Depends(get_script_factory)):
    """Publish immutable manual evidence, not M4 or provider readiness."""
    identifier=await publish_draft(factory,project_id,draft_id,body)
    async with factory() as db:return success_response(await snapshot_view(db,project_id,identifier))


@router.get('/snapshots/{snapshot_id}',response_model=ApiResponse[ScriptSnapshot])
async def get_script_snapshot(project_id:str,snapshot_id:str,factory=Depends(get_script_factory)):
    """Export historical evidence without re-certifying current facts."""
    async with factory() as db:return success_response(await snapshot_view(db,project_id,snapshot_id))


@router.get('/snapshots/{snapshot_id}/preflight',response_model=ApiResponse[ScriptPreflight])
async def preflight_script_snapshot(project_id:str,snapshot_id:str,factory=Depends(get_script_factory)):
    """Reject revisions or actual facts that drifted after manual publication."""
    async with factory() as db:return success_response(await preflight_snapshot(db,project_id,snapshot_id))
