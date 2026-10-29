"""Actual Agent HTTP/scanner work must consume its original project batch and keep uncertainty until explicit billing."""
import asyncio

import pytest
from sqlalchemy import func,select,update

from app.core.contracts.model_accounting import BillingStatementContext,ModelPriceConfiguration,ModelPriceRule
from app.models.agent_execution import AgentRun
from app.models.execution_budget import ExecutionBudget
from app.models.llm import Provider
from app.models.model_charge import ModelCallCharge
from app.models.model_job_charge import ModelJobCharge
from app.services.worker.model_billing import import_statement,reconcile_statement
from app.services.worker.model_prices import ModelPriceRegistry
from tests.test_agent_execution import agent,fee_fields,fixture_price,isolated_client,local_model,submit,terminal


async def wallet(factory,project='fixture-project'):
    """Read actual counters separately from HTTP request and worker transactions."""
    async with factory() as db:
        budget=await db.get(ExecutionBudget,project)
        return budget.limit_minor,budget.reserved_minor,budget.spent_minor


@pytest.mark.asyncio
async def test_success_keeps_full_unknown_hold_and_exact_response_bill_settles_only_original_project(tmp_path):
    """Real two-node results do not infer fees; explicit private response IDs later join a synthetic bill once."""
    with local_model() as server:
        async with isolated_client(tmp_path,server,workers=2) as (client,factory,runtimes):
            config=await agent(client)
            first,second=await asyncio.gather(submit(client,config),submit(client,config))
            assert first.status_code==second.status_code==201 and first.json()['data']['id']==second.json()['data']['id']
            result=await terminal(client,first.json()['data']['id'])
            assert result['status']=='succeeded' and result['project_id']=='fixture-project' and result['authorized_max_minor']=='20'
            assert len(server['calls'])==2 and await wallet(factory)==(1000,20,0) and await wallet(factory,'other-project')==(1000,0,0)
            assert 'fixture-chat-' not in str(result)
            async with factory() as db:
                assert await db.scalar(select(func.count()).select_from(ModelJobCharge))==1
                evidence=(await db.execute(select(ModelCallCharge.provider_request_id,ModelCallCharge.dispatch_state))).all()
                assert set(evidence)=={('fixture-chat-1','accepted'),('fixture-chat-2','accepted')}
            content=b'row_reference,provider_request_id,status,currency,amount_minor\nfirst,fixture-chat-1,charged,CNY,6\nsecond,fixture-chat-2,charged,CNY,8\n'
            statement=await import_statement(factory,context=BillingStatementContext(provider_id='fixture-provider',provider_connection_version=1,
                account_version='synthetic-text-account',statement_key='self-authored-loopback-bill'),normalized_csv=content,
                original_content=b'Authored fixture bill; no actual supplier or external spending.')
            assert all(row['status']=='applied' for row in await reconcile_statement(factory,statement))
            assert all(row['replayed'] for row in await reconcile_statement(factory,statement))
            assert await wallet(factory)==(1000,0,14) and await wallet(factory,'other-project')==(1000,0,0)
            assert (await submit(client,config)).json()['data']['id']==result['id'] and len(server['calls'])==2


@pytest.mark.asyncio
async def test_whole_plan_insufficient_project_budget_rolls_back_every_attempt_and_fee(tmp_path):
    """A project with fifteen minor units cannot admit two ten-minor calls or borrow its sibling's balance."""
    with local_model() as server:
        async with isolated_client(tmp_path,server) as (client,factory,runtimes):
            config=await agent(client)
            async with factory() as db:
                await db.execute(update(ExecutionBudget).where(ExecutionBudget.project_id=='fixture-project').values(limit_minor=15))
                await db.commit()
            response=await submit(client,config)
            assert response.status_code==409
            async with factory() as db:
                assert await db.scalar(select(func.count()).select_from(AgentRun))==0
                assert await db.scalar(select(func.count()).select_from(ModelJobCharge))==0
                assert await db.scalar(select(func.count()).select_from(ModelCallCharge))==0
            assert await wallet(factory)==(15,0,0) and await wallet(factory,'other-project')==(1000,0,0) and not server['calls']


@pytest.mark.asyncio
async def test_unpriced_or_stale_confirmed_policy_never_creates_a_task_or_calls_http(tmp_path):
    """A key, positive budget or client fingerprint cannot supply a missing server price."""
    with local_model() as server:
        async with isolated_client(tmp_path,server) as (client,factory,runtimes):
            config=await agent(client,nodes=1)
            saved=runtimes[0].price_registry;runtimes[0].price_registry=ModelPriceRegistry()
            availability=(await client.get('/api/v1/agent-execution/availability')).json()['data']
            assert not availability['available'] and availability['models'][0]['configured'] and not availability['models'][0]['priced']
            assert (await submit(client,config)).status_code==503
            runtimes[0].price_registry=saved
            response=await client.post('/api/v1/agent-execution/agents/'+config['id']+'/runs',json={
                'request_key':'stale-policy','input_text':'Synthetic','expected_version':config['version'],'confirm_model_call':True,
                **fee_fields(),'expected_price_fingerprint':'f'*64})
            assert response.status_code==409 and await wallet(factory)==(1000,0,0) and not server['calls']


