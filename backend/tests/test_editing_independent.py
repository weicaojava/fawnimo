"""Independent editing fixtures and rational-clock truth; not AI or semantic evidence."""
import hashlib
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import subprocess
from fractions import Fraction
import av
import pytest
import numpy as np
from PIL import Image
from app.services.editing.media import run_child,covers

ROOT=Path(__file__).resolve().parents[5]
FFMPEG=ROOT/'work/ffmpeg.exe'


def digest(path):
    """Hash exact media bytes for unchanged-source assertions and manifest binding."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def media_truth(path):
    """Decode source clocks independently of the product probe/render implementation."""
    with av.open(str(path)) as container:
        videos=[]
        for index,frame in enumerate(container.decode(video=0)):
            stamp=Fraction(frame.pts)*frame.time_base
            videos.append({'index':index,'pts':frame.pts,'time_base':[frame.time_base.numerator,frame.time_base.denominator],
                'duration':frame.duration,'absolute_seconds':[stamp.numerator,stamp.denominator]})
    origin=Fraction(*videos[0]['absolute_seconds'])
    for frame in videos:
        relative=Fraction(*frame['absolute_seconds'])-origin
        frame['relative_seconds']=[relative.numerator,relative.denominator]
    audios=[]
    with av.open(str(path)) as container:
        if container.streams.audio:
            for frame in container.decode(audio=0):
                start=Fraction(frame.pts)*frame.time_base-origin
                end=start+Fraction(frame.samples,frame.sample_rate)
                audios.append({'pts':frame.pts,'time_base':[frame.time_base.numerator,frame.time_base.denominator],
                    'samples':frame.samples,'sample_rate':frame.sample_rate,
                    'start_seconds':[start.numerator,start.denominator],'end_seconds':[end.numerator,end.denominator]})
    return {'sha256':digest(path),'path':str(path),'video_frames':videos,'audio_frames':audios,
            'source_origin_seconds':[origin.numerator,origin.denominator],
            'fixture_only':True,'not_semantic_or_model_evidence':True}


def generate_media_bank(folder,ffmpeg=FFMPEG):
    """Author bounded distinct colors/tones, VFR and positive/negative audio offsets.

    The caller supplies a fresh GUID test-run folder. Existing files are never
    overwritten; return source recipes and exact independent decoded clocks.
    """
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    recipes={
        'red-tone':['-f','lavfi','-i','color=red:s=64x96:r=24:d=2','-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=2'],
        'green-silent':['-f','lavfi','-i','color=green:s=64x96:r=24:d=2'],
        'blue-tone':['-f','lavfi','-i','color=blue:s=64x96:r=24:d=2','-f','lavfi','-i','sine=frequency=880:sample_rate=48000:duration=2'],
        'vfr-offset':['-f','lavfi','-i','testsrc2=s=64x96:r=24:d=2','-vf',"select='not(mod(n,2))+eq(mod(n,7),0)'",'-fps_mode','vfr','-output_ts_offset','3'],
        'vfr-contiguous-offset':['-f','lavfi','-i','testsrc2=s=64x96:r=24:d=2','-vf',"select='not(mod(n,2))+eq(mod(n,7),0)'",'-fps_mode','vfr','-bf','0','-output_ts_offset','3'],
        'positive-audio':['-f','lavfi','-i','color=yellow:s=64x96:r=24:d=2','-itsoffset','0.3','-f','lavfi','-i','sine=frequency=660:sample_rate=48000:duration=2','-output_ts_offset','3'],
        'negative-audio':['-f','lavfi','-i','color=cyan:s=64x96:r=24:d=2','-itsoffset','-0.2','-f','lavfi','-i','sine=frequency=550:sample_rate=48000:duration=2','-output_ts_offset','3'],
        'audio-gap':['-f','lavfi','-i','color=magenta:s=64x96:r=24:d=2','-f','lavfi','-i','sine=frequency=770:sample_rate=48000:duration=2','-af',"aselect='not(between(t,0.6,1.2))'"],
    }
    result={}
    for name,arguments in recipes.items():
        target=folder/(name+'.mp4')
        if target.exists():raise RuntimeError('Independent fixture must not overwrite prior bytes')
        command=[str(ffmpeg),'-nostdin','-v','error',*arguments,'-c:v','libx264','-threads','1','-pix_fmt','yuv420p','-c:a','aac',str(target)]
        subprocess.run(command,check=True,timeout=30)
        result[name]=media_truth(target)
        result[name]['generation_command']=command
    (folder/'independent-clock-truth.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    return result


def expected_source_index(truth,source_time):
    """Implement contract sampling with Fractions independently of renderer code."""
    frames=truth['video_frames']
    eligible=[f for f in frames if Fraction(*f['relative_seconds'])<=source_time]
    if not eligible:raise ValueError('No source presentation covers this output sample')
    frame=eligible[-1];start=Fraction(*frame['relative_seconds'])
    duration=Fraction(frame['duration'])*Fraction(*frame['time_base']) if frame['duration'] else None
    following=frames[frame['index']+1] if frame['index']+1<len(frames) else None
    end=start+duration if duration and duration>0 else Fraction(*following['relative_seconds']) if following else None
    if following is not None:end=min(end,Fraction(*following['relative_seconds']))
    if end is None or not start<=source_time<end:raise ValueError('Sample outside actual effective presentation')
    return frame['index']


def expected_audio_segments(truth):
    """Keep true audio gaps, without using a min/max envelope or synthesized silence."""
    segments=[]
    for frame in truth['audio_frames']:
        start,end=Fraction(*frame['start_seconds']),Fraction(*frame['end_seconds'])
        if segments and start-segments[-1][1]<=Fraction(1,1000000):
            segments[-1]=(segments[-1][0],max(end,segments[-1][1]))
        else:segments.append((start,end))
    return segments


def test_independent_media_fixture_clocks_are_locked(tmp_path):
    """Verify authored fixtures, not the product: no renderer pass is implied."""
    bank=generate_media_bank(tmp_path/'independent-media')
    assert len(bank['red-tone']['video_frames'])==48
    assert not bank['green-silent']['audio_frames']
    origin=Fraction(*bank['vfr-offset']['source_origin_seconds'])
    assert origin==3
    times=[Fraction(*f['relative_seconds']) for f in bank['vfr-offset']['video_frames']]
    assert len({b-a for a,b in zip(times,times[1:])})>1
    assert expected_audio_segments(bank['positive-audio'])[0][0]>Fraction(1,5)
    assert expected_audio_segments(bank['negative-audio'])[0][0]<0
    assert len(expected_audio_segments(bank['audio-gap']))>=2
    assert [expected_source_index(bank['red-tone'],Fraction(k,30)) for k in range(6)]==[0,0,1,2,3,4]
    for evidence in bank.values():assert digest(evidence['path'])==evidence['sha256']


@pytest.mark.asyncio
async def test_product_probe_matches_independent_rational_video_and_audio_truth(tmp_path):
    """Cross-check actual helper clocks, offsets and internal gaps, not its own min/max."""
    bank=generate_media_bank(tmp_path/'probe-sources')
    for name,truth in bank.items():
        actual=await run_child({'mode':'probe','path':truth['path'],'expected_hash':truth['sha256']})
        expected_clock=[[f['pts'],*f['time_base'],f['duration']] for f in truth['video_frames']]
        expected_hash=hashlib.sha256(json.dumps(expected_clock,separators=(',',':'),sort_keys=True).encode()).hexdigest()
        assert actual['video']['frame_clock_hash']==expected_hash
        assert actual['video']['frames']==len(truth['video_frames'])
        assert Fraction(actual['video']['first_pts_num'],actual['video']['first_pts_den'])==Fraction(*truth['source_origin_seconds'])
        if truth['audio_frames']:
            assert actual['audio'] is not None
            if name=='audio-gap':
                assert len(actual['audio']['coverage_us'])>=2
                assert not covers(actual['audio']['coverage_us'],800,900)
            if name=='positive-audio':assert actual['audio']['start_us']>200000
            if name=='negative-audio':assert actual['audio']['start_us']<0
        else:assert actual['audio'] is None


def normalized_picture(frame):
    """Independently scale/pad square pixels according to the fixed profile."""
    image=frame.to_image()
    factor=min(Fraction(360,image.width),Fraction(640,image.height))
    dimensions=(int(image.width*factor),int(image.height*factor))
    image=image.resize(dimensions,Image.Resampling.BILINEAR)
    canvas=Image.new('RGB',(360,640))
    canvas.paste(image,((360-dimensions[0])//2,(640-dimensions[1])//2))
    return np.asarray(canvas,dtype=np.float32)


@pytest.mark.asyncio
async def test_actual_render_vfr_offset_crops_follow_independent_source_indices(tmp_path):
    """Verify every decoded pixel frame and output clock against Fraction sampling."""
    bank=generate_media_bank(tmp_path/'sample-sources')
    source=bank['vfr-contiguous-offset']
    facts=await run_child({'mode':'probe','path':source['path'],'expected_hash':source['sha256']})
    clip={'shot_id':'fixture-shot','take_id':'fixture-take','origin':{'kind':'explicit_software_fixture'},
          'source':{'id':'fixture-vfr','sha256':source['sha256'],**facts},'path':source['path'],
          'source_in_ms':100,'source_out_ms':1600,'source_audio':False,'timeline_start_ms':0,'timeline_end_ms':1500}
    folder=tmp_path/'sample-output';folder.mkdir()
    payload={'schema_version':1,'clips':[clip],'subtitles':[],'duration_ms':1500,'profile':'portrait-360p30-v1'}
    output=await run_child({'mode':'render','folder':str(folder),'ffmpeg':str(FFMPEG),'payload':payload,
        'publication_id':'independent-clock-fixture','publication_hash':'0'*64},deadline=120)
    with av.open(source['path']) as container:original=[normalized_picture(f) for f in container.decode(video=0)]
    with av.open(str(folder/'video.mp4')) as container:rendered=list(container.decode(video=0))
    assert len(rendered)==45
    for k,frame in enumerate(rendered):
        assert Fraction(frame.pts)*frame.time_base==Fraction(k,30)
        expected=expected_source_index(source,Fraction(1,10)+Fraction(k,30))
        error=np.abs(frame.to_ndarray(format='rgb24').astype(np.float32)-original[expected]).mean()/255
        assert error<0.05,(k,expected,error)
    assert output['facts']['video']['frames']==45
    assert digest(source['path'])==source['sha256']


@pytest.mark.asyncio
async def test_positive_duration_vfr_gap_is_not_silently_stretched(tmp_path):
    """Actual duration shorter than following PTS must block a crop crossing the gap."""
    from fastapi import HTTPException
    bank=generate_media_bank(tmp_path/'gap-sources');source=bank['vfr-offset']
    facts=await run_child({'mode':'probe','path':source['path'],'expected_hash':source['sha256']})
    assert len(facts['video']['coverage_us'])>1
    assert not covers(facts['video']['coverage_us'],0,1000)
    clip={'shot_id':'gap-shot','take_id':'gap-take','origin':{'kind':'explicit_software_fixture'},
          'source':{'id':'gappy-vfr','sha256':source['sha256'],**facts},'path':source['path'],
          'source_in_ms':0,'source_out_ms':1000,'source_audio':False,'timeline_start_ms':0,'timeline_end_ms':1000}
    folder=tmp_path/'gap-output';folder.mkdir()
    with pytest.raises(HTTPException) as error:
        await run_child({'mode':'render','folder':str(folder),'ffmpeg':str(FFMPEG),
            'payload':{'clips':[clip],'subtitles':[],'duration_ms':1000,'profile':'portrait-360p30-v1'},
            'publication_id':'gap-negative-fixture','publication_hash':'0'*64},deadline=120)
    assert error.value.status_code==409 and not (folder/'video.mp4').exists()


@pytest.mark.asyncio
async def test_actual_three_color_render_has_all_middle_frames_and_declared_silence(tmp_path):
    """A real three-clip MP4 must contain all colors and tones, never first-clip fallback."""
    import zipfile
    bank=generate_media_bank(tmp_path/'three-sources')
    clips=[]
    for i,name in enumerate(['red-tone','green-silent','blue-tone']):
        source=bank[name]
        facts=await run_child({'mode':'probe','path':source['path'],'expected_hash':source['sha256']})
        clips.append({'shot_id':'fixture-shot-'+str(i),'take_id':'fixture-take-'+str(i),'origin':{'kind':'explicit_software_fixture'},
            'source':{'id':'fixture-'+str(i),'sha256':source['sha256'],**facts},'path':source['path'],
            'source_in_ms':0,'source_out_ms':2000,'source_audio':True,'timeline_start_ms':i*2000,'timeline_end_ms':(i+1)*2000})
    folder=tmp_path/'three-output';folder.mkdir()
    cue={'dialogue_id':'fixture-line','speaker_id':'fixture-person','text':'literal <tag>\n00:00:00 --> marker','start_ms':100,'end_ms':600}
    payload={'schema_version':1,'clips':clips,'subtitles':[cue],'duration_ms':6000,'profile':'portrait-360p30-v1'}
    await run_child({'mode':'render','folder':str(folder),'ffmpeg':str(FFMPEG),'payload':payload,
        'publication_id':'independent-three-fixture','publication_hash':'0'*64},deadline=120)
    with av.open(str(folder/'video.mp4')) as container:
        frames=list(container.decode(video=0))
        assert len(frames)==180
        for index,channel in [(15,0),(75,1),(135,2)]:
            color=frames[index].to_ndarray(format='rgb24')[100:540,100:260].mean(axis=(0,1))
            assert int(color.argmax())==channel and color[channel]>100
    chunks=[]
    with av.open(str(folder/'video.mp4')) as container:
        for frame in container.decode(audio=0):
            time=float(Fraction(frame.pts)*frame.time_base)
            chunks.append((time,float(np.sqrt(np.mean(frame.to_ndarray().astype(np.float64)**2)))))
    loud=[volume for time,volume in chunks if .3<time<1.7 or 4.3<time<5.7]
    silent=[volume for time,volume in chunks if 2.3<time<3.7]
    assert loud and silent and np.mean(loud)>0.02 and max(silent)<0.001
    with zipfile.ZipFile(folder/'export.zip') as archive:
        assert set(archive.namelist())=={'video.mp4','subtitles.srt','subtitles.vtt','manifest.json'}
        assert archive.testzip() is None
        manifest=json.loads(archive.read('manifest.json'))
        assert manifest['human_declarations_only'] and not manifest['model_quality_qualified']
        assert manifest['timeline']['subtitles'][0]['text']==cue['text']
        assert all('path' not in c and 'relative_path' not in c['source'] and 'stat' not in c['source'] for c in manifest['timeline']['clips'])
        for name,evidence in manifest['files'].items():assert hashlib.sha256(archive.read(name)).hexdigest()==evidence['sha256']


async def editing_fixture(tmp_path,monkeypatch):
    """Build real plan/schema/media with explicit imported, not fabricated generated, sources."""
    from tests.test_shot_plan_acceptance import setup,compile_plan
    from app.config import settings
    from app.services.editing.schema import migrate_editing
    from app.models.studio import FileItem
    from app.models.production import ProductionMedia
    monkeypatch.setattr(settings,'production_probe_executable',str(FFMPEG))
    engine,factory,data,versions,runtime,worker,adapter,clock=await setup(tmp_path,monkeypatch)
    await migrate_editing(engine)
    _,plan=await compile_plan(factory,data,runtime)
    bank=generate_media_bank(tmp_path/'source-bank')
    async with factory() as db:
        for name,evidence in bank.items():
            path=Path(evidence['path']);identity='editing-'+name
            db.add(FileItem(id=identity,type='video',name=path.name,storage_key=str(path.relative_to(tmp_path))))
            await db.flush()
            db.add(ProductionMedia(id=identity,project_id='a',name=path.name,mime='video/mp4',sha256=evidence['sha256'],size=path.stat().st_size,relative_path=path.relative_to(tmp_path).as_posix()))
        await db.commit()
    return engine,factory,plan,bank,runtime,worker,adapter,clock


async def selected_timeline(factory,plan):
    """Select two owned actual MP4s and build complete manual retention declarations."""
    from app.core.contracts.editing import SelectionWrite
    from app.services.editing.service import write_selection
    selections=[]
    for shot,media in [('shot-a1','editing-red-tone'),('shot-a1b','editing-green-silent')]:
        selections.append(await write_selection(factory,'a',SelectionWrite(request_key='select-'+shot,shot_id=shot,
            expected_revision=0,media_id=media,plan_id=plan,change_note='Explicit synthetic software import, not AI')))
    first,second=[s['take_id'] for s in selections]
    doc={'schema_version':1,'plan_id':plan,'title':'Software two-clip assembly',
         'clips':[{'shot_id':'shot-a1','take_id':first,'source_in_ms':0,'source_out_ms':2000,'source_audio':True},
                  {'shot_id':'shot-a1b','take_id':second,'source_in_ms':0,'source_out_ms':2000,'source_audio':False}],
         'anchors':[{'kind':'beat','item_id':phase,'take_id':first,'start_ms':100,'end_ms':400,'confirmed':True} for phase in ['conflict','development','resolution']]+
                   [{'kind':'dialogue','item_id':'line-a','take_id':first,'start_ms':500,'end_ms':900,'confirmed':True}]}
    return selections,doc


@pytest.mark.asyncio
async def test_actual_selection_concurrency_replay_does_not_reselect_and_no_orphan_take(tmp_path,monkeypatch):
    """Two CAS intentions race; historical same-key recovery never resets current choice."""
    from fastapi import HTTPException
    from sqlalchemy import select,func
    from app.core.contracts.editing import SelectionWrite
    from app.models.production import ProductionTake
    from app.services.editing.service import write_selection,selection_history
    engine,factory,plan,*_=await editing_fixture(tmp_path,monkeypatch)
    try:
        bodies=[SelectionWrite(request_key='race-'+media,shot_id='shot-a1',expected_revision=0,media_id='editing-'+media,
                              plan_id=plan,change_note='Explicit manual software candidate') for media in ['red-tone','blue-tone']]
        results=await asyncio.gather(*(write_selection(factory,'a',b) for b in bodies),return_exceptions=True)
        accepted=[r for r in results if isinstance(r,dict)];refused=[r for r in results if isinstance(r,HTTPException)]
        assert len(accepted)==1 and len(refused)==1 and refused[0].status_code==409
        winner=next(body for body,result in zip(bodies,results) if isinstance(result,dict))
        async with factory() as db:assert await db.scalar(select(func.count()).select_from(ProductionTake))==1
        later=await write_selection(factory,'a',SelectionWrite(request_key='later-blue',shot_id='shot-a1',expected_revision=1,
            media_id='editing-blue-tone',plan_id=plan,change_note='Explicit later selection'))
        replay=await write_selection(factory,'a',winner)
        assert replay['take_id']==accepted[0]['take_id'] and replay['replayed']
        async with factory() as db:
            current=await selection_history(db,'a','shot-a1')
            assert current['current_take_id']==later['take_id'] and current['revision']==2
        back=await write_selection(factory,'a',SelectionWrite(request_key='explicit-back',shot_id='shot-a1',expected_revision=2,take_id=accepted[0]['take_id']))
        assert back['take_id']==accepted[0]['take_id'] and back['revision']==3
    finally:await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['missing_middle','missing_anchor','unconfirmed','cropped_dialogue','mute_dialogue','duplicate_anchor'])
async def test_timeline_required_sets_and_real_audio_cannot_be_bypassed(tmp_path,monkeypatch,failure):
    """All declared source requirements survive editing and must have real source coverage."""
    from fastapi import HTTPException
    from app.core.contracts.editing import EditDraftCreate,EditFreezeWrite
    from app.services.editing.service import create_draft,freeze_draft,draft_view
    engine,factory,plan,*_=await editing_fixture(tmp_path,monkeypatch)
    try:
        _,doc=await selected_timeline(factory,plan)
        if failure=='missing_middle':doc['clips'].pop()
        if failure=='missing_anchor':doc['anchors'].pop()
        if failure=='unconfirmed':doc['anchors'][-1]['confirmed']=False
        if failure=='cropped_dialogue':doc['clips'][0]['source_out_ms']=700
        if failure=='mute_dialogue':doc['clips'][0]['source_audio']=False
        if failure=='duplicate_anchor':doc['anchors'].append(deepcopy(doc['anchors'][-1]))
        if failure=='duplicate_anchor':
            with pytest.raises(HTTPException) as error:
                await create_draft(factory,'a',EditDraftCreate(chapter_id='chapter-a1',document=doc))
            assert error.value.status_code==422
            return
        identity=await create_draft(factory,'a',EditDraftCreate(chapter_id='chapter-a1',document=doc))
        async with factory() as db:assert not (await draft_view(db,'a',identity))['readiness']['can_freeze']
        with pytest.raises(HTTPException) as error:
            await freeze_draft(factory,'a',identity,EditFreezeWrite(expected_revision=1,acknowledge_changes=True,change_note='Cannot acknowledge missing content away'))
        assert error.value.status_code in {409,422}
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_frozen_timeline_keeps_source_text_history_and_rejects_later_corruption(tmp_path,monkeypatch):
    """Current choice changes do not rewrite historical publication; broken bytes still fail."""
    from fastapi import HTTPException
    from app.core.contracts.editing import EditDraftCreate,EditFreezeWrite,SelectionWrite
    from app.services.editing.service import create_draft,freeze_draft,publication_view,preflight_publication,write_selection
    engine,factory,plan,bank,*_=await editing_fixture(tmp_path,monkeypatch)
    try:
        selections,doc=await selected_timeline(factory,plan)
        identity=await create_draft(factory,'a',EditDraftCreate(chapter_id='chapter-a1',document=doc))
        body=EditFreezeWrite(expected_revision=1,acknowledge_changes=True,change_note='Explicit 4s synthetic crop from 10s manual plan')
        publication=await freeze_draft(factory,'a',identity,body)
        assert await freeze_draft(factory,'a',identity,body)==publication
        async with factory() as db:
            old=await publication_view(db,'a',publication)
            assert old['payload']['duration_ms']==4000 and old['payload']['human_declarations_only']
            assert old['payload']['subtitles'][0]['text']=='Manual required line'
            assert old['payload']['subtitles'][0]['speaker_id']=='shared-hero'
        await write_selection(factory,'a',SelectionWrite(request_key='switch-after-freeze',shot_id='shot-a1',expected_revision=1,
            media_id='editing-blue-tone',plan_id=plan,change_note='Later candidate, historical timeline remains red'))
        async with factory() as db:
            assert await publication_view(db,'a',publication)==old
            state=await preflight_publication(db,'a',publication)
            assert state['usable'] and state['current_selection_differences']
        Path(bank['red-tone']['path']).write_bytes(b'corrupt only this isolated test source')
        async with factory() as db:
            state=await preflight_publication(db,'a',publication)
            assert not state['usable']
    finally:await engine.dispose()



async def export_fixture(tmp_path,monkeypatch):
    """Create a frozen complete real-media timeline and additive export journal."""
    from app.config import settings
    from app.services.editing.export_schema import migrate_editing_export
    from app.core.contracts.editing import EditDraftCreate,EditFreezeWrite
    from app.services.editing.service import create_draft,freeze_draft
    engine,factory,plan,bank,*_=await editing_fixture(tmp_path,monkeypatch)
    await migrate_editing_export(engine)
    monkeypatch.setattr(settings,'editing_export_enabled',True)
    _,doc=await selected_timeline(factory,plan)
    draft=await create_draft(factory,'a',EditDraftCreate(chapter_id='chapter-a1',document=doc))
    publication=await freeze_draft(factory,'a',draft,EditFreezeWrite(expected_revision=1,acknowledge_changes=True,change_note='Independent software crop'))
    return engine,factory,publication,bank


@pytest.mark.asyncio
async def test_export_global_slot_restart_and_expired_token_fencing(tmp_path,monkeypatch):
    """Two actual SQLite workers cannot own one slot; restart replaces expired authority."""
    from fastapi import HTTPException
    from sqlalchemy import select,func
    from app.models.editing_export import EditExportProduct,EditExportJob
    from app.services.editing.exports import LocalExportWorker,create_export,get_export
    engine,factory,publication,bank=await export_fixture(tmp_path,monkeypatch)
    try:
        job=await create_export(factory,'a',publication,'first-export',str(FFMPEG))
        now=[1000.0]
        first=LocalExportWorker(factory,str(FFMPEG),clock=lambda:now[0])
        second=LocalExportWorker(factory,str(FFMPEG),clock=lambda:now[0])
        claims=await asyncio.gather(first.claim(),second.claim())
        claimed=[c for c in claims if c is not None]
        assert len(claimed)==1 and claimed[0][0]==job['id']
        old_id,old_token=claimed[0]
        now[0]+=181
        recovered=await second.claim()
        assert recovered[0]==old_id and recovered[1]!=old_token
        async with factory() as db:
            row=await db.get(EditExportJob,old_id)
            assert row.attempts==2
            with pytest.raises(HTTPException) as error:await first._fenced(db,old_id,old_token)
            assert error.value.status_code==409
        await first.finish_failure(old_id,old_token,'stale worker must not fail replacement')
        assert (await get_export(factory,'a',old_id))['state']=='running'
        async with factory() as db:assert await db.scalar(select(func.count()).select_from(EditExportProduct))==0
        await second.finish_failure(*recovered,'Explicit test interrupted attempt',requeue=True)
        assert (await get_export(factory,'a',old_id))['state']=='queued'
        third=LocalExportWorker(factory,str(FFMPEG),clock=lambda:now[0])
        assert await third.step()
        ready=await get_export(factory,'a',old_id)
        assert ready['state']=='ready' and len(ready['files'])==4 and ready['attempts']==3
        assert await create_export(factory,'a',publication,'first-export','')==ready
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_export_corrupt_queued_source_never_publishes_and_ready_file_tamper_is_refused(tmp_path,monkeypatch):
    """Historical ready receipts remain immutable; missing output is never a fake success."""
    from fastapi import HTTPException
    from app.services.editing.exports import LocalExportWorker,create_export,get_export,content_file
    engine,factory,publication,bank=await export_fixture(tmp_path,monkeypatch)
    try:
        first=await create_export(factory,'a',publication,'ready-before-tamper',str(FFMPEG))
        worker=LocalExportWorker(factory,str(FFMPEG))
        assert await worker.step()
        assert (await get_export(factory,'a',first['id']))['state']=='ready'
        path,*_=await content_file(factory,'a',first['id'],'video')
        second=await create_export(factory,'a',publication,'queued-before-source-tamper',str(FFMPEG))
        Path(bank['red-tone']['path']).write_bytes(b'isolated input corruption')
        assert await LocalExportWorker(factory,str(FFMPEG)).step()
        failed=await get_export(factory,'a',second['id'])
        assert failed['state']=='failed' and failed['files']==[]
        assert (await get_export(factory,'a',first['id']))['state']=='ready'
        path.write_bytes(b'isolated output corruption')
        with pytest.raises(HTTPException) as error:await content_file(factory,'a',first['id'],'video')
        assert error.value.status_code==409
        with pytest.raises(HTTPException) as error:await get_export(factory,'a',first['id'])
        assert error.value.status_code==409
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_export_commit_acknowledgement_loss_preserves_committed_files(tmp_path,monkeypatch):
    """A real committed ready transaction with lost response cannot delete the committed product."""
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy import select
    from app.models.editing_export import EditExportJob,EditExportProduct
    from app.services.editing.exports import LocalExportWorker,create_export,get_export,content_file
    engine,factory,publication,bank=await export_fixture(tmp_path,monkeypatch)
    try:
        job=await create_export(factory,'a',publication,'commit-response-loss',str(FFMPEG))
        original=AsyncSession.commit
        injected=[]
        async def commit_then_lose_response(db):
            """Inject only after the real publication commit, never before its durability."""
            publishing=any(isinstance(x,EditExportJob) and x.state=='ready' for x in db.dirty)
            await original(db)
            if publishing and not injected:
                injected.append(True)
                raise ConnectionError('Independent simulated acknowledgement loss after actual commit')
        monkeypatch.setattr(AsyncSession,'commit',commit_then_lose_response)
        assert await LocalExportWorker(factory,str(FFMPEG)).step()
        assert injected==[True]
        ready=await get_export(factory,'a',job['id'])
        assert ready['state']=='ready' and len(ready['files'])==4
        for item in ready['files']:
            path,*_=await content_file(factory,'a',job['id'],item['kind'])
            assert path.is_file() and digest(path)==item['sha256']
        async with factory() as db:
            product=await db.scalar(select(EditExportProduct).where(EditExportProduct.job_id==job['id']))
            assert product is not None
        assert await create_export(factory,'a',publication,'commit-response-loss','')==ready
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_actual_private_helper_process_tree_is_cancelled_and_joined(tmp_path):
    """Exercise real OS parent/child termination independently of model or fake renderer calls."""
    import os,sys,time,ctypes
    from app.services.editing.media import kill_child
    pidfile=tmp_path/'grandchild.pid'
    code="import subprocess,sys,time,pathlib; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); pathlib.Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)"
    process=await asyncio.create_subprocess_exec(sys.executable,'-c',code,str(pidfile),start_new_session=os.name!='nt')
    try:
        for _ in range(100):
            if pidfile.exists():break
            await asyncio.sleep(.02)
        assert pidfile.exists()
        childpid=int(pidfile.read_text())
        if os.name=='nt':
            kernel=ctypes.windll.kernel32
            kernel.OpenProcess.restype=ctypes.c_void_p
            handle=kernel.OpenProcess(0x100000,False,childpid)
            assert handle
        start=time.monotonic()
        await kill_child(process)
        assert time.monotonic()-start<=5 and process.returncode is not None
        if os.name=='nt':
            assert kernel.WaitForSingleObject(ctypes.c_void_p(handle),0)==0
            kernel.CloseHandle(ctypes.c_void_p(handle))
    finally:
        if process.returncode is None:await kill_child(process)


@pytest.mark.asyncio
@pytest.mark.parametrize('source,anchor_range',[('positive-audio',(0,200)),('audio-gap',(700,900)),('green-silent',(100,300))])
async def test_dialogue_anchor_requires_actual_samples_not_silence_or_minmax(tmp_path,monkeypatch,source,anchor_range):
    """Leading inserted silence, internal sample gaps and no-track imports cannot cover dialogue."""
    from app.core.contracts.editing import SelectionWrite,EditDraftCreate
    from app.services.editing.service import write_selection,create_draft,draft_view
    engine,factory,plan,*_=await editing_fixture(tmp_path,monkeypatch)
    try:
        _,doc=await selected_timeline(factory,plan)
        chosen=await write_selection(factory,'a',SelectionWrite(request_key='offset-or-gap',shot_id='shot-a1',expected_revision=1,
            media_id='editing-'+source,plan_id=plan,change_note='Negative sample coverage fixture'))
        doc['clips'][0]['take_id']=chosen['take_id']
        for anchor in doc['anchors']:anchor['take_id']=chosen['take_id']
        doc['anchors'][-1]['start_ms'],doc['anchors'][-1]['end_ms']=anchor_range
        draft=await create_draft(factory,'a',EditDraftCreate(chapter_id='chapter-a1',document=doc))
        async with factory() as db:
            view=await draft_view(db,'a',draft)
            assert not view['readiness']['can_freeze']
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_editing_scope_cas_and_immutable_publication_cannot_be_relabelled(tmp_path,monkeypatch):
    """Actual database scope and immutable history reject cross chapter/project and stale writes."""
    from fastapi import HTTPException
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    from app.core.contracts.editing import SelectionWrite,EditDraftCreate,EditDraftPatch,EditFreezeWrite
    from app.services.editing.service import write_selection,create_draft,patch_draft,freeze_draft,publication_view
    engine,factory,plan,*_=await editing_fixture(tmp_path,monkeypatch)
    try:
        selections,doc=await selected_timeline(factory,plan)
        for project,shot,status in [('a','shot-a2',422),('a','shot-b',404),('b','shot-a1',404)]:
            with pytest.raises(HTTPException) as error:
                await write_selection(factory,project,SelectionWrite(request_key='wrong-'+project+shot,shot_id=shot,expected_revision=0,
                    take_id=selections[0]['take_id']))
            assert error.value.status_code==status
        with pytest.raises(HTTPException) as error:
            await create_draft(factory,'a',EditDraftCreate(chapter_id='chapter-a2',document=doc))
        assert error.value.status_code==422
        draft=await create_draft(factory,'a',EditDraftCreate(chapter_id='chapter-a1',document=doc))
        await patch_draft(factory,'a',draft,EditDraftPatch(expected_revision=1,document=doc))
        with pytest.raises(HTTPException) as error:
            await patch_draft(factory,'a',draft,EditDraftPatch(expected_revision=1,document=doc))
        assert error.value.status_code==409
        publication=await freeze_draft(factory,'a',draft,EditFreezeWrite(expected_revision=2,acknowledge_changes=True,change_note='Explicit crop'))
        async with factory() as db:
            with pytest.raises(HTTPException) as error:await publication_view(db,'b',publication)
            assert error.value.status_code==404
        async with factory() as db:
            with pytest.raises(IntegrityError):
                await db.execute(text("UPDATE editing_publications SET content_hash='tampered' WHERE id=:id"),{'id':publication})
                await db.commit()
            await db.rollback()
        async with factory() as db:
            with pytest.raises(IntegrityError):
                await db.execute(text('DELETE FROM editing_selection_events WHERE project_id=:id'),{'id':'a'})
                await db.commit()
            await db.rollback()
    finally:await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('name,silent_ranges,loud_ranges',[
    ('positive-audio',[(.05,.20)],[(.45,1.6)]),
    ('negative-audio',[],[(.10,1.4)]),
    ('audio-gap',[(.78,1.05)],[(.12,.4),(1.45,1.8)])])
async def test_actual_audio_render_preserves_video_zero_offsets_and_internal_silence(tmp_path,name,silent_ranges,loud_ranges):
    """Decode real output audio to prove offsets/gaps remain at the authored source positions."""
    bank=generate_media_bank(tmp_path/'offset-bank');source=bank[name]
    facts=await run_child({'mode':'probe','path':source['path'],'expected_hash':source['sha256']})
    clip={'shot_id':'offset-shot','take_id':'offset-take','origin':{'kind':'explicit_software_fixture'},
          'source':{'id':'offset-media','sha256':source['sha256'],**facts},'path':source['path'],
          'source_in_ms':0,'source_out_ms':2000,'source_audio':True,'timeline_start_ms':0,'timeline_end_ms':2000}
    folder=tmp_path/'offset-output';folder.mkdir()
    await run_child({'mode':'render','folder':str(folder),'ffmpeg':str(FFMPEG),
        'payload':{'clips':[clip],'subtitles':[],'duration_ms':2000,'profile':'portrait-360p30-v1'},
        'publication_id':'independent-offset-fixture','publication_hash':'0'*64},deadline=120)
    chunks=[]
    with av.open(str(folder/'video.mp4')) as container:
        for frame in container.decode(audio=0):
            assert frame.sample_rate==48000 and len(frame.layout.channels)==2
            chunks.append((float(Fraction(frame.pts)*frame.time_base),float(np.sqrt(np.mean(frame.to_ndarray().astype(np.float64)**2)))))
    for start,end in silent_ranges:
        values=[v for t,v in chunks if start<t<end]
        assert values and max(values)<.001,(name,start,end,values)
    for start,end in loud_ranges:
        values=[v for t,v in chunks if start<t<end]
        assert values and np.mean(values)>.02,(name,start,end,values)
    assert digest(source['path'])==source['sha256']


@pytest.mark.asyncio
@pytest.mark.parametrize('shutdown',[False,True])
async def test_running_export_cancel_or_shutdown_joins_real_private_process(tmp_path,monkeypatch,shutdown):
    """Worker monitoring/stop cancels an actually running OS helper, leaving recoverable durable state."""
    import sys,time
    from app.services.editing import exports
    from app.services.editing.media import kill_child
    engine,factory,publication,bank=await export_fixture(tmp_path,monkeypatch)
    started=asyncio.Event();processes=[]
    async def stalled_real_helper(parameters,deadline):
        """Replace rendering workload only with a real slow process; retain real cancellation cleanup."""
        process=await asyncio.create_subprocess_exec(sys.executable,'-c','import time; time.sleep(60)')
        processes.append(process);started.set()
        try:await process.wait()
        finally:await kill_child(process)
    monkeypatch.setattr(exports,'run_child',stalled_real_helper)
    worker=exports.LocalExportWorker(factory,str(FFMPEG))
    try:
        job=await exports.create_export(factory,'a',publication,'cancel-or-shutdown',str(FFMPEG))
        await worker.start()
        await asyncio.wait_for(started.wait(),10)
        start=time.monotonic()
        if shutdown:
            await worker.stop()
        else:
            from fastapi import FastAPI
            import httpx
            from app.api.v1.routes.film import task_status
            from app.dependencies import get_db
            async def owned_session():
                async with factory() as db:yield db
            app=FastAPI();app.include_router(task_status.router);app.dependency_overrides[get_db]=owned_session
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                response=await client.post('/tasks/'+job['task_id']+'/cancel',json={'reason':'Independent live helper cancellation'})
                assert response.status_code==200,response.text
                from app.models.task import GenerationTask
                async with factory() as db:
                    db.add(GenerationTask(id='orphan-local-export',mode='async_polling',task_kind='local_export',status='pending',progress=0,payload={'project_id':'a'},executor_type='local_editing'))
                    await db.commit()
                orphan=await client.post('/tasks/orphan-local-export/cancel',json={'reason':'Missing owner cannot fake cancellation'})
                assert orphan.status_code==409,orphan.text
            for _ in range(100):
                if (await exports.get_export(factory,'a',job['id']))['state']=='cancelled':break
                await asyncio.sleep(.025)
        assert time.monotonic()-start<=5
        assert processes[0].returncode is not None
        state=await exports.get_export(factory,'a',job['id'])
        assert state['state']==('queued' if shutdown else 'cancelled') and state['files']==[]
    finally:
        await worker.stop()
        for process in processes:
            if process.returncode is None:await kill_child(process)
        await engine.dispose()


@pytest.mark.asyncio
async def test_actual_worker_candidate_adoption_derives_provenance_and_never_bills_again(tmp_path,monkeypatch):
    """A fixture provider's actual MP4 travels through real artifact publication before explicit adoption."""
    from fastapi import HTTPException
    from sqlalchemy import select,func
    from app.models.editing import TakeOrigin
    from app.models.production import ProductionTake
    from app.models.execution import ExecutionJob,SubmissionAttempt
    from app.models.execution_budget import ExecutionBudgetEntry
    from app.services.shot_plan.service import admit_planned_shot
    from app.services.worker.execution_artifacts import DownloadedArtifact,publish_artifact
    from app.core.contracts.editing import SelectionWrite
    from app.services.editing.service import write_selection
    engine,factory,plan,bank,runtime,worker,adapter,clock=await editing_fixture(tmp_path,monkeypatch)
    try:
        async def actual_download(result,media_id,project_id):
            """Local transport seam returns known authored bytes, never a model qualification."""
            return DownloadedArtifact('software-candidate.mp4',Path(bank['red-tone']['path']).read_bytes())
        monkeypatch.setattr(adapter,'download',actual_download)
        worker.publisher=publish_artifact
        job=await admit_planned_shot(factory,'a',plan,'shot-a1','actual-ready-candidate',runtime=runtime)
        await worker.step(job);clock.advance();await worker.step(job);await worker.step(job)
        async with factory() as db:
            row=await db.get(ExecutionJob,job)
            assert row.state=='ready' and adapter.sent==1
            assert await db.scalar(select(func.count()).select_from(ProductionTake))==0
            before=(await db.scalar(select(func.count()).select_from(SubmissionAttempt)),
                    (await db.get(ExecutionBudgetEntry,job)).state)
        with pytest.raises(HTTPException) as error:
            await write_selection(factory,'a',SelectionWrite(request_key='wrong-candidate-shot',shot_id='shot-a1b',expected_revision=0,job_id=job))
        assert error.value.status_code==422
        body=SelectionWrite(request_key='explicit-candidate',shot_id='shot-a1',expected_revision=0,job_id=job)
        chosen=await write_selection(factory,'a',body)
        assert chosen['origin_kind']=='execution_candidate_unqualified'
        replay=await write_selection(factory,'a',body)
        assert replay['take_id']==chosen['take_id'] and replay['replayed']
        async with factory() as db:
            origin=await db.get(TakeOrigin,chosen['take_id'])
            assert origin.job_id==job and origin.plan_id==plan and origin.evidence['sha256']==bank['red-tone']['sha256']
            assert await db.scalar(select(func.count()).select_from(ProductionTake))==1
            after=(await db.scalar(select(func.count()).select_from(SubmissionAttempt)),
                   (await db.get(ExecutionBudgetEntry,job)).state)
            assert after==before and adapter.sent==1
    finally:await engine.dispose()



