"""Video DNA author checks using real original clips, immutable SQLite and inert HTTP JSON."""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import pytest
import httpx
from fastapi import FastAPI,HTTPException
from sqlalchemy import text,select,func
from app.config import settings
from app.core.contracts.video_dna import DnaCreate,DnaPatch,DnaFreeze,DnaImport
from app.models.video_dna import DnaDraft,DnaSnapshot
from app.models.production import ProductionAsset
from app.services.video_dna import service
from app.services.video_dna.schema import migrate_video_dna
from app.services.reverse.schema import migrate_reverse
from app.services.reverse.ingestion import create_analysis,run_analysis,edit_cuts
from app.services.studio.production_media import store_batch
from tests.test_durable_execution import setup

ROOT=Path(__file__).resolve().parents[5]
FFMPEG=ROOT/'work/ffmpeg.exe'


async def fixture(tmp_path,monkeypatch):
    """Analyze an actual offset CFR MP4, separate from independent VFR acceptance truth."""
    monkeypatch.setattr(settings,'production_media_root',str(tmp_path));monkeypatch.setattr(settings,'production_probe_executable',str(FFMPEG))
    engine,factory,*_=await setup(tmp_path)
    await migrate_reverse(engine);await migrate_video_dna(engine)
    source=tmp_path/'author-real-source.mp4'
    subprocess.run([str(FFMPEG),'-nostdin','-v','error','-f','lavfi','-i','color=yellow:s=64x48:r=12:d=1','-c:v','libx264','-threads','1','-bf','0','-pix_fmt','yuv420p','-output_ts_offset','2',str(source)],check=True,timeout=20)
    async with factory() as db:media=(await store_batch(db,'p',[('author-real-source.mp4',source.read_bytes())]))[0].id
    analysis=await create_analysis(factory,'p',media,'author-analysis')
    assert await run_analysis(factory,analysis,str(FFMPEG))
    await edit_cuts(factory,'p',analysis,[0,6],1)
    return engine,factory,analysis


def reviewed(view):
    """Keep unknown template reasons instead of inventing observed content for a software test."""
    value=deepcopy(view['document']);value.update(title='Author software hypothesis',reviewer='Manual author',human_reviewed=True);return value


