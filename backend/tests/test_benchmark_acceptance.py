"""Independent SQLite acceptance; synthetic declarations never qualify a real C sample."""
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import pytest
from fastapi import HTTPException
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.config import settings
from app.core.contracts.benchmark import BenchmarkDocument, DraftCreate, DraftPatch
from app.models.benchmark import BenchmarkSnapshot
from app.models.production import ProductionMedia
from app.models.reverse import ReverseAnalysis
from app.services.benchmark.schema import migrate_benchmark
from app.services.benchmark.service import create_draft, draft_view, patch_draft, freeze_draft, snapshot_view, canonical
from app.services.reverse.ingestion import create_analysis, run_analysis
from app.services.studio.production_media import store_batch
from tests.test_durable_execution import setup

ROOT = Path(__file__).resolve().parents[5]


def complete_document():
    """Declare synthetic test annotations to exercise storage, not license or semantics."""
    return BenchmarkDocument(title='SOFTWARE FIXTURE ONLY NOT C', source_note='Synthetic testsrc and sine, no speech or people',
        license_basis='owned',license_reference='Synthetic software test declaration only',license_confirmed=True,
        independent_from_target=True,continuous_take=True,reviewer='Test harness, not formal C reviewer',human_reviewed=True,
        roles=[{'id':r,'name':'synthetic '+r} for r in ['a','b']],
        dialogues=[{'id':r,'role_id':r,'text':'DECLARED ONLY synthetic tone','start_frame':0,'end_frame':1} for r in ['a','b']],
        mouth_anchors=[{'role_id':r,'frame_index':i,'state':'speaking' if i%2 else 'closed'} for r in ['a','b'] for i in range(10)],
        events=[{'id':kind,'kind':kind,'description':'DECLARED ONLY','start_frame':0,'end_frame':1} for kind in ['interaction','action','camera_change']],
        cut_frame_indices=[0,10],rubric_notes={k:'Synthetic declaration; no actual quality scoring' for k in ['character','scene','composition','lighting','action','continuity','lipsync','editing']})


async def view(factory, project, identity):
    """Read committed readiness through a fresh real session."""
    async with factory() as db:
        return await draft_view(db,project,identity)


async def denied(awaitable, status):
    """Require exact application failure status instead of accepting any exception."""
    with pytest.raises(HTTPException) as error:
        await awaitable
    assert error.value.status_code==status


