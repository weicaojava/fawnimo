"""Actual installed Linux speech in the deployed image; signal/export correctness never substitutes for spoken-content or film-quality review."""
import hashlib
import os
from pathlib import Path
import sys
import wave

import pytest
from fastapi import HTTPException

from app.config import settings
from app.core.contracts.local_speech import LocalSpeechRequest
from app.core.contracts.local_speech_protocols import LINUX_PROFILE, WINDOWS_PROFILE
from app.core.contracts.sound_timeline import SoundCreate, SoundFreeze
from app.services.editing import local_speech as service, sound_timeline, sound_exports
from app.services.editing.local_speech_engine import LocalSpeechEngine
from tests.test_local_speech import fixture, FixtureEngine

pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux') or os.environ.get('FAWNIMO_TEST_LINUX_SPEECH') != '1',
    reason='Requires the explicit actual deployed-image Linux offline-speech profile')


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['cmn', 'en-us'])
async def test_actual_installed_linux_voices_pcm_and_exclusive_output(tmp_path, monkeypatch, language):
    """Speak real original Mandarin/English text into finite PCM, proving input flags stay text and existing output is preserved."""
    monkeypatch.setattr(settings, 'local_speech_enabled', True)
    monkeypatch.setattr(settings, 'workspace_runtime_locked', False)
    engine = LocalSpeechEngine()
    catalog = await engine.catalog()
    assert catalog['enabled'] and catalog['profile'] == LINUX_PROFILE, engine.last_failure
    assert {row['culture'] for row in catalog['voices']} == {'cmn', 'en-us'}
    voice = next(row for row in catalog['voices'] if row['culture'] == language)
    original = '这是离线配音测试。' if language == 'cmn' else 'This is an offline voice test. --help --voices.'
    intent = {'profile': LINUX_PROFILE, 'external_fee_fen': 0, 'voice': voice, 'text': original, 'rate': 0}
    folder = tmp_path/'new-output'
    folder.mkdir(mode=0o700)
    facts = await engine.render(intent, folder)
    path = folder/'voice.wav'
    assert facts['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert facts['size'] == path.stat().st_size and facts['audio']['peak_pcm16'] > 32
    with wave.open(str(path), 'rb') as source:
        assert (source.getframerate(), source.getnchannels(), source.getsampwidth()) == (48000, 1, 2)
        assert 0 < source.getnframes() <= 48000*300
    retained = tmp_path/'retained-output'
    retained.mkdir(mode=0o700)
    previous = retained/'voice.wav'
    previous.write_bytes(b'original-owned-fixture')
    with pytest.raises(HTTPException):
        await engine.render(intent, retained)
    assert previous.read_bytes() == b'original-owned-fixture'
    monkeypatch.setattr(settings, 'workspace_runtime_locked', True)
    assert (await engine.catalog())['enabled'] is False


@pytest.mark.asyncio
async def test_actual_linux_original_job_candidate_mix_replay_and_windows_history(tmp_path, monkeypatch):
    """Retain authored Windows history while actual Linux speech creates an unadopted candidate, explicit mix/export and scoped replay."""
    db_engine, factory, sound, windows_body = await fixture(tmp_path, monkeypatch)
    windows = FixtureEngine()
    linux = LocalSpeechEngine()
    try:
        previous = await service.create_job(factory, 'a', windows_body, windows)
        assert await service.LocalSpeechWorker(factory, windows).step()
        previous = await service.get_job(factory, 'a', previous['id'])
        catalog = await linux.catalog()
        assert catalog['enabled'] and catalog['profile'] == LINUX_PROFILE, linux.last_failure
        voice = next(row for row in catalog['voices'] if row['culture'] == 'cmn')
        body = LocalSpeechRequest(**{**windows_body.model_dump(), 'request_key': 'actual-linux-original',
            'voice_id': voice['id'], 'profile': LINUX_PROFILE, 'rate': 2})
        queued = await service.create_job(factory, 'a', body, linux)
        assert queued['text'] == '手工对白' and queued['candidate'] is None
        assert await service.LocalSpeechWorker(factory, linux).step()
        ready = await service.get_job(factory, 'a', queued['id'])
        assert ready['state'] == 'ready' and ready['attempts'] == 1, (ready['reason'], linux.last_failure)
        assert ready['profile'] == LINUX_PROFILE and ready['external_fee_fen'] == 0
        assert previous['profile'] == WINDOWS_PROFILE and windows.calls == 1
        with pytest.raises(HTTPException):
            await service.get_job(factory, 'b', ready['id'])
        ending = int(ready['candidate']['audio']['duration_us'])//10000*10
        assert 700 <= ending <= 2000
        sound['picture']['clips'][0].update(source_in_ms=0, source_out_ms=2000)
        sound['tracks'][0].update(candidate_id=ready['candidate']['id'], source_in_ms=0,
            source_out_ms=ending, timeline_start_ms=0, content_confirmed=True)
        draft = await sound_timeline.create_draft(factory, 'a', SoundCreate(chapter_id='c1', document=sound))
        publication = await sound_timeline.freeze_draft(factory, 'a', draft,
            SoundFreeze(expected_revision=1, acknowledge_changes=True, change_note='Explicit authored software adoption; speech content/film quality not qualified'))
        monkeypatch.setattr(settings, 'editing_export_enabled', True)
        exported = await sound_exports.create_export(factory, 'a', publication, 'actual-linux-mix', settings.production_probe_executable)
        assert await sound_exports.SoundExportWorker(factory, settings.production_probe_executable).step()
        result = await sound_exports.get_export(factory, 'a', exported['id'])
        assert result['state'] == 'ready' and result['model_calls'] == 0 and len(result['files']) == 5
        wave_path, _, _ = await sound_exports.content_file(factory, 'a', exported['id'], 'mixdown')
        with wave.open(str(wave_path), 'rb') as mixed:
            assert (mixed.getframerate(), mixed.getnchannels(), mixed.getnframes()) == (48000, 2, 96000)
        cancelled = await service.create_job(factory, 'a', body.model_copy(update={'request_key': 'actual-linux-cancel'}), linux)
        cancelled = await service.cancel_job(factory, 'a', cancelled['id'])
        assert cancelled['state'] == 'cancelled' and cancelled['attempts'] == 0
        monkeypatch.setattr(settings, 'workspace_runtime_locked', True)
        assert await service.create_job(factory, 'a', windows_body, linux) == previous
        assert await service.create_job(factory, 'a', body, linux) == ready
        with pytest.raises(HTTPException) as denied:
            await service.create_job(factory, 'a', body.model_copy(update={'request_key': 'paused-new'}), linux)
        assert denied.value.status_code == 503
    finally:
        await db_engine.dispose()
