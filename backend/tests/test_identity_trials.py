"""Real trial registration/expiry/quota/CLI and gateway-owned workers with synthetic accounts only; no paid models or original user data."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import httpx
import pytest
from fastapi import HTTPException
from app.controlplane.gateway import create_gateway
from app.controlplane.store import IdentityStore
from app.core.contracts.identity_trials import TrialRegistrationPolicy
from app.core.workspace_boundary import prepare_workspace_boundary
from app.core.workspace_trial_quota import prepare_trial_project_quota

PASSWORD='authored-trial-account-password-2026'


def trial_store(tmp_path,*,capacity=2,project_limit=1):
    """Provide a trusted controllable server clock without changing global time or inherited worker configuration."""
    clock=[int(time.time())]; store=IdentityStore(tmp_path/'control',clock=lambda:clock[0])
    return store,TrialRegistrationPolicy(capacity=capacity,duration_seconds=60,project_limit=project_limit),clock


def test_actual_concurrent_last_registration_slot_creates_only_one_owned_space(tmp_path):
    """Eight real competing SQLite writers cannot create more trial users/spaces than the two remaining registration slots."""
    store,policy,_clock=trial_store(tmp_path)
    def claim(index):
        """Return only a safe outcome; failed capacity admission must roll back all user/workspace/membership inserts."""
        try: return store.provision('trial.'+str(index),PASSWORD,trial_policy=policy)
        except HTTPException as error: assert error.status_code==409; return None
    with ThreadPoolExecutor(max_workers=8) as pool: claims=list(pool.map(claim,range(8)))
    assert len([value for value in claims if value])==2
    with store.connect() as db:
        for table in ('users','workspaces','memberships','identity_trial_accounts'): assert db.execute('SELECT count(*) FROM '+table).fetchone()[0]==2
    assert store.trial_registration_status(policy)['remaining']==0


def test_expiry_keeps_data_identity_and_read_only_session_while_permanent_accounts_stay_permanent(tmp_path):
    """Only newly configured trial users expire; an old live session becomes read-only at the exact server expiry and login still permits reading."""
    store,policy,clock=trial_store(tmp_path)
    permanent=store.provision('existing.permanent',PASSWORD); trial=store.provision('new.trial',PASSWORD,trial_policy=policy)
    token=store.login('new.trial',PASSWORD,'authored-local'); old=store.current(token)
    assert old['trial']['read_only'] is False and old['trial']['project_limit']==1
    clock[0]+=60; current=store.current(token)
    assert current['trial']['read_only'] is True and current['user']['id']==trial['user_id'] and current['workspace_id']==trial['workspace_id']
    assert store.current(store.login('new.trial',PASSWORD,'authored-local'))['trial']['read_only'] is True
    existing=store.current(store.login('existing.permanent',PASSWORD,'authored-local'))
    assert 'trial' not in existing and existing['workspace_id']==permanent['workspace_id']
    assert store.trial_registration_status(policy)['remaining']==1


def test_actor_and_workspace_owner_expiry_cannot_be_bypassed_through_membership_switch(tmp_path):
    """A permanent collaborator inherits an expired trial space's write boundary; a trial actor keeps its expiry in a permanent space."""
    store,policy,clock=trial_store(tmp_path)
    trial=store.provision('trial.owner',PASSWORD,trial_policy=policy); permanent=store.provision('permanent.owner',PASSWORD)
    with store.connect() as db:
        db.execute('INSERT INTO memberships VALUES(?,?,?)',(permanent['user_id'],trial['workspace_id'],'editor'))
        db.execute('INSERT INTO memberships VALUES(?,?,?)',(trial['user_id'],permanent['workspace_id'],'editor'))
    trial_token=store.login('trial.owner',PASSWORD,'authored-local'); permanent_token=store.login('permanent.owner',PASSWORD,'authored-local')
    store.switch(permanent_token,trial['workspace_id']); store.switch(trial_token,permanent['workspace_id']); clock[0]+=60
    assert store.current(permanent_token)['trial']['read_only'] is True
    other=store.current(trial_token)['trial']; assert other['read_only'] is True and other['project_limit'] is None


