"""SQLite benchmark state tests use explicit synthetic clocks, not formal C evidence."""
import asyncio
import hashlib
import pytest
from fastapi import HTTPException
from sqlalchemy import text,update
from sqlalchemy.exc import IntegrityError
from app.config import settings
from app.core.contracts.benchmark import BenchmarkDocument,DraftCreate,DraftPatch
from app.models.reverse import ReverseAnalysis
from app.services.benchmark.schema import migrate_benchmark
from app.services.benchmark.service import canonical,create_draft,draft_view,freeze_draft,patch_draft,snapshot_view
from app.services.reverse.ingestion import create_analysis
from tests.test_reverse_ingestion import setup


def complete_document():
    """Describe a synthetic software-only fixture with every declaration field present."""
    return BenchmarkDocument(title='TEST ONLY not licensed C',source_note='Synthetic state test',license_basis='owned',license_reference='fixture declaration only',license_confirmed=True,independent_from_target=True,continuous_take=True,reviewer='fixture reviewer',human_reviewed=True,roles=[{'id':r,'name':r} for r in ['r1','r2']],dialogues=[{'id':'d'+r,'role_id':r,'text':'测试','start_frame':0,'end_frame':10} for r in ['r1','r2']],mouth_anchors=[{'role_id':r,'frame_index':i,'state':'speaking' if i%2 else 'closed'} for r in ['r1','r2'] for i in range(10)],events=[{'id':k,'kind':k,'description':'fixture','start_frame':0,'end_frame':10} for k in ['interaction','action','camera_change']],cut_frame_indices=[0,10],rubric_notes={k:'fixture checkpoints' for k in ['character','scene','composition','lighting','action','continuity','lipsync','editing']})


async def benchmark_fixture(tmp_path):
    """Install actual constraints and a synthetic 30-second timestamp evidence row."""
    engine,factory=await setup(tmp_path)
    await migrate_benchmark(engine);await migrate_benchmark(engine)
    analysis_id=await create_analysis(factory,'a','a-video','fixture')
    directory=tmp_path/'_reverse'/analysis_id/'fixture';directory.mkdir(parents=True)
    (directory/'audio.wav').write_bytes(b'fixture-state-audio-not-decoded'*4)
    frames=[{'index':i,'pts':i,'time_base_num':1,'time_base_den':30,'relative_us':round(i*1000000/30)} for i in range(900)]
    async with factory() as db:
        await db.execute(update(ReverseAnalysis).where(ReverseAnalysis.id==analysis_id).values(state='ready',artifact_dir=f'_reverse/{analysis_id}/fixture',manifest={'test_fixture_only':True,'video':{'frames':frames},'audio':{'present':True,'wav_path':'audio.wav'}}))
        await db.commit()
    return engine,factory,analysis_id


