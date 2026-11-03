"""Authored invoices verify actual SQLite corrections, ambiguous choices, replay and project conservation."""
import asyncio

from fastapi import HTTPException
import pytest
from sqlalchemy import func,select,text
from sqlalchemy.exc import IntegrityError

from app.core.contracts.video_runtime_setup import ProjectBudgetWrite
from app.models.execution_budget import ExecutionBudget,ExecutionBudgetEntry
from app.models.model_billing import ModelBillingLine,ModelBillingReceipt,ModelBillingReview
from app.models.model_billing_adjustment import ModelBillingAssignment,ModelBillingCorrection
from app.services.worker.model_billing import billing_summary,reconcile_line,reconcile_statement,resolve_duplicate
from app.services.worker.model_billing_adjustments import assign_ambiguous_line,correct_billing_line
from app.services.worker.model_billing_schema import migrate_model_billing
from app.services.worker.workspace_video_runtime import read_project_budget,save_project_budget
from tests.test_model_billing import admitted,imported,invoice
from tests.test_model_charges import fixture,state


async def settled(factory,*,amount=12,project='a',scope='initial',request='fixture-request-1'):
    """Post one explicitly authored statement and return its original immutable receipt."""
    charge=await admitted(factory,project=project,scope=scope,request=request)
    statement=await imported(factory,invoice(amount,request=request),key='initial-'+scope)
    result=(await reconcile_statement(factory,statement))[0]
    assert result['status']=='applied'
    return charge,result['receipt_id']


async def correction_line(factory,amount,*,key='correction-one',**context):
    """Import an explicit replacement TOTAL, never an incremental fee or a real supplier refund."""
    statement=await imported(factory,invoice(amount,status='not_charged' if amount==0 else 'charged'),key=key,**context)
    async with factory() as db:
        return await db.scalar(select(ModelBillingLine.id).where(ModelBillingLine.statement_id==statement))


async def correct(factory,line,receipt,revision=0,reason='Authored operator correction evidence'):
    """Supply the exact observed correction revision; no helper refreshes or guesses it."""
    return await correct_billing_line(factory,line_id=line,reference_receipt_id=receipt,expected_revision=revision,reason=reason)


