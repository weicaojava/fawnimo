"""Exercise the real offline billing command in a disposable marked workspace and separate process."""
import asyncio
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker,create_async_engine

from app.controlplane.maintenance import ProcessLease
from app.controlplane.store import IdentityStore
from app.core.workspace_boundary import assert_workspace_boundary,claim_legacy_workspace
from app.dependencies import get_db
from app.main import app
from app.services.worker.model_billing_schema import migrate_model_billing
from tests.test_model_billing import admitted,invoice
from tests.test_model_charges import fixture

BACKEND=Path(__file__).resolve().parents[1]


def prepared_root(tmp_path):
    """Explicitly claim an authored fixture copy; no original studio database or real provider is read."""
    root=tmp_path/'identity'
    store=IdentityStore(root)
    owner=store.provision('billing-owner','synthetic-billing-owner-password')
    directory=root/'workspaces'/owner['workspace_id']
    directory.mkdir(parents=True)
    source=tmp_path/'synthetic-source';source.mkdir()
    async def seed():
        """Store one actual prepared/accepted fixture charge and fully migrated billing tables."""
        engine,factory=await fixture(source)
        await migrate_model_billing(engine)
        await admitted(factory)
        await engine.dispose()
    asyncio.run(seed())
    database=directory/'studio.db'
    shutil.copy2(source/'charges.db',database)
    claim_legacy_workspace(database,owner['workspace_id'],expected_sha256=hashlib.sha256(database.read_bytes()).hexdigest())
    original=tmp_path/'original-private-invoice.txt'
    original.write_bytes(b'PRIVATE_AUTHORED_BILLING_EVIDENCE; no external statement or live model.')
    normalized=tmp_path/'normalized.csv';normalized.write_bytes(invoice())
    return root,store,owner,directory,original,normalized


def command(root,*arguments,account='billing-owner'):
    """Run the actual entry point with a bounded process lifetime and no inherited provider configuration."""
    environment={**os.environ,'JELLYFISH_SKIP_ENV_FILE':'1','PYTHONIOENCODING':'utf-8'}
    for key in ('DATABASE_URL','WORKSPACE_ID','WORKSPACE_INTERNAL_TOKEN','VIDEO_RUNTIME_CONFIG'):
        environment.pop(key,None)
    return subprocess.run([sys.executable,str(BACKEND/'reconcile_model_billing.py'),'--root',str(root),'--account',account,*arguments],
        cwd=BACKEND,env=environment,capture_output=True,text=True,encoding='utf-8',timeout=45,check=False)


def import_arguments(original,normalized,key='fixture-offline-invoice'):
    """Use explicit fixture account/version fields rather than inferred environment credentials."""
    return ['--original',str(original),'--normalized',str(normalized),'--provider-id','p','--connection-version','1',
        '--account-version','synthetic-account-v1','--statement-key',key]


def counters(directory):
    """Read immutable receipts and counters through an independent SQLite connection."""
    with closing(sqlite3.connect(directory/'studio.db')) as db:
        return db.execute('SELECT project_id,reserved_minor,spent_minor,limit_minor FROM execution_budgets ORDER BY project_id').fetchall(),\
            db.execute('SELECT count(*) FROM model_billing_receipts').fetchone()[0]


def test_real_offline_import_replay_duplicate_resolution_and_private_download_boundary(tmp_path):
    """Exact files archive privately; real process replay and explicit duplicate resolution spend once."""
    root,store,owner,directory,original,normalized=prepared_root(tmp_path)
    arguments=import_arguments(original,normalized)
    first=command(root,*arguments)
    assert first.returncode==0,first.stderr
    result=json.loads(first.stdout)
    assert result['applied']==1 and result['external_model_calls']==0 and not result['provider_automatically_authenticated']
    assert 'fixture-request-1' not in first.stdout+first.stderr and 'PRIVATE_AUTHORED' not in first.stdout+first.stderr
    assert counters(directory)==([('a',0,12,100),('b',0,0,100)],1)
    repeated=command(root,'--statement-id',result['statement_id'])
    assert repeated.returncode==0 and json.loads(repeated.stdout)['applied']==1
    assert command(root,*arguments).returncode==0
    archive=directory/'files'/'billing-evidence'/hashlib.sha256(original.read_bytes()).hexdigest()
    assert (archive/'original.bin').read_bytes()==original.read_bytes()
    assert (archive/('normalized-'+hashlib.sha256(normalized.read_bytes()).hexdigest()+'.csv')).read_bytes()==normalized.read_bytes()
    with closing(sqlite3.connect(directory/'studio.db')) as db:
        assert db.execute('SELECT count(*) FROM files').fetchone()==(0,)
        assert db.execute('SELECT imported_by FROM model_billing_statements').fetchone()==(owner['user_id'],)
        assert db.execute('SELECT recorded_by FROM model_billing_receipts').fetchone()==(owner['user_id'],)
    second=command(root,*import_arguments(original,normalized,key='overlapping-invoice'))
    assert second.returncode==0 and json.loads(second.stdout)['conflict']==1
    with closing(sqlite3.connect(directory/'studio.db')) as db:
        line=db.execute('SELECT id FROM model_billing_lines WHERE statement_id=?',(json.loads(second.stdout)['statement_id'],)).fetchone()[0]
        receipt=db.execute('SELECT id FROM model_billing_receipts').fetchone()[0]
    resolved=command(root,'--resolve-line',line,'--reference-receipt',receipt,'--reason','Explicit exact duplicate fixture review')
    assert resolved.returncode==0 and json.loads(resolved.stdout)['action']=='duplicate_evidence'
    assert counters(directory)==([('a',0,12,100),('b',0,0,100)],1)
    assert_workspace_boundary(directory/'studio.db',owner['workspace_id'])
    engine=create_async_engine('sqlite+aiosqlite:///'+(directory/'studio.db').as_posix())
    factory=async_sessionmaker(engine)
    async def workspace_db():
        """Point ordinary file handlers at this same marked disposable database."""
        async with factory() as db:
            yield db
    app.dependency_overrides[get_db]=workspace_db
    try:
        client=TestClient(app)  # No lifespan: do not start default/background studio services.
        listing=client.get('/api/v1/studio/files').json()
        assert 'PRIVATE_AUTHORED' not in json.dumps(listing) and 'billing-evidence' not in json.dumps(listing)
        for suffix in ('original.bin',archive.name,'billing-evidence/'+archive.name+'/original.bin'):
            response=client.get('/api/v1/studio/files/'+suffix+'/download')
            assert response.status_code==404 and b'PRIVATE_AUTHORED' not in response.content
    finally:
        app.dependency_overrides.pop(get_db,None)
        asyncio.run(engine.dispose())


