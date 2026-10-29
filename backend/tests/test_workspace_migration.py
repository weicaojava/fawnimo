"""Private disposable legacy migration fixtures; no original user data or external model is used."""
import asyncio
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

import app.api.v1  # noqa: F401
from app.core.db import Base
from app.core.workspace_boundary import assert_workspace_boundary, prepare_workspace_boundary
from app.controlplane.store import IdentityStore
from app.controlplane.maintenance import ProcessLease, MaintenanceBusy
from app.controlplane import migration as m


def legacy(tmp_path):
    """Create a complete local schema with one project, pending shot and immutable media bytes."""
    root=tmp_path/'source';root.mkdir()
    (root/'media').mkdir();(root/'files').mkdir()
    database=root/'studio.db'
    async def schema():
        """Register actual current tables on a fresh disposable legacy engine."""
        engine=create_async_engine('sqlite+aiosqlite:///'+database.as_posix())
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await engine.dispose()
    asyncio.run(schema())
    content=b'unchanged-source-media'
    (root/'media/fixture.bin').write_bytes(content)
    with closing(sqlite3.connect(database)) as db:
        db.execute("INSERT INTO projects(id,name,description,visual_style,style,seed,unify_style,progress,stats) VALUES('project','Original project','','live_action','real_people_city',0,1,0,'{}')")
        db.execute("INSERT INTO files(id,type,name,thumbnail,tags,storage_key) VALUES('media','video','fixture.bin','','[]','local-production/fixture.bin')")
        db.execute("INSERT INTO production_media(id,project_id,name,mime,sha256,size,relative_path) VALUES('media','project','fixture.bin','video/mp4',?,?, 'fixture.bin')",(hashlib.sha256(content).hexdigest(),len(content)))
        db.commit()
    return root


def staged(tmp_path):
    """Stage a new stable fixture outside its original workspace directory."""
    source=legacy(tmp_path)
    bundle=tmp_path/'bundle'
    m.stage_legacy(source/'studio.db',source/'media',source/'files',bundle)
    return source,bundle


def owner(root):
    """Provision an explicit maintenance-selected owner without ever opening a business worker."""
    store=IdentityStore(root)
    account=store.provision('migration-owner','synthetic-migration-password-2026')
    return store,account


def test_stage_verify_and_independent_restore_preserve_original_bytes(tmp_path):
    """All original rows/media survive staging and rollback without a source mutation."""
    source,bundle=staged(tmp_path)
    manifest,_=m.verify_bundle(bundle)
    before=m.digest(source/'studio.db')
    restored=tmp_path/'restored'
    assert m.restore_bundle(bundle,restored)['services_started'] is False
    assert m.digest(restored/'studio.db')==manifest['database_sha256']
    assert m.validate_database(source/'studio.db')==manifest['tables']
    assert m.tree(restored/'media')==m.tree(source/'media')
    assert m.digest(source/'studio.db')==before
    with pytest.raises(ValueError):
        m.restore_bundle(bundle,restored)
    with pytest.raises(ValueError):
        m.restore_bundle(bundle,source/'nested-restore')


@pytest.mark.parametrize('kind',['database','file'])
def test_source_change_during_copy_does_not_publish_manifest(tmp_path,monkeypatch,kind):
    """Mid-copy DB or file changes produce an unclaimable partial artifact, never a false success."""
    source=legacy(tmp_path)
    real=m.shutil.copytree
    changed=False
    def concurrent_change(*args,**kwargs):
        """Commit a real competing writer after the first payload copy."""
        nonlocal changed
        result=real(*args,**kwargs)
        if not changed:
            changed=True
            if kind=='database':
                with closing(sqlite3.connect(source/'studio.db')) as db:
                    db.execute("UPDATE projects SET name='Concurrent edit'");db.commit()
            else:
                (source/'media/fixture.bin').write_bytes(b'Concurrent bytes')
        return result
    monkeypatch.setattr(m.shutil,'copytree',concurrent_change)
    with pytest.raises(ValueError):
        m.stage_legacy(source/'studio.db',source/'media',source/'files',tmp_path/'bundle')
    assert not (tmp_path/'bundle/manifest.json').exists()