@pytest.mark.asyncio
async def test_credit_debit_full_credit_original_replay_and_public_project_totals(tmp_path):
    """A three-step chain changes only project A net spend; original gross/entries and old retry results survive."""
    engine,factory=await fixture(tmp_path)
    try:
        await migrate_model_billing(engine)
        charge,receipt=await settled(factory)
        async with factory() as db:
            original=(await db.execute(select(ExecutionBudgetEntry.state,ExecutionBudgetEntry.actual_minor).where(ExecutionBudgetEntry.job_id==charge))).one()
        first=await correction_line(factory,5)
        result=await correct(factory,first,receipt)
        assert result['status']=='correction_applied' and result['revision']==1 and not result['replayed']
        assert (await state(factory))[:2]==(0,5)
        second=await correction_line(factory,9,key='correction-two')
        assert (await correct(factory,second,receipt,1))['revision']==2
        third=await correction_line(factory,0,key='correction-three')
        assert (await correct(factory,third,receipt,2))['revision']==3
        assert (await correct(factory,first,receipt))=={**result,'replayed':True}
        assert (await reconcile_line(factory,first))['status']=='correction_applied'
        assert (await state(factory))[:2]==(0,0) and (await state(factory,'b'))[:2]==(0,0)
        async with factory() as db:
            assert (await db.get(ModelBillingReceipt,receipt)).actual_minor==12
            assert (await db.execute(select(ExecutionBudgetEntry.state,ExecutionBudgetEntry.actual_minor).where(ExecutionBudgetEntry.job_id==charge))).one()==original
            summary=await billing_summary(db,'a')
            assert {key:summary[key] for key in ('bill_receipt_count','bill_receipt_total_minor','bill_net_total_minor','bill_correction_count','bill_correction_debit_minor','bill_correction_credit_minor')}=={
                'bill_receipt_count':1,'bill_receipt_total_minor':12,'bill_net_total_minor':0,'bill_correction_count':3,'bill_correction_debit_minor':4,'bill_correction_credit_minor':16}
        async with factory() as db:
            public=await read_project_budget(db,'a')
            assert public.bill_net_total_minor=='0' and public.bill_correction_debit_minor=='4' and public.bill_correction_credit_minor=='16'
            assert public.spent_minor==0 and public.limit_minor==100 and not public.provider_bill_verified
        with pytest.raises(HTTPException) as failure:
            await correct(factory,first,receipt,reason='Changed decision must conflict')
        assert failure.value.status_code==409
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unfunded_debit_stays_disputed_until_explicit_owner_budget_change(tmp_path):
    """A larger invoice is retained in full, blocks new calls and never raises the ceiling or truncates the total."""
    engine,factory=await fixture(tmp_path)
    try:
        await migrate_model_billing(engine)
        _,receipt=await settled(factory)
        line=await correction_line(factory,150)
        assert (await correct(factory,line,receipt))['status']=='disputed'
        assert (await state(factory))[:2]==(0,12)
        async with factory() as db:
            summary=await billing_summary(db,'a')
            assert summary['pending_bill_reviews']==1 and summary['observed_pending_bill_minor']==150 and summary['unfunded_bill_minor']==138
            budget=await db.get(ExecutionBudget,'a'); revision=budget.revision
            assert budget.limit_minor==100
        await save_project_budget(factory,'a',ProjectBudgetWrite(expected_revision=revision,limit_minor=200))
        assert (await correct(factory,line,receipt))['status']=='correction_applied'
        async with factory() as db:
            summary=await billing_summary(db,'a')
            assert summary['pending_bill_reviews']==0 and summary['unfunded_bill_minor']==0 and summary['price_violation_count']==1
        assert (await state(factory))[:2]==(0,150)
        assert (await state(factory,'b'))[:2]==(0,0)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_two_writers_cannot_correct_the_same_observed_revision_twice(tmp_path):
    """Actual separate SQLite connections serialize competing totals; only an explicit refreshed decision can follow."""
    engine,factory=await fixture(tmp_path)
    try:
        await migrate_model_billing(engine)
        _,receipt=await settled(factory)
        one=await correction_line(factory,5,key='one')
        two=await correction_line(factory,9,key='two')
        results=await asyncio.gather(correct(factory,one,receipt),correct(factory,two,receipt),return_exceptions=True)
        good=[item for item in results if isinstance(item,dict)]
        bad=[item for item in results if isinstance(item,HTTPException)]
        assert len(good)==len(bad)==1 and bad[0].status_code==409
        async with factory() as db:
            rows=(await db.scalars(select(ModelBillingCorrection))).all()
            assert len(rows)==1 and rows[0].revision==1
            assert (await db.get(ExecutionBudget,'a')).spent_minor==rows[0].new_total_minor
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('mismatch',['provider','revision','account','request','receipt','same_source'])
async def test_changed_account_request_receipt_or_original_source_cannot_correct(tmp_path,mismatch):
    """An explicit receipt is authority only for the same original account/request and a separate invoice."""
    engine,factory=await fixture(tmp_path)
    try:
        await migrate_model_billing(engine)
        _,receipt=await settled(factory)
        options={'provider_id':'foreign'} if mismatch=='provider' else {'revision':2} if mismatch=='revision' else {'account_version':'foreign-account'} if mismatch=='account' else {}
        if mismatch=='request':
            statement=await imported(factory,invoice(5,request='other-request'),key='changed-request')
            async with factory() as db:
                line=await db.scalar(select(ModelBillingLine.id).where(ModelBillingLine.statement_id==statement))
        elif mismatch=='same_source':
            async with factory() as db:
                line=(await db.get(ModelBillingReceipt,receipt)).line_id
        else:
            line=await correction_line(factory,5,**options)
        if mismatch=='receipt':
            _,receipt=await settled(factory,project='b',scope='second',request='other-request')
        with pytest.raises(HTTPException) as failure:
            await correct(factory,line,receipt)
        assert failure.value.status_code==409
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(ModelBillingCorrection))==0
            assert (await db.get(ExecutionBudget,'a')).spent_minor==12
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ambiguous_choice_settles_only_selected_call_and_cannot_be_reassigned(tmp_path):
    """Two projects may have the same provider request; an explicit choice leaves the other unknown hold intact."""
    engine,factory=await fixture(tmp_path)
    try:
        await migrate_model_billing(engine)
        one=await admitted(factory,project='a',scope='a')
        two=await admitted(factory,project='b',scope='b')
        statement=await imported(factory,invoice())
        assert (await reconcile_statement(factory,statement))[0]['status']=='ambiguous'
        async with factory() as db:
            line=await db.scalar(select(ModelBillingLine.id).where(ModelBillingLine.statement_id==statement))
        assert (await state(factory))[:2]==(25,0) and (await state(factory,'b'))[:2]==(25,0)
        reason='Explicit authored matching-call evidence'
        selected=await assign_ambiguous_line(factory,line_id=line,charge_id=two,reason=reason)
        assert await assign_ambiguous_line(factory,line_id=line,charge_id=two,reason=reason)==selected
        assert (await reconcile_line(factory,line))['status']=='applied'
        assert (await reconcile_line(factory,line))['replayed']
        assert (await state(factory))[:2]==(25,0) and (await state(factory,'b'))[:2]==(0,12)
        with pytest.raises(HTTPException) as failure:
            await assign_ambiguous_line(factory,line_id=line,charge_id=one,reason=reason)
        assert failure.value.status_code==409
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(ModelBillingAssignment))==1
            assert await db.scalar(select(func.count()).select_from(ModelBillingReview).where(ModelBillingReview.status=='ambiguous'))==1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation',['update','delete','physical_replace','chain_fork','fractional','reclassify'])
