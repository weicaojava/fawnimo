"""Actual fixed media bytes through an owned ASGI route; no public hosting or real provider is qualified here."""
import asyncio
from contextlib import asynccontextmanager
import time
from types import SimpleNamespace
from urllib.parse import urlsplit
import uuid

from fastapi import FastAPI
import httpx
import pytest
from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError

from app.api.v1.routes.execution_reference_media import hide_reference_delivery_tokens, router
from app.models.execution import ExecutionJob, SubmissionAttempt
from app.models.model_charge import ModelCallCharge
from app.models.video_reference_inputs import ExecutionReferenceDelivery
from app.services.worker.execution_budget import transaction
from app.services.worker.model_charges import prepare_call
from app.services.worker.reference_delivery import (
    ReferenceMediaDelivery, ScopedReferenceResponse, install_reference_deliveries, prepare_reference_deliveries,
)
from tests.test_verified_reference_video import authored_video
from tests.test_video_reference_schema import actual_reference_job


@asynccontextmanager
async def delivery_client(tmp_path, monkeypatch, content):
    """Build a real prepared ledger attempt and read-only capability in a disposable app/database, with no model POST."""
    async with actual_reference_job(tmp_path, monkeypatch, video_content=content) as (factory, job_id, prepared, path, _content):
        now = [time.time()]
        origin = 'http://127.0.0.1:18123'
        wire, intents = prepare_reference_deliveries(prepared, job_id, origin, trusted_local_origin=origin, clock=lambda: now[0])
        attempt = uuid.uuid4().hex
        async with factory() as db:
            async with transaction(db):
                charge = await db.get(ModelCallCharge, job_id)
                await prepare_call(db, project_id='project-a', charge_id=job_id, binding_hash=charge.binding_hash)
                await db.execute(update(ExecutionJob).where(ExecutionJob.id == job_id).values(
                    state='submitting', lease_token='authored-lease', lease_until=now[0]+90))
                db.add(SubmissionAttempt(id=attempt, job_id=job_id, request_hash=(await db.get(ExecutionJob, job_id)).request_hash,
                    state='prepared'))
                await db.flush()
                await install_reference_deliveries(db, await db.get(ExecutionJob, job_id), attempt, intents)
        service = ReferenceMediaDelivery(factory, clock=lambda: now[0])
        app = FastAPI()
        app.include_router(router, prefix='/api/v1')
        app.middleware('http')(hide_reference_delivery_tokens)
        app.state.execution_runtime = SimpleNamespace(worker=SimpleNamespace(native_accounting=SimpleNamespace(
            reference_delivery=service, reference_delivery_available=True)))
        observed = []

        async def scope_spy(scope, receive, send):
            """Inspect the same ASGI scope an access logger sees after request handling, without printing any token."""
            await app(scope, receive, send)
            observed.append(scope.get('query_string'))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=scope_spy), base_url=origin) as client:
            yield client, factory, job_id, wire[0].delivery_url, intents[0], service, now, path, observed, app


@pytest.mark.asyncio
async def test_actual_owned_video_bytes_head_and_ranges_keep_token_out_of_log_scope(tmp_path, monkeypatch, authored_video):
    """The capability returns exact decoded original bytes, not a mutable URL or a path; reads never make a new fee hold."""
    original = authored_video()
    async with delivery_client(tmp_path, monkeypatch, original) as (client, factory, job_id, url, intent, service, _now, _path, observed, app):
        response = await client.get(url)
        assert response.status_code == 200 and response.content == original
        assert response.headers['content-type'] == 'video/mp4' and response.headers['cache-control'] == 'no-store'
        head = await client.head(url)
        assert head.status_code == 200 and head.content == b'' and int(head.headers['content-length']) == len(original)
        partial = await client.get(url, headers={'Range': 'bytes=0-31'})
        assert partial.status_code == 206 and partial.content == original[:32]
        suffix = await client.get(url, headers={'Range': 'bytes=-8'})
        assert suffix.status_code == 206 and suffix.content == original[-8:]
        rejected = await client.get(url, headers={'Range': 'bytes=0-1,4-5'})
        assert rejected.status_code == 416
        assert observed == [b'']*5 and not service.lane.locked()
        assert intent.token not in repr(intent) and intent.token not in str(response.headers)
        assert not any('execution-reference-media' in path for path in app.openapi()['paths'])
        async with factory() as db:
            delivery = await db.get(ExecutionReferenceDelivery, intent.id)
            assert delivery.token_sha256 != intent.token and delivery.access_count == 5
            assert (await db.execute(text("SELECT reserved_minor,spent_minor FROM execution_budgets WHERE project_id='project-a'"))).one() == (12, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['wrong-token', 'expired', 'cancelled', 'changed-file', 'unavailable-ingress'])
