"""Actual native job/hold/bill transactions with synthetic adapter responses; no paid supplier calls."""
import asyncio
import json
from types import SimpleNamespace

from fastapi import HTTPException
import pytest
from sqlalchemy import func,select,text,update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession,async_sessionmaker

from app.core.contracts.model_accounting import BillingStatementContext
from app.core.contracts.seedance25 import MODEL_ID
from app.core.contracts.video_runtime_setup import VideoRuntimeConfiguration
from app.core.integrations.trusted_http import HttpResult
from app.core.integrations.volcengine.seedance25 import Seedance25DurableTransport
from app.models.execution import ExecutionJob,ExecutionSlot,SubmissionAttempt
from app.models.execution_budget import ExecutionBudget,ExecutionBudgetEntry
from app.models.llm import Model,Provider
from app.models.model_charge import ModelCallCharge
from app.models.task import GenerationTask
from app.services.worker.durable_execution import DurableExecution
from app.services.worker.execution_accounting import release_unaccepted,settle_actual
from app.services.worker.execution_budget import BudgetError,finish,transaction
from app.services.worker.execution_queries import control_job
from app.services.worker.execution_schema import migrate_execution
from app.services.worker.model_billing import import_statement,reconcile_statement
from app.services.worker.model_billing_schema import migrate_model_billing
from app.services.worker.native_video_accounting import NativeVideoAccounting,identity,recover_request
from app.services.worker.native_video_charge_schema import migrate_native_video_charges
from app.services.worker.native_video_charges import verify_plan
from app.services.worker.native_video_models import PROTOCOL
from app.services.worker.workspace_video_runtime import build_managed_video_runtime
from tests.test_durable_execution import Clock
from tests.test_model_charges import ACTOR
from tests.test_native_video_model_connections import setup as model_setup

SNAPSHOT={'model_id':MODEL_ID,'duration':4,'references':[],'prompt':'Self-authored software fixture only','prompt_revision':'1'}
SELECTION={'kind':'direct','snapshot':SNAPSHOT}


class ProviderFixture:
    """Return strictly synthetic HTTP facts while observing only method/body shape and exact call counts."""
    def __init__(self):self.calls=[];self.mode='valid';self.seen=asyncio.Event();self.release=asyncio.Event();self.query_status='running'
    async def request(self,_url,**options):
        """Emulate one response without opening a socket or retaining authorization headers."""
        method=options.get('method','GET');self.calls.append(method)
        if method=='POST':
            self.seen.set()
            if self.mode=='block':await self.release.wait()
            if self.mode=='bad_json':return HttpResult(200,{'x-request-id':'native-bill-1'},b'{')
            if self.mode=='error':return HttpResult(500,{'x-request-id':'native-bill-1'},b'{}')
            if self.mode=='no_request_id':return HttpResult(200,{},b'{"id":"native-task-1"}')
            if self.mode=='reflected_request_id':return HttpResult(200,{'x-request-id':'prefix-synthetic-key-suffix'},b'{"id":"native-task-1"}')
            return HttpResult(200,{'x-request-id':'native-bill-1'},b'{"id":"native-task-1"}')
        return HttpResult(200,{},json.dumps({'id':'native-task-1','model':MODEL_ID,'status':self.query_status}).encode())


async def setup(tmp_path):
    """Build real FK/immutability guards, one managed native model, two project budgets and a private fake adapter."""
    engine,factory,quote,registry=await model_setup(tmp_path)
    await migrate_execution(engine);await migrate_model_billing(engine);await migrate_native_video_charges(engine)
    async with factory() as db:
        await db.execute(update(Model).where(Model.id=='m').values(params={'native_video_protocol':PROTOCOL}));await db.commit()
    response=ProviderFixture();clock=Clock()
    def transport(config,**options):
        """Inject a zero-socket response fixture only at trusted server composition."""
        return Seedance25DurableTransport(config,requester=response.request,**options)
    config=VideoRuntimeConfiguration(enabled=True,model_id='m',service_version='native-fixture-v1')
    accountant=NativeVideoAccounting(factory,config,registry,transport_factory=transport)
    async with factory() as db:_connection,_quote,_profile,policy=await accountant.current(db)
    accountant.allow_new_post=True
    worker=DurableExecution(None,factory=factory,clock=clock,native_accounting=accountant)
    runtime=SimpleNamespace(worker=worker,policies={MODEL_ID:policy},allow_submission=True,running=True)
    return engine,factory,quote,accountant,worker,runtime,response,clock