@pytest.mark.asyncio
async def test_snapshot_freeze_cas_import_and_immutability(tmp_path,monkeypatch):
    """Freeze one software fixture revision twice, then verify hash, CAS and SQL guards."""
    monkeypatch.setattr(settings,'production_media_root',str(tmp_path))
    engine,factory,analysis=await benchmark_fixture(tmp_path)
    try:
        body=DraftCreate(analysis_id=analysis,document=complete_document(),request_key='one')
        draft=await create_draft(factory,'a',body)
        assert await create_draft(factory,'a',body)==draft
        async with factory() as db:assert (await draft_view(db,'a',draft))['readiness']['can_freeze']
        snapshot=await freeze_draft(factory,'a',draft,1)
        assert await freeze_draft(factory,'a',draft,1)==snapshot
        async with factory() as db:
            exported=await snapshot_view(db,'a',snapshot)
            assert exported['content_hash']==hashlib.sha256(canonical(exported['payload']).encode()).hexdigest()
            assert exported['payload']['semantic_verification']=='human_declaration_only'
        for statement in ['UPDATE benchmark_snapshots SET payload=\'{}\' WHERE id=:id','DELETE FROM benchmark_snapshots WHERE id=:id']:
            async with factory() as db:
                with pytest.raises(IntegrityError):await db.execute(text(statement),{'id':snapshot})
                await db.rollback()
        await patch_draft(factory,'a',draft,DraftPatch(expected_revision=1,document=complete_document()))
        with pytest.raises(HTTPException):await patch_draft(factory,'a',draft,DraftPatch(expected_revision=1,document=complete_document()))
        imported=await create_draft(factory,'a',DraftCreate(analysis_id=analysis,document=exported['payload']['document'],request_key='import'))
        async with factory() as db:assert (await draft_view(db,'a',imported))['snapshots']==[]
        async with factory() as db:
            with pytest.raises(HTTPException):await snapshot_view(db,'b',snapshot)
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_incomplete_draft_issues_and_invalid_references(tmp_path,monkeypatch):
    """Incomplete content stays editable but malformed ownership or references fail."""
    monkeypatch.setattr(settings,'production_media_root',str(tmp_path))
    engine,factory,analysis=await benchmark_fixture(tmp_path)
    try:
        draft=await create_draft(factory,'a',DraftCreate(analysis_id=analysis,document=BenchmarkDocument(),request_key='empty'))
        async with factory() as db:
            view=await draft_view(db,'a',draft)
            assert not view['readiness']['can_freeze'] and view['readiness']['issues']
        with pytest.raises(HTTPException):await freeze_draft(factory,'a',draft,1)
        invalid=[{'roles':[{'id':'r'},{'id':'r'}]}, {'roles':[{'id':'r'}],'mouth_anchors':[{'role_id':'r','frame_index':1,'state':s} for s in ['speaking','closed']]}, {'dialogues':[{'id':'d','role_id':'absent','start_frame':0,'end_frame':1}]}, {'cut_frame_indices':[1]}, {'cut_frame_indices':[0,900]}, {'events':[{'id':'e','kind':'action','start_frame':10,'end_frame':2}]}]
        for i,document in enumerate(invalid):
            with pytest.raises(HTTPException) as error:await create_draft(factory,'a',DraftCreate(analysis_id=analysis,document=BenchmarkDocument(**document),request_key=str(i)))
            assert error.value.status_code==422
        with pytest.raises(HTTPException):await create_draft(factory,'b',DraftCreate(analysis_id=analysis,document=BenchmarkDocument(),request_key='foreign'))
    finally:await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['dialogue','event'])
async def test_every_declared_required_item_has_content_before_freeze(tmp_path,monkeypatch,kind):
    """An extra blank required item blocks freezing even when each category is covered."""
    monkeypatch.setattr(settings,'production_media_root',str(tmp_path))
    engine,factory,analysis=await benchmark_fixture(tmp_path)
    try:
        document=complete_document().model_dump()
        if kind=='dialogue':
            document['dialogues'].append({'id':'extra_blank','role_id':'r1','text':'  ','start_frame':20,'end_frame':25})
            expected='dialogue_text_extra_blank'
        else:
            document['events'].append({'id':'extra_blank','kind':'action','description':'  ','start_frame':20,'end_frame':25})
            expected='event_description_extra_blank'
        draft=await create_draft(factory,'a',DraftCreate(analysis_id=analysis,document=BenchmarkDocument(**document),request_key=kind))
        async with factory() as db:
            view=await draft_view(db,'a',draft)
            assert not view['readiness']['can_freeze']
            assert expected in {issue['code'] for issue in view['readiness']['issues']}
        with pytest.raises(HTTPException) as error:await freeze_draft(factory,'a',draft,1)
        assert error.value.status_code==409
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_snapshot_hash_survives_javascript_integral_number_roundtrip(tmp_path,monkeypatch):
    """Browser JSON serialization cannot change the hash of a software snapshot."""
    import json
    def browser_numbers(value):
        """Model JS JSON.stringify's removal of integral float decimal suffixes."""
        if isinstance(value,dict):return {key:browser_numbers(item) for key,item in value.items()}
        if isinstance(value,list):return [browser_numbers(item) for item in value]
        if type(value) is float and value.is_integer():return int(value)
        return value
    monkeypatch.setattr(settings,'production_media_root',str(tmp_path))
    engine,factory,analysis=await benchmark_fixture(tmp_path)
    try:
        draft=await create_draft(factory,'a',DraftCreate(analysis_id=analysis,document=complete_document(),request_key='browser-hash'))
        identifier=await freeze_draft(factory,'a',draft,1)
        async with factory() as db:snapshot=await snapshot_view(db,'a',identifier)
        reimported=json.loads(json.dumps(browser_numbers(snapshot['payload']),ensure_ascii=False))
        assert hashlib.sha256(canonical(reimported).encode()).hexdigest()==snapshot['content_hash']
        for key in ['confirmed_dialogue_coverage','event_coverage_min','event_order_accuracy_min','export_shot_coverage_min','dna_roundtrip_fidelity']:
            assert type(snapshot['payload']['rules'][key]) is int
    finally:await engine.dispose()
