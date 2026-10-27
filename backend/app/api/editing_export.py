"""Owned local-export HTTP handlers; all state transitions live in the service layer."""
from typing import Literal
from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from app.config import settings
from app.core.db import async_session_maker
from app.core.contracts.editing_export import ExportRequest, ExportCancel, ExportRead, ExportCatalog
from app.schemas.common import ApiResponse, success_response
from app.services.editing import exports

router = APIRouter(prefix='/editing/projects/{project_id}', tags=['editing-export'])


def get_editing_export_factory():
    """Keep export admission, worker and preparation on the same application-owned database."""
    return async_session_maker


@router.get('/exports', response_model=ApiResponse[ExportCatalog], operation_id='list_editing_exports')
async def list_editing_exports(project_id: str, chapter_id: str, factory=Depends(get_editing_export_factory)):
    """Return actual bounded local job history and tool availability."""
    return success_response(await exports.list_exports(factory, project_id, chapter_id, settings.production_probe_executable))


@router.post('/publications/{publication_id}/exports', response_model=ApiResponse[ExportRead], status_code=201, operation_id='create_editing_export')
async def create_editing_export(project_id: str, publication_id: str, body: ExportRequest, factory=Depends(get_editing_export_factory)):
    """Persist one explicit local render of the selected immutable timeline."""
    return success_response(await exports.create_export(factory, project_id, publication_id, body.request_key, settings.production_probe_executable))


@router.get('/exports/{export_id}', response_model=ApiResponse[ExportRead], operation_id='get_editing_export')
async def get_editing_export(project_id: str, export_id: str, factory=Depends(get_editing_export_factory)):
    """Read durable status and verified ready files after a page or application restart."""
    return success_response(await exports.get_export(factory, project_id, export_id))


@router.post('/exports/{export_id}/cancel', response_model=ApiResponse[ExportRead], operation_id='cancel_editing_export')
async def cancel_editing_export(project_id: str, export_id: str, body: ExportCancel, factory=Depends(get_editing_export_factory)):
    """Request cancellation without accepting client-provided worker state."""
    return success_response(await exports.cancel_export(factory, project_id, export_id))


@router.get('/exports/{export_id}/content/{kind}', operation_id='read_editing_export_content')
async def read_editing_export_content(project_id: str, export_id: str, kind: Literal['video', 'subtitles_srt', 'subtitles_vtt', 'package'], factory=Depends(get_editing_export_factory)):
    """Stream a verified owned artifact with safe fixed download filenames."""
    path, name, mime = await exports.content_file(factory, project_id, export_id, kind)
    return FileResponse(path, media_type=mime, filename=name)
