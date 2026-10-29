"""Agent-specific execution detail; provider invocations live behind explicit persistent admission."""
from fastapi import APIRouter, Depends, Query, Request
from typing import Literal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.utils import paginate
from app.core.contracts.agent_execution import AgentExecutionAvailability, AgentRunCreate, AgentRunRead, AgentRunRetry, ModelConnectionTestCreate
from app.dependencies import get_db
from app.models.agent_execution import AgentRun
from app.schemas.common import ApiResponse, PaginatedData, created_response, paginated_response, success_response
from app.services import agent_execution as service
from app.api.workspace_actor import model_submitted_by

router = APIRouter(prefix='/agent-execution', tags=['agent-execution'])


@router.get('/availability', response_model=ApiResponse[AgentExecutionAvailability], operation_id='get_agent_execution_availability')
async def get_agent_execution_availability(request: Request, model_id: str | None = None,
    call_kind: Literal['agent_text','model_connection_test']='agent_text', db: AsyncSession = Depends(get_db, scope='function')):
    """Read configuration readiness without issuing any model request or returning credentials."""
    return success_response(await service.availability(db, getattr(request.app.state, 'agent_execution_runtime', None), model_id,call_kind=call_kind))


@router.post('/agents/{agent_id}/runs', response_model=ApiResponse[AgentRunRead], status_code=201, operation_id='create_agent_run')
async def create_agent_run(agent_id: str, body: AgentRunCreate, request: Request, db: AsyncSession = Depends(get_db, scope='function')):
    """Admit a version-pinned workflow; the scanner sees it only after a successful commit."""
    return created_response(await service.run_read(db, await service.create_run(db, getattr(request.app.state, 'agent_execution_runtime', None), agent_id, body,submitted_by=model_submitted_by(request))))


@router.get('/agents/{agent_id}/runs', response_model=ApiResponse[PaginatedData[AgentRunRead]], operation_id='list_agent_runs')
async def list_agent_runs(agent_id: str, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), db: AsyncSession = Depends(get_db, scope='function')):
    """Browse concrete attempts and node logs after refresh or process restart."""
    rows, total = await paginate(db, stmt=select(AgentRun).where(AgentRun.agent_id == agent_id).order_by(AgentRun.created_at.desc(), AgentRun.id), page=page, page_size=page_size)
    return paginated_response([await service.run_read(db, row) for row in rows], page=page, page_size=page_size, total=total)


@router.get('/runs/{run_id}', response_model=ApiResponse[AgentRunRead], operation_id='get_agent_run')
async def get_agent_run(run_id: str, db: AsyncSession = Depends(get_db, scope='function')):
    """Inspect the real current attempt rather than a generated demo result."""
    return success_response(await service.run_read(db, await service.get_run(db, run_id)))


@router.post('/runs/{run_id}/cancel', response_model=ApiResponse[AgentRunRead], operation_id='cancel_agent_run')
async def cancel_agent_run(run_id: str, db: AsyncSession = Depends(get_db, scope='function')):
    """Persist cancellation before the provider watcher can accept a late result."""
    return success_response(await service.run_read(db, await service.cancel_run(db, run_id)))


@router.post('/runs/{run_id}/retry', response_model=ApiResponse[AgentRunRead], status_code=201, operation_id='retry_agent_run')
async def retry_agent_run(run_id: str, body: AgentRunRetry, request: Request, db: AsyncSession = Depends(get_db, scope='function')):
    """Explicitly authorize a new full attempt with the prior request's immutable inputs."""
    return created_response(await service.run_read(db, await service.retry_run(db, getattr(request.app.state, 'agent_execution_runtime', None), run_id, body,submitted_by=model_submitted_by(request))))


@router.post('/models/{model_id}/connection-tests', response_model=ApiResponse[AgentRunRead], status_code=201, operation_id='create_model_connection_test')
async def create_model_connection_test(model_id: str, body: ModelConnectionTestCreate, request: Request, db: AsyncSession = Depends(get_db, scope='function')):
    """Authorize one low-volume real text request; this does not qualify image/video generation."""
    return created_response(await service.run_read(db, await service.create_connection_test(db, getattr(request.app.state, 'agent_execution_runtime', None), model_id, body,submitted_by=model_submitted_by(request))))


@router.get('/models/{model_id}/connection-tests', response_model=ApiResponse[PaginatedData[AgentRunRead]], operation_id='list_model_connection_tests')
async def list_model_connection_tests(model_id: str, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), db: AsyncSession = Depends(get_db, scope='function')):
    """Retain real connection outcomes and sanitized failures instead of a static success badge."""
    rows, total = await paginate(db, stmt=select(AgentRun).where(AgentRun.model_id == model_id, AgentRun.kind == 'connection_test').order_by(AgentRun.created_at.desc(), AgentRun.id), page=page, page_size=page_size)
    return paginated_response([await service.run_read(db, row) for row in rows], page=page, page_size=page_size, total=total)
