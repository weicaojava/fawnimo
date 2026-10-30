"""Saved-source AI proposals: authenticated request identity here, business decisions in services."""
from fastapi import APIRouter,Depends,Request
from app.api.workspace_actor import model_submitted_by
from app.core.db import async_session_maker
from app.core.contracts.script_candidates import Operation,ScriptCandidateAvailability,ScriptCandidateCreate,ScriptCandidateDecision,ScriptCandidateRead
from app.schemas.common import ApiResponse,success_response
from app.services.script_preparation import candidates

router=APIRouter(prefix='/script-preparation/projects/{project_id}/chapters/{chapter_id}/candidates',tags=['script-candidates'])


def candidate_factory():
    """Use the server-owned workspace session factory; never accept an imported database path."""
    return async_session_maker


@router.get('/availability',response_model=ApiResponse[ScriptCandidateAvailability])
async def candidate_availability(project_id:str,chapter_id:str,request:Request,operation:Operation='optimize',
        source_field:str='raw_text',shot_id:str|None=None,model_id:str|None=None,factory=Depends(candidate_factory)):
    """Inspect saved source and exact model price; this GET never submits a model request."""
    return success_response(await candidates.inspect_availability(factory,getattr(request.app.state,'agent_execution_runtime',None),
        project_id,chapter_id,operation,source_field,shot_id,model_id))


@router.get('',response_model=ApiResponse[list[ScriptCandidateRead]])
async def list_script_candidates(project_id:str,chapter_id:str,factory=Depends(candidate_factory)):
    """Recover the actual proposal history for this chapter after refresh."""
    return success_response(await candidates.list_candidates(factory,project_id,chapter_id))


@router.post('',response_model=ApiResponse[ScriptCandidateRead],status_code=201)
async def create_script_candidate(project_id:str,chapter_id:str,body:ScriptCandidateCreate,request:Request,factory=Depends(candidate_factory)):
    """Authorize one bounded, original-project call and recover exact repeated intent."""
    return success_response(await candidates.admit(factory,getattr(request.app.state,'agent_execution_runtime',None),
        project_id,chapter_id,body,submitted_by=model_submitted_by(request)))


@router.get('/{candidate_id}',response_model=ApiResponse[ScriptCandidateRead])
async def get_script_candidate(project_id:str,chapter_id:str,candidate_id:str,factory=Depends(candidate_factory)):
    """Read a validated candidate without importing its text into human edits."""
    return success_response(await candidates.read_candidate(factory,project_id,chapter_id,candidate_id))


@router.post('/{candidate_id}/adopt',response_model=ApiResponse[ScriptCandidateRead])
async def adopt_script_candidate(project_id:str,chapter_id:str,candidate_id:str,body:ScriptCandidateDecision,factory=Depends(candidate_factory)):
    """Adopt the exact reviewed result only if its original saved source is still current."""
    return success_response(await candidates.adopt(factory,project_id,chapter_id,candidate_id,body))


@router.post('/{candidate_id}/dismiss',response_model=ApiResponse[ScriptCandidateRead])
async def dismiss_script_candidate(project_id:str,chapter_id:str,candidate_id:str,factory=Depends(candidate_factory)):
    """Ignore a proposal and cancel unsent work; sent model fees remain unknown."""
    return success_response(await candidates.dismiss(factory,project_id,chapter_id,candidate_id))