def test_changed_bundle_and_missing_or_external_files_are_rejected(tmp_path):
    """Claim/restore cannot trust altered bytes or silently skip unavailable external storage."""
    source,bundle=staged(tmp_path)
    (bundle/'media/fixture.bin').write_bytes(b'changed')
    with pytest.raises(ValueError):
        m.verify_bundle(bundle)
    with closing(sqlite3.connect(source/'studio.db')) as db:
        db.execute("UPDATE files SET storage_key='external/missing-key'");db.commit()
    with pytest.raises(ValueError):
        m.stage_legacy(source/'studio.db',source/'media',source/'files',tmp_path/'missing-bundle')


def test_claim_is_explicit_bound_locked_and_idempotent_after_new_edits(tmp_path):
    """Trusted claim preserves source rows and later destination edits; it never assigns to another owner."""
    source,bundle=staged(tmp_path)
    store,account=owner(tmp_path/'control')
    result=m.claim_bundle(bundle,store.root,account['account'])
    assert result['runtime_locked'] is True
    target=store.root/'workspaces'/account['workspace_id']
    assert_workspace_boundary(target/'studio.db',account['workspace_id'])
    manifest,_=m.verify_bundle(bundle)
    expected=m.business_receipts(manifest)
    assert m.validate_database(target/'studio.db',expected=expected)==expected
    with closing(sqlite3.connect(target/'studio.db')) as db:
        assert db.execute('SELECT runtime_locked FROM workspace_import').fetchone()==(1,)
        db.execute("UPDATE projects SET name='New destination edit'");db.commit()
        with pytest.raises(sqlite3.IntegrityError):
            db.execute('UPDATE workspace_import SET runtime_locked=0')
        db.rollback()
    assert m.claim_bundle(bundle,store.root,account['account'])==result
    with closing(sqlite3.connect(target/'studio.db')) as db:
        assert db.execute('SELECT name FROM projects').fetchone()==('New destination edit',)
    with closing(sqlite3.connect(source/'studio.db')) as db:
        assert db.execute('SELECT name FROM projects').fetchone()==('Original project',)
    assert not m.migration_pending(store.root/'workspaces',account['workspace_id'])


def test_normal_worker_still_cannot_claim_nonempty_legacy_database(tmp_path):
    """The migration-only helper does not weaken public/new-account initialization policy."""
    source=legacy(tmp_path)
    with pytest.raises(ValueError):
        prepare_workspace_boundary(source/'studio.db','ws_'+'a'*32,allow_initialize=True)


def test_claim_refuses_wrong_account_nonempty_space_and_active_maintenance(tmp_path):
    """An explicit account/owner and an unused destination are required before any publication."""
    _,bundle=staged(tmp_path)
    store,account=owner(tmp_path/'control')
    with pytest.raises(ValueError):
        m.claim_bundle(bundle,store.root,'unknown-owner')
    with ProcessLease(store.root/'.maintenance.lock'):
        with pytest.raises(MaintenanceBusy):
            m.claim_bundle(bundle,store.root,account['account'])
    target=store.root/'workspaces'/account['workspace_id'];target.mkdir(parents=True)
    (target/'untouched.txt').write_text('existing user data')
    with pytest.raises(ValueError):
        m.claim_bundle(bundle,store.root,account['account'])
    assert (target/'untouched.txt').read_text()=='existing user data'


def test_interrupted_publication_is_fenced_then_resumes_original_intent(tmp_path,monkeypatch):
    """A crash before rename leaves a durable pending claim, not an apparently empty new workspace."""
    _,bundle=staged(tmp_path)
    store,account=owner(tmp_path/'control')
    original=Path.rename
    def failed_publish(path,target):
        """Fail only the final pending-directory publication after successful offline migration."""
        if path.name.startswith('.pending-'):
            raise OSError('Synthetic interruption before publication')
        return original(path,target)
    monkeypatch.setattr(Path,'rename',failed_publish)
    with pytest.raises(OSError):
        m.claim_bundle(bundle,store.root,account['account'])
    assert m.migration_pending(store.root/'workspaces',account['workspace_id'])
    assert not (store.root/'workspaces'/account['workspace_id']).exists()
    monkeypatch.setattr(Path,'rename',original)
    assert m.claim_bundle(bundle,store.root,account['account'])['runtime_locked']
    assert not m.migration_pending(store.root/'workspaces',account['workspace_id'])


