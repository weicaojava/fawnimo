"""Offline voice journals, original text, cancellation and actual installed-engine WAV validation."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import pytest
from fastapi import HTTPException
from sqlalchemy import select, text, func
from sqlalchemy.exc import IntegrityError
from app.config import settings
from app.core.contracts.local_speech import LocalSpeechRequest
from app.models.local_speech import LocalSpeechJob, LocalSpeechProduct
from app.models.sound_timeline import SoundCandidate, SoundDraft
from app.services.editing import local_speech as service
from app.services.editing.local_speech_schema import migrate_local_speech
from app.services.editing.local_speech_engine import LocalSpeechEngine, PROFILE
from app.services.editing.media import stat_identity
from app.services.editing.sound_wave import inspect_wave
from tests.test_sound_timeline import fixture as sound_fixture, wav_bytes

VOICE = {'id': 'sapi_'+'a'*64, 'name': 'Authored test engine', 'culture': 'zh-CN'}


class FixtureEngine:
    """Author tone PCM for workflow tests only; this engine never claims to speak real dialogue."""
    def __init__(self, pause=False):
        """Track calls and joins so cancellation is observable independently of job status."""
        self.calls, self.joined, self.enabled = 0, 0, True
        self.pause = pause
        self.entered = asyncio.Event()

    async def catalog(self):
        """Return one explicit local profile or model it being removed after original admission."""
        return {'enabled': self.enabled, 'reason': '', 'profile': PROFILE, 'external_fee_fen': 0, 'voices': [VOICE] if self.enabled else []}

    async def render(self, intent, folder):
        """Write actual finite sample bytes, optionally waiting to exercise cancellation and restart."""
        self.calls += 1; self.entered.set()
        try:
            if self.pause: await asyncio.Event().wait()
            path = Path(folder)/'voice.wav'; raw = wav_bytes(rate=24000); path.write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest()
            facts = inspect_wave(path, digest); facts.pop('interval_peaks_pcm16')
            return {'sha256': digest, 'size': len(raw), 'stat': stat_identity(path), 'audio': facts}
        finally: self.joined += 1


async def fixture(tmp_path, monkeypatch):
    """Use an actual immutable project plan and SQL scope guards, plus an explicit tone-only test engine."""
    engine, factory, sound, _, _ = await sound_fixture(tmp_path, monkeypatch)
    await migrate_local_speech(engine)
    monkeypatch.setattr(settings, 'local_speech_enabled', True)
    monkeypatch.setattr(settings, 'workspace_runtime_locked', False)
    body = LocalSpeechRequest(request_key='local-voice', plan_id=sound['picture']['plan_id'], dialogue_id='line', voice_id=VOICE['id'], rate=0, profile=PROFILE)
    return engine, factory, sound, body


@pytest.mark.asyncio
async def test_exact_original_dialogue_zero_fee_candidate_and_original_replay(tmp_path, monkeypatch):
    """One job yields one original-project candidate; replay ignores later engine disablement and never adopts."""
    engine, factory, _, body = await fixture(tmp_path, monkeypatch)
    voice = FixtureEngine()
    try:
        first = await service.create_job(factory, 'a', body, voice)
        assert first['text'] == '手工对白' and first['speaker_id'] == 'hero' and first['external_fee_fen'] == 0
        assert first['candidate'] is None and first['state'] == 'queued'
        assert (await service.create_job(factory, 'a', body, voice))['id'] == first['id']
        worker = service.LocalSpeechWorker(factory, voice)
        assert await worker.step()
        result = await service.get_job(factory, 'a', first['id'])
        assert result['state'] == 'ready' and result['attempts'] == 1 and result['candidate']['origin'] == 'local_speech'
        assert result['candidate']['audio']['sample_frames'] == 24000 and result['candidate']['chapter_id'] == 'c1'
        assert voice.calls == voice.joined == 1
        voice.enabled = False; monkeypatch.setattr(settings, 'local_speech_enabled', False)
        assert (await service.create_job(factory, 'a', body, voice)) == result
        assert not await worker.step()
        with pytest.raises(HTTPException): await service.create_job(factory, 'a', body.model_copy(update={'rate': 1}), voice)
        with pytest.raises(HTTPException): await service.get_job(factory, 'b', first['id'])
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(LocalSpeechJob)) == 1
            assert await db.scalar(select(func.count()).select_from(LocalSpeechProduct)) == 1
            assert await db.scalar(select(func.count()).select_from(SoundCandidate)) == 2
            assert await db.scalar(select(func.count()).select_from(SoundDraft)) == 0
            existing = set((await db.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).scalars())
            for table in ('execution_jobs', 'model_charge_batches', 'model_call_charges', 'model_job_charges'):
                if table not in existing: continue
                count = await db.scalar(text(f'SELECT COUNT(*) FROM {table}'))
                assert count == 0
    finally: await engine.dispose()


@pytest.mark.asyncio
async def test_cancellation_joins_running_local_attempt_without_publishing(tmp_path, monkeypatch):
    """Cancellation reaches the original process owner and never turns a partial file into a candidate."""
    engine, factory, _, body = await fixture(tmp_path, monkeypatch)
    voice = FixtureEngine(pause=True)
    try:
        job = await service.create_job(factory, 'a', body, voice)
        worker = service.LocalSpeechWorker(factory, voice)
        running = asyncio.create_task(worker.step())
        await asyncio.wait_for(voice.entered.wait(), 5)
        await service.cancel_job(factory, 'a', job['id'])
        assert await asyncio.wait_for(running, 5)
        status = await service.get_job(factory, 'a', job['id'])
        assert status['state'] == 'cancelled' and status['candidate'] is None
        assert voice.calls == voice.joined == 1
        async with factory() as db: assert await db.scalar(select(func.count()).select_from(LocalSpeechProduct)) == 0
    finally: await engine.dispose()


@pytest.mark.asyncio
async def test_shutdown_requeues_original_job_and_restart_yields_one_candidate(tmp_path, monkeypatch):
    """Restart can redo zero-fee local computation, preserving the same journal and immutable original text."""
    engine, factory, _, body = await fixture(tmp_path, monkeypatch)
    voice = FixtureEngine(pause=True)
    try:
        job = await service.create_job(factory, 'a', body, voice)
        worker = service.LocalSpeechWorker(factory, voice)
        running = asyncio.create_task(worker.step())
        await asyncio.wait_for(voice.entered.wait(), 5)
        running.cancel()
        with pytest.raises(asyncio.CancelledError): await running
        assert (await service.get_job(factory, 'a', job['id']))['state'] == 'queued' and voice.joined == 1
        restart_voice = FixtureEngine()
        assert await service.LocalSpeechWorker(factory, restart_voice).step()
        result = await service.get_job(factory, 'a', job['id'])
        assert result['state'] == 'ready' and result['attempts'] == 2 and result['text'] == job['text']
        async with factory() as db: assert await db.scalar(select(func.count()).select_from(LocalSpeechProduct)) == 1
    finally: await engine.dispose()


@pytest.mark.asyncio
async def test_expired_old_attempt_cannot_publish_or_release_new_owner(tmp_path, monkeypatch):
    """Both job and global slot leases fence a late process from publishing over recovered work."""
    engine, factory, _, body = await fixture(tmp_path, monkeypatch)
    clock = [1000]
    voice = FixtureEngine()
    try:
        job = await service.create_job(factory, 'a', body, voice)
        worker = service.LocalSpeechWorker(factory, voice, clock=lambda: clock[0])
        old_id, old_token = await worker.claim()
        clock[0] += 121
        new_id, new_token = await worker.claim()
        assert old_id == new_id == job['id'] and old_token != new_token
        async with factory() as db: intent = (await db.get(LocalSpeechJob, old_id)).intent
        folder = tmp_path/'late-voice'; folder.mkdir()
        facts = await voice.render(intent, folder)
        with pytest.raises(HTTPException): await worker.publish(old_id, old_token, folder, facts)
        await worker.finish_failure(old_id, old_token, 'old owner')
        await worker.publish(new_id, new_token, folder, facts)
        result = await service.get_job(factory, 'a', old_id)
        assert result['state'] == 'ready' and result['attempts'] == 2
    finally: await engine.dispose()


@pytest.mark.asyncio
async def test_sql_intent_receipt_and_original_voice_binding(tmp_path, monkeypatch):
    """Raw SQL cannot rewrite original voice intent or move a published result to another plan/dialogue."""
    from app.core.contracts.sound_timeline import SoundCreate, SoundFreeze
    from app.services.editing import sound_timeline
    engine, factory, sound, body = await fixture(tmp_path, monkeypatch)
    voice = FixtureEngine()
    try:
        await migrate_local_speech(engine)
        job = await service.create_job(factory, 'a', body, voice)
        await service.LocalSpeechWorker(factory, voice).step()
        result = await service.get_job(factory, 'a', job['id'])
        for statement in ('UPDATE local_speech_jobs SET intent=intent WHERE id=:id', 'DELETE FROM local_speech_jobs WHERE id=:id',
            'INSERT OR REPLACE INTO local_speech_jobs SELECT * FROM local_speech_jobs WHERE id=:id',
            'UPDATE local_speech_products SET intent_hash=intent_hash WHERE job_id=:id', 'DELETE FROM local_speech_products WHERE job_id=:id',
            'INSERT OR REPLACE INTO local_speech_products SELECT * FROM local_speech_products WHERE job_id=:id'):
            async with factory() as db:
                if statement.startswith('UPDATE local_speech_jobs SET intent=intent'):
                    # A no-op identity write does not change any immutable value.
                    await db.execute(text(statement), {'id': job['id']}); await db.rollback(); continue
                with pytest.raises(IntegrityError): await db.execute(text(statement), {'id': job['id']})
                await db.rollback()
        async with factory() as db:
            with pytest.raises(IntegrityError): await db.execute(text('UPDATE local_speech_jobs SET intent=:intent WHERE id=:id'), {'intent': '{}', 'id': job['id']})
            await db.rollback()
        sound['tracks'][0]['candidate_id'] = result['candidate']['id']
        draft = await sound_timeline.create_draft(factory, 'a', SoundCreate(chapter_id='c1', document=sound))
        publication = await sound_timeline.freeze_draft(factory, 'a', draft, SoundFreeze(expected_revision=1, acknowledge_changes=True, change_note='local authored workflow candidate only'))
        assert publication
    finally: await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != 'nt', reason='Actual installed Windows system speech is unavailable on this host')
async def test_actual_installed_windows_voice_writes_fully_decoded_pcm(tmp_path, monkeypatch):
    """Run the real installed offline engine, independent of the tone-only workflow test engine."""
    monkeypatch.setattr(settings, 'local_speech_enabled', True)
    monkeypatch.setattr(settings, 'workspace_runtime_locked', False)
    engine = LocalSpeechEngine()
    catalog = await engine.catalog()
    if not catalog['enabled']: pytest.skip('No usable installed Windows system voice')
    voice = next((row for row in catalog['voices'] if row['culture'] == 'zh-CN'), catalog['voices'][0])
    folder = tmp_path/'actual-offline-speech'; folder.mkdir()
    facts = await engine.render({'profile': PROFILE, 'external_fee_fen': 0, 'text': '这是本地配音验收。', 'voice': voice, 'rate': 0}, folder)
    assert facts['audio']['sample_rate'] == 24000 and facts['audio']['channels'] == 1
    assert facts['audio']['peak_pcm16'] > 32 and facts['audio']['duration_us'] > 10000
    assert hashlib.sha256((folder/'voice.wav').read_bytes()).hexdigest() == facts['sha256']
