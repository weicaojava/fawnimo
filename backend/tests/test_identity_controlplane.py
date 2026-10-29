"""Exercise real identity transactions and fail-closed gateway admission without private data."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import sqlite3

import httpx
import pytest
from fastapi import HTTPException

from app.controlplane.gateway import create_gateway
from app.controlplane.store import IdentityStore
from app.controlplane.workers import worker_environment

PASSWORD = 'isolated-fixture-password-2026'


@pytest.fixture
def store(tmp_path):
    """Use a disposable control plane, never the user's persistent studio."""
    return IdentityStore(tmp_path)


def test_first_login_is_atomic_and_idempotent(store):
    """Concurrent login cannot multiply personal workspaces or attach legacy data."""
    account = store.provision('tester.a', PASSWORD)
    with ThreadPoolExecutor(max_workers=4) as executor:
        tokens = list(executor.map(lambda _: store.login('tester.a', PASSWORD, 'local'), range(4)))
    assert {store.current(value)['workspace_id'] for value in tokens} == {account['workspace_id']}
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM workspaces').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM memberships').fetchone()[0] == 1
        assert db.execute('SELECT password_hash FROM users').fetchone()[0] != PASSWORD
        hashes = {value[0] for value in db.execute('SELECT token_hash FROM sessions')}
        assert hashes == {hashlib.sha256(value.encode()).hexdigest() for value in tokens}


def test_duplicate_account_and_untrusted_ownership(store):
    """Caller-controlled names cannot determine ownership, paths or an existing workspace."""
    first = store.provision('tester.a', PASSWORD)
    with pytest.raises(HTTPException) as error:
        store.provision('Tester.A', PASSWORD)
    assert error.value.status_code == 409
    second = store.provision('tester.b', PASSWORD)
    assert first['workspace_id'] != second['workspace_id']
    with pytest.raises(HTTPException):
        store.provision('../private', PASSWORD)


def test_revocation_expiry_and_membership_are_enforced(store):
    """A still-valid cookie loses access when its membership or account is disabled."""
    user = store.provision('tester.a', PASSWORD)
    token = store.login('tester.a', PASSWORD, 'local')
    with store.connect() as db:
        db.execute('DELETE FROM memberships WHERE user_id=?', (user['user_id'],))
    with pytest.raises(HTTPException) as error:
        store.current(token)
    assert error.value.status_code == 401
    with store.connect() as db:
        db.execute('INSERT INTO memberships VALUES(?,?,?)', (user['user_id'], user['workspace_id'], 'owner'))
        db.execute('UPDATE sessions SET expires_at=0')
    with pytest.raises(HTTPException):
        store.current(token)
    token = store.login('tester.a', PASSWORD, 'local')
    store.logout(token)
    with pytest.raises(HTTPException):
        store.current(token)
    token = store.login('tester.a', PASSWORD, 'local')
    with store.connect() as db:
        db.execute('UPDATE users SET disabled=1')
    with pytest.raises(HTTPException):
        store.current(token)


def test_switch_requires_membership_and_preferences_are_user_owned(store):
    """Switches never trust a submitted workspace ID; preferences follow the user."""
    a, b = store.provision('tester.a', PASSWORD), store.provision('tester.b', PASSWORD)
    token = store.login('tester.a', PASSWORD, 'local')
    with pytest.raises(HTTPException) as error:
        store.switch(token, b['workspace_id'])
    assert error.value.status_code == 404
    with store.connect() as db:
        db.execute('INSERT INTO memberships VALUES(?,?,?)', (a['user_id'], b['workspace_id'], 'viewer'))
    changed = store.profile(token, {'theme': 'dark', 'language': 'en-US'})
    assert changed['user']['theme'] == 'dark'
    changed = store.switch(token, b['workspace_id'])
    assert changed['role'] == 'viewer'
    assert changed['user']['language'] == 'en-US'
    assert store.current(store.login('tester.b', PASSWORD, 'local'))['user']['theme'] == 'light'


def test_failed_logins_have_persistent_limits(store):
    """A restart does not reset repeated password guessing for an account/peer."""
    store.provision('tester.a', PASSWORD)
    for _ in range(10):
        with pytest.raises(HTTPException) as error:
            store.login('tester.a', 'incorrect-password', 'same-peer')
        assert error.value.status_code == 401
    with pytest.raises(HTTPException) as error:
        IdentityStore(store.root).login('tester.a', PASSWORD, 'same-peer')
    assert error.value.status_code == 429