def test_additive_trial_schema_keeps_prior_accounts_and_membership_unchanged(tmp_path):
    """Simulate an actual pre-trial control schema; reopening adds only empty optional trial state without adopting or expiring retained accounts."""
    store=IdentityStore(tmp_path/'legacy'); old=store.provision('legacy.account',PASSWORD); token=store.login('legacy.account',PASSWORD,'authored-local')
    before=store.current(token)
    with store.connect() as db:
        for name, in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'identity_trial_%'").fetchall(): db.execute('DROP TRIGGER '+name)
        db.execute('DROP TABLE identity_trial_accounts')
    reopened=IdentityStore(store.root)
    assert reopened.current(token)==before and reopened.workspace_trial_policy(old['workspace_id']) is None
    with reopened.connect() as db: assert db.execute('SELECT count(*) FROM identity_trial_accounts').fetchone()[0]==0


@pytest.mark.parametrize('action',['UPDATE','DELETE','REPLACE_ROWID'])
def test_trial_lifecycle_sql_cannot_be_extended_deleted_or_replaced(tmp_path,action):
    """The actual immutable record retains expiry/capacity under default SQLite pragmas, including physical-key replacement attempts."""
    store,policy,_clock=trial_store(tmp_path); account=store.provision('trial.immutable',PASSWORD,trial_policy=policy)
    with store.connect() as db:
        db.execute('PRAGMA recursive_triggers=OFF'); row=db.execute('SELECT rowid,* FROM identity_trial_accounts').fetchone()
        with pytest.raises(sqlite3.IntegrityError):
            if action=='UPDATE': db.execute('UPDATE identity_trial_accounts SET expires_at=expires_at+86400')
            elif action=='DELETE': db.execute('DELETE FROM identity_trial_accounts')
            else: db.execute('INSERT OR REPLACE INTO identity_trial_accounts(rowid,user_id,profile,created_at,expires_at,project_limit) VALUES(?,?,?,?,?,?)',
                (row[0],account['user_id'],'trial_v1',row['created_at'],row['expires_at']+86400,10))


def test_native_sqlite_project_quota_is_atomic_and_cannot_be_disabled_or_retargeted(tmp_path):
    """All SQL insert paths share one fixed-space trigger; delete frees active capacity, and omission/mutation cannot remove an installed policy."""
    database=tmp_path/'worker.db'; space='ws_'+'a'*32
    with closing(sqlite3.connect(database)) as db: db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT NOT NULL)')
    prepare_workspace_boundary(database,space,allow_initialize=True)
    policy={'profile':'trial_v1','expires_at':int(time.time())+60,'project_limit':1}
    prepare_trial_project_quota(database,space,policy)
    def create(index):
        """Use separate actual writers instead of a mirrored in-memory count."""
        with closing(sqlite3.connect(database,timeout=2)) as db:
            try: db.execute('INSERT INTO projects(id,name) VALUES(?,?)',(str(index),'Authored project')); db.commit(); return True
            except sqlite3.IntegrityError as error: assert 'FAWNIMO_TRIAL_PROJECT_LIMIT' in str(error); return False
    with ThreadPoolExecutor(max_workers=2) as pool: assert sum(pool.map(create,range(2)))==1
    for value in (None,{**policy,'project_limit':2}):
        with pytest.raises(ValueError): prepare_trial_project_quota(database,space,value)
    with closing(sqlite3.connect(database)) as db:
        db.execute('DELETE FROM projects'); db.commit()
        with pytest.raises(sqlite3.IntegrityError): db.execute('DELETE FROM workspace_trial_quota')
    assert create(3)


