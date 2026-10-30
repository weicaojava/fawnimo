"""Actual published scripts and immutable native shot plans preserve complete financial batch semantics."""
import asyncio

from fastapi import HTTPException
import pytest
from sqlalchemy import func,select,text,update

from app.core.contracts.model_accounting import BillingStatementContext
from app.core.contracts.script_preparation import ScriptCreate,ScriptPublish
from app.core.contracts.shot_plan import AssetRegistration,PlanCreate,PlanCompile
from app.core.contracts.seedance25 import MODEL_ID
from app.models.execution import ExecutionJob,SubmissionAttempt
from app.models.execution_budget import ExecutionBudget,ExecutionBudgetEntry
from app.models.model_charge import ModelCallCharge
from app.models.production import ProductionAsset
from app.models.studio import Chapter,Character,Scene,Shot
from app.services.script_preparation.schema import migrate_script_preparation
from app.services.script_preparation.service import create_draft as create_script,publish_draft as publish_script
from app.services.shot_plan.schema import migrate_shot_plan
from app.services.shot_plan.service import create_draft,compile_draft,register_asset,admit_planned_shot
from app.services.worker.model_billing import import_statement,reconcile_statement
from app.schemas.studio.production import VersionWrite
from app.services.studio.production import add_version
from tests.test_model_charges import ACTOR
from tests.test_native_video_project_accounting import setup,admit,counters
from tests.test_script_preparation import document


async def planned_setup(tmp_path,*,count=2):
    """Publish only self-authored text and actual owned entities; no source video or credentials are copied."""
    result=await setup(tmp_path)
    engine,factory,_quote,_accountant,_worker,runtime,_response,_clock=result
    await migrate_script_preparation(engine);await migrate_shot_plan(engine)
    async with factory() as db:
        db.add(Chapter(id='c1',project_id='a',index=1,title='Self-authored native accounting fixture',raw_text='A short fictional scene.'))
        db.add(Character(id='hero',project_id='a',name='Fixture person',description='Self-authored identity',style='test'))
        db.add(Scene(id='scene',name='Fixture room',description='Self-authored room',style='test'));await db.flush()
        db.add(ProductionAsset(id='owned-scene',project_id='a',kind='scene',name='Fixture room',entity_id='scene'))
        for index in range(count):db.add(Shot(id='s'+str(index+1),chapter_id='c1',index=index,title='Fixture shot',status='ready'))
        await db.commit()
    async with factory() as db:
        await add_version(db,await db.get(ProductionAsset,'owned-scene'),VersionWrite(expected_revision=0,description='Self-authored room',media_ids=[]));await db.commit()
    value=document().model_dump();value['bindings'][0]['duration_ms']=4000
    value['target_duration_ms']=4000*count
    value['bindings']=[{**value['bindings'][0],'shot_id':'s'+str(index+1)} for index in range(count)]
    script=await create_script(factory,'a',ScriptCreate(chapter_id='c1',document=value))
    publication=await publish_script(factory,'a',script,ScriptPublish(expected_revision=1))
    character=await register_asset(factory,'a',AssetRegistration(kind='character',entity_id='hero'))
    scene=await register_asset(factory,'a',AssetRegistration(kind='scene',entity_id='scene'))
    choices=[{'shot_id':'s'+str(index+1),'version_ids':[character['version_id'],scene['version_id']],'reference_media_ids':[],'prompt_addition':''} for index in range(count)]
    draft=await create_draft(factory,'a',PlanCreate(chapter_id='c1',document={'model_id':MODEL_ID,'script_snapshot_id':publication,'shots':choices}))
    plan=await compile_draft(factory,'a',draft,PlanCompile(expected_revision=1),runtime)
    selection={'kind':'plan','plan_id':plan,'shot_ids':[choice['shot_id'] for choice in choices]}
    return result,selection


