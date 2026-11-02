"""Actual zero-model PCM/video assembly, original-dialogue retention and durable sound exports."""
import copy
import asyncio
import hashlib
import io
import json
import math
from pathlib import Path
import struct
import wave
import zipfile
import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import select, text, func
from sqlalchemy.exc import IntegrityError
from app.config import settings
from app.core.contracts.sound_timeline import SoundCandidateImport, SoundCreate, SoundPatch, SoundFreeze, SoundDocument
from app.core.contracts.editing import EditDraftCreate, EditFreezeWrite
from app.models.editing import EditPublication
from app.models.sound_timeline import SoundCandidate, SoundPublication, SoundExportJob
from app.services.editing import sound_timeline as service, sound_exports
from app.services.editing.sound_schema import migrate_sound
from app.services.editing.sound_wave import inspect_wave
from app.services.editing.sound_process import child_environment
from app.services.studio.production_media import store_batch
from tests.test_editing import fixture as picture_fixture, intent, document
from app.services.editing.service import write_selection, create_draft, freeze_draft


def wav_bytes(rate=48000, channels=1, frames=None, silence=False, signal_after_ms=0):
    """Author PCM sample truth directly; no model or downloaded media is involved."""
    frames = frames if frames is not None else rate
    samples = [0 if silence or index * 1000 < signal_after_ms * rate else round(6000 * math.sin(2 * math.pi * 400 * index / rate)) for index in range(frames)]
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as target:
        target.setnchannels(channels); target.setsampwidth(2); target.setframerate(rate)
        target.writeframes(struct.pack('<'+'h'*(frames * channels), *[value for value in samples for _ in range(channels)]))
    return stream.getvalue()


async def fixture(tmp_path, monkeypatch, silence=False, signal_late=False):
    """Use actual registered plans, silent video and a separately imported voiced candidate."""
    engine, factory, plan, video = await picture_fixture(tmp_path, monkeypatch, audio=False)
    await migrate_sound(engine)
    adopted = await write_selection(factory, 'a', intent(plan, video))
    picture = document(plan, adopted['take_id'], audio=False)
    blob = wav_bytes(silence=silence, signal_after_ms=800 if signal_late else 0)
    async with factory() as db: media = (await store_batch(db, 'a', [('manual-voice.wav', blob)]))[0]
    body = SoundCandidateImport(request_key='voice-import', chapter_id='c1', media_id=media.id,
        expected_sha256=hashlib.sha256(blob).hexdigest(), kind='voice', name='人工原创音频')
    candidate = await service.import_candidate(factory, 'a', body)
    sound = {'schema_version': 3, 'title': '独立声音版本', 'picture': picture, 'edges': [], 'tracks': [
        {'id': 'voice-1', 'candidate_id': candidate['id'], 'source_in_ms': 100, 'source_out_ms': 600,
            'timeline_start_ms': 100, 'gain_db': -6, 'dialogue_id': 'line', 'content_confirmed': True}]}
    return engine, factory, sound, candidate, body


@pytest.mark.parametrize('rate,channels', [(24000, 1), (24000, 2), (48000, 1), (48000, 2)])
def test_actual_wave_samples_and_intervals(tmp_path, rate, channels):
    """The admitted clock comes from all actual PCM bytes, including exact interval peaks."""
    blob = wav_bytes(rate, channels); path = tmp_path/'actual.wav'; path.write_bytes(blob)
    facts = inspect_wave(path, hashlib.sha256(blob).hexdigest(), [(100, 600)])
    assert facts['sample_frames'] == rate and facts['channels'] == channels
    assert facts['duration_us'] == 1000000 and facts['coverage_us'] == [[0, 1000000]]
    assert facts['peak_pcm16'] == facts['interval_peaks_pcm16'][0] == 6000


