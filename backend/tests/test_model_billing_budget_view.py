"""Project budget HTTP projections expose complete invoices without floating-point rounding or sibling leakage."""
import asyncio

from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text

from app.api import video_runtime_setup as api
from app.core.contracts.video_runtime_setup import ProjectBudgetWrite
from app.services.worker.model_billing import reconcile_statement
from app.services.worker.model_billing_schema import migrate_model_billing
from app.services.worker.project_budget import list_project_entries
from app.services.worker.workspace_video_runtime import read_project_budget,save_project_budget
from tests.test_model_billing import admitted,imported,invoice
from tests.test_model_charges import fixture,state


@pytest.mark.asyncio
async def test_posted_overage_labels_and_pagination_stay_in_the_charge_project(tmp_path):
    """A funded invoice shows the full amount and its explicit difference, without changing sibling balances."""
    engine,factory=await fixture(tmp_path)
    await migrate_model_billing(engine)
    try:
        await admitted(factory)
        statement=await imported(factory,invoice(80))
        await reconcile_statement(factory,statement)
        async with factory() as db:
            summary=await read_project_budget(db,'a')
            assert summary.coverage=='project_ledger' and summary.spent_minor==80 and summary.available_minor==20
            assert summary.bill_receipt_total_minor=='80' and summary.bill_receipt_count==1
            assert summary.price_violation_count==1 and summary.pending_bill_reviews==0
            assert summary.unfunded_bill_minor=='0' and summary.available_after_bills_minor=='20'
            first=await list_project_entries(db,'a',after=None,limit=1)
            second=await list_project_entries(db,'a',after=first.next_after,limit=1)
            assert len(first.items)==len(second.items)==1 and second.next_after is None
            rows=first.items+second.items
            assert {row.source for row in rows}=={'model_call','bill_adjustment'}
            assert sum(int(row.actual_minor_exact) for row in rows)==80
            assert next(row for row in rows if row.source=='model_call').call_kind=='agent_text'
            other=await read_project_budget(db,'b')
            assert other.bill_receipt_count==0 and other.available_minor==100 and other.price_violation_count==0
            assert not (await list_project_entries(db,'b',after=first.next_after,limit=1)).items
    finally:
        await engine.dispose()


def test_unfunded_http_amounts_remain_exact_beyond_javascript_and_sqlite_sum_limits(tmp_path):
    """Three explicit huge synthetic bills remain decimal strings and do not disappear behind held quotes."""
    engine,factory=asyncio.run(fixture(tmp_path))
    maximum=(1<<62)-1
    async def prepare():
        """Record three distinct accepted fixture requests and explicit unaffordable invoice facts."""
        await migrate_model_billing(engine)
        for index in range(3):
            request='huge-request-'+str(index)
            await admitted(factory,scope='agent:huge-'+str(index),request=request)
        for index in range(3):
            request='huge-request-'+str(index)
            statement=await imported(factory,invoice(maximum,request=request),key='huge-'+str(index))
            assert (await reconcile_statement(factory,statement))[0]['status']=='disputed'
    asyncio.run(prepare())
    app=FastAPI()
    app.include_router(api.router,prefix='/api/v1')
    app.dependency_overrides[api.get_video_setup_factory]=lambda:factory
    try:
        with TestClient(app) as client:
            endpoint='/api/v1/video-runtime/projects/a/budget'
            result=client.get(endpoint)
            assert result.status_code==200
            data=result.json()['data']
            assert data['reserved_minor']==75 and data['spent_minor']==0 and data['available_minor']==25
            assert data['observed_pending_bill_minor']==str(maximum*3)
            assert data['unfunded_bill_minor']==str(maximum*3-75)
            assert data['available_after_bills_minor']==str(100-maximum*3)
            assert data['pending_bill_reviews']==3 and data['price_violation_count']==3
            denied=client.put(endpoint,json={'expected_revision':data['revision'],'limit_minor':100})
            assert denied.status_code==409
            sibling=client.get('/api/v1/video-runtime/projects/b/budget').json()['data']
            assert sibling['available_after_bills_minor']=='100' and sibling['observed_pending_bill_minor']=='0'
            assert sibling['pending_bill_reviews']==0
    finally:
        asyncio.run(engine.dispose())


