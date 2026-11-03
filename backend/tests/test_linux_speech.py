"""Linux offline speech boundaries and real image synthesis, separated from authored-tone history fixtures and semantic quality."""
import hashlib
import json
import os
from pathlib import Path
import sys
import wave

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import text

from app.config import settings
from app.core.contracts.local_speech import LocalSpeechRequest
from app.core.contracts.local_speech_protocols import LINUX_PROFILE, WINDOWS_PROFILE
from app.services.editing import local_speech as service
from app.services.editing import local_speech_linux_child as child
from app.services.editing.local_speech_engine import LocalSpeechEngine
from app.services.editing.local_speech_schema import migrate_local_speech
from tests.test_local_speech import fixture, FixtureEngine

LINUX_VOICE = {'id': 'espeak_'+'b'*64, 'name': 'Authored Linux history fixture', 'culture': 'cmn'}
ACTUAL_LINUX = sys.platform.startswith('linux') and os.environ.get('FAWNIMO_TEST_LINUX_SPEECH') == '1'


class LinuxToneEngine(FixtureEngine):
    """Produce authored tone only for schema/history tests; this is never an actual speech engine qualification."""
    profile = LINUX_PROFILE
    async def catalog(self):
        """Bind the fixture to a distinct explicit profile, preserving the original Windows fixture unchanged."""
        return {'enabled': self.enabled, 'reason': '', 'profile': LINUX_PROFILE, 'external_fee_fen': 0,
                'voices': [LINUX_VOICE] if self.enabled else []}


def test_voice_requests_cannot_switch_profile_with_the_old_identifier():
    """A saved Windows request cannot become Linux synthesis through a profile-only or voice-only edit."""
    base = {'request_key': 'original', 'plan_id': 'plan', 'dialogue_id': 'dialogue', 'rate': 0}
    for profile, identifier in ((LINUX_PROFILE, 'sapi_'+'a'*64), (WINDOWS_PROFILE, LINUX_VOICE['id'])):
        with pytest.raises(ValidationError):
            LocalSpeechRequest(**base, profile=profile, voice_id=identifier)
    accepted = LocalSpeechRequest(**base, profile=LINUX_PROFILE, voice_id=LINUX_VOICE['id'])
    assert accepted.profile == LINUX_PROFILE and accepted.voice_id == LINUX_VOICE['id']


def test_linux_helper_rejects_command_path_and_frame_overrides(tmp_path):
    """Malformed private frames cannot choose another command, implicit rate, source file or output directory."""
    base = {'mode': 'render', 'voice': LINUX_VOICE, 'text': '原对白', 'rate': 0, 'folder': str(tmp_path)}
    values = [b'{"mode":"catalog","mode":"render"}', b' '*(child.MAX_INPUT+1),
        json.dumps({'mode': 'catalog', 'executable': '/other/command'}).encode(),
        json.dumps({**base, 'rate': True}).encode(), json.dumps({**base, 'folder': '../other'}).encode(),
        json.dumps({**base, 'voice': {**LINUX_VOICE, 'culture': '../other'}}).encode(),
        json.dumps({**base, 'text': 'original\x00hidden'}).encode()]
    for value in values:
        with pytest.raises(ValueError):
            child.configuration(value)
    assert list(tmp_path.iterdir()) == []
    original = '<speak>原对白 --voices -w ignored</speak>'
    assert child.configuration(json.dumps({**base, 'text': original}).encode())['text'] == original


def test_installed_voice_catalog_refuses_ambiguous_basic_or_external_variants(tmp_path, monkeypatch):
    """A duplicate language/MBROLA row cannot silently substitute another installed voice profile."""
    monkeypatch.setattr(child, 'trusted_binary', lambda _path: '1'*64)
    def response(_binary, arguments, _folder, **_options):
        """Provide bounded public catalog text only; no binary or model is executed by this fixture."""
        if arguments == ['--version']:
            return b'eSpeak NG text-to-speech: authored test version\n'
        return b'Pty Language Age/Gender VoiceName File\n5 cmn --/M Mandarin sit/cmn\n5 cmn --/M Other mb/other\n'
    monkeypatch.setattr(child, 'command', response)
    with pytest.raises(ValueError):
        child.catalog(tmp_path)


