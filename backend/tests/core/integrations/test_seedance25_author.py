"""Author checks for the fixed S25A protocol; all credentials and media are fixtures."""
from __future__ import annotations

from dataclasses import replace
from email.utils import formatdate
import asyncio
import json
import traceback

import pytest

from app.core.contracts.execution_control import QueryBackoff, RejectedSubmission
from app.core.contracts.seedance25 import (
    ARK_BASE_URL, CREATE_PATH, DOWNLOAD_HOSTS, MODEL_ID, OUTPUT_PROFILE,
    PREPARATION_PROFILE_HASH, Seedance25Configuration,
)
from app.core.integrations.trusted_http import HttpResult
from app.core.integrations.volcengine.seedance25 import (
    Seedance25DurableTransport, Seedance25ProtocolError,
)
from app.core.integrations.volcengine.seedance25_payload import build_seedance25_body
from app.services.worker.seedance25_runtime import build_preparation_runtime, preparation_policy

FAKE_KEY = 'S25A_FAKE_KEY_NEVER_REAL'
SIGNED_MARKER = 'S25A_FAKE_SIGNED_TOKEN'
REMOTE = 'cgt-software-fixture-1'
MEDIA_URL = ('https://ark-acg-cn-beijing.tos-cn-beijing.volces.com/software.mp4'
             '?signature=' + SIGNED_MARKER)


def snapshot(**changes) -> dict:
    """Create a normal frozen-style request without provider configuration fields."""
    return {'model_id': MODEL_ID, 'duration': 4, 'references': [],
            'prompt': '  软件夹具，保留全文。\n  ', 'prompt_revision': 'fixture-revision', **changes}


def configuration(**changes) -> Seedance25Configuration:
    """Use an obvious fake credential; no environment or user account is consulted."""
    return Seedance25Configuration(
        api_key=FAKE_KEY, service_version='FIXTURE_SERVICE_V1',
        account_version='FIXTURE_ACCOUNT_V1', **changes,
    )


def metadata(status='succeeded', **changes) -> dict:
    """Include intentionally sensitive-looking unused fields to test result projection."""
    return {'id': REMOTE, 'model': MODEL_ID, 'status': status,
            'content': {'video_url': MEDIA_URL},
            'error': {'message': FAKE_KEY + SIGNED_MARKER}, **changes}


def response(body, *, status=200, headers=None) -> HttpResult:
    """Encode one fake provider response without logging its body."""
    encoded = body if isinstance(body, bytes) else json.dumps(body).encode()
    return HttpResult(status, headers or {}, encoded)


def test_fixed_body_preserves_original_prompt_and_omits_local_identity():
    """The preview/body is deterministic and contains only the documented narrow request."""
    original = snapshot()
    body = build_seedance25_body(original)
    assert body == {'model': MODEL_ID, 'content': [{'type': 'text', 'text': original['prompt']}],
                    'ratio': '9:16', 'resolution': '480p', 'duration': 4,
                    'output_format': 'mp4', 'generate_audio': False, 'watermark': True}
    body['content'][0]['text'] = 'changed copy'
    assert original['prompt'] != 'changed copy'
    assert build_seedance25_body(original)['content'][0]['text'] == original['prompt']
    with pytest.raises(TypeError):
        OUTPUT_PROFILE['ratio'] = '16:9'


@pytest.mark.parametrize('changes', [
    {'duration': True}, {'duration': 4.0}, {'duration': 3}, {'duration': 31},
    {'duration': -1}, {'model_id': MODEL_ID + '-other'}, {'prompt': ''},
    {'prompt': ' '}, {'prompt': 'x' * 32001}, {'prompt': '\ud800'},
    {'references': [{'media_id': 'owned-image', 'kind': 'image'}]},
    {'references': None}, {'references': ()}, {'prompt_revision': 1},
    {'endpoint': 'https://untrusted.invalid'}, {'authorization': FAKE_KEY},
    {'quote_minor': 0}, {'ratio': '16:9'}, {'model_qualified': True},
])
def test_unsupported_or_injected_request_never_builds(changes):
    """Unsupported content and imported qualification/options fail before any request."""
    with pytest.raises((ValueError, TypeError)) as caught:
        build_seedance25_body(snapshot(**changes))
    assert FAKE_KEY not in str(caught.value)