@pytest.mark.asyncio
async def test_queued_cancel_and_changed_price_release_unsent_original_units(tmp_path):
    """A paused scanner cannot use a replacement quote to execute a previously authorized batch."""
    with local_model() as server:
        async with isolated_client(tmp_path,server) as (client,factory,runtimes):
            runtime=runtimes[0];await runtime.stop();runtime.running=True
            config=await agent(client)
            queued=(await submit(client,config)).json()['data']
            assert await wallet(factory)==(1000,20,0)
            assert (await client.post('/api/v1/agent-execution/runs/'+queued['id']+'/cancel')).status_code==200
            assert await wallet(factory)==(1000,0,0)
            queued=(await submit(client,config,key='price-changed')).json()['data']
            changed=fixture_price().model_copy(update={'max_call_minor':11})
            runtime.price_registry=ModelPriceRegistry(ModelPriceConfiguration(enabled=True,rules=[ModelPriceRule(quote=changed,kinds=['agent_text'])]))
            await runtime.tick()
            assert (await terminal(client,queued['id']))['status']=='failed'
            assert await wallet(factory)==(1000,0,0) and not server['calls']


@pytest.mark.asyncio
async def test_cancel_inflight_keeps_prepared_fee_but_releases_future_nodes(tmp_path):
    """Only the actual possible-acceptance unit remains held after cancelling a two-node run."""
    with local_model() as server:
        server['mode']='block'
        async with isolated_client(tmp_path,server) as (client,factory,runtimes):
            config=await agent(client)
            result=(await submit(client,config)).json()['data']
            for _ in range(100):
                if server['received'].is_set():break
                await asyncio.sleep(.03)
            assert server['received'].is_set()
            assert (await client.post('/api/v1/agent-execution/runs/'+result['id']+'/cancel')).status_code==200
            assert await wallet(factory)==(1000,10,0) and await wallet(factory,'other-project')==(1000,0,0)
            server['release'].set();await asyncio.sleep(.25)
            assert (await terminal(client,result['id']))['status']=='cancelled' and len(server['calls'])==1
            assert await wallet(factory)==(1000,10,0)


@pytest.mark.asyncio
async def test_key_rotation_after_first_call_keeps_old_account_evidence_and_never_sends_second_node(tmp_path):
    """Captured old credentials can finish one prepared call; later nodes stop and their unspent holds release."""
    with local_model() as server:
        server['mode']='block'
        async with isolated_client(tmp_path,server) as (client,factory,runtimes):
            config=await agent(client)
            result=(await submit(client,config)).json()['data']
            for _ in range(100):
                if server['received'].is_set():break
                await asyncio.sleep(.03)
            assert server['received'].is_set()
            async with factory() as db:
                await db.execute(update(Provider).where(Provider.id=='fixture-provider').values(api_key='rotated-synthetic-key'))
                await db.commit()
            server['release'].set()
            final=await terminal(client,result['id'])
            assert final['status']=='failed' and len(server['calls'])==1 and server['calls'][0]['authenticated']
            assert await wallet(factory)==(1000,10,0)
            async with factory() as db:
                recorded=await db.scalar(select(ModelCallCharge).where(ModelCallCharge.provider_request_id=='fixture-chat-1'))
                assert recorded is not None and recorded.provider_connection_version==1


@pytest.mark.asyncio
async def test_connection_probe_has_its_own_quote_and_rejects_workflow_price_confirmation(tmp_path):
    """The same model's eight-token probe reserves its explicit protocol bound, never the workflow's price."""
    with local_model() as server:
        async with isolated_client(tmp_path,server) as (client,factory,runtimes):
            probe=fixture_price().model_copy(update={'max_call_minor':3,'source_note':'Authored probe-specific price'})
            runtimes[0].price_registry=ModelPriceRegistry(ModelPriceConfiguration(enabled=True,rules=[
                ModelPriceRule(quote=fixture_price(),kinds=['agent_text']),
                ModelPriceRule(quote=probe,kinds=['model_connection_test'])]))
            ready=(await client.get('/api/v1/agent-execution/availability',params={'call_kind':'model_connection_test'})).json()['data']
            assert ready['available'] and ready['models'][0]['max_call_minor']==3
            assert ready['models'][0]['price_fingerprint']==probe.fingerprint
            endpoint='/api/v1/agent-execution/models/fixture-model/connection-tests'
            body={'request_key':'protocol-price','confirm_model_call':True,**fee_fields()}
            assert (await client.post(endpoint,json=body)).status_code==409
            assert not server['calls'] and await wallet(factory)==(1000,0,0)
            body['expected_price_fingerprint']=probe.fingerprint
            response=await client.post(endpoint,json=body)
            assert response.status_code==201,response.text
            result=await terminal(client,response.json()['data']['id'])
            assert result['status']=='succeeded' and result['authorized_max_minor']=='3'
            assert len(server['calls'])==1 and server['calls'][0]['body']['max_tokens']==8
            assert await wallet(factory)==(1000,3,0) and await wallet(factory,'other-project')==(1000,0,0)
