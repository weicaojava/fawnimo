"""Actual gateway ASGI and private-loopback HTTP verify scoped routing; authored wire bodies are not model/media-quality evidence."""
import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import threading
import httpx
from types import SimpleNamespace
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import pytest

from app.controlplane.gateway import create_gateway
from app.controlplane.reference_proxy import ScopedReferenceProxyResponse

ORIGIN='http://127.0.0.1:18006'
BODY=b'authored fixed-worker wire bytes'
INTERNAL='owned-fixture-worker-token-'*3
IDENTIFIER='a'*32
TOKEN='b'*64


class WirePool:
    """Record exactly which registered fixed worker the gateway selects; no workspace files or models are opened."""
    def __init__(self,port):
        """Use one explicit test TCP server; its incoming workspace header identifies the requested fixed scope."""
        self.port=port;self.calls=[]

    async def get(self,workspace_id):
        """Expose only the fixed private loopback target and gateway-owned token."""
        self.calls.append(workspace_id)
        return SimpleNamespace(workspace_id=workspace_id,port=self.port,token=INTERNAL)

    async def close(self):
        """The context manager below owns and joins the actual fixture HTTP server."""
        return None


@contextmanager
def wire_backend():
    """Serve finite authored responses and retain sanitized test observations; never contact a supplier."""
    class Handler(BaseHTTPRequestHandler):
        """Implement only the old worker media read expected after gateway capability verification."""
        protocol_version='HTTP/1.1'

        def log_message(self,*args):
            """Do not log authored tokens or query strings."""
            return None

        def do_GET(self):
            """Record HTTP GET through the same controlled path as HEAD."""
            self.reply(False)

        def do_HEAD(self):
            """Return the GET length without an actual response body."""
            self.reply(True)

        def reply(self,head):
            """Require the private token and exact rewritten path; test modes only alter response protocol facts."""
            self.close_connection=True
            self.server.observed.append({'headers':dict(self.headers),'path':self.path,'method':self.command})
            assert self.headers.get('X-Workspace-Token')==INTERNAL
            assert self.path==f'/api/v1/execution-reference-media/{IDENTIFIER}?token={TOKEN}'
            mode=self.server.mode
            status=301 if mode=='redirect' else 404 if mode=='missing' else 429 if mode=='busy' else 416 if mode=='range_error' else 200
            data=BODY;content_range=None
            if status==200 and self.headers.get('range')=='bytes=2-6':status=206;data=BODY[2:7];content_range=f'bytes 2-6/{len(BODY)}'
            length=33554433 if mode=='oversize' else len(data)
            self.send_response(status)
            self.send_header('Content-Type','text/html' if mode=='active_content' else 'video/mp4')
            self.send_header('Content-Length',str(length))
            if content_range:self.send_header('Content-Range',content_range)
            if status==416:self.send_header('Content-Range',f'bytes */{len(BODY)}')
            if status==301:self.send_header('Location','https://example.invalid/not-followed')
            self.send_header('Set-Cookie','authored-private-worker-cookie=not-forwarded')
            self.end_headers()
            if not head and mode!='oversize':self.wfile.write(data)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.observed=[];server.mode='ok'
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield server
    finally:
        server.shutdown();server.server_close();thread.join(3)
        assert not thread.is_alive()


@pytest.fixture
def gateway(tmp_path):
    """Create two new owners and one opt-in gateway; all signer clocks and data belong only to this fixture."""
    with wire_backend() as backend:
        pool=WirePool(backend.server_port)
        app=create_gateway(tmp_path/'identity',workers=pool,public_origin=ORIGIN,secure_cookies=False,reference_media_origin=ORIGIN)
        store=app.state.identity_store
        one=store.provision('reference-owner-a','authored-reference-owner-password-a')
        two=store.provision('reference-owner-b','authored-reference-owner-password-b')
        authority=app.state.reference_delivery_authority;authority.clock=lambda:1001
        url=authority.policy_for_workspace(one['workspace_id']).signed_url(IDENTIFIER,TOKEN,1000)
        with TestClient(app) as client:yield SimpleNamespace(app=app,client=client,pool=pool,backend=backend,store=store,a=one,b=two,url=url)


