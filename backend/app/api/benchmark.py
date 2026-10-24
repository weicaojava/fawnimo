"""Account-free benchmark software preparation; no Q0 certification is returned."""
from fastapi import APIRouter,Depends
from sqlalchemy import select
from app.core.db import async_session_maker
from app.core.contracts.benchmark import BenchmarkRead,BenchmarkSnapshot,DraftCreate,DraftPatch,FreezeWrite
from app.models.benchmark import BenchmarkDraft
from app.schemas.common import ApiResponse,success_response
from app.services.benchmark.service import create_draft,draft_view,patch_draft,freeze_draft,snapshot_view
from app.services.studio.production import project_exists

router=APIRouter(prefix='/benchmarks/projects/{project_id}',tags=['benchmarks'])


def get_benchmark_factory():
    """Use the trusted application database without accepting client paths."""
    return async_session_maker


@router.get('/drafts',response_model=ApiResponse[list[BenchmarkRead]])
async def list_benchmark_drafts(project_id:str,factory=Depends(get_benchmark_factory)):
    """List bounded owned drafts after checking their original sources."""
    async with factory() as db:
        await project_exists(db,project_id)
        ids=(await db.execute(select(BenchmarkDraft.id).where(BenchmarkDraft.project_id==project_id).order_by(BenchmarkDraft.id).limit(100))).scalars().all()
        return success_response([await draft_view(db,project_id,identifier) for identifier in ids])


@router.post('/drafts',response_model=ApiResponse[BenchmarkRead],status_code=201)
async def create_benchmark_draft(project_id:str,body:DraftCreate,factory=Depends(get_benchmark_factory)):
    """Create an incomplete draft without implying any external qualification."""
    identifier=await create_draft(factory,project_id,body)
    async with factory() as db:return success_response(await draft_view(db,project_id,identifier))


@router.get('/drafts/{draft_id}',response_model=ApiResponse[BenchmarkRead])
async def get_benchmark_draft(project_id:str,draft_id:str,factory=Depends(get_benchmark_factory)):
    """Return one owned draft and its software checklist issues."""
    async with factory() as db:return success_response(await draft_view(db,project_id,draft_id))


@router.patch('/drafts/{draft_id}',response_model=ApiResponse[BenchmarkRead])
async def patch_benchmark_draft(project_id:str,draft_id:str,body:DraftPatch,factory=Depends(get_benchmark_factory)):
    """CAS a complete replacement document at its expected draft revision."""
    await patch_draft(factory,project_id,draft_id,body)
    async with factory() as db:return success_response(await draft_view(db,project_id,draft_id))


@router.post('/drafts/{draft_id}/freeze',response_model=ApiResponse[BenchmarkSnapshot])
async def freeze_benchmark_draft(project_id:str,draft_id:str,body:FreezeWrite,factory=Depends(get_benchmark_factory)):
    """Freeze an immutable human-declared preparation snapshot, not Q0 PASSED."""
    identifier=await freeze_draft(factory,project_id,draft_id,body.expected_revision)
    async with factory() as db:return success_response(await snapshot_view(db,project_id,identifier))


@router.get('/snapshots/{snapshot_id}',response_model=ApiResponse[BenchmarkSnapshot])
async def get_benchmark_snapshot(project_id:str,snapshot_id:str,factory=Depends(get_benchmark_factory)):
    """Export immutable JSON without runtime paths, credentials or execution hooks."""
    async with factory() as db:return success_response(await snapshot_view(db,project_id,snapshot_id))


@router.post('/import-draft',response_model=ApiResponse[BenchmarkRead],status_code=201)
async def import_benchmark_draft(project_id:str,body:DraftCreate,factory=Depends(get_benchmark_factory)):
    """Revalidate document against a local analysis; never import snapshot qualification."""
    identifier=await create_draft(factory,project_id,body)
    async with factory() as db:return success_response(await draft_view(db,project_id,identifier))