def test_command_failure_exposes_counts_without_raw_stderr_or_dialogue(tmp_path, monkeypatch):
    """Retain bounded numeric resource diagnostics without reflecting any subprocess content."""
    from types import SimpleNamespace
    monkeypatch.setenv('PULSE_CLIENTCONFIG','AUTHORED_AMBIENT_AUDIO_POLICY')
    monkeypatch.setenv('PULSE_COOKIE','AUTHORED_PRIVATE_AUDIO_COOKIE')
    def fail(_arguments, **options):
        """Author a failed version command; no real system speech executable is launched."""
        policy=Path(options['env']['PULSE_CLIENTCONFIG'])
        assert policy == Path(child.__file__).with_name('local_speech_pulse.conf').resolve()
        assert 'enable-shm = no' in policy.read_text('utf8') and 'autospawn = no' in policy.read_text('utf8')
        assert 'PULSE_COOKIE' not in options['env'] and 'AUTHORED_AMBIENT' not in str(options['env'])
        options['stdout'].write(b'version')
        options['stderr'].write(b'private-original-dialogue-or-secret')
        return SimpleNamespace(returncode=-25)
    monkeypatch.setattr(child.subprocess,'run',fail)
    with pytest.raises(child.SpeechCommandError) as failure:
        child.command(Path('/usr/bin/espeak-ng'),['--version'],tmp_path,stage='engine_version')
    assert failure.value.stage == 'engine_version' and failure.value.returncode == -25
    assert (failure.value.stdout_bytes,failure.value.stderr_bytes) == (7,35)
    assert 'private' not in str(failure.value) and 'secret' not in str(failure.value)


@pytest.mark.asyncio
async def test_exact_legacy_scope_upgrade_preserves_history_and_rejects_unknown_guard_atomically(tmp_path, monkeypatch):
    """Upgrade the frozen real v1 trigger while preserving Windows rows; an unknown companion guard rolls back all DDL."""
    engine, factory, _, original = await fixture(tmp_path, monkeypatch)
    windows = FixtureEngine()
    try:
        old = await service.create_job(factory, 'a', original, windows)
        assert await service.LocalSpeechWorker(factory, windows).step()
        previous = await service.get_job(factory, 'a', old['id'])
        legacy = Path(__file__).with_name('fixtures').joinpath('local_speech_job_scope_v1.sql').read_text('utf8').strip()
        async with engine.begin() as db:
            companion = (await db.execute(text("SELECT sql FROM sqlite_master WHERE name='local_speech_job_no_delete'"))).scalar_one()
            await db.execute(text('DROP TRIGGER local_speech_job_scope'))
            await db.execute(text(legacy))
            await db.execute(text('DROP TRIGGER local_speech_job_no_delete'))
            await db.execute(text("CREATE TRIGGER local_speech_job_no_delete BEFORE DELETE ON local_speech_jobs BEGIN SELECT RAISE(ABORT,'changed guard'); END"))
        with pytest.raises(RuntimeError, match='guard changed'):
            await migrate_local_speech(engine)
        async with engine.begin() as db:
            retained = (await db.execute(text("SELECT sql FROM sqlite_master WHERE name='local_speech_job_scope'"))).scalar_one()
            assert ' '.join(retained.split()) == ' '.join(legacy.split())
            await db.execute(text('DROP TRIGGER local_speech_job_no_delete'))
            await db.execute(text(companion))
        assert await service.get_job(factory, 'a', old['id']) == previous
        await migrate_local_speech(engine)
        waiting_windows = await service.create_job(factory, 'a', original.model_copy(update={'request_key': 'waiting-for-windows'}), windows)
        linux = LinuxToneEngine()
        request = LocalSpeechRequest(**{**original.model_dump(), 'request_key': 'new-linux-history',
            'voice_id': LINUX_VOICE['id'], 'profile': LINUX_PROFILE})
        new = await service.create_job(factory, 'a', request, linux)
        assert new['profile'] == LINUX_PROFILE and new['voice'] == LINUX_VOICE
        assert await service.LocalSpeechWorker(factory, linux).step()
        ready = await service.get_job(factory, 'a', new['id'])
        assert ready['state'] == 'ready' and ready['profile'] == LINUX_PROFILE and ready['candidate']['origin'] == 'local_speech'
        waiting = await service.get_job(factory, 'a', waiting_windows['id'])
        assert waiting['state'] == 'queued' and waiting['attempts'] == 0
        assert (await service.cancel_job(factory, 'a', waiting['id']))['state'] == 'cancelled'
        linux.enabled = False
        assert await service.create_job(factory, 'a', original, linux) == previous
        assert await service.create_job(factory, 'a', request, linux) == ready
        assert windows.calls == linux.calls == 1
        with pytest.raises(HTTPException):
            await service.get_job(factory, 'b', new['id'])
    finally:
        await engine.dispose()