def test_opt_in_read_range_and_head_forward_only_owned_scope_and_private_token(gateway):
    """A complete link grants only its one read; unrelated cookies/headers cannot retarget or reach the private worker."""
    item=gateway
    supplied={'Authorization':'authored-do-not-forward','Cookie':'untrusted-cookie=authored',
        'X-Workspace-Token':'untrusted-internal-token','X-Workspace-Id':item.b['workspace_id'],'X-Identity-Id':item.b['user_id']}
    result=item.client.get(item.url,headers=supplied)
    assert result.status_code==200 and result.content==BODY
    assert result.headers['cache-control']=='no-store' and result.headers['referrer-policy']=='no-referrer' and 'set-cookie' not in result.headers
    partial=item.client.get(item.url,headers={'Range':'bytes=2-6'})
    assert partial.status_code==206 and partial.content==BODY[2:7] and partial.headers['content-range']==f'bytes 2-6/{len(BODY)}'
    head=item.client.head(item.url)
    assert head.status_code==200 and head.content==b'' and head.headers['content-length']==str(len(BODY))
    assert item.pool.calls==[item.a['workspace_id']]*3
    for record in item.backend.observed:
        headers={key.lower():value for key,value in record['headers'].items()}
        assert headers['x-workspace-token']==INTERNAL and headers['x-workspace-id']==item.a['workspace_id']
        assert not any(key in headers for key in ('authorization','cookie','x-identity-id','x-workspace-role'))


@pytest.mark.parametrize('change',['workspace','identifier','token','expiry','signature','extra_query'])
def test_invalid_capability_does_not_select_a_worker_or_open_a_business_endpoint(gateway,change):
    """Changing a path/query authority fact fails before the private worker; the same fields do not authenticate normal API reads."""
    item=gateway;url=item.url
    if change=='workspace':url=url.replace(item.a['workspace_id'],item.b['workspace_id'])
    if change=='identifier':url=url.replace(IDENTIFIER,'c'*32)
    if change=='token':url=url.replace(TOKEN,'d'*64)
    if change=='expiry':url=url.replace('expires=4600','expires=4601')
    if change=='signature':url=url[:url.rfind('=')+1]+'0'*64
    if change=='extra_query':url+='&workspace_id='+item.b['workspace_id']
    result=item.client.get(url)
    assert result.status_code==404 and TOKEN not in result.text and result.headers['referrer-policy']=='no-referrer'
    assert item.pool.calls==[] and item.backend.observed==[]
    assert item.client.get('/api/v1/studio/projects?'+urlsplit(item.url).query).status_code==401


def test_unknown_registered_scope_or_disabled_owner_cannot_start_a_worker(gateway):
    """Even a fixture-signed unknown space or a revoked real owner cannot trigger process selection."""
    item=gateway;authority=item.app.state.reference_delivery_authority
    unknown=authority.policy_for_workspace('ws_'+'f'*32).signed_url(IDENTIFIER,TOKEN,1000)
    assert item.client.get(unknown).status_code==404
    with item.store.connect() as db:db.execute('UPDATE users SET disabled=1 WHERE id=?',(item.a['user_id'],))
    assert item.client.get(item.url).status_code==404 and item.pool.calls==[] and item.backend.observed==[]


def test_pool_returning_another_fixed_identity_is_rejected_before_private_http(gateway):
    """A pool inconsistency cannot use an otherwise valid link to select the wrong fixed process."""
    item=gateway
    async def wrong(workspace_id):
        """Return a different declared scope to exercise the actual last check before HTTP dispatch."""
        return SimpleNamespace(workspace_id=item.b['workspace_id'],port=item.backend.server_port,token=INTERNAL)
    item.pool.get=wrong
    assert item.client.get(item.url).status_code==503 and item.backend.observed==[]


@pytest.mark.parametrize('mode,expected',[('redirect',503),('oversize',503),('active_content',503),('missing',404),('busy',429),('range_error',416)])
def test_bad_upstream_or_finite_worker_response_never_opens_another_target_or_leaks_headers(gateway,mode,expected):
    """Actual HTTP response rejection closes its lane; redirects, active content, oversize and private cookies are not forwarded."""
    item=gateway;item.backend.mode=mode
    result=item.client.get(item.url)
    assert result.status_code==expected and result.content!=BODY and 'set-cookie' not in result.headers
    assert TOKEN not in result.text
    item.backend.mode='ok'
    assert item.client.get(item.url).content==BODY