def test_configuration_fingerprint_covers_versions_and_origin_but_not_key():
    """A credential rotation alone does not fingerprint secret bytes; versions must change."""
    cfg = configuration()
    assert len(cfg.profile_hash) == 64 and cfg.profile_hash != PREPARATION_PROFILE_HASH
    assert replace(cfg, api_key='ANOTHER_FAKE_KEY').profile_hash == cfg.profile_hash
    assert replace(cfg, service_version='FIXTURE_SERVICE_V2').profile_hash != cfg.profile_hash
    assert replace(cfg, account_version='FIXTURE_ACCOUNT_V2').profile_hash != cfg.profile_hash
    assert cfg.profile_hash in cfg.binding_id
    assert FAKE_KEY not in repr(cfg)
    local = configuration(
        base_url='http://127.0.0.1:18765/api/v3', download_hosts=frozenset({'127.0.0.1'}),
        trusted_local_host='127.0.0.1', trusted_local_port=18765,
    )
    assert local.profile_hash != cfg.profile_hash


@pytest.mark.parametrize('changes', [
    {'base_url': ARK_BASE_URL + '/extra'},
    {'base_url': ARK_BASE_URL + '?route=x'},
    {'base_url': 'http://ark.cn-beijing.volces.com/api/v3'},
    {'download_hosts': frozenset({'other.example'})},
    {'base_url': 'http://127.0.0.1:18765/api/v3'},
    {'base_url': 'http://127.0.0.1:18765/api/v3', 'trusted_local_host': '127.0.0.1',
     'trusted_local_port': 18766, 'download_hosts': frozenset({'127.0.0.1'})},
    {'base_url': 'http://10.0.0.1:18765/api/v3', 'trusted_local_host': '10.0.0.1',
     'trusted_local_port': 18765, 'download_hosts': frozenset({'10.0.0.1'})},
])
def test_configuration_cannot_expand_network_trust(changes):
    """Only the fixed production origin or independently exact loopback fixture is allowed."""
    with pytest.raises((ValueError, TypeError)):
        configuration(**changes)


@pytest.mark.asyncio
async def test_preparation_runtime_is_immutable_and_never_opens_factory():
    """Catalog preparation cannot activate execution even through lifecycle or mutation."""
    opened = []
    def forbidden_factory():
        """Record accidental connections so this test proves construction/lifecycle are inert."""
        opened.append(True)
        raise AssertionError('Preparation must not connect')
    runtime = build_preparation_runtime(forbidden_factory)
    await runtime.start()
    await runtime.stop()
    assert not runtime.running and not runtime.allow_submission and not opened
    assert runtime.worker.factory is forbidden_factory
    policy = preparation_policy()
    assert policy.quote_minor is None and policy.currency == 'CNY'
    assert policy.capability.max_references == 0 and not policy.capability.reference_kinds
    assert runtime.policies[MODEL_ID] == policy
    with pytest.raises((AttributeError, TypeError)):
        runtime.allow_submission = True
    with pytest.raises(TypeError):
        runtime.policies[MODEL_ID] = policy
    with pytest.raises(RuntimeError):
        await runtime.worker.step('must-not-run')
    assert not opened


@pytest.mark.asyncio
async def test_submit_uses_one_fixed_post_without_attempt_identity_or_extra_headers():
    """A local durable attempt is not represented as an unproven provider idempotency key."""
    calls = []
    async def fake(url, **options):
        """Capture the one fake network operation and return only a remote identity."""
        calls.append((url, options))
        return response({'id': REMOTE})
    transport = Seedance25DurableTransport(configuration(), requester=fake)
    assert await transport.submit(snapshot(), 'local-attempt-not-a-provider-key') == REMOTE
    assert await transport.reconcile('same-attempt') is None
    assert len(calls) == 1
    url, options = calls[0]
    assert url == ARK_BASE_URL + CREATE_PATH and options['method'] == 'POST'
    assert options['timeout'] == 15 and options['max_bytes'] == 65536
    assert options['max_redirects'] == 0 and options['allowed_hosts'] == {'ark.cn-beijing.volces.com'}
    assert options['headers'] == {'Authorization': 'Bearer ' + FAKE_KEY,
                                  'Content-Type': 'application/json'}
    assert json.loads(options['data']) == build_seedance25_body(snapshot())
    assert b'local-attempt' not in options['data']