@pytest.mark.parametrize('fault', ['hash', 'truncated', 'trailing', 'size', 'rate', 'interval', 'duplicate', 'empty'])
def test_actual_wave_rejects_corruption_and_fake_coverage(tmp_path, fault):
    """Headers, suffixes or client duration fields cannot substitute for real samples."""
    blob = wav_bytes(rate=44100 if fault == 'rate' else 48000)
    if fault == 'truncated': blob = blob[:-2]
    if fault == 'trailing': blob += b'junk'
    if fault == 'size': blob = blob[:4] + struct.pack('<I', 0xffffffff) + blob[8:]
    if fault == 'duplicate':
        blob += b'data'+struct.pack('<I', 2)+b'\0\0'; blob = blob[:4]+struct.pack('<I', len(blob)-8)+blob[8:]
    if fault == 'empty': blob = wav_bytes(frames=0)
    path = tmp_path/'bad.wav'; path.write_bytes(blob)
    digest = '0'*64 if fault == 'hash' else hashlib.sha256(blob).hexdigest()
    with pytest.raises(ValueError): inspect_wave(path, digest, [(0, 1010)] if fault == 'interval' else [])


@pytest.mark.parametrize('change', [{'gain_db': True}, {'muted': 'false'}, {'source_out_ms': 0}, {'timeline_start_ms': 1}, {'path': 'private.wav'}, {'source_in_ms': 0.0}])
def test_strict_sound_form_rejects_hidden_inputs(change):
    """Clients cannot send private paths, fake strict types or an unsupported sample grid."""
    track = {'id': 'one', 'candidate_id': 'one', 'source_in_ms': 0, 'source_out_ms': 100, 'timeline_start_ms': 0, **change}
    with pytest.raises(ValidationError): SoundDocument(picture={}, tracks=[track])


@pytest.mark.asyncio
async def test_silent_picture_requires_separate_explicit_dub_and_old_v1_stays_blocked(tmp_path, monkeypatch):
    """A full sound flow accepts actual adopted audio without weakening v1's original-audio rule."""
    engine, factory, sound, candidate, body = await fixture(tmp_path, monkeypatch)
    try:
        old_draft = await create_draft(factory, 'a', EditDraftCreate(chapter_id='c1', document=sound['picture']))
        with pytest.raises(HTTPException): await freeze_draft(factory, 'a', old_draft, EditFreezeWrite(expected_revision=1, acknowledge_changes=True, change_note='cannot bypass'))
        assert await service.import_candidate(factory, 'a', body) == candidate
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(SoundCandidate)) == 1
            assert await db.scalar(select(func.count()).select_from(SoundPublication)) == 0
        draft = await service.create_draft(factory, 'a', SoundCreate(chapter_id='c1', document=sound))
        async with factory() as db:
            state = await service.draft_view(db, 'a', draft)
            assert state['can_freeze'] and state['duration_ms'] == 1400
        freeze = SoundFreeze(expected_revision=1, acknowledge_changes=True, change_note='明确采用本地音频并保留原对白标记')
        publication = await service.freeze_draft(factory, 'a', draft, freeze)
        assert await service.freeze_draft(factory, 'a', draft, freeze) == publication
        async with factory() as db:
            view = await service.publication_view(db, 'a', publication)
            assert view['payload']['subtitles'] == [{'dialogue_id': 'line', 'speaker_id': 'hero', 'text': '手工对白', 'start_ms': 100, 'end_ms': 600}]
            assert 'relative_path' not in json.dumps(view) and '"stat"' not in json.dumps(view)
            assert await db.scalar(select(func.count()).select_from(EditPublication)) == 0
        monkeypatch.setattr(settings, 'editing_export_enabled', True)
        queued = await sound_exports.create_export(factory, 'a', publication, 'sound-export', settings.production_probe_executable)
        worker = sound_exports.SoundExportWorker(factory, settings.production_probe_executable)
        assert await worker.step()
        ready = await sound_exports.get_export(factory, 'a', queued['id'])
        assert ready['state'] == 'ready', ready
        assert ready['attempts'] == 1 and len(ready['files']) == 5 and ready['model_calls'] == 0
        assert (await sound_exports.create_export(factory, 'a', publication, 'sound-export', 'missing-tool'))['id'] == queued['id']
        mix, _, _ = await sound_exports.content_file(factory, 'a', queued['id'], 'mixdown')
        with wave.open(str(mix), 'rb') as decoded:
            assert (decoded.getframerate(), decoded.getnchannels(), decoded.getnframes()) == (48000, 2, 67200)
            pcm = struct.unpack('<'+'h'*(decoded.getnframes()*2), decoded.readframes(decoded.getnframes()))
        assert max(abs(value) for value in pcm[:4800*2]) == 0
        assert 2000 < max(abs(value) for value in pcm[5000*2:28000*2]) < 3100
        assert max(abs(value) for value in pcm[28800*2:]) == 0
        package, _, _ = await sound_exports.content_file(factory, 'a', queued['id'], 'package')
        with zipfile.ZipFile(package) as archive:
            assert set(archive.namelist()) == {'video.mp4', 'audio.wav', 'subtitles.srt', 'subtitles.vtt', 'manifest.json'}
            manifest = json.loads(archive.read('manifest.json'))
            assert manifest['timeline_hash'] == view['content_hash'] and 'relative_path' not in json.dumps(manifest)
            assert '手工对白' in archive.read('subtitles.srt').decode('utf8')
        with pytest.raises(HTTPException): await sound_exports.get_export(factory, 'b', queued['id'])
        with pytest.raises(HTTPException): await service.patch_draft(factory, 'a', draft, SoundPatch(expected_revision=99, document=sound))
    finally: await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['silent', 'silent_cue', 'muted', 'quiet', 'unconfirmed', 'late', 'outside', 'missing', 'foreign', 'duplicate', 'wrong_dialogue'])