async def admit(accountant,runtime,quote,*,key='new-native',project='a',actor=ACTOR,selection=SELECTION):
    """Use the actual complete admission service and exact confirmed operator fingerprint."""
    return await accountant.admit(project,key,selection,submitted_by=actor,confirmed=True,price_fingerprint=quote.fingerprint,runtime=runtime)


async def counters(factory,project='a'):
    """Read balances, actual job count and ledger count from a separate physical Session."""
    async with factory() as db:
        budget=await db.get(ExecutionBudget,project)
        jobs=await db.scalar(select(func.count()).select_from(ExecutionJob).where(ExecutionJob.project_id==project))
        entries=await db.scalar(select(func.count()).select_from(ExecutionBudgetEntry).where(ExecutionBudgetEntry.project_id==project))
        return budget.reserved_minor,budget.spent_minor,jobs,entries


@pytest.mark.asyncio
async def test_native_uses_one_actual_hold_and_exact_replay_precedes_current_price_and_connection(tmp_path):
    """Five identical concurrent requests reserve once; another project cannot lend funds or recover the first actor's intent."""
    engine,factory,quote,accountant,worker,runtime,response,_clock=await setup(tmp_path)
    try:
        results=await asyncio.gather(*(admit(accountant,runtime,quote) for _ in range(5)))
        assert all(result==results[0] for result in results)
        identifier=results[0]['job_ids'][0]
        assert await counters(factory)==(25,0,1,1) and await counters(factory,'b')==(0,0,0,0)
        async with factory() as db:
            call=await db.get(ModelCallCharge,identifier)
            assert call.kind=='native_video' and call.id==identifier and call.dispatch_state=='planned'
            assert await db.scalar(select(func.count()).select_from(GenerationTask))==1
            await db.execute(update(Provider).where(Provider.id=='p').values(api_key='rotated-native-key'));await db.commit()
        accountant.registry.rules.clear();runtime.allow_submission=False
        assert await admit(accountant,runtime,quote)==results[0]
        with pytest.raises(HTTPException):await admit(accountant,runtime,quote,actor='usr_'+'b'*32)
        with pytest.raises(HTTPException):await admit(accountant,runtime,quote,selection={'kind':'direct','snapshot':{**SNAPSHOT,'prompt':'different'}})
        assert await counters(factory)==(25,0,1,1) and response.calls==[]
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_exact_project_limit_and_conflicts_never_leave_partial_jobs(tmp_path):
    """Five competing distinct requests for four quotes admit exactly four complete jobs and cannot borrow project B."""
    engine,factory,quote,accountant,_worker,runtime,response,_clock=await setup(tmp_path)
    try:
        results=await asyncio.gather(*(admit(accountant,runtime,quote,key='job-'+str(index)) for index in range(5)),return_exceptions=True)
        assert sum(isinstance(result,dict) for result in results)==4
        assert sum(isinstance(result,HTTPException) and result.status_code==409 for result in results)==1
        assert await counters(factory)==(100,0,4,4)
        await admit(accountant,runtime,quote,project='b')
        assert await counters(factory,'b')==(25,0,1,1) and response.calls==[]
    finally:await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('control',['direct','worker'])
async def test_unsent_cancellation_advances_common_call_once_through_both_controls(tmp_path,control):
    """Both the light task API service and worker intent path release one planned native hold without drift or double refund."""
    engine,factory,quote,accountant,worker,runtime,response,_clock=await setup(tmp_path)
    try:
        identifier=(await admit(accountant,runtime,quote))['job_ids'][0]
        if control=='direct':
            async with factory() as db:
                await control_job(db,'a',identifier,'cancel');await db.commit()
        else:
            await worker.request_cancel(identifier);await worker.step(identifier)
        assert await release_unaccepted('a',identifier,factory=factory)=='released'
        async with factory() as db:
            assert (await db.get(ModelCallCharge,identifier)).dispatch_state=='not_sent'
            assert (await db.get(ExecutionBudgetEntry,identifier)).state=='released'
            assert await db.scalar(select(func.count()).select_from(SubmissionAttempt))==0
        assert await counters(factory)==(0,0,1,1) and response.calls==[]
    finally:await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode,accepted',[('valid',True),('bad_json',True),('error',True),('no_request_id',False),('reflected_request_id',False)])
