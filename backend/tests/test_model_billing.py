"""Explicit synthetic invoices test exact matching, financial truth and non-duplicating project settlement."""
import json

from fastapi import HTTPException
import pytest
from sqlalchemy import func,select,text
from sqlalchemy.exc import IntegrityError

from app.core.contracts.model_accounting import BillingStatementContext
from app.core.contracts.video_runtime_setup import ProjectBudgetWrite
from app.models.execution_budget import ExecutionBudgetEntry
from app.models.model_billing import ModelBillingLine,ModelBillingReceipt,ModelBillingReview,ModelBillingStatement,ModelBillingResolution
from app.models.model_charge import ModelCallCharge
from app.services.worker.execution_budget import transaction
from app.services.worker.model_billing import import_statement,parse_normalized_csv,reconcile_statement,resolve_duplicate,billing_summary
from app.services.worker.model_billing_schema import migrate_model_billing
from app.services.worker.model_charges import prepare_call,record_accepted_call,reserve_batch
from app.services.worker.workspace_video_runtime import save_project_budget
from tests.test_model_charges import ACTOR,calls,fixture,state

HEADER='row_reference,provider_request_id,status,currency,amount_minor\n'


def context(**extra):
    """Name one explicit synthetic account/version, never a real supplier or inferred model identity."""
    return BillingStatementContext(provider_id=extra.get('provider_id','p'),provider_connection_version=extra.get('revision',1),
        account_version=extra.get('account_version','synthetic-account-v1'),statement_key=extra.get('key','fixture-invoice-1'))


def invoice(amount=12,*,status='charged',request='fixture-request-1',row='row-1'):
    """Author normalized fixture rows; these minor amounts are not supplier pricing or actual spend."""
    return (HEADER+f'{row},{request},{status},CNY,{amount}\n').encode()


async def admitted(factory,*,project='a',scope='agent:invoice',request='fixture-request-1',accept=True):
    """Prepare a local ledger unit and optionally store an explicit trusted fixture response identity."""
    batch=await reserve_batch(factory,project_id=project,scope_key=scope,submitted_by=ACTOR,calls=calls(count=1))
    async with factory() as db:
        identity=await db.scalar(select(ModelCallCharge.id).where(ModelCallCharge.batch_id==batch))
    async with factory() as db:
        async with transaction(db):
            await prepare_call(db,project_id=project,charge_id=identity,binding_hash='a'*64)
            if accept:
                await record_accepted_call(db,project_id=project,charge_id=identity,provider_request_id=request)
    return identity


async def imported(factory,content,**extra):
    """Bind the normalized line to an explicit original authored evidence hash."""
    return await import_statement(factory,context=context(**extra),normalized_csv=content,
        original_content=b'Synthetic authored invoice evidence. No real bill or external call.')


@pytest.mark.asyncio
async def test_exact_actual_bill_settles_once_with_receipt_and_source_hashes(tmp_path):
    """A genuine explicit fixture amount settles its original hold; repeated import/application spend once."""
    engine,factory=await fixture(tmp_path)
    await migrate_model_billing(engine)
    try:
        charge=await admitted(factory)
        statement=await imported(factory,invoice())
        assert await imported(factory,invoice())==statement
        result=await reconcile_statement(factory,statement)
        assert result[0]['status']=='applied' and not result[0]['replayed']
        assert (await state(factory))[:2]==(0,12)
        repeated=await reconcile_statement(factory,statement)
        assert repeated[0]['replayed'] and repeated[0]['receipt_id']==result[0]['receipt_id']
        assert (await state(factory,'b'))[:2]==(0,0)
        async with factory() as db:
            receipt=await db.scalar(select(ModelBillingReceipt))
            source=await db.get(ModelBillingStatement,statement)
            assert receipt.charge_id==charge and receipt.actual_minor==12 and receipt.adjustment_entry_id is None
            assert len(source.original_sha256)==len(source.normalized_sha256)==64
        with pytest.raises(HTTPException) as failure:
            await imported(factory,invoice(13))
        assert failure.value.status_code==409 and (await state(factory))[:2]==(0,12)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('mismatch',['provider','revision','account','unknown_request'])
