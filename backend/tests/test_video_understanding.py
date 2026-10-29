"""Real decoded video, SQLite and controlled HTTP acceptance; fixtures prove software, never model quality."""
import asyncio
import base64
from contextlib import contextmanager
from copy import deepcopy
from email import policy
from email.parser import BytesParser
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import threading
import time
import httpx
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import event, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.api.video_understanding import router, get_understanding_factory
from app.config import settings
from app.core.db import Base
from app.core.contracts.video_understanding import UnderstandingConnectionWrite, UnderstandingCreate
from app.core.contracts.video_dna import DnaDocument, DnaPatch
from app.models.production import ProductionMedia
from app.models.reverse import ReverseAnalysis
from app.models.studio import Project
from app.models.video_dna import DnaDraft, DnaSnapshot
from app.models.video_understanding import UnderstandingAttempt, UnderstandingConfig, UnderstandingJob
from app.services.reverse.ingestion import create_analysis, edit_cuts, run_analysis
from app.services.studio.production_media import store_batch
from app.services.video_dna import service as dna
from app.services.video_understanding import service
from app.services.video_understanding.connection import endpoint_base
from app.services.video_understanding.errors import UnderstandingFailure
from app.services.video_understanding.runtime import UnderstandingWorker, claim_job
from app.services.video_understanding.schema import migrate_video_understanding
from app.services.video_understanding.source import prepare_inputs, sample_indices
from app.services.video_understanding.transport import normalize_candidate, transcripts_from_response

ROOT = Path(__file__).resolve().parents[5]
FFMPEG = ROOT / 'work/ffmpeg.exe'


def fixture_candidate(context):
    """Return explicitly labeled synthetic claims solely to exercise provenance and reference constraints."""
    frames = context['sampled_frames']
    first = {'start_frame': frames[0]['index'], 'end_frame': frames[0]['index'] + 1}
    claim = {'zh': '受控HTTP软件测试候选，非模型质量结果', 'en': 'Controlled HTTP software fixture, not model quality', 'status': 'observed', 'evidence': [first], 'confidence': 50}
    assets = [
        {'id': 'C1', 'kind': 'character', 'name_zh': '测试人物候选', 'name_en': 'Fixture character', 'fields': {'costume': deepcopy(claim)}},
        {'id': 'E1', 'kind': 'scene', 'name_zh': '测试场景候选', 'name_en': 'Fixture scene', 'fields': {'background': deepcopy(claim)}},
        {'id': 'P1', 'kind': 'prop', 'name_zh': '测试道具候选', 'name_en': 'Fixture prop', 'fields': {'shape': deepcopy(claim)}},
    ]
    shots = []
    for item in context['shots']:
        evidence = next(frame['index'] for frame in frames if item['start_frame'] <= frame['index'] < item['end_frame'])
        local = {**deepcopy(claim), 'evidence': [{'start_frame': evidence, 'end_frame': evidence + 1}]}
        shots.append({key: item[key] for key in ('id', 'start_frame', 'end_frame')} | {'character_ids': ['C1'], 'scene_id': 'E1', 'prop_ids': ['P1'], 'action': local, 'image_prompt': deepcopy(local), 'video_prompt': deepcopy(local)})
    return DnaDocument(title='受控HTTP软件候选，不代表真实识别质量', assets=assets, shots=shots).model_dump()