async def test_real_post_response_identity_is_distinct_from_task_handle_and_never_implies_payment(tmp_path,mode,accepted):
    """Errors and bad JSON still retain a safe header identity; missing/reflected IDs do not guess from content IDs or release funds."""
    engine,factory,quote,accountant,worker,runtime,response,clock=await setup(tmp_path)
    try:
        response.mode=mode;identifier=(await admit(accountant,runtime,quote))['job_ids'][0]
        await worker.step(identifier)
        async with factory() as db:
            call=await db.get(ModelCallCharge,identifier);entry=await db.get(ExecutionBudgetEntry,identifier)
            assert call.dispatch_state==('accepted' if accepted else 'prepared')
            assert call.provider_request_id==('native-bill-1' if accepted else None)
            assert entry.state=='unknown' and entry.actual_minor is None
        assert await counters(factory)==(25,0,1,1) and response.calls==['POST']
        with pytest.raises(BudgetError):await settle_actual('a',identifier,12,factory=factory)
        async with factory() as db:
            with pytest.raises(BudgetError):await finish(db,'a',identifier,'release_reconciled')
        clock.advance();await worker.step(identifier)
        assert response.calls.count('POST')==1
    finally:await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('bill_status,bill_minor,final_state',[('charged',12,'settled'),('not_charged',0,'released')])