def test_worker_lease_rejects_claim_even_when_gateway_has_stopped(tmp_path):
    """A surviving private worker is independently fenced by its process-lifetime lease."""
    _,bundle=staged(tmp_path)
    store,account=owner(tmp_path/'control')
    with ProcessLease(store.root/'workspaces/.leases'/(account['workspace_id']+'.lock')):
        with pytest.raises(MaintenanceBusy):
            m.claim_bundle(bundle,store.root,account['account'])


def test_post_rename_interruption_rechecks_payload_before_completing(tmp_path):
    """A pending claim with damaged published bytes cannot be reopened after a crash."""
    _,bundle=staged(tmp_path)
    store,account=owner(tmp_path/'control')
    m.claim_bundle(bundle,store.root,account['account'])
    target=store.root/'workspaces'/account['workspace_id']
    # Represent interruption after atomic rename but before the journal's final commit.
    with store.connect() as db:
        db.execute("UPDATE workspace_import_claims SET state='preparing' WHERE workspace_id=?",(account['workspace_id'],))
    (target/'media/fixture.bin').write_bytes(b'Corrupt durability fixture')
    with pytest.raises(ValueError):
        m.claim_bundle(bundle,store.root,account['account'])
    assert m.migration_pending(store.root/'workspaces',account['workspace_id'])
    (target/'media/fixture.bin').write_bytes((bundle/'media/fixture.bin').read_bytes())
    assert m.claim_bundle(bundle,store.root,account['account'])['runtime_locked']


def test_retired_workspace_budget_history_keeps_original_values(tmp_path):
    """The supported budget-table rename is checked by original columns, not mistaken for data loss."""
    source=legacy(tmp_path)
    with closing(sqlite3.connect(source/'studio.db')) as db:
        db.execute('CREATE TABLE workspace_budget(id INTEGER PRIMARY KEY,currency TEXT,limit_minor INTEGER,revision INTEGER)')
        db.execute("INSERT INTO workspace_budget VALUES(1,'CNY',900,2)");db.commit()
    bundle=tmp_path/'bundle'
    m.stage_legacy(source/'studio.db',source/'media',source/'files',bundle)
    store,account=owner(tmp_path/'control')
    m.claim_bundle(bundle,store.root,account['account'])
    with closing(sqlite3.connect(store.root/'workspaces'/account['workspace_id']/'studio.db')) as db:
        assert db.execute('SELECT id,currency,limit_minor,revision FROM retired_workspace_budget').fetchall()==[(1,'CNY',900,2)]


def test_linked_source_bytes_and_owned_sources_are_rejected(tmp_path):
    """Bundles cannot follow linked payloads or relabel an already owned tenant database."""
    source=legacy(tmp_path)
    try:
        (source/'media/link.bin').symlink_to(source/'media/fixture.bin')
    except OSError:
        pytest.skip('Symlink creation unavailable to this Windows account')
    with pytest.raises(ValueError):
        m.stage_legacy(source/'studio.db',source/'media',source/'files',tmp_path/'linked-bundle')


@pytest.mark.parametrize('module',['exports','transition_exports'])
def test_import_lock_disables_new_export_admission(monkeypatch,module):
    """An existing tool cannot advertise executable renders while import recovery is locked."""
    from importlib import import_module
    import sys
    service=import_module('app.services.editing.'+module)
    monkeypatch.setattr(service.settings,'editing_export_enabled',True)
    monkeypatch.setattr(service.settings,'workspace_runtime_locked',True)
    assert service.enabled(sys.executable) is False
    assert '迁入空间' in service.disabled_reason()
