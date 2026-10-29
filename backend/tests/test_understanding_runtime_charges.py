"""Actual authored vision/ASR HTTP proves complete project fee plans, safe recovery and retained unknown outcomes."""
import asyncio
import json
import time
import uuid

from fastapi import HTTPException
import pytest
from sqlalchemy import func, select, text, update

from app.core.contracts.model_accounting import BillingStatementContext, ModelPriceConfiguration
from app.core.contracts.video_understanding import UnderstandingConnectionWrite
from app.models.execution_budget import ExecutionBudget, ExecutionBudgetEntry
from app.models.llm import Model, Provider
from app.models.model_charge import ModelCallCharge
from app.models.understanding_charge import UnderstandingAttemptCharge
from app.models.video_dna import DnaDraft
from app.models.video_understanding import UnderstandingAttempt, UnderstandingJob
from app.services.video_understanding import service
from app.services.worker.model_billing import billing_summary, import_statement, reconcile_statement
from app.services.worker.execution_budget import transaction
from app.services.worker.model_prices import ModelPriceRegistry
from tests.test_video_understanding import ModelFixture, configured, explicit_retry, fixture


async def balance(factory, project='p'):
    """Inspect real original-project counters outside any admission or worker transaction."""
    async with factory() as db:
        row = await db.get(ExecutionBudget, project)
        return row.reserved_minor, row.spent_minor


async def calls(factory, identifier, attempt=1):
    """Project the exact immutable attempt link and typed financial units, not a synthetic task outcome."""
    async with factory() as db:
        link = await db.get(UnderstandingAttemptCharge, (identifier, attempt))
        return (await db.scalars(select(ModelCallCharge).where(ModelCallCharge.batch_id==link.batch_id).order_by(ModelCallCharge.kind))).all()


async def authored_bill(factory, rows, key='authored-invoice'):
    """Apply explicitly self-authored account/request totals; this does not authenticate a supplier invoice."""
    csv = 'row_reference,provider_request_id,status,currency,amount_minor\n' + ''.join(
        f'row-{index},{request},charged,CNY,{amount}\n' for index, (request, amount) in enumerate(rows))
    statement = await import_statement(factory,context=BillingStatementContext(provider_id='fixture-understanding-provider',
        provider_connection_version=1,account_version='authored-understanding',statement_key=key),
        normalized_csv=csv.encode(),original_content=b'Authored accounting fixture, not supplier evidence')
    return await reconcile_statement(factory, statement)


@pytest.mark.asyncio
async def test_actual_two_phase_success_retains_unknown_holds_until_explicit_matching_bills(tmp_path, monkeypatch):
    """A successful candidate is inert for billing; only two explicit original-request amounts settle this project's units."""
    engine, factory, analysis = await fixture(tmp_path, monkeypatch)
    provider = ModelFixture()
    try:
        with provider.server() as base:
            worker, identifier, intent = await configured(factory, analysis, base)
            assert await balance(factory)==(18,0) and await balance(factory,'other')==(0,0)
            await worker.run_job(identifier)
            units = await calls(factory,identifier)
            assert len(provider.calls)==2 and all(unit.dispatch_state=='accepted' for unit in units)
            assert {unit.provider_request_id for unit in units}=={'authored-understanding-1','authored-understanding-2'}
            assert await balance(factory)==(18,0)
            async with factory() as db:
                view = await service.job_view(db,'p',identifier)
                assert view['state']=='succeeded' and not view['document']['human_reviewed']
                public = json.dumps(view,ensure_ascii=False)
                assert 'fixture-only-token' not in public and 'authored-understanding-1' not in public
            assert await service.create_job(factory,'p',intent,worker)==identifier
            result = await authored_bill(factory,[('authored-understanding-1',3),('authored-understanding-2',9)])
            assert [row['status'] for row in result]==['applied','applied']
            assert await balance(factory)==(0,12) and await balance(factory,'other')==(0,0)
            assert len(provider.calls)==2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['untimed','http_error','malformed_json','duplicate_json'])
