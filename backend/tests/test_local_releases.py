"""Real private local release roots, SQLite backups/WAL, native locks, fresh identities, command processes and gateway HTTP; no personal data/models/hosting."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import httpx
import pytest
from app.core.contracts.local_releases import LocalPreviewInput,LocalSnapshotInput,LocalRestoreInput,LocalReleaseFeedbackInput
from app.controlplane.maintenance import ProcessLease,MaintenanceBusy
from app.controlplane.store import IdentityStore
from app.services.community.local_releases import LocalReleaseManager
from app.services.community.local_release_files import LocalReleaseError,read_json,inventory,file_hash


@pytest.fixture
def releases(tmp_path):
    """Initialize a new private maintenance root and declare only a public revision label, without asserting runtime image/CI qualification."""
    manager=LocalReleaseManager(tmp_path/'release-audit',initialize=True)
    body=LocalPreviewInput(request_key='preview-original-001',bundle_id='a'*64,source_commit='b'*40)
    preview=manager.prepare_preview(body,tmp_path/'preview-data'); credentials=read_json(preview['result']['credential_file'])
    return manager,body,preview,credentials


def fill_preview(preview,credentials):
    """Create real per-user SQLite/files and an opaque private connection fixture; nothing is copied from any original user account."""
    root=Path(preview['result']['preview_root'])
    for index,label in enumerate(('a','b')):
        workspace=root/'workspaces'/credentials[label]['workspace_id']; workspace.mkdir(parents=True)
        with closing(sqlite3.connect(workspace/'studio.db')) as db:
            db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT NOT NULL)')
            db.execute('INSERT INTO projects VALUES(?,?)',(label,'Authored project '+label)); db.commit()
        (workspace/'files').mkdir(); (workspace/'files'/'authored.bin').write_bytes(bytes([index+1])*1000)
        (workspace/'private').mkdir(); (workspace/'private'/'connection.json').write_text(json.dumps({'credential':'authored_private_test_connection_'+label}))
    return root


def test_new_preview_accounts_are_distinct_and_original_recovery_keeps_later_data(releases,tmp_path):
    """Preview users/workspaces are freshly generated; repeated original preparation does not reset passwords, projects or private files."""
    manager,body,preview,credentials=releases; source=fill_preview(preview,credentials)
    assert credentials['a']['user_id']!=credentials['b']['user_id'] and credentials['a']['workspace_id']!=credentials['b']['workspace_id']
    assert credentials['a']['password']!=credentials['b']['password']
    protected=inventory(source,time.monotonic()+20)
    assert manager.prepare_preview(body,source)==preview and inventory(source,time.monotonic()+20)==protected
    with pytest.raises(LocalReleaseError): manager.prepare_preview(body,tmp_path/'different-preview')
    assert not (tmp_path/'different-preview').exists() and preview['result']['models_configured'] is False
    assert preview['services_started'] is False and preview['runtime_revision_verified'] is False


def test_complete_snapshot_and_fresh_path_restore_preserve_later_edits_and_ownership(releases,tmp_path):
    """Backup the actual control DB, both workspace DBs, files/private configuration; restore old bytes only into a new independent path."""
    manager,_body,preview,credentials=releases; source=fill_preview(preview,credentials)
    request=LocalSnapshotInput(request_key='snapshot-original-001',source_commit='b'*40); saved=manager.snapshot(request,source)
    assert saved['phase']=='completed' and saved['result']['database_count']==3 and saved['result']['workspace_count']==2
    assert manager.verify(saved['id'])['verified'] is True
    active=source/'workspaces'/credentials['a']['workspace_id']
    with closing(sqlite3.connect(active/'studio.db')) as db: db.execute("UPDATE projects SET name='Later retained edit' WHERE id='a'"); db.commit()
    (active/'files'/'authored.bin').write_bytes(b'Later private bytes')
    later=inventory(source,time.monotonic()+20)
    restored=manager.restore(LocalRestoreInput(request_key='restore-original-001',snapshot_id=saved['id']),tmp_path/'restored-data')
    assert restored['phase']=='completed' and inventory(source,time.monotonic()+20)==later
    target=Path(restored['result']['restored_root']); identity=IdentityStore(target)
    for label in ('a','b'):
        token=identity.login(credentials[label]['account'],credentials[label]['password'],'authored-local')
        assert identity.current(token)['workspace_id']==credentials[label]['workspace_id']
    with closing(sqlite3.connect(target/'workspaces'/credentials['a']['workspace_id']/'studio.db')) as db:
        assert db.execute('SELECT name FROM projects').fetchone()==('Authored project a',)
    assert (target/'workspaces'/credentials['a']['workspace_id']/'files/authored.bin').read_bytes()==b'\x01'*1000
    assert manager.snapshot(request,source)==saved
    assert restored['existing_data_overwritten'] is False and restored['public_deployment_verified'] is False


def test_sqlite_backup_captures_uncheckpointed_wal(releases):
    """A real WAL-only committed row must appear in the archive rather than being lost by raw .db file copying."""
    manager,_body,preview,credentials=releases; root=fill_preview(preview,credentials)
    database=root/'workspaces'/credentials['a']['workspace_id']/'studio.db'
    with closing(sqlite3.connect(database)) as live:
        live.execute('PRAGMA journal_mode=WAL'); live.execute('PRAGMA wal_autocheckpoint=0')
        live.execute("INSERT INTO projects VALUES('wal','Committed in authored WAL')"); live.commit()
        assert database.with_name(database.name+'-wal').stat().st_size>0
        saved=manager.snapshot(LocalSnapshotInput(request_key='wal-snapshot-original',source_commit='b'*40),root)
        payload=manager.root/'archives'/saved['id']/'payload'/'workspaces'/credentials['a']['workspace_id']/'studio.db'
        with closing(sqlite3.connect(payload)) as copied:
            assert copied.execute("SELECT name FROM projects WHERE id='wal'").fetchone()==('Committed in authored WAL',)


@pytest.mark.parametrize('scope',['gateway','workspace'])
def test_actual_kernel_maintenance_lease_blocks_live_snapshot(releases,scope):
    """A cooperating gateway or surviving individual worker cannot share the offline source with a snapshot controller."""
    manager,_body,preview,credentials=releases; root=fill_preview(preview,credentials)
    lock=root/'.maintenance.lock' if scope=='gateway' else root/'workspaces/.leases'/(credentials['a']['workspace_id']+'.lock')
    with ProcessLease(lock):
        with pytest.raises(LocalReleaseError): manager.snapshot(LocalSnapshotInput(request_key='busy-snapshot-original',source_commit='b'*40),root)
    original=manager.get(hashlib.sha256(b'busy-snapshot-original').hexdigest())
    assert original['phase']=='attention'
    assert manager.snapshot(LocalSnapshotInput(request_key='busy-snapshot-original',source_commit='b'*40),root)==original


@pytest.mark.parametrize('bad',['changed-file','extra-file','missing-file','changed-manifest','corrupt-db'])
def test_tampered_archive_never_restores_to_destination(releases,tmp_path,bad):
    """Changed private archive bytes, schema manifest or extra/missing files cannot be passed off as a verified rollback."""
    manager,_body,preview,credentials=releases; root=fill_preview(preview,credentials)
    saved=manager.snapshot(LocalSnapshotInput(request_key='sealed-snapshot-original',source_commit='b'*40),root)
    archive=manager.root/'archives'/saved['id']; payload=archive/'payload'; media=payload/'workspaces'/credentials['a']['workspace_id']/'files/authored.bin'
    if bad=='changed-file': media.write_bytes(b'Changed')
    elif bad=='extra-file': (payload/'extra.bin').write_bytes(b'Extra')
    elif bad=='missing-file': media.unlink()
    elif bad=='changed-manifest': (archive/'manifest.json').write_text('{}')
    else: (payload/'control.db').write_bytes(b'Corrupt SQLite')
    with pytest.raises((LocalReleaseError,sqlite3.Error)): manager.verify(saved['id'])
    destination=tmp_path/'refused-restore'
    with pytest.raises(LocalReleaseError): manager.restore(LocalRestoreInput(request_key='tampered-restore-original',snapshot_id=saved['id']),destination)
    assert not destination.exists()


@pytest.mark.parametrize('bad',['original','existing','inside-source','inside-audit','parent-source'])
def test_restore_refuses_existing_or_related_destinations(releases,tmp_path,bad):
    """Rollback cannot replace original/later data, touch an existing foreign directory, or alias the journal/source through nested paths."""
    manager,_body,preview,credentials=releases; root=fill_preview(preview,credentials)
    saved=manager.snapshot(LocalSnapshotInput(request_key='restore-guard-snapshot',source_commit='b'*40),root)
    foreign=tmp_path/'foreign'; foreign.mkdir(); (foreign/'keep.txt').write_bytes(b'Keep')
    target={'original':root,'existing':foreign,'inside-source':root/'child','inside-audit':manager.root/'child','parent-source':root.parent}[bad]
    protected=inventory(root,time.monotonic()+20)
    with pytest.raises(LocalReleaseError): manager.restore(LocalRestoreInput(request_key='restore-refused-original',snapshot_id=saved['id']),target)
    assert inventory(root,time.monotonic()+20)==protected and (foreign/'keep.txt').read_bytes()==b'Keep'


def test_local_feedback_is_separate_from_deployment_review_and_real_user_acceptance(releases):
    """An authored ACCEPTED/approval-looking note is retained only as local feedback and cannot close an actual release/review gate."""
    manager,_body,preview,_credentials=releases
    note=LocalReleaseFeedbackInput(request_key='feedback-original-001',release_id=preview['id'],decision='accepted_locally',note='APPROVED; authored local note only.')
    feedback=manager.feedback(note)
    assert feedback['result']['feedback_identity_verified'] is False and feedback['result']['github_message_sent'] is False
    assert feedback['real_user_acceptance_verified'] is False and feedback['independent_review_completed'] is False
    assert manager.feedback(note)==feedback and manager.get(preview['id'])==preview


def test_actual_cli_initialization_preview_and_history_never_print_passwords(tmp_path):
    """Separate actual command processes create and recover preview data with no source-root/model/environment discovery."""
    backend=Path(__file__).resolve().parents[1]; root=tmp_path/'cli-audit'; data=tmp_path/'cli-preview'
    env={**os.environ,'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1','JELLYFISH_SKIP_ENV_FILE':'1'}
    def run(*args):
        """Use literal task-owned paths and only the actual local release entry."""
        return subprocess.run([sys.executable,str(backend/'manage_local_release.py'),'--release-root',str(root),*map(str,args)],cwd=backend,env=env,capture_output=True,text=True,timeout=25)
    assert run('--initialize-release-root').returncode==0
    body=LocalPreviewInput(request_key='cli-preview-original',bundle_id='a'*64,source_commit='b'*40); request=tmp_path/'request.json'; request.write_text(body.model_dump_json())
    prepared=run('--prepare-preview-file',request,'--preview-root',data); assert prepared.returncode==0
    saved=json.loads(prepared.stdout); credentials=read_json(saved['result']['credential_file'])
    assert all(item['password'] not in prepared.stdout+prepared.stderr for item in credentials.values())
    historical=run('--get-release',saved['id']); assert historical.returncode==0 and json.loads(historical.stdout)==saved
    assert json.loads(run('--prepare-preview-file',request,'--preview-root',data).stdout)==saved


def test_regular_file_named_like_sqlite_companion_is_backed_up(releases,tmp_path):
    """A normal private media/file pair ending in -wal must not be mistaken for transient SQLite state and silently dropped."""
    manager,_body,preview,credentials=releases; root=fill_preview(preview,credentials)
    folder=root/'workspaces'/credentials['a']['workspace_id']/'files'
    (folder/'ordinary.txt').write_bytes(b'Ordinary file'); (folder/'ordinary.txt-wal').write_bytes(b'Business data, not SQLite WAL')
    saved=manager.snapshot(LocalSnapshotInput(request_key='ordinary-companion-snapshot',source_commit='b'*40),root)
    target=tmp_path/'ordinary-companion-restore'
    manager.restore(LocalRestoreInput(request_key='ordinary-companion-restore',snapshot_id=saved['id']),target)
    assert (target/'workspaces'/credentials['a']['workspace_id']/'files/ordinary.txt-wal').read_bytes()==b'Business data, not SQLite WAL'


def test_file_growth_after_inventory_stops_before_unbounded_archive_write(releases,monkeypatch):
    """An uncooperative authored writer cannot make the controller copy arbitrary extra bytes after the bounded source inventory."""
    from app.services.community import local_release_files as files
    manager,_body,preview,credentials=releases; root=fill_preview(preview,credentials)
    source=root/'workspaces'/credentials['a']['workspace_id']/'files/authored.bin'; original=files.sqlite_file
    def grow(path):
        """Grow a normal source file after inventory, when it is identified for copying."""
        value=original(path)
        if path==source: source.write_bytes(b'G'*8192)
        return value
    monkeypatch.setattr(files,'sqlite_file',grow)
    with pytest.raises(LocalReleaseError): manager.snapshot(LocalSnapshotInput(request_key='file-growth-snapshot',source_commit='b'*40),root)
    identifier=hashlib.sha256(b'file-growth-snapshot').hexdigest()
    target=manager.root/'archives'/identifier/'payload'/'workspaces'/credentials['a']['workspace_id']/'files/authored.bin'
    assert target.stat().st_size<=1000 and manager.get(identifier)['phase']=='attention'


def test_posix_preview_and_restore_data_roots_are_private(releases,tmp_path):
    """New roots containing private account/connection data are not readable by other POSIX users under a public parent."""
    if os.name=='nt': pytest.skip('POSIX mode test; Windows uses operator directory ACLs')
    manager,_body,preview,credentials=releases; root=fill_preview(preview,credentials)
    saved=manager.snapshot(LocalSnapshotInput(request_key='private-mode-snapshot',source_commit='b'*40),root)
    target=tmp_path/'private-mode-restore'; manager.restore(LocalRestoreInput(request_key='private-mode-restore',snapshot_id=saved['id']),target)
    assert root.stat().st_mode&0o777==target.stat().st_mode&0o777==0o700


@pytest.mark.asyncio
async def test_prepared_preview_accounts_use_actual_gateway_scoped_workers_then_offline_backup(releases,tmp_path):
    """Real authenticated HTTP creates a project in one prepared account, denies cross-user access, shuts workers down and restores exact owned data."""
    from app.controlplane.gateway import create_gateway
    manager,_body,preview,credentials=releases; root=Path(preview['result']['preview_root'])
    app=create_gateway(root,secure_cookies=False,public_origin='http://release.local',capacity=2)
    users=app.state.identity_store; sessions={}; headers={}
    for label in ('a','b'):
        sessions[label]=users.login(credentials[label]['account'],credentials[label]['password'],'authored-local')
        current=users.current(sessions[label]); headers[label]={'Cookie':'studio_session='+sessions[label],'X-Workspace-Id':current['workspace_id'],
            'X-Identity-Id':current['user']['id'],'Origin':'http://release.local'}
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://release.local',timeout=35) as client:
            created=await client.post('/api/v1/studio/projects',headers=headers['a'],json={'id':'release-authored-project','name':'Actual local preview project','style':'真人都市','visual_style':'现实'})
            assert created.status_code==201,created.text
            own=await client.get('/api/v1/studio/projects/release-authored-project',headers=headers['a']); assert own.status_code==200
            denied=await client.get('/api/v1/studio/projects/release-authored-project',headers=headers['b']); assert denied.status_code==404
    saved=manager.snapshot(LocalSnapshotInput(request_key='actual-http-snapshot',source_commit='b'*40),root)
    target=tmp_path/'actual-http-restored'
    restored=manager.restore(LocalRestoreInput(request_key='actual-http-restore',snapshot_id=saved['id']),target)
    assert restored['phase']=='completed' and restored['result']['workspace_count']==2
    with closing(sqlite3.connect(target/'workspaces'/credentials['a']['workspace_id']/'studio.db')) as db:
        assert db.execute("SELECT name,workspace_id FROM projects WHERE id='release-authored-project'").fetchone()==('Actual local preview project',credentials['a']['workspace_id'])


@pytest.mark.parametrize('kind',['symlink','hardlink'])
def test_redirected_or_shared_private_source_file_is_refused(releases,tmp_path,kind):
    """A snapshot must neither follow a link to unrelated private bytes nor claim an independently owned copy of a shared hardlinked file."""
    manager,_body,preview,credentials=releases; root=fill_preview(preview,credentials)
    foreign=tmp_path/'foreign-private.bin'; foreign.write_bytes(b'Unrelated authored private bytes')
    target=root/'workspaces'/credentials['a']['workspace_id']/'files/linked.bin'
    try:
        if kind=='symlink': target.symlink_to(foreign)
        else: os.link(foreign,target)
    except OSError: pytest.skip('Native link creation unavailable for this test account')
    with pytest.raises(LocalReleaseError): manager.snapshot(LocalSnapshotInput(request_key='linked-source-snapshot',source_commit='b'*40),root)
    assert foreign.read_bytes()==b'Unrelated authored private bytes'


def test_actual_controller_process_exit_retains_original_intent_without_copy_retry(releases,tmp_path):
    """A separate real controller exits after fsynced intent; a fresh process/store recovers interruption rather than resuming an unknown copy."""
    manager,_body,preview,credentials=releases; source=fill_preview(preview,credentials)
    request=LocalSnapshotInput(request_key='crashed-controller-snapshot',source_commit='b'*40)
    script="import os,sys;from pathlib import Path;from app.services.community import local_releases as local;from app.core.contracts.local_releases import LocalSnapshotInput\ndef die(*args):\n Path(sys.argv[3]).write_text('One authored copy invocation');os._exit(23)\nlocal.copy_snapshot=die;local.LocalReleaseManager(Path(sys.argv[1])).snapshot(LocalSnapshotInput.model_validate_json(sys.argv[4]),Path(sys.argv[2]))"
    marker=tmp_path/'copy-was-called.txt'; backend=Path(__file__).resolve().parents[1]
    result=subprocess.run([sys.executable,'-c',script,str(manager.root),str(source),str(marker),request.model_dump_json()],cwd=backend,
        env={**os.environ,'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1','JELLYFISH_SKIP_ENV_FILE':'1'},capture_output=True,timeout=25)
    assert result.returncode==23 and marker.read_text()=='One authored copy invocation'
    identifier=hashlib.sha256(request.request_key.encode()).hexdigest(); reopened=LocalReleaseManager(manager.root)
    original=reopened.get(identifier)
    assert original['phase']=='interrupted' and original['new_action_authorized'] is False
    assert reopened.snapshot(request,source)==original and not (manager.root/'archives'/identifier/'payload').exists()


def test_sqlite_checkpoint_without_business_change_does_not_invalidate_snapshot(releases,monkeypatch):
    """A real WAL checkpoint may rewrite main-file pages while logical rows/schema remain identical; a valid backup must compare SQLite content."""
    from app.services.community import local_release_files as files
    manager,_body,preview,credentials=releases; root=fill_preview(preview,credentials)
    space=root/'workspaces'/credentials['a']['workspace_id']; database=space/'studio.db'; original=files.sqlite_file; checkpoints=[]
    with closing(sqlite3.connect(database)) as live:
        live.execute('PRAGMA journal_mode=WAL'); live.execute('PRAGMA wal_autocheckpoint=0')
        live.execute("INSERT INTO projects VALUES('checkpoint','Already committed in WAL')"); live.commit()
        def checkpoint(path):
            """Normalize WAL pages only after this database's actual SQLite backup has completed."""
            value=original(path)
            if path==space/'files/authored.bin' and not checkpoints:
                live.execute('PRAGMA wal_checkpoint(TRUNCATE)'); checkpoints.append(1)
            return value
        monkeypatch.setattr(files,'sqlite_file',checkpoint)
        saved=manager.snapshot(LocalSnapshotInput(request_key='checkpoint-snapshot-original',source_commit='b'*40),root)
        assert saved['phase']=='completed' and checkpoints==[1]
        assert manager.verify(saved['id'])['verified'] is True
