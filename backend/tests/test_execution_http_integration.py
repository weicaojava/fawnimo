"""Owned HTTP fixture -> isolated transport -> SQLite -> decoded artifact, without models."""
import json
from pathlib import Path
import os
import pytest
from app.config import settings
from app.core.integrations.polling_transport import PollingConfiguration,PollingTransport
from app.services.worker.durable_execution import DurableExecution
from app.services.worker.execution_budget import configure,reserve
from app.models.production import ProductionMedia
from tests.test_trusted_http import Handler,server
from tests.test_execution_artifacts import png
from tests.test_durable_execution import setup,read


@pytest.mark.asyncio
async def test_real_http_to_validated_artifact_with_restart(tmp_path,monkeypatch):
    """Restart between accepted submission and query; preserve one remote request."""
    monkeypatch.setattr(settings,'production_media_root',str(tmp_path/'media'))
    monkeypatch.setattr(settings,'production_probe_executable',os.environ.get('TEST_MEDIA_PROBE',str(Path(__file__).resolve().parents[4]/'ffmpeg.exe')))
    accepted=[]
    class Fixture(Handler):
        """Serve fixed PNG bytes and a test-only polling protocol."""
        def do_POST(self):
            """Record the stable submission ID once."""
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            accepted.append(body['request_id']);self.send_response(200);self.end_headers();self.wfile.write(b'{"id":"fixture-remote"}')
        def do_GET(self):
            """Return fixed fixture completion and bytes without invoking a model."""
            self.send_response(200);self.end_headers()
            if self.path=='/artifact':self.wfile.write(png());return
            self.wfile.write(json.dumps({'status':'succeeded','artifact_url':f'http://127.0.0.1:{self.server.server_port}/artifact','name':'fixture.png'}).encode())
    engine,factory,_,clock,_,_=await setup(tmp_path)
    try:
        with server(Fixture) as fixture:
            config=PollingConfiguration(f'http://127.0.0.1:{fixture.server_port}',frozenset({'127.0.0.1'}),'127.0.0.1',frozenset({fixture.server_port}))
            worker=DurableExecution(PollingTransport(config),factory=factory,clock=clock)
            job_id=await worker.create('p','http-fixture',{'prompt_revision':1})
            async with factory() as db:await configure(db,'p','TEST',100)
            async with factory() as db:await reserve(db,'p',job_id,10,'TEST')
            await worker.step(job_id)
            assert (await read(factory,job_id)).state=='running'
            replacement=DurableExecution(PollingTransport(config),factory=factory,clock=clock)
            clock.advance();await replacement.step(job_id);await replacement.step(job_id)
            job=await read(factory,job_id)
            assert job.state=='ready' and len(accepted)==1
            async with factory() as db:
                media=await db.get(ProductionMedia,job.media_id)
                assert media.project_id=='p' and media.size==len(png())
    finally:await engine.dispose()