async def test_failed_asr_records_response_evidence_and_releases_only_unsent_vision(tmp_path,monkeypatch,mode):
    """Bad HTTP/JSON/segment responses retain the actually sent ASR hold and never trigger a vision prefix or automatic retry."""
    engine,factory,analysis=await fixture(tmp_path,monkeypatch)
    provider=ModelFixture();provider.audio_mode=mode
    try:
        with provider.server() as base:
            worker,identifier,_intent=await configured(factory,analysis,base)
            await worker.run_job(identifier);await worker.tick()
            assert len(provider.calls)==1 and await balance(factory)==(7,0)
            units={unit.kind:unit for unit in await calls(factory,identifier)}
            assert units['video_asr'].dispatch_state=='accepted' and units['video_asr'].provider_request_id=='authored-understanding-1'
            assert units['video_vision'].dispatch_state=='not_sent' and units['video_vision'].provider_request_id is None
            async with factory() as db:
                view=await service.job_view(db,'p',identifier)
                assert view['state']=='failed' and view['document'] is None
                assert 'fixture-only-token' not in json.dumps(view)
                assert await db.scalar(select(func.count()).select_from(DnaDraft))==0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('request_id_mode',['missing','secret'])
async def test_missing_or_secret_reflecting_request_ids_never_guess_bill_identity(tmp_path,monkeypatch,request_id_mode):
    """A valid result without an admissible private response header keeps unknown holds and no fabricated request identifier."""
    engine,factory,analysis=await fixture(tmp_path,monkeypatch)
    provider=ModelFixture();provider.request_id_mode=request_id_mode
    try:
        with provider.server() as base:
            worker,identifier,_intent=await configured(factory,analysis,base)
            await worker.run_job(identifier)
            assert len(provider.calls)==2 and await balance(factory)==(18,0)
            assert all(unit.dispatch_state=='prepared' and unit.provider_request_id is None for unit in await calls(factory,identifier))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_reflected_asr_and_vision_keys_are_redacted_before_next_provider_and_candidate(tmp_path,monkeypatch):
    """A provider's reflected key is removed before ASR text can enter the later vision request or the persisted candidate."""
    engine,factory,analysis=await fixture(tmp_path,monkeypatch)
    provider=ModelFixture();provider.audio_mode=provider.vision_mode='echo_secret'
    try:
        with provider.server() as base:
            worker,identifier,_intent=await configured(factory,analysis,base)
            await worker.run_job(identifier)
            assert len(provider.calls)==2 and b'fixture-only-token' not in provider.calls[1]['body']
            async with factory() as db:
                view=await service.job_view(db,'p',identifier)
                assert view['state']=='succeeded' and 'fixture-only-token' not in json.dumps(view)
                assert view['transcripts'][0]['text']=='reflected [redacted]'
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('action',['cancel','rename_vision','rotate_key','change_price','funded_overage','disputed_overage'])
async def test_change_between_actual_asr_response_and_vision_never_sends_next_unit(tmp_path,monkeypatch,action):
    """The original ASR response stays accounted for while cancellation, rotation or a new invoice fences the later real HTTP."""
    engine,factory,analysis=await fixture(tmp_path,monkeypatch)
    provider=ModelFixture()
    try:
        with provider.server() as base:
            worker,identifier,_intent=await configured(factory,analysis,base)
            record=worker.record_response
            async def after_response(job,charge_id,request_id):
                """Inject the state change only after the actual first response is durably linked to its original charge."""
                await record(job,charge_id,request_id)
                if len(provider.calls)!=1:
                    return
                if action=='cancel':
                    await service.cancel_job(factory,'p',identifier,1)
                elif action in {'rename_vision','rotate_key'}:
                    async with factory() as db:
                        statement=update(Model).where(Model.id=='fixture-vision').values(name='a new costly vision model') if action=='rename_vision' else update(Provider).where(Provider.id=='fixture-understanding-provider').values(api_key='a-new-fixture-token')
                        await db.execute(statement);await db.commit()
                elif action=='change_price':
                    rules=[{'kinds':rule.kinds,'quote':rule.quote.model_copy(update={'max_call_minor':rule.quote.max_call_minor+1})} for rule in worker.price_registry.configuration.rules]
                    worker.price_registry=ModelPriceRegistry(ModelPriceConfiguration(enabled=True,rules=rules))
                else:
                    await authored_bill(factory,[(request_id,8 if action=='funded_overage' else 20000)])
            monkeypatch.setattr(worker,'record_response',after_response)
            await worker.run_job(identifier)
            assert len(provider.calls)==1
            assert await balance(factory)==((0,8) if action=='funded_overage' else (7,0))
            units={unit.kind:unit for unit in await calls(factory,identifier)}
            assert units['video_vision'].dispatch_state=='not_sent' and units['video_asr'].provider_request_id=='authored-understanding-1'
            async with factory() as db:
                view=await service.job_view(db,'p',identifier)
                assert view['state'] in {'failed','cancelled'} and view['document'] is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['vision_name','asr_name','connection_key','missing_vision_price'])