class ModelFixture:
    """Receive actual image/WAV HTTP requests on loopback without an external account or API key."""
    def __init__(self):
        self.calls, self.contexts = [], []
        self.audio_mode, self.vision_mode = 'timed', 'valid'
        self.vision_received, self.release_vision = threading.Event(), threading.Event()
        self.delay_vision = False

    @contextmanager
    def server(self):
        """Run a bounded thread server whose exact received media bytes are checked by the tests."""
        owner = self
        class Handler(BaseHTTPRequestHandler):
            """Implement only the two provider endpoints used by the real adapter."""
            def log_message(self, *_args):
                """Suppress HTTP logs; no request bodies or credentials enter diagnostics."""
                return

            def do_POST(self):
                """Capture real transport bytes and return intentionally controlled JSON responses."""
                body = self.rfile.read(int(self.headers['Content-Length']))
                owner.calls.append({'path': self.path, 'body': body, 'content_type': self.headers.get('Content-Type'), 'authorization': self.headers.get('Authorization')})
                status = 200
                if self.path.endswith('/audio/transcriptions'):
                    result = {'text': '未带时标，不得粘贴到各镜'} if owner.audio_mode == 'untimed' else {'text': '受控转写候选', 'segments': [{'start': 0.2, 'end': 0.8, 'text': '受控转写候选'}]}
                elif self.path.endswith('/chat/completions'):
                    payload = json.loads(body)
                    context = json.loads(payload['messages'][1]['content'][0]['text'])
                    owner.contexts.append(context)
                    owner.vision_received.set()
                    if owner.delay_vision:
                        owner.release_vision.wait(10)
                    document = fixture_candidate(context)
                    if owner.vision_mode == 'unsent_frame':
                        document['shots'][0]['action']['evidence'] = [{'start_frame': 1, 'end_frame': 2}]
                    result = {'choices': [{'message': {'content': json.dumps(document, ensure_ascii=False)}}]}
                    if owner.vision_mode == 'redirect':
                        status = 302
                else:
                    status, result = 404, {'error': 'unknown fixture endpoint'}
                encoded = json.dumps(result, ensure_ascii=False).encode()
                self.send_response(status); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(encoded)))
                if status == 302:
                    self.send_header('Location', '/redirect-must-not-receive-credentials')
                self.end_headers()
                try:
                    if owner.vision_mode == 'slow' and self.path.endswith('/chat/completions'):
                        for byte in encoded:
                            self.wfile.write(bytes([byte])); self.wfile.flush(); time.sleep(0.05)
                    else:
                        self.wfile.write(encoded)
                except (BrokenPipeError, ConnectionResetError):
                    pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            yield f'http://127.0.0.1:{server.server_port}/v1'
        finally:
            owner.release_vision.set(); server.shutdown(); server.server_close(); thread.join(2)