@pytest.mark.asyncio
async def test_submit_only_local_invalid_input_is_definite_rejection():
    """HTTP failures remain unknown acceptance; only zero-network validation is rejected."""
    calls = []
    async def fake(url, **options):
        """Return a limited provider response without ever contacting that provider."""
        calls.append(url)
        return response({'error': FAKE_KEY}, status=429, headers={'retry-after': '86400'})
    transport = Seedance25DurableTransport(configuration(), requester=fake)
    with pytest.raises(RejectedSubmission):
        await transport.submit(snapshot(duration=3), 'local')
    assert calls == []
    with pytest.raises(Seedance25ProtocolError) as caught:
        await transport.submit(snapshot(), 'local')
    assert not isinstance(caught.value, (RejectedSubmission, QueryBackoff))
    assert len(calls) == 1 and FAKE_KEY not in str(caught.value)


@pytest.mark.parametrize('remote_id', [None, True, 42, {}, '', 'a/b', 'a?x', 'x' * 129, '中文'])
@pytest.mark.asyncio
async def test_create_never_coerces_invalid_provider_identity(remote_id):
    """An ambiguous create response preserves unknown acceptance rather than inventing an ID."""
    calls = []
    async def fake(url, **options):
        """Record one attempt and return a deliberately invalid remote identity."""
        calls.append(url)
        return response({'id': remote_id})
    transport = Seedance25DurableTransport(configuration(), requester=fake)
    with pytest.raises(Seedance25ProtocolError):
        await transport.submit(snapshot(), 'local')
    assert len(calls) == 1


@pytest.mark.parametrize('status', [201, 204, 301, 400, 401, 403, 404, 500, 503])
@pytest.mark.asyncio
async def test_non200_create_never_becomes_definite_nonacceptance(status):
    """No HTTP status alone grants the accounting service authority to release a hold."""
    async def fake(url, **options):
        """Return a provider-shaped response with a non-success protocol status."""
        return response({'id': REMOTE, 'message': FAKE_KEY}, status=status)
    transport = Seedance25DurableTransport(configuration(), requester=fake)
    with pytest.raises(Seedance25ProtocolError) as caught:
        await transport.submit(snapshot(), 'local')
    assert not isinstance(caught.value, RejectedSubmission)
    assert FAKE_KEY not in str(caught.value)


@pytest.mark.parametrize('status,expected', [
    ('queued', {'status': 'running'}), ('running', {'status': 'running'}),
    ('succeeded', {'status': 'succeeded', 'locator': REMOTE, 'name': 'seedance25.mp4'}),
    ('failed', {'status': 'failed'}), ('expired', {'status': 'failed'}),
    ('cancelled', {'status': 'cancelled'}),
])
@pytest.mark.asyncio
async def test_query_projection_excludes_signed_urls_errors_and_credentials(status, expected):
    """The only data eligible for durable result storage is the exact normalized projection."""
    async def fake(url, **options):
        """Serve a provider-shaped object containing fake secret markers in ignored metadata."""
        return response(metadata(status))
    result = await Seedance25DurableTransport(configuration(), requester=fake).query(REMOTE)
    assert result == expected
    encoded = json.dumps(result)
    assert SIGNED_MARKER not in encoded and FAKE_KEY not in encoded and 'https:' not in encoded