async def test_missing_audio_and_false_dialogue_declarations_cannot_freeze(tmp_path, monkeypatch, failure):
    """An explicit checkbox alone cannot turn missing, silent or incorrectly placed media into coverage."""
    engine, factory, sound, _, _ = await fixture(tmp_path, monkeypatch, silence=failure == 'silent', signal_late=failure == 'silent_cue')
    try:
        track = sound['tracks'][0]
        if failure == 'silent_cue': track.update(source_in_ms=0, source_out_ms=1000, timeline_start_ms=0)
        if failure == 'muted': track['muted'] = True
        if failure == 'quiet': track['gain_db'] = -60
        if failure == 'unconfirmed': track['content_confirmed'] = False
        if failure == 'late': track['timeline_start_ms'] = 200
        if failure == 'outside': track['timeline_start_ms'] = 1000
        if failure == 'missing': sound['tracks'] = []
        if failure == 'foreign': track['candidate_id'] = 'another-project'
        if failure == 'duplicate': sound['tracks'].append({**track, 'id': 'two'})
        if failure == 'wrong_dialogue': track['dialogue_id'] = 'not-in-original-plan'
        try:
            draft = await service.create_draft(factory, 'a', SoundCreate(chapter_id='c1', document=sound))
        except HTTPException:
            assert failure == 'foreign'
            return
        async with factory() as db: assert not (await service.draft_view(db, 'a', draft))['can_freeze']
        with pytest.raises(HTTPException): await service.freeze_draft(factory, 'a', draft, SoundFreeze(expected_revision=1, acknowledge_changes=True, change_note='must not bypass'))
        async with factory() as db: assert await db.scalar(select(func.count()).select_from(SoundPublication)) == 0
    finally: await engine.dispose()