async def test_whole_plan_rechecked_before_first_http_not_only_the_first_asr_unit(tmp_path,monkeypatch,change):
    """A stale second unit cannot leak a first-unit prefix; all untouched holds release without any model HTTP."""
    engine,factory,analysis=await fixture(tmp_path,monkeypatch)
    provider=ModelFixture()
    try:
        with provider.server() as base:
            worker,identifier,_intent=await configured(factory,analysis,base)
            if change=='missing_vision_price':
                worker.price_registry=ModelPriceRegistry(ModelPriceConfiguration(enabled=True,
                    rules=[rule for rule in worker.price_registry.configuration.rules if rule.kinds==['video_asr']]))
            else:
                async with factory() as db:
                    if change=='connection_key':
                        statement=update(Provider).where(Provider.id=='fixture-understanding-provider').values(api_key='new-fixture-only-token')
                    else:
                        statement=update(Model).where(Model.id==('fixture-vision' if change=='vision_name' else 'fixture-asr')).values(name='renamed costly model')
                    await db.execute(statement);await db.commit()
            await worker.run_job(identifier)
            assert provider.calls==[] and await balance(factory)==(0,0)
            assert all(unit.dispatch_state=='not_sent' for unit in await calls(factory,identifier))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('same_key',[True,False])
async def test_concurrent_complete_admission_is_idempotent_or_rejects_one_without_borrowing(tmp_path,monkeypatch,same_key):
    """Competing real admissions share one writer lock and can never reserve a partial batch or sibling budget."""
    engine,factory,analysis=await fixture(tmp_path,monkeypatch)
    provider=ModelFixture()
    try:
        with provider.server() as base:
            worker,identifier,intent=await configured(factory,analysis,base)
            await service.cancel_job(factory,'p',identifier,1)
            async with factory() as db:
                await db.execute(update(ExecutionBudget).where(ExecutionBudget.project_id=='p').values(limit_minor=18));await db.commit()
            first=intent.model_copy(update={'request_key':'concurrent-first'})
            second=intent.model_copy(update={'request_key':'concurrent-first' if same_key else 'concurrent-second'})
            result=await asyncio.gather(service.create_job(factory,'p',first,worker),service.create_job(factory,'p',second,worker),return_exceptions=True)
            if same_key:
                assert result[0]==result[1] and isinstance(result[0],str)
            else:
                assert sum(isinstance(row,HTTPException) and row.status_code==409 for row in result)==1
                assert sum(isinstance(row,str) for row in result)==1
            assert await balance(factory)==(18,0) and await balance(factory,'other')==(0,0) and provider.calls==[]
            async with factory() as db:
                assert await db.scalar(select(func.count()).select_from(UnderstandingJob))==2
                assert await db.scalar(select(func.count()).select_from(UnderstandingAttemptCharge))==2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_original_retry_recovery_works_at_full_cap_after_configuration_and_price_change(tmp_path,monkeypatch):
    """A lost retry response replays its exact accepted body before present balance, attempt or configuration checks."""
    engine,factory,analysis=await fixture(tmp_path,monkeypatch)
    provider=ModelFixture();provider.audio_mode='untimed'
    try:
        with provider.server() as base:
            worker,identifier,_intent=await configured(factory,analysis,base)
            await worker.run_job(identifier)
            async with factory() as db:
                await db.execute(update(ExecutionBudget).where(ExecutionBudget.project_id=='p').values(limit_minor=25));await db.commit()
            body=await explicit_retry(factory,worker,identifier,1,'recover-original-retry')
            assert await balance(factory)==(25,0)
            worker.price_registry=ModelPriceRegistry()
            async with factory() as db:
                await db.execute(update(Provider).where(Provider.id=='fixture-understanding-provider').values(api_key='rotated-after-accepted-retry'));await db.commit()
            await service.retry_job(factory,'p',identifier,body,worker)
            assert await balance(factory)==(25,0) and len(provider.calls)==1
            async with factory() as db:
                assert (await db.get(UnderstandingJob,identifier)).attempt==2
                assert await db.scalar(select(func.count()).select_from(UnderstandingAttemptCharge))==2
            with pytest.raises(HTTPException):
                await service.retry_job(factory,'p',identifier,body,worker,submitted_by='usr_'+'a'*32)
            with pytest.raises(HTTPException) as cross:
                await service.retry_job(factory,'other',identifier,body,worker)
            assert cross.value.status_code==404
            with pytest.raises(HTTPException):
                await service.cancel_job(factory,'p',identifier,1)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_unpriced_queued_job_stays_readable_but_never_sends_with_host_environment(tmp_path,monkeypatch):
    """An old URL/env journal cannot acquire fresh fee authority or reuse a host credential by merely reaching the scanner."""
    engine,factory,analysis=await fixture(tmp_path,monkeypatch)
    provider=ModelFixture()
    try:
        with provider.server() as base:
            worker,identifier,_intent=await configured(factory,analysis,base)
            await service.cancel_job(factory,'p',identifier,1)
            async with factory() as db:
                original=await db.get(UnderstandingJob,identifier)
                legacy=UnderstandingJob(id='legacy-queued',project_id='p',analysis_id=analysis,request_key='old-request',request_hash='a'*64,
                    source=original.source,connection={**original.connection,'vision_model_id':'','audio_model_id':'','base_url':base,
                        'vision_model':'fixture-vision','audio_model':'fixture-asr','credential_env':'DNA_UNDERSTANDING_OLD'},
                    state='queued',phase='queued',attempt=1,progress=0,created_at=time.time(),updated_at=time.time())
                db.add(legacy);await db.flush()
                db.add(UnderstandingAttempt(id=str(uuid.uuid4()),job_id=legacy.id,project_id='p',attempt=1,state='queued',phase='queued'));await db.commit()
            monkeypatch.setenv('DNA_UNDERSTANDING_OLD','a-host-token-that-must-not-send')
            await worker.run_job('legacy-queued')
            assert provider.calls==[] and await balance(factory)==(0,0)
            async with factory() as db:
                view=await service.job_view(db,'p','legacy-queued')
                assert view['state']=='failed' and '没有项目费用授权' in view['reason']
                preview=await service.retry_view(db,'p','legacy-queued',worker)
                assert not preview['available']
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_actual_silent_source_reserves_and_sends_only_vision_with_transcription_enabled(tmp_path,monkeypatch):
    """The actual decoded absence of audio removes the ASR unit even while project transcription remains selected."""
    engine,factory,analysis=await fixture(tmp_path,monkeypatch,audio=False)
    provider=ModelFixture()
    try:
        with provider.server() as base:
            worker,identifier,intent=await configured(factory,analysis,base,transcribe=True)
            assert set(intent.expected_price_fingerprints)=={'video_vision'} and await balance(factory)==(11,0)
            await worker.run_job(identifier)
            assert len(provider.calls)==1 and provider.calls[0]['path']=='/v1/chat/completions'
            assert [unit.kind for unit in await calls(factory,identifier)]==['video_vision']
            assert await balance(factory)==(11,0)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['budget_short','no_price','missing_asr_quote','stale_fingerprint','partial_confirmation','wrong_protocol','changed_name','untrusted_local_port'])