@pytest.mark.parametrize('body', [
    b'{"id":"cgt-software-fixture-1","id":"other"}',
    b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}', b'[]', b'not json', b'\xff',
    {'id': REMOTE, 'model': MODEL_ID, 'status': ['succeeded']},
    {'id': REMOTE, 'model': MODEL_ID, 'status': 'new-provider-status'},
    metadata(id='wrong-task'), metadata(model='wrong-model'),
    metadata(content={}), metadata(content={'video_url': 'https://untrusted.invalid/x.mp4'}),
    metadata(content={'video_url': 'https://user:pass@ark-acg-cn-beijing.tos-cn-beijing.volces.com/x'}),
])
@pytest.mark.asyncio
async def test_ambiguous_json_identity_or_false_success_is_rejected_safely(body):
    """Bad metadata can never be interpreted as a successful result or echo raw error fields."""
    async def fake(url, **options):
        """Return a bounded invalid object through the test-only requester."""
        return response(body)
    with pytest.raises(Seedance25ProtocolError) as caught:
        await Seedance25DurableTransport(configuration(), requester=fake).query(REMOTE)
    assert FAKE_KEY not in str(caught.value) and SIGNED_MARKER not in str(caught.value)


@pytest.mark.parametrize('header,seconds', [
    ('1', 5), ('86400', 86400), ('0', 5), ('1.5', 30), ('invalid', 30),
    (formatdate(1001, usegmt=True), 5), (formatdate(1101, usegmt=True), 101),
])
@pytest.mark.asyncio
async def test_query_cancel_and_download_metadata_share_backoff(header, seconds):
    """Every control-plane read preserves the server delay through the same narrow signal."""
    calls = []
    async def fake(url, **options):
        """Return only fixture 429 responses with the requested Retry-After form."""
        calls.append(options['method'])
        return response({'error': FAKE_KEY}, status=429, headers={'Retry-After': header})
    transport = Seedance25DurableTransport(configuration(), requester=fake, clock=lambda: 1000.5)
    operations = [transport.query(REMOTE), transport.cancel(REMOTE), transport.download(
        {'status': 'succeeded', 'locator': REMOTE, 'name': 'seedance25.mp4'}, 'media', 'project')]
    for operation in operations:
        with pytest.raises(QueryBackoff) as caught:
            await operation
        assert caught.value.retry_after_seconds == seconds
        assert FAKE_KEY not in str(caught.value)
    assert calls == ['GET', 'GET', 'GET']


@pytest.mark.asyncio
async def test_download_refreshes_locator_and_does_not_forward_control_credentials():
    """Download uses the latest ephemeral URL and returns bytes for the existing publisher."""
    calls = []
    fresh_url = MEDIA_URL + '-refreshed'
    async def fake(url, **options):
        """Separate metadata and media endpoints without creating a second task."""
        calls.append((url, options))
        if '/api/v3/' in url:
            return response(metadata(content={'video_url': fresh_url}))
        return HttpResult(200, {}, b'fixture bytes; publisher validates actual MP4')
    transport = Seedance25DurableTransport(configuration(), requester=fake)
    locator = {'status': 'succeeded', 'locator': REMOTE, 'name': 'seedance25.mp4'}
    artifact = await transport.download(locator, 'media', 'project')
    assert artifact.name == 'seedance25.mp4' and artifact.data.startswith(b'fixture bytes')
    assert calls[0][0] == ARK_BASE_URL + CREATE_PATH + '/' + REMOTE
    assert calls[1][0] == fresh_url and calls[1][1]['headers'] == {}
    assert calls[1][1]['timeout'] == 30 and calls[1][1]['max_redirects'] == 3
    assert calls[1][1]['allowed_hosts'] == set(DOWNLOAD_HOSTS)
    assert SIGNED_MARKER not in json.dumps(locator)


@pytest.mark.asyncio
async def test_download_cdn_backoff_is_not_a_generic_retry():
    """A 429 from the actual media endpoint must pause the downloading phase too."""
    async def fake(url, **options):
        """Return fresh metadata followed by a limited media read."""
        if '/api/v3/' in url:
            return response(metadata())
        return HttpResult(429, {'retry-after': '86400'}, b'')
    transport = Seedance25DurableTransport(configuration(), requester=fake)
    with pytest.raises(QueryBackoff) as caught:
        await transport.download(
            {'status': 'succeeded', 'locator': REMOTE, 'name': 'seedance25.mp4'}, 'm', 'p')
    assert caught.value.retry_after_seconds == 86400


