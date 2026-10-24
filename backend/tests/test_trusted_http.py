"""Network-boundary tests use local fixture HTTP only; they do not qualify any model."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import asyncio
from threading import Thread
import pytest
from app.core.integrations.trusted_http import UnsafeTarget, request_bounded, request_isolated, resolve_target


@pytest.mark.asyncio
async def test_async_cancellation_kills_and_joins_network_child(monkeypatch):
    """Cancel after a real fixture receives the request and verify child termination."""
    from threading import Event
    from app.core.integrations.trusted_http import request_isolated_async
    received=Event();release=Event();children=[]
    original=asyncio.create_subprocess_exec
    async def capture(*args,**kwargs):
        """Capture the real child handle without replacing its network behavior."""
        child=await original(*args,**kwargs);children.append(child);return child
    monkeypatch.setattr(asyncio,'create_subprocess_exec',capture)
    class Slow(Handler):
        """Hold a real connection open until cancellation has been checked."""
        def do_GET(self):
            """Signal acceptance while the child is waiting on socket data."""
            received.set();release.wait(5)
    with server(Slow) as fixture:
        task=asyncio.create_task(request_isolated_async(f'http://127.0.0.1:{fixture.server_port}/',allowed_hosts={'127.0.0.1'},trusted_local_host='127.0.0.1',trusted_local_ports={fixture.server_port},timeout=5))
        try:
            for _ in range(200):
                if received.is_set():break
                await asyncio.sleep(.01)
            assert received.is_set()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
            assert len(children)==1 and children[0].returncode is not None
        finally:
            release.set()
            if not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)


@contextmanager
def server(handler):
    """Start an owned loopback fixture and shut it down after each test."""
    instance=ThreadingHTTPServer(('127.0.0.1',0),handler)
    thread=Thread(target=instance.serve_forever,daemon=True);thread.start()
    try:yield instance
    finally:instance.shutdown();instance.server_close();thread.join(timeout=3)


class Handler(BaseHTTPRequestHandler):
    """Fixture endpoints for bounded data and malicious redirects."""
    def do_GET(self):
        """Respond with fixture bytes, never generated media or model output."""
        if self.path=='/redirect-private':
            self.send_response(302);self.send_header('Location','https://169.254.169.254/latest/meta-data');self.end_headers();return
        self.send_response(200);self.send_header('Content-Length','100');self.end_headers();self.wfile.write(b'x'*100)
    def log_message(self,*args):
        """Keep fixture logs free from URLs and authorization values."""
        return


def test_loopback_requires_explicit_trusted_configuration():
    """Local addresses are denied by default and allowed only for the exact test host."""
    with pytest.raises(UnsafeTarget):resolve_target('https://127.0.0.1',{'127.0.0.1'})
    with server(Handler) as fixture:
        url=f'http://127.0.0.1:{fixture.server_port}/'
        result=request_bounded(url,allowed_hosts={'127.0.0.1'},trusted_local_host='127.0.0.1',trusted_local_ports={fixture.server_port},max_bytes=100)
        assert result.body==b'x'*100 and result.status==200


def test_redirect_cannot_reach_metadata_or_expand_trust():
    """Even an allowlisted metadata hostname cannot bypass its forbidden IP class."""
    with server(Handler) as fixture:
        with pytest.raises(UnsafeTarget):request_bounded(f'http://127.0.0.1:{fixture.server_port}/redirect-private',allowed_hosts={'127.0.0.1','169.254.169.254'},trusted_local_host='127.0.0.1',trusted_local_ports={fixture.server_port})


def test_content_bound():
    """Known oversized responses are rejected before their body is consumed."""
    with server(Handler) as fixture:
        with pytest.raises(UnsafeTarget):request_bounded(f'http://127.0.0.1:{fixture.server_port}/',allowed_hosts={'127.0.0.1'},trusted_local_host='127.0.0.1',trusted_local_ports={fixture.server_port},max_bytes=10)


@pytest.mark.parametrize('url',['file:///C:/secret','ftp://example.com/x','https://user:pass@example.com/x','https://unconfigured.example/x'])
def test_untrusted_url_types(url):
    """Schemes, embedded credentials and unconfigured hosts cannot become requests."""
    with pytest.raises(UnsafeTarget):resolve_target(url,{'example.com'})


def test_mixed_private_dns_answers_are_rejected(monkeypatch):
    """All DNS addresses must pass, rather than trusting only the first answer."""
    monkeypatch.setattr(socket,'getaddrinfo',lambda *args,**kwargs:[(2,1,6,'',('8.8.8.8',443)),(2,1,6,'',('10.0.0.1',443))])
    with pytest.raises(UnsafeTarget):resolve_target('https://fixture.example/x',{'fixture.example'})


def test_authorization_is_not_forwarded_across_redirect_origins():
    """A local fixture proves cross-origin redirects strip provider authorization."""
    seen=[]
    class Destination(Handler):
        """Capture only whether an Authorization header reached the other origin."""
        def do_GET(self):
            """Record a boolean, then serve the fixed body."""
            seen.append(any(self.headers.get(key) is not None for key in ['Authorization','X-API-Key','API-Key','X-Token']));super().do_GET()
    with server(Destination) as destination:
        class Redirect(Handler):
            """Redirect to a different local port, which is a different origin."""
            def do_GET(self):
                """Send no response body or model data."""
                self.send_response(302);self.send_header('Location',f'http://127.0.0.1:{destination.server_port}/');self.end_headers()
        with server(Redirect) as origin:
            request_bounded(f'http://127.0.0.1:{origin.server_port}/',allowed_hosts={'127.0.0.1'},trusted_local_host='127.0.0.1',trusted_local_ports={origin.server_port,destination.server_port},headers={'Authorization':'Bearer FIXTURE_ONLY','X-API-Key':'FIXTURE','API-Key':'FIXTURE','X-Token':'FIXTURE'})
    assert seen==[False]


def test_routing_headers_and_unconfigured_local_ports_are_rejected():
    """A local exception cannot reach arbitrary ports or accept a forged Host header."""
    with pytest.raises(UnsafeTarget):resolve_target('http://127.0.0.1:2375',{'127.0.0.1'},trusted_local_host='127.0.0.1',trusted_local_ports={8000})
    with pytest.raises(UnsafeTarget):request_bounded('https://fixture.example',allowed_hosts={'fixture.example'},headers={'Host':'metadata.internal'})


def test_isolated_network_child_returns_bounded_fixture():
    """The production transport wrapper uses a killable child, not a lingering thread."""
    with server(Handler) as fixture:
        result=request_isolated(f'http://127.0.0.1:{fixture.server_port}/',allowed_hosts={'127.0.0.1'},trusted_local_host='127.0.0.1',trusted_local_ports={fixture.server_port},max_bytes=100,timeout=5)
        assert result.status==200 and len(result.body)==100
