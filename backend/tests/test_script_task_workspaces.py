"""Real gateway/fixed-worker script history isolation; public paid admission stays closed and model HTTP is zero."""
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sqlite3
import threading
import time

from fastapi.testclient import TestClient
import httpx
import pytest

from app.controlplane.gateway import create_gateway
from app.core.contracts.model_accounting import ModelPriceConfiguration, ModelPriceEnvelope, ModelPriceRule

PROJECT = 'same-project'
CHAPTER = 'same-chapter'
MODEL = 'same-model'
CANDIDATES = f'/script-preparation/projects/{PROJECT}/chapters/{CHAPTER}/candidates'


class ScriptSpaces:
    """Keep two separate databases with deliberately colliding business IDs and unrelated account/price facts."""

    def __init__(self, app, client, root, origin):
        """Create only new local identities, model metadata, saved text and conservative fixture holds."""
        self.app, self.client, self.root, self.origin = app, client, root, origin
        self.identities, self.tokens, self.jobs, self.quotes = {}, {}, {}, {}
        for side in ('a', 'b'):
            app.state.identity_store.provision('script-tasks-' + side, 'authored-script-task-password')
            token = app.state.identity_store.login('script-tasks-' + side, 'authored-script-task-password', 'authored-local')
            self.tokens[side] = token
            self.identities[side] = app.state.identity_store.current(token)
            self.ok(side, 'POST', '/studio/projects', json={'id': PROJECT, 'name': 'PRIVATE-' + side, 'style': '真人都市'})
            self.ok(side, 'POST', '/studio/chapters', json={'id': CHAPTER, 'project_id': PROJECT, 'index': 1, 'title': 'PRIVATE-' + side})
            history = self.ok(side, 'GET', f'/studio/chapters/{CHAPTER}/text-history')
            self.ok(side, 'POST', f'/studio/chapters/{CHAPTER}/text-history', json={'expected_revision': history['current_revision'], 'raw_text': 'PRIVATE-' + side + ' authored text'})
            self.ok(side, 'POST', '/llm/providers', json={'id': 'same-provider', 'name': 'OpenAI',
                'base_url': origin, 'api_key': 'authored-private-' + side, 'status': 'testing'})
            self.ok(side, 'POST', '/llm/models', json={'id': MODEL, 'provider_id': 'same-provider', 'category': 'text',
                'name': 'Authored text ' + side, 'params': {}})
            self.ok(side, 'PUT', f'/video-runtime/projects/{PROJECT}/budget', json={'expected_revision': 0, 'limit_minor': 100})
            directory = root / 'workspaces' / self.identities[side]['workspace_id']
            with closing(sqlite3.connect(directory / 'studio.db')) as db:
                revision = db.execute("SELECT revision FROM provider_connection_versions WHERE provider_id='same-provider'").fetchone()[0]
            quote = ModelPriceEnvelope(model_id=MODEL, model_name='Authored text ' + side, provider_id='same-provider',
                provider_connection_version=revision, account_version='authored-' + side, max_call_minor=10,
                source_note='Local negative transport fixture only; no supplier price or paid authorization')
            self.quotes[side] = quote
            private = directory / 'private'; private.mkdir()
            (private / 'model-prices.json').write_text(ModelPriceConfiguration(enabled=True,
                rules=[ModelPriceRule(quote=quote, kinds=['agent_text'])]).model_dump_json(), 'utf-8')
            self.client.portal.call(self.restart, self.identities[side]['workspace_id'])
            state = self.ok(side, 'GET', CANDIDATES + '/availability', params={'operation': 'optimize', 'model_id': MODEL})
            assert not state['available']  # Public admission remains503 despite a configured worker price.
            body = {'request_key': 'same-request', 'operation': 'optimize', 'source_field': 'raw_text', 'model_id': MODEL,
                'expected_source_revision': state['source_revision'], 'expected_source_hash': state['source_hash'],
                'expected_price_fingerprint': quote.fingerprint, 'confirm_model_call': True}
            # Seed retained history through the real private HTTP contract. Its transport is deliberately
            # rejected before any socket request: the fixed worker has no trusted local model ports.
            worker = app.state.workers.workers[self.identities[side]['workspace_id']]
            with httpx.Client(trust_env=False, follow_redirects=False) as internal:
                response = internal.post(f'http://127.0.0.1:{worker.port}/api/v1' + CANDIDATES, json=body,
                    headers={'X-Workspace-Token': worker.token, 'X-Identity-Id': self.identities[side]['user']['id']})
            assert response.status_code == 201, response.text
            self.jobs[side] = response.json()['data']
        for side in ('a', 'b'):
            for _ in range(100):
                row = self.ok(side, 'GET', CANDIDATES + '/' + self.jobs[side]['id'])
                if row['status'] == 'failed':
                    self.jobs[side] = row
                    break
                time.sleep(.05)
            assert self.jobs[side]['status'] == 'failed'

    async def restart(self, workspace):
        """Restart only this disposable fixture's owned process so it loads its own private quote file."""
        pool = self.app.state.workers
        async with pool.lock:
            worker = pool.workers.pop(workspace)
            await pool.stop_worker(worker)

    def request(self, side, method, path, **kwargs):
        """Let the gateway derive the authenticated worker; supplied IDs never select a foreign database."""
        identity = self.identities[side]
        headers = {'Cookie': 'studio_session=' + self.tokens[side], 'X-Workspace-Id': identity['workspace_id'],
            'X-Identity-Id': identity['user']['id'], 'Origin': 'http://script-task.local'}
        headers.update(kwargs.pop('headers', {}))
        return self.client.request(method, '/api/v1' + path, headers=headers, **kwargs)

    def ok(self, side, method, path, **kwargs):
        """Require positive owned reads/writes instead of counting a refused route as functional coverage."""
        response = self.request(side, method, path, **kwargs)
        assert response.is_success, (method, path, response.status_code, response.text)
        return response.json()['data']


