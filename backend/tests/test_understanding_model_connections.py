"""Managed vision/ASR selection is read-only, explicitly opted in and distinct from video generation."""
from fastapi import HTTPException
import pytest
from sqlalchemy import update

from app.core.contracts.model_accounting import ModelPriceConfiguration,ModelPriceRule
from app.models.llm import Model,ModelCategoryKey,ModelSettings,Provider
from app.schemas.llm import ModelCreate
from app.services.llm.manage import create_model
from app.services.llm.resolver import _settings_model_id,get_default_model_by_category
from app.services.llm.runtime import _default_model_id
from app.services.video_understanding.model_connections import choices,material,safe_binding
from app.services.worker.model_prices import ModelPriceRegistry
from tests.test_model_charges import calls,fixture


@pytest.mark.asyncio
async def test_audio_model_is_saved_explicitly_and_never_resolves_a_video_default(tmp_path):
    """The new input modality works through model management, with no implicit video or text fallback."""
    engine,factory=await fixture(tmp_path)
    try:
        async with factory() as db:
            audio=await create_model(db,body=ModelCreate(id='asr',name='Authored ASR',category='audio',provider_id='p',
                params={'understanding_asr_protocol':'openai_verbose_segments_v1'}))
            assert audio.category=='audio'
            db.add(Model(id='video',name='Generation only',category='video',provider_id='p',params={}))
            await db.flush();db.add(ModelSettings(id=1,default_video_model_id='video'));await db.commit()
        async with factory() as db:
            setting=await db.get(ModelSettings,1)
            assert _settings_model_id(setting,ModelCategoryKey.audio) is None
            assert _default_model_id(setting,ModelCategoryKey.audio) is None
            with pytest.raises(HTTPException,match='No default model configured'):
                await get_default_model_by_category(db,ModelCategoryKey.audio)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_only_opted_in_protocol_models_expose_independent_authoritative_quotes(tmp_path):
    """A generation model or a key alone never supplies vision/ASR eligibility or a price."""
    engine,factory=await fixture(tmp_path)
    try:
        async with factory() as db:
            assert await choices(db)==[]
            await db.execute(update(Model).where(Model.id=='m').values(params={'understanding_vision_protocol':'openai_chat_image_v1'}))
            db.add_all([Model(id='asr',name='Authored ASR',category='audio',provider_id='p',
                params={'understanding_asr_protocol':'openai_verbose_segments_v1'}),
                Model(id='video',name='Video generation is not understanding',category='video',provider_id='p',
                    params={'understanding_vision_protocol':'openai_chat_image_v1'})])
            await db.commit()
        vision=calls(count=1,amount=11)[0].quote
        audio=vision.model_copy(update={'model_id':'asr','model_name':'Authored ASR','max_call_minor':7})
        prices=ModelPriceRegistry(ModelPriceConfiguration(enabled=True,rules=[
            ModelPriceRule(quote=vision,kinds=['video_vision']),ModelPriceRule(quote=audio,kinds=['video_asr'])]))
        async with factory() as db:
            ready={row.kind:row for row in await choices(db,prices)}
            assert set(ready)=={'video_vision','video_asr'}
            assert ready['video_vision'].max_call_minor==11 and ready['video_asr'].max_call_minor==7
            assert ready['video_asr'].price_fingerprint==audio.fingerprint and all(row.configured and row.priced for row in ready.values())
            assert 'synthetic-key' not in str([row.model_dump() for row in ready.values()])
            unpriced=await choices(db)
            assert len(unpriced)==2 and all(row.configured and not row.priced for row in unpriced)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_managed_capture_retains_original_clock_and_key_while_rotation_invalidates_old_price(tmp_path):
    """One-query material preserves a prepared call's old credential, while later eligibility uses the new connection clock."""
    engine,factory=await fixture(tmp_path)
    try:
        async with factory() as db:
            await db.execute(update(Model).where(Model.id=='m').values(params={'understanding_vision_protocol':'openai_chat_image_v1'}));await db.commit()
        prices=ModelPriceRegistry(ModelPriceConfiguration(enabled=True,rules=[ModelPriceRule(quote=calls(count=1)[0].quote,kinds=['video_vision'])]))
        async with factory() as db:
            read_only=await safe_binding(db,'m','video_vision')
            original,secret=await material(db,'m','video_vision')
            assert read_only==original
        assert original['provider_connection_version']==1 and secret=='synthetic-key' and secret not in str(original)
        async with factory() as db:
            await db.execute(update(Provider).where(Provider.id=='p').values(api_key='different-authored-key'));await db.commit()
        async with factory() as db:
            changed,new_secret=await material(db,'m','video_vision')
            assert changed['provider_connection_version']==2 and new_secret=='different-authored-key'
            assert not (await choices(db,prices))[0].priced
        assert secret=='synthetic-key' and original['provider_connection_version']==1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_wrong_input_category_or_missing_opt_in_rejects_material_before_any_transport(tmp_path):
    """A model name or video protocol flag cannot be used as proof that the model accepts image or audio input."""
    engine,factory=await fixture(tmp_path)
    try:
        async with factory() as db:
            for kind in ('video_vision','video_asr'):
                with pytest.raises(HTTPException):await material(db,'m',kind)
            await db.execute(update(Model).where(Model.id=='m').values(params={'understanding_asr_protocol':'openai_verbose_segments_v1'}));await db.commit()
        async with factory() as db:
            with pytest.raises(HTTPException):await material(db,'m','video_asr')
    finally:
        await engine.dispose()
