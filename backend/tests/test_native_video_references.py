"""Coherent real native job/hold/HTTP/reference delivery/candidate flow with authored local services only."""
import asyncio
import base64
from contextlib import asynccontextmanager, contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import threading
from types import SimpleNamespace
from urllib.parse import urlsplit
import urllib.request

from fastapi import FastAPI, HTTPException
import pytest
from sqlalchemy import func, select, update
import uvicorn

from app.api.v1.routes.execution_reference_media import hide_reference_delivery_tokens, router
from app.config import settings
from app.core.contracts.model_accounting import ModelPriceConfiguration, ModelPriceEnvelope, ModelPriceRule
from app.core.contracts.seedance25 import MODEL_ID
from app.core.contracts.seedance_references import REFERENCE_PROTOCOL, VideoReferenceSelection
from app.core.contracts.video_runtime_setup import VideoRuntimeConfiguration
from app.models.execution import ExecutionJob
from app.models.execution_budget import ExecutionBudget
from app.models.llm import Model, Provider
from app.models.model_charge import ModelCallCharge
from app.models.production import ProductionMedia
from app.models.provider_connection_version import ProviderConnectionVersion
from app.models.studio import FileItem
from app.models.video_reference_inputs import VideoReferenceInputSet
from app.services.llm.connection_versions import migrate_connection_versions
from app.services.worker.durable_execution import DurableExecution
from app.services.worker.execution_budget import configure
from app.services.worker.execution_schema import migrate_execution
from app.services.worker.model_billing_schema import migrate_model_billing
from app.services.worker.model_charge_schema import migrate_model_charges
from app.services.worker.model_prices import ModelPriceRegistry
from app.services.worker.native_video_accounting import NativeVideoAccounting
from app.services.worker.native_video_charge_schema import migrate_native_video_charges
from app.services.worker.video_reference_schema import migrate_video_reference_inputs
from tests.test_verified_reference_video import authored_video
from tests.test_video_reference_inputs import owned_media

KEY = 'authored-native-reference-key'


@contextmanager
def model_server(candidate):
    """Own one loopback model endpoint and its output bytes; never accept an arbitrary host from a test body."""
    state = {'posts': [], 'reads': [], 'source_video': None, 'media_origin': None, 'output_auth': None}

    class Handler(BaseHTTPRequestHandler):
        """Exchange actual bounded native HTTP while retaining only authored payloads and boolean credential checks."""
        def log_message(self, *_args):
            """Do not log signed media capabilities or fake authorization headers."""
            return

        def reply(self, data, mime='application/json'):
            """Return exact fixture bytes and a distinct private response witness for the original POST."""
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            if self.command == 'POST':
                self.send_header('X-Request-Id', 'authored-native-reference-post')
            self.end_headers()
            try:
                self.wfile.write(data)
            except (ConnectionError, OSError):
                pass

        def do_POST(self):
            """Observe actual chosen image bytes or fetch the application's scoped video with no provider credentials."""
            assert self.path == '/api/v3/contents/generations/tasks'
            assert self.headers.get('Authorization') == 'Bearer '+KEY
            size = int(self.headers['Content-Length'])
            assert 0 < size <= 18*1024*1024
            body = json.loads(self.rfile.read(size))
            for item in body['content'][1:]:
                if item['type'] == 'image_url':
                    assert base64.b64decode(item['image_url']['url'].split(',', 1)[1], validate=True) == state['image']
                else:
                    target = item['video_url']['url']
                    assert target.startswith(state['media_origin']+'/api/v1/execution-reference-media/') and KEY not in target
                    with urllib.request.urlopen(target, timeout=5) as response:
                        fetched = response.read(32*1024*1024+1)
                    assert fetched == state['source_video']
                    state['source_video_fetched'] = True
            state['posts'].append(body)
            self.reply(json.dumps({'id': 'authored-reference-task-'+str(len(state['posts']))}).encode())

        def do_GET(self):
            """Serve original-account task metadata and a credential-free actual MP4 artifact."""
            if self.path == '/candidate.mp4':
                state['output_auth'] = self.headers.get('Authorization')
                self.reply(candidate, 'video/mp4')
                return
            assert self.path.startswith('/api/v3/contents/generations/tasks/')
            assert self.headers.get('Authorization') == 'Bearer '+KEY
            identity = self.path.rsplit('/', 1)[1]
            state['reads'].append(identity)
            self.reply(json.dumps({'id': identity, 'model': MODEL_ID, 'status': 'succeeded',
                'content': {'video_url': f'http://127.0.0.1:{self.server.server_port}/candidate.mp4'}}).encode())
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state['port'] = server.server_port
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@asynccontextmanager
async def live_reference_app(app):
    """Run only the owned material route in the test's same event loop and close its exact listener afterward."""
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    sock.listen(128)
    sock.setblocking(False)
    origin = 'http://127.0.0.1:'+str(sock.getsockname()[1])
    server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False, lifespan='off'))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        for _index in range(500):
            if server.started:
                break
            if task.done():
                await task
            await asyncio.sleep(0.01)
        assert server.started
        yield origin
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=5)
        sock.close()


