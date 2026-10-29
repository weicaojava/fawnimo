"""A mutable display/request model name cannot borrow an older model's server-authorized price."""
import hashlib
import json

from fastapi import HTTPException
import pytest
from sqlalchemy import event,select,update
from sqlalchemy.ext.asyncio import async_sessionmaker,create_async_engine

from app.core.db import Base
from app.core.contracts.model_accounting import BillingStatementContext,ModelPriceConfiguration,ModelPriceEnvelope,ModelPriceRule,QuotedModelCall
from app.models.execution_budget import ExecutionBudget,ExecutionBudgetEntry
from app.models.llm import Model,Provider
from app.models.model_charge import ModelCallCharge,ModelChargeBatch
from app.models.studio import Project
from app.services.llm.connection_versions import connection_version,migrate_connection_versions
from app.services.worker.execution_budget import transaction
from app.services.worker.model_billing import import_statement,reconcile_statement
from app.services.worker.model_billing_schema import migrate_model_billing
from app.services.worker.model_charge_schema import migrate_model_charges
from app.services.worker.model_charges import manifest,prepare_call,release_unprepared,reserve_batch
from app.services.worker.model_prices import ModelPriceRegistry
from tests.test_model_charges import ACTOR,calls,fixture,state
from tests.test_agent_execution import agent,fee_fields,fixture_price,isolated_client,local_model,terminal


@pytest.mark.asyncio
@pytest.mark.parametrize('point',['reserve','prepare'])
async def test_actual_model_name_must_match_price_even_when_provider_clock_is_unchanged(tmp_path,point):
    """Fresh metadata checks reject both a new batch and an older reserved unit after the underlying model name changes."""
    engine,factory=await fixture(tmp_path)
    try:
        batch=await reserve_batch(factory,project_id='a',scope_key='name-binding',submitted_by=ACTOR,calls=calls(count=1)) if point=='prepare' else None
        async with factory() as db:
            assert await connection_version(db,'p')==1
            await db.execute(update(Model).where(Model.id=='m').values(name='Another costly model'));await db.commit()
        async with factory() as db:assert await connection_version(db,'p')==1
        with pytest.raises(HTTPException) as failure:
            if point=='reserve':
                await reserve_batch(factory,project_id='a',scope_key='name-binding',submitted_by=ACTOR,calls=calls(count=1))
            else:
                async with factory() as db:
                    charge=await db.scalar(select(ModelCallCharge).where(ModelCallCharge.batch_id==batch));identity=charge.id
                async with factory() as db:
                    async with transaction(db):await prepare_call(db,project_id='a',charge_id=identity,binding_hash='a'*64)
        assert failure.value.status_code==409 and '模型名称' in failure.value.detail
        assert (await state(factory))[:2]==((0,0) if point=='reserve' else (25,0))
        if point=='prepare':
            async with factory() as db:assert (await db.get(ModelCallCharge,identity)).dispatch_state=='planned'
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_agent_rejects_old_quote_after_model_rename_and_sends_only_after_explicit_new_name_policy(tmp_path):
    """Actual loopback execution sends zero requests under the stale quote and one after an operator supplies a matching policy."""
    with local_model() as provider:
        async with isolated_client(tmp_path,provider) as (client,factory,runtimes):
            config=await agent(client,nodes=1)
            async with factory() as db:
                await db.execute(update(Model).where(Model.id=='fixture-model').values(name='different-authored-model'));await db.commit()
                assert await connection_version(db,'fixture-provider')==1
            ready=(await client.get('/api/v1/agent-execution/availability')).json()['data']
            assert not ready['available'] and ready['models'][0]['configured'] and not ready['models'][0]['priced']
            endpoint='/api/v1/agent-execution/agents/'+config['id']+'/runs'
            body={'request_key':'name-changed','input_text':'Authored input','expected_version':1,'confirm_model_call':True,**fee_fields()}
            assert (await client.post(endpoint,json=body)).status_code==503 and not provider['calls']
            quote=fixture_price().model_copy(update={'model_name':'different-authored-model'})
            runtimes[0].price_registry=ModelPriceRegistry(ModelPriceConfiguration(enabled=True,rules=[ModelPriceRule(quote=quote,kinds=['agent_text'])]))
            assert (await client.post(endpoint,json=body)).status_code==409 and not provider['calls']
            body['expected_price_fingerprint']=quote.fingerprint
            response=await client.post(endpoint,json=body)
            assert response.status_code==201,response.text
            assert (await terminal(client,response.json()['data']['id']))['status']=='succeeded'
            assert len(provider['calls'])==1 and provider['calls'][0]['body']['model']=='different-authored-model'