@pytest.mark.asyncio
async def test_download_rejects_persisted_url_and_cancel_never_deletes():
    """Forged local results cannot add a URL, while cancel is only an observed state query."""
    calls = []
    async def fake(url, **options):
        """Observe only methods and never perform remote operations."""
        calls.append(options['method'])
        return response(metadata('running'))
    transport = Seedance25DurableTransport(configuration(), requester=fake)
    with pytest.raises(Seedance25ProtocolError):
        await transport.download({'status': 'succeeded', 'locator': REMOTE,
                                  'name': 'seedance25.mp4', 'url': MEDIA_URL}, 'm', 'p')
    assert calls == []
    assert not await transport.cancel(REMOTE)
    assert calls == ['GET']


@pytest.mark.asyncio
async def test_aggregate_download_deadline_cancels_inflight_metadata(monkeypatch):
    """The shared deadline covers metadata itself and awaits cancellation cleanup."""
    from app.core.integrations.volcengine import seedance25
    original_timeout = asyncio.timeout
    limits = []
    cleaned = []
    def short_timeout(seconds):
        """Keep this test fast while proving the production aggregate deadline is 45s."""
        limits.append(seconds)
        return original_timeout(.01)
    async def fake(url, **options):
        """Simulate waiting metadata and record completion of its cancellation cleanup."""
        try:
            await asyncio.sleep(1)
            return response(metadata())
        finally:
            cleaned.append(True)
    monkeypatch.setattr(seedance25.asyncio, 'timeout', short_timeout)
    transport = Seedance25DurableTransport(configuration(), requester=fake)
    with pytest.raises(Seedance25ProtocolError):
        await transport.download(
            {'status': 'succeeded', 'locator': REMOTE, 'name': 'seedance25.mp4'}, 'm', 'p')
    assert limits == [45] and cleaned == [True]


@pytest.mark.asyncio
async def test_raw_transport_failure_is_suppressed_in_visible_traceback():
    """URL/token-shaped upstream errors cannot surface through normal error reporting."""
    async def fake(url, **options):
        """Inject an unsafe underlying error solely inside a fake transport."""
        raise RuntimeError(FAKE_KEY + SIGNED_MARKER)
    transport = Seedance25DurableTransport(configuration(), requester=fake)
    with pytest.raises(Seedance25ProtocolError) as caught:
        await transport.query(REMOTE)
    visible = ''.join(traceback.format_exception(caught.value))
    assert FAKE_KEY not in visible and SIGNED_MARKER not in visible


@pytest.mark.asyncio
async def test_real_isolated_http_fixture_keeps_auth_off_media():
    """Exercise actual child HTTP with a single exact loopback origin and software bytes."""
    from tests.test_trusted_http import Handler, server
    seen = []
    class Fixture(Handler):
        """Serve a fixed Ark-shaped protocol solely inside this owned local server."""
        def do_POST(self):
            """Accept one test create and record its exact payload and authentication presence."""
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            seen.append(('POST', self.path, bool(self.headers.get('Authorization')), data))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({'id': REMOTE}).encode())
        def do_GET(self):
            """Use an ephemeral local media URL; never return a real model product."""
            seen.append(('GET', self.path, bool(self.headers.get('Authorization')), None))
            self.send_response(200)
            self.end_headers()
            if self.path.startswith('/artifact'):
                self.wfile.write(b'software HTTP fixture bytes')
            else:
                url = f'http://127.0.0.1:{self.server.server_port}/artifact?signature={SIGNED_MARKER}'
                self.wfile.write(json.dumps(metadata(content={'video_url': url})).encode())
    with server(Fixture) as fixture:
        cfg = configuration(
            base_url=f'http://127.0.0.1:{fixture.server_port}/api/v3',
            download_hosts=frozenset({'127.0.0.1'}), trusted_local_host='127.0.0.1',
            trusted_local_port=fixture.server_port,
        )
        transport = Seedance25DurableTransport(cfg)
        assert await transport.submit(snapshot(), 'fixture-attempt') == REMOTE
        result = await transport.query(REMOTE)
        artifact = await transport.download(result, 'fixture-media', 'fixture-project')
        assert artifact.data == b'software HTTP fixture bytes'
        assert [entry[0] for entry in seen] == ['POST', 'GET', 'GET', 'GET']
        assert [entry[2] for entry in seen] == [True, True, True, False]
        assert SIGNED_MARKER not in json.dumps(result)
