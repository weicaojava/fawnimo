"""A closed paid gateway cannot present a priced worker's readiness as permission to submit."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import threading
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from app.controlplane.gateway import create_gateway


@contextmanager
def priced_metadata_worker():
    """Serve authored ready metadata on a real loopback socket; this is not a model or fixed-worker qualification."""
    state={'get':0,'post':0}
    class Handler(BaseHTTPRequestHandler):
        """Return only deliberate metadata and count any unexpected paid intent reaching the stub."""
        def log_message(self,*_):
            """Keep headers and synthetic account context out of test logs."""
        def do_GET(self):
            """Represent a worker with a configured positive quote while gateway admission is closed."""
            state['get']+=1
            body=json.dumps({'code':0,'message':'ok','data':{'available':True,'message':'Authored ready fixture',
                'models':[{'id':'synthetic-model','configured':True,'priced':True,'max_call_minor':15,'price_fingerprint':'a'*64}]}}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)))
            self.end_headers();self.wfile.write(body)
        def do_POST(self):
            """A closed gateway must reject the request before reaching this inert handler."""
            state['post']+=1;self.send_error(500)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        yield state,server.server_port
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)


@pytest.mark.parametrize('path',['agent-execution','asset-image-generation','script-candidates'])
@pytest.mark.parametrize('suffix',['','/'])
def test_gateway_overrides_ready_state_but_preserves_nonsecret_quote_and_rejects_paid_post(tmp_path,path,suffix):
    """Actual login/proxy HTTP keeps positive pricing visible while the existing submit gate remains503."""
    with priced_metadata_worker() as (state,port):
        class Pool:
            """Expose only the owned loopback metadata server without starting any model runtime."""
            async def get(self,workspace):
                """Return an inert local target after normal identity selection."""
                return SimpleNamespace(port=port,token='synthetic-internal-token')
            async def close(self):
                """The outer fixture owns the server lifetime."""
        gateway=create_gateway(tmp_path/'root',workers=Pool(),secure_cookies=False)
        gateway.state.identity_store.provision('pricing-status','synthetic-status-password')
        with TestClient(gateway) as client:
            identity=client.post('/api/v1/identity/login',json={'account':'pricing-status','password':'synthetic-status-password'}).json()['data']
            headers={'X-Identity-Id':identity['user']['id'],'X-Workspace-Id':identity['workspace_id']}
            availability=path+'/availability' if path!='script-candidates' else 'script-preparation/projects/p/chapters/c/candidates/availability'
            response=client.get('/api/v1/'+availability+suffix,headers=headers)
            assert response.status_code==200 and response.headers['Cache-Control']=='no-store'
            data=response.json()['data']
            assert not data['available'] and '尚未开放' in data['message']
            assert data['models'][0]['priced'] and data['models'][0]['max_call_minor']==15 and data['models'][0]['price_fingerprint']=='a'*64
            target={'agent-execution':'agent-execution/agents/synthetic/runs',
                'asset-image-generation':'asset-image-generation/assets/actor/synthetic/slots/1/jobs',
                'script-candidates':'script-preparation/projects/p/chapters/c/candidates'}[path]
            assert client.post('/api/v1/'+target+suffix,json={},headers=headers).status_code==503
            assert state=={'get':1,'post':0}
