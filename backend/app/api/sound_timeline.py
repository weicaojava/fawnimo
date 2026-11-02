"""Owned sound-v3 forms, candidates and exports, separate from unchanged picture versions."""
from typing import Literal
from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy import select
from app.config import settings
from app.core.db import async_session_maker
from app.core.contracts.sound_timeline import SoundCreate, SoundPatch, SoundFreeze, SoundDraftRead, SoundPublicationRead
from app.core.contracts.editing_export import ExportRequest, ExportCancel
from app.core.contracts.sound_timeline import SoundCandidateImport, SoundCandidateRead, SoundExportRead as ExportRead, SoundExportCatalog as ExportCatalog
from app.models.sound_timeline import SoundDraft
from app.schemas.common import ApiResponse, success_response
from app.services.editing import sound_timeline as sounds, sound_exports as exports
from app.services.editing.common import chapter_owned
from app.core.contracts.local_speech import LocalSpeechRequest, LocalSpeechRead, LocalSpeechHistory
from app.services.editing import local_speech
from app.services.editing.local_speech_engine import LocalSpeechEngine

router = APIRouter(prefix='/sound-v3/projects/{project_id}', tags=['sound-v3'])


def get_sound_factory():
    """Keep sound forms, publishers and local workers on one application-owned database."""
    return async_session_maker


def get_local_speech_engine():
    """Only the application selects the trusted offline engine; callers select an installed voice."""
    return LocalSpeechEngine()


@router.get('/drafts', response_model=ApiResponse[list[SoundDraftRead]], operation_id='list_sound_drafts')
async def list_sound_drafts(project_id: str, chapter_id: str, factory=Depends(get_sound_factory)):
    """List actual variants in a chapter; this read never creates data."""
    async with factory() as db:
        await chapter_owned(db, project_id, chapter_id)
        query = select(SoundDraft.id).where(SoundDraft.project_id == project_id, SoundDraft.chapter_id == chapter_id)
        identifiers = list((await db.execute(query.limit(101))).scalars())
        if len(identifiers) > 100:
            from fastapi import HTTPException
            raise HTTPException(409, '声音草稿超过本版上限')
        return success_response([await sounds.draft_view(db, project_id, identifier) for identifier in identifiers])


@router.post('/drafts', response_model=ApiResponse[SoundDraftRead], status_code=201, operation_id='create_sound_draft')
async def create_sound_draft(project_id: str, body: SoundCreate, factory=Depends(get_sound_factory)):
    """Create an independent sound form with an explicitly copied whole picture document."""
    identifier = await sounds.create_draft(factory, project_id, body)
    async with factory() as db: return success_response(await sounds.draft_view(db, project_id, identifier))


@router.patch('/drafts/{draft_id}', response_model=ApiResponse[SoundDraftRead], operation_id='patch_sound_draft')
async def patch_sound_draft(project_id: str, draft_id: str, body: SoundPatch, factory=Depends(get_sound_factory)):
    """Save the complete picture and sound form using the expected saved revision."""
    await sounds.patch_draft(factory, project_id, draft_id, body)
    async with factory() as db: return success_response(await sounds.draft_view(db, project_id, draft_id))


@router.post('/drafts/{draft_id}/freeze', response_model=ApiResponse[SoundPublicationRead], operation_id='freeze_sound_draft')
async def freeze_sound_draft(project_id: str, draft_id: str, body: SoundFreeze, factory=Depends(get_sound_factory)):
    """Lock verified original sources and explicit overlap into a new sound publication."""
    identifier = await sounds.freeze_draft(factory, project_id, draft_id, body)
    async with factory() as db: return success_response(await sounds.publication_view(db, project_id, identifier))


@router.get('/publications/{publication_id}', response_model=ApiResponse[SoundPublicationRead], operation_id='get_sound_publication')
async def get_sound_publication(project_id: str, publication_id: str, factory=Depends(get_sound_factory)):
    """Inspect immutable sound history without replacing v1 or current adoptions."""
    async with factory() as db: return success_response(await sounds.publication_view(db, project_id, publication_id))