@pytest.mark.asyncio
async def test_actual_ten_second_b_is_not_freezable_and_source_is_rechecked(tmp_path,monkeypatch):
    """Use a read-only backup of actual approved B evidence, never modify its original."""
    evidence=json.loads((ROOT/'outputs/r1/recovery-result.json').read_text(encoding='utf8'))
    original=Path(evidence['run_dir'])
    before=hashlib.sha256((original/'restored.db').read_bytes()).hexdigest()
    with sqlite3.connect((original/'restored.db').as_uri()+'?mode=ro',uri=True) as source, sqlite3.connect(tmp_path/'copy.db') as target:
        source.backup(target)
    shutil.copytree(original/'media',tmp_path/'media')
    monkeypatch.setattr(settings,'production_media_root',str(tmp_path/'media'))
    engine=create_async_engine('sqlite+aiosqlite:///'+(tmp_path/'copy.db').as_posix())
    @event.listens_for(engine.sync_engine,'connect')
    def foreign_keys(connection,_):
        """Enable actual database project-reference constraints on test copies."""
        connection.execute('PRAGMA foreign_keys=ON')
    factory=async_sessionmaker(engine,expire_on_commit=False)
    try:
        await migrate_benchmark(engine)
        body=DraftCreate(analysis_id=evidence['analysis_id'],request_key='real-B-negative',document=complete_document())
        identity=await create_draft(factory,'r1-ui',body)
        result=await view(factory,'r1-ui',identity)
        assert not result['readiness']['can_freeze']
        assert 'duration_30_60' in {x['code'] for x in result['readiness']['issues']}
        await denied(freeze_draft(factory,'r1-ui',identity,1),409)
        await denied(create_draft(factory,'foreign',body),404)
        await denied(view(factory,'foreign',identity),404)
        await denied(freeze_draft(factory,'foreign',identity,1),404)
        async with factory() as db:
            analysis=await db.get(ReverseAnalysis,evidence['analysis_id'])
            media=await db.get(ProductionMedia,analysis.media_id)
            path=tmp_path/'media'/media.relative_path
        path.write_bytes(b'corruption ONLY in disposable copy')
        await denied(view(factory,'r1-ui',identity),409)
        await denied(freeze_draft(factory,'r1-ui',identity,1),409)
        await denied(patch_draft(factory,'r1-ui',identity,DraftPatch(expected_revision=1,document=complete_document())),409)
        assert hashlib.sha256((original/'restored.db').read_bytes()).hexdigest()==before
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_real_synthetic_freeze_hash_immutability_and_reimport(tmp_path,monkeypatch):
    """Actual decoded 30s tone clip tests software snapshots, not formal C semantics."""
    ffmpeg=ROOT/'work/ffmpeg.exe'
    source=tmp_path/'synthetic30.mp4'
    subprocess.run([str(ffmpeg),'-nostdin','-v','error','-f','lavfi','-i','testsrc2=s=64x48:r=2:d=30',
        '-f','lavfi','-i','sine=frequency=440:duration=30','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(source)],check=True,timeout=60)
    monkeypatch.setattr(settings,'production_media_root',str(tmp_path/'media'))
    monkeypatch.setattr(settings,'production_probe_executable',str(ffmpeg))
    engine,factory,_,_,_,_=await setup(tmp_path)
    try:
        await migrate_benchmark(engine)
        async with factory() as db:
            media=(await store_batch(db,'p',[(source.name,source.read_bytes())]))[0]
        analysis=await create_analysis(factory,'p',media.id,'actual-synthetic-decode')
        assert await run_analysis(factory,analysis,str(ffmpeg))
        body=DraftCreate(analysis_id=analysis,request_key='positive-software',document=complete_document())
        identity=await create_draft(factory,'p',body)
        assert await create_draft(factory,'p',body)==identity
        assert (await view(factory,'p',identity))['readiness']['semantic_verification']=='human_declaration_only'
        snap=await freeze_draft(factory,'p',identity,1)
        assert await freeze_draft(factory,'p',identity,1)==snap
        async with factory() as db:
            exported=await snapshot_view(db,'p',snap)
        assert hashlib.sha256(canonical(exported['payload']).encode('utf8')).hexdigest()==exported['content_hash']
        assert exported['payload']['semantic_verification']=='human_declaration_only'
        async with factory() as db:
            assert len((await db.execute(select(BenchmarkSnapshot))).scalars().all())==1
        for statement in ['UPDATE benchmark_snapshots SET content_hash=\'tampered\' WHERE id=:id','DELETE FROM benchmark_snapshots WHERE id=:id']:
            async with factory() as db:
                with pytest.raises(IntegrityError):
                    await db.execute(text(statement),{'id':snap})
                await db.rollback()
        await patch_draft(factory,'p',identity,DraftPatch(expected_revision=1,document=complete_document()))
        await denied(patch_draft(factory,'p',identity,DraftPatch(expected_revision=1,document=complete_document())),409)
        await denied(freeze_draft(factory,'p',identity,1),409)
        new=await freeze_draft(factory,'p',identity,2)
        assert new!=snap
        # Actual silent 30-second media is independently blocked, not just short B.
        silent=tmp_path/'silent30.mp4'
        subprocess.run([str(ffmpeg),'-nostdin','-v','error','-i',str(source),'-an','-c:v','copy',str(silent)],check=True,timeout=30)
        async with factory() as db:
            silent_media=(await store_batch(db,'p',[(silent.name,silent.read_bytes())]))[0]
        silent_analysis=await create_analysis(factory,'p',silent_media.id,'silent-actual-decode')
        assert await run_analysis(factory,silent_analysis,str(ffmpeg))
        silent_draft=await create_draft(factory,'p',DraftCreate(analysis_id=silent_analysis,request_key='silent-negative',document=complete_document()))
        assert 'audio_available' in {x['code'] for x in (await view(factory,'p',silent_draft))['readiness']['issues']}
        await denied(freeze_draft(factory,'p',silent_draft,1),409)
        # Reimport document only: a fresh draft must re-evaluate its current audio.
        imported=DraftCreate(analysis_id=analysis,request_key='reimport-inert-document',document=BenchmarkDocument.model_validate(exported['payload']['document']))
        async with factory() as db:
            row=await db.get(ReverseAnalysis,analysis)
            wav=tmp_path/'media'/row.artifact_dir/row.manifest['audio']['wav_path']
        wav.unlink()
        draft2=await create_draft(factory,'p',imported)
        readiness=(await view(factory,'p',draft2))['readiness']
        assert not readiness['can_freeze'] and 'audio_available' in {x['code'] for x in readiness['issues']}
        await denied(freeze_draft(factory,'p',draft2,1),409)
        # Same revision is a historical idempotent lookup, not renewed certification.
        assert await freeze_draft(factory,'p',identity,2)==new
        async with factory() as db:
            assert len((await db.execute(select(BenchmarkSnapshot))).scalars().all())==2
        await patch_draft(factory,'p',identity,DraftPatch(expected_revision=2,document=complete_document()))
        await denied(freeze_draft(factory,'p',identity,3),409)
    finally:
        await engine.dispose()