def test_audit_identity_is_immutable_and_never_stores_credentials(store):
    """Submission receipts hold trusted actor/scope and a digest, preserving unknown outcomes."""
    store.provision('tester.a', PASSWORD)
    identity = store.current(store.login('tester.a', PASSWORD, 'local'))
    receipt = store.audit_start(identity, 'POST', '/api/v1/llm/providers', b'{"api_key":"fixture-private"}')
    with store.connect() as db:
        row = dict(db.execute('SELECT * FROM request_audit WHERE id=?', (receipt,)).fetchone())
        assert 'fixture-private' not in str(row)
        assert row['submitted_by'] == identity['user']['id'] and row['status'] is None
        with pytest.raises(sqlite3.IntegrityError):
            db.execute('UPDATE request_audit SET submitted_by=?', ('forged',))
    store.audit_finish(receipt, 201)
    store.audit_finish(receipt, 500)
    with store.connect() as db:
        assert db.execute('SELECT status FROM request_audit').fetchone()[0] == 201


class NoWorker:
    """Any unauthorized request reaching a runtime is a test failure."""
    async def get(self, workspace_id):
        """Catch admission bypasses before a private process could start."""
        raise AssertionError('Request must not reach a workspace worker')

    async def close(self):
        """No resources are created by the rejecting fixture."""


@pytest.mark.asyncio
async def test_gateway_authentication_csrf_stale_tabs_and_billing_gates(tmp_path):
    """Reject missing identity, cross-space writes, legacy execution and unbudgeted generation."""
    app = create_gateway(tmp_path, secure_cookies=False, workers=NoWorker())
    store = app.state.identity_store
    a, b = store.provision('tester.a', PASSWORD), store.provision('tester.b', PASSWORD)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://studio.local') as client:
        response = await client.get('/api/v1/studio/projects')
        assert response.status_code == 401
        assert (await client.get('/api/v1/studio/files/private/content')).status_code == 401
        assert (await client.post('/api/v1/identity/register', json={'account':'third','password':PASSWORD})).status_code == 403
        response = await client.post('/api/v1/identity/login', json={'account':'tester.a','password':PASSWORD}, headers={'Origin':'https://evil.example'})
        assert response.status_code == 403
        response = await client.post('/api/v1/identity/login', json={'account':'tester.a','password':PASSWORD})
        assert response.status_code == 200 and 'httponly' in response.headers['set-cookie'].lower()
        assert PASSWORD not in response.text and 'password_hash' not in response.text
        headers = {'X-Workspace-Id': a['workspace_id'], 'X-Identity-Id':a['user_id']}
        assert (await client.post('/api/v1/studio/projects', json={})).status_code == 409
        assert (await client.get('/api/v1/studio/projects', headers={'X-Workspace-Id':b['workspace_id']})).status_code == 409
        assert (await client.post('/api/v1/identity/switch', json={'workspace_id':b['workspace_id']}, headers=headers)).status_code == 404
        assert (await client.post('/api/v1/studio/projects', json={}, headers={**headers,'Origin':'https://evil.example'})).status_code == 403
        for path in ['/film/tasks/video', '/script-processing/extract-shots', '/studio/image-tasks/create', '/agent-execution/agents/agent/runs', '/agent-execution/models/model/connection-tests', '/asset-image-generation/assets/actor/a/slots/s/jobs', '/video-understanding/projects/p/jobs', '/execution/projects/p/submissions', '/shot-plans/projects/p/plans/pl/shots/s/submissions']:
            assert (await client.post('/api/v1'+path, json={}, headers=headers)).status_code == 503, path
        for path in ['/agent-execution/agents/a/%2572uns', '/%2566ilm/tasks/video', '/studio/%252e%252e/film/tasks/video']:
            assert (await client.post('/api/v1'+path, json={}, headers=headers)).status_code == 400, path
        token = client.cookies.get('studio_session')
        assert (await client.post('/api/v1/identity/logout', headers=headers)).status_code == 200
        client.cookies.set('studio_session', token)
        assert (await client.get('/api/v1/identity/me')).status_code == 401