def test_closed_default_and_write_methods_never_treat_a_read_link_as_authority(tmp_path,gateway):
    """Opt-in media does not enable registration, normal private APIs or any write/paid submission."""
    root=tmp_path/'closed';app=create_gateway(root,workers=WirePool(gateway.backend.server_port))
    with TestClient(app) as client:assert client.get(gateway.url).status_code==404
    assert not (root/'.reference-delivery.key').exists()
    assert gateway.client.post(gateway.url,json={'root':'untrusted'}).status_code==404
    assert gateway.pool.calls==[]


@pytest.mark.asyncio
async def test_response_owner_joins_close_through_repeated_cancellation_before_releasing_lane():
    """Real ASGI send/cancellation waits for both sequential owners, then propagates cancellation without leaking admission."""
    started=asyncio.Event();release=asyncio.Event();sent=asyncio.Event();lane=asyncio.Semaphore(1);await lane.acquire()
    class Owned:
        """Represent a controllably slow response closer and its client for exact ownership assertions."""
        def __init__(self,slow=False):
            """Keep actual close completion distinct from cancellation of the caller."""
            self.slow=slow;self.closed=False
        async def aclose(self):
            """Only the original response waits; the client must still close after it."""
            if self.slow:started.set();await release.wait()
            self.closed=True
    upstream=Owned(True);client=Owned()
    async def body():
        """Keep the actual response alive until the test cancels its owner."""
        yield b'owned'
        await asyncio.Future()
    async def receive():
        """No client disconnect is synthesized; the actual owner task receives cancellation."""
        await asyncio.Future()
    async def send(message):
        """Observe an actual response start before cancelling stream ownership."""
        if message['type']=='http.response.start':sent.set()
    response=ScopedReferenceProxyResponse(body(),client=client,upstream=upstream,release=lane.release)
    task=asyncio.create_task(response({'type':'http','asgi':{'spec_version':'2.4'}},receive,send))
    await asyncio.wait_for(sent.wait(),2);task.cancel();await asyncio.wait_for(started.wait(),2)
    task.cancel();await asyncio.sleep(0)
    assert not task.done() and lane.locked()
    release.set()
    with pytest.raises(asyncio.CancelledError):await asyncio.wait_for(task,2)
    assert upstream.closed and client.closed and not lane.locked()


@pytest.mark.asyncio
async def test_ready_worker_read_does_not_wait_for_another_workspace_startup(tmp_path):
    """An already-ready media reader remains available while another startup owns the global creation lock."""
    from app.controlplane.workers import Worker,WorkspaceWorkers
    workspace='ws_'+'a'*32;pool=WorkspaceWorkers(tmp_path/'workspaces')
    cached=Worker(workspace,19001,INTERNAL,SimpleNamespace(returncode=None),None)
    pool.workers[workspace]=cached
    await pool.lock.acquire()
    try:
        assert await asyncio.wait_for(pool.get(workspace),2) is cached
    finally:
        pool.lock.release();pool.workers.clear()


@pytest.mark.asyncio
async def test_gateway_removes_capability_query_from_real_asgi_server_scope_before_error_responses(tmp_path):
    """Observe the outer server-facing response start; unknown targets, bad signatures and legacy auth failures reveal no query."""
    app=create_gateway(tmp_path/'identity',public_origin=ORIGIN,secure_cookies=False,reference_media_origin=ORIGIN)
    authority=app.state.reference_delivery_authority;authority.clock=lambda:1001
    url=authority.policy_for_workspace('ws_'+'a'*32).signed_url(IDENTIFIER,TOKEN,1000)
    observed=[]
    async def tapped(scope,receive,send):
        """Measure the same scope that a native ASGI access logger sees when response headers are sent."""
        async def capture(message):
            """Capture only query bytes for this synthetic fixture, never print or persist them."""
            if message['type']=='http.response.start':observed.append(scope.get('query_string'))
            await send(message)
        await app(scope,receive,capture)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=tapped),base_url=ORIGIN) as client:
        for target,status in ((url,404),(url+'&extra=ignored',404),('/api/v1/execution-reference-media/'+IDENTIFIER+'?token='+TOKEN,401)):
            response=await client.get(target)
            assert response.status_code==status and TOKEN not in response.text
    assert observed==[b'',b'',b''] and app.state.workers.workers=={}