@pytest.mark.asyncio
async def test_candidate_and_publication_cannot_update_delete_or_replace(tmp_path, monkeypatch):
    """Append-only SQL guards reject raw replacement as well as ordinary history mutation."""
    engine, factory, sound, candidate, _ = await fixture(tmp_path, monkeypatch)
    try:
        await migrate_sound(engine)
        draft = await service.create_draft(factory, 'a', SoundCreate(chapter_id='c1', document=sound))
        publication = await service.freeze_draft(factory, 'a', draft, SoundFreeze(expected_revision=1, acknowledge_changes=True, change_note='explicit shorter picture'))
        for table, identifier in [('sound_candidates', candidate['id']), ('sound_publications', publication)]:
            for statement in [f'UPDATE {table} SET project_id=project_id WHERE id=:id', f'DELETE FROM {table} WHERE id=:id', f'INSERT OR REPLACE INTO {table} SELECT * FROM {table} WHERE id=:id']:
                async with factory() as db:
                    with pytest.raises(IntegrityError): await db.execute(text(statement), {'id': identifier})
                    await db.rollback()
    finally: await engine.dispose()


@pytest.mark.asyncio
async def test_owned_http_uses_strict_documents_and_actual_original_job(tmp_path, monkeypatch):
    """Real HTTP routes enforce scope and preserve export replay after local tool disablement."""
    from app.api.sound_timeline import router, get_sound_factory
    engine, factory, sound, candidate, body = await fixture(tmp_path, monkeypatch)
    app = FastAPI(); app.include_router(router, prefix='/api/v1'); app.dependency_overrides[get_sound_factory] = lambda: factory
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            base = '/api/v1/sound-v3/projects/a'
            assert (await client.get(base+'/candidates', params={'chapter_id': 'c1'})).json()['data'][0]['id'] == candidate['id']
            assert (await client.get('/api/v1/sound-v3/projects/b/candidates', params={'chapter_id': 'c1'})).status_code in (404, 422)
            bad = await client.post(base+'/drafts', json={'chapter_id': 'c1', 'document': sound, 'verified': True})
            assert bad.status_code == 422
            created = await client.post(base+'/drafts', json={'chapter_id': 'c1', 'document': sound})
            assert created.status_code == 201, created.text
            draft = created.json()['data']['id']
            frozen = await client.post(base+f'/drafts/{draft}/freeze', json={'expected_revision': 1, 'acknowledge_changes': True, 'change_note': 'actual imported source'})
            assert frozen.status_code == 200, frozen.text
            publication = frozen.json()['data']['id']
            queued = await client.post(base+f'/publications/{publication}/exports', json={'request_key': 'same'})
            assert queued.status_code == 201
            job = queued.json()['data']
            cancelled = await client.post(base+f'/exports/{job["id"]}/cancel', json={})
            assert cancelled.json()['data']['state'] == 'cancelled'
            monkeypatch.setattr(settings, 'editing_export_enabled', False)
            replay = await client.post(base+f'/publications/{publication}/exports', json={'request_key': 'same'})
            assert replay.status_code == 201 and replay.json()['data']['id'] == job['id']
            async with factory() as db: assert await db.scalar(select(func.count()).select_from(SoundExportJob)) == 1
    finally: await engine.dispose()


def test_local_sound_children_do_not_inherit_application_credentials(monkeypatch):
    """Every new decoder/encoder/speech child receives only explicit non-credential runtime essentials."""
    monkeypatch.setenv('ARK_API_KEY', 'authored-do-not-copy')
    monkeypatch.setenv('DATABASE_URL', 'authored-private-database')
    monkeypatch.setenv('WORKSPACE_INTERNAL_TOKEN', 'authored-do-not-copy')
    environment = child_environment()
    assert not {'ARK_API_KEY', 'DATABASE_URL', 'WORKSPACE_INTERNAL_TOKEN'} & set(environment)
    assert environment['PYTHONUTF8'] == '1' and environment['PYTHONDONTWRITEBYTECODE'] == '1'


