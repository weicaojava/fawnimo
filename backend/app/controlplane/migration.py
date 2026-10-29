"""Offline, copy-only legacy staging, explicit owner claim and independent-path restore.

Bundles are private operator artifacts and can contain model credentials and
personal media. No HTTP endpoint or first-registration path can claim one.
"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import uuid

from app.controlplane.maintenance import ProcessLease
from app.core.workspace_boundary import claim_legacy_workspace, prepare_workspace_boundary, assert_workspace_boundary, INFRASTRUCTURE_TABLES
from app.controlplane.workers import worker_environment


def digest(path: Path) -> str:
    """Hash actual file bytes using a bounded streaming reader."""
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def identifier(value: str) -> str:
    """Quote inspected SQLite names instead of interpreting table or column names as SQL."""
    return '"'+value.replace('"','""')+'"'


def plain_path(path: Path, *, exists=True) -> Path:
    """Reject redirects, including Windows junctions, before reading or publishing private files."""
    path = Path(path).absolute()
    if path.resolve() != path or path.is_symlink() or (path.exists() and path.stat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT if os.name == 'nt' else False):
        raise ValueError('Migration paths cannot contain links or junctions')
    if exists and not path.exists():
        raise ValueError('Migration source is missing')
    return path


def tree(root: Path) -> list[dict]:
    """Inventory complete local roots, rejecting unsafe aliases and nonregular filesystem entries."""
    root = plain_path(root)
    if not root.is_dir():
        raise ValueError('Migration file root must be a directory')
    result = []
    for current, directories, files in os.walk(root, followlinks=False):
        for name in sorted(directories+files):
            path = plain_path(Path(current)/name)
            if not path.is_relative_to(root) or name.endswith((' ','.')) or ':' in name or '\\' in name:
                raise ValueError('Unsafe local migration entry')
            if path.is_dir():
                continue
            if not path.is_file():
                raise ValueError('Migration supports only regular files')
            result.append({'path':path.relative_to(root).as_posix(),'size':path.stat().st_size,'sha256':digest(path)})
            if len(result)>100000:
                raise ValueError('Migration file count exceeds the supported bound')
    return sorted(result,key=lambda row:row['path'])


def table_receipts(db, expected: dict | None = None, *, renames: dict | None = None) -> dict:
    """Fingerprint original column values without exposing their credentials or private text.

    During claim verification new owner/migration columns are ignored; original
    rows and original columns must still be byte-for-byte logically equivalent.
    """
    tables = expected or {row[0]:None for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")}
    result = {}
    for name, prior in tables.items():
        columns = prior['columns'] if prior else [row[1] for row in db.execute(f'PRAGMA table_info({identifier(name)})')]
        fields = ','.join(identifier(column) for column in columns)
        hasher, count = hashlib.sha256(), 0
        table = (renames or {}).get(name,name)
        for row in db.execute(f'SELECT {fields} FROM {identifier(table)} ORDER BY {fields}'):
            values = [('blob',value.hex()) if isinstance(value,bytes) else value for value in row]
            encoded = json.dumps(values,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
            hasher.update(len(encoded).to_bytes(8,'big')); hasher.update(encoded); count += 1
        result[name] = {'columns':columns,'rows':count,'sha256':hasher.hexdigest()}
    return result


def validate_database(path: Path, *, legacy=False, expected=None) -> dict:
    """Require SQLite/FK integrity and reject attempts to reassign already-owned data."""
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('Migration database integrity or references failed')
        if legacy:
            for name, in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                if name in {'workspace_identity','workspace_import'} or any(row[1]=='workspace_id' for row in db.execute(f'PRAGMA table_info({identifier(name)})')):
                    raise ValueError('Only unowned legacy databases can be staged')
        return table_receipts(db,expected)


def business_receipts(manifest: dict) -> dict:
    """Exclude service schema/lease initialization from preservation of original user business rows."""
    return {name:receipt for name,receipt in manifest['tables'].items() if name not in INFRASTRUCTURE_TABLES}


def verify_claim_payload(database: Path, directory: Path, manifest: dict) -> None:
    """Verify all original business values and media, including supported archive table renames."""
    expected = business_receipts(manifest)
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as db:
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        renames = {name:'retired_'+name for name in ('workspace_budget','workspace_budget_changes') if name in expected and name not in names and 'retired_'+name in names}
        if table_receipts(db,expected,renames=renames) != expected:
            raise ValueError('Schema upgrade changed original business values')
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('Claim database integrity failed')
    if {kind:tree(directory/kind) for kind in ('media','files')} != manifest['trees']:
        raise ValueError('Pending migration bytes changed')


def validate_file_references(database: Path, inventories: dict) -> None:
    """Require local bytes for every stored file and exact production size/hash; never fetch remote data."""
    lookup = {kind:{row['path']:row for row in rows} for kind,rows in inventories.items()}
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as db:
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'files' in names:
            for key, in db.execute('SELECT storage_key FROM files'):
                if not isinstance(key,str) or not key or key.startswith(('/', '\\')) or any(part in {'','.','..'} for part in key.split('/')):
                    raise ValueError('Legacy file has an unsupported local reference')
                kind, relative = ('media',key[17:]) if key.startswith('local-production/') else ('files',key)
                if relative not in lookup[kind]:
                    raise ValueError('Legacy file bytes are missing or require external storage')
        if 'production_media' in names:
            for relative,size,sha in db.execute('SELECT relative_path,size,sha256 FROM production_media'):
                actual = lookup['media'].get(relative)
                if actual is None or (actual['size'],actual['sha256']) != (size,sha):
                    raise ValueError('Production media size or digest does not match its immutable record')


def write_json(path: Path, value: dict) -> None:
    """Seal a complete new receipt without overwriting any previous operator artifact."""
    with path.open('x',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())


def stage_legacy(source_db: Path, media: Path, files: Path, destination: Path) -> dict:
    """Copy one stable source view into an unclaimed private bundle; never mutate the source."""
    source_db, media, files = plain_path(source_db), plain_path(media), plain_path(files)
    destination = plain_path(destination,exists=False)
    if destination.exists() or destination.is_relative_to(source_db.parent) or any(destination.is_relative_to(root) or root.is_relative_to(destination) for root in (media,files)) or media.is_relative_to(files) or files.is_relative_to(media):
        raise ValueError('Bundle destination must be new and independent from both file roots')
    destination.mkdir(parents=True,exist_ok=False,mode=0o700)
    # Private database/media artifacts must not be swept into an ordinary git add.
    (destination/'.gitignore').write_text('*\n',encoding='utf-8')
    with closing(sqlite3.connect(source_db.as_uri()+'?mode=ro',uri=True)) as monitor:
        version = monitor.execute('PRAGMA data_version').fetchone()[0]
        before = {'media':tree(media),'files':tree(files)}
        database = destination/'studio.db'
        with closing(sqlite3.connect(database)) as snapshot:
            monitor.backup(snapshot)
        receipts = validate_database(database,legacy=True)
        for kind, root in (('media',media),('files',files)):
            shutil.copytree(root,destination/kind,symlinks=False)
        copied = {kind:tree(destination/kind) for kind in ('media','files')}
        after = {'media':tree(media),'files':tree(files)}
        if before != copied or before != after or monitor.execute('PRAGMA data_version').fetchone()[0] != version:
            raise ValueError('Source changed during staging; no claimable manifest was published')
        if validate_database(source_db,legacy=True) != receipts:
            raise ValueError('Source rows changed during staging')
        if monitor.execute('PRAGMA data_version').fetchone()[0] != version:
            raise ValueError('Source changed during final staging verification')
    validate_file_references(database,copied)
    manifest = {'schema_version':1,'import_id':'imp_'+uuid.uuid4().hex,'created_at':datetime.now(timezone.utc).isoformat(),
                'database_sha256':digest(database),'tables':receipts,'trees':copied,'runtime_locked':True,
                'source_paths':{'database':str(source_db),'media':str(media),'files':str(files)}}
    write_json(destination/'manifest.json',manifest)
    return {'import_id':manifest['import_id'],'tables':len(receipts),'files':sum(len(rows) for rows in copied.values()),'source_modified':False}


def verify_bundle(bundle: Path) -> tuple[dict,str]:
    """Recheck the sealed snapshot and every byte before claim or independent-path restore."""
    bundle = plain_path(bundle)
    manifest_path = plain_path(bundle/'manifest.json')
    manifest = json.loads(manifest_path.read_text('utf-8'))
    if manifest.get('schema_version') != 1 or not re.fullmatch(r'imp_[a-f0-9]{32}',manifest.get('import_id','')) or manifest.get('runtime_locked') is not True:
        raise ValueError('Unsupported migration bundle')
    database = plain_path(bundle/'studio.db')
    if digest(database) != manifest['database_sha256'] or validate_database(database,legacy=True) != manifest['tables']:
        raise ValueError('Bundle database changed')
    inventories = {kind:tree(bundle/kind) for kind in ('media','files')}
    if inventories != manifest['trees']:
        raise ValueError('Bundle file bytes changed')
    validate_file_references(bundle/'studio.db',inventories)
    return manifest,digest(manifest_path)


def copy_payload(bundle: Path, destination: Path) -> None:
    """Copy only the verified inert payload into a new path, never replace an existing directory."""
    destination.mkdir(parents=True,exist_ok=False,mode=0o700)
    (destination/'.gitignore').write_text('*\n',encoding='utf-8')
    shutil.copyfile(bundle/'studio.db',destination/'studio.db')
    for kind in ('media','files'):
        shutil.copytree(bundle/kind,destination/kind)


def flush_directory(path: Path) -> None:
    """Persist directory metadata where the platform exposes supported directory fsync."""
    if os.name != 'nt' and hasattr(os,'O_DIRECTORY'):
        descriptor = os.open(path,os.O_RDONLY|os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def flush_payload(directory: Path) -> None:
    """Flush owned copied files before publication; never open any original source for writing."""
    for root,_,files in os.walk(directory,topdown=False):
        for name in files:
            path = plain_path(Path(root)/name)
            with path.open('r+b') as stream:
                os.fsync(stream.fileno())
        flush_directory(Path(root))


def _control(root: Path):
    """Open an existing operator-selected control plane without provisioning an accidental new one."""
    from app.controlplane.store import IdentityStore
    if not (root/'control.db').is_file():
        raise ValueError('Existing identity control root required')
    return IdentityStore(root)


def migration_pending(workspaces: Path, workspace_id: str) -> bool:
    """Fence worker creation while a crash-interrupted claim awaits operator recovery."""
    control = workspaces.parent/'control.db'
    if not control.is_file():
        return False
    with closing(sqlite3.connect(control.as_uri()+'?mode=ro',uri=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='workspace_import_claims'").fetchone():
            return False
        return db.execute("SELECT 1 FROM workspace_import_claims WHERE workspace_id=? AND state!='complete'",(workspace_id,)).fetchone() is not None


def claim_bundle(bundle: Path, root: Path, account: str) -> dict:
    """Publish a verified copy only into an explicit owner's never-initialized personal space."""
    bundle, root = plain_path(bundle), plain_path(root)
    if bundle.is_relative_to(root) or root.is_relative_to(bundle):
        raise ValueError('Private bundle and identity roots must be independent')
    manifest, manifest_hash = verify_bundle(bundle)
    with ProcessLease(root/'.maintenance.lock'):
        store = _control(root)
        with store.connect() as db:
            owner = db.execute("""SELECT u.id,w.id FROM users u JOIN workspaces w ON w.owner_id=u.id
                JOIN memberships m ON m.user_id=u.id AND m.workspace_id=w.id
                WHERE u.account=? AND u.disabled=0 AND m.role='owner'""",(account.strip().lower(),)).fetchone()
            if owner is None:
                raise ValueError('Explicit active owner account required')
            user_id, workspace_id = owner
            db.executescript("""CREATE TABLE IF NOT EXISTS workspace_import_claims(
                workspace_id TEXT PRIMARY KEY REFERENCES workspaces(id),user_id TEXT NOT NULL REFERENCES users(id),
                import_id TEXT NOT NULL UNIQUE,manifest_sha256 TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('preparing','complete')));
                CREATE TRIGGER IF NOT EXISTS workspace_import_claims_identity BEFORE UPDATE OF workspace_id,user_id,import_id,manifest_sha256 ON workspace_import_claims
                BEGIN SELECT RAISE(ABORT,'Immutable import claim'); END;
                CREATE TRIGGER IF NOT EXISTS workspace_import_claims_no_delete BEFORE DELETE ON workspace_import_claims
                BEGIN SELECT RAISE(ABORT,'Immutable import claim'); END;""")
            prior = db.execute('SELECT * FROM workspace_import_claims WHERE workspace_id=?',(workspace_id,)).fetchone()
            if prior and (prior['user_id'],prior['import_id'],prior['manifest_sha256']) != (user_id,manifest['import_id'],manifest_hash):
                raise ValueError('Workspace is reserved by a different import')
        workspaces = root/'workspaces'
        workspaces.mkdir(exist_ok=True)
        target = plain_path(workspaces/workspace_id,exists=False)
        with ProcessLease(workspaces/'.leases'/(workspace_id+'.lock')):
            if prior and target.is_dir():
                with closing(sqlite3.connect((target/'studio.db').as_uri()+'?mode=ro',uri=True)) as db:
                    receipt = db.execute('SELECT import_id,manifest_sha256,runtime_locked FROM workspace_import WHERE id=1').fetchone()
                if receipt != (manifest['import_id'],manifest_hash,1):
                    raise ValueError('Published import identity does not match its claim')
                assert_workspace_boundary(target/'studio.db',workspace_id)
                if prior['state']=='preparing':
                    # Crash recovery must validate payload durability before
                    # opening the gateway fence. Completed replays retain edits.
                    verify_claim_payload(target/'studio.db',target,manifest)
            else:
                if target.exists() or (prior and prior['state']=='complete'):
                    raise ValueError('Destination must never have been initialized; existing data is not overwritten')
                if not prior:
                    with store.connect() as db:
                        db.execute('INSERT INTO workspace_import_claims VALUES(?,?,?,?,?)',(workspace_id,user_id,manifest['import_id'],manifest_hash,'preparing'))
                pending = workspaces/('.pending-'+manifest['import_id']+'-'+uuid.uuid4().hex)
                copy_payload(bundle,pending)
                database = pending/'studio.db'
                if digest(database) != manifest['database_sha256']:
                    raise ValueError('Copied database changed before binding')
                claim_legacy_workspace(database,workspace_id,expected_sha256=manifest['database_sha256'])
                with closing(sqlite3.connect(database)) as db:
                    db.execute(f"CREATE TABLE workspace_import(id INTEGER PRIMARY KEY CHECK(id=1),import_id TEXT NOT NULL,manifest_sha256 TEXT NOT NULL,runtime_locked INTEGER NOT NULL CHECK(runtime_locked=1),workspace_id TEXT NOT NULL DEFAULT '{workspace_id}' CHECK(workspace_id='{workspace_id}'))")
                    db.execute('INSERT INTO workspace_import(id,import_id,manifest_sha256,runtime_locked) VALUES(1,?,?,1)',(manifest['import_id'],manifest_hash))
                    for action in ('UPDATE','DELETE'):
                        db.execute(f"CREATE TRIGGER workspace_import_no_{action.lower()} BEFORE {action} ON workspace_import BEGIN SELECT RAISE(ABORT,'Immutable import receipt'); END")
                    db.commit()
                environment = worker_environment()
                environment.update(DATABASE_URL='sqlite+aiosqlite:///'+database.as_posix(),WORKSPACE_ID=workspace_id,WORKSPACE_RUNTIME_LOCKED='true')
                launcher = Path(__file__).resolve().parents[2]/'migrate_workspace.py'
                result = subprocess.run([sys.executable,str(launcher),'_upgrade'],env=environment,cwd=launcher.parent,capture_output=True,timeout=180)
                if result.returncode:
                    raise ValueError('Offline schema upgrade failed; source and bundle retained for inspection')
                prepare_workspace_boundary(database,workspace_id)
                assert_workspace_boundary(database,workspace_id)
                verify_claim_payload(database,pending,manifest)
                verify_bundle(bundle)
                if target.exists():
                    raise ValueError('Destination appeared during migration; publication refused')
                flush_payload(pending)
                pending.rename(target)
                flush_directory(workspaces)
            with store.connect() as db:
                db.execute("UPDATE workspace_import_claims SET state='complete' WHERE workspace_id=?",(workspace_id,))
    return {'import_id':manifest['import_id'],'workspace_id':workspace_id,'runtime_locked':True,'source_modified':False}


def restore_bundle(bundle: Path, destination: Path) -> dict:
    """Restore the original legacy snapshot at a new independent path, preserving all later edits."""
    bundle, destination = plain_path(bundle), plain_path(destination,exists=False)
    manifest, _ = verify_bundle(bundle)
    source_roots = [Path(manifest['source_paths']['database']).parent, Path(manifest['source_paths']['media']), Path(manifest['source_paths']['files'])]
    if destination.exists() or destination.is_relative_to(bundle) or bundle.is_relative_to(destination) or any(destination.is_relative_to(root) for root in source_roots):
        raise ValueError('Restore destination must be new and independent')
    copy_payload(bundle,destination)
    if digest(destination/'studio.db') != manifest['database_sha256'] or {kind:tree(destination/kind) for kind in ('media','files')} != manifest['trees']:
        raise ValueError('Restored payload does not match its source bundle')
    verify_bundle(bundle)
    flush_payload(destination)
    flush_directory(destination.parent)
    return {'import_id':manifest['import_id'],'restored':True,'services_started':False,'existing_data_overwritten':False}
