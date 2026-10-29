"""Atomic project model-call accounting in fresh SQLite; no model transport is configured."""
import asyncio

from fastapi import HTTPException
import pytest
from pydantic import ValidationError
from sqlalchemy import event,func,select,text,update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker,create_async_engine

from app.core.contracts.model_accounting import ModelPriceEnvelope,QuotedModelCall
from app.core.db import Base
from app.models.execution_budget import ExecutionBudget,ExecutionBudgetEntry
from app.models.llm import Model,Provider
from app.models.model_charge import ModelCallCharge,ModelChargeBatch
from app.models.studio import Project
from app.models.task import GenerationTask
from app.services.llm.connection_versions import migrate_connection_versions
from app.services.worker.execution_budget import configure,reserve,transaction
from app.services.worker.model_charge_schema import migrate_model_charges
from app.services.worker.model_charges import prepare_call,record_accepted_call,release_unprepared,reserve_batch,reserve_in_transaction

ACTOR='usr_'+'a'*32


async def fixture(tmp_path):
    """Give two independent projects balances in the same space, alongside one untouched project."""
    engine=create_async_engine('sqlite+aiosqlite:///'+(tmp_path/'charges.db').as_posix())
    @event.listens_for(engine.sync_engine,'connect')
    def sqlite_constraints(connection,_record):
        """Enforce real FK scope and a bounded wait for competing BEGIN IMMEDIATE writers."""
        connection.execute('PRAGMA foreign_keys=ON');connection.execute('PRAGMA busy_timeout=5000')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await migrate_connection_versions(engine)
    await migrate_model_charges(engine)
    factory=async_sessionmaker(engine,expire_on_commit=False,autoflush=False)
    async with factory() as db:
        for identity in ('a','b','unconfigured'):
            db.add(Project(id=identity,name='Synthetic '+identity,style='真人都市'))
        db.add(Provider(id='p',name='openai',base_url='https://example.invalid/v1',api_key='synthetic-key',status='testing'))
        await db.flush()
        db.add(Model(id='m',name='Synthetic text',category='text',provider_id='p',params={}))
        await db.commit()
    for project in ('a','b'):
        async with factory() as db:
            await configure(db,project,'CNY',100)
    return engine,factory


def calls(*,count=2,amount=25,revision=1):
    """Use an explicitly synthetic operator quote, never a fetched or invented supplier price."""
    quote=ModelPriceEnvelope(model_id='m',model_name='Synthetic text',provider_id='p',provider_connection_version=revision,
        account_version='synthetic-account-v1',max_call_minor=amount,source_note='Synthetic accounting fixture; no supplier or actual bill')
    return [QuotedModelCall(unit_key='node-'+str(index),kind='agent_text',binding_hash='a'*64,quote=quote) for index in range(count)]


async def state(factory,project='a'):
    """Read independent project counters and all typed evidence for exact before/after checks."""
    async with factory() as db:
        row=await db.get(ExecutionBudget,project)
        batches=await db.scalar(select(func.count()).select_from(ModelChargeBatch).where(ModelChargeBatch.project_id==project))
        entries=(await db.execute(select(ExecutionBudgetEntry.job_id,ExecutionBudgetEntry.state,ExecutionBudgetEntry.quote_minor)
            .where(ExecutionBudgetEntry.project_id==project).order_by(ExecutionBudgetEntry.job_id))).all()
        return (row.reserved_minor,row.spent_minor,row.revision,batches,[tuple(item) for item in entries])