async def test_account_or_request_mismatch_retains_unknown_full_hold(tmp_path,mismatch):
    """A plausible invoice with the wrong historical account cannot settle another connection's hold."""
    engine,factory=await fixture(tmp_path)
    await migrate_model_billing(engine)
    try:
        await admitted(factory)
        options={'provider_id':'other'} if mismatch=='provider' else {'revision':2} if mismatch=='revision' else {'account_version':'other'} if mismatch=='account' else {}
        statement=await imported(factory,invoice(request='missing' if mismatch=='unknown_request' else 'fixture-request-1'),**options)
        result=await reconcile_statement(factory,statement)
        assert result[0]['status']=='unmatched'
        assert (await state(factory))[:2]==(25,0)
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(ModelBillingReceipt))==0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_explicit_no_charge_releases_but_missing_evidence_does_not(tmp_path):
    """Only an explicit account/request-matched zero-charge line can release possible acceptance."""
    engine,factory=await fixture(tmp_path)
    await migrate_model_billing(engine)
    try:
        await admitted(factory)
        statement=await imported(factory,invoice(0,status='not_charged'))
        assert (await reconcile_statement(factory,statement))[0]['status']=='applied'
        result=await state(factory)
        assert result[:2]==(0,0) and result[4][0][1]=='released'
        await admitted(factory,scope='agent:unknown',accept=False)
        unknown=await imported(factory,invoice(0,status='not_charged',request='not-stored-response'),key='no-proof')
        assert (await reconcile_statement(factory,unknown))[0]['status']=='unmatched'
        assert (await state(factory))[:2]==(25,0)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_above_quote_bill_uses_existing_headroom_and_explicit_adjustment_without_raising_limit(tmp_path):
    """The complete observed amount is recorded, with a separate adjustment and invalidated old quote."""
    engine,factory=await fixture(tmp_path)
    await migrate_model_billing(engine)
    try:
        charge=await admitted(factory)
        statement=await imported(factory,invoice(80))
        assert (await reconcile_statement(factory,statement))[0]['status']=='applied'
        assert (await state(factory))[:2]==(0,80)
        async with factory() as db:
            receipt=await db.scalar(select(ModelBillingReceipt).where(ModelBillingReceipt.charge_id==charge))
            assert receipt.actual_minor==80 and receipt.quote_minor==25 and receipt.adjustment_entry_id
            original=await db.get(ExecutionBudgetEntry,charge)
            adjustment=await db.get(ExecutionBudgetEntry,receipt.adjustment_entry_id)
            assert original.actual_minor==25 and adjustment.actual_minor==55
            assert await db.scalar(select(func.sum(ExecutionBudgetEntry.actual_minor)).where(ExecutionBudgetEntry.project_id=='a'))==80
        # Shared invalid pricing blocks new use, while neither project's limit or balance is borrowed.
        for quoted in (calls(count=1),calls(count=1,amount=79)):
            with pytest.raises(HTTPException) as failure:
                await reserve_batch(factory,project_id='b',scope_key='agent:bad-price',submitted_by=ACTOR,calls=quoted)
            assert failure.value.status_code==409
        assert (await state(factory,'b'))[:2]==(0,0)
        await reserve_batch(factory,project_id='b',scope_key='agent:new-price',submitted_by=ACTOR,calls=calls(count=1,amount=80))
        assert (await state(factory,'b'))[:2]==(80,0)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unfunded_bill_is_preserved_and_blocks_project_until_explicit_ceiling_change(tmp_path):
    """No truncation or silent budget increase occurs when a full observed invoice exceeds authorization."""
    engine,factory=await fixture(tmp_path)
    await migrate_model_billing(engine)
    try:
        await admitted(factory)
        statement=await imported(factory,invoice(150))
        assert (await reconcile_statement(factory,statement))[0]['status']=='disputed'
        assert (await state(factory))[:2]==(25,0)
        async with factory() as db:
            line=await db.scalar(select(ModelBillingLine))
            assert line.amount_minor==150
        with pytest.raises(HTTPException):
            await reserve_batch(factory,project_id='a',scope_key='agent:unfunded',submitted_by=ACTOR,calls=calls(count=1,amount=80))
        before=await state(factory)
        await save_project_budget(factory,'a',ProjectBudgetWrite(expected_revision=before[2],limit_minor=200))
        assert (await reconcile_statement(factory,statement))[0]['status']=='applied'
        assert (await state(factory))[:2]==(0,150)
        assert (await state(factory,'b'))[:2]==(0,0)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ambiguous_request_and_second_invoice_cannot_double_settle(tmp_path):
    """Repeated provider IDs are retained as ambiguity or conflict; neither guesses a second expense."""
    engine,factory=await fixture(tmp_path)
    await migrate_model_billing(engine)
    try:
        await admitted(factory)
        first=await imported(factory,invoice())
        assert (await reconcile_statement(factory,first))[0]['status']=='applied'
        second=await imported(factory,invoice(),key='different-invoice')
        assert (await reconcile_statement(factory,second))[0]['status']=='conflict'
        assert (await state(factory))[:2]==(0,12)
        # Use another project/account request so the prior conflict does not admit further project-A work.
        await admitted(factory,project='b',scope='agent:b-one',request='reused-provider-id')
        await admitted(factory,project='b',scope='agent:b-two',request='reused-provider-id')
        ambiguous=await imported(factory,invoice(request='reused-provider-id'),key='ambiguous')
        assert (await reconcile_statement(factory,ambiguous))[0]['status']=='ambiguous'
        assert (await state(factory,'b'))[:2]==(50,0)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_source_line_review_and_settlement_history_cannot_be_rewritten(tmp_path):
    """Raw writes cannot erase source evidence, actual amounts or the receipt preventing double spending."""
    engine,factory=await fixture(tmp_path)
    await migrate_model_billing(engine)
    try:
        await admitted(factory)
        statement=await imported(factory,invoice(80))
        await reconcile_statement(factory,statement)
        for table in ('model_billing_statements','model_billing_lines','model_billing_receipts','model_billing_reviews'):
            async with factory() as db:
                with pytest.raises(IntegrityError):
                    async with transaction(db):
                        await db.execute(text('DELETE FROM '+table))
        assert (await state(factory))[:2]==(0,80)
    finally:
        await engine.dispose()