async def test_invalid_new_complete_plan_rolls_back_business_and_financial_admission(tmp_path,monkeypatch,fault):
    """Every invalid two-stage admission fails before any new job, attempt, hold or supplier HTTP can exist."""
    engine,factory,analysis=await fixture(tmp_path,monkeypatch)
    provider=ModelFixture()
    try:
        with provider.server() as base:
            worker,identifier,intent=await configured(factory,analysis,base)
            await service.cancel_job(factory,'p',identifier,1)
            body=intent.model_copy(update={'request_key':'rejected-new-'+fault})
            if fault=='budget_short':
                async with factory() as db:
                    await db.execute(update(ExecutionBudget).where(ExecutionBudget.project_id=='p').values(limit_minor=17));await db.commit()
            elif fault=='no_price':
                worker.price_registry=ModelPriceRegistry()
            elif fault=='missing_asr_quote':
                worker.price_registry=ModelPriceRegistry(ModelPriceConfiguration(enabled=True,
                    rules=[rule for rule in worker.price_registry.configuration.rules if rule.kinds==['video_vision']]))
            elif fault in {'stale_fingerprint','partial_confirmation'}:
                fingerprints=dict(body.expected_price_fingerprints)
                if fault=='stale_fingerprint':fingerprints['video_asr']='0'*64
                else:fingerprints.pop('video_asr')
                body=body.model_copy(update={'expected_price_fingerprints':fingerprints})
            elif fault in {'wrong_protocol','changed_name'}:
                async with factory() as db:
                    values={'params':{}} if fault=='wrong_protocol' else {'name':'new unpriced actual request model'}
                    await db.execute(update(Model).where(Model.id=='fixture-vision').values(**values));await db.commit()
            else:
                worker.trusted_local_ports=frozenset()
            with pytest.raises(HTTPException) as rejected:
                await service.create_job(factory,'p',body,worker)
            assert rejected.value.status_code in {409,503}
            async with factory() as db:
                assert await db.scalar(select(func.count()).select_from(UnderstandingJob))==1
                assert await db.scalar(select(func.count()).select_from(UnderstandingAttempt))==1
                assert await db.scalar(select(func.count()).select_from(UnderstandingAttemptCharge))==1
            assert await balance(factory)==(0,0) and await balance(factory,'other')==(0,0) and provider.calls==[]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_persistent_scanner_repeated_asr_cancellation_allows_another_project_to_write(tmp_path,monkeypatch):
    """Sixteen real in-flight cancellations with financial polling must not strand a physical SQLite writer or stop the scanner."""
    engine,factory,analysis=await fixture(tmp_path,monkeypatch)
    provider=ModelFixture();provider.delay_audio=True
    worker=None;readers=[]
    try:
        with provider.server() as base:
            worker,identifier,intent=await configured(factory,analysis,base)
            async def poll_budget():
                """Exercise actual explicit read snapshots while cancellation and writer cleanup race."""
                for _ in range(180):
                    async with factory() as db:
                        await billing_summary(db,'p');await asyncio.sleep(.01)
                    await asyncio.sleep(.02)
            readers=[asyncio.create_task(poll_budget()) for _ in range(2)]
            await worker.start()
            revision=0
            for index in range(16):
                if index:
                    provider.audio_received.clear();provider.release_audio.clear()
                    identifier=await service.create_job(factory,'p',intent.model_copy(update={'request_key':'scanner-cancel-'+str(index)}),worker)
                assert await asyncio.to_thread(provider.audio_received.wait,8)
                await service.cancel_job(factory,'p',identifier,1)
                provider.release_audio.set()
                await asyncio.sleep(.03 if index%2 else .3)
                saved=await asyncio.wait_for(service.save_connection(factory,'other',UnderstandingConnectionWrite(
                    expected_revision=revision,enabled=True,vision_model_id='fixture-vision',audio_model_id='fixture-asr',timeout_seconds=120+index),worker),8)
                revision=saved['revision']
                async with factory() as db:
                    async with transaction(db):await db.execute(text('SELECT 1'))
            await asyncio.gather(*readers)
            assert len(provider.calls)==16 and all(call['path'].endswith('/audio/transcriptions') for call in provider.calls)
            assert await balance(factory)==(112,0) and await balance(factory,'other')==(0,0)
            assert worker.task is not None and not worker.task.done()
    finally:
        provider.release_audio.set()
        for reader in readers:
            if not reader.done():reader.cancel()
        await asyncio.gather(*readers,return_exceptions=True)
        if worker is not None:await asyncio.wait_for(worker.stop(),8)
        await engine.dispose()