@pytest.mark.asyncio
async def test_model_calls_share_the_existing_project_ledger_without_any_space_total(tmp_path):
    """Video and text holds spend one project balance; the other project's full balance stays usable."""
    engine,factory=await fixture(tmp_path)
    try:
        async with factory() as db:
            await reserve(db,'a','existing-video-hold',10,'CNY')
        identifier=await reserve_batch(factory,project_id='a',scope_key='agent:one',submitted_by=ACTOR,calls=calls())
        assert (await state(factory))[:2]==(60,0)
        assert (await state(factory,'b'))[:2]==(0,0)
        assert await reserve_batch(factory,project_id='a',scope_key='agent:one',submitted_by=ACTOR,calls=list(reversed(calls())))==identifier
        assert (await state(factory))[:2]==(60,0)
        await reserve_batch(factory,project_id='b',scope_key='agent:other-project',submitted_by=ACTOR,calls=calls(count=1,amount=100))
        assert (await state(factory,'b'))[:2]==(100,0)
        assert (await state(factory))[:2]==(60,0)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_competing_batches_cannot_overreserve_or_leave_partial_rows(tmp_path):
    """Two sixty-minor batches compete for one hundred; one wins, with exactly its own entries."""
    engine,factory=await fixture(tmp_path)
    try:
        results=await asyncio.gather(*[reserve_batch(factory,project_id='a',scope_key='agent:'+key,submitted_by=ACTOR,calls=calls(amount=30)) for key in ('one','two')],return_exceptions=True)
        assert len([item for item in results if isinstance(item,str)])==1
        errors=[item for item in results if isinstance(item,HTTPException)]
        assert len(errors)==1 and errors[0].status_code==409
        result=await state(factory)
        assert result[0]==60 and result[3]==1 and len(result[4])==2
        assert (await state(factory,'b'))[0]==0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_failed_job_and_batch_are_rolled_back_together(tmp_path):
    """Insufficient funds roll back a new job in the same actual writer transaction."""
    engine,factory=await fixture(tmp_path)
    try:
        before=await state(factory)
        with pytest.raises(HTTPException) as failure:
            async with factory() as db:
                async with transaction(db):
                    db.add(GenerationTask(id='never-admitted',mode='async_polling',task_kind='synthetic-no-executor',payload={'fixture_only':True}))
                    await db.flush()
                    await reserve_in_transaction(db,project_id='a',scope_key='agent:too-large',submitted_by=ACTOR,calls=calls(amount=60))
        assert failure.value.status_code==409
        assert await state(factory)==before
        async with factory() as db:
            assert await db.get(GenerationTask,'never-admitted') is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['project','actor','quote','call_count'])