@pytest.mark.asyncio
async def test_whole_native_batch_insufficient_budget_rolls_back_all_jobs_and_exact_limit_reserves_only_once(tmp_path):
    """A two-shot action is one transaction; it never leaves a paid prefix or doubles the actual native holds."""
    values,selection=await planned_setup(tmp_path)
    engine,factory,quote,accountant,_worker,runtime,response,_clock=values
    try:
        async with factory() as db:
            await db.execute(update(ExecutionBudget).where(ExecutionBudget.project_id=='a').values(limit_minor=49));await db.commit()
        with pytest.raises(HTTPException):await admit(accountant,runtime,quote,selection=selection)
        assert await counters(factory)==(0,0,0,0)
        async with factory() as db:
            await db.execute(update(ExecutionBudget).where(ExecutionBudget.project_id=='a').values(limit_minor=50));await db.commit()
        results=await asyncio.gather(*(admit(accountant,runtime,quote,selection=selection) for _ in range(3)))
        assert results[0]==results[1]==results[2] and len(results[0]['job_ids'])==2
        assert await counters(factory)==(50,0,2,2) and await counters(factory,'b')==(0,0,0,0)
        async with factory() as db:assert await db.scalar(select(func.count()).select_from(ModelCallCharge))==2
        assert response.calls==[]
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_a_funded_above_quote_bill_on_the_first_unit_blocks_the_next_native_post(tmp_path):
    """Already settled liability remains a whole-plan gate, even when the first business job has become terminal."""
    values,selection=await planned_setup(tmp_path)
    engine,factory,quote,accountant,worker,runtime,response,clock=values
    try:
        accepted=await admit(accountant,runtime,quote,selection=selection)
        first,second=accepted['job_ids'];await worker.step(first)
        context=BillingStatementContext(provider_id=quote.provider_id,provider_connection_version=quote.provider_connection_version,
            account_version=quote.account_version,statement_key='native-overquote')
        csv=b'row_reference,provider_request_id,status,currency,amount_minor\nline-1,native-bill-1,charged,CNY,40\n'
        statement=await import_statement(factory,context=context,normalized_csv=csv,original_content=b'Self-authored overquote native bill',imported_by=ACTOR)
        assert (await reconcile_statement(factory,statement,recorded_by=ACTOR))[0]['status']=='applied'
        response.query_status='cancelled';clock.advance();await worker.step(first);await worker.step(second)
        assert response.calls==['POST','GET']
        async with factory() as db:
            assert (await db.get(ModelCallCharge,second)).dispatch_state=='planned'
            assert (await db.get(ExecutionJob,second)).reason=='native_project_fees_unavailable'
            assert (await db.get(ExecutionBudgetEntry,second)).state=='reserved'
        assert (await counters(factory))[:2]==(25,40)
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_an_incomplete_later_native_unit_blocks_the_first_post(tmp_path):
    """Simulated incorrect database maintenance cannot allow an apparently valid first unit to send a partial batch."""
    values,selection=await planned_setup(tmp_path)
    engine,factory,quote,accountant,worker,runtime,response,_clock=values
    try:
        accepted=await admit(accountant,runtime,quote,selection=selection)
        async with factory() as db:
            await db.execute(text('DROP TRIGGER native_video_job_charges_no_delete'))
            await db.execute(text('DELETE FROM native_video_job_charges WHERE job_id=:id'),{'id':accepted['job_ids'][1]});await db.commit()
        await worker.step(accepted['job_ids'][0])
        assert response.calls==[]
        async with factory() as db:assert await db.scalar(select(func.count()).select_from(SubmissionAttempt))==0
        assert await counters(factory)==(50,0,2,2)
    finally:await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('bad_state',['released','settled','unknown'])
async def test_bad_sibling_hold_lifecycle_blocks_the_first_native_post_even_when_totals_are_conserved(tmp_path,bad_state):
    """Incorrect maintenance of a later hold cannot authorize a paid prefix merely because its immutable quote still matches."""
    values,selection=await planned_setup(tmp_path)
    engine,factory,quote,accountant,worker,runtime,response,_clock=values
    try:
        accepted=await admit(accountant,runtime,quote,selection=selection)
        async with factory() as db:
            entry=await db.get(ExecutionBudgetEntry,accepted['job_ids'][1]);entry.state=bad_state
            if bad_state=='settled':entry.actual_minor=0
            if bad_state in {'released','settled'}:
                await db.execute(update(ExecutionBudget).where(ExecutionBudget.project_id=='a').values(reserved_minor=ExecutionBudget.reserved_minor-25))
            await db.commit()
        await worker.step(accepted['job_ids'][0])
        assert response.calls==[]
        async with factory() as db:assert await db.scalar(select(func.count()).select_from(SubmissionAttempt))==0
        assert (await counters(factory))[:2]==(50 if bad_state=='unknown' else 25,0)
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_fact_drift_releases_only_unprepared_native_units_and_exact_single_shot_replay_ignores_current_draft(tmp_path):
    """The legacy public service delegates new managed work, keeps the original actor and recovers it while runtime is off."""
    values,selection=await planned_setup(tmp_path,count=1)
    engine,factory,quote,_accountant,worker,runtime,response,_clock=values
    try:
        with pytest.raises(HTTPException) as mismatched:
            await admit_planned_shot(lambda:factory(),'a',selection['plan_id'],'s1','one-intent',runtime,
                submitted_by=ACTOR,confirmed=True,price_fingerprint=quote.fingerprint)
        assert mismatched.value.status_code==503 and await counters(factory)==(0,0,0,0)
        identifier=await admit_planned_shot(factory,'a',selection['plan_id'],'s1','one-intent',runtime,
            submitted_by=ACTOR,confirmed=True,price_fingerprint=quote.fingerprint)
        async with factory() as db:
            await db.execute(update(Character).where(Character.id=='hero').values(description='Edited after original admission'));await db.commit()
        assert await admit_planned_shot(factory,'a',selection['plan_id'],'s1','one-intent',None,
            submitted_by=ACTOR,confirmed=True,price_fingerprint=quote.fingerprint)==identifier
        with pytest.raises(HTTPException):await admit_planned_shot(factory,'a',selection['plan_id'],'s1','one-intent',None,
            submitted_by='usr_'+'b'*32,confirmed=True,price_fingerprint=quote.fingerprint)
        await worker.step(identifier)
        assert response.calls==[] and await counters(factory)==(0,0,1,1)
        async with factory() as db:assert (await db.get(ModelCallCharge,identifier)).dispatch_state=='not_sent'
    finally:await engine.dispose()
