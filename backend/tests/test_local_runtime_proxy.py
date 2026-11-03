"""Actual ASGI proxy tests: fixed targets, private-header closure, byte bounds, raw range responses and owned disconnect cleanup."""
import asyncio
import json

import httpx
import pytest

import local_runtime_bootstrap as runtime


class Bytes(httpx.AsyncByteStream):
    """Track actual upstream stream closure while returning untouched media bytes."""

    def __init__(self, value=b'authored-media'):
        """Retain synthetic media only, never a user's artifact."""
        self.value = value
        self.closed = False

    async def __aiter__(self):
        """Deliver a single untouched chunk for real aiter_raw forwarding."""
        yield self.value

    async def aclose(self):
        """Record the upstream ownership cleanup observed by the test."""
        self.closed = True


def proxy_config():
    """The proxy receives public numeric routing facts and no data-root path, credential or readiness token."""
    return {'profile': 'local_runtime_release_v1', 'slot': 'stable', 'origin': 'http://127.0.0.1:18080', 'target_ip': '172.23.0.2'}


@pytest.mark.asyncio
async def test_fixed_target_preserves_raw_ranges_cookies_and_duplicate_set_cookie_without_internal_secrets():
    """Only the fixed app receives ordinary authenticated HTTP; internal readiness headers and hop-by-hop fields are stripped."""
    calls = []
    body = Bytes(b'\x00\x01\xfe\xff')

    async def upstream(request):
        """Observe the actual outgoing request URL and headers without a real external endpoint."""
        calls.append(request)
        return httpx.Response(206, headers=[('Content-Type', 'video/mp4'), ('Content-Range', 'bytes 1-4/9'),
            ('Set-Cookie', 'a=1; HttpOnly'), ('Set-Cookie', 'b=2; HttpOnly'), ('Connection', 'x-hop'), ('x-hop', 'removed')], stream=body)

    app = runtime.proxy(proxy_config(), transport=httpx.MockTransport(upstream))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:18080') as client:
            result = await client.get('/api/v1/files/owned?token=authored-token', headers={'Cookie': 'jellyfish_session=owned', 'Range': 'bytes=1-4',
                'Origin': 'http://127.0.0.1:18080', 'x-fawnimo-private-runtime-ready': 'never-forward', 'Proxy-Authorization': 'never-forward'})
        assert result.status_code == 206 and result.content == body.value
        assert result.headers.get_list('set-cookie') == ['a=1; HttpOnly', 'b=2; HttpOnly']
        assert result.headers['content-range'] == 'bytes 1-4/9' and 'x-hop' not in result.headers
        assert len(calls) == 1 and str(calls[0].url) == 'http://172.23.0.2:8000/api/v1/files/owned?token=authored-token'
        assert calls[0].headers['cookie'] == 'jellyfish_session=owned' and calls[0].headers['range'] == 'bytes=1-4'
        assert 'x-fawnimo-private-runtime-ready' not in calls[0].headers and 'proxy-authorization' not in calls[0].headers
        assert body.closed
    finally: await app.state.runtime_client.aclose()


@pytest.mark.asyncio
async def test_private_route_wrong_host_and_oversized_body_never_reach_app(monkeypatch):
    """Public ingress cannot ask for the private readiness route or choose a different authority, and actual body bytes are bounded."""
    calls = []
    app = runtime.proxy(proxy_config(), transport=httpx.MockTransport(lambda request: calls.append(request)))
    monkeypatch.setattr(runtime, 'BODY_LIMIT', 16)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:18080') as client:
            assert (await client.get(runtime.READY_PATH)).status_code == 404
            assert (await client.get('/%5f%5ffawnimo_private_runtime_ready')).status_code == 404
            assert (await client.get('/health', headers={'Host': 'other.example'})).status_code == 404
            assert (await client.post('/api/v1/projects', content=b'x' * 17)).status_code == 413
        assert not calls
    finally: await app.state.runtime_client.aclose()


@pytest.mark.asyncio
async def test_finite_concurrency_rejects_ninth_and_releases_after_completed_streams():
    """At most eight private transfers can own connections; immediate saturation cannot queue unbounded requests."""
    entered = 0
    ready = asyncio.Event()
    release = asyncio.Event()

    async def upstream(request):
        """Hold exactly eight active requests so the real proxy lane limit can be observed."""
        nonlocal entered
        entered += 1
        if entered == 8: ready.set()
        await release.wait()
        return httpx.Response(200, stream=Bytes(b'ok'))

    app = runtime.proxy(proxy_config(), transport=httpx.MockTransport(upstream))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:18080') as client:
            tasks = [asyncio.create_task(client.get('/health')) for _ in range(8)]
            try:
                await asyncio.wait_for(ready.wait(), 3)
                assert (await client.get('/health')).status_code == 429
            finally:
                release.set()
                assert all(result.status_code == 200 for result in await asyncio.gather(*tasks))
            assert (await client.get('/health')).status_code == 200
    finally: await app.state.runtime_client.aclose()


@pytest.mark.parametrize('change', [{'target_ip': '8.8.8.8'}, {'target_ip': '127.0.0.1'}, {'target_ip': '169.254.169.254'},
    {'target_ip': '::1'}, {'origin': 'http://localhost:18080'}, {'origin': 'http://127.0.0.1:8000'},
    {'slot': 'preview'}, {'ready_token': 'a' * 64}, {'root': '/data'}])
def test_fixed_proxy_configuration_cannot_widen_authority(change):
    """Only the declared new slot host and a private fixed app IPv4 target are accepted; paths, credentials and arbitrary origins are forbidden."""
    with pytest.raises(ValueError): runtime.configuration(json.dumps(proxy_config() | change).encode())


@pytest.mark.asyncio
async def test_shared_transport_never_retains_or_injects_another_clients_login_cookie():
    """Independent login jars and an anonymous caller send only their own session, while Set-Cookie still reaches each correct browser."""
    seen=[]
    async def upstream(request):
        """Return ordinary synthetic login cookies through the actual HTTPX extraction/ASGI forwarding path."""
        seen.append((request.url.path,request.headers.get('cookie')))
        headers={}
        if request.url.path.startswith('/login/'):
            headers['Set-Cookie']='studio_session=session-'+request.url.path.rsplit('/',1)[-1]+'; HttpOnly; Path=/; SameSite=lax'
        return httpx.Response(200,headers=headers,stream=Bytes(b'{}'))
    app=runtime.proxy(proxy_config(),transport=httpx.MockTransport(upstream))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=proxy_config()['origin']) as a, \
                   httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=proxy_config()['origin']) as b, \
                   httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=proxy_config()['origin']) as anonymous:
            await a.post('/login/a');await b.post('/login/b');await anonymous.get('/private');await a.get('/private');await b.get('/private')
        assert seen==[('/login/a',None),('/login/b',None),('/private',None),('/private','studio_session=session-a'),('/private','studio_session=session-b')]
        assert len(app.state.runtime_client.cookies)==0
    finally:await app.state.runtime_client.aclose()
