"""Independent R2A software acceptance: actual media, rational clocks, no model or semantic truth."""
import asyncio
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess
import av
import pytest
from PIL import Image
from fastapi import HTTPException
from sqlalchemy import select,func,text
from sqlalchemy.exc import IntegrityError
from app.config import settings
from app.models.production import ProductionAsset,ProductionMedia,AssetVersion,VersionMedia
from app.models.reverse import ReverseAnalysis
from app.models.studio import FileItem,Project
from app.services.reverse.ingestion import create_analysis,run_analysis,edit_cuts
from app.services.reverse.schema import migrate_reverse
from app.services.studio.production import add_version
from app.schemas.studio.production import VersionWrite

ROOT=Path(__file__).resolve().parents[5]
FFMPEG=ROOT/'work/ffmpeg.exe'
SAFE=9007199254740991


def canonical(value):
    """Independent Python encoding for the fixed ASCII schema, without calling product hash helpers."""
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def checksum(path):
    """Hash exact owned fixture bytes before and after analysis without modifying originals."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def decoded_clock(path):
    """Independently decode exact frame clocks; never infer terminal duration from neighbours."""
    frames=[]
    with av.open(str(path)) as container:
        for frame in container.decode(video=0):
            instant=Fraction(frame.pts)*frame.time_base
            if not frames:origin=instant
            frames.append({'index':len(frames),'pts':frame.pts,'time_base_num':frame.time_base.numerator,
                'time_base_den':frame.time_base.denominator,'relative_us':round((instant-origin)*1000000)})
    return frames


async def fixture(tmp_path,monkeypatch,*,audio=False,vfr=True):
    """Create actual file SQLite, genuine R1 probe evidence and an owned M2 PNG version."""
    from tests.test_durable_execution import setup
    from app.services.studio import production_schema
    monkeypatch.setattr(settings,'production_media_root',str(tmp_path))
    monkeypatch.setattr(settings,'production_probe_executable',str(FFMPEG))
    engine,factory,*_=await setup(tmp_path)
    monkeypatch.setattr(production_schema,'engine',engine)
    await production_schema.migrate_production()
    await migrate_reverse(engine)
    source=tmp_path/'independent-source.mp4'
    args=[str(FFMPEG),'-nostdin','-v','error','-f','lavfi','-i','testsrc2=s=64x48:r=24:d=1']
    if audio:args+=['-f','lavfi','-i','sine=frequency=440:duration=1']
    if vfr:args+=['-vf',"select='not(mod(n,3))+eq(mod(n,3),1)'",'-fps_mode','vfr']
    args+=['-c:v','libx264','-threads','1','-bf','0','-pix_fmt','yuv420p','-output_ts_offset','3']
    if audio:args+=['-c:a','aac']
    subprocess.run([*args,str(source)],check=True,timeout=20)
    image=tmp_path/'owned-production.png';Image.new('RGB',(16,16),'red').save(image)
    async with factory() as db:
        db.add(Project(id='foreign',name='Foreign software fixture',style='真人古装',visual_style='现实'))
        for identity,path,mime in [('source',source,'video/mp4'),('image',image,'image/png')]:
            db.add(FileItem(id=identity,type=mime.split('/')[0],name=path.name,storage_key=path.name));await db.flush()
            db.add(ProductionMedia(id=identity,project_id='p',name=path.name,mime=mime,sha256=checksum(path),size=path.stat().st_size,relative_path=path.name));await db.flush()
        asset=ProductionAsset(id='character-asset',project_id='p',kind='character',name='Manual production reference')
        db.add(asset);await db.flush()
        version=await add_version(db,asset,VersionWrite(description='Actual immutable PNG reference',media_ids=['image'],expected_revision=0))
        versionid=version.id;await db.commit()
    before=checksum(source)
    analysis=await create_analysis(factory,'p','source','actual-r1-analysis')
    assert await run_analysis(factory,analysis,str(FFMPEG))
    truth=decoded_clock(source)
    async with factory() as db:
        row=await db.get(ReverseAnalysis,analysis)
        assert row.manifest['video']['frames']==truth and row.confirmed_cuts is None
    cuts=[0,len(truth)//2]
    edited=await edit_cuts(factory,'p',analysis,cuts,1)
    assert edited['revision']==2 and checksum(source)==before
    (tmp_path/'independent-clock-truth.json').write_text(json.dumps({'frames':truth,'sha256':before,'cuts':cuts,
        'terminal_duration_known':False,'software_only':True},ensure_ascii=False,indent=2),'utf8')
    return engine,factory,analysis,truth,cuts,versionid,source,image


@pytest.mark.asyncio
async def test_independent_real_r1_fixture_has_exact_vfr_offset_and_no_invented_tail(tmp_path,monkeypatch):
    """Lock genuine R1 rational clocks independently before testing R2A behavior."""
    engine,factory,analysis,truth,cuts,version,source,image=await fixture(tmp_path,monkeypatch)
    try:
        assert truth[0]['pts']>0 and truth[0]['relative_us']==0
        gaps={b['relative_us']-a['relative_us'] for a,b in zip(truth,truth[1:])}
        assert len(gaps)>1 and cuts==[0,len(truth)//2]
        assert all(set(f)=={'index','pts','time_base_num','time_base_den','relative_us'} for f in truth)
        assert image.is_file() and 'duration' not in truth[-1]
    finally:await engine.dispose()


def unknown_claim(reason='Not established by this software-only human annotation'):
    """Create an explicit unknown declaration rather than inventing visual or audio facts."""
    return {'zh':None,'en':None,'status':'unknown','reason':reason,'confidence':None,'evidence':[]}


def reviewed_document(view,version=None):
    """Fill required review metadata/unknown reasons without adding model-generated claims."""
    document=deepcopy(view['document'])
    def annotate(value):
        """Recursively retain each existing field while supplying explicit unknown reasons."""
        if isinstance(value,dict):
            if value.get('status')=='unknown':value.update(unknown_claim())
            else:
                for child in value.values():annotate(child)
        elif isinstance(value,list):
            for child in value:annotate(child)
    annotate(document)
    document.update(title='Independent manual DNA 软件夹具',reviewer='Independent software reviewer',human_reviewed=True)
    if version:
        document['assets']=[{'id':'hero','kind':'character','name_zh':'人工参考角色','name_en':'Manual reference character',
            'fields':{key:unknown_claim() for key in ['face','hair','body','costume','makeup','accessories','voice']},
            'lock_prompt':unknown_claim(),'production_version_id':version,'binding_note':'Explicit manual correspondence, no identity recognition'}]
        document['shots'][0]['character_ids']=['hero']
    return document


async def dna_fixture(tmp_path,monkeypatch,**kwargs):
    """Add the actual new schema only after the independent source/production fixture is complete."""
    from app.services.video_dna.schema import migrate_video_dna
    from app.services.video_dna.service import create_draft,draft_view
    from app.core.contracts.video_dna import DnaCreate
    result=await fixture(tmp_path,monkeypatch,**kwargs)
    engine,factory,analysis,*_=result
    await migrate_video_dna(engine)
    body=DnaCreate(analysis_id=analysis,expected_analysis_revision=2,request_key='independent-template')
    draft=await create_draft(factory,'p',body)
    async with factory() as db:view=await draft_view(db,'p',draft)
    return (*result,draft,view,body)


async def freeze_reviewed(factory,draft,view,version=None):
    """Publish a complete explicit-unknown manual annotation under CAS for downstream tests."""
    from app.services.video_dna.service import patch_draft,freeze_draft
    from app.core.contracts.video_dna import DnaPatch,DnaFreeze
    document=reviewed_document(view,version)
    await patch_draft(factory,'p',draft,DnaPatch(expected_revision=1,document=document))
    snapshot=await freeze_draft(factory,'p',draft,DnaFreeze(expected_revision=2))
    return snapshot,document


@pytest.mark.asyncio
async def test_dna_source_preserves_real_rational_clock_and_terminal_unknown(tmp_path,monkeypatch):
    """Template boundaries use confirmed original indices, not candidates/fps or guessed terminal duration."""
    engine,factory,analysis,truth,cuts,version,source,image,draft,view,body=await dna_fixture(tmp_path,monkeypatch)
    try:
        fixed=view['source']
        assert fixed['frames']==truth and fixed['confirmed_cut_indices']==cuts
        expected=hashlib.sha256(canonical({'schema_version':1,'frames':truth}).encode()).hexdigest()
        assert fixed['frame_clock_hash']==expected and fixed['sha256']==checksum(source)
        assert fixed['terminal_time_known'] is False
        shots=view['document']['shots']
        assert [(s['start_frame'],s['end_frame']) for s in shots]==[(0,cuts[1]),(cuts[1],len(truth))]
        assert not view['readiness']['can_freeze']
        snapshot,document=await freeze_reviewed(factory,draft,view)
        from app.services.video_dna.service import snapshot_view
        async with factory() as db:
            locked=await snapshot_view(db,'p',snapshot)
            assert locked['payload']['source']==fixed and locked['payload']['human_declarations_only']
            assert hashlib.sha256(canonical(locked['payload']).encode()).hexdigest()==locked['content_hash']
        assert checksum(source)==fixed['sha256']
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_dna_cas_immutable_snapshot_and_precise_replay_before_changed_source(tmp_path,monkeypatch):
    """Concurrent writers cannot overwrite; recovery of an earlier key must not create a fresh intention."""
    from app.core.contracts.video_dna import DnaCreate,DnaPatch,DnaFreeze
    from app.services.video_dna.service import create_draft,patch_draft,freeze_draft,draft_view,snapshot_view
    engine,factory,analysis,truth,cuts,version,source,image,draft,view,body=await dna_fixture(tmp_path,monkeypatch)
    try:
        docs=[reviewed_document(view) for _ in range(6)]
        for i,document in enumerate(docs):document['title']='CAS '+str(i)
        results=await asyncio.gather(*(patch_draft(factory,'p',draft,DnaPatch(expected_revision=1,document=d)) for d in docs),return_exceptions=True)
        assert sum(not isinstance(x,BaseException) for x in results)==1
        assert all(isinstance(x,HTTPException) and x.status_code==409 for x in results if isinstance(x,BaseException))
        snapshot=await freeze_draft(factory,'p',draft,DnaFreeze(expected_revision=2))
        assert await freeze_draft(factory,'p',draft,DnaFreeze(expected_revision=2))==snapshot
        async with factory() as db:
            with pytest.raises(HTTPException) as error:await snapshot_view(db,'foreign',snapshot)
            assert error.value.status_code==404
        async with factory() as db:
            with pytest.raises(IntegrityError):
                await db.execute(text("UPDATE video_dna_snapshots SET content_hash='changed' WHERE id=:id"),{'id':snapshot})
                await db.commit()
            await db.rollback()
        source.write_bytes(b'Corrupt only this isolated source after earlier key committed')
        assert await create_draft(factory,'p',body)==draft
        with pytest.raises(HTTPException) as error:
            await create_draft(factory,'p',DnaCreate(analysis_id=analysis,expected_analysis_revision=2,request_key='new-after-corruption'))
        assert error.value.status_code==409
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_changed_confirmed_cuts_block_new_freeze_but_do_not_rewrite_historical_snapshot(tmp_path,monkeypatch):
    """A later human boundary revision cannot silently move old annotations or their clock evidence."""
    from app.core.contracts.video_dna import DnaCreate,DnaFreeze,DnaPatch
    from app.services.video_dna.service import create_draft,patch_draft,freeze_draft,snapshot_view,preflight_snapshot
    engine,factory,analysis,truth,cuts,version,source,image,draft,view,body=await dna_fixture(tmp_path,monkeypatch)
    try:
        snapshot,document=await freeze_reviewed(factory,draft,view)
        async with factory() as db:old=await snapshot_view(db,'p',snapshot)
        other=await create_draft(factory,'p',DnaCreate(analysis_id=analysis,expected_analysis_revision=2,request_key='second-before-cuts',document=document))
        await edit_cuts(factory,'p',analysis,[0],2)
        with pytest.raises(HTTPException) as error:await freeze_draft(factory,'p',other,DnaFreeze(expected_revision=1))
        assert error.value.status_code==409
        async with factory() as db:
            assert await snapshot_view(db,'p',snapshot)==old
            check=await preflight_snapshot(db,'p',snapshot)
            assert check['source_available'] and check['usable'] and not check['source_current']
    finally:await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['missing-media','same-size-tamper','missing-version-media','version-hash-mismatch'])
async def test_production_version_freeze_checks_real_bytes_and_exact_relation_set(tmp_path,monkeypatch,fault):
    """An immutable version hash of media IDs alone cannot certify referenced bytes or missing links."""
    from app.core.contracts.video_dna import DnaPatch,DnaFreeze
    from app.services.video_dna.service import patch_draft,freeze_draft
    engine,factory,analysis,truth,cuts,version,source,image,draft,view,body=await dna_fixture(tmp_path,monkeypatch)
    try:
        if fault in {'missing-version-media','version-hash-mismatch'}:
            async with factory() as db:
                old=await db.get(AssetVersion,version)
                payload=deepcopy(old.payload)
                version='malformed-'+fault
                db.add(AssetVersion(id=version,asset_id=old.asset_id,project_id='p',revision=2,payload=payload,
                    content_hash=hashlib.sha256(canonical(payload).encode()).hexdigest() if fault=='missing-version-media' else '0'*64))
                await db.flush()
                if fault=='version-hash-mismatch':db.add(VersionMedia(version_id=version,media_id='image',project_id='p'))
                await db.commit()
        document=reviewed_document(view,version)
        if fault=='missing-media':image.unlink()
        if fault=='same-size-tamper':
            content=image.read_bytes();image.write_bytes(bytes([content[0]^1])+content[1:])
        with pytest.raises(HTTPException) as error:
            await patch_draft(factory,'p',draft,DnaPatch(expected_revision=1,document=document))
            await freeze_draft(factory,'p',draft,DnaFreeze(expected_revision=2))
        assert error.value.status_code in {409,422}
    finally:await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('location',['voice','shot','global','dialogue-inferred'])
async def test_no_audio_cannot_be_relabelled_as_observed_voice_or_dialogue(tmp_path,monkeypatch,location):
    """No-track sources cannot acquire original speech merely through a different claim field."""
    from app.core.contracts.video_dna import DnaPatch,DnaFreeze
    from app.services.video_dna.service import patch_draft,freeze_draft
    engine,factory,analysis,truth,cuts,version,source,image,draft,view,body=await dna_fixture(tmp_path,monkeypatch)
    try:
        document=reviewed_document(view,version)
        claim={'zh':'人工构想发声','en':'Human imagined sound','status':'observed','reason':'Manual software annotation','confidence':50,'evidence':[{'start_frame':0,'end_frame':1}]}
        if location=='voice':document['assets'][0]['fields']['voice']=claim
        if location=='shot':document['shots'][0]['audio']=claim
        if location=='global':document['audio_editing']=claim
        if location=='dialogue-inferred':
            claim['status']='inferred'
            document['shots'][0]['dialogues']=[{'id':'invented-line','speaker_id':'hero','claim':claim}]
        with pytest.raises(HTTPException) as error:
            await patch_draft(factory,'p',draft,DnaPatch(expected_revision=1,document=document))
            await freeze_draft(factory,'p',draft,DnaFreeze(expected_revision=2))
        assert error.value.status_code in {409,422}
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_no_audio_inferred_sound_design_can_remain_explicit_hypothesis(tmp_path,monkeypatch):
    """Imagined sound design may be recorded with reasons while never becoming original-source speech."""
    from app.core.contracts.video_dna import DnaPatch,DnaFreeze
    from app.services.video_dna.service import patch_draft,freeze_draft,snapshot_view
    engine,factory,analysis,truth,cuts,version,source,image,draft,view,body=await dna_fixture(tmp_path,monkeypatch)
    try:
        document=reviewed_document(view,version)
        document['shots'][0]['audio']={'zh':'可后配风声','en':'Proposed wind sound design','status':'inferred','reason':'No source audio; proposed reconstruction only','confidence':20,'evidence':[{'start_frame':0,'end_frame':1}]}
        await patch_draft(factory,'p',draft,DnaPatch(expected_revision=1,document=document))
        snapshot=await freeze_draft(factory,'p',draft,DnaFreeze(expected_revision=2))
        async with factory() as db:
            value=await snapshot_view(db,'p',snapshot)
            assert value['payload']['document']['shots'][0]['audio']['status']=='inferred'
            assert value['payload']['source']['audio_present'] is False
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_snapshot_import_roundtrip_is_new_draft_not_claimed_qualification(tmp_path,monkeypatch):
    """Roundtrip preserves semantic hash but imported PASS/task/provider fields never acquire authority."""
    from app.core.contracts.video_dna import DnaImport,DnaFreeze
    from app.services.video_dna.service import import_draft,freeze_draft,snapshot_view,draft_view
    from app.models.task import GenerationTask
    from app.models.execution import ExecutionJob,SubmissionAttempt
    from app.models.production import ProductionTake,ShotSelection
    engine,factory,analysis,truth,cuts,version,source,image,draft,view,body=await dna_fixture(tmp_path,monkeypatch)
    try:
        original,_=await freeze_reviewed(factory,draft,view,version)
        async with factory() as db:
            envelope=await snapshot_view(db,'p',original)
            before=[await db.scalar(select(func.count()).select_from(model)) for model in [GenerationTask,ExecutionJob,SubmissionAttempt,ProductionTake,ShotSelection]]
        exported=deepcopy(envelope);exported.update(status='PASSED',model_qualified=True,execution_enabled=True,score=100,task_id='fake-task')
        imported=await import_draft(factory,'p',DnaImport(analysis_id=analysis,expected_analysis_revision=2,request_key='roundtrip',content=exported))
        assert imported!=draft
        async with factory() as db:
            current=await draft_view(db,'p',imported)
            assert current['document']==envelope['payload']['document']
            assert [await db.scalar(select(func.count()).select_from(model)) for model in [GenerationTask,ExecutionJob,SubmissionAttempt,ProductionTake,ShotSelection]]==before
        second=await freeze_draft(factory,'p',imported,DnaFreeze(expected_revision=1))
        async with factory() as db:
            restored=await snapshot_view(db,'p',second)
            assert restored['content_hash']==envelope['content_hash'] and restored['payload']==envelope['payload']
        forged=deepcopy(envelope)
        forged['payload']['source']['frames'][0]['pts']+=1
        with pytest.raises(HTTPException) as error:
            await import_draft(factory,'p',DnaImport(analysis_id=analysis,expected_analysis_revision=2,request_key='forged-clock',content=forged))
        assert error.value.status_code in {409,422}
    finally:await engine.dispose()


@pytest.mark.parametrize('bad',[True,1.0,2])
def test_schema_version_never_coerces_boolean_float_or_future_version(bad):
    """The explicit v1 format cannot silently reinterpret boolean, float or future schemas."""
    from pydantic import ValidationError
    from app.core.contracts.video_dna import DnaDocument
    with pytest.raises(ValidationError):DnaDocument(schema_version=bad)


@pytest.mark.parametrize('bad',[SAFE+1,-SAFE-1,1.0,True])
def test_nested_frame_clock_rejects_unsafe_or_noninteger_pts(bad):
    """Portable clocks reject loss of JS precision and coercion at the actual frame DTO boundary."""
    from pydantic import ValidationError
    from app.core.contracts.video_dna import DnaFrame
    with pytest.raises(ValidationError):DnaFrame(index=0,pts=bad,time_base_num=1,time_base_den=1000,relative_us=0)


def test_exact_safe_frame_integer_boundaries_are_not_rounded():
    """Both signed maximum-safe PTS values survive unchanged; no string conversion is allowed."""
    from app.core.contracts.video_dna import DnaFrame
    for value in [-SAFE,SAFE]:
        frame=DnaFrame(index=0,pts=value,time_base_num=1,time_base_den=1000,relative_us=0)
        assert type(frame.pts) is int and frame.pts==value


@pytest.mark.asyncio
async def test_preparation_is_seven_layers_deterministic_hypothesis_with_unknown_terminal(tmp_path,monkeypatch):
    """Known/unknown/inferred declarations remain distinct in a non-executable deterministic preparation."""
    from app.services.video_dna.service import owned_snapshot
    from app.services.video_dna.compiler import preparation_view
    engine,factory,analysis,truth,cuts,version,source,image,draft,view,body=await dna_fixture(tmp_path,monkeypatch)
    try:
        snapshot,document=await freeze_reviewed(factory,draft,view,version)
        async with factory() as db:
            fixed=await owned_snapshot(db,'p',snapshot)
            first=preparation_view(fixed);second=preparation_view(fixed)
            assert first==second
            assert hashlib.sha256(canonical(first['payload']).encode()).hexdigest()==first['content_hash']
            from types import SimpleNamespace
            alternate=preparation_view(SimpleNamespace(id='different-envelope-id',payload=deepcopy(fixed.payload),content_hash=fixed.content_hash))
            assert alternate['content_hash']==first['content_hash'] and alternate['snapshot_id']!=first['snapshot_id']
        prepared=first['payload']
        assert prepared['execution_enabled'] is False and prepared['model_qualified'] is False and prepared['original_author_prompt'] is False
        assert prepared['prompt_provenance']=='human_reconstruction_hypothesis'
        expected=['GLOBAL_STYLE_PROMPT','CHARACTER_LOCK_PROMPT','SCENE_LOCK_PROMPT','SHOT_IMAGE_PROMPT','SHOT_VIDEO_PROMPT','NEGATIVE_CONSTRAINTS','AUDIO_AND_EDITING_PROMPTS']
        for entry in prepared['entries']:
            assert [layer['key'] for layer in entry['layers']]==expected
            assert entry['unknowns'] and all(x['reason'] for x in entry['unknowns'])
        last=prepared['entries'][-1]
        assert last['end_frame']==len(truth) and last['end_relative_us'] is None
        assert any(item['code']=='terminal_duration_unknown' for item in last['issues'])
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_comparison_targets_id_dependencies_and_metadata_does_not_touch_all_shots(tmp_path,monkeypatch):
    """Asset edits/removal use old-new users; title-only edits cannot pretend every request changed."""
    from types import SimpleNamespace
    from app.services.video_dna.service import owned_snapshot
    from app.services.video_dna.comparison import compare_snapshots
    engine,factory,analysis,truth,cuts,version,source,image,draft,view,body=await dna_fixture(tmp_path,monkeypatch)
    try:
        snapshot,document=await freeze_reviewed(factory,draft,view,version)
        async with factory() as db:fixed=await owned_snapshot(db,'p',snapshot)
        base=SimpleNamespace(id='base',payload=deepcopy(fixed.payload))
        firstshot,secondshot=[s['id'] for s in document['shots']]
        def compare(mutator):
            """Mutate only independent input data; expected impact derives from declared local fixture references."""
            changed=deepcopy(base.payload);mutator(changed)
            return compare_snapshots(base,SimpleNamespace(id='changed',payload=changed))
        title=compare(lambda p:p['document'].update(title='Metadata only'))
        assert title['affected_shot_ids']==[] and '/document/title' in title['changed_paths']
        costume=compare(lambda p:p['document']['assets'][0]['fields']['costume'].update(reason='Different unknown costume explanation'))
        assert costume['affected_shot_ids']==[firstshot]
        globalchange=compare(lambda p:p['document']['global_style'].update(reason='New global unknown explanation'))
        assert globalchange['affected_shot_ids']==[firstshot,secondshot]
        removed=compare(lambda p:p['document']['shots'][0].update(character_ids=[]))
        assert removed['affected_shot_ids']==[firstshot] and removed['dependency_index']['hero']==[firstshot]
        reordered=compare(lambda p:p['document']['assets'].reverse())
        assert reordered['affected_shot_ids']==[]
        foreign=deepcopy(base.payload);foreign['source']['sha256']='0'*64
        with pytest.raises(HTTPException) as error:compare_snapshots(base,SimpleNamespace(id='foreign',payload=foreign))
        assert error.value.status_code==422
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_frame_clock_hash_is_sensitive_to_rational_pts_but_not_confirmed_cuts(tmp_path,monkeypatch):
    """Independent transformed manifests distinguish exact rational identity from rounded relative display time."""
    from types import SimpleNamespace
    from app.services.video_dna.source import source_from_analysis
    engine,factory,analysis,truth,cuts,version,source,image=await fixture(tmp_path,monkeypatch)
    try:
        async with factory() as db:
            row=await db.get(ReverseAnalysis,analysis)
            properties={key:deepcopy(getattr(row,key)) for key in ['id','revision','media_id','source_hash','state','manifest','confirmed_cuts']}
        baseline=source_from_analysis(SimpleNamespace(**properties))
        shifted=deepcopy(properties)
        for frame in shifted['manifest']['video']['frames']:frame['pts']+=1
        exact=source_from_analysis(SimpleNamespace(**shifted))
        assert [f['relative_us'] for f in exact['frames']]==[f['relative_us'] for f in baseline['frames']]
        assert exact['frame_clock_hash']!=baseline['frame_clock_hash']
        recut=deepcopy(properties);recut['confirmed_cuts']=[recut['confirmed_cuts'][0]];recut['revision']+=1
        boundaries=source_from_analysis(SimpleNamespace(**recut))
        assert boundaries['frame_clock_hash']==baseline['frame_clock_hash']
    finally:await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['missing-shot','unknown-with-text','inferred-without-reason','missing-English','foreign-character-reference','outside-shot-evidence'])
async def test_review_cannot_lock_missing_structure_or_mislabeled_claims(tmp_path,monkeypatch,fault):
    """Human-reviewed does not override whole-source coverage, status coherence or local evidence bounds."""
    from app.core.contracts.video_dna import DnaPatch,DnaFreeze
    from app.services.video_dna.service import patch_draft,freeze_draft
    engine,factory,analysis,truth,cuts,version,source,image,draft,view,body=await dna_fixture(tmp_path,monkeypatch)
    try:
        document=reviewed_document(view,version)
        observed={'zh':'人工观察构图','en':'Human declared framing','status':'observed','reason':'','confidence':50,'evidence':[{'start_frame':0,'end_frame':1}]}
        if fault=='missing-shot':document['shots'].pop()
        if fault=='unknown-with-text':document['global_style']['zh']='Cannot mix known text with unknown status'
        if fault=='inferred-without-reason':
            observed['status']='inferred';document['shots'][0]['camera']=observed
        if fault=='missing-English':
            observed['en']=None;document['shots'][0]['composition']=observed
        if fault=='foreign-character-reference':document['shots'][0]['character_ids']=['missing-entity']
        if fault=='outside-shot-evidence':
            observed['evidence']=[{'start_frame':cuts[1],'end_frame':cuts[1]+1}];document['shots'][0]['camera']=observed
        with pytest.raises(HTTPException) as error:
            await patch_draft(factory,'p',draft,DnaPatch(expected_revision=1,document=document))
            await freeze_draft(factory,'p',draft,DnaFreeze(expected_revision=2))
        assert error.value.status_code in {409,422}
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_owned_source_version_kind_and_idempotent_intent_cannot_be_bypassed(tmp_path,monkeypatch):
    """Project ownership and selected version kind are server facts, not import or client assertions."""
    from app.core.contracts.video_dna import DnaCreate,DnaPatch
    from app.services.video_dna.service import create_draft,patch_draft
    engine,factory,analysis,truth,cuts,version,source,image,draft,view,body=await dna_fixture(tmp_path,monkeypatch)
    try:
        with pytest.raises(HTTPException) as error:await create_draft(factory,'foreign',body)
        assert error.value.status_code==404
        changed=body.model_dump();changed['document']=reviewed_document(view)
        with pytest.raises(HTTPException) as error:await create_draft(factory,'p',DnaCreate(**changed))
        assert error.value.status_code==409
        async with factory() as db:
            wrong=ProductionAsset(id='wrong-scene',project_id='p',kind='scene',name='Actual scene, not a character')
            db.add(wrong);await db.flush()
            scene=await add_version(db,wrong,VersionWrite(expected_revision=0))
            wrongversion=scene.id;await db.commit()
        document=reviewed_document(view,wrongversion)
        with pytest.raises(HTTPException) as error:await patch_draft(factory,'p',draft,DnaPatch(expected_revision=1,document=document))
        assert error.value.status_code==422
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_actual_node_python_snapshot_and_preparation_canonical_hash_agree(tmp_path,monkeypatch):
    """Run an independent JavaScript canonicalizer over real Unicode snapshot/preparation values."""
    import shutil
    from app.core.contracts.video_dna import DnaPatch,DnaFreeze
    from app.services.video_dna.service import patch_draft,freeze_draft,owned_snapshot
    from app.services.video_dna.compiler import preparation_view
    node=shutil.which('node');assert node,'Actual JavaScript runtime required for this cross-runtime check'
    engine,factory,analysis,truth,cuts,version,source,image,draft,view,body=await dna_fixture(tmp_path,monkeypatch)
    try:
        document=reviewed_document(view,version)
        document['global_style']={'zh':'人工风格：雪夜 😄 𠀀\n引号" 与 \\ 路径字样仅文本\u2028分隔',
            'en':'Human style hypothesis: snow night 😄\nquote" backslash\\',
            'status':'inferred','reason':'Manual cross-runtime text fixture, not source fact','confidence':50,
            'evidence':[{'start_frame':0,'end_frame':1}]}
        await patch_draft(factory,'p',draft,DnaPatch(expected_revision=1,document=document))
        identity=await freeze_draft(factory,'p',draft,DnaFreeze(expected_revision=2))
        async with factory() as db:
            fixed=await owned_snapshot(db,'p',identity)
            preparation=preparation_view(fixed)
            values=[{'payload':fixed.payload,'expected':fixed.content_hash},{'payload':preparation['payload'],'expected':preparation['content_hash']}]
        path=tmp_path/'cross-runtime-values.json';path.write_text(json.dumps(values,ensure_ascii=False),'utf8')
        code="const fs=require('fs'),crypto=require('crypto');const canonical=v=>Array.isArray(v)?v.map(canonical):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])):v;const xs=JSON.parse(fs.readFileSync(process.argv[1],'utf8'));for(const x of xs){const hash=crypto.createHash('sha256').update(JSON.stringify(canonical(x.payload)),'utf8').digest('hex');if(hash!==x.expected)throw new Error(hash+' != '+x.expected);console.log(hash)}"
        result=subprocess.run([node,'-e',code,str(path)],capture_output=True,text=True,check=True,timeout=15)
        assert result.stdout.splitlines()==[item['expected'] for item in values]
        (tmp_path/'cross-runtime-hash-result.json').write_text(json.dumps({'status':'PASSED','node':node,'hashes':result.stdout.splitlines()},indent=2),'utf8')
    finally:await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('broken',['source','version-media'])
async def test_historical_snapshot_remains_readable_but_bad_bytes_never_gain_preflight_availability(tmp_path,monkeypatch,broken):
    """Historical fixed semantics survive byte loss, while availability is an independently failed fact."""
    from app.services.video_dna.service import snapshot_view,preflight_snapshot,owned_snapshot
    from app.services.video_dna.compiler import preparation_view
    engine,factory,analysis,truth,cuts,version,source,image,draft,view,body=await dna_fixture(tmp_path,monkeypatch)
    try:
        identity,_=await freeze_reviewed(factory,draft,view,version)
        async with factory() as db:
            before=await snapshot_view(db,'p',identity)
            original_preparation=preparation_view(await owned_snapshot(db,'p',identity))
        (source if broken=='source' else image).unlink()
        async with factory() as db:
            assert await snapshot_view(db,'p',identity)==before
            check=await preflight_snapshot(db,'p',identity)
            assert not check['usable']
            assert check['source_available']==(broken!='source')
            assert check['asset_versions_available']==(broken!='version-media')
            assert preparation_view(await owned_snapshot(db,'p',identity))==original_preparation
    finally:await engine.dispose()


def test_complete_portable_envelope_uses_exact_two_mib_boundary():
    """Independently measure full-envelope bytes at 2MiB; payload-only size must not bypass the guard."""
    from app.services.video_dna.service import portable_snapshot
    identifier='0'*36;digest='f'*64;limit=2*1024*1024
    payload={'padding':''}
    overhead=len(canonical({'id':identifier,'content_hash':digest,'payload':payload}).encode())
    payload['padding']='x'*(limit-overhead)
    assert len(canonical(payload).encode())<limit
    exact=portable_snapshot(identifier,payload,digest)
    assert len(canonical(exact).encode())==limit
    payload['padding']+='x'
    assert len(canonical(payload).encode())<limit
    with pytest.raises(HTTPException) as error:portable_snapshot(identifier,payload,digest)
    assert error.value.status_code==409


@pytest.mark.asyncio
async def test_freeze_full_envelope_budget_rolls_back_then_actual_http_export_reimports_at_limit(tmp_path,monkeypatch):
    """A lowered quota seam exercises actual freeze/HTTP serialization, without constructing 2MiB of fake claims."""
    import httpx
    from fastapi import FastAPI
    from app.api.video_dna import router,get_video_dna_factory
    from app.core.contracts.video_dna import DnaCreate,DnaFreeze
    from app.models.video_dna import DnaSnapshot
    from app.services.video_dna import service
    engine,factory,analysis,truth,cuts,version,source,image,draft,view,body=await dna_fixture(tmp_path,monkeypatch)
    app=FastAPI();app.include_router(router);app.dependency_overrides[get_video_dna_factory]=lambda:factory
    try:
        first,document=await freeze_reviewed(factory,draft,view,version)
        async with factory() as db:
            saved=await service.snapshot_view(db,'p',first)
            count=await db.scalar(select(func.count()).select_from(DnaSnapshot))
        payloadbytes=len(canonical(saved['payload']).encode())
        fullbytes=len(canonical(saved).encode())
        assert fullbytes>payloadbytes+1
        nextdraft=await service.create_draft(factory,'p',DnaCreate(analysis_id=analysis,expected_analysis_revision=2,request_key='boundary-draft',document=document))
        monkeypatch.setattr(service,'MAX_PORTABLE_BYTES',payloadbytes+1)
        with pytest.raises(HTTPException) as error:await service.freeze_draft(factory,'p',nextdraft,DnaFreeze(expected_revision=1))
        assert error.value.status_code==409
        async with factory() as db:assert await db.scalar(select(func.count()).select_from(DnaSnapshot))==count
        monkeypatch.setattr(service,'MAX_PORTABLE_BYTES',fullbytes)
        accepted=await service.freeze_draft(factory,'p',nextdraft,DnaFreeze(expected_revision=1))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local',trust_env=False) as client:
            base='/video-dna/projects/p'
            response=await client.get(base+'/snapshots/'+accepted+'/export')
            assert response.status_code==200,response.text
            assert len(response.content)==fullbytes
            envelope=response.json()
            assert envelope['content_hash']==saved['content_hash']
            imported=await client.post(base+'/imports',json={'analysis_id':analysis,'expected_analysis_revision':2,'request_key':'boundary-roundtrip','content':envelope})
            assert imported.status_code==201,imported.text
            importedid=imported.json()['data']['id']
            locked=await client.post(base+'/drafts/'+importedid+'/freeze',json={'expected_revision':1})
            assert locked.status_code==201,locked.text
            assert locked.json()['data']['content_hash']==saved['content_hash']
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_import_false_cut_is_rejected_even_when_python_integer_equality_would_match(tmp_path,monkeypatch):
    """A real HTTP importer cannot treat False as the first original-frame index 0."""
    import httpx
    from fastapi import FastAPI
    from app.api.video_dna import router,get_video_dna_factory
    from app.services.video_dna.service import snapshot_view
    from app.models.video_dna import DnaDraft,DnaSnapshot
    engine,factory,analysis,truth,cuts,version,source,image,draft,view,body=await dna_fixture(tmp_path,monkeypatch)
    app=FastAPI();app.include_router(router);app.dependency_overrides[get_video_dna_factory]=lambda:factory
    try:
        snapshot,_=await freeze_reviewed(factory,draft,view)
        async with factory() as db:
            envelope=await snapshot_view(db,'p',snapshot)
            before=[await db.scalar(select(func.count()).select_from(model)) for model in [DnaDraft,DnaSnapshot]]
        bad=deepcopy(envelope);bad['payload']['source']['confirmed_cut_indices'][0]=False
        assert bad['payload']['source']['confirmed_cut_indices']==cuts
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local',trust_env=False) as client:
            response=await client.post('/video-dna/projects/p/imports',json={'analysis_id':analysis,'expected_analysis_revision':2,'request_key':'false-cut','content':bad})
            assert response.status_code==422,response.text
        async with factory() as db:
            assert [await db.scalar(select(func.count()).select_from(model)) for model in [DnaDraft,DnaSnapshot]]==before
    finally:await engine.dispose()
