"""Actual fixed-worker local text/image protocols with separate accounts; public paid routes remain closed."""
import base64
from contextlib import closing, ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import sqlite3
import threading
import time

from fastapi.testclient import TestClient
import httpx
from PIL import Image
import pytest

from app.controlplane.gateway import create_gateway
from app.core.contracts.model_accounting import ModelPriceConfiguration, ModelPriceEnvelope, ModelPriceRule
from app.core.contracts.seedance25 import MODEL_ID as NATIVE_MODEL
from tests.test_script_task_workspaces import ScriptSpaces, PROJECT, CHAPTER, MODEL, CANDIDATES


@contextmanager
def model_origin(side):
    """Serve deterministic authored text/PNG responses on this fixture's own loopback origin."""
    stream = BytesIO(); Image.new('RGB', (256, 256), (170, 30, 20) if side == 'a' else (20, 40, 170)).save(stream, 'PNG')
    state = {'key': 'authored-local-' + side, 'calls': [], 'native_queries': [], 'png': stream.getvalue()}
    class Handler(BaseHTTPRequestHandler):
        """Implement compatible transports without exposing authorization values or pretending to assess quality."""
        def log_message(self, *_):
            """Keep payloads and fixture account material out of diagnostics."""
        def do_POST(self):
            """Require the current space's synthetic key and return an inert protocol result exactly once."""
            raw = self.rfile.read(int(self.headers['Content-Length']))
            body = None if self.path.endswith('/audio/transcriptions') else json.loads(raw)
            authenticated = self.headers.get('Authorization') == 'Bearer ' + state['key']
            state['calls'].append({'path': self.path, 'authenticated': authenticated, 'has_wav': b'RIFF' in raw})
            if not authenticated:
                self.send_error(403); return
            if self.path == '/v1/images/generations':
                result = {'data': [{'b64_json': base64.b64encode(state['png']).decode()}]}
            elif self.path == '/api/v3/contents/generations/tasks':
                result = {'id': 'authored-native-' + side}
            elif self.path == '/v1/audio/transcriptions':
                result = {'text': 'Authored local transcript', 'segments': [{'start': .2, 'end': .8, 'text': 'Authored local transcript'}]}
            elif self.path == '/v1/chat/completions':
                user = body['messages'][-1]['content']
                if isinstance(user, list):
                    from tests.test_video_understanding import fixture_candidate
                    facts = json.loads(user[0]['text'])
                    text = json.dumps(fixture_candidate(facts), ensure_ascii=False)
                    state['calls'][-1]['frame_images'] = sum(item['type'] == 'image_url' for item in user)
                else:
                    try:
                        facts = json.loads(user)
                        text = json.dumps({'text': 'Authored proposal ' + side, 'summary': 'Local software fixture only'}) if facts.get('operation') else 'Authored text ' + side
                    except (ValueError, AttributeError):
                        text = 'Authored text ' + side
                result = {'choices': [{'message': {'content': text}}], 'usage': {'total_tokens': 12}}
            else:
                self.send_error(404); return
            data = json.dumps(result).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(data)))
            self.send_header('X-Request-Id', 'private-authored-' + side + '-' + str(len(state['calls'])))
            self.end_headers(); self.wfile.write(data)
        def do_GET(self):
            """Return only the same authored native task's running state, never a video or another account's handle."""
            authenticated = self.headers.get('Authorization') == 'Bearer ' + state['key']
            state['native_queries'].append({'path': self.path, 'authenticated': authenticated})
            if not authenticated or self.path != '/api/v3/contents/generations/tasks/authored-native-' + side:
                self.send_error(404); return
            data = json.dumps({'id': 'authored-native-' + side, 'model': NATIVE_MODEL, 'status': 'running'}).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(data)))
            self.end_headers(); self.wfile.write(data)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    state.update(port=server.server_port, origin=f'http://127.0.0.1:{server.server_port}/v1')
    try:
        yield state
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


