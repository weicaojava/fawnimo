"""Actual PCM exports prove explicit loop duration and confirmed-dialogue attenuation without any model calls."""
import array
import copy
import hashlib
import io
import math
import struct
import sys
import wave
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from app.config import settings
from app.core.contracts.sound_timeline import SoundTrack,SoundCandidateImport,SoundCreate,SoundPatch,SoundFreeze
from app.services.editing import sound_timeline as service,sound_exports
from app.services.studio.production_media import store_batch
from tests.test_sound_timeline import fixture


def music_wave(rate=48000,channels=2):
    """Author200ms of220Hz PCM, distinct from the400Hz original spoken-track test signal."""
    values=[round(4000*math.sin(2*math.pi*220*index/rate)) for index in range(rate//5)]
    stream=io.BytesIO()
    with wave.open(stream,'wb') as target:
        target.setnchannels(channels);target.setsampwidth(2);target.setframerate(rate)
        target.writeframes(struct.pack('<'+'h'*(len(values)*channels),*[value for value in values for _ in range(channels)]))
    return stream.getvalue()


async def with_music(tmp_path,monkeypatch,rate=48000,channels=2):
    """Register actual new project media as an independent music candidate; no voice/source history is repurposed."""
    engine,factory,sound,voice,body=await fixture(tmp_path,monkeypatch)
    blob=music_wave(rate,channels)
    async with factory() as db:row=(await store_batch(db,'a',[('authored-music.wav',blob)]))[0]
    candidate=await service.import_candidate(factory,'a',SoundCandidateImport(request_key='music-new',chapter_id='c1',
        media_id=row.id,expected_sha256=hashlib.sha256(blob).hexdigest(),kind='music',name='原创短音乐'))
    sound['tracks'].append({'id':'music-1','candidate_id':candidate['id'],'source_in_ms':0,'source_out_ms':200,
        'timeline_start_ms':0,'gain_db':-6,'muted':False,'music':{'loop_until_ms':1400}})
    return engine,factory,sound,candidate


@pytest.mark.parametrize('options',[{}, {'loop_until_ms':True},{'loop_until_ms':11},{'loop_until_ms':300010},
    {'loop_until_ms':'1400'},{'voice_duck_db':True},{'voice_duck_db':-2},{'voice_duck_db':-31},
    {'voice_duck_db':-12.5},{'voice_duck_db':-12,'expression':'arbitrary'},
    {'loop_until_ms':None,'voice_duck_db':None},{'loop_until_ms':200}])
def test_reject_unbounded_or_implicit_music_operations(options):
    """The new field cannot admit fake strict values, no-op loops, arbitrary filter strings or hidden operations."""
    track={'id':'music','candidate_id':'candidate','source_in_ms':0,'source_out_ms':200,'timeline_start_ms':0,'music':options}
    with pytest.raises(ValidationError):SoundTrack.model_validate(track)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['voice','effect'])
async def test_options_require_actual_music_candidate(tmp_path,monkeypatch,kind):
    """A client cannot turn a voice/effect into loopable music through document metadata."""
    engine,factory,sound,candidate=await with_music(tmp_path,monkeypatch)
    try:
        async with factory() as db:
            if kind=='voice':identifier=sound['tracks'][0]['candidate_id']
            else:
                voice=await service.owned_candidate(db,'a',sound['tracks'][0]['candidate_id'],'c1')
                row=await service.import_candidate(factory,'a',SoundCandidateImport(request_key='effect-new',chapter_id='c1',
                    media_id=voice.media_id,expected_sha256=voice.evidence['sha256'],kind='effect',name='原创效果'))
                identifier=row['id']
        sound['tracks'][1]['candidate_id']=identifier
        with pytest.raises(HTTPException) as failure:await service.create_draft(factory,'a',SoundCreate(chapter_id='c1',document=sound))
        assert failure.value.status_code==422
    finally:await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('change',[{'muted':True},{'gain_db':-19},{'content_confirmed':False}])
async def test_ducking_needs_confirmed_audible_original_dialogue(tmp_path,monkeypatch,change):
    """Unsupported voice coverage cannot silently stand in for an adopted and confirmed dialogue track."""
    engine,factory,sound,candidate=await with_music(tmp_path,monkeypatch)
    try:
        sound['tracks'][0].update(change);sound['tracks'][1]['music']['voice_duck_db']=-12
        identifier=await service.create_draft(factory,'a',SoundCreate(chapter_id='c1',document=sound))
        async with factory() as db:view=await service.draft_view(db,'a',identifier)
        assert not view['can_freeze'] and 'music_voice_missing' in {item['code'] for item in view['issues']}
        with pytest.raises(HTTPException):await service.freeze_draft(factory,'a',identifier,SoundFreeze(expected_revision=1,acknowledge_changes=True))
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_muted_music_does_not_require_an_unused_ducking_voice(tmp_path,monkeypatch):
    """Muting music retains its saved choices while removing the active ducking requirement; original dialogue rules remain."""
    engine,factory,sound,candidate=await with_music(tmp_path,monkeypatch)
    try:
        sound['tracks'][0]['muted']=True
        sound['tracks'][1].update(muted=True,music={'loop_until_ms':1400,'voice_duck_db':-12})
        identifier=await service.create_draft(factory,'a',SoundCreate(chapter_id='c1',document=sound))
        async with factory() as db:view=await service.draft_view(db,'a',identifier)
        codes={item['code'] for item in view['issues']}
        assert 'voice_inaudible' in codes and 'music_voice_missing' not in codes
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_music_loop_cannot_extend_the_picture_clock(tmp_path,monkeypatch):
    """A valid longer integer loop target remains unfreezable when it exceeds actual output coverage."""
    engine,factory,sound,candidate=await with_music(tmp_path,monkeypatch)
    try:
        sound['tracks'][1]['music']['loop_until_ms']=1500
        identifier=await service.create_draft(factory,'a',SoundCreate(chapter_id='c1',document=sound))
        async with factory() as db:view=await service.draft_view(db,'a',identifier)
        assert view['duration_ms']==1400 and not view['can_freeze']
        assert 'sound_outside' in {item['code'] for item in view['issues']}
    finally:await engine.dispose()


async def render(factory,publication,key):
    """Use the actual durable worker and owned encoder subprocess, then decode the independently downloadable mix."""
    job=await sound_exports.create_export(factory,'a',publication,key,settings.production_probe_executable)
    worker=sound_exports.SoundExportWorker(factory,settings.production_probe_executable)
    assert await worker.step()
    state=await sound_exports.get_export(factory,'a',job['id']);assert state['state']=='ready',state
    path,_,_=await sound_exports.content_file(factory,'a',job['id'],'mixdown')
    with wave.open(str(path),'rb') as source:
        assert (source.getframerate(),source.getnchannels(),source.getnframes())==(48000,2,67200)
        raw=source.readframes(source.getnframes())
    values=array.array('h',raw)
    if sys.byteorder!='little':values.byteswap()
    return job,path,values[::2]


def amplitude(samples,frequency,begin,end):
    """Measure the actual music frequency independently of the distinct adopted voice waveform."""
    first,last=round(begin*48000),round(end*48000)
    real=sum(samples[index]*math.cos(2*math.pi*frequency*index/48000) for index in range(first,last))
    imaginary=sum(samples[index]*math.sin(2*math.pi*frequency*index/48000) for index in range(first,last))
    return 2*math.hypot(real,imaginary)/(last-first)


@pytest.mark.asyncio
@pytest.mark.parametrize('rate,channels',[(24000,1),(48000,2)])
async def test_real_looped_music_ducking_samples_and_immutable_history(tmp_path,monkeypatch,rate,channels):
    """Actual paired PCM exports prove loop coverage,12dB attenuation,50/150ms ramps, unchanged voice and historical replay."""
    engine,factory,sound,candidate=await with_music(tmp_path,monkeypatch,rate,channels)
    try:
        monkeypatch.setattr(settings,'editing_export_enabled',True)
        identifier=await service.create_draft(factory,'a',SoundCreate(chapter_id='c1',document=sound))
        first=await service.freeze_draft(factory,'a',identifier,SoundFreeze(expected_revision=1,acknowledge_changes=True,change_note='明确循环原创音乐'))
        first_job,first_path,baseline=await render(factory,first,'loop-export')
        original_hash=hashlib.sha256(first_path.read_bytes()).hexdigest()
        async with factory() as db:first_view=await service.publication_view(db,'a',first)
        track=first_view['payload']['tracks'][1]
        assert track['timeline_end_ms']==1400 and track['source']['audio']['duration_us']==200000
        assert track['source_out_ms']==200 and track['music']['loop_until_ms']==1400
        assert first_view['payload']['music_mix']['voice_windows_ms']==[[100,600]]
        sound['tracks'][1]['music']['voice_duck_db']=-12
        await service.patch_draft(factory,'a',identifier,SoundPatch(expected_revision=1,document=sound))
        second=await service.freeze_draft(factory,'a',identifier,SoundFreeze(expected_revision=2,acknowledge_changes=True,change_note='明确配音对白避让'))
        second_job,second_path,ducked=await render(factory,second,'duck-export')
        factor=10**(-12/20)
        assert amplitude(ducked,220,.2,.4)/amplitude(baseline,220,.2,.4)==pytest.approx(factor,abs=.004)
        assert amplitude(ducked,400,.2,.4)==pytest.approx(amplitude(baseline,400,.2,.4),rel=.004)
        for at,weight in [(.026,0),(.076,(.076-.050)/.050),(.656,(.750-.656)/.150),(1.026,0),(1.326,0)]:
            index=round(at*48000);expected=baseline[index]*(1-(1-factor)*weight)
            assert abs(baseline[index])>100
            assert abs(ducked[index]-expected)<=5,(at,ducked[index],expected)
        assert amplitude(baseline,220,1.1,1.3)>1000
        assert hashlib.sha256(first_path.read_bytes()).hexdigest()==original_hash
        async with factory() as db:assert await service.publication_view(db,'a',first)==first_view
        assert (await sound_exports.create_export(factory,'a',first,'loop-export','missing-tool'))['id']==first_job['id']
        with pytest.raises(HTTPException):await sound_exports.get_export(factory,'b',second_job['id'])
        async with factory() as db:
            with pytest.raises(HTTPException):await service.publication_view(db,'b',second)
    finally:await engine.dispose()
