"""Persisted business-attempt/project/batch bindings; no scanner or external model runs in this fixture."""
from fastapi import HTTPException
import pytest
from sqlalchemy import select,text,update
from sqlalchemy.exc import IntegrityError

from app.models.agent_execution import AgentRun,AgentNodeRun
from app.models.execution_budget import ExecutionBudgetEntry
from app.models.model_job_charge import ModelJobCharge
from app.services.agent_execution import clock
from app.services.worker.execution_budget import transaction
from app.services.worker.model_job_charge_schema import migrate_model_job_charges
from app.services.worker.model_job_charges import bind_new_job,binding_digest,charged_job_unit,job_charge,release_job_unsent
from app.services.worker.model_charges import prepare_call,reserve_in_transaction
from tests.test_model_charges import ACTOR,calls,fixture,state

BINDING={'model_id':'m','model_name':'Synthetic text','provider_id':'p','provider_connection_version':1,
    'base_url':'https://example.invalid/v1','params':{}}


async def attempt(db,identifier='job-one',count=2):
    """Save a concrete queued Agent row and its node specification in the same transaction as its future charge."""
    row=AgentRun(id=identifier,kind='workflow',scope_key='synthetic:'+identifier,request_key='once',request_hash='b'*64,
        agent_id=None,agent_name='Authored fixture',agent_version=1,config_snapshot={'workflow':[
            {'id':'node-'+str(index),'type':'llm'} for index in range(count)]},model_id='m',model_name='Synthetic text',
        model_binding=BINDING,status='queued',input_text='Synthetic text only',output_text='',error='',max_tokens=8,
        attempt=1,retry_of=None,created_at=clock())
    db.add(row);await db.flush()
    for index in range(count):
        db.add(AgentNodeRun(run_id=identifier,node_id='node-'+str(index),position=index,label='Synthetic node',type='llm',status='pending',
            input_text='',output_text='',prompt='',parameters={},usage={},error=''))
    await db.flush()
    return row


def units(count=2):
    """Use the exact saved non-secret model connection and an explicitly synthetic per-call ceiling."""
    return [unit.model_copy(update={'binding_hash':binding_digest(BINDING)}) for unit in calls(count=count)]


async def attach(db,identifier='job-one',**changes):
    """Match one saved Agent with its full expected quoted unit list."""
    arguments={'kind':'agent','job_id':identifier,'project_id':'a','submitted_by':ACTOR,'request_hash':'b'*64,'calls':units()}
    arguments.update(changes)
    return await bind_new_job(db,**arguments)


@pytest.mark.asyncio
async def test_saved_attempt_and_complete_project_reservation_replay_without_second_hold(tmp_path):
    """A normal persisted job owns exactly one original batch, while a sibling project remains untouched."""
    engine,factory=await fixture(tmp_path);await migrate_model_job_charges(engine)
    try:
        async with factory() as db:
            async with transaction(db):
                await attempt(db)
                first=await attach(db)
                batch=first.batch_id
        assert (await state(factory))[:2]==(50,0) and (await state(factory,'b'))[:2]==(0,0)
        async with factory() as db:
            async with transaction(db):
                assert (await attach(db)).batch_id==batch
                saved=await job_charge(db,'agent','job-one',required=True)
                assert saved.project_id=='a' and saved.submitted_by==ACTOR
                assert (await charged_job_unit(db,saved,'node-0')).batch_id==batch
        assert (await state(factory))[:2]==(50,0)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['project','actor','hash','unit_count','binding'])
