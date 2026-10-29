"""Read-only lightweight summaries for independent durable jobs outside legacy Film task storage."""
from fastapi import APIRouter, Depends, Query
from app.core.db import async_session_maker
from app.core.contracts.workspace_tasks import WorkspaceTaskFeed
from app.schemas.common import ApiResponse, success_response
from app.services.workspace_tasks import recent_tasks

router = APIRouter(prefix='/workspace-tasks', tags=['workspace-tasks'])


def get_workspace_task_factory():
    """Use the application database while allowing genuinely isolated read-only acceptance tests."""
    return async_session_maker


@router.get('/recent', response_model=ApiResponse[WorkspaceTaskFeed], operation_id='listWorkspaceTasks')
async def get_workspace_tasks(limit: int = Query(100, ge=1, le=200), factory=Depends(get_workspace_task_factory)):
    """Read bounded generic job metadata without dispatching a model or loading business outputs."""
    return success_response(await recent_tasks(factory, limit))
