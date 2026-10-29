"""Real loopback HTTP + isolated SQLite acceptance; these fixtures do not qualify any real model."""
import asyncio
from contextlib import asynccontextmanager, contextmanager
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

from fastapi import FastAPI
import httpx
import pytest
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1.routes.agents import router as config_router
from app.api.v1.routes.agent_execution import router
from app.dependencies import get_db
from app.models.agent_config import AgentConfig, AgentConfigRevision
from app.models.agent_execution import AgentNodeRun, AgentRun
from app.models.llm import Model, ModelSettings, Provider
from app.services.llm.connection_versions import migrate_connection_versions
from app.services.agent_execution import AgentExecutionRuntime, clock
from app.services.agent_execution import create_run
from app.core.contracts.agent_execution import AgentRunCreate
from app.core.contracts.model_accounting import ModelPriceConfiguration,ModelPriceEnvelope,ModelPriceRule
from app.core.db import Base
from app.models.studio import Project
from app.services.worker.model_prices import ModelPriceRegistry
from app.services.worker.model_charge_schema import migrate_model_charges
from app.services.worker.model_billing_schema import migrate_model_billing
from app.services.worker.model_job_charge_schema import migrate_model_job_charges
from app.services.worker.execution_budget import configure as configure_budget

FIXTURE_KEY = 'local-fixture-not-a-secret'


def fixture_price():
    """Author a synthetic ten-minor upper bound; it is not an actual supplier price or spend authorization."""
    return ModelPriceEnvelope(model_id='fixture-model',model_name='local-fixture-model',provider_id='fixture-provider',provider_connection_version=1,
        account_version='synthetic-text-account',max_call_minor=10,source_note='Synthetic local transport/accounting fixture only')


def fee_fields():
    """Select the fixture project and confirm exactly the server fixture envelope."""
    return {'project_id':'fixture-project','expected_price_fingerprint':fixture_price().fingerprint}


