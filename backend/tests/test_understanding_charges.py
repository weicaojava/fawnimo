"""Original-project vision/ASR financial plans over real authored source metadata, without model HTTP."""
import time

from fastapi import HTTPException
import pytest
from sqlalchemy import select,text,update
from sqlalchemy.exc import IntegrityError

from app.core.contracts.model_accounting import ModelPriceEnvelope,QuotedModelCall
from app.core.contracts.video_understanding import UnderstandingConnection
from app.models.execution_budget import ExecutionBudget
from app.models.llm import Model,Provider
from app.models.reverse import ReverseAnalysis
from app.models.understanding_charge import UnderstandingAttemptCharge
from app.models.video_understanding import UnderstandingAttempt,UnderstandingJob
from app.services.llm.connection_versions import migrate_connection_versions
from app.services.video_understanding.charge_schema import migrate_understanding_charges
from app.services.video_understanding.charges import attempt_charge,bind_attempt,release_unsent
from app.services.video_understanding.model_connections import safe_binding
from app.services.video_understanding.source import source_snapshot
from app.services.worker.execution_budget import configure,transaction
from app.services.worker.model_charge_schema import migrate_model_charges
from app.services.worker.model_job_charges import binding_digest
from app.services.worker.model_charges import prepare_call,reserve_in_transaction
from tests.test_video_understanding import fixture as source_fixture


async def setup(tmp_path,monkeypatch,limit=100):
    """Build new source/project tables, explicit protocol models and synthetic prices without starting a model worker."""
    engine,factory,analysis=await source_fixture(tmp_path,monkeypatch)
    await migrate_connection_versions(engine);await migrate_model_charges(engine);await migrate_understanding_charges(engine)
    async with factory() as db:
        db.add(Provider(id='understanding-provider',name='openai',base_url='https://example.invalid/v1',api_key='authored-key',status='testing'))
        await db.flush()
        db.add_all([Model(id='vision',name='Authored vision',category='text',provider_id='understanding-provider',params={'understanding_vision_protocol':'openai_chat_image_v1'}),
            Model(id='asr',name='Authored ASR',category='audio',provider_id='understanding-provider',params={'understanding_asr_protocol':'openai_verbose_segments_v1'})])
        await db.commit()
    for project in ('p','other'):
        async with factory() as db:await configure(db,project,'CNY',limit)
    quotes={kind:ModelPriceEnvelope(model_id=model,model_name='Authored vision' if model=='vision' else 'Authored ASR',provider_id='understanding-provider',provider_connection_version=1,
        account_version='authored-understanding-account',max_call_minor=amount,source_note='Authored integer software test quote, not a supplier price')
        for kind,model,amount in [('video_vision','vision',11),('video_asr','asr',7)]}
    return engine,factory,analysis,quotes


async def save_job(db,analysis):
    """Save one real analysis-owned queued business attempt before asking for its financial batch."""
    source=source_snapshot(await db.get(ReverseAnalysis,analysis))
    connection=UnderstandingConnection(enabled=True,vision_model_id='vision',audio_model_id='asr').model_dump()
    job=UnderstandingJob(id='authored-job',project_id='p',analysis_id=analysis,request_key='job-once',request_hash='a'*64,
        source=source,connection=connection,state='queued',phase='queued',attempt=1,progress=0,created_at=time.time(),updated_at=time.time())
    db.add(job);await db.flush()
    db.add(UnderstandingAttempt(id='attempt-one',job_id=job.id,project_id='p',attempt=1,state='queued',phase='queued'));await db.flush()
    bindings={kind:await safe_binding(db,model,kind) for kind,model in [('video_vision','vision'),('video_asr','asr')]}
    return job,bindings


async def balance(factory,project='p'):
    """Read actual independent counters outside the admission transaction."""
    async with factory() as db:
        row=await db.get(ExecutionBudget,project)
        return row.reserved_minor,row.spent_minor


@pytest.mark.asyncio
async def test_complete_understanding_plan_replays_once_and_releases_only_unsent_units(tmp_path,monkeypatch):
    """Both stages reserve together, and an already prepared ASR unit stays held after the unused vision unit releases."""
    engine,factory,analysis,quotes=await setup(tmp_path,monkeypatch)
    try:
        async with factory() as db:
            async with transaction(db):
                job,bindings=await save_job(db,analysis)
                first=await bind_attempt(db,job,submitted_by='local_operator',request_key='intent-once',request_hash='b'*64,bindings=bindings,quotes=quotes)
                again=await bind_attempt(db,job,submitted_by='local_operator',request_key='intent-once',request_hash='b'*64,bindings=bindings,quotes=quotes)
                assert first.batch_id==again.batch_id
        assert await balance(factory)==(18,0) and await balance(factory,'other')==(0,0)
        async with factory() as db:
            async with transaction(db):
                link=await attempt_charge(db,job.id,1,'p',required=True)
                from app.models.model_charge import ModelCallCharge
                charge=await db.scalar(select(ModelCallCharge).where(ModelCallCharge.batch_id==link.batch_id,ModelCallCharge.unit_key=='asr'))
                await prepare_call(db,project_id='p',charge_id=charge.id,binding_hash=binding_digest(bindings['video_asr']))
                await release_unsent(db,job.id,1,'p')
        assert await balance(factory)==(7,0)
        for statement in ('UPDATE understanding_attempt_charges SET project_id=\'other\'','DELETE FROM understanding_attempt_charges',
            'INSERT OR REPLACE INTO understanding_attempt_charges SELECT * FROM understanding_attempt_charges'):
            async with factory() as db:
                with pytest.raises(IntegrityError):
                    async with transaction(db):await db.execute(text(statement))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_insufficient_full_plan_rolls_back_the_actual_job_and_attempt(tmp_path,monkeypatch):
    """Seventeen minor units cannot fund an eighteen-unit two-stage plan or borrow from a sibling project."""
    engine,factory,analysis,quotes=await setup(tmp_path,monkeypatch,limit=17)
    try:
        async with factory() as db:
            with pytest.raises(HTTPException):
                async with transaction(db):
                    job,bindings=await save_job(db,analysis)
                    await bind_attempt(db,job,submitted_by='local_operator',request_key='intent-once',request_hash='b'*64,bindings=bindings,quotes=quotes)
        async with factory() as db:assert await db.get(UnderstandingJob,'authored-job') is None
        assert await balance(factory)==(0,0) and await balance(factory,'other')==(0,0)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_partial_native_plan_cannot_link_to_audio_bearing_understanding_attempt(tmp_path,monkeypatch):
    """The DB guard rejects an incorrectly maintained vision-only batch before either possible model request."""
    engine,factory,analysis,quotes=await setup(tmp_path,monkeypatch)
    try:
        async with factory() as db:
            with pytest.raises(IntegrityError):
                async with transaction(db):
                    job,bindings=await save_job(db,analysis)
                    batch=await reserve_in_transaction(db,project_id='p',scope_key='model-job:understanding:authored-job:1',submitted_by='local_operator',
                        calls=[QuotedModelCall(unit_key='vision',kind='video_vision',binding_hash=binding_digest(bindings['video_vision']),quote=quotes['video_vision'])])
                    db.add(UnderstandingAttemptCharge(job_id=job.id,attempt=1,project_id='p',batch_id=batch,submitted_by='local_operator',
                        request_key='wrong-incomplete-plan',request_hash='b'*64,job_request_hash=job.request_hash,bindings=bindings));await db.flush()
        assert await balance(factory)==(0,0)
    finally:
        await engine.dispose()
