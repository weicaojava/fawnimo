"""Explicit managed native-video connection and pricing projections are inert, clock-bound and protocol-specific."""
import json

from fastapi import HTTPException
import pytest
from sqlalchemy import event, update

from app.core.contracts.model_accounting import ModelPriceConfiguration, ModelPriceRule
from app.core.contracts.seedance25 import ARK_BASE_URL, MODEL_ID
from app.models.llm import Model, Provider
from app.services.llm.connection_versions import connection_version
from app.services.worker.model_prices import ModelPriceRegistry
from app.services.worker.native_video_models import PROTOCOL, choices, material, safe_binding
from tests.test_model_charges import calls, fixture


async def setup(tmp_path):
    """Create only synthetic DB metadata and an explicit positive native quote; no provider HTTP is constructed."""
    engine,factory=await fixture(tmp_path)
    async with factory() as db:
        await db.execute(update(Provider).where(Provider.id=='p').values(name='volcengine',base_url=ARK_BASE_URL))
        await db.execute(update(Model).where(Model.id=='m').values(name=MODEL_ID,category='video',params={}))
        await db.commit()
    async with factory() as db:version=await connection_version(db,'p')
    quote=calls(count=1)[0].quote.model_copy(update={'model_name':MODEL_ID,'provider_connection_version':version})
    registry=ModelPriceRegistry(ModelPriceConfiguration(enabled=True,rules=[ModelPriceRule(kinds=['native_video'],quote=quote)]))
    return engine,factory,quote,registry


@pytest.mark.asyncio
async def test_native_catalog_requires_explicit_protocol_and_independent_native_price(tmp_path):
    """A video category, model name or a text quote never silently provides native execution eligibility."""
    engine,factory,quote,registry=await setup(tmp_path)
    try:
        async with factory() as db:
            assert await choices(db,registry)==[]
            await db.execute(update(Model).where(Model.id=='m').values(params={'native_video_protocol':PROTOCOL}));await db.commit()
        text=ModelPriceRegistry(ModelPriceConfiguration(enabled=True,rules=[ModelPriceRule(kinds=['agent_text'],quote=quote)]))
        async with factory() as db:
            unpriced=await choices(db,text)
            assert len(unpriced)==1 and unpriced[0].configured and not unpriced[0].priced
            priced=await choices(db,registry)
            assert len(priced)==1 and priced[0].priced and priced[0].price_fingerprint==quote.fingerprint
            assert priced[0].max_call_minor==25 and 'synthetic-key' not in json.dumps(priced[0].model_dump())
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_native_material_is_one_query_and_rotation_never_changes_captured_key_or_old_quote(tmp_path):
    """Safe views project key presence only, and a coherent private snapshot keeps the old key while later views see the new clock."""
    engine,factory,_quote,registry=await setup(tmp_path)
    queries=[]
    @event.listens_for(engine.sync_engine,'before_cursor_execute')
    def count(_connection,_cursor,statement,_parameters,_context,_many):
        """Count query boundaries without retaining bound credentials or SQL arguments."""
        if statement.lstrip().upper().startswith('SELECT'):queries.append(True)
    try:
        async with factory() as db:
            await db.execute(update(Model).where(Model.id=='m').values(params={'native_video_protocol':PROTOCOL}));await db.commit()
        async with factory() as db:
            queries.clear();safe=await safe_binding(db,'m');assert len(queries)==1
            queries.clear();old,key=await material(db,'m');assert len(queries)==1 and old==safe
            assert key=='synthetic-key' and key not in json.dumps(old)
            async with factory() as other:
                await other.execute(update(Provider).where(Provider.id=='p').values(api_key='rotated-native-fixture-key'));await other.commit()
            queries.clear();fresh=await safe_binding(db,'m');assert len(queries)==1
            assert fresh['provider_connection_version']==old['provider_connection_version']+1
            assert key=='synthetic-key' and not (await choices(db,registry))[0].priced
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['audio_category','wrong_supplier','different_actual_model','asr_flag_only'])
async def test_wrong_native_model_or_protocol_cannot_capture_a_key(tmp_path,fault):
    """Wrong categories, providers, actual request names and transcription-only flags fail before returning private material."""
    engine,factory,_quote,registry=await setup(tmp_path)
    try:
        async with factory() as db:
            values={'params':{'native_video_protocol':PROTOCOL}}
            if fault=='audio_category':values['category']='audio'
            if fault=='different_actual_model':values['name']='another-unpriced-native-model'
            if fault=='asr_flag_only':values['params']={'understanding_asr_protocol':'openai_verbose_segments_v1'}
            await db.execute(update(Model).where(Model.id=='m').values(**values))
            if fault=='wrong_supplier':await db.execute(update(Provider).where(Provider.id=='p').values(name='openai'))
            await db.commit()
        async with factory() as db:
            with pytest.raises(HTTPException) as rejected:await material(db,'m')
            assert rejected.value.status_code==503 and 'synthetic-key' not in str(rejected.value.detail)
            assert await choices(db,registry)==[]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_native_local_exception_cannot_be_inferred_from_a_saved_provider_url(tmp_path):
    """Local model metadata stays inert unless trusted runtime code independently supplies the exact loopback endpoint."""
    engine,factory,_quote,_registry=await setup(tmp_path)
    try:
        async with factory() as db:
            await db.execute(update(Model).where(Model.id=='m').values(params={'native_video_protocol':PROTOCOL}))
            await db.execute(update(Provider).where(Provider.id=='p').values(base_url='http://127.0.0.1:43210/api/v3'));await db.commit()
        async with factory() as db:
            with pytest.raises(HTTPException):await safe_binding(db,'m')
            accepted=await safe_binding(db,'m',trusted_local_host='127.0.0.1',trusted_local_port=43210)
            assert accepted['base_url']=='http://127.0.0.1:43210/api/v3' and 'synthetic-key' not in json.dumps(accepted)
            with pytest.raises(HTTPException):await safe_binding(db,'m',trusted_local_host='127.0.0.1',trusted_local_port=43211)
            with pytest.raises(HTTPException):await safe_binding(db,'m',trusted_local_host='169.254.169.254',trusted_local_port=43210)
    finally:
        await engine.dispose()
