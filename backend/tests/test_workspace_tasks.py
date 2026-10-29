"""Isolated read-only SQL/API task-center checks; metadata fixtures are not model execution evidence."""
from datetime import datetime, timedelta, timezone
import json
import time
import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.api.workspace_tasks import router, get_workspace_task_factory
from app.core.db import Base
from app.models.agent_config import AgentConfig
from app.models.agent_execution import AgentRun
from app.models.asset_image_generation import AssetImageJob
from app.models.llm import Model, Provider
from app.models.production import ProductionMedia
from app.models.reverse import ReverseAnalysis
from app.models.studio import Actor, FileItem, Project
from app.models.video_understanding import UnderstandingAttempt, UnderstandingJob
from app.services.workspace_tasks import recent_tasks

SENSITIVE = 'NEVER_INCLUDE_NODE_PROMPT_OUTPUT_SECRET_OR_MODEL_ENDPOINT'


async def seed_summaries(factory):
    """Seed only isolated stored job metadata and valid navigation targets; never start a provider worker."""
    now = datetime.now(timezone.utc)
    async with factory() as db:
        db.add(Provider(id='summary-provider', name='Summary fixture provider', base_url='https://never-call.invalid/' + SENSITIVE, api_key=SENSITIVE, status='disabled'))
        db.add(AgentConfig(id='summary-agent', name='Summary fixture Agent', type='other', prompt=SENSITIVE, workflow=[{'id': 'start', 'type': 'start', 'label': '开始'}, {'id': 'llm', 'type': 'llm', 'label': '测试节点', 'prompt': SENSITIVE}, {'id': 'end', 'type': 'end', 'label': '结束'}]))
        db.add(Actor(id='summary-actor', name='Summary fixture actor', style='真人都市', visual_style='现实'))
        await db.flush()
        db.add(Model(id='summary-model', name='Summary fixture model', category='text', provider_id='summary-provider', params={'private': SENSITIVE}))
        db.add(Project(id='summary-project', name='Summary fixture project', style='真人都市', visual_style='现实'))
        db.add(FileItem(id='summary-media', name='Private source file', type='video', storage_key='never-read/' + SENSITIVE))
        await db.flush()
        db.add(ProductionMedia(id='summary-media', project_id='summary-project', name='Private fixture source', mime='video/mp4', size=1, sha256='0' * 64, relative_path='never-read/' + SENSITIVE))
        await db.flush()
        db.add(ReverseAnalysis(id='summary-analysis', project_id='summary-project', media_id='summary-media', request_key='summary-analysis', source_hash='0' * 64, state='ready', manifest={'private': SENSITIVE}))
        await db.flush()
        common = {'scope_key': 'summary-fixture', 'request_hash': '0' * 64, 'agent_version': 1, 'config_snapshot': {'private': SENSITIVE}, 'agent_name': 'No private name is needed', 'model_name': 'No private model metadata is needed', 'model_binding': {'secret': SENSITIVE}, 'input_text': SENSITIVE, 'output_text': SENSITIVE, 'error': SENSITIVE, 'max_tokens': 8, 'created_at': now, 'started_at': now - timedelta(seconds=2)}
        db.add(AgentRun(id='same-id', kind='workflow', request_key='agent-summary', agent_id='summary-agent', model_id='summary-model', status='running', **common))
        db.add(AgentRun(id='model-test', kind='connection_test', request_key='model-summary', agent_id=None, model_id='summary-model', status='queued', **{**common, 'started_at': None}))
        db.add(AgentRun(id='deleted-agent', kind='workflow', request_key='deleted-agent-summary', agent_id='missing-agent', model_id='summary-model', status='failed', finished_at=now, **common))
        db.add(AgentRun(id='deleted-model', kind='connection_test', request_key='deleted-model-summary', agent_id=None, model_id='missing-model', status='failed', finished_at=now, **common))
        db.add(UnderstandingJob(id='same-id', project_id='summary-project', analysis_id='summary-analysis', request_key='understanding-summary', request_hash='0' * 64, source={'private': SENSITIVE}, connection={'base_url': SENSITIVE}, state='running', phase='understanding', attempt=1, progress=65, document={'private': SENSITIVE}, created_at=now.timestamp(), updated_at=now.timestamp()))
        await db.flush()
        db.add(UnderstandingAttempt(id='summary-attempt', job_id='same-id', project_id='summary-project', attempt=1, state='running', phase='understanding', started_at=now.timestamp() - 1, input_receipt={'private': SENSITIVE}))
        image = {'scope_key': 'summary-image-fixture', 'request_hash': '0' * 64, 'asset_kind': 'actor', 'slot_id': 1, 'model_id': 'summary-model', 'model_name': 'Private fixture model', 'model_binding': {'secret': SENSITIVE}, 'prompt': SENSITIVE, 'size': '256x256', 'error': SENSITIVE, 'created_at': now, 'finished_at': now}
        db.add(AssetImageJob(id='same-id', request_key='image-summary', asset_id='summary-actor', status='candidate', **image))
        db.add(AssetImageJob(id='deleted-image', request_key='deleted-image-summary', asset_id='missing-actor', status='adopted', **image))
        await db.commit()