@pytest.mark.asyncio
async def test_repeated_owner_cancellation_joins_actual_response_writer_and_retains_original_charge(tmp_path,monkeypatch):
    """Two shutdown cancellations cannot interrupt an actual ASR response's commit/close or strand the next project's writer."""
    from app.services.video_understanding import runtime
    engine,factory,analysis=await fixture(tmp_path,monkeypatch)
    provider=ModelFixture();entered=asyncio.Event();release=asyncio.Event()
    owner=None
    original=runtime.record_accepted_call
    async def pause_after_response(db,**kwargs):
        """Hold only this test's already written response inside its real transaction to make repeated cancellation deterministic."""
        await original(db,**kwargs)
        entered.set();await release.wait()
    monkeypatch.setattr(runtime,'record_accepted_call',pause_after_response)
    try:
        with provider.server() as base:
            worker,identifier,_intent=await configured(factory,analysis,base)
            owner=asyncio.create_task(worker.run_job(identifier))
            await asyncio.wait_for(entered.wait(),8)
            owner.cancel();await asyncio.sleep(.02);owner.cancel();await asyncio.sleep(.02)
            assert not owner.done()
            release.set()
            await asyncio.wait_for(asyncio.gather(owner,return_exceptions=True),8)
            await asyncio.wait_for(service.save_connection(factory,'other',UnderstandingConnectionWrite(expected_revision=0),worker),3)
            units={unit.kind:unit for unit in await calls(factory,identifier)}
            assert len(provider.calls)==1 and units['video_asr'].provider_request_id=='authored-understanding-1'
            assert units['video_asr'].dispatch_state=='accepted' and units['video_vision'].dispatch_state=='not_sent'
            assert await balance(factory)==(7,0)
            async with factory() as db:
                assert (await db.get(UnderstandingJob,identifier)).state=='failed'
    finally:
        release.set()
        if owner is not None and not owner.done():
            owner.cancel();await asyncio.gather(owner,return_exceptions=True)
        await engine.dispose()