async def fixture(tmp_path, monkeypatch):
    """Build an independent audio-bearing offset-PTS video and analyze its real bytes through FFmpeg."""
    monkeypatch.setattr(settings, 'production_media_root', str(tmp_path / 'media'))
    monkeypatch.setattr(settings, 'production_probe_executable', str(FFMPEG))
    engine = create_async_engine('sqlite+aiosqlite:///' + (tmp_path / 'understanding.db').as_posix())
    @event.listens_for(engine.sync_engine, 'connect')
    def configure(connection, _record):
        """Exercise the same ownership constraints and writer contention protection as local production."""
        connection.execute('PRAGMA foreign_keys=ON'); connection.execute('PRAGMA busy_timeout=5000'); connection.execute('PRAGMA journal_mode=WAL')
    tables = [model.__table__ for model in (Project, ProductionMedia, ReverseAnalysis, DnaDraft, DnaSnapshot, UnderstandingConfig, UnderstandingJob, UnderstandingAttempt)]
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
    await migrate_video_understanding(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as db:
        db.add_all([Project(id='p', name='Understanding fixture', style='真人古装', visual_style='现实'), Project(id='other', name='Other fixture', style='真人古装', visual_style='现实')]); await db.commit()
    original = tmp_path / 'filename-must-never-be-a-character-name.mp4'
    subprocess.run([str(FFMPEG), '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=96x64:rate=12:duration=2', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=16000:duration=2', '-c:v', 'libx264', '-threads', '1', '-bf', '0', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', '-output_ts_offset', '2', str(original)], check=True, timeout=20)
    async with factory() as db:
        media = (await store_batch(db, 'p', [(original.name, original.read_bytes())]))[0].id
    analysis = await create_analysis(factory, 'p', media, 'real-decoded-fixture')
    assert await run_analysis(factory, analysis, str(FFMPEG))
    return engine, factory, analysis


async def configured(factory, analysis, base, *, transcribe=True):
    """Choose only a fresh fixture authorization reference; no legacy key/table is accessed."""
    worker = UnderstandingWorker(factory, str(FFMPEG), environment={'DNA_UNDERSTANDING_TEST_TOKEN': 'fixture-only-token'})
    body = UnderstandingConnectionWrite(expected_revision=0, enabled=True, base_url=base, vision_model='fixture-vision', transcribe_audio=transcribe, audio_model='fixture-asr', credential_env='DNA_UNDERSTANDING_TEST_TOKEN')
    await service.save_connection(factory, 'p', body, worker)
    intent = UnderstandingCreate(analysis_id=analysis, expected_analysis_revision=1, expected_connection_revision=1, request_key='fixture-understanding')
    identifier = await service.create_job(factory, 'p', intent, worker)
    return worker, identifier, intent


@pytest.mark.asyncio
async def test_actual_http_media_clock_candidate_and_idempotent_dna_import(tmp_path, monkeypatch):
    """Actual frame/WAV transport creates an inert candidate; only matching reviewed cuts permit a new draft."""
    engine, factory, analysis = await fixture(tmp_path, monkeypatch)
    provider = ModelFixture()
    try:
        with provider.server() as base:
            worker, identifier, intent = await configured(factory, analysis, base)
            assert await service.create_job(factory, 'p', intent, worker) == identifier
            assert await worker.run_job(identifier)
            async with factory() as db:
                view = await service.job_view(db, 'p', identifier)
                assert await db.scalar(select(func.count()).select_from(DnaDraft)) == 0
            assert view['state'] == 'succeeded' and len(provider.calls) == 2, view['reason']
            assert view['document']['human_reviewed'] is False and view['document']['reviewer'] == ''
            assert view['document']['shots'][0]['action']['status'] == 'inferred'
            assert view['document']['shots'][0]['dialogues'] == [] and len(view['transcripts']) == 1
            assert view['source']['frames'][0]['pts'] > 0 and view['source']['terminal_time_known'] is False
            segment = view['transcripts'][0]
            assert segment['video_start_us'] == 200000 + view['source']['audio_origin_relative_us']
            assert segment['video_end_us'] == 800000 + view['source']['audio_origin_relative_us']
            receipt = view['attempts'][0]['input_receipt']
            vision = json.loads(provider.calls[1]['body'])
            images = [item['image_url']['url'] for item in vision['messages'][1]['content'] if item['type'] == 'image_url']
            assert [hashlib.sha256(base64.b64decode(value.split(',', 1)[1])).hexdigest() for value in images] == [frame['sha256'] for frame in receipt['frames']]
            assert [frame['index'] for frame in receipt['frames']] == sample_indices(view['source'], 24)
            assert 'filename-must-never' not in provider.calls[1]['body'].decode()
            multipart = BytesParser(policy=policy.default).parsebytes(('Content-Type: ' + provider.calls[0]['content_type'] + '\r\nMIME-Version: 1.0\r\n\r\n').encode() + provider.calls[0]['body'])
            audio = next(part.get_payload(decode=True) for part in multipart.iter_parts() if part.get_param('name', header='content-disposition') == 'file')
            assert hashlib.sha256(audio).hexdigest() == receipt['audio']['sha256']
            with pytest.raises(HTTPException):
                await service.create_candidate_draft(factory, 'p', identifier, 1)
            await edit_cuts(factory, 'p', analysis, view['source']['cut_indices'], 1)
            draft_id = await service.create_candidate_draft(factory, 'p', identifier, 2)
            async with factory() as db:
                draft = await db.get(DnaDraft, draft_id)
                edited = deepcopy(draft.document); edited['title'] = 'Human edits preserved after import'
            await dna.patch_draft(factory, 'p', draft_id, DnaPatch(expected_revision=1, document=edited))
            assert await service.create_candidate_draft(factory, 'p', identifier, 2) == draft_id
            async with factory() as db:
                assert (await db.get(DnaDraft, draft_id)).document['title'] == edited['title']
                assert await db.scalar(select(func.count()).select_from(DnaDraft)) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_untimed_asr_fails_without_vision_or_draft_and_explicit_retry_retains_attempt(tmp_path, monkeypatch):
    """A text-only ASR response never becomes fabricated shot dialogue or an automatic retry."""
    engine, factory, analysis = await fixture(tmp_path, monkeypatch)
    provider = ModelFixture(); provider.audio_mode = 'untimed'
    try:
        with provider.server() as base:
            worker, identifier, _intent = await configured(factory, analysis, base)
            await worker.run_job(identifier)
            async with factory() as db:
                failed = await service.job_view(db, 'p', identifier)
                assert failed['state'] == 'failed' and failed['document'] is None
                assert '逐段时标' in failed['reason'] and len(provider.calls) == 1
                assert await db.scalar(select(func.count()).select_from(DnaDraft)) == 0
            await worker.tick()
            assert len(provider.calls) == 1
            provider.audio_mode = 'timed'
            await service.retry_job(factory, 'p', identifier, 1, worker)
            await worker.run_job(identifier)
            async with factory() as db:
                view = await service.job_view(db, 'p', identifier)
                assert view['state'] == 'succeeded' and view['attempt'] == 2
                assert [item['state'] for item in view['attempts']] == ['succeeded', 'failed']
                assert len(provider.calls) == 3
                with pytest.raises(IntegrityError):
                    await db.execute(update(UnderstandingAttempt).where(UnderstandingAttempt.job_id == identifier, UnderstandingAttempt.attempt == 1).values(reason='overwrite old failure'))
                await db.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unsent_frame_is_rejected_and_source_identity_is_frozen(tmp_path, monkeypatch):
    """Structurally plausible model content cannot cite an original frame that was never sent."""
    engine, factory, analysis = await fixture(tmp_path, monkeypatch)
    provider = ModelFixture(); provider.vision_mode = 'unsent_frame'
    try:
        with provider.server() as base:
            worker, identifier, _intent = await configured(factory, analysis, base, transcribe=False)
            await worker.run_job(identifier)
            async with factory() as db:
                view = await service.job_view(db, 'p', identifier)
                assert view['state'] == 'failed' and view['document'] is None
                assert len(provider.calls) == 1
                with pytest.raises(HTTPException) as denied:
                    await service.owned_job(db, 'other', identifier)
                assert denied.value.status_code == 404
                with pytest.raises(IntegrityError):
                    await db.execute(update(UnderstandingJob).where(UnderstandingJob.id == identifier).values(source={'sha256': 'fake'}))
                await db.rollback()
            with pytest.raises(HTTPException):
                await service.create_candidate_draft(factory, 'p', identifier, 1)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_queued_and_inflight_cancellation_and_interrupted_attempt_are_not_replayed(tmp_path, monkeypatch):
    """Cancel both before and after real HTTP transmission; interruption requires an explicit retry."""
    engine, factory, analysis = await fixture(tmp_path, monkeypatch)
    provider = ModelFixture(); provider.delay_vision = True
    try:
        with provider.server() as base:
            worker, identifier, _intent = await configured(factory, analysis, base, transcribe=False)
            await service.cancel_job(factory, 'p', identifier, 1)
            assert not await worker.run_job(identifier) and provider.calls == []
            await service.retry_job(factory, 'p', identifier, 1, worker)
            running = asyncio.create_task(worker.run_job(identifier))
            assert await asyncio.to_thread(provider.vision_received.wait, 10)
            await service.cancel_job(factory, 'p', identifier, 2)
            await asyncio.wait_for(running, 3)
            provider.release_vision.set()
            async with factory() as db:
                view = await service.job_view(db, 'p', identifier)
                assert view['state'] == 'cancelled' and view['document'] is None
                assert await db.scalar(select(func.count()).select_from(DnaDraft)) == 0
            await service.retry_job(factory, 'p', identifier, 2, worker)
            assert await claim_job(factory, identifier) is not None
            async with factory() as db:
                await db.execute(update(UnderstandingJob).where(UnderstandingJob.id == identifier).values(lease_until=0)); await db.commit()
            await worker.tick()
            async with factory() as db:
                view = await service.job_view(db, 'p', identifier)
                assert view['state'] == 'failed' and view['attempt'] == 3 and len(provider.calls) == 1
                assert '不会自动重试' in view['reason']
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_actual_api_setup_cas_availability_and_cross_project_access(tmp_path, monkeypatch):
    """Use actual route contracts while a missing connection remains honestly unavailable."""
    engine, factory, analysis = await fixture(tmp_path, monkeypatch)
    worker = UnderstandingWorker(factory, str(FFMPEG), enabled=False, environment={})
    app = FastAPI(); app.include_router(router); app.state.understanding_worker = worker
    app.dependency_overrides[get_understanding_factory] = lambda: factory
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://local') as client:
            base = '/video-understanding/projects/p'
            response = await client.get(base + '/connection')
            assert response.status_code == 200 and response.json()['data']['available'] is False
            value = {'expected_revision': 0, 'enabled': True, 'base_url': 'http://127.0.0.1:9999/v1', 'vision_model': 'local-vision', 'transcribe_audio': False}
            saved = await client.put(base + '/connection', json=value)
            assert saved.status_code == 200 and saved.json()['data']['revision'] == 1 and saved.json()['data']['available'] is False
            assert (await client.put(base + '/connection', json={**value, 'vision_model': 'stale-overwrite'})).status_code == 409
            assert (await client.post(base + '/jobs', json={'analysis_id': analysis, 'expected_analysis_revision': 1, 'expected_connection_revision': 1, 'request_key': 'disabled'})).status_code == 503
            invalid = await client.put(base + '/connection', json={**value, 'expected_revision': 1, 'credential_env': 'EXISTING_PROVIDER_API_KEY'})
            assert invalid.status_code == 422
    finally:
        await engine.dispose()


def test_remote_policy_and_untimed_or_out_of_audio_timestamps():
    """Remote opt-in and real audio bounds remain enforced independently of any model account."""
    with pytest.raises(UnderstandingFailure):
        endpoint_base('https://example.com/v1', allow_remote=False)
    with pytest.raises(UnderstandingFailure):
        endpoint_base('http://127.0.0.1:8888/v1?key=not-allowed', allow_remote=False)
    source = {'frames': [{'relative_us': 0}, {'relative_us': 40000}, {'relative_us': 80000}], 'audio_origin_relative_us': -20000}
    receipt = {'sample_count': 16000, 'sample_rate': 16000}
    mapped = transcripts_from_response({'segments': [{'start': 0.02, 'end': 0.09, 'text': '待核对'}]}, source, receipt)[0]
    assert mapped['video_start_us'] == 0 and mapped['start_frame'] == 0 and mapped['end_frame'] == 2
    with pytest.raises(UnderstandingFailure):
        transcripts_from_response({'segments': [{'start': 0, 'end': 2, 'text': '超过实际音轨'}]}, source, receipt)


@pytest.mark.asyncio
async def test_redirect_is_not_followed_and_slow_stream_has_absolute_deadline(tmp_path, monkeypatch):
    """A 302 JSON body is not a candidate, and steady slow bytes cannot bypass the whole-job deadline."""
    engine, factory, analysis = await fixture(tmp_path, monkeypatch)
    provider = ModelFixture(); provider.vision_mode = 'redirect'
    try:
        with provider.server() as base:
            worker, identifier, _intent = await configured(factory, analysis, base, transcribe=False)
            await worker.run_job(identifier)
            async with factory() as db:
                view = await service.job_view(db, 'p', identifier)
                assert view['state'] == 'failed' and '重定向' in view['reason'] and view['document'] is None
                assert len(provider.calls) == 1 and provider.calls[0]['path'] == '/v1/chat/completions'
                source, connection = view['source'], view['connection']
            await prepare_inputs(factory, 'p', source, connection, str(FFMPEG))
            provider.vision_mode = 'slow'
            await service.retry_job(factory, 'p', identifier, 1, worker)
            worker.deadline_seconds = 1
            started = time.monotonic()
            await worker.run_job(identifier)
            assert time.monotonic() - started < 3
            async with factory() as db:
                view = await service.job_view(db, 'p', identifier)
                assert view['state'] == 'failed' and '执行时限' in view['reason'] and view['document'] is None
                assert len(provider.calls) == 2 and await db.scalar(select(func.count()).select_from(DnaDraft)) == 0
    finally:
        await engine.dispose()