@pytest.fixture(scope='module')
def script_spaces(tmp_path_factory):
    """Own every test account, process and inert origin; no user files, inherited model keys or paid HTTP."""
    requests = []
    class Handler(BaseHTTPRequestHandler):
        """Any reached model endpoint is an unexpected test failure, never a provider-shaped success."""
        def log_message(self, *_):
            """Do not log the fixture's private authorization headers."""
        def do_POST(self):
            """Count unexpected transport access while returning no generated content."""
            requests.append(self.path); self.send_error(500)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    root = tmp_path_factory.mktemp('script-task-spaces')
    app = create_gateway(root, secure_cookies=False, public_origin='http://script-task.local', capacity=2)
    try:
        with TestClient(app, base_url='http://script-task.local') as client:
            yield ScriptSpaces(app, client, root, f'http://127.0.0.1:{server.server_port}/v1')
        assert not requests, 'A disabled local transport must never reach a model socket'
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


@pytest.mark.parametrize('side', ['a', 'b'])
def test_script_history_and_project_holds_are_private_through_real_fixed_workers(script_spaces, side):
    """Owned positives and foreign negatives use the same IDs, while distinct runs, users and charges remain isolated."""
    spaces = script_spaces; other = 'b' if side == 'a' else 'a'
    own, foreign = spaces.jobs[side], spaces.jobs[other]
    feed = spaces.ok(side, 'GET', '/workspace-tasks/recent')
    rows = [row for row in feed['items'] if row['kind'] == 'script_candidate']
    assert feed['complete'] and len(rows) == 1 and rows[0]['status'] == 'failed'
    assert rows[0]['id'] == 'workspace:script_candidate:' + own['id']
    assert own['id'] in rows[0]['return_path'] and foreign['id'] not in json.dumps(feed)
    assert 'PRIVATE-' not in json.dumps(feed) and spaces.origin not in json.dumps(feed)
    assert len(spaces.ok(side, 'GET', CANDIDATES)) == 1
    assert 'PRIVATE-' + side in spaces.ok(side, 'GET', CANDIDATES + '/' + own['id'])['source_text']
    assert spaces.request(side, 'GET', CANDIDATES + '/' + foreign['id']).status_code == 404
    foreign_before = spaces.ok(other, 'GET', CANDIDATES + '/' + foreign['id'])
    foreign_budget = spaces.ok(other, 'GET', f'/video-runtime/projects/{PROJECT}/budget')
    for action in ('dismiss', 'adopt'):
        body = {'expected_source_revision': foreign['source_revision'], 'expected_source_hash': foreign['source_hash'], 'expected_output_hash': '0'*64} if action == 'adopt' else {}
        assert spaces.request(side, 'POST', CANDIDATES + '/' + foreign['id'] + '/' + action, json=body).status_code == 404
    assert spaces.ok(other, 'GET', CANDIDATES + '/' + foreign['id']) == foreign_before
    assert spaces.ok(other, 'GET', f'/video-runtime/projects/{PROJECT}/budget') == foreign_budget
    assert spaces.request(side, 'GET', '/workspace-tasks/recent', headers={'X-Workspace-Id': spaces.identities[other]['workspace_id']}).status_code == 409
    for suffix in ('', '/'):
        assert spaces.request(side, 'POST', CANDIDATES + suffix, json={}).status_code == 503
    assert len(spaces.ok(side, 'GET', CANDIDATES)) == 1
    directory = spaces.root / 'workspaces' / spaces.identities[side]['workspace_id']
    with closing(sqlite3.connect(directory / 'studio.db')) as db:
        saved = db.execute('SELECT workspace_id FROM script_candidates WHERE id=?', (own['id'],)).fetchone()
        actors = db.execute('SELECT submitted_by FROM model_charge_batches').fetchall()
    assert saved == (spaces.identities[side]['workspace_id'],)
    assert actors == [(spaces.identities[side]['user']['id'],)]
