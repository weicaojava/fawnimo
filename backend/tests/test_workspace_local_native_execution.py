"""Local native admission/query on actual fixed workers; no real model, video download or public paid admission."""
from contextlib import closing, ExitStack
import json
import sqlite3
import time

from fastapi.testclient import TestClient
import httpx
import pytest

from app.controlplane.gateway import create_gateway
from app.core.contracts.model_accounting import ModelPriceConfiguration, ModelPriceEnvelope, ModelPriceRule
from app.core.contracts.seedance25 import MODEL_ID
from app.core.contracts.video_runtime_setup import VideoRuntimeConfiguration
from app.services.worker.native_video_models import PROTOCOL
from tests.test_workspace_local_model_execution import LocalExecutionSpaces, model_origin, PROJECT


class LocalNativeSpaces(LocalExecutionSpaces):
    """Keep previous completed local runs, adding one managed native account/configuration per original worker."""
    def __init__(self, app, client, root, origins):
        """Configure only authored origins/keys and start one explicit original-project native request per account."""
        super().__init__(app, client, root, origins)
        self.native_jobs, self.native_bodies = {}, {}
        for side in ('a', 'b'):
            self.ok(side, 'POST', '/llm/providers', json={'id': 'native-provider', 'name': 'volcengine',
                'base_url': f"http://127.0.0.1:{origins[side]['port']}/api/v3", 'api_key': origins[side]['key'], 'status': 'testing'})
            self.ok(side, 'POST', '/llm/models', json={'id': 'native-model', 'provider_id': 'native-provider', 'name': MODEL_ID,
                'category': 'video', 'params': {'native_video_protocol': PROTOCOL}})
            directory = root / 'workspaces' / self.identities[side]['workspace_id']; private = directory / 'private'
            with closing(sqlite3.connect(directory / 'studio.db')) as db:
                revision = db.execute("SELECT revision FROM provider_connection_versions WHERE provider_id='native-provider'").fetchone()[0]
            quote = ModelPriceEnvelope(model_id='native-model', model_name=MODEL_ID, provider_id='native-provider',
                provider_connection_version=revision, account_version='authored-native-' + side, max_call_minor=25,
                source_note='Authored local native request only, no supplier price or spending authorization')
            prices = ModelPriceConfiguration.model_validate_json((private / 'model-prices.json').read_bytes())
            prices = prices.model_copy(update={'rules': [*prices.rules, ModelPriceRule(quote=quote, kinds=['native_video'])]})
            (private / 'model-prices.json').write_text(prices.model_dump_json(), 'utf-8')
            local = json.loads((private / 'local-models.json').read_text('utf-8')); local['native_video_port'] = origins[side]['port']
            (private / 'local-models.json').write_text(json.dumps(local), 'utf-8')
            (private / 'native-video.json').write_text(VideoRuntimeConfiguration(enabled=True, model_id='native-model',
                account_version='authored-native-' + side, service_version='authored-local-native').model_dump_json(), 'utf-8')
            client.portal.call(self.restart, self.identities[side]['workspace_id'])
            setup = self.ok(side, 'GET', '/video-runtime/setup')
            assert setup['enabled'] and setup['running'] and setup['managed_model_id'] == 'native-model', setup
            body = {'request_key': 'same-native-request', 'model_id': MODEL_ID, 'duration': 4, 'references': [],
                'prompt': 'Authored original action ' + side, 'prompt_revision': '1', 'confirm_project_charges': True,
                'expected_price_fingerprint': quote.fingerprint}
            self.native_bodies[side] = body
            self.native_jobs[side] = self.native_submit(side, body)
        for side in ('a', 'b'):
            for _ in range(200):
                state = self.ok(side, 'GET', f'/execution/projects/{PROJECT}/jobs/' + self.native_jobs[side])
                if state['state'] == 'running' and origins[side]['native_queries']:
                    break
                assert state['state'] not in {'failed', 'cancelled'}, state
                time.sleep(.05)
            assert state['state'] == 'running' and origins[side]['native_queries']

    def native_submit(self, side, body):
        """Exercise the normal worker contract under its private test token; public admission must remain closed."""
        worker = self.app.state.workers.workers[self.identities[side]['workspace_id']]
        with httpx.Client(trust_env=False, follow_redirects=False) as internal:
            response = internal.post(f'http://127.0.0.1:{worker.port}/api/v1/execution/projects/{PROJECT}/submissions',
                json=body, headers={'X-Workspace-Token': worker.token, 'X-Identity-Id': self.identities[side]['user']['id']})
        assert response.status_code == 201, response.text
        return response.json()['data']['job_id']


@pytest.fixture(scope='module')
def native_spaces(tmp_path_factory):
    """Own and close all accounts, prices, HTTP origins and actual worker processes used in native verification."""
    with ExitStack() as stack:
        origins = {side: stack.enter_context(model_origin(side)) for side in ('a', 'b')}
        root = tmp_path_factory.mktemp('local-native-execution')
        app = create_gateway(root, secure_cookies=False, public_origin='http://script-task.local', capacity=2)
        with TestClient(app, base_url='http://script-task.local') as client:
            yield LocalNativeSpaces(app, client, root, origins)


@pytest.mark.parametrize('side', ['a', 'b'])
def test_local_native_request_query_original_recovery_and_project_fee_stay_in_their_fixed_account(native_spaces, side):
    """Positive local POST/GET use the original key; a sibling cannot read/control the job or spend its project cap."""
    spaces = native_spaces; other = 'b' if side == 'a' else 'a'; identifier = spaces.native_jobs[side]
    path = f'/execution/projects/{PROJECT}/jobs/' + identifier
    row = spaces.ok(side, 'GET', path)
    assert row['state'] == 'running' and 'authored-native-' not in json.dumps(row)
    before = len(spaces.origins[side]['calls'])
    assert spaces.native_submit(side, spaces.native_bodies[side]) == identifier
    assert len(spaces.origins[side]['calls']) == before == 6
    assert all(call['authenticated'] for call in spaces.origins[side]['calls'] + spaces.origins[side]['native_queries'])
    assert sum(call['path'].startswith('/api/v3/') for call in spaces.origins[side]['calls']) == 1
    for method, suffix in [('GET', ''), ('POST', '/pause'), ('POST', '/cancel')]:
        assert spaces.request(other, method, path + suffix, **({'json': {}} if method == 'POST' else {})).status_code == 404
    assert spaces.request(side, 'POST', f'/execution/projects/{PROJECT}/submissions', json=spaces.native_bodies[side]).status_code == 503
    budget = spaces.ok(side, 'GET', f'/video-runtime/projects/{PROJECT}/budget')
    assert budget['reserved_minor'] == 80 and budget['spent_minor'] == 0
    directory = spaces.root / 'workspaces' / spaces.identities[side]['workspace_id']
    with closing(sqlite3.connect(directory / 'studio.db')) as db:
        saved = db.execute("SELECT dispatch_state,provider_request_id,project_id FROM model_call_charges WHERE id=? AND kind='native_video'", (identifier,)).fetchone()
        actors = db.execute('SELECT DISTINCT submitted_by FROM model_charge_batches').fetchall()
    assert saved == ('accepted', 'private-authored-' + side + '-6', PROJECT)
    assert actors == [(spaces.identities[side]['user']['id'],)]
