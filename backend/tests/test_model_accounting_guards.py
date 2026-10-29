"""Reject SQLite replace/unique-conflict shortcuts and competing duplicate bill applications."""
import asyncio

import pytest
from sqlalchemy import select,text
from sqlalchemy.exc import IntegrityError

from app.models.model_billing import ModelBillingLine
from app.services.worker.execution_budget import transaction
from app.services.worker.model_billing import reconcile_statement,resolve_duplicate
from app.services.worker.model_billing_schema import migrate_model_billing
from tests.test_model_billing import admitted,imported,invoice
from tests.test_model_charges import fixture,state

TABLES=['model_charge_batches','model_call_charges','model_billing_statements','model_billing_lines',
    'model_billing_receipts','model_billing_reviews','model_billing_resolutions']


@pytest.mark.asyncio
@pytest.mark.parametrize('table,new_primary_key',[(table,changed) for table in TABLES for changed in (False,True)
    if not (table=='model_billing_reviews' and changed)])
async def test_replace_cannot_rewrite_accounting_by_primary_or_natural_unique_key(tmp_path,table,new_primary_key):
    """All immutable identities reject REPLACE before SQLite can silently delete an existing row."""
    engine,factory=await fixture(tmp_path)
    await migrate_model_billing(engine)
    try:
        await admitted(factory)
        first=await imported(factory,invoice(80))
        receipt=(await reconcile_statement(factory,first))[0]['receipt_id']
        second=await imported(factory,invoice(80),key='overlap-for-guard')
        assert (await reconcile_statement(factory,second))[0]['status']=='conflict'
        async with factory() as db:
            line=await db.scalar(select(ModelBillingLine.id).where(ModelBillingLine.statement_id==second))
        await resolve_duplicate(factory,line,receipt,reason='Confirmed duplicate authored invoice fixture')
        before=await state(factory)
        async with factory() as db:
            columns=[row[1] for row in (await db.execute(text('PRAGMA table_info('+table+')'))).all()]
            fields=','.join('"'+column+'"' for column in columns)
            values=','.join("'replacement-primary-key'" if new_primary_key and column=='id' else '"'+column+'"' for column in columns)
            with pytest.raises(IntegrityError) as failure:
                await db.execute(text('INSERT OR REPLACE INTO '+table+'('+fields+') SELECT '+values+' FROM '+table+' LIMIT 1'))
            assert 'cannot be replaced' in str(failure.value)
            await db.rollback()
        assert await state(factory)==before
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_competing_bill_applications_create_one_receipt_and_spend_once(tmp_path):
    """Independent writers racing the same immutable line reuse its final receipt and full amount."""
    engine,factory=await fixture(tmp_path)
    await migrate_model_billing(engine)
    try:
        await admitted(factory)
        statement=await imported(factory,invoice(80))
        results=await asyncio.gather(reconcile_statement(factory,statement),reconcile_statement(factory,statement))
        assert all(result[0]['status']=='applied' for result in results)
        assert results[0][0]['receipt_id']==results[1][0]['receipt_id']
        assert sorted(result[0]['replayed'] for result in results)==[False,True]
        assert (await state(factory))[:2]==(0,80) and (await state(factory,'b'))[:2]==(0,0)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('statement',[
    "UPDATE execution_budgets SET spent_minor=1.5 WHERE project_id='a'",
    "UPDATE execution_budgets SET limit_minor=100.5 WHERE project_id='a'",
    "UPDATE execution_budget_entries SET quote_minor=25.5",
    "UPDATE execution_budget_entries SET actual_minor=1.5",
    "INSERT INTO provider_connection_versions(provider_id,revision) VALUES('fractional-clock',1.5)",
    "INSERT INTO model_billing_lines(id,statement_id,row_reference,provider_request_id,status,currency,amount_minor) SELECT 'fractional-line',statement_id,'fractional-row',provider_request_id,status,currency,1.5 FROM model_billing_lines LIMIT 1",
    "INSERT INTO model_billing_lines(id,statement_id,row_reference,provider_request_id,status,currency,amount_minor) SELECT 'overflow-line',statement_id,'overflow-row',provider_request_id,status,currency,9223372036854775808 FROM model_billing_lines LIMIT 1",
])
async def test_raw_sql_cannot_introduce_fractional_or_overflow_minor_units(tmp_path,statement):
    """SQLite numeric affinity alone cannot protect integer minor units; explicit guards cover future writes."""
    engine,factory=await fixture(tmp_path)
    await migrate_model_billing(engine)
    try:
        await admitted(factory)
        await imported(factory,invoice())
        before=await state(factory)
        async with factory() as db:
            with pytest.raises(IntegrityError):
                async with transaction(db):
                    await db.execute(text(statement))
        assert await state(factory)==before
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_reconciliation_revalidates_an_invalid_line_from_a_simulated_older_database(tmp_path):
    """Even evidence inserted before a current schema guard existed cannot post fractional money."""
    engine,factory=await fixture(tmp_path)
    await migrate_model_billing(engine)
    try:
        await admitted(factory)
        statement=await imported(factory,invoice())
        async with engine.begin() as connection:
            # This disposable fixture deliberately simulates a prior table without
            # the new value guard, retaining the invalid evidence for revalidation.
            await connection.execute(text('DROP TRIGGER model_billing_lines_integer_insert'))
            await connection.execute(text("INSERT INTO model_billing_lines(id,statement_id,row_reference,provider_request_id,status,currency,amount_minor) SELECT 'legacy-fraction',statement_id,'legacy-fraction',provider_request_id,status,currency,1.5 FROM model_billing_lines LIMIT 1"))
        await migrate_model_billing(engine)
        before=await state(factory)
        from fastapi import HTTPException
        from app.services.worker.model_billing import reconcile_line
        with pytest.raises(HTTPException) as failure:
            await reconcile_line(factory,'legacy-fraction')
        assert failure.value.status_code==409 and await state(factory)==before
    finally:
        await engine.dispose()
