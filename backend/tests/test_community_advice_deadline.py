"""Actual owned loopback HTTP timing checks: slow valid model headers fit the existing overall deadline, while trickled headers/body cannot extend it."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import threading
import time
from types import SimpleNamespace
import pytest
from app.services.community.advice_child import local_json


@contextmanager
def timed_origin(mode):
    """Own one finite local socket/thread and interrupt deliberately delayed output on cleanup; no AI model, credentials or outside network is involved."""
    state=SimpleNamespace(stop=threading.Event(),requests=0)
    class Handler(BaseHTTPRequestHandler):
        """Produce bounded fixed JSON, deliberately delaying either complete valid headers or only a few original response bytes."""
        def log_message(self,*_args):
            """Suppress fixture diagnostics without changing its actual wire behavior."""
        def do_POST(self):
            """Count the one exact model route and retain a valid response even when its first byte arrives after the connect timeout."""
            assert self.path=='/api/chat';self.rfile.read(int(self.headers['Content-Length']));state.requests+=1
            body=b' '*64+b'{"ok":true}'
            headers=b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: '+str(len(body)).encode()+b'\r\nConnection: close\r\n\r\n'
            try:
                if mode=='late_headers':
                    if state.stop.wait(5.3):return
                    self.connection.sendall(headers+body)
                elif mode=='trickle_headers':
                    for value in headers[:64]:
                        self.connection.sendall(bytes([value]))
                        if state.stop.wait(.04):return
                    self.connection.sendall(headers[64:]+body)
                elif mode=='trickle_body':
                    self.connection.sendall(headers)
                    for value in body[:64]:
                        self.connection.sendall(bytes([value]))
                        if state.stop.wait(.04):return
                    self.connection.sendall(body[64:])
                else:self.connection.sendall(headers+body)
            except (BrokenPipeError,ConnectionResetError):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield server.server_port,state
    finally:
        state.stop.set();server.shutdown();server.server_close();thread.join(timeout=2)
        assert not thread.is_alive()


def test_valid_delayed_model_headers_fit_original_overall_deadline():
    """A5.3-second valid response is below the existing10-second request budget and must not fail at the unrelated5-second connection cap."""
    with timed_origin('late_headers') as (port,state):
        started=time.monotonic();value,_=local_json(port,'POST','/api/chat',b'{}',started+10)
        assert value=={'ok':True} and state.requests==1 and 5<=time.monotonic()-started<10


@pytest.mark.parametrize('mode',['trickle_headers','trickle_body'])
def test_continuous_small_response_bytes_cannot_extend_original_deadline(mode):
    """An actual endpoint sending every40ms cannot defeat the original250ms total deadline by resetting a per-read socket timeout."""
    with timed_origin(mode) as (port,state):
        started=time.monotonic()
        with pytest.raises(TimeoutError):local_json(port,'POST','/api/chat',b'{}',started+.25)
        assert state.requests==1 and time.monotonic()-started<1


def test_expired_deadline_never_sends_a_new_model_request():
    """A fully spent original budget fails before connecting rather than granting a fresh per-stage timeout."""
    with timed_origin('ready') as (port,state):
        with pytest.raises(TimeoutError):local_json(port,'POST','/api/chat',b'{}',time.monotonic()-1)
        assert state.requests==0