async def test_explicit_bill_uses_original_project_and_single_native_hold_once(tmp_path,bill_status,bill_minor,final_state):
    """The existing normalized reconciliation service settles an actual native charge and cannot charge a sibling project."""
    engine,factory,quote,accountant,worker,runtime,_response,_clock=await setup(tmp_path)
    try:
        identifier=(await admit(accountant,runtime,quote))['job_ids'][0];await worker.step(identifier)
        context=BillingStatementContext(provider_id=quote.provider_id,provider_connection_version=quote.provider_connection_version,
            account_version=quote.account_version,statement_key='native-fixture')
        csv=f'row_reference,provider_request_id,status,currency,amount_minor\nline-1,native-bill-1,{bill_status},CNY,{bill_minor}\n'.encode()
        statement=await import_statement(factory,context=context,normalized_csv=csv,original_content=b'Private self-authored native invoice fixture',imported_by=ACTOR)
        first=await reconcile_statement(factory,statement,recorded_by=ACTOR)
        assert first[0]['status']=='applied' and first[0]['project_id']=='a'
        assert (await reconcile_statement(factory,statement,recorded_by=ACTOR))[0]['replayed']
        assert await counters(factory)==(0,bill_minor,1,1) and await counters(factory,'b')==(0,0,0,0)
        async with factory() as db:
            entry=await db.get(ExecutionBudgetEntry,identifier)
            assert entry.state==final_state and entry.actual_minor==(bill_minor if final_state=='settled' else None)
            await verify_plan(db,(await db.get(ModelCallCharge,identifier)).batch_id)
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_current_price_and_iteration_change_do_not_rebind_original_control_but_key_rotation_blocks_it(tmp_path):
    """Accepted task queries ignore new prices/iteration caps, while an actual connection clock change causes zero new control HTTP."""
    engine,factory,quote,accountant,worker,runtime,response,clock=await setup(tmp_path)
    try:
        identifier=(await admit(accountant,runtime,quote))['job_ids'][0];await worker.step(identifier)
        accountant.registry.rules.clear();accountant.config=accountant.config.model_copy(update={'enabled':False,'iteration_limit':99})
        accountant.allow_new_post=False;runtime.allow_submission=False
        async with factory() as db:
            await db.execute(update(Model).where(Model.id=='m').values(params={}));await db.commit()
        clock.advance();await worker.step(identifier)
        assert response.calls==['POST','GET']
        async with factory() as db:
            await db.execute(update(Provider).where(Provider.id=='p').values(api_key='another-native-fixture-key'));await db.commit()
        clock.advance();await worker.step(identifier)
        assert response.calls==['POST','GET']
        async with factory() as db:assert (await db.get(ExecutionJob,identifier)).reason=='native_original_connection_unavailable'
        assert await counters(factory)==(25,0,1,1)
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_native_plan_seal_prevents_append_replace_and_source_reassignment(tmp_path):
    """Real SQLite rejects context/link changes and replacement of already priced jobs, bindings and their original entries."""
    engine,factory,quote,accountant,worker,runtime,_response,_clock=await setup(tmp_path)
    try:
        accepted=await admit(accountant,runtime,quote);identifier=accepted['job_ids'][0]
        attempts=[
            ("UPDATE native_video_charge_batches SET submitted_by='local_operator' WHERE id=:id",accepted['batch_id']),
            ("DELETE FROM native_video_job_charges WHERE job_id=:id",identifier),
            ("INSERT OR REPLACE INTO execution_bindings(job_id,binding_hash) VALUES(:id,'different')",identifier),
            ("INSERT OR REPLACE INTO execution_budget_entries(job_id,project_id,quote_minor,state) VALUES(:id,'a',25,'reserved')",identifier),
            ("INSERT OR REPLACE INTO native_video_job_charges SELECT * FROM native_video_job_charges WHERE job_id=:id",identifier),
        ]
        for statement,identity in attempts:
            async with factory() as db:
                with pytest.raises(IntegrityError):await db.execute(text(statement),{'id':identity})
                await db.rollback()
        async with factory() as db:await verify_plan(db,accepted['batch_id'])
        await worker.step(identifier)
        async with factory() as db:
            with pytest.raises(IntegrityError):await db.execute(text('INSERT OR REPLACE INTO submission_attempts SELECT * FROM submission_attempts WHERE job_id=:id'),{'id':identifier})
            await db.rollback()
        assert await counters(factory)==(25,0,1,1)
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_managed_application_builder_rejects_legacy_env_authority_and_keeps_control_separate(tmp_path):
    """A legacy manual quote cannot grant new app execution; disabled managed setup can still compose original-account control."""
    engine,factory,quote,accountant,_worker,_runtime,_response,_clock=await setup(tmp_path)
    try:
        path=tmp_path/'new-video-config.json'
        path.write_text(json.dumps(VideoRuntimeConfiguration(enabled=True,quote_minor=1,quote_note='legacy fixture').model_dump()),'utf-8')
        runtime,view=await build_managed_video_runtime(factory,str(path),accountant.registry)
        assert runtime is None and view['issue_code']=='managed_model_required'
        path.write_text(json.dumps(VideoRuntimeConfiguration(enabled=False,model_id='m').model_dump()),'utf-8')
        runtime,view=await build_managed_video_runtime(factory,str(path),accountant.registry)
        assert not runtime.allow_submission and runtime.allow_original_control and view['original_control_available']
        assert not runtime.worker.native_accounting.allow_new_post and not runtime.running
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_original_native_request_recovery_uses_one_read_snapshot_while_a_real_bill_settles(tmp_path):
    """A bill committed between financial reads cannot falsely reject an already accepted request with a mixed old/new ledger view."""
    engine,factory,quote,accountant,worker,runtime,_response,_clock=await setup(tmp_path)
    entered=asyncio.Event();release=asyncio.Event();pending=None;held=False
    try:
        async with engine.connect() as connection:assert (await connection.scalar(text('PRAGMA journal_mode=WAL'))).lower()=='wal'
        accepted=await admit(accountant,runtime,quote);await worker.step(accepted['job_ids'][0])
        statement=await import_statement(factory,context=BillingStatementContext(provider_id=quote.provider_id,
            provider_connection_version=quote.provider_connection_version,account_version=quote.account_version,statement_key='native-concurrent-read'),
            normalized_csv=b'row_reference,provider_request_id,status,currency,amount_minor\nline-1,native-bill-1,charged,CNY,12\n',
            original_content=b'Self-authored concurrent original-request bill',imported_by=ACTOR)
        class ReaderSession(AsyncSession):
            """Pause an actual old ledger SELECT while another connection commits a complete legitimate supplier-bill fixture."""
            async def scalar(self,query,*args,**kwargs):
                """Keep no SQL arguments or keys; select only the first ledger seam in this one read task."""
                nonlocal held
                result=await super().scalar(query,*args,**kwargs)
                if not held and 'FROM execution_budget_entries' in str(query):
                    held=True;entered.set();await release.wait()
                return result
        reader=async_sessionmaker(engine,class_=ReaderSession,expire_on_commit=False,autoflush=False)
        pending=asyncio.create_task(recover_request(reader,'a','new-native',identity('a',ACTOR,SELECTION,quote.fingerprint)))
        await asyncio.wait_for(entered.wait(),3)
        assert (await reconcile_statement(factory,statement,recorded_by=ACTOR))[0]['status']=='applied'
        assert await counters(factory)==(0,12,1,1)
        release.set();assert await asyncio.wait_for(pending,3)==accepted
    finally:
        release.set()
        if pending is not None and not pending.done():pending.cancel();await asyncio.gather(pending,return_exceptions=True)
        await engine.dispose()