class LocalExecutionSpaces(ScriptSpaces):
    """Reuse only actual gateway request/process helpers, with a new positive local-execution setup."""
    def __init__(self, app, client, root, origins):
        """Provision independent workers, per-model quotes and local port policies without any host credentials."""
        self.app, self.client, self.root, self.origins = app, client, root, origins
        self.identities, self.tokens, self.quotes, self.jobs, self.intents = {}, {}, {}, {}, {}
        for side in ('a', 'b'):
            store = app.state.identity_store
            store.provision('local-execution-' + side, 'authored-local-execution-password')
            self.tokens[side] = store.login('local-execution-' + side, 'authored-local-execution-password', 'authored-local')
            self.identities[side] = store.current(self.tokens[side]); self.jobs[side] = {}; self.intents[side] = {}
            self.ok(side, 'POST', '/studio/projects', json={'id': PROJECT, 'name': 'Authored project ' + side, 'style': '真人都市'})
            self.ok(side, 'POST', '/studio/chapters', json={'id': CHAPTER, 'project_id': PROJECT, 'index': 1, 'title': 'Authored ' + side})
            history = self.ok(side, 'GET', f'/studio/chapters/{CHAPTER}/text-history')
            self.ok(side, 'POST', f'/studio/chapters/{CHAPTER}/text-history', json={'expected_revision': history['current_revision'], 'raw_text': 'Authored saved source ' + side})
            self.ok(side, 'POST', '/llm/providers', json={'id': 'same-provider', 'name': 'OpenAI',
                'base_url': origins[side]['origin'], 'api_key': origins[side]['key'], 'status': 'testing'})
            self.ok(side, 'POST', '/llm/models', json={'id': MODEL, 'provider_id': 'same-provider', 'name': 'Authored text ' + side, 'category': 'text'})
            self.ok(side, 'POST', '/llm/models', json={'id': 'image-model', 'provider_id': 'same-provider', 'name': 'Authored image ' + side,
                'category': 'image', 'params': {'asset_image_protocol': 'openai_b64_v1'}})
            self.ok(side, 'POST', '/studio/entities/actor', json={'id': 'same-actor', 'project_id': PROJECT, 'name': 'Authored ' + side})
            self.slot = self.ok(side, 'POST', '/studio/entities/actor/same-actor/images', json={})['id']
            self.ok(side, 'PUT', f'/video-runtime/projects/{PROJECT}/budget', json={'expected_revision': 0, 'limit_minor': 1000})
            directory = root / 'workspaces' / self.identities[side]['workspace_id']
            with closing(sqlite3.connect(directory / 'studio.db')) as db:
                revision = db.execute("SELECT revision FROM provider_connection_versions WHERE provider_id='same-provider'").fetchone()[0]
            text_quote = ModelPriceEnvelope(model_id=MODEL, model_name='Authored text ' + side, provider_id='same-provider',
                provider_connection_version=revision, account_version='authored-' + side, max_call_minor=10, source_note='Authored free local fixture only')
            image_quote = text_quote.model_copy(update={'model_id': 'image-model', 'model_name': 'Authored image ' + side, 'max_call_minor': 15})
            self.quotes[side] = {'text': text_quote, 'image': image_quote}
            private = directory / 'private'; private.mkdir()
            (private / 'model-prices.json').write_text(ModelPriceConfiguration(enabled=True, rules=[
                ModelPriceRule(quote=text_quote, kinds=['agent_text', 'model_connection_test']),
                ModelPriceRule(quote=image_quote, kinds=['asset_image'])]).model_dump_json(), 'utf-8')
            (private / 'local-models.json').write_text(json.dumps({'schema_version': 1, 'workspace_id': self.identities[side]['workspace_id'],
                'enabled': True, 'agent_text_ports': [origins[side]['port']], 'asset_image_ports': [origins[side]['port']]}), 'utf-8')
            client.portal.call(self.restart, self.identities[side]['workspace_id'])
            self.ok(side, 'GET', '/agent-execution/availability', params={'model_id': MODEL})
            config = self.ok(side, 'POST', '/agents', json={'name': 'Authored workflow ' + side, 'type': 'character', 'prompt': 'Authored local fixture',
                'workflow': [{'id': 'start', 'label': 'Start', 'type': 'start'},
                    {'id': 'one', 'label': 'One', 'type': 'llm'}, {'id': 'two', 'label': 'Two', 'type': 'llm'},
                    {'id': 'end', 'label': 'End', 'type': 'end'}]})
            fee = {'project_id': PROJECT, 'expected_price_fingerprint': text_quote.fingerprint, 'confirm_model_call': True}
            self.submit_local(side, 'workflow', f"/agent-execution/agents/{config['id']}/runs", {
                **fee, 'model_id': MODEL, 'request_key': 'same-workflow', 'input_text': 'Authored input ' + side, 'expected_version': config['version']})
            self.submit_local(side, 'connection', f'/agent-execution/models/{MODEL}/connection-tests', {**fee, 'request_key': 'same-connection'})
            facts = self.ok(side, 'GET', CANDIDATES + '/availability', params={'operation': 'optimize', 'model_id': MODEL})
            self.submit_local(side, 'script', CANDIDATES, {**{key: value for key, value in fee.items() if key != 'project_id'}, 'model_id': MODEL, 'request_key': 'same-script', 'operation': 'optimize',
                'source_field': 'raw_text', 'expected_source_revision': facts['source_revision'], 'expected_source_hash': facts['source_hash']})
            self.submit_local(side, 'image', f'/asset-image-generation/assets/actor/same-actor/slots/{self.slot}/jobs', {
                'request_key': 'same-image', 'project_id': PROJECT, 'model_id': 'image-model', 'prompt': 'Authored image ' + side,
                'expected_price_fingerprint': image_quote.fingerprint, 'size': '256x256', 'confirm_model_call': True})
        for side in ('a', 'b'):
            for kind, job in self.jobs[side].items():
                path = '/asset-image-generation/jobs/' if kind == 'image' else '/agent-execution/runs/'
                for _ in range(150):
                    row = self.ok(side, 'GET', path + job['id'])
                    if row['status'] not in {'queued', 'running'}:
                        self.jobs[side][kind] = row; break
                    time.sleep(.05)
                assert row['status'] == ('candidate' if kind == 'image' else 'succeeded'), (side, kind, row)

    def submit_local(self, side, kind, path, body):
        """Use the private token solely in this test; public gateway admission is separately required to remain503."""
        worker = self.app.state.workers.workers[self.identities[side]['workspace_id']]
        with httpx.Client(trust_env=False, follow_redirects=False) as internal:
            response = internal.post(f'http://127.0.0.1:{worker.port}/api/v1' + path, json=body,
                headers={'X-Workspace-Token': worker.token, 'X-Identity-Id': self.identities[side]['user']['id']})
        assert response.status_code == 201, response.text
        row = response.json()['data']
        if kind in self.jobs[side]:
            assert row['id'] == self.jobs[side][kind]['id']
        else:
            self.jobs[side][kind] = row; self.intents[side][kind] = (path, body)
        return row


