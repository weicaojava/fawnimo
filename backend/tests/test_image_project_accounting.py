"""Actual image candidates/adoption keep their project hold until explicit response-matched billing."""
import asyncio

import pytest
from sqlalchemy import func,select,update

from app.core.contracts.model_accounting import BillingStatementContext
from app.models.asset_image_generation import AssetImageJob
from app.models.execution_budget import ExecutionBudget
from app.models.model_charge import ModelCallCharge
from app.models.model_job_charge import ModelJobCharge
from app.services.worker.model_billing import import_statement,reconcile_statement
from app.services.worker.model_prices import ModelPriceRegistry
from tests.test_asset_image_generation import adoption_body,fixture_client,image_fee_fields,model_server,submit,terminal


async def wallet(factory,project='image-project'):
    """Read a project's committed original counters independently from its business result."""
    async with factory() as db:
        row=await db.get(ExecutionBudget,project)
        return row.limit_minor,row.reserved_minor,row.spent_minor


async def image_bill(factory,amount=9,*,status='charged'):
    """Match a self-authored explicit bill to the known fixture response; never a real tariff or statement."""
    content=('row_reference,provider_request_id,status,currency,amount_minor\nrow-1,fixture-image-1,'+status+',CNY,'+str(amount)+'\n').encode()
    statement=await import_statement(factory,context=BillingStatementContext(provider_id='image-provider',provider_connection_version=1,
        account_version='synthetic-image-account',statement_key='synthetic-image-bill'),normalized_csv=content,
        original_content=b'Authored protocol fixture bill; no external supplier or model spend.')
    return await reconcile_statement(factory,statement)


@pytest.mark.asyncio
async def test_candidate_adoption_and_bill_settlement_do_not_reassign_original_project(tmp_path,monkeypatch):
    """Successful decoded bytes and explicit adoption never infer zero or actual fees."""
    with model_server() as server:
        async with fixture_client(tmp_path,monkeypatch,server,workers=2) as (client,factory,slots,runtimes):
            first,second=await asyncio.gather(submit(client,'actor',slots['actor']),submit(client,'actor',slots['actor']))
            assert first.status_code==second.status_code==201 and first.json()['data']['id']==second.json()['data']['id']
            row=await terminal(client,first.json()['data']['id'])
            assert row['status']=='candidate' and row['project_id']=='image-project' and row['authorized_max_minor']=='15'
            assert await wallet(factory)==(1000,15,0) and await wallet(factory,'other-project')==(1000,0,0)
            assert len(server['calls'])==1 and (await client.get(row['content_url'])).content==server['png']
            body=await adoption_body(client,'actor')
            response=await client.post('/api/v1/asset-image-generation/jobs/'+row['id']+'/adopt',json=body)
            assert response.status_code==200 and response.json()['data']['project_id']=='image-project'
            assert await wallet(factory)==(1000,15,0)
            async with factory() as db:
                evidence=await db.scalar(select(ModelCallCharge))
                assert evidence.provider_request_id=='fixture-image-1' and evidence.dispatch_state=='accepted'
            assert 'fixture-image-1' not in str(response.json())
            assert (await image_bill(factory))[0]['status']=='applied'
            assert (await image_bill(factory))[0]['replayed']
            assert await wallet(factory)==(1000,0,9) and await wallet(factory,'other-project')==(1000,0,0)
            assert (await submit(client,'actor',slots['actor'])).json()['data']['id']==row['id'] and len(server['calls'])==1


@pytest.mark.asyncio
async def test_unpriced_insufficient_or_stale_image_policy_leaves_no_job_or_network(tmp_path,monkeypatch):
    """A positive sibling balance and a configured key cannot authorize an unpriced or underfunded image."""
    with model_server() as server:
        async with fixture_client(tmp_path,monkeypatch,server) as (client,factory,slots,runtimes):
            old=runtimes[0].price_registry;runtimes[0].price_registry=ModelPriceRegistry()
            assert (await submit(client,'actor',slots['actor'])).status_code==503
            runtimes[0].price_registry=old
            assert (await submit(client,'actor',slots['actor'],expected_price_fingerprint='f'*64)).status_code==409
            async with factory() as db:
                await db.execute(update(ExecutionBudget).where(ExecutionBudget.project_id=='image-project').values(limit_minor=14));await db.commit()
            assert (await submit(client,'actor',slots['actor'])).status_code==409
            async with factory() as db:
                assert await db.scalar(select(func.count()).select_from(AssetImageJob))==0
                assert await db.scalar(select(func.count()).select_from(ModelJobCharge))==0
            assert await wallet(factory)==(14,0,0) and await wallet(factory,'other-project')==(1000,0,0) and not server['calls']


@pytest.mark.asyncio
async def test_failed_image_response_keeps_fee_until_matching_explicit_no_charge_bill(tmp_path,monkeypatch):
    """Invalid returned image bytes do not imply free; the saved private response ID can later prove no charge."""
    with model_server() as server:
        server['mode']='badimage'
        async with fixture_client(tmp_path,monkeypatch,server) as (client,factory,slots,runtimes):
            response=await submit(client,'actor',slots['actor'])
            row=await terminal(client,response.json()['data']['id'])
            assert row['status']=='failed' and row['content_url'] is None and await wallet(factory)==(1000,15,0)
            assert (await image_bill(factory,0,status='not_charged'))[0]['status']=='applied'
            assert await wallet(factory)==(1000,0,0) and len(server['calls'])==1


@pytest.mark.asyncio
async def test_queued_cancel_releases_but_inflight_cancel_retains_possible_image_fee(tmp_path,monkeypatch):
    """Cancellation uses original batch state; it cannot refund a request already prepared for HTTP."""
    with model_server() as server:
        async with fixture_client(tmp_path,monkeypatch,server) as (client,factory,slots,runtimes):
            runtime=runtimes[0];await runtime.stop();runtime.running=True
            queued=(await submit(client,'actor',slots['actor'])).json()['data']
            assert await wallet(factory)==(1000,15,0)
            assert (await client.post('/api/v1/asset-image-generation/jobs/'+queued['id']+'/cancel')).status_code==200
            assert await wallet(factory)==(1000,0,0) and not server['calls']
            runtime.running=False;await runtime.start();server['mode']='block'
            live=(await submit(client,'actor',slots['actor'],key='live')).json()['data']
            for _ in range(100):
                if server['received'].is_set():break
                await asyncio.sleep(.03)
            assert server['received'].is_set()
            assert (await client.post('/api/v1/asset-image-generation/jobs/'+live['id']+'/cancel')).status_code==200
            assert await wallet(factory)==(1000,15,0)
            server['release'].set();await asyncio.sleep(.25)
            assert (await terminal(client,live['id']))['status']=='cancelled' and len(server['calls'])==1
