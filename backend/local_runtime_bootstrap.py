"""Pinned public bootstrap for owned local images and a data-free fixed-target HTTP proxy.

This single file may be copied to a code-only read-only volume. It imports the
selected image's installed gateway, never host source or another slot's data.
"""
import asyncio
from contextlib import asynccontextmanager
import hmac
import hashlib
from http.cookiejar import CookieJar, DefaultCookiePolicy
import ipaddress
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

PROFILE = 'local_runtime_release_v1'
READY_PATH = '/__fawnimo_private_runtime_ready'
READY_HEADER = b'x-fawnimo-private-runtime-ready'
BODY_LIMIT = 70 * 1024 * 1024
RESPONSE_LIMIT = 512 * 1024 * 1024
HOP = {b'connection', b'keep-alive', b'proxy-authenticate', b'proxy-authorization',
       b'te', b'trailer', b'transfer-encoding', b'upgrade', b'content-length'}


class ForwardedCookiePolicy(DefaultCookiePolicy):
    """A shared reverse-proxy transport never owns a browser login; only explicitly forwarded per-request cookies are allowed."""

    def set_ok(self, cookie, request):
        """Do not retain upstream Set-Cookie in a cross-request jar; still forward the raw response header to its actual browser."""
        return False

    def return_ok(self, cookie, request):
        """Never manufacture an outgoing Cookie header from transport-wide state."""
        return False


def configuration(raw):
    """Accept only bounded installed runtime configuration with separate stable/preview loopback origins."""
    if not isinstance(raw, bytes) or len(raw) > 8192: raise ValueError('Invalid private runtime configuration')
    from app.services.community.events import unique_fields, invalid_number
    value = json.loads(raw, object_pairs_hook=unique_fields, parse_constant=invalid_number)
    allowed = {'profile', 'slot', 'origin', 'ready_token', 'target_ip'}
    if not isinstance(value, dict) or set(value) - allowed or value.get('profile') != PROFILE or value.get('slot') not in {'stable', 'preview'}:
        raise ValueError('Unknown local runtime profile')
    origin = urlsplit(value.get('origin', ''))
    host = '127.0.0.1' if value['slot'] == 'stable' else '127.0.0.2'
    if (origin.scheme != 'http' or origin.hostname != host or origin.username or origin.password
            or origin.path or origin.query or origin.fragment or not origin.port or not 1024 <= origin.port <= 65535
            or origin.port in {8000, 17888, 18000} or value['origin'] != f'http://{host}:{origin.port}'):
        raise ValueError('An exact new slot loopback origin is required')
    if 'target_ip' in value:
        address = ipaddress.ip_address(value['target_ip'])
        if address.version != 4 or not address.is_private or address.is_loopback or address.is_link_local or address.is_unspecified:
            raise ValueError('A fixed private app address is required')
        if 'ready_token' in value: raise ValueError('The data-free proxy cannot receive a readiness secret')
    elif re.fullmatch(r'[a-f0-9]{64}', value.get('ready_token', '')) is None:
        raise ValueError('A fresh private app readiness token is required')
    return value