@pytest.fixture(scope='module')
def local_spaces(tmp_path_factory):
    """Own two real worker processes and two authored HTTP origins; shutdown joins only these disposable resources."""
    with ExitStack() as stack:
        origins = {side: stack.enter_context(model_origin(side)) for side in ('a', 'b')}
        root = tmp_path_factory.mktemp('local-model-execution')
        app = create_gateway(root, secure_cookies=False, public_origin='http://script-task.local', capacity=2)
        with TestClient(app, base_url='http://script-task.local') as client:
            yield LocalExecutionSpaces(app, client, root, origins)


@pytest.mark.parametrize('side', ['a', 'b'])
@pytest.mark.parametrize('kind', ['workflow', 'connection', 'script', 'image'])
def test_fixed_worker_executes_only_original_local_model_and_retains_original_project_charge(local_spaces, side, kind):
    """Actual local HTTP results and original request replay remain private across deliberately equal business IDs."""
    spaces = local_spaces; other = 'b' if side == 'a' else 'a'; job = spaces.jobs[side][kind]
    path = '/asset-image-generation/jobs/' if kind == 'image' else '/agent-execution/runs/'
    positive = spaces.ok(side, 'GET', path + job['id'])
    assert positive['status'] == ('candidate' if kind == 'image' else 'succeeded')
    assert spaces.request(other, 'GET', path + job['id']).status_code == 404
    assert 'private-authored-' not in json.dumps(positive) and spaces.origins[side]['key'] not in json.dumps(positive)
    before = [len(spaces.origins[value]['calls']) for value in ('a', 'b')]
    assert spaces.submit_local(side, kind, *spaces.intents[side][kind])['id'] == job['id']
    assert [len(spaces.origins[value]['calls']) for value in ('a', 'b')] == before == [5, 5]
    assert all(call['authenticated'] for call in spaces.origins[side]['calls'])
    assert sum(call['path'].endswith('/images/generations') for call in spaces.origins[side]['calls']) == 1
    public_path, body = spaces.intents[side][kind]
    assert spaces.request(side, 'POST', public_path, json=body).status_code == 503
    budget = spaces.ok(side, 'GET', f'/video-runtime/projects/{PROJECT}/budget')
    assert budget['reserved_minor'] == 55 and budget['spent_minor'] == 0
    directory = spaces.root / 'workspaces' / spaces.identities[side]['workspace_id']
    with closing(sqlite3.connect(directory / 'studio.db')) as db:
        actors = db.execute('SELECT DISTINCT submitted_by FROM model_charge_batches').fetchall()
        charges = db.execute('SELECT kind,dispatch_state,provider_request_id FROM model_call_charges').fetchall()
    assert actors == [(spaces.identities[side]['user']['id'],)] and len(charges) == 5
    assert {row[0] for row in charges} == {'agent_text', 'model_connection_test', 'asset_image'}
    assert all(row[1] == 'accepted' and row[2].startswith('private-authored-' + side) for row in charges)
    if kind == 'image':
        own = spaces.request(side, 'GET', path + job['id'] + '/content')
        assert own.status_code == 200 and own.content == spaces.origins[side]['png']
        assert spaces.request(other, 'GET', path + job['id'] + '/content').status_code == 404


