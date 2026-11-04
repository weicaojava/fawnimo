"""Actual owned loopback HTTP through the isolated production transport; authored protocol/bytes only, not a real model or decoded-video claim."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from types import SimpleNamespace
import threading

import pytest

from app.core.contracts.wan30 import Wan30Configuration,REFERENCE_PROTOCOL,TEXT_PROTOCOL
from app.core.contracts.seedance_references import VideoReferenceSelection
from app.core.integrations.wan30 import Wan30DurableTransport,Wan30ProtocolError
from tests.test_wan30_payload import text_snapshot,image_proof,reference_snapshot


@contextmanager
def owned_wan_server(*,fail_create=False):
    """Own one ephemeral exact-port fixture and join its request threads before returning; never contact a vendor."""
    state=SimpleNamespace(calls=[],posts=0,artifact=b'AUTHORED_WAN_TRANSPORT_BYTES')
    class Handler(BaseHTTPRequestHandler):
        """Expose only the declared create/query/result paths with authored response witnesses."""
        def log_message(self,*_args):
            """Keep fake keys, bodies and ephemeral URLs out of unrelated logs."""
            return
        def send(self,value,*,status=200,content_type='application/json'):
            """Send one finite closed response; cancellation may close an owned socket first."""
            raw=value if isinstance(value,bytes) else json.dumps(value).encode('utf8')
            self.send_response(status);self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(raw)));self.send_header('Connection','close');self.end_headers()
            try:self.wfile.write(raw)
            except (BrokenPipeError,ConnectionResetError):pass
        def do_POST(self):
            """Record one actual owned create and its exact original wire body, without interpreting the prompt."""
            state.posts+=1
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state.calls.append({'method':'POST','path':self.path,'headers':dict(self.headers),'body':body})
            if self.path!='/api/v1/services/aigc/video-generation/video-synthesis':return self.send({},status=404)
            if fail_create:return self.send({'request_id':'owned-error-witness','message':'AUTHORED_PRIVATE_ERROR'},status=500)
            self.send({'request_id':'owned-create-witness','output':{'task_id':'owned-wan-task','task_status':'PENDING'}})
        def do_GET(self):
            """Serve original-task metadata and bytes separately, capturing whether any credential reaches the result path."""
            state.calls.append({'method':'GET','path':self.path,'headers':dict(self.headers)})
            if self.path=='/api/v1/tasks/owned-wan-task':
                return self.send({'request_id':'owned-query-witness','output':{'task_id':'owned-wan-task','task_status':'SUCCEEDED',
                    'video_url':f'http://127.0.0.1:{self.server.server_address[1]}/result.mp4?token=AUTHORED_RESULT'}})
            if self.path=='/result.mp4?token=AUTHORED_RESULT':return self.send(state.artifact,content_type='video/mp4')
            self.send({},status=404)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield server.server_port,state
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)
        assert not thread.is_alive()


def local_configuration(port,protocol):
    """Name the exact separate fixture control/download authority with a clearly fake key."""
    return Wan30Configuration(api_key='AUTHORED_WAN_CREDENTIAL',service_version='owned-service-v1',account_version='owned-account-v1',
        base_url=f'http://127.0.0.1:{port}/api/v1',protocol=protocol,trusted_local_host='127.0.0.1',
        trusted_local_port=port,download_hosts=frozenset({'127.0.0.1'}))


@pytest.mark.parametrize('protocol',[TEXT_PROTOCOL,REFERENCE_PROTOCOL],ids=['text','first-frame'])
@pytest.mark.asyncio
async def test_actual_owned_http_create_query_download_and_original_reads(protocol):
    """Use the real isolated HTTP child once for creation, then original reads and credential-free result delivery."""
    with owned_wan_server() as(port,state):
        witnesses=[];options={};snapshot=text_snapshot()
        if protocol==REFERENCE_PROTOCOL:
            proof=image_proof();snapshot=reference_snapshot((proof,),('first_frame',),'first_frame')
            options={'project_id':'authored-project','selection':VideoReferenceSelection.model_validate(snapshot['video_reference']),'references':(proof,)}
        async def observe(value):
            """Retain only the one original create-response identity."""
            witnesses.append(value)
        transport=Wan30DurableTransport(local_configuration(port,protocol),on_submission_response=observe,**options)
        identifier=await transport.submit(snapshot,'original-owner-request')
        result=await transport.query(identifier)
        artifact=await transport.download(result,'owned-media','authored-project')
        assert identifier=='owned-wan-task' and artifact.data==state.artifact and state.posts==1
        assert await transport.reconcile('original-owner-request') is None
        assert await transport.cancel(identifier) is False
        assert witnesses==['owned-create-witness'] and state.posts==1
        post=next(row for row in state.calls if row['method']=='POST')
        assert post['headers']['X-DashScope-Async']=='enable' and post['headers']['Authorization']=='Bearer AUTHORED_WAN_CREDENTIAL'
        assert post['body']['parameters']['audio'] is False and post['body']['input']['prompt']==snapshot['prompt']
        result_requests=[row for row in state.calls if row['path'].startswith('/result.mp4')]
        assert len(result_requests)==1 and all(key.lower()!='authorization' for key in result_requests[0]['headers'])
        assert all(row['method'] in {'GET','POST'} for row in state.calls)


@pytest.mark.asyncio
async def test_actual_owned_create_error_retains_original_witness_without_repeating():
    """A real500 response through the owned child remains unconfirmed even with a valid original request witness."""
    with owned_wan_server(fail_create=True) as(port,state):
        witnesses=[]
        async def observe(value):
            """Record an authored response identity without returning raw provider errors."""
            witnesses.append(value)
        transport=Wan30DurableTransport(local_configuration(port,TEXT_PROTOCOL),on_submission_response=observe)
        with pytest.raises(Wan30ProtocolError) as caught:await transport.submit(text_snapshot(),'original-owner-request')
        assert 'AUTHORED_PRIVATE' not in str(caught.value) and witnesses==['owned-error-witness']
        assert await transport.reconcile('original-owner-request') is None and state.posts==1
