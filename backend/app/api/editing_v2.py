"""Thin owned v2 transition endpoints; v1 plans and export endpoints keep their old semantics."""
from typing import Literal
from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy import select
from app.config import settings
from app.core.db import async_session_maker
from app.core.contracts.editing_v2 import TransitionCreate, TransitionPatch, TransitionFreeze, TransitionDraftRead, TransitionPublicationRead
from app.core.contracts.editing_export import ExportRequest, ExportCancel, ExportRead, ExportCatalog
from app.models.editing_v2 import TransitionDraft
from app.schemas.common import ApiResponse, success_response
from app.services.editing import transitions, transition_exports as exports
from app.services.editing.common import chapter_owned

router = APIRouter(prefix='/editing-v2/projects/{project_id}', tags=['editing-v2'])


def get_transition_factory():
    """Keep transition forms, publishers and local workers on one application-owned database."""
    return async_session_maker


@router.get('/drafts', response_model=ApiResponse[list[TransitionDraftRead]], operation_id='list_transition_drafts')
async def list_transition_drafts(project_id: str, chapter_id: str, base_publication_id: str | None = None, factory=Depends(get_transition_factory)):
    """List actual variants in a chapter; optional base filtering never creates data."""
    async with factory() as db:
        await chapter_owned(db, project_id, chapter_id)
        query = select(TransitionDraft.id).where(TransitionDraft.project_id == project_id, TransitionDraft.chapter_id == chapter_id)
        if base_publication_id: query = query.where(TransitionDraft.base_publication_id == base_publication_id)
        identifiers = list((await db.execute(query.limit(101))).scalars())
        if len(identifiers) > 100:
            from fastapi import HTTPException
            raise HTTPException(409, '转场草稿超过100份，请按基础版本筛选')
        return success_response([await transitions.draft_view(db, project_id, identifier) for identifier in identifiers])


@router.post('/drafts', response_model=ApiResponse[TransitionDraftRead], status_code=201, operation_id='create_transition_draft')
async def create_transition_draft(project_id: str, body: TransitionCreate, factory=Depends(get_transition_factory)):
    """Create an independent variant tied to the selected actual v1 publication."""
    identifier = await transitions.create_draft(factory, project_id, body)
    async with factory() as db: return success_response(await transitions.draft_view(db, project_id, identifier))


@router.patch('/drafts/{draft_id}', response_model=ApiResponse[TransitionDraftRead], operation_id='patch_transition_draft')
async def patch_transition_draft(project_id: str, draft_id: str, body: TransitionPatch, factory=Depends(get_transition_factory)):
    """Save all boundary choices using the expected saved revision."""
    await transitions.patch_draft(factory, project_id, draft_id, body)
    async with factory() as db: return success_response(await transitions.draft_view(db, project_id, draft_id))


@router.post('/drafts/{draft_id}/freeze', response_model=ApiResponse[TransitionPublicationRead], operation_id='freeze_transition_draft')
async def freeze_transition_draft(project_id: str, draft_id: str, body: TransitionFreeze, factory=Depends(get_transition_factory)):
    """Lock verified original sources and explicit overlap into a new v2 publication."""
    identifier = await transitions.freeze_draft(factory, project_id, draft_id, body)
    async with factory() as db: return success_response(await transitions.publication_view(db, project_id, identifier))


@router.get('/publications/{publication_id}', response_model=ApiResponse[TransitionPublicationRead], operation_id='get_transition_publication')
async def get_transition_publication(project_id: str, publication_id: str, factory=Depends(get_transition_factory)):
    """Inspect immutable v2 history without replacing v1 or current adoptions."""
    async with factory() as db: return success_response(await transitions.publication_view(db, project_id, publication_id))


@router.get('/exports', response_model=ApiResponse[ExportCatalog], operation_id='list_transition_exports')
async def list_transition_exports(project_id: str, chapter_id: str, factory=Depends(get_transition_factory)):
    """Read only the independent v2 render queue and actual local tool availability."""
    return success_response(await exports.list_exports(factory, project_id, chapter_id, settings.production_probe_executable))


@router.post('/publications/{publication_id}/exports', response_model=ApiResponse[ExportRead], status_code=201, operation_id='create_transition_export')
async def create_transition_export(project_id: str, publication_id: str, body: ExportRequest, factory=Depends(get_transition_factory)):
    """Queue a real linear-crossfade video/audio render with a durable replay key."""
    return success_response(await exports.create_export(factory, project_id, publication_id, body.request_key, settings.production_probe_executable))


@router.get('/exports/{export_id}', response_model=ApiResponse[ExportRead], operation_id='get_transition_export')
async def get_transition_export(project_id: str, export_id: str, factory=Depends(get_transition_factory)):
    """Read a durable v2 outcome and verify every downloadable output."""
    return success_response(await exports.get_export(factory, project_id, export_id))


@router.post('/exports/{export_id}/cancel', response_model=ApiResponse[ExportRead], operation_id='cancel_transition_export')
async def cancel_transition_export(project_id: str, export_id: str, body: ExportCancel, factory=Depends(get_transition_factory)):
    """Cancel only this owned local v2 job; no model budget is touched."""
    return success_response(await exports.cancel_export(factory, project_id, export_id))


@router.get('/exports/{export_id}/content/{kind}', operation_id='read_transition_export_content')
async def read_transition_export_content(project_id: str, export_id: str, kind: Literal['video', 'subtitles_srt', 'subtitles_vtt', 'package'], factory=Depends(get_transition_factory)):
    """Stream fixed-name outputs only after source ownership and full byte hashes are checked."""
    target, name, mime = await exports.content_file(factory, project_id, export_id, kind)
    return FileResponse(target, filename=name, media_type=mime)
