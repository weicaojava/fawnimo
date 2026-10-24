"""Application lifecycle and strict admission API tests use an owned fixture only."""
import asyncio
import pytest
from httpx import ASGITransport,AsyncClient
from sqlalchemy import text,select,func
from app.core.contracts.execution_capabilities import Capability
from app.models.execution import ExecutionJob
from app.services.worker.execution_admission import AdmissionPolicy
from app.services.worker.execution_budget import configure
from app.services.worker.execution_dispatcher import ExecutionDispatcher
from app.services.worker.execution_runtime import ExecutionRuntime
from app.services.worker.durable_execution import DurableExecution
from tests.test_durable_execution import setup,read,fixture_publisher


@pytest.mark.asyncio
async def test_unconfigured_api_never_admits_and_forbids_overrides(tmp_path):
    """An installation without a trusted runtime cannot create paid work."""
    from app.main import app
    previous=getattr(app.state,'execution_runtime',None)
    app.state.execution_runtime=None
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',trust_env=False) as client:
            response=await client.get('/api/v1/execution/availability')
            assert response.json()['data']['available'] is False
            body={'request_key':'key','model_id':'fixture','duration':5,'prompt':'test fixture'}
            response=await client.post('/api/v1/execution/projects/p/submissions',json=body)
            assert response.status_code==503
            for injected in [{'endpoint':'https://injected.invalid'},{'quote_minor':0},{'duration':True},{'references':[{'media_id':'x','kind':'image','url':'https://injected.invalid'}]}]:
                response=await client.post('/api/v1/execution/projects/p/submissions',json={**body,**injected})
                assert response.status_code==422
            response=await client.post('/api/v1/execution/callbacks',json={'status':'succeeded'})
            assert response.status_code==403
    finally:app.state.execution_runtime=previous


@pytest.mark.asyncio
async def test_restarted_different_account_cannot_execute_old_binding(tmp_path):
    """A changed trusted account/policy must not inherit an old queued request."""
    from dataclasses import replace
    from app.core.contracts.execution_binding import binding_fingerprint
    from app.services.worker.execution_admission import admit
    engine,factory,adapter,clock,_,_=await setup(tmp_path)
    try:
        policy=AdmissionPolicy(Capability('fixture',frozenset({5}),frozenset(),0),10,'TEST',3,binding_id='account-a-v1')
        async with factory() as db:await configure(db,'p','TEST',100)
        job_id=await admit(factory,'p','bound-job',{'model_id':'fixture','duration':5,'prompt':'fixture only'},policy)
        worker=DurableExecution(adapter,factory=factory,clock=clock,publisher=fixture_publisher)
        worker.allowed_bindings=frozenset({binding_fingerprint(replace(policy,binding_id='account-b-v1'))})
        await worker.step(job_id)
        assert adapter.sent==0 and (await read(factory,job_id)).reason=='runtime_binding_unavailable'
        worker.allowed_bindings=frozenset({binding_fingerprint(policy)})
        await worker.step(job_id)
        assert adapter.sent==1 and (await read(factory,job_id)).state=='running'
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_lifespan_admission_scan_shutdown_and_restart(tmp_path,monkeypatch):
    """Accepted admission is durable across runtime restart without a queue message."""
    from app.main import app,lifespan
    import app.core.db as core_db
    engine,factory,adapter,clock,_,_=await setup(tmp_path)
    previous=getattr(app.state,'execution_runtime',None)
    monkeypatch.setattr(core_db,'engine',engine)
    worker=DurableExecution(adapter,factory=factory,clock=clock,publisher=fixture_publisher)
    policies={'fixture':AdmissionPolicy(Capability('fixture',frozenset({5}),frozenset(),0),10,'TEST',3)}
    runtime=ExecutionRuntime(worker,policies,allow_submission=True,dispatcher=ExecutionDispatcher(worker,factory=factory,clock=clock,interval_seconds=.01))
    app.state.execution_runtime=runtime
    try:
        async with engine.begin() as connection:
            await connection.execute(text('CREATE TABLE production_schema(version INTEGER)'))
            await connection.execute(text('INSERT INTO production_schema VALUES(1)'))
        async with factory() as db:await configure(db,'p','TEST',100)
        async with lifespan(app):
            assert runtime.running
            async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',trust_env=False) as client:
                body={'request_key':'durable-admission','model_id':'fixture','duration':5,'prompt':'fixture only'}
                response=await client.post('/api/v1/execution/projects/p/submissions',json=body)
                assert response.status_code==201,response.text
                job_id=response.json()['data']['job_id']
                repeated=await client.post('/api/v1/execution/projects/p/submissions',json=body)
                assert repeated.json()['data']['job_id']==job_id
                for _ in range(200):
                    if (await read(factory,job_id)).state=='running':break
                    await asyncio.sleep(.01)
                assert (await read(factory,job_id)).state=='running'
        assert not runtime.running
        replacement=ExecutionRuntime(worker,policies,allow_submission=True,dispatcher=ExecutionDispatcher(worker,factory=factory,clock=clock,interval_seconds=.01))
        app.state.execution_runtime=replacement
        clock.advance()
        async with lifespan(app):
            for _ in range(200):
                if (await read(factory,job_id)).state=='ready':break
                await asyncio.sleep(.01)
            assert (await read(factory,job_id)).state=='ready'
        assert not replacement.running and adapter.sent==1
        async with factory() as db:assert await db.scalar(select(func.count()).select_from(ExecutionJob))==1
    finally:
        await runtime.stop()
        app.state.execution_runtime=previous
        await engine.dispose()