@pytest.mark.asyncio
async def test_legacy_quote_fingerprints_manifests_and_bill_settlement_survive_without_authorizing_new_http(tmp_path):
    """Exact v1 quote bytes remain auditable and settle a received call; another legacy planned call cannot dispatch."""
    old={'model_id':'m','provider_id':'p','provider_connection_version':0,'account_version':'legacy-account',
        'currency':'CNY','max_call_minor':25,'source_note':'Authored legacy quote fixture'}
    quote=ModelPriceEnvelope.model_validate(old)
    expected=hashlib.sha256(json.dumps(old,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    assert quote.model_name is None and quote.model_dump()==old and quote.fingerprint==expected
    units=[{'unit_key':key,'kind':'agent_text','binding_hash':'a'*64,'quote':old} for key in ('old-first','old-later')]
    legacy_manifest=hashlib.sha256(json.dumps({'project_id':'legacy','submitted_by':'local_operator','calls':units},ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    assert manifest('legacy','local_operator',[QuotedModelCall.model_validate(row) for row in units])==legacy_manifest
    registry=ModelPriceRegistry(ModelPriceConfiguration(enabled=True,rules=[ModelPriceRule(quote=quote,kinds=['agent_text'])]))
    with pytest.raises(HTTPException):registry.required('agent_text','m')
    engine=create_async_engine('sqlite+aiosqlite:///'+(tmp_path/'legacy.db').as_posix())
    @event.listens_for(engine.sync_engine,'connect')
    def foreign_keys(connection,_):
        """Exercise actual old-row referential integrity rather than disabling foreign keys for compatibility."""
        connection.execute('PRAGMA foreign_keys=ON')
    async with engine.begin() as connection:await connection.run_sync(Base.metadata.create_all)
    factory=async_sessionmaker(engine,expire_on_commit=False,autoflush=False)
    try:
        async with factory() as db:
            db.add(Project(id='legacy',name='Legacy fixture',style='真人都市'));await db.flush()
            db.add(Provider(id='p',name='openai',base_url='https://example.invalid/v1',api_key='authored-legacy-key',status='testing'));await db.flush()
            db.add(Model(id='m',name='Legacy model',category='text',provider_id='p',params={}));await db.flush()
            db.add(ExecutionBudget(project_id='legacy',currency='CNY',limit_minor=100,reserved_minor=50,spent_minor=0,revision=0));await db.flush()
            db.add(ModelChargeBatch(id='legacy-batch',scope_key='legacy-batch',project_id='legacy',submitted_by='local_operator',manifest_hash=legacy_manifest,call_count=2));await db.flush()
            for identity,unit,dispatch,request in [('legacy-sent','old-first','accepted','legacy-response'),('legacy-planned','old-later','planned',None)]:
                db.add(ExecutionBudgetEntry(job_id=identity,project_id='legacy',quote_minor=25,state='unknown' if request else 'reserved'));await db.flush()
                db.add(ModelCallCharge(id=identity,project_id='legacy',batch_id='legacy-batch',unit_key=unit,kind='agent_text',model_id='m',provider_id='p',
                    provider_connection_version=0,account_version='legacy-account',binding_hash='a'*64,price_snapshot=old,dispatch_state=dispatch,provider_request_id=request));await db.flush()
            await db.commit()
        await migrate_connection_versions(engine);await migrate_model_charges(engine);await migrate_model_billing(engine)
        statement=await import_statement(factory,context=BillingStatementContext(provider_id='p',provider_connection_version=0,account_version='legacy-account',statement_key='legacy-invoice'),
            normalized_csv=b'row_reference,provider_request_id,status,currency,amount_minor\nold,legacy-response,charged,CNY,12\n',original_content=b'Authored original legacy bill evidence only')
        assert (await reconcile_statement(factory,statement))[0]['status']=='applied'
        async with factory() as db:
            with pytest.raises(HTTPException) as failure:
                async with transaction(db):await prepare_call(db,project_id='legacy',charge_id='legacy-planned',binding_hash='a'*64)
            assert '实际模型名称' in failure.value.detail
        async with factory() as db:
            async with transaction(db):await release_unprepared(db,project_id='legacy',batch_id='legacy-batch')
        async with factory() as db:
            budget=await db.get(ExecutionBudget,'legacy')
            assert (budget.reserved_minor,budget.spent_minor)==(0,12)
            assert (await db.get(ModelCallCharge,'legacy-sent')).price_snapshot==old
    finally:
        await engine.dispose()