@pytest.fixture(scope='module')
def restricted_local_spaces(tmp_path_factory):
    """Use a separate copy so rejected routing cannot change positive fixture clocks or existing exact replay tests."""
    with ExitStack() as stack:
        origins = {side: stack.enter_context(model_origin(side)) for side in ('a', 'b')}
        root = tmp_path_factory.mktemp('restricted-local-model-execution')
        app = create_gateway(root, secure_cookies=False, public_origin='http://script-task.local', capacity=2)
        with TestClient(app, base_url='http://script-task.local') as client:
            yield LocalExecutionSpaces(app, client, root, origins)


@pytest.mark.parametrize('side', ['a', 'b'])
def test_private_port_exception_does_not_allow_the_other_workspaces_model_origin(restricted_local_spaces, side):
    """Even a newly re-priced saved connection cannot send through a local port absent from its fixed worker policy."""
    spaces = restricted_local_spaces; other = 'b' if side == 'a' else 'a'
    spaces.ok(side, 'PATCH', '/llm/providers/same-provider', json={'base_url': spaces.origins[other]['origin']})
    directory = spaces.root / 'workspaces' / spaces.identities[side]['workspace_id']; private = directory / 'private'
    with closing(sqlite3.connect(directory / 'studio.db')) as db:
        revision = db.execute("SELECT revision FROM provider_connection_versions WHERE provider_id='same-provider'").fetchone()[0]
    quote = spaces.quotes[side]['text'].model_copy(update={'provider_connection_version': revision})
    prices = ModelPriceConfiguration.model_validate_json((private / 'model-prices.json').read_bytes())
    prices = prices.model_copy(update={'rules': [ModelPriceRule(quote=quote, kinds=['agent_text', 'model_connection_test']), prices.rules[1]]})
    (private / 'model-prices.json').write_text(prices.model_dump_json(), 'utf-8')
    spaces.client.portal.call(spaces.restart, spaces.identities[side]['workspace_id'])
    spaces.ok(side, 'GET', '/agent-execution/availability', params={'model_id': MODEL})
    row = spaces.submit_local(side, 'wrong-local-port', f'/agent-execution/models/{MODEL}/connection-tests', {
        'request_key': 'wrong-local-port', 'project_id': PROJECT, 'confirm_model_call': True, 'expected_price_fingerprint': quote.fingerprint})
    for _ in range(150):
        row = spaces.ok(side, 'GET', '/agent-execution/runs/' + row['id'])
        if row['status'] not in {'queued', 'running'}:
            break
        time.sleep(.05)
    assert row['status'] == 'failed'
    assert len(spaces.origins['a']['calls']) == len(spaces.origins['b']['calls']) == 5
    assert spaces.origins[side]['key'] not in json.dumps(row) and spaces.origins[other]['origin'] not in json.dumps(row)
    assert spaces.ok(side, 'GET', f'/video-runtime/projects/{PROJECT}/budget')['reserved_minor'] == 65