@router.get('/exports', response_model=ApiResponse[ExportCatalog], operation_id='list_sound_exports')
async def list_sound_exports(project_id: str, chapter_id: str, factory=Depends(get_sound_factory)):
    """Read only the independent sound render queue and actual local tool availability."""
    return success_response(await exports.list_exports(factory, project_id, chapter_id, settings.production_probe_executable))


@router.post('/publications/{publication_id}/exports', response_model=ApiResponse[ExportRead], status_code=201, operation_id='create_sound_export')
async def create_sound_export(project_id: str, publication_id: str, body: ExportRequest, factory=Depends(get_sound_factory)):
    """Queue a real picture and independently placed audio render with a durable replay key."""
    return success_response(await exports.create_export(factory, project_id, publication_id, body.request_key, settings.production_probe_executable))


@router.get('/exports/{export_id}', response_model=ApiResponse[ExportRead], operation_id='get_sound_export')
async def get_sound_export(project_id: str, export_id: str, factory=Depends(get_sound_factory)):
    """Read a durable sound outcome and verify every downloadable output."""
    return success_response(await exports.get_export(factory, project_id, export_id))


@router.post('/exports/{export_id}/cancel', response_model=ApiResponse[ExportRead], operation_id='cancel_sound_export')
async def cancel_sound_export(project_id: str, export_id: str, body: ExportCancel, factory=Depends(get_sound_factory)):
    """Cancel only this owned local sound job; no model budget is touched."""
    return success_response(await exports.cancel_export(factory, project_id, export_id))


@router.get('/exports/{export_id}/content/{kind}', operation_id='read_sound_export_content')
async def read_sound_export_content(project_id: str, export_id: str, kind: Literal['video', 'mixdown', 'subtitles_srt', 'subtitles_vtt', 'package'], factory=Depends(get_sound_factory)):
    """Stream fixed-name outputs only after source ownership and full byte hashes are checked."""
    target, name, mime = await exports.content_file(factory, project_id, export_id, kind)
    return FileResponse(target, filename=name, media_type=mime)


@router.get('/candidates', response_model=ApiResponse[list[SoundCandidateRead]], operation_id='list_sound_candidates')
async def list_sound_candidates(project_id: str, chapter_id: str, factory=Depends(get_sound_factory)):
    """List actual candidates; this read never creates or adopts audio."""
    async with factory() as db: return success_response(await sounds.list_candidates(db, project_id, chapter_id))


@router.post('/candidates/import', response_model=ApiResponse[SoundCandidateRead], status_code=201, operation_id='import_sound_candidate')
async def import_sound_candidate(project_id: str, body: SoundCandidateImport, factory=Depends(get_sound_factory)):
    """Register a fully decoded actual WAV; explicit track adoption remains a later action."""
    return success_response(await sounds.import_candidate(factory, project_id, body))


@router.get('/speech', response_model=ApiResponse[LocalSpeechHistory], operation_id='list_local_speech')
async def list_local_speech(project_id: str, chapter_id: str, factory=Depends(get_sound_factory), engine=Depends(get_local_speech_engine)):
    """Inspect actual local voices and historical jobs without synthesis or paid traffic."""
    return success_response(await local_speech.history(factory, project_id, chapter_id, engine))


@router.post('/speech', response_model=ApiResponse[LocalSpeechRead], status_code=201, operation_id='create_local_speech')
async def create_local_speech(project_id: str, body: LocalSpeechRequest, factory=Depends(get_sound_factory), engine=Depends(get_local_speech_engine)):
    """Queue exact original dialogue at zero external fee; result adoption is a separate user action."""
    return success_response(await local_speech.create_job(factory, project_id, body, engine))


@router.get('/speech/{job_id}', response_model=ApiResponse[LocalSpeechRead], operation_id='get_local_speech')
async def get_local_speech(project_id: str, job_id: str, factory=Depends(get_sound_factory)):
    """Recover the original durable job even if the current voice or engine is unavailable."""
    return success_response(await local_speech.get_job(factory, project_id, job_id))


@router.post('/speech/{job_id}/cancel', response_model=ApiResponse[LocalSpeechRead], operation_id='cancel_local_speech')
async def cancel_local_speech(project_id: str, job_id: str, body: ExportCancel, factory=Depends(get_sound_factory)):
    """Request cancellation from the owner of the local synthesis process."""
    return success_response(await local_speech.cancel_job(factory, project_id, job_id))