@pytest.mark.asyncio
async def test_template_unknowns_freeze_and_source_fixed_under_concurrent_cas(tmp_path,monkeypatch):
    """Templates preserve all unknowns and only one full-document concurrent update wins."""
    engine,factory,analysis=await fixture(tmp_path,monkeypatch)
    try:
        create=DnaCreate(analysis_id=analysis,expected_analysis_revision=2,request_key='original-null-template')
        identifier=await service.create_draft(factory,'p',create)
        async with factory() as db:
            view=await service.draft_view(db,'p',identifier);data=reviewed(view)
            assert [(s['start_frame'],s['end_frame']) for s in data['shots']]==[(0,6),(6,12)]
            assert all(s['camera']['reason'] for s in data['shots']) and view['source']['terminal_time_known'] is False
        results=await asyncio.gather(*(service.patch_draft(factory,'p',identifier,DnaPatch(expected_revision=1,document=data)) for _ in range(2)),return_exceptions=True)
        assert sum(isinstance(r,HTTPException) and r.status_code==409 for r in results)==1
        snapshot=await service.freeze_draft(factory,'p',identifier,DnaFreeze(expected_revision=2))
        async with factory() as db:
            saved=await service.snapshot_view(db,'p',snapshot)
            assert all(s['video_prompt']['status']=='unknown' for s in saved['payload']['document']['shots'])
            assert saved['payload']['source']['frames'][0]['pts']>0
            assert (await service.preflight_snapshot(db,'p',snapshot))['usable']
        await edit_cuts(factory,'p',analysis,[0,4,8],2)
        assert await service.create_draft(factory,'p',create)==identifier
        assert await service.freeze_draft(factory,'p',identifier,DnaFreeze(expected_revision=2))==snapshot
        async with factory() as db:
            assert (await service.snapshot_view(db,'p',snapshot))==saved
            assert not (await service.preflight_snapshot(db,'p',snapshot))['source_current']
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_actual_http_portable_roundtrip_and_inert_qualification(tmp_path,monkeypatch):
    """Only original document/source are imported; a fake PASS never creates a model task."""
    from app.api.video_dna import router,get_video_dna_factory
    engine,factory,analysis=await fixture(tmp_path,monkeypatch)
    app=FastAPI();app.include_router(router);app.dependency_overrides[get_video_dna_factory]=lambda:factory
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local') as client:
            base='/video-dna/projects/p'
            response=await client.post(base+'/drafts',json={'analysis_id':analysis,'expected_analysis_revision':2,'request_key':'http-template'})
            assert response.status_code==201,response.text
            row=response.json()['data'];identifier=row['id'];data=reviewed(row)
            patched=await client.patch(base+'/drafts/'+identifier,json={'expected_revision':1,'document':data});assert patched.status_code==200,patched.text
            frozen=await client.post(base+'/drafts/'+identifier+'/freeze',json={'expected_revision':2});assert frozen.status_code==201,frozen.text
            original=frozen.json()['data'];download=await client.get(base+'/snapshots/'+original['id']+'/export');assert download.status_code==200
            envelope=download.json();envelope.update({'PASSED':True,'model_qualified':True,'task_id':'fake-task'})
            imported=await client.post(base+'/imports',json={'analysis_id':analysis,'expected_analysis_revision':2,'request_key':'inert-import','content':envelope});assert imported.status_code==201,imported.text
            new=imported.json()['data'];assert new['id']!=identifier and new['document']==data
            second=await client.post(base+'/drafts/'+new['id']+'/freeze',json={'expected_revision':1});assert second.status_code==201,second.text
            assert second.json()['data']['content_hash']==original['content_hash']
            assert 'model_qualified' not in second.json()['data']['payload']
            foreign=await client.get('/video-dna/projects/foreign/snapshots/'+original['id']);assert foreign.status_code==404
            bad=deepcopy(envelope);bad['payload']['source']['frames'][0]['pts']+=1
            rejected=await client.post(base+'/imports',json={'analysis_id':analysis,'expected_analysis_revision':2,'request_key':'tamper-clock','content':bad});assert rejected.status_code==409
    finally:await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('entrypoint',['metadata','video_dna'])
async def test_actual_frozen_m7a_database_additive_dna_upgrade(tmp_path,entrypoint):
    """Upgrade actual frozen M7A copies through generic and dedicated entrypoints, never originals."""
    import sqlite3
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.core.db import Base
    evidence=json.loads((ROOT/'outputs/m7a/recovery-result.json').read_text(encoding='utf8'))
    source=Path(evidence['restored_run'])/'current.db';before=hashlib.sha256(source.read_bytes()).hexdigest();copy=tmp_path/'m7a-copy.db'
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as original,sqlite3.connect(copy) as target:original.backup(target)
    engine=create_async_engine('sqlite+aiosqlite:///'+copy.as_posix())
    @event.listens_for(engine.sync_engine,'connect')
    def enforce_fk(connection,_):
        """Keep actual parent scope FKs enabled, rather than weakening migration for a test."""
        connection.execute('PRAGMA foreign_keys=ON')
    try:
        if entrypoint=='video_dna':await migrate_video_dna(engine)
        else:
            async with engine.begin() as connection:await connection.run_sync(Base.metadata.create_all)
        async with engine.connect() as connection:
            assert not (await connection.execute(text('PRAGMA foreign_key_check'))).first()
            assert (await connection.execute(text("SELECT 1 FROM sqlite_master WHERE name='video_dna_snapshots'"))).first()
        assert hashlib.sha256(source.read_bytes()).hexdigest()==before
    finally:await engine.dispose()


