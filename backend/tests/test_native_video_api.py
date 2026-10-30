"""Managed native HTTP contracts bind trusted actors, exact confirmations and recovery before current eligibility."""
from fastapi import FastAPI,HTTPException
from httpx import ASGITransport,AsyncClient
import pytest
from sqlalchemy import select,update

from app.api import execution,execution_submission,shot_plan,video_runtime_setup
from app.api.v1.routes.film.task_status import TaskCancelRequest,cancel_task
from app.config import settings
from app.dependencies import get_db
from app.models.execution import ExecutionJob
from app.models.llm import Provider
from app.models.native_video_charge import NativeVideoChargeBatch
from app.models.studio import Character
from tests.test_native_video_project_accounting import setup,SNAPSHOT,counters
from tests.test_native_video_planned_accounting import planned_setup


def application(factory,runtime,registry):
    """Use real routes with one explicitly owned fixture DB, without starting global application services."""
    app=FastAPI();app.state.execution_runtime=runtime;app.state.model_prices=registry
    app.state.native_video_history_factory=factory
    for router in (execution.router,execution_submission.router,shot_plan.router,video_runtime_setup.router):app.include_router(router,prefix='/api/v1')
    app.dependency_overrides[shot_plan.get_shot_plan_factory]=lambda:factory
    app.dependency_overrides[video_runtime_setup.get_video_setup_factory]=lambda:factory
    async def session():
        """Keep the route's intentionally unused control dependency scoped to this fixture engine."""
        async with factory() as db:
            try:yield db;await db.commit()
            except BaseException:await db.rollback();raise
    app.dependency_overrides[get_db]=session
    return app


@pytest.mark.asyncio
async def test_direct_http_requires_exact_confirmation_and_recovers_with_runtime_off_without_borrowing_current_connection(tmp_path,monkeypatch):
    """A lost response recovers the same original job before current authorization; changed body/actor claims cannot create another fee."""
    monkeypatch.setattr(settings,'workspace_id',None)
    engine,factory,quote,accountant,_worker,runtime,response,_clock=await setup(tmp_path)
    app=application(factory,runtime,accountant.registry)
    body={**SNAPSHOT,'request_key':'native-api','confirm_project_charges':True,'expected_price_fingerprint':quote.fingerprint}
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://fixture.test') as client:
            path='/api/v1/execution/projects/a/submissions'
            assert (await client.post(path,json={**body,'confirm_project_charges':False})).status_code==422
            assert (await client.post(path,json={**body,'quote_minor':1})).status_code==422
            accepted=await client.post(path,json=body,headers={'x-identity-id':'public-looking-forged-actor'})
            assert accepted.status_code==201
            identifier=accepted.json()['data']['job_id']
            async with factory() as db:
                batch=await db.scalar(select(NativeVideoChargeBatch))
                assert batch.submitted_by=='local_operator'
                await db.execute(update(Provider).where(Provider.id=='p').values(api_key='rotated-native-after-acceptance'));await db.commit()
            app.state.execution_runtime=None;accountant.registry.rules.clear()
            recovered=await client.post(path,json=body)
            assert recovered.status_code==201 and recovered.json()['data']['job_id']==identifier
            for changed in ({'prompt':'another frozen action'},{'expected_price_fingerprint':'a'*64},{'confirm_project_charges':False}):
                assert (await client.post(path,json={**body,**changed})).status_code==409
            assert (await client.post('/api/v1/execution/projects/b/submissions',json=body)).status_code==503
        assert response.calls==[] and await counters(factory)==(25,0,1,1) and await counters(factory,'b')==(0,0,0,0)
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_planned_http_freezes_original_actor_and_recovers_after_source_drift_and_runtime_shutdown(tmp_path,monkeypatch):
    """Both public native admission paths use the same exact project fee contract; fresh plan state does not replace accepted intent."""
    monkeypatch.setattr(settings,'workspace_id',None)
    values,selection=await planned_setup(tmp_path,count=1)
    engine,factory,quote,accountant,_worker,runtime,response,_clock=values
    app=application(factory,runtime,accountant.registry)
    body={'request_key':'native-plan-api','confirm_project_charges':True,'expected_price_fingerprint':quote.fingerprint}
    path=f"/api/v1/shot-plans/projects/a/plans/{selection['plan_id']}/shots/s1/submissions"
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://fixture.test') as client:
            first=await client.post(path,json=body)
            assert first.status_code==201
            async with factory() as db:
                await db.execute(update(Character).where(Character.id=='hero').values(description='Edited after original confirmation'));await db.commit()
            app.state.execution_runtime=None
            recovered=await client.post(path,json=body)
            assert recovered.status_code==201 and recovered.json()['data']==first.json()['data']
            assert (await client.post(path,json={**body,'confirm_project_charges':False})).status_code==409
        assert response.calls==[] and await counters(factory)==(25,0,1,1)
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_native_models_read_and_task_controls_never_expose_bill_ids_or_use_the_legacy_cancel_bypass(tmp_path,monkeypatch):
    """Safe model selection is inert; old film cancellation remains blocked and actual project cancellation releases the common call once."""
    monkeypatch.setattr(settings,'workspace_id',None)
    engine,factory,quote,accountant,_worker,runtime,response,_clock=await setup(tmp_path)
    app=application(factory,runtime,accountant.registry)
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://fixture.test') as client:
            models=await client.get('/api/v1/video-runtime/models')
            assert models.status_code==200 and models.json()['data'][0]['price_fingerprint']==quote.fingerprint
            assert 'synthetic-key' not in models.text
            accepted=await client.post('/api/v1/execution/projects/a/submissions',json={**SNAPSHOT,'request_key':'cancel-api',
                'confirm_project_charges':True,'expected_price_fingerprint':quote.fingerprint})
            identifier=accepted.json()['data']['job_id']
            async with factory() as db:
                job=await db.get(ExecutionJob,identifier)
                with pytest.raises(HTTPException) as blocked:await cancel_task(job.task_id,TaskCancelRequest(),db)
                assert blocked.value.status_code==409
            assert await counters(factory)==(25,0,1,1)
            wrong=await client.post('/api/v1/execution/projects/b/jobs/'+identifier+'/cancel')
            assert wrong.status_code==404
            cancelled=await client.post('/api/v1/execution/projects/a/jobs/'+identifier+'/cancel')
            assert cancelled.status_code==200 and cancelled.json()['data']['state']=='cancelled'
            assert (await client.post('/api/v1/execution/projects/a/jobs/'+identifier+'/cancel')).status_code==200
            assert 'native-bill-' not in cancelled.text and 'synthetic-key' not in cancelled.text
        assert await counters(factory)==(0,0,1,1) and response.calls==[]
    finally:await engine.dispose()
