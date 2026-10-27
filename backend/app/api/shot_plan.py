"""Whole-chapter plan preparation and explicit trusted per-shot software admission."""
from fastapi import APIRouter,Depends,Request
from sqlalchemy import select
from app.core.db import async_session_maker
from app.core.contracts.shot_plan import PlanCatalog,PlanRead,CompiledPlanRead,PlanPreflight,PlannedJobRead,PlanCreate,PlanPatch,PlanCompile,PlannedSubmission,PlannedSubmissionRead,AssetRegistration,AssetRegistrationRead
from app.models.shot_plan import ShotPlanDraft
from app.schemas.common import ApiResponse,success_response
from app.services.shot_plan.service import catalog,create_draft,patch_draft,compile_draft,draft_view,plan_view,preflight_plan,register_asset,list_jobs,admit_planned_shot
from app.services.script_preparation.service import chapter_owned

router=APIRouter(prefix='/shot-plans/projects/{project_id}',tags=['shot-plans'])


def get_shot_plan_factory():
    """Keep the plan and worker database application-owned and test-isolatable."""
    return async_session_maker


@router.get('/catalog',response_model=ApiResponse[PlanCatalog])
async def get_shot_plan_catalog(project_id:str,chapter_id:str,request:Request,factory=Depends(get_shot_plan_factory)):
    """List complete bounded actual chapter choices and public trusted policy metadata."""
    async with factory() as db:return success_response(await catalog(db,project_id,chapter_id,getattr(request.app.state,'execution_runtime',None)))


@router.post('/asset-registrations',response_model=ApiResponse[AssetRegistrationRead],status_code=201)
async def register_shot_plan_asset(project_id:str,body:AssetRegistration,factory=Depends(get_shot_plan_factory)):
    """Create an initial asset version for an existing project identity, never a duplicate character."""
    return success_response(await register_asset(factory,project_id,body))


@router.get('/drafts',response_model=ApiResponse[list[PlanRead]])
async def list_shot_plan_drafts(project_id:str,chapter_id:str,request:Request,factory=Depends(get_shot_plan_factory)):
    """Read the unique chapter draft and its current preparation issues."""
    async with factory() as db:
        await chapter_owned(db,project_id,chapter_id)
        ids=(await db.execute(select(ShotPlanDraft.id).where(ShotPlanDraft.project_id==project_id,ShotPlanDraft.chapter_id==chapter_id))).scalars().all()
        return success_response([await draft_view(db,project_id,i,getattr(request.app.state,'execution_runtime',None)) for i in ids])


@router.post('/drafts',response_model=ApiResponse[PlanRead],status_code=201)
async def create_shot_plan_draft(project_id:str,body:PlanCreate,request:Request,factory=Depends(get_shot_plan_factory)):
    """Save incomplete preparation without invoking or enabling a model."""
    identifier=await create_draft(factory,project_id,body)
    async with factory() as db:return success_response(await draft_view(db,project_id,identifier,getattr(request.app.state,'execution_runtime',None)))


@router.get('/drafts/{draft_id}',response_model=ApiResponse[PlanRead])
async def get_shot_plan_draft(project_id:str,draft_id:str,request:Request,factory=Depends(get_shot_plan_factory)):
    """Read a single actual owned draft with safe historical plan summaries."""
    async with factory() as db:return success_response(await draft_view(db,project_id,draft_id,getattr(request.app.state,'execution_runtime',None)))


@router.patch('/drafts/{draft_id}',response_model=ApiResponse[PlanRead])
async def patch_shot_plan_draft(project_id:str,draft_id:str,body:PlanPatch,request:Request,factory=Depends(get_shot_plan_factory)):
    """Save a CAS revision; historical compiled inputs remain unchanged."""
    await patch_draft(factory,project_id,draft_id,body)
    async with factory() as db:return success_response(await draft_view(db,project_id,draft_id,getattr(request.app.state,'execution_runtime',None)))


@router.post('/drafts/{draft_id}/compile',response_model=ApiResponse[CompiledPlanRead])
async def compile_shot_plan(project_id:str,draft_id:str,body:PlanCompile,request:Request,factory=Depends(get_shot_plan_factory)):
    """Compile a complete chapter to immutable deterministic inputs without charging."""
    identifier=await compile_draft(factory,project_id,draft_id,body,getattr(request.app.state,'execution_runtime',None))
    async with factory() as db:return success_response(await plan_view(db,project_id,identifier))


@router.get('/plans/{plan_id}',response_model=ApiResponse[CompiledPlanRead])
async def get_shot_plan(project_id:str,plan_id:str,factory=Depends(get_shot_plan_factory)):
    """Export original preparation evidence without recertifying current validity."""
    async with factory() as db:return success_response(await plan_view(db,project_id,plan_id))


@router.get('/plans/{plan_id}/preflight',response_model=ApiResponse[PlanPreflight])
async def preflight_shot_plan(project_id:str,plan_id:str,request:Request,factory=Depends(get_shot_plan_factory)):
    """Separate current content usability from runtime, policy and budget availability."""
    async with factory() as db:return success_response(await preflight_plan(db,project_id,plan_id,getattr(request.app.state,'execution_runtime',None)))


@router.post('/plans/{plan_id}/shots/{shot_id}/submissions',response_model=ApiResponse[PlannedSubmissionRead],status_code=201)
async def submit_planned_shot(project_id:str,plan_id:str,shot_id:str,body:PlannedSubmission,request:Request,factory=Depends(get_shot_plan_factory)):
    """Replay original jobs before new eligibility checks; never accept client execution inputs."""
    return success_response({'job_id':await admit_planned_shot(factory,project_id,plan_id,shot_id,body.request_key,getattr(request.app.state,'execution_runtime',None))})


@router.get('/plans/{plan_id}/jobs',response_model=ApiResponse[list[PlannedJobRead]])
async def list_planned_shot_jobs(project_id:str,plan_id:str,factory=Depends(get_shot_plan_factory)):
    """Read lightweight plan/shot/task candidate provenance, without automatic adoption."""
    async with factory() as db:return success_response(await list_jobs(db,project_id,plan_id))
