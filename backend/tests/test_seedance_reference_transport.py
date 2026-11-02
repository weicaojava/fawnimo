"""Actual authored loopback creation and response-witness cancellation; no existing account or supplier is contacted."""
import asyncio
import base64
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest

from app.core.contracts.execution_control import RejectedSubmission
from app.core.contracts.seedance25 import Seedance25Configuration
from app.core.contracts.seedance_reference_profile import SeedanceReferenceConfiguration, checked_delivery_origin
from app.core.integrations.volcengine.seedance25 import Seedance25ProtocolError
from app.core.integrations.volcengine.seedance_reference import SeedanceReferenceTransport
from tests.test_seedance_reference_payload import image_proof, selection

KEY = 'authored-reference-transport-key'


@contextmanager
def own_server():
    """Record actual model-protocol HTTP only on a freshly allocated local endpoint owned by this test."""
    state = {'posts': [], 'reads': [], 'mode': 'valid'}

    class Handler(BaseHTTPRequestHandler):
        """Return bounded authored protocol responses and avoid retaining request URLs/authorization in logs."""
        def log_message(self, *_args):
            """Keep synthetic headers and future delivery capabilities out of incidental HTTP logs."""
            return

        def do_POST(self):
            """Retain the real JSON condition bytes before producing a single response witness."""
            assert self.path == '/api/v3/contents/generations/tasks'
            size = int(self.headers['Content-Length'])
            assert 0 < size <= 18*1024*1024
            state['posts'].append({'body': json.loads(self.rfile.read(size)),
                'authorization_matches': self.headers.get('Authorization') == 'Bearer '+KEY})
            raw = b'{malformed' if state['mode'] == 'malformed' else json.dumps({'id': 'authored-reference-task'}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Content-Type', 'application/json')
            self.send_header('X-Request-Id', 'authored-post-witness')
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (ConnectionError, OSError):
                pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state['port'] = server.server_port
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def configuration(state, **values):
    """Explicitly trust one test API port; no public configuration is allowed to infer this exception."""
    return SeedanceReferenceConfiguration(api_key=KEY, service_version='authored-service', account_version='authored-account',
        base_url=f"http://127.0.0.1:{state['port']}/api/v3", trusted_local_host='127.0.0.1',
        trusted_local_port=state['port'], download_hosts=frozenset({'127.0.0.1'}), **values)


def snapshot(selected):
    """Use only immutable caller choices; no actual image bytes or delivery secrets enter the durable snapshot."""
    return {'model_id': 'doubao-seedance-2-5-260628', 'duration': 4, 'prompt': '自作几何',
        'references': [{'media_id': item.media_id, 'kind': item.kind} for item in selected.references],
        'video_reference': selected.model_dump(mode='json')}


@pytest.mark.asyncio
async def test_actual_http_sends_exact_images_and_records_original_response_before_returning_handle():
    """Actual outgoing data URLs decode to chosen bytes and the callback observes the POST witness first."""
    proof = await image_proof()
    selected = selection([proof], ['first_frame'], 'first_frame')
    seen = []
    async def witnessed(value):
        """Stand in for the joined durable response-recording callback, not an actual bill or model quality claim."""
        seen.append(value)
    with own_server() as state:
        transport = SeedanceReferenceTransport(configuration(state), project_id='project-a', selection=selected,
            references=(proof,), on_submission_response=witnessed)
        assert await transport.submit(snapshot(selected), 'authored-attempt') == 'authored-reference-task'
        assert seen == ['authored-post-witness'] and len(state['posts']) == 1
        sent = state['posts'][0]
        assert sent['authorization_matches'] and sent['body']['ratio'] == 'adaptive'
        encoded = sent['body']['content'][1]['image_url']['url'].split(',', 1)[1]
        assert base64.b64decode(encoded, validate=True) == proof.content
        assert sent['body']['content'][1]['role'] == 'first_frame'


@pytest.mark.asyncio
async def test_actual_post_witness_survives_malformed_response_and_is_not_resent():
    """Later parsing failure cannot erase the real accepted-response witness or silently retry creation."""
    proof = await image_proof()
    selected = selection([proof], ['reference_image'])
    seen = []
    async def witnessed(value):
        """Preserve only a bounded original response identifier from the actual local POST."""
        seen.append(value)
    with own_server() as state:
        state['mode'] = 'malformed'
        transport = SeedanceReferenceTransport(configuration(state), project_id='project-a', selection=selected,
            references=(proof,), on_submission_response=witnessed)
        with pytest.raises(Seedance25ProtocolError):
            await transport.submit(snapshot(selected), 'authored-attempt')
        assert seen == ['authored-post-witness'] and len(state['posts']) == 1
        assert await transport.reconcile('authored-attempt') is None and len(state['posts']) == 1


@pytest.mark.asyncio
async def test_repeated_owner_cancel_joins_the_original_response_callback():
    """A second cancellation cannot abandon original POST evidence while leaving its DB writer to run later."""
    proof = await image_proof()
    selected = selection([proof], ['reference_image'])
    entered, release = asyncio.Event(), asyncio.Event()
    seen = []
    async def witnessed(value):
        """Hold an owned callback so repeated cancellation is exercised at the actual evidence boundary."""
        entered.set()
        await release.wait()
        seen.append(value)
    with own_server() as state:
        transport = SeedanceReferenceTransport(configuration(state), project_id='project-a', selection=selected,
            references=(proof,), on_submission_response=witnessed)
        task = asyncio.create_task(transport.submit(snapshot(selected), 'authored-attempt'))
        await asyncio.wait_for(entered.wait(), timeout=10)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert seen == ['authored-post-witness'] and len(state['posts']) == 1


@pytest.mark.asyncio
async def test_control_only_descriptor_and_mismatched_scope_never_create():
    """A historical-read transport has no prepared condition authority, and a different project cannot borrow bytes."""
    proof = await image_proof()
    selected = selection([proof], ['reference_image'])
    with own_server() as state:
        control = SeedanceReferenceTransport(configuration(state))
        with pytest.raises(RejectedSubmission):
            await control.submit(snapshot(selected), 'authored-attempt')
        wrong = SeedanceReferenceTransport(configuration(state), project_id='project-b', selection=selected, references=(proof,))
        with pytest.raises(RejectedSubmission):
            await wrong.submit(snapshot(selected), 'authored-attempt')
        assert state['posts'] == []


def test_reference_profile_hash_changes_for_delivery_policy_but_never_for_a_credential_value():
    """Profile identity includes every declared input boundary while keeping key material out of hashes/repr."""
    base = SeedanceReferenceConfiguration(api_key=KEY, service_version='authored-service', account_version='authored-account')
    assert replace(base, api_key='different-authored-key').profile_hash == base.profile_hash
    assert replace(base, input_delivery_origin='https://media.example.org').profile_hash != base.profile_hash
    old = Seedance25Configuration(api_key=KEY, service_version='authored-service', account_version='authored-account')
    assert old.profile_hash != base.profile_hash and old.binding_id != base.binding_id
    assert KEY not in repr(base)
    with pytest.raises(TypeError):
        SeedanceReferenceTransport(old)


@pytest.mark.parametrize('origin', ['http://media.example.org', 'https://localhost', 'https://127.0.0.1',
    'https://media.example.org/path', 'https://user:secret@media.example.org', 'https://media.example.org:444',
    'https://*.example.org', 'https://media.local', 'https://media.example.org?token=secret'])
def test_media_origin_cannot_be_inferred_from_an_api_local_exception_or_untrusted_url_parts(origin):
    """Production material delivery requires its own exact public origin; setting a provider loopback port cannot grant it."""
    with pytest.raises(ValueError):
        checked_delivery_origin(origin)
    assert checked_delivery_origin('http://127.0.0.1:18123', trusted_local_origin='http://127.0.0.1:18123') == 'http://127.0.0.1:18123'