def test_complete_portable_envelope_exact_two_mib_boundary():
    """Budget id/hash/payload together at the real 2MiB limit, including non-ASCII UTF8 bytes."""
    from app.services.script_preparation.service import canonical,content_hash
    identifier='00000000-0000-0000-0000-000000000000'
    payload={'padding':''}
    overhead=len(canonical({'id':identifier,'content_hash':content_hash(payload),'payload':payload}).encode('utf8'))
    payload['padding']='界'+('x'*(2*1024*1024-overhead-3))
    envelope=service.portable_snapshot(identifier,payload)
    assert len(canonical(envelope).encode('utf8'))==2*1024*1024
    assert len(canonical(payload).encode('utf8'))<2*1024*1024
    payload['padding']+='x'
    assert len(canonical(payload).encode('utf8'))<2*1024*1024
    with pytest.raises(HTTPException) as exc:service.portable_snapshot(identifier,payload)
    assert exc.value.status_code==409


@pytest.mark.asyncio
async def test_freeze_checks_full_envelope_outside_and_inside_writer(tmp_path,monkeypatch):
    """A smaller test-only budget exposes envelope overhead; actual final UUID is checked twice."""
    from app.services.script_preparation.service import canonical,content_hash
    engine,factory,analysis=await fixture(tmp_path,monkeypatch)
    try:
        identifier=await service.create_draft(factory,'p',DnaCreate(analysis_id=analysis,expected_analysis_revision=2,request_key='budget-template'))
        async with factory() as db:data=reviewed(await service.draft_view(db,'p',identifier))
        await service.patch_draft(factory,'p',identifier,DnaPatch(expected_revision=1,document=data))
        async with factory() as db:
            draft=await service.owned_draft(db,'p',identifier)
            payload=service.DnaSnapshotPayload(source=draft.source,document=draft.document,asset_versions=[]).model_dump()
        envelope_size=len(canonical({'id':'0'*36,'content_hash':content_hash(payload),'payload':payload}).encode('utf8'))
        assert len(canonical(payload).encode('utf8'))<envelope_size-1
        # Tighten only this isolated test; production remains at the real 2MiB bound.
        monkeypatch.setattr(service,'MAX_PORTABLE_BYTES',envelope_size-1)
        with pytest.raises(HTTPException):await service.freeze_draft(factory,'p',identifier,DnaFreeze(expected_revision=2))
        async with factory() as db:assert await db.scalar(select(func.count()).select_from(DnaSnapshot))==0
        monkeypatch.setattr(service,'MAX_PORTABLE_BYTES',envelope_size)
        original=service.portable_snapshot;calls=[]
        def checked(actual_id,actual_payload,digest=None):
            """Record the same UUID and exact complete envelope on both sides of the writer boundary."""
            result=original(actual_id,actual_payload,digest);calls.append((actual_id,len(canonical(result).encode('utf8'))));return result
        monkeypatch.setattr(service,'portable_snapshot',checked)
        snapshot=await service.freeze_draft(factory,'p',identifier,DnaFreeze(expected_revision=2))
        assert calls==[(snapshot,envelope_size),(snapshot,envelope_size)]
        async with factory() as db:assert len(canonical(await service.snapshot_view(db,'p',snapshot)).encode('utf8'))==envelope_size
    finally:await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('cuts',[[False,6],[0,True],[0,0],[6,0]])
async def test_import_cut_coordinates_do_not_accept_boolean_equivalence_or_bad_order(tmp_path,monkeypatch,cuts):
    """Python False==0 must not make imported noninteger cuts match original frame zero."""
    from app.services.video_dna.source import source_from_analysis,owned_analysis
    engine,factory,analysis=await fixture(tmp_path,monkeypatch)
    try:
        async with factory() as db:source=source_from_analysis(await owned_analysis(db,'p',analysis))
        source['confirmed_cut_indices']=cuts
        document=service.template({**source,'confirmed_cut_indices':[0,6]})
        content={'schema_version':1,'source':source,'document':document}
        with pytest.raises(HTTPException) as exc:await service.import_draft(factory,'p',DnaImport(analysis_id=analysis,expected_analysis_revision=2,request_key='bad-cut-type-order',content=content))
        assert exc.value.status_code==422
        async with factory() as db:assert await db.scalar(select(func.count()).select_from(DnaDraft))==0
    finally:await engine.dispose()