@pytest.mark.asyncio
async def test_overlapping_pending_statements_require_one_call_total_then_no_repeated_liability(tmp_path):
    """Two invoice copies retain their own review history while authorization covers the request once."""
    engine,factory=await fixture(tmp_path)
    await migrate_model_billing(engine)
    try:
        await admitted(factory)
        statements=[await imported(factory,invoice(150),key='overlap-'+str(index)) for index in range(2)]
        for statement in statements:
            assert (await reconcile_statement(factory,statement))[0]['status']=='disputed'
        async with factory() as db:
            before=await read_project_budget(db,'a')
            assert before.observed_pending_bill_minor=='150' and before.unfunded_bill_minor=='125'
            assert before.available_after_bills_minor=='-50' and before.pending_bill_reviews==2
        await save_project_budget(factory,'a',ProjectBudgetWrite(expected_revision=before.revision,limit_minor=150))
        assert (await reconcile_statement(factory,statements[0]))[0]['status']=='applied'
        assert (await reconcile_statement(factory,statements[1]))[0]['status']=='conflict'
        async with factory() as db:
            after=await read_project_budget(db,'a')
            assert after.spent_minor==150 and after.reserved_minor==0
            assert after.unfunded_bill_minor=='0' and after.available_after_bills_minor=='0'
            assert after.pending_bill_reviews==1  # The remaining disputed/conflict events name one unreviewed line.
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_higher_conflicting_total_preserves_only_the_gap_above_posted_full_amount(tmp_path):
    """A later different invoice is not silently ignored, and previously posted money is not counted twice."""
    engine,factory=await fixture(tmp_path)
    await migrate_model_billing(engine)
    try:
        await admitted(factory)
        first=await imported(factory,invoice(12))
        await reconcile_statement(factory,first)
        other=await imported(factory,invoice(80),key='different-observed-total')
        assert (await reconcile_statement(factory,other))[0]['status']=='conflict'
        async with factory() as db:
            result=await read_project_budget(db,'a')
            assert result.spent_minor==12 and result.reserved_minor==0 and result.available_minor==88
            assert result.observed_pending_bill_minor=='80' and result.unfunded_bill_minor=='68'
            assert result.available_after_bills_minor=='20' and result.pending_bill_reviews==1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_budget_read_during_reconciliation_keeps_one_actual_sqlite_snapshot(tmp_path):
    """A bill settling between projection queries cannot mix old liabilities with new posted balances."""
    engine,factory=await fixture(tmp_path)
    await migrate_model_billing(engine)
    async with engine.connect() as connection:
        await connection.execute(text('PRAGMA journal_mode=WAL'))
    try:
        await admitted(factory)
        statement=await imported(factory,invoice(150))
        await reconcile_statement(factory,statement)
        async with factory() as db:
            revision=(await read_project_budget(db,'a')).revision
        await save_project_budget(factory,'a',ProjectBudgetWrite(expected_revision=revision,limit_minor=200))
        reached=asyncio.Event();finished=asyncio.Event()
        async with factory() as reader:
            original=reader.execute
            async def pause_between_financial_queries(query,*args,**kwargs):
                """Let a genuine independent writer commit after the reader has inspected held bill liability."""
                sql=str(query)
                if not reached.is_set() and 'sum(model_billing_receipts.actual_minor)' in sql:
                    reached.set()
                    await asyncio.wait_for(finished.wait(),10)
                return await original(query,*args,**kwargs)
            reader.execute=pause_between_financial_queries
            async def writer():
                """Settle the now-funded bill without reusing the read connection or its snapshot."""
                await asyncio.wait_for(reached.wait(),10)
                try:
                    assert (await reconcile_statement(factory,statement))[0]['status']=='applied'
                finally:
                    finished.set()
            old,unused=await asyncio.gather(read_project_budget(reader,'a'),writer())
            assert reached.is_set() and old.reserved_minor==25 and old.spent_minor==0
            assert old.pending_bill_reviews==1 and old.unfunded_bill_minor=='125' and old.available_after_bills_minor=='50'
        async with factory() as db:
            current=await read_project_budget(db,'a')
            assert current.reserved_minor==0 and current.spent_minor==150 and current.pending_bill_reviews==0
            assert current.unfunded_bill_minor=='0' and current.available_after_bills_minor=='50'
    finally:
        await engine.dispose()