@pytest.mark.asyncio
async def test_real_registration_status_capacity_and_bounded_private_auth_inputs(tmp_path):
    """Actual gateway HTTP returns safe capacity/status, refuses protected overrides/overflow, then atomically exhausts one available trial slot."""
    app=create_gateway(tmp_path/'control',allow_registration=True,secure_cookies=False,trial_registration_policy=TrialRegistrationPolicy(capacity=1))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://trial.local') as client:
        status=(await client.get('/api/v1/identity/status')).json()['data']
        assert status['registration_enabled'] and status['trial_registration']['remaining']==1
        payload={'account':'trial.one','password':PASSWORD}
        rejected=await client.post('/api/v1/identity/register',json={**payload,'project_limit':10})
        assert rejected.status_code==422 and PASSWORD not in rejected.text
        overflow=await client.post('/api/v1/identity/register',content=b'x'*16385,headers={'Content-Type':'application/json'})
        assert overflow.status_code==413
        created=await client.post('/api/v1/identity/register',json=payload); assert created.status_code==200
        identity=created.json()['data']; assert identity['trial']['project_limit']==2 and identity['trial']['read_only'] is False
        second=await client.post('/api/v1/identity/register',json={'account':'trial.two','password':PASSWORD})
        assert second.status_code==409 and '名额已满' in second.json()['message']
        assert (await client.get('/api/v1/identity/status')).json()['data']['trial_registration']['remaining']==0


@pytest.mark.asyncio
async def test_real_owned_worker_project_limit_deletion_expiry_and_read_download_access(tmp_path):
    """The actual gateway and permanently scoped worker enforce quota, reject client override, allow freed capacity and make old sessions read-only at expiry."""
    app=create_gateway(tmp_path/'control',allow_registration=True,secure_cookies=False,trial_registration_policy=TrialRegistrationPolicy(capacity=1,duration_seconds=60,project_limit=1))
    clock=[int(time.time())]; app.state.identity_store.clock=lambda:clock[0]
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://trial.local',timeout=45) as client:
            response=await client.post('/api/v1/identity/register',json={'account':'trial.worker','password':PASSWORD}); assert response.status_code==200
            current=response.json()['data']; client.headers.update({'X-Identity-Id':current['user']['id'],'X-Workspace-Id':current['workspace_id']})
            def project(identifier):
                """Use the current public project contract, without provider calls or media uploads."""
                return {'id':identifier,'name':'Authored trial project','style':'真人都市','visual_style':'现实'}
            first=await client.post('/api/v1/studio/projects',json=project('trial-first')); assert first.status_code==201,first.text
            denied=await client.post('/api/v1/studio/projects',json=project('trial-second'),headers={'X-Trial-Project-Limit':'1000','X-Trial-Expires-At':'9999999999'})
            assert denied.status_code==409 and '体验项目' in denied.json()['message']
            demo=await client.post('/api/v1/identity/demo',json={'request_key':'trial-demo-full-001'}); assert demo.status_code==409 and '体验项目' in demo.json()['message']
            deleted=await client.delete('/api/v1/studio/projects/trial-first'); assert deleted.status_code in {200,204},deleted.text
            assert (await client.post('/api/v1/studio/projects',json=project('trial-second'))).status_code==201
            clock[0]+=60
            assert (await client.get('/api/v1/identity/me')).json()['data']['trial']['read_only'] is True
            assert (await client.get('/api/v1/studio/projects/trial-second')).status_code==200
            expired=await client.patch('/api/v1/studio/projects/trial-second',json={'name':'Must not change'})
            assert expired.status_code==403 and '体验已到期' in expired.json()['message']
            assert (await client.patch('/api/v1/identity/me',json={'theme':'dark'})).status_code==200
            assert (await client.post('/api/v1/identity/logout')).status_code==200