async def test_attempt_cannot_be_reassigned_or_authorized_with_an_incomplete_plan(tmp_path,change):
    """A differing identity or quote plan rolls back without borrowing from another project."""
    engine,factory=await fixture(tmp_path);await migrate_model_job_charges(engine)
    try:
        async with factory() as db:
            async with transaction(db):
                await attempt(db);await attach(db)
        before=await state(factory)
        options={'project_id':'b'} if change=='project' else {'submitted_by':'usr_'+'f'*32} if change=='actor' else {
            'request_hash':'c'*64} if change=='hash' else {'calls':units(1)} if change=='unit_count' else {
                'calls':[item.model_copy(update={'binding_hash':'c'*64}) for item in units()]}
        async with factory() as db:
            with pytest.raises(HTTPException) as failure:
                async with transaction(db):
                    await attach(db,**options)
        assert failure.value.status_code==409 and await state(factory)==before and (await state(factory,'b'))[:2]==(0,0)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_insufficient_funds_roll_back_actual_attempt_batch_and_link_together(tmp_path):
    """No queued business attempt can survive without the complete authorized reservation."""
    engine,factory=await fixture(tmp_path);await migrate_model_job_charges(engine)
    try:
        before=await state(factory)
        async with factory() as db:
            with pytest.raises(HTTPException):
                async with transaction(db):
                    await attempt(db,count=5);await attach(db,calls=units(5))
        assert await state(factory)==before
        async with factory() as db:
            assert await db.get(AgentRun,'job-one') is None
            assert await job_charge(db,'agent','job-one') is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_binding_and_priced_job_inputs_reject_replace_delete_and_rewrite(tmp_path):
    """New linkage also protects the concrete job payload, while legitimate status changes remain possible."""
    engine,factory=await fixture(tmp_path);await migrate_model_job_charges(engine)
    try:
        async with factory() as db:
            async with transaction(db):
                await attempt(db);await attach(db)
        operations=["UPDATE model_job_charges SET project_id='b'",'DELETE FROM model_job_charges',
            'INSERT OR REPLACE INTO model_job_charges SELECT * FROM model_job_charges',
            "UPDATE agent_execution_runs SET input_text='Changed unauthorized input' WHERE id='job-one'",
            "DELETE FROM agent_execution_runs WHERE id='job-one'",
            'INSERT OR REPLACE INTO agent_execution_runs SELECT * FROM agent_execution_runs']
        for operation in operations:
            async with factory() as db:
                with pytest.raises(IntegrityError):
                    async with transaction(db):
                        await db.execute(text(operation))
        async with factory() as db:
            async with transaction(db):
                await db.execute(update(AgentRun).where(AgentRun.id=='job-one').values(status='cancelled'))
                await release_job_unsent(db,'agent','job-one')
        assert (await state(factory))[:2]==(0,0)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_original_job_release_keeps_prepared_unit_and_ignores_legacy_history(tmp_path):
    """Cancelling a partially prepared job releases only its own untouched node, never the possible HTTP charge."""
    engine,factory=await fixture(tmp_path);await migrate_model_job_charges(engine)
    try:
        async with factory() as db:
            async with transaction(db):
                await attempt(db);binding=await attach(db)
                charge=await charged_job_unit(db,binding,'node-0')
                await prepare_call(db,project_id='a',charge_id=charge.id,binding_hash=binding_digest(BINDING))
                await release_job_unsent(db,'agent','job-one')
                await release_job_unsent(db,'agent','unpriced-history')
        assert (await state(factory))[:2]==(25,0) and (await state(factory,'b'))[:2]==(0,0)
        async with factory() as db:
            with pytest.raises(HTTPException):
                await job_charge(db,'agent','unpriced-history',required=True)
            with pytest.raises(HTTPException):
                await charged_job_unit(db,binding,'foreign-or-missing-unit')
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('corruption',['missing_unit','wrong_protocol','entry_quote'])
async def test_native_link_and_scanner_reject_incomplete_or_inconsistent_financial_plan(tmp_path,corruption):
    """A maintenance-style insert cannot turn a partial or contradictory batch into permission to send a node."""
    engine,factory=await fixture(tmp_path);await migrate_model_job_charges(engine)
    try:
        async with factory() as db:
            async with transaction(db):
                await attempt(db)
                planned=units(1) if corruption=='missing_unit' else units()
                if corruption=='wrong_protocol':
                    planned=[unit.model_copy(update={'kind':'asset_image'}) for unit in planned]
                batch=await reserve_in_transaction(db,project_id='a',scope_key='model-job:agent:job-one',submitted_by=ACTOR,calls=planned)
                if corruption=='entry_quote':
                    await db.execute(update(ExecutionBudgetEntry).where(ExecutionBudgetEntry.project_id=='a').values(quote_minor=24))
        async with factory() as db:
            if corruption=='missing_unit':
                with pytest.raises(IntegrityError):
                    async with transaction(db):
                        db.add(ModelJobCharge(job_kind='agent',job_id='job-one',project_id='a',batch_id=batch,submitted_by=ACTOR,request_hash='b'*64))
                        await db.flush()
            else:
                async with transaction(db):
                    db.add(ModelJobCharge(job_kind='agent',job_id='job-one',project_id='a',batch_id=batch,submitted_by=ACTOR,request_hash='b'*64))
                    await db.flush()
        if corruption!='missing_unit':
            async with factory() as db:
                with pytest.raises(HTTPException,match='完整项目费用计划'):
                    await job_charge(db,'agent','job-one',required=True)
    finally:
        await engine.dispose()