@pytest.mark.parametrize('busy',['root','worker'])
def test_active_service_lease_prevents_offline_mutation(tmp_path,busy):
    """A separate live lock holder fences maintenance before any invoice or private archive is created."""
    root,store,owner,directory,original,normalized=prepared_root(tmp_path)
    before=counters(directory)
    target=root/'.maintenance.lock' if busy=='root' else root/'workspaces'/'.leases'/(owner['workspace_id']+'.lock')
    with ProcessLease(target):
        result=command(root,*import_arguments(original,normalized))
    assert result.returncode==2 and counters(directory)==before and not (directory/'files').exists()
    assert 'PRIVATE_AUTHORED' not in result.stdout+result.stderr


@pytest.mark.parametrize('invalid',['disabled','viewer','missing','marker','redirect'])
def test_wrong_owner_marker_or_evidence_redirect_never_imports_a_bill(tmp_path,invalid):
    """Only an active owner of the fixed marked directory may run maintenance; evidence cannot escape it."""
    root,store,owner,directory,original,normalized=prepared_root(tmp_path)
    if invalid in {'disabled','viewer'}:
        with store.connect() as db:
            if invalid=='disabled':
                db.execute('UPDATE users SET disabled=1 WHERE id=?',(owner['user_id'],))
            else:
                db.execute("UPDATE memberships SET role='viewer' WHERE user_id=?",(owner['user_id'],))
    if invalid=='marker':
        with closing(sqlite3.connect(directory/'studio.db')) as db:
            db.execute('DROP TRIGGER workspace_identity_no_update')
            db.commit()
    if invalid=='redirect':
        (directory/'files').mkdir()
        outside=tmp_path/'outside';outside.mkdir()
        try:
            (directory/'files'/'billing-evidence').symlink_to(outside,target_is_directory=True)
        except OSError:
            pytest.skip('Creating a directory symlink requires platform permission')
    before=counters(directory)
    result=command(root,*import_arguments(original,normalized),account='missing-owner' if invalid=='missing' else 'billing-owner')
    assert result.returncode==2 and counters(directory)==before
    assert 'PRIVATE_AUTHORED' not in result.stdout+result.stderr
    if invalid=='redirect':
        assert not list(outside.iterdir())


@pytest.mark.parametrize('claim_state',['preparing','unknown-state','complete'])
def test_persistent_claim_journal_fences_billing_until_original_claim_is_complete(tmp_path,claim_state):
    """A crash-published target cannot be edited before recovery verifies the original migration receipt."""
    root,store,owner,directory,original,normalized=prepared_root(tmp_path)
    with store.connect() as db:
        db.execute('CREATE TABLE workspace_import_claims(workspace_id TEXT PRIMARY KEY,user_id TEXT,import_id TEXT,manifest_sha256 TEXT,state TEXT)')
        db.execute('INSERT INTO workspace_import_claims VALUES(?,?,?,?,?)',
            (owner['workspace_id'],owner['user_id'],'imp_'+'a'*32,'b'*64,claim_state))
    before=counters(directory)
    result=command(root,*import_arguments(original,normalized))
    if claim_state=='complete':
        assert result.returncode==0 and counters(directory)==([('a',0,12,100),('b',0,0,100)],1)
    else:
        assert result.returncode==2 and counters(directory)==before and not (directory/'files').exists()