async def test_invalid_capability_or_changed_original_never_yields_media(tmp_path, monkeypatch, authored_video, boundary):
    """A previous URL cannot authorize a changed file, cancelled job, expired window or another token."""
    original = authored_video()
    async with delivery_client(tmp_path, monkeypatch, original) as (client, factory, job_id, url, intent, _service, now, path, _observed, app):
        if boundary == 'wrong-token': url = urlsplit(url).path+'?token='+'0'*64
        if boundary == 'expired': now[0] += 3600
        if boundary == 'cancelled':
            async with factory() as db:
                await db.execute(update(ExecutionJob).where(ExecutionJob.id == job_id).values(cancel_requested=True))
                await db.commit()
        if boundary == 'changed-file': path.write_bytes(original[:-1]+b'X')
        if boundary == 'unavailable-ingress':
            app.state.execution_runtime.worker.native_accounting.reference_delivery_available = False
        response = await client.get(url)
        assert response.status_code == (409 if boundary == 'changed-file' else 404)
        assert response.content != original


@pytest.mark.asyncio
async def test_delivery_access_limit_survives_new_service_instance(tmp_path, monkeypatch, authored_video):
    """Restarting an in-memory reader cannot reset the persisted16-access scope of an already issued URL."""
    async with delivery_client(tmp_path, monkeypatch, authored_video()) as (client, factory, _job, url, _intent, _service, now, _path, _observed, app):
        for _index in range(16):
            assert (await client.head(url)).status_code == 200
        app.state.execution_runtime.worker.native_accounting.reference_delivery = ReferenceMediaDelivery(factory, clock=lambda: now[0])
        assert (await client.get(url)).status_code == 404


@pytest.mark.asyncio
async def test_sql_cannot_extend_reassign_reset_or_replace_a_delivery_capability(tmp_path, monkeypatch, authored_video):
    """A direct SQL mutation cannot extend original media access beyond the prepared attempt's immutable scope."""
    async with delivery_client(tmp_path, monkeypatch, authored_video()) as (_client, factory, _job, _url, intent, _service, _now, _path, _observed, _app):
        statements = ["UPDATE execution_reference_deliveries SET expires_at=expires_at+1 WHERE id=:id",
            "UPDATE execution_reference_deliveries SET project_id='project-b' WHERE id=:id",
            'UPDATE execution_reference_deliveries SET access_count=0 WHERE id=:id',
            'INSERT OR REPLACE INTO execution_reference_deliveries SELECT * FROM execution_reference_deliveries WHERE id=:id']
        for statement in statements:
            async with factory() as db:
                with pytest.raises(IntegrityError):
                    await db.execute(text(statement), {'id': intent.id})
                await db.rollback()


@pytest.mark.asyncio
async def test_response_lane_releases_when_actual_asgi_send_fails():
    """A disconnected recipient cannot strand the owned media lane for later requests."""
    lock = asyncio.Lock()
    await lock.acquire()
    response = ScopedReferenceResponse(b'authored', release=lock.release)

    async def failed_send(_message):
        """Simulate a real downstream failure at the response-send boundary."""
        raise ConnectionError('authored disconnect')
    with pytest.raises(ConnectionError):
        await response({'type': 'http', 'method': 'GET'}, None, failed_send)
    assert not lock.locked()