@contextmanager
def local_model():
    """Listen on an unpredictable loopback port and record request parameters without auth values."""
    state = {'calls': [], 'mode': 'ok', 'received': threading.Event(), 'release': threading.Event()}

    class Handler(BaseHTTPRequestHandler):
        """Return explicit fixture outputs over actual sockets; no remote network is reachable."""
        def log_message(self, *_args):
            """Suppress HTTP access logs so no headers or user text leak to pytest output."""

        def do_POST(self):
            """Implement the exact compatible chat route with controllable failure and latency."""
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
            state['calls'].append({'path': self.path, 'body': body, 'authenticated': self.headers.get('Authorization') == 'Bearer ' + FIXTURE_KEY})
            state['received'].set()
            mode = state['mode']
            if mode == 'block':
                state['release'].wait(5)
            status = 503 if mode == 'fail' else 200
            result = {'error': {'message': 'untrusted upstream echoed ' + FIXTURE_KEY}} if status == 503 else {'choices': [{'message': {'content': f"local-fixture-output-{len(state['calls'])}"}}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 4, 'total_tokens': 14}}
            if mode == 'malformed':
                result = {'unexpected': FIXTURE_KEY}
            data = json.dumps(result).encode()
            try:
                self.send_response(status); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(data)))
                self.send_header('X-Request-Id','fixture-chat-'+str(len(state['calls'])))
                self.end_headers(); self.wfile.write(data)
            except (ConnectionError, OSError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    state['port'] = server.server_port
    try:
        yield state
    finally:
        state['release'].set(); server.shutdown(); server.server_close(); thread.join(timeout=2)


@asynccontextmanager
async def isolated_client(tmp_path, fixture, *, workers=1, configured=True):
    """Use only new local credentials and DB tables, keeping the user's saved accounts untouched."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine('sqlite+aiosqlite:///' + (tmp_path / 'execution.db').as_posix())

    @event.listens_for(engine.sync_engine, 'connect')
    def configure(connection, _):
        """Match supported local foreign-key and contention behavior."""
        connection.execute('PRAGMA foreign_keys=ON'); connection.execute('PRAGMA busy_timeout=5000')

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await migrate_connection_versions(engine)
    await migrate_model_charges(engine)
    await migrate_model_billing(engine)
    await migrate_model_job_charges(engine)
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with sessions() as db:
        db.add_all([Project(id=identity,name='Synthetic '+identity,style='真人都市') for identity in ('fixture-project','other-project')])
        if configured:
            db.add(Provider(id='fixture-provider', name='openai', base_url=f"http://127.0.0.1:{fixture['port']}/v1", api_key=FIXTURE_KEY, api_secret='', status='testing', created_by='fixture'))
            await db.flush()
            db.add(Model(id='fixture-model', name='local-fixture-model', category='text', provider_id='fixture-provider', params={'frequency_penalty': .2}, created_by='fixture'))
            await db.flush()
            db.add(ModelSettings(id=1, default_text_model_id='fixture-model', api_timeout=3))
        await db.commit()
    for project in ('fixture-project','other-project'):
        async with sessions() as db:
            await configure_budget(db,project,'CNY',1000)

    async def database():
        """Commit successful requests before workers observe admission and rollback errors."""
        async with sessions() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    prices=ModelPriceRegistry(ModelPriceConfiguration(enabled=True,rules=[ModelPriceRule(quote=fixture_price(),kinds=['agent_text','model_connection_test'])]))
    runtimes = [AgentExecutionRuntime(sessions, poll_interval=.02, trusted_local_host='127.0.0.1', trusted_local_ports=frozenset({fixture['port']}),price_registry=prices) for _ in range(workers)]
    app = FastAPI(); app.include_router(config_router, prefix='/api/v1'); app.include_router(router, prefix='/api/v1'); app.dependency_overrides[get_db] = database
    app.state.agent_execution_runtime = runtimes[0]
    for runtime in runtimes:
        await runtime.start()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture-app') as client:
            yield client, sessions, runtimes
    finally:
        for runtime in runtimes:
            await runtime.stop()
        await engine.dispose()


async def agent(client, *, nodes=2):
    """Create a saved multi-node workflow whose second node must consume the first output."""
    body = {'name': 'Fixture workflow', 'type': 'character', 'prompt': 'Extract visible evidence only', 'parameters': {'character_limit': 3, 'plot_threshold': .4}, 'workflow': [{'id': 'start', 'label': 'Start', 'type': 'start'}, *[{'id': f'llm{i}', 'label': f'Node {i}', 'type': 'llm', 'prompt': f'fixture-node-{i}', 'temperature': .1 * i, 'top_p': max(.1, .8 - i * .1)} for i in range(1, nodes + 1)], {'id': 'end', 'label': 'End', 'type': 'end'}]}
    response = await client.post('/api/v1/agents', json=body)
    assert response.status_code == 201, response.text
    return response.json()['data']


async def submit(client, row, key='request-1'):
    """Authorize one bounded workflow through its real public route."""
    return await client.post(f"/api/v1/agent-execution/agents/{row['id']}/runs", json={'request_key': key, 'input_text': 'Visible red coat', 'expected_version': row['version'], 'max_tokens': 24, 'confirm_model_call': True,**fee_fields()})


async def terminal(client, run_id):
    """Wait only for durable terminal status, never assume a successful HTTP admission completed work."""
    for _ in range(200):
        row = (await client.get(f'/api/v1/agent-execution/runs/{run_id}')).json()['data']
        if row['status'] in {'succeeded', 'failed', 'cancelled'}:
            return row
        await asyncio.sleep(.03)
    raise AssertionError('Persistent run did not reach terminal state')


@pytest.mark.asyncio
async def test_real_http_node_dependencies_parameters_idempotency_and_multiworker(tmp_path):
    """Two scanners execute exactly two requests, forwarding node settings and prior output."""
    with local_model() as fixture:
        async with isolated_client(tmp_path, fixture, workers=2) as (client, sessions, _):
            config = await agent(client)
            response, duplicate = await asyncio.gather(submit(client, config), submit(client, config))
            assert response.status_code == 201, response.text
            run_id = response.json()['data']['id']
            assert duplicate.status_code == 201 and duplicate.json()['data']['id'] == run_id
            assert (await submit(client, config)).json()['data']['id'] == run_id
            result = await terminal(client, run_id)
            assert result['status'] == 'succeeded', result
            assert result['output_text'] == 'local-fixture-output-2'
            assert len(fixture['calls']) == 2
            assert all(call['authenticated'] and call['path'] == '/v1/chat/completions' for call in fixture['calls'])
            first, second = [call['body'] for call in fixture['calls']]
            assert first['temperature'] == .1 and first['top_p'] == pytest.approx(.7) and first['max_tokens'] == 24
            assert second['temperature'] == .2 and second['messages'][1]['content'] == 'local-fixture-output-1'
            assert '"character_limit": 3' in first['messages'][0]['content'] and 'fixture-node-1' in first['messages'][0]['content']
            assert first['frequency_penalty'] == .2
            assert [node['status'] for node in result['nodes']] == ['succeeded'] * 4
            assert result['nodes'][2]['input_text'] == result['nodes'][1]['output_text']
            assert FIXTURE_KEY not in json.dumps(result)
            changed = await client.post(f"/api/v1/agent-execution/agents/{config['id']}/runs", json={'request_key': 'request-1', 'input_text': 'changed', 'expected_version': 1, 'confirm_model_call': True,**fee_fields()})
            assert changed.status_code == 409
            async with sessions() as db:
                persisted = await db.get(AgentRun, run_id)
                assert FIXTURE_KEY not in json.dumps(persisted.model_binding)
            history = (await client.get(f"/api/v1/agent-execution/agents/{config['id']}/runs")).json()['data']['items']
            assert history[0]['output_text'] == result['output_text']


@pytest.mark.asyncio
async def test_provider_failure_is_sanitized_and_retry_is_explicit_new_attempt(tmp_path):
    """An upstream error never fabricates output or retries; acknowledged retry creates a new run."""
    with local_model() as fixture:
        fixture['mode'] = 'fail'
        async with isolated_client(tmp_path, fixture) as (client, _, _):
            config = await agent(client, nodes=1)
            original = (await submit(client, config)).json()['data']
            failed = await terminal(client, original['id'])
            assert failed['status'] == 'failed' and '503' in failed['error'] and failed['output_text'] == ''
            assert FIXTURE_KEY not in json.dumps(failed)
            await asyncio.sleep(.15)
            assert len(fixture['calls']) == 1
            assert (await client.post(f"/api/v1/agent-execution/runs/{original['id']}/retry", json={'request_key': 'retry-1'})).status_code == 422
            fixture['mode'] = 'ok'
            retry = await client.post(f"/api/v1/agent-execution/runs/{original['id']}/retry", json={'request_key': 'retry-1', 'acknowledge_duplicate_charge': True,'expected_price_fingerprint':fixture_price().fingerprint})
            assert retry.status_code == 201, retry.text
            retried = await terminal(client, retry.json()['data']['id'])
            assert retried['status'] == 'succeeded' and retried['attempt'] == 2 and retried['retry_of'] == original['id']
            assert (await client.get(f"/api/v1/agent-execution/runs/{original['id']}")).json()['data']['status'] == 'failed'
            assert len(fixture['calls']) == 2


@pytest.mark.asyncio
async def test_cancel_discards_late_output_and_expired_lease_never_auto_replays(tmp_path):
    """Cancellation stops in-flight HTTP; crash leases become unknown failures without another request."""
    with local_model() as fixture:
        fixture['mode'] = 'block'
        async with isolated_client(tmp_path, fixture) as (client, sessions, runtimes):
            config = await agent(client, nodes=1)
            original = (await submit(client, config)).json()['data']
            for _ in range(100):
                if fixture['received'].is_set():
                    break
                await asyncio.sleep(.03)
            assert fixture['received'].is_set()
            cancelled = await client.post(f"/api/v1/agent-execution/runs/{original['id']}/cancel")
            assert cancelled.json()['data']['status'] == 'cancelled'
            fixture['release'].set()
            await asyncio.sleep(.25)
            observed = await terminal(client, original['id'])
            assert observed['status'] == 'cancelled' and observed['output_text'] == ''
            assert all(node['output_text'] == '' for node in observed['nodes'] if node['type'] == 'llm')
            await runtimes[0].stop()
            second = (await submit(client, config, key='crash-1'))
            assert second.status_code == 503
            runtimes[0].running = True
            second = (await submit(client, config, key='crash-1')).json()['data']
            async with sessions() as db:
                await db.execute(update(AgentRun).where(AgentRun.id == second['id']).values(status='running', lease_token='dead-worker', lease_expires_at=clock() - timedelta(seconds=1)))
                await db.commit()
            await runtimes[0].tick()
            crashed = await terminal(client, second['id'])
            assert crashed['status'] == 'failed' and '结果可能未知' in crashed['error']
            assert len(fixture['calls']) == 1


@pytest.mark.asyncio
async def test_missing_configuration_and_one_request_connection_probe(tmp_path):
    """A missing account is honest; the connection test sends exactly eight max tokens once."""
    with local_model() as fixture:
        async with isolated_client(tmp_path, fixture, configured=False) as (client, _, _):
            config = await agent(client, nodes=1)
            availability = (await client.get('/api/v1/agent-execution/availability')).json()['data']
            assert not availability['available'] and availability['models'] == []
            assert (await submit(client, config)).status_code == 503
            assert fixture['calls'] == []
    with local_model() as fixture:
        async with isolated_client(tmp_path / 'probe', fixture) as (client, _, _):
            body = {'request_key': 'probe-1', 'confirm_model_call': True,**fee_fields()}
            response = await client.post('/api/v1/agent-execution/models/fixture-model/connection-tests', json=body)
            assert response.status_code == 201, response.text
            result = await terminal(client, response.json()['data']['id'])
            assert result['status'] == 'succeeded' and result['kind'] == 'connection_test'
            assert len(fixture['calls']) == 1 and fixture['calls'][0]['body']['max_tokens'] == 8 and fixture['calls'][0]['body']['messages'][1]['content'] == 'ping'
            assert (await client.post('/api/v1/agent-execution/models/fixture-model/connection-tests', json=body)).json()['data']['id'] == result['id']
            history = (await client.get('/api/v1/agent-execution/models/fixture-model/connection-tests')).json()['data']['items']
            assert len(history) == 1


@pytest.mark.asyncio
async def test_expired_owner_cannot_commit_late_success_and_admission_rollback_is_invisible(tmp_path):
    """A paused owner cannot outlive its lease; an uncommitted admission never starts HTTP."""
    with local_model() as fixture:
        async with isolated_client(tmp_path, fixture) as (client, sessions, runtimes):
            config = await agent(client, nodes=1)
            async with sessions() as db:
                uncommitted = await create_run(db, runtimes[0], config['id'], AgentRunCreate(request_key='rollback', input_text='never send', expected_version=1, confirm_model_call=True,**fee_fields()))
                uncommitted_id = uncommitted.id
                await asyncio.sleep(.1)
                assert fixture['calls'] == []
                await db.rollback()
            async with sessions() as db:
                assert await db.get(AgentRun, uncommitted_id) is None
            fixture['mode'] = 'block'
            response = await submit(client, config, key='expire-live')
            run_id = response.json()['data']['id']
            for _ in range(100):
                if fixture['received'].is_set():
                    break
                await asyncio.sleep(.03)
            assert fixture['received'].is_set()
            async with sessions() as db:
                await db.execute(update(AgentRun).where(AgentRun.id == run_id).values(lease_expires_at=clock() - timedelta(seconds=1)))
                await db.commit()
            fixture['release'].set()
            result = await terminal(client, run_id)
            assert result['status'] == 'failed' and result['output_text'] == ''
            assert all(node['output_text'] == '' for node in result['nodes'] if node['type'] == 'llm')
            assert len(fixture['calls']) == 1


@pytest.mark.asyncio
async def test_bounded_workflow_and_malformed_response_never_fake_success(tmp_path):
    """Unsupported execution size fails before HTTP; malformed provider bytes persist as failure."""
    with local_model() as fixture:
        async with isolated_client(tmp_path, fixture) as (client, _, _):
            oversized = await agent(client, nodes=9)
            assert (await submit(client, oversized)).status_code == 422
            assert fixture['calls'] == []
            fixture['mode'] = 'malformed'
            config = await agent(client, nodes=1)
            response = await submit(client, config)
            result = await terminal(client, response.json()['data']['id'])
            assert result['status'] == 'failed' and result['output_text'] == ''
            assert FIXTURE_KEY not in json.dumps(result) and '格式无效' in result['error']
            assert len(fixture['calls']) == 1


@pytest.mark.asyncio
async def test_queued_model_binding_change_is_a_recorded_node_failure_without_redirect(tmp_path):
    """A model edit cannot silently change an already authorized task's HTTP target or identity."""
    with local_model() as fixture:
        async with isolated_client(tmp_path, fixture) as (client, sessions, runtimes):
            config = await agent(client, nodes=1)
            await runtimes[0].stop()
            runtimes[0].running = True
            response = await submit(client, config, key='binding-change')
            run_id = response.json()['data']['id']
            async with sessions() as db:
                await db.execute(update(Model).where(Model.id == 'fixture-model').values(name='different-model'))
                await db.commit()
            await runtimes[0].tick()
            result = await terminal(client, run_id)
            assert result['status'] == 'failed' and '模型配置在排队后改变' in result['error']
            assert result['nodes'][1]['status'] == 'failed' and result['nodes'][1]['input_text'] == 'Visible red coat'
            assert fixture['calls'] == []