async def fixture(tmp_path):
    """Create an independent database with all real models and no executor or credential configuration."""
    engine = create_async_engine('sqlite+aiosqlite:///' + (tmp_path / 'workspace-tasks.db').as_posix())
    @event.listens_for(engine.sync_engine, 'connect')
    def constraints(connection, _record):
        """Keep fixture ownership checks identical to the local runtime."""
        connection.execute('PRAGMA foreign_keys=ON')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    await seed_summaries(factory)
    return engine, factory


@pytest.mark.asyncio
async def test_actual_get_is_metadata_only_namespaced_and_read_only(tmp_path):
    """Prove the endpoint selects only allowed columns and no execution data enters its JSON response."""
    engine, factory = await fixture(tmp_path)
    statements = []
    @event.listens_for(engine.sync_engine, 'before_cursor_execute')
    def queries(_connection, _cursor, statement, _parameters, _context, _many):
        """Capture SQL structure only, never bind values containing fixture private fields."""
        statements.append(statement)
    app = FastAPI(); app.include_router(router); app.dependency_overrides[get_workspace_task_factory] = lambda: factory
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://local') as client:
            response = await client.get('/workspace-tasks/recent')
            assert response.status_code == 200, response.text
            value = response.json()['data']
            assert value['complete'] and len(value['items']) == 7
            assert len({item['id'] for item in value['items']}) == 7
            assert {item['kind'] for item in value['items']} == {'agent_workflow', 'model_connection_test', 'video_understanding', 'asset_image_generation'}
            assert SENSITIVE not in response.text and 'https://' not in response.text
            for item in value['items']:
                assert set(item) == {'id', 'kind', 'title', 'status', 'progress', 'created_at_ts', 'started_at_ts', 'finished_at_ts', 'return_path', 'return_note'}
                assert item['progress'] == (65 if item['kind'] == 'video_understanding' else None)
            assert all(statement.lstrip().upper().startswith('SELECT') for statement in statements)
            joined = '\n'.join(statements).lower()
            for forbidden in ('input_text', 'output_text', 'prompt', 'error', 'model_binding', 'config_snapshot', 'base_url', 'storage_key', 'input_receipt', 'document', 'transcripts', 'generation_tasks'):
                assert forbidden not in joined, forbidden
            assert 'video_understanding_jobs.source' not in joined and 'providers.api_key' not in joined
            assert (await client.get('/workspace-tasks/recent?limit=0')).status_code == 422
            assert (await client.get('/workspace-tasks/recent?limit=201')).status_code == 422
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_deleted_targets_return_existing_lists_and_no_fake_percentage(tmp_path):
    """Retained jobs must not manufacture links to removed Agent/model/asset edit pages."""
    engine, factory = await fixture(tmp_path)
    try:
        value = await recent_tasks(factory)
        items = {item['id'].split(':')[-1]: item for item in value['items'] if item['id'].split(':')[-1].startswith('deleted-')}
        assert items['deleted-agent']['return_path'] == '/agents'
        assert items['deleted-model']['return_path'] == '/models?tab=models'
        assert items['deleted-image']['return_path'] == '/assets'
        assert all('历史' in item['return_note'] and item['progress'] is None for item in items.values())
        live = next(item for item in value['items'] if item['id'] == 'workspace:agent_workflow:same-id')
        assert live['return_path'] == '/agents/summary-agent/edit?agentRun=same-id#agent-execution'
        connection = next(item for item in value['items'] if item['id'] == 'workspace:model_connection_test:model-test')
        assert 'connectionModel=summary-model' in connection['return_path'] and connection['started_at_ts'] is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_limits_keep_active_jobs_and_failed_source_is_explicit(tmp_path):
    """Bounded recent lists prioritize active work; a missing source remains a visible partial read."""
    engine, factory = await fixture(tmp_path)
    try:
        limited = await recent_tasks(factory, 1)
        assert len(limited['items']) == 1 and limited['items'][0]['status'] == 'running'
        assert limited['per_source_limit'] == 1 and limited['limit'] == 1
        async with engine.begin() as connection:
            await connection.execute(text('DROP TABLE asset_image_generation_jobs'))
        partial = await recent_tasks(factory)
        assert not partial['complete'] and len(partial['items']) == 5
        unavailable = next(source for source in partial['sources'] if source['kind'] == 'asset_image_generation')
        assert not unavailable['available'] and '上次记录' in unavailable['message']
        assert 'sqlite' not in json.dumps(partial).lower() and 'workspace-tasks.db' not in json.dumps(partial)
    finally:
        await engine.dispose()