@pytest.mark.asyncio
async def test_cancel_during_real_claim_commit_closes_immediately_without_waiting_for_lease_expiry(tmp_path,monkeypatch):
    """A claim that commits after owner cancellation must immediately fail/release the unsent plan and allow another project to write."""
    from app.services.video_understanding.runtime import recover_interrupted
    engine,factory,analysis=await fixture(tmp_path,monkeypatch)
    provider=ModelFixture();entered=asyncio.Event();release=asyncio.Event()
    owner=None
    try:
        with provider.server() as base:
            worker,identifier,_intent=await configured(factory,analysis,base)
            commit=factory.class_.commit
            async def pause_claim_commit(db):
                """Pause only the real running claim's commit, after its lease/attempt writes already exist in the transaction."""
                if not entered.is_set() and any(isinstance(row,UnderstandingJob) and row.id==identifier and row.state=='running' for row in db.identity_map.values()):
                    entered.set();await release.wait()
                return await commit(db)
            monkeypatch.setattr(factory.class_,'commit',pause_claim_commit)
            owner=asyncio.create_task(worker.run_job(identifier))
            await asyncio.wait_for(entered.wait(),8)
            owner.cancel();await asyncio.sleep(.02);owner.cancel()
            assert not owner.done() and provider.calls==[]
            release.set()
            await asyncio.wait_for(asyncio.gather(owner,return_exceptions=True),8)
            async with factory() as db:
                job=await db.get(UnderstandingJob,identifier)
                assert job.state=='failed' and job.lease_token is None
                attempt=await db.scalar(select(UnderstandingAttempt).where(UnderstandingAttempt.job_id==identifier))
                assert attempt.state=='failed' and attempt.finished_at is not None
            assert await balance(factory)==(0,0) and provider.calls==[]
            assert all(unit.dispatch_state=='not_sent' for unit in await calls(factory,identifier))
            assert await recover_interrupted(factory)==0
            await asyncio.wait_for(service.save_connection(factory,'other',UnderstandingConnectionWrite(expected_revision=0),worker),3)
    finally:
        release.set()
        if owner is not None and not owner.done():
            owner.cancel();await asyncio.gather(owner,return_exceptions=True)
        await engine.dispose()