@pytest.mark.parametrize('content',[
    b'',(HEADER+'x,r,charged,CNY,1.0\n').encode(),(HEADER+'x,r,charged,CNY,-1\n').encode(),
    (HEADER+'x,r,charged,CNY,true\n').encode(),(HEADER+'x,r,not_charged,CNY,1\n').encode(),
    (HEADER+'x,r,charged,USD,1\n').encode(),(HEADER+'x,=formula,charged,CNY,1\n').encode(),
    (HEADER+'x,r,charged,CNY,1\nx,r2,charged,CNY,2\n').encode(),
    (HEADER+'x,r,charged,CNY,1\ny,r,charged,CNY,2\n').encode(),
    b'api_key,amount_minor\nSYNTHETIC_PRIVATE_INPUT,1\n',
])
def test_untrusted_or_ambiguous_csv_never_echoes_input_or_infers_money(content):
    """Invalid source columns, formulas, mixed currencies and implicit no-charge conclusions reject wholly."""
    with pytest.raises(HTTPException) as failure:
        parse_normalized_csv(content)
    assert failure.value.status_code==422 and 'SYNTHETIC_PRIVATE_INPUT' not in str(failure.value.detail)


@pytest.mark.asyncio
async def test_duplicate_evidence_needs_explicit_immutable_resolution_without_another_expense(tmp_path):
    """The operator can close an exact overlapping statement while preserving its conflict and original receipt."""
    engine,factory=await fixture(tmp_path)
    await migrate_model_billing(engine)
    try:
        await admitted(factory)
        first=await imported(factory,invoice())
        receipt=(await reconcile_statement(factory,first))[0]['receipt_id']
        second=await imported(factory,invoice(),key='overlapping-statement')
        assert (await reconcile_statement(factory,second))[0]['status']=='conflict'
        async with factory() as db:
            line=await db.scalar(select(ModelBillingLine.id).where(ModelBillingLine.statement_id==second))
            assert (await billing_summary(db,'a'))['pending_bill_reviews']==1
        with pytest.raises(HTTPException):
            await reserve_batch(factory,project_id='a',scope_key='agent:conflict',submitted_by=ACTOR,calls=calls(count=1))
        before=await state(factory)
        resolution=await resolve_duplicate(factory,line,receipt,reason='Verified exact overlapping synthetic invoice')
        assert await resolve_duplicate(factory,line,receipt,reason='Verified exact overlapping synthetic invoice')==resolution
        assert await state(factory)==before
        assert (await reconcile_statement(factory,second))[0]['status']=='duplicate_resolved'
        async with factory() as db:
            summary=await billing_summary(db,'a')
            assert summary['pending_bill_reviews']==0 and summary['bill_receipt_count']==1 and summary['bill_receipt_total_minor']==12
            assert await db.scalar(select(func.count()).select_from(ModelBillingReview).where(ModelBillingReview.line_id==line))==1
        with pytest.raises(HTTPException) as changed:
            await resolve_duplicate(factory,line,receipt,reason='Changed historical reason should not replace the first one')
        assert changed.value.status_code==409
        for operation in ('DELETE FROM model_billing_resolutions',"UPDATE model_billing_resolutions SET reason='Changed'"):
            async with factory() as db:
                with pytest.raises(IntegrityError):
                    async with transaction(db):
                        await db.execute(text(operation))
        await reserve_batch(factory,project_id='a',scope_key='agent:after-resolution',submitted_by=ACTOR,calls=calls(count=1))
        assert (await state(factory))[:2]==(25,12)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('mismatch',['amount','status','account','receipt'])
