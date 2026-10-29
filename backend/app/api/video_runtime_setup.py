"""Non-secret execution setup and project-owned budget controls, never direct billing."""
import json
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from app.core.contracts.video_runtime_setup import ProjectBudgetRead, ProjectBudgetWrite, ProjectBudgetPage, VideoRuntimeSetupRead, VideoExecutionPlanPreview
from app.config import settings
from app.core.db import async_session_maker
from app.schemas.common import ApiResponse, success_response
from app.services.worker.workspace_video_runtime import configuration_template, read_project_budget, save_project_budget, setup_projection, preview_executable_plan
from app.services.worker.project_budget import list_project_entries

router = APIRouter(prefix='/video-runtime', tags=['video-runtime'])


def get_video_setup_factory():
    """Resolve the application database while allowing isolated API verification."""
    return async_session_maker


@router.get('/setup', response_model=ApiResponse[VideoRuntimeSetupRead], operation_id='get_video_runtime_setup')
async def get_video_runtime_setup(request: Request):
    """Read availability without touching credentials, accepting jobs or returning private paths."""
    view = dict(getattr(request.app.state, 'workspace_video_setup', setup_projection()))
    runtime = getattr(request.app.state, 'execution_runtime', None)
    view['running'] = bool(runtime and runtime.allow_submission and runtime.running)
    if view['enabled'] and view['configured'] and not view['running'] and view['issue_code'] == 'configured':
        view['issue_code'] = 'not_running'
        view['message'] = '配置已保存，执行器尚未启动或已停止，未接受新生成任务。'
    return success_response(view)


@router.get('/configuration-template', operation_id='get_video_configuration_template')
async def get_video_configuration_template():
    """Download an inert server-owned template; no key, endpoint override or guessed price is included."""
    return Response(json.dumps(configuration_template(), ensure_ascii=False, indent=2), media_type='application/json',
                    headers={'Content-Disposition': 'attachment; filename="video-runtime.example.json"'})


@router.get('/projects/{project_id}/budget', response_model=ApiResponse[ProjectBudgetRead], operation_id='get_video_project_budget')
async def get_video_project_budget(project_id: str, factory=Depends(get_video_setup_factory)):
    """Show this project's actual ledger without reading or altering another project."""
    async with factory() as db:
        return success_response(await read_project_budget(db, project_id))


@router.put('/projects/{project_id}/budget', response_model=ApiResponse[ProjectBudgetRead], operation_id='save_video_project_budget')
async def save_video_project_budget(project_id: str, body: ProjectBudgetWrite, request: Request, factory=Depends(get_video_setup_factory)):
    """Save an explicitly chosen ceiling; saving does not start generation or settle provider charges."""
    if settings.workspace_id is not None and request.headers.get('x-workspace-role') != 'owner':
        raise HTTPException(403, '只有所有者可以调整项目预算')
    actor = request.headers.get('x-identity-id') if settings.workspace_id is not None else None
    if settings.workspace_id is not None and not actor:
        raise HTTPException(403, '项目预算需要受信的操作者身份')
    return success_response(await save_project_budget(factory, project_id, body, actor_id=actor))


@router.get('/projects/{project_id}/budget/entries', response_model=ApiResponse[ProjectBudgetPage], operation_id='get_project_budget_entries')
async def get_project_budget_entries(project_id: str, after: str | None = Query(None, min_length=1, max_length=64), limit: int = Query(20, ge=1, le=100), factory=Depends(get_video_setup_factory)):
    """Read a bounded ledger belonging only to the current owned project."""
    async with factory() as db:
        return success_response(await list_project_entries(db, project_id, after=after, limit=limit))


@router.get('/projects/{project_id}/plans/{plan_id}/request-preview', response_model=ApiResponse[VideoExecutionPlanPreview], operation_id='preview_executable_video_plan')
async def preview_executable_video_plan(project_id: str, plan_id: str, request: Request, factory=Depends(get_video_setup_factory)):
    """Read the opted-in transport body; no task, reservation or remote request is created."""
    async with factory() as db:
        return success_response(await preview_executable_plan(db, project_id, plan_id, getattr(request.app.state, 'execution_runtime', None)))
