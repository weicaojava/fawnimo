from __future__ import annotations

from fastapi import HTTPException
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import Base
from app.models.llm import LogLevel, Model, ModelCategoryKey, ModelSettings, Provider
from app.schemas.llm import ModelCreate, ModelSettingsUpdate, ModelUpdate, ProviderCreate
from app.services.llm.manage import (
    create_model,
    create_provider,
    get_image_generation_options,
    get_or_create_settings,
    list_models_paginated,
    update_model,
    update_model_settings,
)


async def _build_session() -> tuple[AsyncSession, object]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    session_local = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return session_local(), engine


@pytest.mark.asyncio
async def test_create_model_persists_with_non_default_flag() -> None:
    db, engine = await _build_session()
    async with db:
        await create_provider(
            db,
            body=ProviderCreate(
                id="p1",
                name="OpenAI",
                base_url="https://api.openai.com/v1",
                api_key="k",
            ),
        )
        created = await create_model(
            db,
            body=ModelCreate(
                id="m1",
                name="gpt-4o-mini",
                category=ModelCategoryKey.text,
                provider_id="p1",
            ),
        )
        assert created.id == "m1"
    await engine.dispose()


@pytest.mark.asyncio
async def test_update_model_allows_regular_field_updates() -> None:
    db, engine = await _build_session()
    async with db:
        provider = Provider(id="p1", name="OpenAI", base_url="https://api.openai.com/v1", api_key="k")
        db.add(provider)
        db.add(
            Model(
                id="m_text",
                name="gpt-4o-mini",
                category=ModelCategoryKey.text,
                provider_id="p1",
            )
        )
        await db.commit()

        updated = await update_model(
            db,
            model_id="m_text",
            body=ModelUpdate(description="updated"),
        )
        assert updated.description == "updated"
    await engine.dispose()


@pytest.mark.asyncio
async def test_get_or_create_settings_behaves_like_singleton() -> None:
    db, engine = await _build_session()
    async with db:
        first = await get_or_create_settings(db)
        second = await get_or_create_settings(db)

        rows = (await db.execute(select(ModelSettings))).scalars().all()

        assert first.id == 1
        assert second.id == 1
        assert len(rows) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_update_model_settings_persists_latest_values() -> None:
    db, engine = await _build_session()
    async with db:
        updated = await update_model_settings(
            db,
            body=ModelSettingsUpdate(api_timeout=45, log_level=LogLevel.debug),
        )

        stored = await db.get(ModelSettings, 1)
        assert updated.id == 1
        assert updated.api_timeout == 45
        assert updated.log_level == LogLevel.debug
        assert stored is not None and stored.api_timeout == 45
    await engine.dispose()


@pytest.mark.asyncio
async def test_list_models_paginated_returns_filtered_items() -> None:
    db, engine = await _build_session()
    async with db:
        provider = Provider(id="p1", name="OpenAI", base_url="https://api.openai.com/v1", api_key="k")
        db.add(provider)
        db.add_all(
            [
                Model(id="m1", name="gpt-4o-mini", category=ModelCategoryKey.text, provider_id="p1"),
                Model(id="m2", name="seedream", category=ModelCategoryKey.image, provider_id="p1"),
            ]
        )
        await db.commit()

        resp = await list_models_paginated(
            db,
            provider_id="p1",
            category=ModelCategoryKey.image,
            q="seed",
            order="created_at",
            is_desc=False,
            page=1,
            page_size=10,
            allow_fields={"created_at", "name"},
        )

        assert resp.data is not None
        assert resp.data.pagination.total == 1
        assert [item.id for item in resp.data.items] == ["m2"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_model_rejects_unsupported_category_for_provider() -> None:
    """Reject the still-unsupported image category while explicit Wan video metadata is supported."""
    db, engine = await _build_session()
    async with db:
        await create_provider(
            db,
            body=ProviderCreate(
                id="p-bailian",
                name="阿里百炼",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                api_key="k",
            ),
        )

        with pytest.raises(HTTPException) as exc_info:
            await create_model(
                db,
                body=ModelCreate(
                    id="m-image-invalid",
                    name="authored-unsupported-image",
                    category=ModelCategoryKey.image,
                    provider_id="p-bailian",
                ),
            )
        assert exc_info.value.status_code == 400
        assert "does not support category=image" in str(exc_info.value.detail)
    await engine.dispose()


@pytest.mark.asyncio
async def test_update_model_rejects_switch_to_unsupported_provider_category_combo() -> None:
    """Changing an image model to a text/video-only provider remains invalid and performs no generation."""
    db, engine = await _build_session()
    async with db:
        await create_provider(
            db,
            body=ProviderCreate(
                id="p-openai",
                name="OpenAI",
                base_url="https://api.openai.com/v1",
                api_key="k",
            ),
        )
        await create_provider(
            db,
            body=ProviderCreate(
                id="p-bailian",
                name="阿里百炼",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                api_key="k",
            ),
        )
        await create_model(
            db,
            body=ModelCreate(
                id="m-image-ok",
                name="authored-image",
                category=ModelCategoryKey.image,
                provider_id="p-openai",
            ),
        )

        with pytest.raises(HTTPException) as exc_info:
            await update_model(
                db,
                model_id="m-image-ok",
                body=ModelUpdate(provider_id="p-bailian"),
            )
        assert exc_info.value.status_code == 400
        assert "does not support category=image" in str(exc_info.value.detail)
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol',['wan30_text_v1','wan30_reference_v1'])
async def test_bailian_stores_explicit_wan_video_without_model_io(protocol,monkeypatch):
    """Supported video metadata can be saved without invoking a provider; image remains an unsupported category."""
    import httpx
    def forbidden_client(*_args,**_kwargs):
        """Model setup cannot test or charge a real supplier implicitly."""
        raise AssertionError('Saving a model must not construct an HTTP client')
    monkeypatch.setattr(httpx,'AsyncClient',forbidden_client)
    db,engine=await _build_session()
    try:
        async with db:
            await create_provider(db,body=ProviderCreate(id='authored-wan-provider',name='阿里百炼',
                base_url='https://authored-space.cn-beijing.maas.aliyuncs.com/api/v1',api_key='AUTHORED_SETUP_KEY'))
            await create_model(db,body=ModelCreate(id='authored-wan-model',name='wan3.0-video',category=ModelCategoryKey.video,
                provider_id='authored-wan-provider',params={'native_video_protocol':protocol}))
            row=await db.get(Model,'authored-wan-model')
            assert row.name=='wan3.0-video' and row.params['native_video_protocol']==protocol
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_get_image_generation_options_uses_default_image_model_capability() -> None:
    db, engine = await _build_session()
    async with db:
        await create_provider(
            db,
            body=ProviderCreate(
                id="p-volc",
                name="火山引擎",
                base_url="https://ark.cn-beijing.volces.com/api/v3",
                api_key="k",
            ),
        )
        await create_model(
            db,
            body=ModelCreate(
                id="m-image-default",
                name="seedream-4.0",
                category=ModelCategoryKey.image,
                provider_id="p-volc",
            ),
        )
        await update_model_settings(
            db,
            body=ModelSettingsUpdate(default_image_model_id="m-image-default"),
        )

        options = await get_image_generation_options(db)

        assert options.provider == "volcengine"
        assert options.model_id == "m-image-default"
        assert options.default_resolution_profile == "standard"
        assert options.ratio_size_profiles["9:16"]["standard"] == "1600x2848"
        assert options.ratio_size_profiles["21:9"]["high"] == "4704x2016"
    await engine.dispose()