def test_actual_existing_account_provision_cli_remains_nonblocking_and_private(tmp_path):
    """The old trusted account-creation command still exits without starting a server, prints no password and never overwrites its private receipt."""
    backend=Path(__file__).resolve().parents[1]; root=tmp_path/'cli-control'; credential=tmp_path/'private-credential.json'
    args=[sys.executable,str(backend/'run_controlplane.py'),'--root',str(root),'--provision-account','legacy.cli','--credential-file',str(credential)]
    env={**os.environ,'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1','JELLYFISH_SKIP_ENV_FILE':'1'}
    result=subprocess.run(args,cwd=backend,env=env,capture_output=True,text=True,timeout=25)
    assert result.returncode==0; saved=credential.read_bytes(); account=json.loads(saved)
    assert account['password'] not in result.stdout+result.stderr
    identity=IdentityStore(root); token=identity.login('legacy.cli',account['password'],'authored-local'); assert 'trial' not in identity.current(token)
    refused=subprocess.run(args,cwd=backend,env=env,capture_output=True,text=True,timeout=25)
    assert refused.returncode==2 and credential.read_bytes()==saved


@pytest.mark.asyncio
async def test_chunked_identity_body_limit_runs_before_account_creation(tmp_path):
    """The actual streaming route bounds raw input even without Content-Length; no oversized malformed request creates an account."""
    app=create_gateway(tmp_path/'control',allow_registration=True,secure_cookies=False)
    async def chunks():
        """Send a finite authored body across multiple ASGI frames, never a real password or external upload."""
        yield b'{'*8192; yield b'x'*8193
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://trial.local') as client:
        response=await client.post('/api/v1/identity/register',content=chunks(),headers={'Content-Type':'application/json'})
        assert response.status_code==413
    with app.state.identity_store.connect() as db: assert db.execute('SELECT count(*) FROM users').fetchone()[0]==0


@pytest.mark.asyncio
async def test_original_demo_recovery_precedes_trial_project_capacity(tmp_path):
    """Recover a real previously completed demo at the full active-project cap; a different new intent gets a clear quota response."""
    app=create_gateway(tmp_path/'control',allow_registration=True,secure_cookies=False,
        trial_registration_policy=TrialRegistrationPolicy(capacity=1,project_limit=1))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://trial.local',timeout=45) as client:
            created=await client.post('/api/v1/identity/register',json={'account':'trial.demo','password':PASSWORD}); assert created.status_code==200
            current=created.json()['data']; client.headers.update({'X-Identity-Id':current['user']['id'],'X-Workspace-Id':current['workspace_id']})
            first=await client.post('/api/v1/identity/demo',json={'request_key':'original-trial-demo-key'}); assert first.status_code==200,first.text
            second=await client.post('/api/v1/identity/demo',json={'request_key':'new-trial-demo-key'}); assert second.status_code==409 and '体验项目' in second.json()['message']
            replay=await client.post('/api/v1/identity/demo',json={'request_key':'original-trial-demo-key'}); assert replay.status_code==200,replay.text
            assert replay.json()['data']['project_id']==first.json()['data']['project_id'] and replay.json()['data']['replayed'] is True


@pytest.mark.parametrize('override',[{'project_limit':True},{'expires_at':False},{'url':'https://untrusted.invalid'},{'command':'never accepted'}])
def test_real_worker_gate_rejects_invalid_trial_policy_before_creating_data(tmp_path,override):
    """Actual separate-process gate parsing cannot inherit or accept a caller-shaped quota/command/URL in the private policy envelope."""
    backend=Path(__file__).resolve().parents[1]; root=tmp_path/'must-not-create'
    policy={'profile':'trial_v1','expires_at':int(time.time())+60,'project_limit':1,**override}
    gate={'root':str(root),'workspace_id':'ws_'+'a'*32,'port':18452,'internal_token':'authored-worker-token-'+'x'*32,'trial_policy':policy}
    script='import json,sys;from run_tenant_worker import configure_worker;configure_worker(json.loads(sys.stdin.read()))'
    result=subprocess.run([sys.executable,'-c',script],input=json.dumps(gate).encode(),cwd=backend,
        env={**os.environ,'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1','JELLYFISH_SKIP_ENV_FILE':'1'},capture_output=True,timeout=25)
    assert result.returncode!=0 and not root.exists()
