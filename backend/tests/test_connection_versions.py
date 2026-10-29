"""Coherent connection clocks and actual local HTTP no-redirect checks; no external supplier calls."""
import json

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.db import Base
from app.models.llm import Model, Provider
from app.models.provider_connection_version import ProviderConnectionVersion
from app.services.agent_execution import safe_binding, text_connection_material
from app.services.llm.connection_versions import connection_version, migrate_connection_versions
from tests.test_agent_execution import agent, isolated_client, local_model, submit, terminal
from tests.test_asset_image_generation import fixture_client, model_server, submit as submit_image, terminal as image_terminal


@pytest.mark.asyncio
async def test_legacy_zero_then_mutations_rollback_delete_recreate_and_monotonic_clock(tmp_path):
    """A tombstone survives provider reuse and never stores a credential value or digest."""
    engine=create_async_engine('sqlite+aiosqlite:///'+(tmp_path/'clocks.db').as_posix())
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory=async_sessionmaker(engine,expire_on_commit=False)
    try:
        async with factory() as db:
            db.add(Provider(id='legacy',name='openai',base_url='https://example.invalid/v1',api_key='synthetic-original',status='testing'))
            await db.commit()
        await migrate_connection_versions(engine)
        await migrate_connection_versions(engine)
        async with factory() as db:
            assert await connection_version(db,'legacy')==0
            await db.execute(update(Provider).where(Provider.id=='legacy').values(api_key='synthetic-rotated'))
            assert await connection_version(db,'legacy')==1
            await db.rollback()
            assert await connection_version(db,'legacy')==0
            await db.execute(update(Provider).where(Provider.id=='legacy').values(api_key='synthetic-rotated'))
            await db.commit()
            await db.execute(text('DELETE FROM providers WHERE id=:id'),{'id':'legacy'})
            await db.commit()
            assert await connection_version(db,'legacy')==2
            db.add(Provider(id='legacy',name='openai',base_url='https://example.invalid/v1',api_key='synthetic-new-account',status='testing'))
            await db.commit()
            assert await connection_version(db,'legacy')==3
            assert set(ProviderConnectionVersion.__table__.columns.keys())=={'provider_id','revision'}
            for statement in ["DELETE FROM provider_connection_versions", "UPDATE provider_connection_versions SET revision=1", "UPDATE provider_connection_versions SET provider_id='other',revision=revision+1",
                "INSERT OR REPLACE INTO provider_connection_versions(provider_id,revision) VALUES('legacy',1)",
                "INSERT INTO provider_connection_versions(provider_id,revision) VALUES('legacy',1) ON CONFLICT(provider_id) DO UPDATE SET revision=1"]:
                with pytest.raises(IntegrityError):
                    await db.execute(text(statement))
                await db.rollback()
            assert await connection_version(db,'legacy')==3
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_one_statement_snapshot_refreshes_cached_provider_and_keeps_exact_transient_key(tmp_path):
    """Old cached ORM data cannot pair a new numeric clock with a different account's credential."""
    engine=create_async_engine('sqlite+aiosqlite:///'+(tmp_path/'snapshot.db').as_posix())
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await migrate_connection_versions(engine)
    factory=async_sessionmaker(engine,expire_on_commit=False)
    try:
        async with factory() as db:
            db.add(Provider(id='p',name='openai',base_url='https://example.invalid/v1',api_key='synthetic-first',status='testing'))
            await db.flush()
            db.add(Model(id='m',name='fixture',category='text',provider_id='p',params={}))
            await db.commit()
        async with factory() as stale:
            cached=await stale.get(Provider,'p')
            assert cached.api_key=='synthetic-first'
            await stale.commit()
            async with factory() as editor:
                await editor.execute(update(Provider).where(Provider.id=='p').values(api_key='synthetic-second',base_url='https://second.invalid/v1'))
                await editor.commit()
            binding,transient_key=await text_connection_material(stale,'m')
            assert binding['provider_connection_version']==2 and binding['base_url']=='https://second.invalid/v1'
            assert transient_key=='synthetic-second'
            assert 'synthetic-' not in json.dumps(binding)
            assert cached.api_key=='synthetic-second'
            async with factory() as editor:
                await editor.execute(update(Provider).where(Provider.id=='p').values(api_key='synthetic-third'))
                await editor.commit()
            assert transient_key=='synthetic-second'
            assert (await safe_binding(stale,'m'))['provider_connection_version']==3
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_text_key_rotation_rejects_queued_job_before_any_local_http(tmp_path):
    """A real scanner preserves the old attempt and sends neither the old nor new account a request."""
    with local_model() as fixture:
        async with isolated_client(tmp_path,fixture) as (client,factory,runtimes):
            config=await agent(client,nodes=1)
            await runtimes[0].stop();runtimes[0].running=True
            response=await submit(client,config,key='connection-clock')
            assert response.status_code==201,response.text
            async with factory() as db:
                await db.execute(update(Provider).where(Provider.id=='fixture-provider').values(api_key='synthetic-new-account'))
                await db.commit()
            await runtimes[0].tick()
            result=await terminal(client,response.json()['data']['id'])
            assert result['status']=='failed' and not fixture['calls']
            assert 'synthetic-new-account' not in json.dumps(result)
            assert any(node['status']=='failed' for node in result['nodes'])


@pytest.mark.asyncio
async def test_image_key_rotation_rejects_queued_job_without_candidate_or_http(tmp_path,monkeypatch):
    """The image scanner uses the same captured connection clock before receiving or publishing bytes."""
    with model_server() as fixture:
        async with fixture_client(tmp_path,monkeypatch,fixture) as (client,factory,slots,runtimes):
            await runtimes[0].stop();runtimes[0].running=True
            response=await submit_image(client,'actor',slots['actor'],key='image-clock')
            assert response.status_code==201,response.text
            async with factory() as db:
                await db.execute(update(Provider).where(Provider.id=='image-provider').values(api_key='synthetic-new-account'))
                await db.commit()
            await runtimes[0].tick()
            result=await image_terminal(client,response.json()['data']['id'])
            assert result['status']=='failed' and not fixture['calls']
            assert result['file_id'] is None and result['content_url'] is None
            assert 'synthetic-new-account' not in json.dumps(result)