def registry(quote):
    """Enable one authored tagged quote, never a real price or additional protocol."""
    return ModelPriceRegistry(ModelPriceConfiguration(enabled=True, rules=[ModelPriceRule(quote=quote, kinds=['native_video'])]))


@asynccontextmanager
async def reference_runtime(tmp_path, monkeypatch, authored_video, *, mode='frames', limit=100):
    """Compose actual finance, scheduler step, network children and material reads in one disposable standalone namespace."""
    candidate = authored_video(duration=4)
    async with owned_media(tmp_path, monkeypatch) as (factory, selected, path, image):
        monkeypatch.setattr(settings, 'production_probe_executable', os.environ['FAWNIMO_TEST_FFMPEG'])
        engine = factory.kw['bind']
        for migrate in (migrate_execution, migrate_connection_versions, migrate_model_charges,
                        migrate_model_billing, migrate_native_video_charges, migrate_video_reference_inputs):
            await migrate(engine)
        for project in ('project-a', 'project-b'):
            async with factory() as db:
                await configure(db, project, 'CNY', limit if project == 'project-a' else 100)
        if mode == 'video':
            source = authored_video()
            path = Path(settings.production_media_root)/'native-source.mp4'
            path.write_bytes(source)
            checksum = hashlib.sha256(source).hexdigest()
            async with factory() as db:
                db.add(FileItem(id='native-source', type='video', name='Authored source', storage_key='local-production/native-source.mp4'))
                await db.flush()
                db.add(ProductionMedia(id='native-source', project_id='project-a', name='Authored source', mime='video/mp4',
                    sha256=checksum, size=len(source), relative_path='native-source.mp4'))
                await db.commit()
            selected = VideoReferenceSelection.model_validate({'mode': 'reference', 'references': [
                {'media_id': 'native-source', 'expected_sha256': checksum, 'role': 'reference_video'}]})
        else:
            source = None
        app = FastAPI()
        app.include_router(router, prefix='/api/v1')
        app.middleware('http')(hide_reference_delivery_tokens)
        with model_server(candidate) as upstream:
            upstream.update(image=image, source_video=source)
            async with live_reference_app(app) as origin:
                upstream['media_origin'] = origin
                async with factory() as db:
                    db.add(Provider(id='native-reference-provider', name='volcengine',
                        base_url=f"http://127.0.0.1:{upstream['port']}/api/v3", api_key=KEY))
                    await db.flush()
                    db.add(Model(id='native-reference-model', name=MODEL_ID, category='video', provider_id='native-reference-provider',
                        params={'native_video_protocol': REFERENCE_PROTOCOL}))
                    await db.commit()
                async with factory() as db:
                    revision = (await db.get(ProviderConnectionVersion, 'native-reference-provider')).revision
                quote = ModelPriceEnvelope(model_id='native-reference-model', model_name=MODEL_ID, input_protocol=REFERENCE_PROTOCOL,
                    provider_id='native-reference-provider', provider_connection_version=revision, account_version='authored-account',
                    max_call_minor=12, source_note='Authored complete reference protocol upper bound; not a supplier tariff')
                config = VideoRuntimeConfiguration(enabled=True, model_id=quote.model_id, service_version='authored-reference-service',
                    reference_media_origin=origin if mode == 'video' else None)
                accountant = NativeVideoAccounting(factory, config, registry(quote), trusted_local_host='127.0.0.1',
                    trusted_local_port=upstream['port'], trusted_local_media_origin=origin if mode == 'video' else None,
                    reference_delivery_available=True)
                async with factory() as db:
                    _connection, _quote, _profile, policy = await accountant.current(db)
                accountant.allow_new_post = True
                worker = DurableExecution(None, factory=factory, native_accounting=accountant)
                runtime = SimpleNamespace(worker=worker, policies={MODEL_ID: policy}, allow_submission=True, running=True)
                app.state.execution_runtime = runtime
                snapshot = {'model_id': MODEL_ID, 'duration': 4, 'prompt': 'Self-authored geometry only',
                    'references': [{'media_id': item.media_id, 'kind': item.kind} for item in selected.references],
                    'video_reference': selected.model_dump(mode='json')}
                yield SimpleNamespace(factory=factory, quote=quote, accountant=accountant, worker=worker, runtime=runtime,
                    upstream=upstream, selection={'kind': 'direct', 'snapshot': snapshot}, path=path, candidate=candidate)