async def test_an_existing_batch_cannot_be_rebound_or_repriced(tmp_path,change):
    """Reusing a saved scope with a different project, actor or immutable manifest spends nothing new."""
    engine,factory=await fixture(tmp_path)
    try:
        arguments={'project_id':'a','scope_key':'agent:original','submitted_by':ACTOR,'calls':calls()}
        await reserve_batch(factory,**arguments)
        before=await state(factory)
        if change=='project':arguments['project_id']='b'
        if change=='actor':arguments['submitted_by']='usr_'+'b'*32
        if change=='quote':arguments['calls']=calls(amount=26)
        if change=='call_count':arguments['calls']=calls(count=1)
        with pytest.raises(HTTPException) as failure:
            await reserve_batch(factory,**arguments)
        assert failure.value.status_code==409
        assert await state(factory)==before
        assert (await state(factory,'b'))[0]==0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_prepared_call_holds_unknown_funds_and_only_unsent_units_release(tmp_path):
    """Possible acceptance survives cancellation/reopen; response IDs do not settle guessed costs."""
    engine,factory=await fixture(tmp_path)
    try:
        batch=await reserve_batch(factory,project_id='a',scope_key='agent:dispatch',submitted_by=ACTOR,calls=calls())
        async with factory() as db:
            charge_id=await db.scalar(select(ModelCallCharge.id).where(ModelCallCharge.batch_id==batch,ModelCallCharge.unit_key=='node-0'))
        async with factory() as db:
            async with transaction(db):
                await prepare_call(db,project_id='a',charge_id=charge_id,binding_hash='a'*64)
        await engine.dispose()
        async with factory() as db:
            async with transaction(db):
                assert await release_unprepared(db,project_id='a',batch_id=batch)==1
                await record_accepted_call(db,project_id='a',charge_id=charge_id,provider_request_id='fixture-request-1')
        result=await state(factory)
        assert result[:2]==(25,0)
        assert {entry[1] for entry in result[4]}=={'unknown','released'}
        async with factory() as db:
            async with transaction(db):
                assert await release_unprepared(db,project_id='a',batch_id=batch)==0
                await record_accepted_call(db,project_id='a',charge_id=charge_id,provider_request_id='fixture-request-1')
        for project,identity in [('a',charge_id),('b',charge_id),('a','missing')]:
            with pytest.raises(HTTPException) as failure:
                async with factory() as db:
                    async with transaction(db):
                        await prepare_call(db,project_id=project,charge_id=identity,binding_hash='a'*64)
            assert failure.value.status_code==(409 if project=='a' and identity==charge_id else 404)
        assert await state(factory)==result
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_rotated_connection_cannot_prepare_or_reserve_old_quote(tmp_path):
    """A changed account blocks dispatch and later old-price admission, while proven-unsent holds can release."""
    engine,factory=await fixture(tmp_path)
    try:
        batch=await reserve_batch(factory,project_id='a',scope_key='agent:before-change',submitted_by=ACTOR,calls=calls())
        async with factory() as db:
            charge_id=await db.scalar(select(ModelCallCharge.id).where(ModelCallCharge.batch_id==batch))
            await db.execute(update(Provider).where(Provider.id=='p').values(api_key='synthetic-rotated-key'))
            await db.commit()
        for action in ('prepare','reserve'):
            with pytest.raises(HTTPException) as failure:
                if action=='prepare':
                    async with factory() as db:
                        async with transaction(db):
                            await prepare_call(db,project_id='a',charge_id=charge_id,binding_hash='a'*64)
                else:
                    await reserve_batch(factory,project_id='a',scope_key='agent:stale-quote',submitted_by=ACTOR,calls=calls())
            assert failure.value.status_code==409
        assert (await state(factory))[:2]==(50,0)
        async with factory() as db:
            async with transaction(db):
                assert await release_unprepared(db,project_id='a',batch_id=batch)==2
        assert (await state(factory))[:2]==(0,0)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_database_retains_immutable_batch_and_call_identity(tmp_path):
    """Raw writes cannot reassign a typed hold or erase its original quote and submitter."""
    engine,factory=await fixture(tmp_path)
    try:
        await reserve_batch(factory,project_id='a',scope_key='agent:immutable',submitted_by=ACTOR,calls=calls())
        for statement in ["DELETE FROM model_call_charges","DELETE FROM model_charge_batches", "UPDATE model_call_charges SET project_id='b'",
                          "UPDATE model_call_charges SET price_snapshot='{}'", "UPDATE model_charge_batches SET submitted_by='local_operator'"]:
            async with factory() as db:
                with pytest.raises(IntegrityError):
                    async with transaction(db):
                        await db.execute(text(statement))
        assert (await state(factory))[0]==50
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_two_dispatchers_can_prepare_only_once(tmp_path):
    """Competing execution ownership attempts get exactly one permission to issue the original call."""
    engine,factory=await fixture(tmp_path)
    try:
        batch=await reserve_batch(factory,project_id='a',scope_key='agent:dispatch-race',submitted_by=ACTOR,calls=calls(count=1))
        async with factory() as db:
            identity=await db.scalar(select(ModelCallCharge.id).where(ModelCallCharge.batch_id==batch))
        async def dispatch():
            """Use the same actual writer transaction boundary that a worker must hold."""
            async with factory() as db:
                async with transaction(db):
                    await prepare_call(db,project_id='a',charge_id=identity,binding_hash='a'*64)
            return 'prepared'
        results=await asyncio.gather(dispatch(),dispatch(),return_exceptions=True)
        assert results.count('prepared')==1
        rejected=[item for item in results if isinstance(item,HTTPException)]
        assert len(rejected)==1 and rejected[0].status_code==409
        result=await state(factory)
        assert result[:2]==(25,0) and result[4][0][1]=='unknown'
    finally:
        await engine.dispose()


@pytest.mark.parametrize('value',[None,False,True,0,-1,1.5,'10'])
def test_unknown_free_or_non_integer_prices_are_never_admissible(value):
    """Missing operator prices and permissive numeric coercion cannot create free model calls."""
    with pytest.raises(ValidationError):
        ModelPriceEnvelope(model_id='m',provider_id='p',provider_connection_version=1,account_version='fixture',max_call_minor=value,source_note='Synthetic')
