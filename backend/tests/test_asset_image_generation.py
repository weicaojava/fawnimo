"""Real loopback image HTTP and isolated SQLite/files; no external model or user source material."""
import asyncio
import base64
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import threading
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
import httpx
from PIL import Image
import pytest
from sqlalchemy import event, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1.routes.asset_image_generation import router, get_asset_image_factory
from app.config import settings
from app.core import local_storage
from app.core.db import Base
from app.dependencies import get_db
from app.models.asset_image_generation import AssetImageJob
from app.models.llm import Model, ModelSettings, Provider
from app.models.studio import Actor, ActorImage, Costume, CostumeImage, FileItem, Prop, PropImage, Scene, SceneImage
from app.services.asset_image_generation import AssetImageGenerationRuntime, migrate_asset_image_generation, verified_bytes
from app.services.agent_execution import clock
from app.services.llm.connection_versions import migrate_connection_versions
from app.services.studio.files import build_download_response, delete_file, get_storage_info, read_file_bytes

KEY = 'image-fixture-only-key'
KINDS = {'actor': (Actor, ActorImage, 'actor_id'), 'costume': (Costume, CostumeImage, 'costume_id'), 'scene': (Scene, SceneImage, 'scene_id'), 'prop': (Prop, PropImage, 'prop_id')}


def authored_image(format='PNG'):
    """Create deterministic fixture bytes solely for protocol decoding and integrity checks."""
    stream = BytesIO(); Image.new('RGB', (256, 256), (40, 70, 100)).save(stream, format=format); return stream.getvalue()