async def submit(value, key='authored-reference-action'):
    """Confirm only this fixture's original project and displayed complete tagged price."""
    return await value.accountant.admit('project-a', key, value.selection, submitted_by='local_operator', confirmed=True,
        price_fingerprint=value.quote.fingerprint, runtime=value.runtime)


async def finish(value, job_id):
    """Drive at most a bounded number of real worker operations, without waiting on automatic background scheduling."""
    for _index in range(6):
        async with value.factory() as db:
            job = await db.get(ExecutionJob, job_id)
            if job.state == 'ready':
                return job
            await db.execute(update(ExecutionJob).where(ExecutionJob.id == job_id).values(next_due=0))
            await db.commit()
        await value.worker.step(job_id)
    async with value.factory() as db:
        job = await db.get(ExecutionJob, job_id)
        assert job.state == 'ready', (job.state, job.reason)
        return job


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['frames', 'video'])
async def test_coherent_original_job_single_hold_real_http_delivery_and_candidate(tmp_path, monkeypatch, authored_video, mode):
    """The very same persisted job sends its actual conditions once and publishes the model fixture's actual immutable MP4."""
    async with reference_runtime(tmp_path, monkeypatch, authored_video, mode=mode) as value:
        accepted = await submit(value)
        job_id = accepted['job_ids'][0]
        ready = await finish(value, job_id)
        assert len(value.upstream['posts']) == 1 and value.upstream['output_auth'] is None
        assert value.upstream['posts'][0]['ratio'] == ('adaptive' if mode == 'frames' else '9:16')
        if mode == 'video':
            assert value.upstream['source_video_fetched']
        async with value.factory() as db:
            header = await db.get(VideoReferenceInputSet, job_id)
            charge = await db.get(ModelCallCharge, job_id)
            media = await db.get(ProductionMedia, ready.media_id)
            budget = await db.get(ExecutionBudget, 'project-a')
            assert header.sealed and header.reference_count == (2 if mode == 'frames' else 1)
            assert charge.dispatch_state == 'accepted' and charge.provider_request_id == 'authored-native-reference-post'
            assert media.project_id == 'project-a' and media.sha256 == hashlib.sha256(value.candidate).hexdigest()
            assert budget.reserved_minor == 12 and budget.spent_minor == 0
            assert (await db.get(ExecutionBudget, 'project-b')).reserved_minor == 0