@pytest.mark.asyncio
@pytest.mark.parametrize('legacy',[False,True])
async def test_independent_history_context_does_not_upgrade_take_provenance(tmp_path,monkeypatch,legacy):
    """A viewing plan can accompany rollback without rewriting historical source qualifications."""
    from fastapi import HTTPException
    from app.models.production import ProductionTake,TakeVersion
    from app.models.editing import TakeOrigin
    from app.models.shot_plan import ShotPlan
    from app.core.contracts.shot_plan import PlanPatch,PlanCompile
    from app.services.shot_plan.service import patch_draft as patch_plan,compile_draft
    from app.core.contracts.editing import SelectionWrite,EditDraftCreate,EditFreezeWrite
    from app.services.editing.service import write_selection,create_draft,draft_view,freeze_draft
    engine,factory,plan,bank,runtime,*_=await editing_fixture(tmp_path,monkeypatch)
    try:
        chosen,doc=await selected_timeline(factory,plan)
        take=chosen[0]['take_id']
        async with factory() as db:
            source=await db.get(ProductionTake,take)
            if legacy:
                take='independent-no-origin'
                db.add(ProductionTake(id=take,project_id='a',shot_id='shot-a1',media_id=source.media_id,provenance='user_import',version_ids=list(source.version_ids)))
                await db.flush()
                for version in source.version_ids:db.add(TakeVersion(take_id=take,version_id=version,project_id='a'))
                await db.commit()
            original=await db.get(ShotPlan,plan)
            draftid=original.draft_id;document=deepcopy(original.payload['document'])
        await patch_plan(factory,'a',draftid,PlanPatch(expected_revision=1,document=document))
        current=await compile_draft(factory,'a',draftid,PlanCompile(expected_revision=2),runtime=runtime)
        switched=await write_selection(factory,'a',SelectionWrite(request_key='independent-rollback',shot_id='shot-a1',expected_revision=1,take_id=take,plan_id=current))
        assert switched['revision']==2 and switched['take_id']==take
        async with factory() as db:
            origin=await db.get(TakeOrigin,take)
            assert (origin is None) if legacy else origin.plan_id==plan
        doc['plan_id']=current;doc['clips'][0]['take_id']=take
        for anchor in doc['anchors']:anchor['take_id']=take
        draft=await create_draft(factory,'a',EditDraftCreate(chapter_id='chapter-a1',document=doc))
        async with factory() as db:assert not (await draft_view(db,'a',draft))['readiness']['can_freeze']
        with pytest.raises(HTTPException):await freeze_draft(factory,'a',draft,EditFreezeWrite(expected_revision=1,acknowledge_changes=True,change_note='Acknowledgement cannot upgrade origin'))
    finally:await engine.dispose()