@contextmanager
def model_server():
    """Implement one real compatible HTTP route with controllable base64, URL and failure outputs."""
    state = {'calls': [], 'gets': 0, 'mode': 'ok', 'received': threading.Event(), 'release': threading.Event(), 'png': authored_image(), 'jpeg': authored_image('JPEG')}

    class Handler(BaseHTTPRequestHandler):
        """Never log authorization values or assume a fake transport proves real model quality."""
        def log_message(self, *_args):
            """Keep request headers and synthetic input out of console logs."""

        def do_GET(self):
            """Count unexpected URL-download attempts; the supported adapter must never use this."""
            state['gets'] += 1; self.send_error(404)

        def do_POST(self):
            """Send actual PNG/JPEG bytes inside bounded b64_json over a local socket."""
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state['calls'].append({'path': self.path, 'payload': payload, 'authenticated': self.headers.get('Authorization') == 'Bearer ' + KEY})
            mode = state['mode']; state['received'].set()
            if mode == 'block':
                state['release'].wait(5)
            status = 503 if mode == 'fail' else 200
            result = {'data': [{'b64_json': base64.b64encode(state['jpeg'] if mode == 'jpeg' else state['png']).decode()}]}
            if mode == 'url': result = {'data': [{'url': f'http://127.0.0.1:{self.server.server_port}/forbidden.png'}]}
            if mode == 'bad64': result = {'data': [{'b64_json': 'not-valid!'}]}
            if mode == 'badimage': result = {'data': [{'b64_json': base64.b64encode(b'not an image').decode()}]}
            if mode == 'fail': result = {'error': {'message': KEY}}
            data = json.dumps(result).encode()
            try:
                self.send_response(status); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
            except (ConnectionError, OSError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start(); state['port'] = server.server_port
    try:
        yield state
    finally:
        state['release'].set(); server.shutdown(); server.server_close(); thread.join(timeout=2)


@asynccontextmanager
async def fixture_client(tmp_path, monkeypatch, state, workers=1):
    """Own every key, asset, file and SQLite row; S3 selection cannot redirect candidate reads."""
    monkeypatch.setattr(settings, 'local_storage_root', str(tmp_path / 'objects'))
    monkeypatch.setattr(settings, 'storage_backend', 's3')
    monkeypatch.setattr(settings, 's3_bucket_name', 'never-contact-this-fixture-bucket')
    engine = create_async_engine('sqlite+aiosqlite:///' + (tmp_path / 'images.db').as_posix())
    @event.listens_for(engine.sync_engine, 'connect')
    def configure(connection, _):
        """Use the same bounded SQLite write contention and FK behavior as local production."""
        connection.execute('PRAGMA foreign_keys=ON'); connection.execute('PRAGMA busy_timeout=5000')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await migrate_asset_image_generation(engine)
    await migrate_connection_versions(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    local_storage.upload('files/original.png', state['png'], 'image/png')
    slots = {}
    async with factory() as db:
        db.add(Provider(id='image-provider', name='openai', base_url=f"http://127.0.0.1:{state['port']}/v1", api_key=KEY, api_secret='', status='testing'))
        db.add(FileItem(id='original', type='image', name='Original reference', thumbnail='original-reference', storage_key='files/original.png'))
        await db.flush()
        db.add(Model(id='image-model', name='local-image-fixture', provider_id='image-provider', category='image', params={'asset_image_protocol': 'openai_b64_v1'}))
        await db.flush(); db.add(ModelSettings(id=1, default_image_model_id='image-model'))
        for kind, (parent, image_model, owner_key) in KINDS.items():
            db.add(parent(id=kind + '-asset', name='Fixture ' + kind, style='真人都市'))
            await db.flush()
            image = image_model(**{owner_key: kind + '-asset', 'file_id': 'original', 'quality_level': 'LOW', 'view_angle': 'FRONT'})
            db.add(image); await db.flush(); slots[kind] = image.id
        await db.commit()

    async def database():
        """Only a successful request commit makes a model job visible to the scanner."""
        async with factory() as db:
            try:
                yield db; await db.commit()
            except Exception:
                await db.rollback(); raise

    runtimes = [AssetImageGenerationRuntime(factory, poll_interval=.02, trusted_local_host='127.0.0.1', trusted_local_ports=frozenset({state['port']})) for _ in range(workers)]
    app = FastAPI(); app.include_router(router, prefix='/api/v1'); app.dependency_overrides[get_db] = database; app.dependency_overrides[get_asset_image_factory] = lambda: factory; app.state.asset_image_generation_runtime = runtimes[0]
    for runtime in runtimes: await runtime.start()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://image-app') as client:
            yield client, factory, slots, runtimes
    finally:
        for runtime in runtimes: await runtime.stop()
        await engine.dispose()


async def submit(client, kind, slot_id, key='image-1', **extra):
    """Authorize one actual image HTTP request through the production route."""
    return await client.post(f'/api/v1/asset-image-generation/assets/{kind}/{kind}-asset/slots/{slot_id}/jobs', json={'request_key': key, 'model_id': 'image-model', 'prompt': 'Fixture red coat in snow', 'size': '256x256', 'confirm_model_call': True, **extra})


async def terminal(client, job_id):
    """Wait for persisted candidate or honest failure, never equating admission with completion."""
    for _ in range(200):
        row = (await client.get('/api/v1/asset-image-generation/jobs/' + job_id)).json()['data']
        if row['status'] in {'candidate', 'adopted', 'failed', 'cancelled'}: return row
        await asyncio.sleep(.03)
    raise AssertionError('Image job did not become terminal')


async def adoption_body(client, kind, key='adopt-1'):
    """Use the actual current slot clock and file identity as the compare-and-swap token."""
    slot = (await client.get(f'/api/v1/asset-image-generation/assets/{kind}/{kind}-asset/slots')).json()['data'][0]
    return {'request_key': key, 'expected_file_id': slot['file_id'], 'expected_updated_at': slot['updated_at'], 'confirm_replace': True}


@pytest.mark.asyncio
async def test_all_four_assets_real_png_jpeg_candidates_and_explicit_adoption(tmp_path, monkeypatch):
    """Generate four actual files without auto-overwrite, then adopt only their exact own slot."""
    with model_server() as state:
        async with fixture_client(tmp_path, monkeypatch, state, workers=2) as (client, factory, slots, _):
            paths = set()
            for kind, slot_id in slots.items():
                state['mode'] = 'jpeg' if kind == 'scene' else 'ok'
                first, duplicate = await asyncio.gather(submit(client, kind, slot_id), submit(client, kind, slot_id))
                assert first.status_code == duplicate.status_code == 201
                assert first.json()['data']['id'] == duplicate.json()['data']['id']
                job = await terminal(client, first.json()['data']['id'])
                assert job['status'] == 'candidate' and job['file_id'] is None
                expected = state['jpeg'] if kind == 'scene' else state['png']
                content = await client.get(job['content_url'])
                assert content.content == expected and job['sha256'] == hashlib.sha256(expected).hexdigest()
                assert job['width'] == job['height'] == 256
                async with factory() as db:
                    image = await db.get(KINDS[kind][1], slot_id)
                    assert image.file_id == 'original'
                    paths.add((await db.get(AssetImageJob, job['id'])).storage_key)
                body = await adoption_body(client, kind)
                if kind == 'actor':
                    body['expected_updated_at'] = datetime.fromisoformat(body['expected_updated_at'].replace('Z', '+00:00')).astimezone(timezone(timedelta(hours=8))).isoformat()
                adopted = await client.post('/api/v1/asset-image-generation/jobs/' + job['id'] + '/adopt', json=body)
                assert adopted.status_code == 200, adopted.text
                saved = adopted.json()['data']
                assert saved['status'] == 'adopted' and saved['file_id'] != 'original'
                replay = await client.post('/api/v1/asset-image-generation/jobs/' + job['id'] + '/adopt', json=body)
                assert replay.json()['data']['file_id'] == saved['file_id']
                async with factory() as db:
                    _, raw = await read_file_bytes(db, file_id=saved['file_id'])
                    assert raw == expected
                    file = await db.get(FileItem, saved['file_id'])
                    assert file.name.endswith('.jpg' if kind == 'scene' else '.png')
                    download = await build_download_response(db, file_id=saved['file_id'])
                    assert download.headers['content-type'] == job['mime']
                    assert download.headers['content-disposition'].endswith('.jpg' if kind == 'scene' else '.png')
                    info = await get_storage_info(db, file_id=saved['file_id'])
                    assert info['etag'] == job['sha256'] and info['size'] == len(expected)
                    assert await db.get(FileItem, 'original') is not None
                    with pytest.raises(HTTPException) as protected:
                        await delete_file(db, file_id=saved['file_id'])
                    assert protected.value.status_code == 409
                assert KEY not in json.dumps(saved) and 'storage_key' not in saved
            assert len(paths) == 4 and len(state['calls']) == 4
            assert all(row['path'] == '/v1/images/generations' and row['authenticated'] and row['payload']['n'] == 1 and row['payload']['response_format'] == 'b64_json' and 'images' not in row['payload'] for row in state['calls'])
            assert local_storage.download('files/original.png') == state['png']


@pytest.mark.asyncio
async def test_candidate_adoption_cas_and_success_immutability_and_hash_protection(tmp_path, monkeypatch):
    """Two candidates cannot overwrite one stale slot token; SQL and bytes tampering are rejected."""
    with model_server() as state:
        async with fixture_client(tmp_path, monkeypatch, state) as (client, factory, slots, _):
            jobs = []
            for key in ('candidate-a', 'candidate-b'):
                response = await submit(client, 'actor', slots['actor'], key)
                jobs.append(await terminal(client, response.json()['data']['id']))
            body = await adoption_body(client, 'actor')
            responses = await asyncio.gather(*[client.post('/api/v1/asset-image-generation/jobs/' + job['id'] + '/adopt', json={**body, 'request_key': job['id']}) for job in jobs])
            assert sorted(response.status_code for response in responses) == [200, 409]
            async with factory() as db:
                row = await db.get(AssetImageJob, jobs[0]['id']); key, original = row.storage_key, await verified_bytes(row)
                for field, value in [('prompt', 'mutate identity'), ('sha256', '0' * 64)]:
                    with pytest.raises(IntegrityError):
                        await db.execute(update(AssetImageJob).where(AssetImageJob.id == jobs[0]['id']).values(**{field: value}))
                    await db.rollback()
            target = local_storage.storage_path(key); target.write_bytes(b'tampered')
            assert (await client.get(jobs[0]['content_url'])).status_code == 409
            target.write_bytes(original)
            with pytest.raises(HTTPException) as escape:
                await verified_bytes(SimpleNamespace(status='candidate', storage_key='../outside.png', id=jobs[0]['id'], sha256=jobs[0]['sha256']))
            assert escape.value.status_code == 409
            assert len(state['calls']) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['url', 'bad64', 'badimage'])
async def test_unsupported_or_invalid_image_response_never_creates_candidate(tmp_path, monkeypatch, mode):
    """URL-only results are not downloaded and invalid bytes cannot masquerade as successful images."""
    with model_server() as state:
        state['mode'] = mode
        async with fixture_client(tmp_path, monkeypatch, state) as (client, _, slots, _):
            response = await submit(client, 'prop', slots['prop'])
            job = await terminal(client, response.json()['data']['id'])
            assert job['status'] == 'failed' and job['content_url'] is None and job['sha256'] is None
            assert state['gets'] == 0 and len(state['calls']) == 1
            assert KEY not in json.dumps(job)
            assert (await client.post('/api/v1/asset-image-generation/jobs/' + job['id'] + '/adopt', json=await adoption_body(client, 'prop'))).status_code == 409


@pytest.mark.asyncio
async def test_image_failure_explicit_retry_cancel_and_expired_owner(tmp_path, monkeypatch):
    """A single failed/unknown call is never replayed automatically; cancelled or expired owners publish nothing."""
    with model_server() as state:
        async with fixture_client(tmp_path, monkeypatch, state) as (client, factory, slots, _):
            state['mode'] = 'fail'
            response = await submit(client, 'costume', slots['costume'])
            failed = await terminal(client, response.json()['data']['id'])
            assert failed['status'] == 'failed' and '503' in failed['error']
            await asyncio.sleep(.1); assert len(state['calls']) == 1
            state['mode'] = 'ok'
            retry = await client.post('/api/v1/asset-image-generation/jobs/' + failed['id'] + '/retry', json={'request_key': 'retry', 'acknowledge_duplicate_charge': True})
            candidate = await terminal(client, retry.json()['data']['id'])
            assert candidate['status'] == 'candidate' and candidate['attempt'] == 2 and candidate['retry_of'] == failed['id']
            for action in ('cancel', 'expire'):
                state['mode'] = 'block'; state['received'].clear(); state['release'].clear()
                response = await submit(client, 'costume', slots['costume'], action)
                job_id = response.json()['data']['id']
                for _ in range(100):
                    if state['received'].is_set(): break
                    await asyncio.sleep(.03)
                assert state['received'].is_set()
                if action == 'cancel':
                    assert (await client.post('/api/v1/asset-image-generation/jobs/' + job_id + '/cancel')).json()['data']['status'] == 'cancelled'
                else:
                    async with factory() as db:
                        await db.execute(update(AssetImageJob).where(AssetImageJob.id == job_id).values(lease_expires_at=clock() - timedelta(seconds=1))); await db.commit()
                state['release'].set()
                job = await terminal(client, job_id)
                assert job['status'] == ('cancelled' if action == 'cancel' else 'failed') and job['content_url'] is None
            assert len(state['calls']) == 4


@pytest.mark.asyncio
async def test_explicit_protocol_and_asset_scope_reject_before_network(tmp_path, monkeypatch):
    """References, non-opted-in models and cross-owner image IDs never reach HTTP."""
    with model_server() as state:
        async with fixture_client(tmp_path, monkeypatch, state) as (client, factory, slots, _):
            assert (await submit(client, 'actor', slots['actor'], images=['reference'])).status_code == 422
            assert (await submit(client, 'actor', 999999)).status_code == 404
            async with factory() as db:
                await db.execute(update(Model).where(Model.id == 'image-model').values(params={})); await db.commit()
            state_read = (await client.get('/api/v1/asset-image-generation/availability?model_id=image-model')).json()['data']
            assert not state_read['available'] and '单张图片生成' in state_read['message']
            assert (await submit(client, 'actor', slots['actor'])).status_code == 503
            assert state['calls'] == []