@pytest.mark.asyncio
async def test_old_text_quote_cannot_admit_reference_inputs_or_reuse_the_old_confirmation(tmp_path, monkeypatch, authored_video):
    """Changing the model flag never promotes its old text-only price or creates a partial task/hold."""
    async with reference_runtime(tmp_path, monkeypatch, authored_video) as value:
        raw = value.quote.model_dump()
        raw.pop('input_protocol')
        old = ModelPriceEnvelope.model_validate(raw)
        value.accountant.registry = registry(old)
        with pytest.raises(HTTPException) as error:
            await value.accountant.admit('project-a', 'unpriced-ref', value.selection, submitted_by='local_operator', confirmed=True,
                price_fingerprint=old.fingerprint, runtime=value.runtime)
        assert error.value.status_code == 503
        value.accountant.registry = registry(value.quote)
        with pytest.raises(HTTPException) as error:
            await value.accountant.admit('project-a', 'stale-ref-price', value.selection, submitted_by='local_operator', confirmed=True,
                price_fingerprint=old.fingerprint, runtime=value.runtime)
        assert error.value.status_code == 409
        async with value.factory() as db:
            assert await db.scalar(select(func.count()).select_from(ExecutionJob)) == 0
            assert (await db.get(ExecutionBudget, 'project-a')).reserved_minor == 0
        assert value.upstream['posts'] == []


@pytest.mark.asyncio
async def test_changed_queued_source_never_posts_and_releases_only_the_known_unsent_hold(tmp_path, monkeypatch, authored_video):
    """The original input proof is rechecked after admission before any prepare-call or network operation."""
    async with reference_runtime(tmp_path, monkeypatch, authored_video) as value:
        job_id = (await submit(value))['job_ids'][0]
        value.path.write_bytes(b'changed authored input')
        await value.worker.step(job_id)
        async with value.factory() as db:
            job = await db.get(ExecutionJob, job_id)
            charge = await db.get(ModelCallCharge, job_id)
            assert job.state == 'cancelled' and charge.dispatch_state == 'not_sent'
            assert (await db.get(ExecutionBudget, 'project-a')).reserved_minor == 0
        assert value.upstream['posts'] == []


@pytest.mark.asyncio
async def test_full_budget_replay_precedes_missing_source_current_price_and_disabled_runtime(tmp_path, monkeypatch, authored_video):
    """A lost-ack recovery returns the actual original job instead of decoding unavailable current inputs or reserving again."""
    async with reference_runtime(tmp_path, monkeypatch, authored_video, limit=12) as value:
        accepted = await submit(value)
        await finish(value, accepted['job_ids'][0])
        value.path.unlink()
        value.accountant.registry = ModelPriceRegistry()
        value.runtime.allow_submission = False
        assert await submit(value) == accepted
        assert len(value.upstream['posts']) == 1
        async with value.factory() as db:
            assert await db.scalar(select(func.count()).select_from(ExecutionJob)) == 1
            assert (await db.get(ExecutionBudget, 'project-a')).reserved_minor == 12


@pytest.mark.asyncio
async def test_accepted_original_query_survives_removing_new_generation_flag_and_current_price(tmp_path, monkeypatch, authored_video):
    """Only the sealed original connection controls an accepted task; a current model-purpose switch does not resend it."""
    async with reference_runtime(tmp_path, monkeypatch, authored_video) as value:
        job_id = (await submit(value))['job_ids'][0]
        await value.worker.step(job_id)
        async with value.factory() as db:
            await db.execute(update(Model).where(Model.id == value.quote.model_id).values(params={}))
            await db.commit()
        value.accountant.registry = ModelPriceRegistry()
        value.accountant.allow_new_post = False
        await finish(value, job_id)
        assert len(value.upstream['posts']) == 1 and value.upstream['reads']