class PrivateReady:
    """Answer an internal token-bound readiness observation outside the public OpenAPI and SPA catch-all routes."""

    def __init__(self, app, value):
        """Keep the secret in this app's private memory; it is never forwarded by the published proxy."""
        self.app = app
        self.value = value
        self.prepared = False
        self.lock = asyncio.Lock()

    async def prepare(self):
        """Verify original rows/files on first preparation of a new generation; restart the same verified generation with its legitimate later edits."""
        if self.prepared: return
        async with self.lock:
            if self.prepared: return
            from contextlib import closing
            import sqlite3
            import time
            from app.controlplane.migration import table_receipts
            from app.services.community.local_release_files import read_json, file_hash, plain
            from app.core.workspace_boundary import assert_workspace_boundary
            baseline_path=plain(Path('/data/runtime-baseline.json'))
            baseline = read_json(baseline_path)
            if (baseline.get('profile') != 'local_runtime_baseline_v1' or len(baseline['workspaces']) > 4
                    or any(re.fullmatch(r'ws_[a-f0-9]{32}', item) is None for item in baseline['workspaces'])):
                raise ValueError('Invalid original runtime data baseline')
            with self.app.state.identity_store.connect() as db:
                current_spaces=[row[0] for row in db.execute('SELECT id FROM workspaces ORDER BY id')]
            if current_spaces!=baseline['workspaces']:raise ValueError('Current runtime workspace topology differs from its own generation')
            expected_receipt={'profile':'local_runtime_prepared_v1','slot':self.value['slot'],
                'baseline_sha256':file_hash(baseline_path,time.monotonic()+15)['sha256'],
                'config_sha256':hashlib.sha256(json.dumps(self.value,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
            receipt_path=plain(Path('/data/runtime-prepared.json'),exists=False)
            previously_prepared=receipt_path.exists()
            if previously_prepared:
                import stat
                entry=receipt_path.stat()
                if not stat.S_ISREG(entry.st_mode) or entry.st_nlink!=1 or entry.st_size>4096 or read_json(receipt_path)!=expected_receipt:
                    raise ValueError('Original runtime preparation receipt changed')
            for workspace in baseline['workspaces']:
                await self.app.state.workers.get(workspace)
                database = Path('/data/studio/workspaces') / workspace / 'studio.db'
                assert_workspace_boundary(database, workspace)
                expected = baseline['databases'].get(workspace)
                if expected and not previously_prepared:
                    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=1.5)) as db:
                        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 8388608)
                        until = time.monotonic() + 15
                        db.set_progress_handler(lambda: 1 if time.monotonic() > until else 0, 1000)
                        if table_receipts(db, expected=expected) != expected:
                            raise ValueError('Selected image changed original business rows')
            deadline = time.monotonic() + 30
            for name, expected in ({} if previously_prepared else baseline['files']).items():
                relative = Path(name)
                if relative.is_absolute() or '..' in relative.parts: raise ValueError('Invalid original private file reference')
                if file_hash(plain(Path('/data/studio') / relative), deadline) != expected:
                    raise ValueError('Selected image changed original private file bytes')
            if not previously_prepared:
                # Successful first preparation is bound to this exact private
                # config/baseline. New activation/restore targets never copy it.
                # Later same-generation restarts retain legitimate user edits.
                descriptor=os.open(receipt_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(descriptor,'wb') as stream:
                    stream.write((json.dumps(expected_receipt,sort_keys=True,separators=(',',':'))+'\n').encode())
                    stream.flush();os.fsync(stream.fileno())
                from app.controlplane.migration import flush_directory
                flush_directory(receipt_path.parent)
            self.prepared = True

    async def __call__(self, scope, receive, send):
        """Intercept only the fixed internal GET route, returning no users, sessions, passwords, paths or model settings."""
        if scope['type'] == 'http' and scope.get('path') == READY_PATH:
            from starlette.responses import JSONResponse, Response
            tokens = [value for key, value in scope['headers'] if key.lower() == READY_HEADER]
            if scope['method'] != 'GET' or len(tokens) != 1 or not hmac.compare_digest(tokens[0], self.value['ready_token'].encode()):
                return await Response(status_code=404)(scope, receive, send)
            try: await self.prepare()
            except Exception: return await Response(status_code=503)(scope, receive, send)
            return await JSONResponse({'profile': PROFILE, 'slot': self.value['slot'], 'ready': True,
                                       'paid_generation_enabled': False}, headers={'Cache-Control': 'no-store'})(scope, receive, send)
        return await self.app(scope, receive, send)


def gateway(value):
    """Run only the selected image's compatible login gateway on its private app network, with the exact public loopback origin."""
    from app.controlplane.gateway import create_gateway
    from app.core.contracts.sound_mix import music_mix_policy
    if not callable(music_mix_policy): raise ValueError('This image does not support the required local runtime protocol')
    app = create_gateway(Path('/data/studio'), frontend=Path('/app/front/dist'), probe=Path('/usr/bin/ffmpeg'),
                         secure_cookies=False, public_origin=value['origin'], allow_registration=False, capacity=4)
    return PrivateReady(app, value)


def proxy(value, *, transport=None):
    """Publish only a fixed numeric app target; no data mount, arbitrary URL, proxy environment, redirect or socket tunnel is supported."""
    import httpx
    from fastapi import FastAPI, Request
    from starlette.responses import Response, StreamingResponse
    target = 'http://' + value['target_ip'] + ':8000'
    expected_host = urlsplit(value['origin']).netloc.encode()
    client = httpx.AsyncClient(base_url=target, trust_env=False, follow_redirects=False, transport=transport,
                               cookies=CookieJar(policy=ForwardedCookiePolicy()),
                               timeout=httpx.Timeout(60, connect=2, pool=1),
                               limits=httpx.Limits(max_connections=8, max_keepalive_connections=4))
    lanes = asyncio.Semaphore(8)

    @asynccontextmanager
    async def lifespan(app):
        """Join and close this proxy's own connections during controlled shutdown."""
        try: yield
        finally: await client.aclose()

    app = FastAPI(lifespan=lifespan, openapi_url=None, docs_url=None, redoc_url=None)
    app.state.runtime_client = client

    @app.api_route('/{path:path}', methods=['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'])
    async def forward(request: Request, path: str):
        """Bound and relay raw HTTP to the one app; release the connection/lane on body failure, cancellation and disconnect."""
        headers = request.scope['headers']
        hosts = [content for name, content in headers if name.lower() == b'host']
        raw_path = request.scope.get('raw_path', b'/')
        if (hosts != [expected_host] or not raw_path.startswith(b'/') or raw_path.startswith(b'//')
                or request.url.path.startswith('/__fawnimo_') or len(headers) > 128
                or sum(len(name) + len(content) for name, content in headers) > 32768
                or len(raw_path) > 8192 or len(request.scope.get('query_string', b'')) > 8192):
            return Response(status_code=404)
        if lanes.locked(): return Response(status_code=429, headers={'Retry-After': '1'})
        await lanes.acquire()
        upstream = None
        released = False

        async def close():
            """Release exactly once, including a downstream disconnect before the first response chunk."""
            nonlocal released
            if released: return
            released = True
            try:
                if upstream is not None: await upstream.aclose()
            finally: lanes.release()

        try:
            body = bytearray()
            async with asyncio.timeout(30):
                async for chunk in request.stream():
                    if len(body) + len(chunk) > BODY_LIMIT:
                        await close()
                        return Response(status_code=413)
                    body.extend(chunk)
            connection_names = {part.strip().lower() for name, content in headers if name.lower() == b'connection' for part in content.split(b',')}
            clean = [(name, content) for name, content in headers
                     if name.lower() not in HOP | connection_names | {READY_HEADER, b'x-fawnimo-worker', b'x-fawnimo-internal-token'}
                     and not name.lower().startswith(b'proxy-')]
            # Construct the URL from the fixed target and raw path/query parts;
            # a client-supplied absolute URL can never replace its authority.
            query = request.scope.get('query_string', b'')
            url = httpx.URL(target).copy_with(raw_path=raw_path + (b'?' + query if query else b''))
            # Construct directly: build_request merges a client's cookie jar,
            # which must never let another caller inherit/revoke a login.
            upstream = await client.send(httpx.Request(request.method, url, headers=clean, content=bytes(body)), stream=True)
            response_connections = {part.strip().lower() for name, content in upstream.headers.raw if name.lower() == b'connection' for part in content.split(b',')}
            outgoing = [(name, content) for name, content in upstream.headers.raw if name.lower() not in HOP | response_connections]

            async def chunks():
                """Keep a finite total transfer deadline and byte budget, without buffering or decoding private media."""
                count = 0
                try:
                    async with asyncio.timeout(120):
                        async for chunk in upstream.aiter_raw():
                            count += len(chunk)
                            if count > RESPONSE_LIMIT: raise ValueError('Local proxy response limit reached')
                            yield chunk
                finally: await close()

            class OwnedStream(StreamingResponse):
                """Close an owned upstream even when ASGI disconnects before entering its body iterator."""
                async def __call__(self, scope, receive, send):
                    """Shield/join cleanup using the installed process ownership cancellation helper."""
                    try: await super().__call__(scope, receive, send)
                    finally:
                        from app.core.owned_tasks import join_tasks
                        task = asyncio.create_task(close())
                        await join_tasks(task)

            response = OwnedStream(chunks(), status_code=upstream.status_code)
            response.raw_headers = outgoing
            return response
        except BaseException as error:
            from app.core.owned_tasks import join_tasks
            await join_tasks(asyncio.create_task(close()))
            if isinstance(error, asyncio.CancelledError): raise
            return Response(status_code=502)

    return app


def main():
    """Only the controller's two fixed commands are available; no source path, executable or arbitrary listen address is accepted."""
    os.environ['JELLYFISH_SKIP_ENV_FILE'] = '1'
    sys.path.insert(0, '/app/backend')
    if sys.argv[1:] == ['app']:
        with Path('/data/runtime.json').open('rb') as stream: value = configuration(stream.read(8193))
        if 'target_ip' in value: raise ValueError('App profile cannot be a proxy')
        from app.controlplane.maintenance import ProcessLease
        with ProcessLease(Path('/data/studio/.maintenance.lock')):
            import uvicorn
            uvicorn.run(gateway(value), host='0.0.0.0', port=8000, proxy_headers=False, access_log=False,
                        timeout_graceful_shutdown=15, limit_concurrency=64, timeout_keep_alive=5)
    elif len(sys.argv) == 3 and sys.argv[1] == 'proxy':
        # This argument contains only a numeric target and public loopback
        # origin. The proxy receives no token, claims or business data mount.
        value = configuration(sys.argv[2].encode())
        if 'target_ip' not in value: raise ValueError('Proxy requires a fixed app address')
        import uvicorn
        uvicorn.run(proxy(value), host='0.0.0.0', port=8000, proxy_headers=False, access_log=False,
                    timeout_graceful_shutdown=15, limit_concurrency=64, timeout_keep_alive=5)
    else: raise ValueError('Unknown fixed local runtime command')


if __name__ == '__main__': main()
