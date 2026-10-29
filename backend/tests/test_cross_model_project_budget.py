"""Actual text and image runtimes share one selected project's ledger without a workspace spending cap."""
from fastapi import FastAPI
import httpx
import pytest
from sqlalchemy import select,update

from app.api.v1.routes.agents import router as configs
from app.api.v1.routes.agent_execution import router as agents
from app.core.contracts.model_accounting import ModelPriceConfiguration,ModelPriceRule
from app.dependencies import get_db
from app.models.execution_budget import ExecutionBudget
from app.models.llm import Model,ModelSettings,Provider
from app.models.model_job_charge import ModelJobCharge
from app.services.agent_execution import AgentExecutionRuntime
from app.services.worker.model_prices import ModelPriceRegistry
from tests.test_agent_execution import FIXTURE_KEY,agent,fixture_price,local_model,terminal as text_terminal
from tests.test_asset_image_generation import fixture_client,image_fixture_price,model_server,submit as image_submit,terminal as image_terminal
from tests.test_image_project_accounting import image_bill,wallet


@pytest.mark.asyncio
async def test_image_unknown_hold_blocks_text_in_same_project_until_explicit_bill_frees_capacity(tmp_path,monkeypatch):
    """Both real local HTTP protocols charge the same project; another project's full balance cannot subsidize either."""
    with model_server() as image_server,local_model() as text_server:
        async with fixture_client(tmp_path,monkeypatch,image_server) as (images,factory,slots,image_runtimes):
            async with factory() as db:
                db.add(Provider(id='fixture-provider',name='openai',base_url=f"http://127.0.0.1:{text_server['port']}/v1",api_key=FIXTURE_KEY,status='testing'))
                await db.flush()
                db.add(Model(id='fixture-model',name='local-fixture-model',category='text',provider_id='fixture-provider',params={}))
                await db.flush()
                await db.execute(update(ModelSettings).where(ModelSettings.id==1).values(default_text_model_id='fixture-model'))
                await db.execute(update(ExecutionBudget).where(ExecutionBudget.project_id=='image-project').values(limit_minor=20))
                await db.commit()
            prices=ModelPriceRegistry(ModelPriceConfiguration(enabled=True,rules=[
                ModelPriceRule(quote=fixture_price(),kinds=['agent_text','model_connection_test']),
                ModelPriceRule(quote=image_fixture_price(),kinds=['asset_image'])]))
            image_runtimes[0].price_registry=prices
            runtime=AgentExecutionRuntime(factory,poll_interval=.02,trusted_local_host='127.0.0.1',
                trusted_local_ports=frozenset({text_server['port']}),price_registry=prices)
            async def database():
                """Commit the same actual SQLite session only after successful route response construction."""
                async with factory() as db:
                    try:
                        yield db;await db.commit()
                    except Exception:
                        await db.rollback();raise
            app=FastAPI();app.include_router(configs,prefix='/api/v1');app.include_router(agents,prefix='/api/v1')
            app.dependency_overrides[get_db]=database;app.state.agent_execution_runtime=runtime
            await runtime.start()
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://text-fixture') as text:
                    image=(await image_submit(images,'actor',slots['actor'])).json()['data']
                    assert (await image_terminal(images,image['id']))['status']=='candidate'
                    assert await wallet(factory)==(20,15,0)
                    config=await agent(text,nodes=1)
                    payload={'request_key':'shared-project-run','input_text':'Authored fixture text','expected_version':config['version'],
                        'confirm_model_call':True,'project_id':'image-project','expected_price_fingerprint':fixture_price().fingerprint}
                    path='/api/v1/agent-execution/agents/'+config['id']+'/runs'
                    assert (await text.post(path,json=payload)).status_code==409
                    assert not text_server['calls'] and await wallet(factory,'other-project')==(1000,0,0)
                    assert (await image_bill(factory,5))[0]['status']=='applied'
                    assert await wallet(factory)==(20,0,5)
                    response=await text.post(path,json=payload)
                    assert response.status_code==201,response.text
                    result=await text_terminal(text,response.json()['data']['id'])
                    assert result['status']=='succeeded' and result['project_id']=='image-project'
                    assert await wallet(factory)==(20,10,5) and await wallet(factory,'other-project')==(1000,0,0)
                    assert len(text_server['calls'])==len(image_server['calls'])==1
                    async with factory() as db:
                        bound=(await db.execute(select(ModelJobCharge.job_kind,ModelJobCharge.project_id))).all()
                        assert set(bound)=={('agent','image-project'),('image','image-project')}
            finally:
                await runtime.stop()