async def test_different_bill_facts_cannot_be_labeled_as_a_duplicate(tmp_path,mismatch):
    """A manual duplicate decision cannot hide a correction, no-charge discrepancy or another account's expense."""
    engine,factory=await fixture(tmp_path)
    await migrate_model_billing(engine)
    try:
        await admitted(factory)
        first=await imported(factory,invoice(0))
        receipt=(await reconcile_statement(factory,first))[0]['receipt_id']
        content=invoice(1) if mismatch=='amount' else invoice(0,status='not_charged') if mismatch=='status' else invoice(0)
        second=await imported(factory,content,key='mismatched-statement',**({'account_version':'other-account'} if mismatch=='account' else {}))
        await reconcile_statement(factory,second)
        if mismatch=='receipt':
            await admitted(factory,project='b',scope='agent:other-receipt',request='another-request')
            other=await imported(factory,invoice(request='another-request'),key='other-project-statement')
            receipt=(await reconcile_statement(factory,other))[0]['receipt_id']
        before=await state(factory)
        async with factory() as db:
            line=await db.scalar(select(ModelBillingLine.id).where(ModelBillingLine.statement_id==second))
        with pytest.raises(HTTPException) as failure:
            await resolve_duplicate(factory,line,receipt,reason='This invalid duplicate decision must be refused')
        assert failure.value.status_code==409 and await state(factory)==before
        # The database independently refuses a bypass of the business-service check.
        async with factory() as db:
            with pytest.raises(IntegrityError):
                async with transaction(db):
                    db.add(ModelBillingResolution(id='invalid',line_id=line,reference_receipt_id=receipt,
                        action='duplicate_evidence',reason='Must not hide a mismatch',recorded_by=ACTOR,created_at=1))
                    await db.flush()
    finally:
        await engine.dispose()
