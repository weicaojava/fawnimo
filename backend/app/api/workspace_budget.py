"""Authenticated fixed-space ledger views and owner-only ceiling edits."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from app.api.video_runtime_setup import get_video_setup_factory
from app.core.contracts.workspace_budget import WorkspaceBudgetRead, WorkspaceBudgetWrite, WorkspaceBudgetPage
from app.schemas.common import ApiResponse, success_response
from app.services.worker.workspace_budget import read_workspace_budget, save_workspace_budget, list_workspace_entries

router = APIRouter(prefix='/video-runtime/workspace-budget', tags=['workspace-budget'])


@router.get('', response_model=ApiResponse[WorkspaceBudgetRead], operation_id='get_workspace_budget')
async def get_workspace_budget(factory=Depends(get_video_setup_factory)):
    """Return a coherent shared ceiling and actual held amounts without remote calls."""
    async with factory() as db:
        return success_response(await read_workspace_budget(db))


@router.put('', response_model=ApiResponse[WorkspaceBudgetRead], operation_id='save_workspace_budget')
async def put_workspace_budget(body: WorkspaceBudgetWrite, request: Request, factory=Depends(get_video_setup_factory)):
    """Trust only gateway-injected role and actor after the worker's private token fence."""
    if request.headers.get('x-workspace-role') != 'owner':
        raise HTTPException(403, '只有空间所有者可以调整空间预算')
    return success_response(await save_workspace_budget(factory, body, request.headers.get('x-identity-id', '')))


@router.get('/entries', response_model=ApiResponse[WorkspaceBudgetPage], operation_id='get_workspace_budget_entries')
async def get_workspace_budget_entries(after: str | None = Query(None, min_length=1, max_length=64), limit: int = Query(20, ge=1, le=100), factory=Depends(get_video_setup_factory)):
    """List bounded local accounting details; never expose model inputs or connection secrets."""
    async with factory() as db:
        return success_response(await list_workspace_entries(db, after=after, limit=limit))