@pytest.mark.asyncio
async def test_publication_lease_expiring_after_product_flush_cannot_commit_ready(tmp_path,monkeypatch):
    """Authority must remain live through the final publish boundary, not merely its first read."""
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy import select,func
    from app.models.editing_export import EditExportProduct
    from app.services.editing.exports import create_export,LocalExportWorker,get_export
    engine,factory,publication,*_=await export_fixture(tmp_path,monkeypatch)
    now=[1000.0];expired=[];original=AsyncSession.flush
    try:
        from app.models.production import ProductionMedia
        from app.config import settings
        async with factory() as db:media_before=await db.scalar(select(func.count()).select_from(ProductionMedia))
        job=await create_export(factory,'a',publication,'expiry-at-publication',str(FFMPEG))
        async def flush_then_expire(db,*args,**kwargs):
            """Advance injected clock only after a real product insert, before commit."""
            publishing=any(isinstance(row,EditExportProduct) for row in db.new)
            await original(db,*args,**kwargs)
            if publishing and not expired:now[0]+=181;expired.append(True)
        monkeypatch.setattr(AsyncSession,'flush',flush_then_expire)
        assert await LocalExportWorker(factory,str(FFMPEG),clock=lambda:now[0]).step()
        assert expired==[True]
        state=await get_export(factory,'a',job['id'])
        assert state['state']!='ready',state
        async with factory() as db:assert await db.scalar(select(func.count()).select_from(EditExportProduct))==0
    finally:await engine.dispose()
