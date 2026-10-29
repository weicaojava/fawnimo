"""Typed local Agent configuration endpoints; intentionally no model-run endpoint."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.utils import apply_keyword_filter, paginate
from app.dependencies import get_db
from app.models.agent_config import AgentConfig, AgentConfigRevision
from app.schemas.agent_config import AgentConfigCreate, AgentConfigExport, AgentConfigRead, AgentConfigRevisionRead, AgentConfigUpdate
from app.schemas.common import ApiResponse, PaginatedData, created_response, empty_response, paginated_response, success_response
from app.services.agent_config import copy_agent, create_agent, draft_of, import_agent, owned_agent, update_agent

router = APIRouter(prefix='/agents', tags=['agents'])


@router.get('', response_model=ApiResponse[PaginatedData[AgentConfigRead]], operation_id='list_agent_configs')
async def list_agent_configs(db: AsyncSession = Depends(get_db, scope='function'), q: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    """Browse persisted configs; the empty state is a successful empty collection."""
    statement = apply_keyword_filter(select(AgentConfig), q=q, fields=[AgentConfig.name, AgentConfig.description]).order_by(AgentConfig.updated_at.desc(), AgentConfig.id)
    rows, total = await paginate(db, stmt=statement, page=page, page_size=page_size)
    return paginated_response([AgentConfigRead.model_validate(row) for row in rows], page=page, page_size=page_size, total=total)


@router.post('', response_model=ApiResponse[AgentConfigRead], status_code=201, operation_id='create_agent_config')
async def create_agent_config(body: AgentConfigCreate, db: AsyncSession = Depends(get_db, scope='function')):
    """Save complete settings and first history without executing a model."""
    return created_response(AgentConfigRead.model_validate(await create_agent(db, body)))


@router.post('/import', response_model=ApiResponse[AgentConfigRead], status_code=201, operation_id='import_agent_config')
async def import_agent_config(body: AgentConfigExport, db: AsyncSession = Depends(get_db, scope='function')):
    """Import a bounded portable config under a new database identity."""
    return created_response(AgentConfigRead.model_validate(await import_agent(db, body)))


@router.get('/{agent_id}', response_model=ApiResponse[AgentConfigRead], operation_id='get_agent_config')
async def get_agent_config(agent_id: str, db: AsyncSession = Depends(get_db, scope='function')):
    """Load complete saved metadata and workflow draft."""
    return success_response(AgentConfigRead.model_validate(await owned_agent(db, agent_id)))


@router.patch('/{agent_id}', response_model=ApiResponse[AgentConfigRead], operation_id='update_agent_config')
async def update_agent_config(agent_id: str, body: AgentConfigUpdate, db: AsyncSession = Depends(get_db, scope='function')):
    """Persist edits or favorite/default changes with an optional version guard."""
    return success_response(AgentConfigRead.model_validate(await update_agent(db, agent_id, body)))


@router.delete('/{agent_id}', response_model=ApiResponse[None], operation_id='delete_agent_config')
async def delete_agent_config(agent_id: str, db: AsyncSession = Depends(get_db, scope='function')):
    """Delete a config and its own revision history, leaving every other config intact."""
    agent = await owned_agent(db, agent_id)
    await db.delete(agent)
    await db.flush()
    return empty_response()


@router.post('/{agent_id}/copy', response_model=ApiResponse[AgentConfigRead], status_code=201, operation_id='copy_agent_config')
async def copy_agent_config(agent_id: str, db: AsyncSession = Depends(get_db, scope='function')):
    """Create a real, independently editable copy."""
    return created_response(AgentConfigRead.model_validate(await copy_agent(db, agent_id)))


@router.get('/{agent_id}/export', response_model=ApiResponse[AgentConfigExport], operation_id='export_agent_config')
async def export_agent_config(agent_id: str, db: AsyncSession = Depends(get_db, scope='function')):
    """Export portable settings; file download is handled by the caller after success."""
    return success_response(AgentConfigExport(config=draft_of(await owned_agent(db, agent_id))))


@router.get('/{agent_id}/revisions', response_model=ApiResponse[list[AgentConfigRevisionRead]], operation_id='list_agent_config_revisions')
async def list_agent_config_revisions(agent_id: str, db: AsyncSession = Depends(get_db, scope='function')):
    """Inspect actual saved versions instead of a fabricated history list."""
    await owned_agent(db, agent_id)
    rows = (await db.scalars(select(AgentConfigRevision).where(AgentConfigRevision.agent_id == agent_id).order_by(AgentConfigRevision.version.desc()).limit(100))).all()
    return success_response([AgentConfigRevisionRead(version=row.version, saved_at=row.saved_at, document=row.document) for row in rows])
