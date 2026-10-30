"""Actual fixed-worker local ASR/vision with newly authored video and original-project holds, never real model quality."""
from contextlib import closing, ExitStack
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time

from fastapi.testclient import TestClient
import httpx
import pytest

from app.controlplane.gateway import create_gateway
from app.core.contracts.model_accounting import ModelPriceConfiguration, ModelPriceEnvelope, ModelPriceRule
from tests.test_workspace_local_model_execution import LocalExecutionSpaces, model_origin, PROJECT


class LocalUnderstandingSpaces(LocalExecutionSpaces):
    """Reuse completed local text/image history, then execute one separate two-phase understanding attempt per worker."""
    def __init__(self, app, client, root, origins, probe):
        """Author a video from filters, analyze its real bytes and bind explicit managed input models and quotes."""
        super().__init__(app, client, root, origins)
        clip = root / 'self-authored-input.mp4'
        subprocess.run([str(probe), '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=96x64:rate=12:duration=2',
            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=16000:duration=2', '-c:v', 'libx264', '-threads', '1',
            '-bf', '0', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', '-output_ts_offset', '2', str(clip)], check=True, timeout=20)
        self.understanding_jobs, self.understanding_bodies = {}, {}
        for side in ('a', 'b'):
            for model, category, params in [('vision-model', 'text', {'understanding_vision_protocol': 'openai_chat_image_v1'}),
                    ('asr-model', 'audio', {'understanding_asr_protocol': 'openai_verbose_segments_v1'})]:
                self.ok(side, 'POST', '/llm/models', json={'id': model, 'provider_id': 'same-provider', 'name': model + '-' + side,
                    'category': category, 'params': params})
            directory = root / 'workspaces' / self.identities[side]['workspace_id']; private = directory / 'private'
            with closing(sqlite3.connect(directory / 'studio.db')) as db:
                revision = db.execute("SELECT revision FROM provider_connection_versions WHERE provider_id='same-provider'").fetchone()[0]
            prices = ModelPriceConfiguration.model_validate_json((private / 'model-prices.json').read_bytes())
            rules = [ModelPriceRule(kinds=[kind], quote=ModelPriceEnvelope(model_id=model, model_name=model + '-' + side,
                provider_id='same-provider', provider_connection_version=revision, account_version='authored-understanding-' + side,
                max_call_minor=amount, source_note='Authored local vision/ASR test only'))
                for kind, model, amount in [('video_vision', 'vision-model', 11), ('video_asr', 'asr-model', 7)]]
            prices = prices.model_copy(update={'rules': [*prices.rules, *rules]})
            (private / 'model-prices.json').write_text(prices.model_dump_json(), 'utf-8')
            local = json.loads((private / 'local-models.json').read_text('utf-8')); local['video_understanding_ports'] = [origins[side]['port']]
            (private / 'local-models.json').write_text(json.dumps(local), 'utf-8')
            client.portal.call(self.restart, self.identities[side]['workspace_id'])
            media = self.ok(side, 'POST', f'/studio/production/projects/{PROJECT}/media', files={'file': ('authored-input.mp4', clip.read_bytes(), 'video/mp4')})[0]
            analysis = self.ok(side, 'POST', f'/reverse/projects/{PROJECT}/analyses', json={'media_id': media['id'], 'request_key': 'same-authored-analysis'})
            for _ in range(250):
                analysis = self.ok(side, 'GET', f'/reverse/projects/{PROJECT}/analyses/' + analysis['id'])
                if analysis['state'] in {'ready', 'degraded'}:
                    break
                assert analysis['state'] != 'failed', analysis
                time.sleep(.08)
            assert analysis['state'] in {'ready', 'degraded'}
            self.ok(side, 'PUT', f'/video-understanding/projects/{PROJECT}/connection', json={'expected_revision': 0, 'enabled': True,
                'vision_model_id': 'vision-model', 'audio_model_id': 'asr-model', 'transcribe_audio': True, 'max_frames': 3, 'timeout_seconds': 5})
            connection = self.ok(side, 'GET', f'/video-understanding/projects/{PROJECT}/connection', params={'analysis_id': analysis['id']})
            assert connection['available'] and connection['max_reservation_minor'] == 18, connection
            body = {'analysis_id': analysis['id'], 'expected_analysis_revision': analysis['revision'],
                'expected_connection_revision': connection['revision'], 'request_key': 'same-authored-understanding',
                'confirm_model_call': True, 'expected_price_fingerprints': connection['price_fingerprints']}
            self.understanding_bodies[side] = body
            self.understanding_jobs[side] = self.understanding_submit(side, body)
        for side in ('a', 'b'):
            for _ in range(250):
                row = self.ok(side, 'GET', f'/video-understanding/projects/{PROJECT}/jobs/' + self.understanding_jobs[side])
                if row['state'] not in {'queued', 'running'}:
                    break
                time.sleep(.05)
            assert row['state'] == 'succeeded', row['reason']

    def understanding_submit(self, side, body):
        """Admit only in the authored worker test; gateway fee permission remains an independent closed gate."""
        worker = self.app.state.workers.workers[self.identities[side]['workspace_id']]
        with httpx.Client(trust_env=False, follow_redirects=False) as internal:
            response = internal.post(f'http://127.0.0.1:{worker.port}/api/v1/video-understanding/projects/{PROJECT}/jobs',
                json=body, headers={'X-Workspace-Token': worker.token, 'X-Identity-Id': self.identities[side]['user']['id']})
        assert response.status_code == 201, response.text
        return response.json()['data']['id']


@pytest.fixture(scope='module')
def understanding_spaces(tmp_path_factory):
    """Allocate fresh roots, media, accounts, processes and origins, with a trusted local decoder only."""
    probe = os.environ.get('FAWNIMO_TEST_FFMPEG') or shutil.which('ffmpeg')
    assert probe and Path(probe).is_file(), 'A trusted FFmpeg is required for real local video evidence'
    with ExitStack() as stack:
        origins = {side: stack.enter_context(model_origin(side)) for side in ('a', 'b')}
        root = tmp_path_factory.mktemp('local-understanding-execution')
        app = create_gateway(root, secure_cookies=False, public_origin='http://script-task.local', capacity=2, probe=Path(probe))
        with TestClient(app, base_url='http://script-task.local') as client:
            yield LocalUnderstandingSpaces(app, client, root, origins, Path(probe))


@pytest.mark.parametrize('side', ['a', 'b'])
def test_local_asr_and_vision_use_only_owned_media_models_attempt_and_original_project_fee(understanding_spaces, side):
    """Real decoded WAV/frames traverse the chosen local origin, returning unreviewed evidence without settled fees."""
    spaces = understanding_spaces; other = 'b' if side == 'a' else 'a'; identifier = spaces.understanding_jobs[side]
    path = f'/video-understanding/projects/{PROJECT}/jobs/' + identifier
    row = spaces.ok(side, 'GET', path)
    assert row['state'] == 'succeeded' and row['document']['human_reviewed'] is False and len(row['transcripts']) == 1
    assert 'private-authored-' not in json.dumps(row) and spaces.origins[side]['key'] not in json.dumps(row)
    calls = spaces.origins[side]['calls']
    assert len(calls) == 7 and calls[-2]['path'].endswith('/audio/transcriptions') and calls[-2]['has_wav']
    assert calls[-1]['path'].endswith('/chat/completions') and calls[-1]['frame_images'] == 3
    assert all(call['authenticated'] for call in calls)
    assert spaces.understanding_submit(side, spaces.understanding_bodies[side]) == identifier and len(calls) == 7
    assert spaces.request(other, 'GET', path).status_code == 404
    assert spaces.request(other, 'POST', path + '/cancel', json={'expected_attempt': 1}).status_code == 404
    assert spaces.request(side, 'POST', f'/video-understanding/projects/{PROJECT}/jobs', json=spaces.understanding_bodies[side]).status_code == 503
    budget = spaces.ok(side, 'GET', f'/video-runtime/projects/{PROJECT}/budget')
    assert budget['reserved_minor'] == 73 and budget['spent_minor'] == 0
    directory = spaces.root / 'workspaces' / spaces.identities[side]['workspace_id']
    with closing(sqlite3.connect(directory / 'studio.db')) as db:
        units = db.execute("SELECT kind,dispatch_state,provider_request_id FROM model_call_charges WHERE kind IN ('video_asr','video_vision') ORDER BY kind").fetchall()
        actors = db.execute('SELECT DISTINCT submitted_by FROM model_charge_batches').fetchall()
    assert len(units) == 2 and {item[0] for item in units} == {'video_asr', 'video_vision'}
    assert all(item[1] == 'accepted' and item[2].startswith('private-authored-' + side) for item in units)
    assert actors == [(spaces.identities[side]['user']['id'],)]