@pytest.mark.asyncio
async def test_secure_cookies_validation_and_preferences(tmp_path):
    """HTTPS defaults are explicit and malformed password inputs are never reflected."""
    app = create_gateway(tmp_path, workers=NoWorker())
    user = app.state.identity_store.provision('tester.a', PASSWORD)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://studio.local') as client:
        invalid = await client.post('/api/v1/identity/login', json={'account':'tester.a','password':'tiny-secret'})
        assert invalid.status_code == 422 and 'tiny-secret' not in invalid.text
        response = await client.post('/api/v1/identity/login', json={'account':'tester.a','password':PASSWORD})
        assert 'secure' in response.headers['set-cookie'].lower()
        assert 'samesite=strict' in response.headers['set-cookie'].lower()
        headers = {'X-Workspace-Id':user['workspace_id'], 'X-Identity-Id':user['user_id']}
        response = await client.patch('/api/v1/identity/me', json={'theme':'dark','language':'en-US'}, headers=headers)
        assert response.json()['data']['user']['theme'] == 'dark'
        assert (await client.patch('/api/v1/identity/me', json={'role':'owner'})).status_code == 422
        assert (await client.get('/health')).json() == {'status':'ok'}


@pytest.mark.asyncio
async def test_identity_writes_reject_stale_tab_after_account_switch(tmp_path):
    """An old account's page cannot update or revoke the new account sharing the browser cookie."""
    app = create_gateway(tmp_path, workers=NoWorker(), secure_cookies=False)
    store = app.state.identity_store
    a, b = store.provision('tester.a', PASSWORD), store.provision('tester.b', PASSWORD)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://studio.local') as client:
        await client.post('/api/v1/identity/login', json={'account':'tester.a', 'password':PASSWORD})
        headers = {'X-Workspace-Id':a['workspace_id'], 'X-Identity-Id':a['user_id']}
        await client.post('/api/v1/identity/login', json={'account':'tester.b', 'password':PASSWORD})
        for method, route, body in [('PATCH','me',{'theme':'dark'}),('POST','switch',{'workspace_id':a['workspace_id']}),('POST','logout',{})]:
            response = await client.request(method, '/api/v1/identity/'+route, json=body, headers=headers)
            assert response.status_code == 409
        current = (await client.get('/api/v1/identity/me')).json()['data']
        assert current['user']['id'] == b['user_id'] and current['user']['theme'] == 'light'


def test_worker_environment_is_a_credential_allowlist(monkeypatch):
    """New credential names remain excluded without relying on an ever-growing denylist."""
    monkeypatch.setenv('FUTURE_VENDOR_KEY', 'fixture-only')
    monkeypatch.setenv('VIDEO_RUNTIME_CONFIG', '/private/config.json')
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///private.db')
    monkeypatch.setenv('HTTP_PROXY', 'https://not-used.example')
    environment = worker_environment()
    assert not {'FUTURE_VENDOR_KEY','VIDEO_RUNTIME_CONFIG','DATABASE_URL','HTTP_PROXY'}.intersection(environment)
    assert environment['JELLYFISH_SKIP_ENV_FILE'] == '1'


@pytest.mark.asyncio
async def test_registration_is_explicit_and_cannot_choose_ownership(tmp_path):
    """Opt-in registration creates one personal space and rejects injected membership fields."""
    app = create_gateway(tmp_path, workers=NoWorker(), secure_cookies=False, allow_registration=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://studio.local') as client:
        assert (await client.post('/api/v1/identity/register', json={'account':'new.user','password':PASSWORD,'workspace_id':'ws_'+'0'*32})).status_code == 422
        response = await client.post('/api/v1/identity/register', json={'account':'new.user','password':PASSWORD,'display_name':'New user'})
        assert response.status_code == 200
        data = response.json()['data']
        assert data['role'] == 'owner' and len(data['workspaces']) == 1
        assert data['user']['display_name'] == 'New user'
        assert (await client.get('/api/v1/identity/me')).json()['data']['workspace_id'] == data['workspace_id']
        assert (await client.post('/api/v1/identity/register', json={'account':'new.user','password':PASSWORD})).status_code == 409
    with app.state.identity_store.connect() as db:
        assert db.execute('SELECT count(*) FROM users').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM workspaces').fetchone()[0] == 1