@pytest.mark.asyncio
async def test_database_monitor_failure_joins_sound_renderer_before_returning(tmp_path, monkeypatch):
    """A transient database failure must not leave a rendering subprocess running without its supervisor."""
    engine, factory, sound, _, _ = await fixture(tmp_path, monkeypatch)
    class FailOnceFactory:
        """Raise once after rendering starts; allow the owner's failure journal to commit normally."""
        def __init__(self):
            """Keep the failure switch private to this authored monitor test."""
            self.fail = False
        def __call__(self):
            """Wrap actual independent database sessions, not the media validation result."""
            if self.fail:
                self.fail = False
                raise RuntimeError('Authored transient database monitor failure')
            return factory()
    wrapped = FailOnceFactory()
    joined = asyncio.Event()
    async def paused_render(_parameters, _deadline):
        """Represent an active owned helper, proving cancellation is joined on a monitor exception."""
        wrapped.fail = True
        try: await asyncio.Event().wait()
        finally: joined.set()
    try:
        draft = await service.create_draft(factory, 'a', SoundCreate(chapter_id='c1', document=sound))
        publication = await service.freeze_draft(factory, 'a', draft, SoundFreeze(expected_revision=1, acknowledge_changes=True, change_note='explicit software fixture'))
        queued = await sound_exports.create_export(factory, 'a', publication, 'monitor-failure', settings.production_probe_executable)
        monkeypatch.setattr(sound_exports, 'run_child', paused_render)
        worker = sound_exports.SoundExportWorker(wrapped, settings.production_probe_executable)
        assert await asyncio.wait_for(worker.step(), 5)
        assert joined.is_set()
        assert (await sound_exports.get_export(factory, 'a', queued['id']))['state'] == 'failed'
    finally: await engine.dispose()


@pytest.mark.asyncio
async def test_actual_sound_overlay_uses_final_crossfade_clock(tmp_path):
    """Independent real tone/PCM/pixel checks prove an overlay stays on output time after picture shortening."""
    from tests.test_transitions_v2 import source_fixture, transition_document, audio_samples, tone_amplitude, FFMPEG
    from app.services.editing.transitions import compose
    from app.services.editing.sound_media import run_child
    from app.services.editing.media import canonical_hash
    base = await source_fixture(tmp_path/'sources')
    picture_before = copy.deepcopy(base.payload)
    payload, issues = compose(base, transition_document())
    assert not issues and payload['duration_ms'] == 3500
    wave_path = tmp_path/'effect.wav'; raw = wav_bytes(); wave_path.write_bytes(raw)
    facts = inspect_wave(wave_path, hashlib.sha256(raw).hexdigest()); facts.pop('interval_peaks_pcm16')
    payload.update(schema_version=3, profile='portrait-360p30-sound-v3', tracks=[{
        'id': 'effect', 'source_in_ms': 100, 'source_out_ms': 600, 'timeline_start_ms': 2300, 'timeline_end_ms': 2800,
        'gain_db': -6, 'muted': False, 'path': str(wave_path), 'source': {'id': 'effect-wave', 'mime': 'audio/wav',
            'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw), 'audio': facts}}])
    for clip in payload['clips']: clip['path'] = str(tmp_path/'sources'/clip['source']['relative_path'])
    folder = tmp_path/'sound-render'; folder.mkdir()
    result = await run_child({'payload': payload, 'folder': str(folder), 'ffmpeg': str(FFMPEG), 'publication_id': 'authored-sound-crossfade', 'publication_hash': canonical_hash(payload)})
    assert result['facts']['video']['frames'] == 105 and result['facts']['mixdown']['sample_frames'] == 168000
    samples = audio_samples(folder/'audio.wav')
    assert tone_amplitude(samples, 2.4, 400) > .02
    assert tone_amplitude(samples, 1.1, 400) < .003 and tone_amplitude(samples, 3.1, 400) < .003
    assert payload['subtitles'][1]['start_ms'] == 2300 and payload['subtitles'][1]['end_ms'] == 2700
    assert base.payload == picture_before