async def test_actual_sqlite_correction_history_guards(tmp_path,operation):
    """Real SQL cannot edit/delete/physically replace a correction, fork its chain or reclassify an applied line."""
    engine,factory=await fixture(tmp_path)
    try:
        await migrate_model_billing(engine)
        _,receipt=await settled(factory)
        line=await correction_line(factory,5)
        result=await correct(factory,line,receipt)
        async with engine.connect() as db:
            columns=(await db.execute(text('PRAGMA table_info(model_billing_corrections)'))).all()
            fields=[row[1] for row in columns]
        if operation=='reclassify':
            with pytest.raises(HTTPException):
                await resolve_duplicate(factory,line,receipt,reason='Cannot reclassify a posted correction')
        else:
            if operation=='update':
                query="UPDATE model_billing_corrections SET reason='Changed history'"
            elif operation=='delete':
                query='DELETE FROM model_billing_corrections'
            else:
                changed={'id':"'new-correction-id'",'line_id':"'different-line'"}
                if operation=='chain_fork': changed['revision']='revision+1'
                if operation=='fractional': changed['new_total_minor']='5.5'
                names=','.join(fields);values=','.join(changed.get(name,name) for name in fields)
                prefix='rowid,' if operation=='physical_replace' else ''
                query=f'INSERT OR REPLACE INTO model_billing_corrections({prefix}{names}) SELECT {prefix}{values} FROM model_billing_corrections'
            with pytest.raises(IntegrityError):
                async with engine.begin() as db:
                    await db.execute(text(query))
        async with factory() as db:
            assert (await db.get(ModelBillingCorrection,result['correction_id'])).new_total_minor==5
            assert await db.scalar(select(func.count()).select_from(ModelBillingCorrection))==1
        assert (await state(factory))[:2]==(0,5)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_actual_old_review_physical_replace_counterexample_now_rejected(tmp_path):
    """The observed old rowid overwrite cannot erase a price-violation review using a different logical ID."""
    engine,factory=await fixture(tmp_path)
    try:
        await migrate_model_billing(engine)
        await settled(factory,amount=30)
        async with factory() as db:
            original=await db.scalar(select(ModelBillingReview.id).where(ModelBillingReview.status=='price_violation'))
        with pytest.raises(IntegrityError):
            async with engine.begin() as db:
                await db.execute(text("INSERT OR REPLACE INTO model_billing_reviews(rowid,id,line_id,charge_id,project_id,price_fingerprint,status,code,created_at) SELECT rowid,'authored-physical-counterexample',line_id,NULL,NULL,NULL,'unmatched','request_account_match_required',created_at FROM model_billing_reviews WHERE id=:old"),{'old':original})
        async with factory() as db:
            assert (await db.get(ModelBillingReview,original)).status=='price_violation'
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_pending_bill_gap_uses_corrected_net_not_original_gross(tmp_path):
    """A credited old bill cannot conceal a later larger unresolved liability behind the old gross amount."""
    engine,factory=await fixture(tmp_path)
    try:
        await migrate_model_billing(engine)
        _,receipt=await settled(factory)
        line=await correction_line(factory,5)
        await correct(factory,line,receipt)
        statement=await imported(factory,invoice(80),key='later-unresolved-bill')
        assert (await reconcile_statement(factory,statement))[0]['status']=='conflict'
        async with factory() as db:
            public=await read_project_budget(db,'a')
            assert public.spent_minor==5 and public.bill_receipt_total_minor=='12' and public.bill_net_total_minor=='5'
            assert public.unfunded_bill_minor=='75' and public.available_after_bills_minor=='20' and public.pending_bill_reviews==1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_actual_assignment_rowid_cannot_replace_an_old_choice_with_another_valid_choice(tmp_path):
    """Even another valid matching line/call cannot erase a prior assignment through physical SQLite REPLACE."""
    engine,factory=await fixture(tmp_path)
    try:
        await migrate_model_billing(engine)
        one=await admitted(factory,project='a',scope='one')
        two=await admitted(factory,project='b',scope='two')
        lines=[]
        for key in ('first-authored-bill','second-authored-bill'):
            statement=await imported(factory,invoice(),key=key)
            assert (await reconcile_statement(factory,statement))[0]['status']=='ambiguous'
            async with factory() as db:
                lines.append(await db.scalar(select(ModelBillingLine.id).where(ModelBillingLine.statement_id==statement)))
        original=await assign_ambiguous_line(factory,line_id=lines[0],charge_id=one,reason='Authored original matching choice')
        with pytest.raises(IntegrityError) as failure:
            async with engine.begin() as db:
                await db.execute(text("INSERT OR REPLACE INTO model_billing_assignments(rowid,id,line_id,charge_id,project_id,reason,recorded_by,created_at) "
                    "SELECT rowid,'different-valid-choice',:line,:charge,'b',reason,recorded_by,created_at FROM model_billing_assignments WHERE id=:original"),
                    {'line':lines[1],'charge':two,'original':original})
        assert 'physical identity' in str(failure.value.orig)
        async with factory() as db:
            assert (await db.get(ModelBillingAssignment,original)).charge_id==one
            assert await db.scalar(select(func.count()).select_from(ModelBillingAssignment))==1
        assert (await state(factory))[:2]==(25,0) and (await state(factory,'b'))[:2]==(25,0)
    finally:
        await engine.dispose()
