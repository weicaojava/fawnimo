"""Trusted finite helper actions on controller-owned named volumes; no host paths, ambient credentials or arbitrary commands."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import time

from app.controlplane.store import IdentityStore
from app.services.community.local_release_files import (
    copy_snapshot, restore_snapshot, verify_snapshot, read_json, write_json,
    plain, file_hash, database_receipt, flush_copy,
)
from app.services.community.runtime_release_safety import inspect_idle_gateway, checked_file, digest
from app.services.community.runtime_release_store import RuntimeReleaseError

PORTABLE = ('topology_sha256', 'user_ids_sha256', 'control_schema_sha256', 'financial_security_sha256')


def check_frame(value):
    """Validate the host controller's finite fixed helper envelope before opening a business root."""
    from local_runtime_bootstrap import configuration
    if (not isinstance(value, dict) or set(value) != {'profile', 'action', 'operation_id', 'config', 'bootstrap_sha256', 'target_ip', 'archive_manifest_sha256'}
            or value['profile'] != 'local_runtime_release_v1'
            or value['action'] not in {'bootstrap', 'initialize', 'snapshot', 'restore', 'probe'}
            or re.fullmatch(r'[a-f0-9]{64}', value['operation_id']) is None
            or re.fullmatch(r'[a-f0-9]{64}', value['bootstrap_sha256']) is None):
        raise RuntimeReleaseError('Unknown fixed runtime helper request')
    config = configuration(json.dumps(value['config']).encode())
    if 'target_ip' in config: raise RuntimeReleaseError('Helper requires an own app configuration')
    if value['action'] == 'probe': configuration(json.dumps({key: item for key, item in config.items() if key != 'ready_token'} | {'target_ip': value['target_ip']}).encode())
    elif value['target_ip'] is not None: raise RuntimeReleaseError('Offline helper cannot receive a network target')
    if value['action'] == 'restore':
        if not isinstance(value['archive_manifest_sha256'], str) or re.fullmatch(r'[a-f0-9]{64}', value['archive_manifest_sha256']) is None:
            raise RuntimeReleaseError('Restore requires its original immutable archive manifest identity')
    elif value['archive_manifest_sha256'] is not None: raise RuntimeReleaseError('Unexpected archive identity on a different helper action')
    return value


def install_bootstrap(target, expected, deadline):
    """Copy exactly this installed public bootstrap to an empty code-only volume and verify its pinned bytes."""
    source = Path(__file__).resolve().parents[3] / 'local_runtime_bootstrap.py'
    if file_hash(source, deadline)['sha256'] != expected or any(target.iterdir()):
        raise RuntimeReleaseError('Trusted bootstrap identity or empty code volume differs')
    destination = target / 'runtime.py'
    with source.open('rb') as reader, destination.open('xb') as writer:
        shutil.copyfileobj(reader, writer, 65536)
        writer.flush(); os.fsync(writer.fileno())
    destination.chmod(0o444)
    if file_hash(destination, deadline)['sha256'] != expected: raise RuntimeReleaseError('Bootstrap bytes changed')
    return {'bootstrap_sha256': expected, 'public_code_only': True}


def initialize_data(target, config, operation, deadline):
    """Create fresh independent A/B local accounts in a new slot; never import users, models or data from another environment."""
    if any(target.iterdir()): raise RuntimeReleaseError('New local slot requires a fresh empty volume')
    if os.name != 'nt': target.chmod(0o700)
    root = target / 'studio'
    root.mkdir(mode=0o700)
    identity = IdentityStore(root)
    credentials = {}
    for label in ('a', 'b'):
        password = secrets.token_urlsafe(32)
        account = identity.provision('local.' + label + '.' + operation[:16], password)
        credentials[label] = {**account, 'password': password}
    write_json(target / 'claims.json', credentials)
    write_json(target / 'runtime.json', config)
    if os.name != 'nt':
        for name in ('claims.json', 'runtime.json'): (target / name).chmod(0o600)
    write_baseline(target, deadline)
    flush_copy(target, deadline)
    return {'account_count': 2, 'source_credentials_copied': False, 'model_connections_copied': False,
            'safety': inspect_idle_gateway(root), 'private_claims_retained': True}


def snapshot_data(source, target, deadline):
    """Inspect a stopped owned whole-root and seal a verified private full SQLite/WAL/media archive in a new volume."""
    if any(target.iterdir()): raise RuntimeReleaseError('Snapshot requires a fresh empty archive volume')
    if os.name != 'nt': target.chmod(0o700)
    safety = inspect_idle_gateway(source / 'studio')
    manifest = copy_snapshot(source / 'studio', target / 'payload', deadline)
    write_json(target / 'manifest.json', manifest)
    archived = inspect_idle_gateway(target / 'payload')
    if any(archived[name] != safety[name] for name in PORTABLE + ('current_control_sha256',)):
        raise RuntimeReleaseError('Archived content differs from the stopped source')
    write_json(target / 'safety.json', archived)
    flush_copy(target, deadline)
    return {'manifest_sha256': file_hash(target / 'manifest.json', deadline)['sha256'],
            'safety': archived, 'file_count': len(manifest['files']), 'database_count': len(manifest['databases'])}


def carry_security(current, destination, deadline):
    """Keep current sessions/passwords/trials/authorization and signing-key rotation in the new restored copy only."""
    original = checked_file(current / 'control.db')
    before = database_receipt(original, deadline)
    target = checked_file(destination / 'control.db')
    # Only the new, independently restored copy is replaced. No current root,
    # retained data volume or immutable archive is opened for writing.
    target.unlink()
    with closing(sqlite3.connect(original.as_uri() + '?mode=ro', uri=True, timeout=1.5)) as reader, closing(sqlite3.connect(target)) as writer:
        reader.backup(writer, pages=128, sleep=.01)
    if database_receipt(target, deadline) != before or database_receipt(original, deadline) != before:
        raise RuntimeReleaseError('Current control security state changed during carry-forward')
    old_key = current / '.reference-delivery.key'
    new_key = destination / '.reference-delivery.key'
    if new_key.exists(): checked_file(new_key).unlink()
    key_present = old_key.exists()
    if key_present:
        content = checked_file(old_key).read_bytes()
        if len(content) != 32: raise RuntimeReleaseError('Unsupported current reference signing key')
        descriptor = os.open(new_key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
    return {'current_control_sha256': digest(before), 'current_reference_key_presence_preserved': key_present,
            'current_security_carried_forward': True, 'old_snapshot_security_reactivated': False}


def restore_data(source, current, target, config, deadline):
    """Restore same-slot ordinary business data to a fresh volume, refusing any changed monetary/model/identity topology history."""
    if any(target.iterdir()): raise RuntimeReleaseError('Restore requires a fresh empty data volume')
    if os.name != 'nt': target.chmod(0o700)
    manifest = read_json(source / 'manifest.json')
    verify_snapshot(source / 'payload', manifest, deadline)
    archived = inspect_idle_gateway(source / 'payload')
    if archived != read_json(source / 'safety.json'):
        # storage_rechecks is observational, not content. Linux archive helpers
        # normally report zero, but do not treat a Windows recheck as new data.
        stored = read_json(source / 'safety.json')
        if any(archived.get(name) != stored.get(name) for name in PORTABLE + ('current_control_sha256',)):
            raise RuntimeReleaseError('Archive safety fingerprint changed')
    latest = inspect_idle_gateway(current / 'studio')
    if any(archived[name] != latest[name] for name in PORTABLE):
        raise RuntimeReleaseError('Current finances, model policy or identity topology differs; old-data rollback refused')
    result = restore_snapshot(source / 'payload', manifest, target / 'studio', deadline)
    security = carry_security(current / 'studio', target / 'studio', deadline)
    claims = checked_file(current / 'claims.json')
    if claims.stat().st_size > 8192: raise RuntimeReleaseError('Private local claims exceed their fixed bound')
    write_json(target / 'claims.json', read_json(claims))
    write_json(target / 'runtime.json', config)
    if os.name != 'nt':
        for name in ('claims.json', 'runtime.json'): (target / name).chmod(0o600)
    final = inspect_idle_gateway(target / 'studio')
    if any(final[name] != latest[name] for name in PORTABLE + ('current_control_sha256',)):
        raise RuntimeReleaseError('Restored current security state could not be verified')
    write_baseline(target, deadline)
    flush_copy(target, deadline)
    return {**result, **security, 'safety': final, 'whole_root_equals_old_snapshot': False,
            'later_data_retained_in_stopped_source': True}


def write_baseline(target, deadline):
    """Seal original business columns/rows and non-database bytes for the selected image's actual native-worker startup check."""
    from app.core.workspace_boundary import INFRASTRUCTURE_TABLES
    from app.services.community.local_release_files import inventory, sqlite_file
    root = target / 'studio'
    identity = IdentityStore(root)
    with identity.connect() as db: workspaces = [row[0] for row in db.execute('SELECT id FROM workspaces ORDER BY id')]
    if len(workspaces) > 4: raise RuntimeReleaseError('Local runtime supports at most four existing personal spaces')
    databases = {}
    for workspace in workspaces:
        path = root / 'workspaces' / workspace / 'studio.db'
        if path.exists():
            receipt = database_receipt(path, deadline)
            databases[workspace] = {name: row for name, row in receipt['tables'].items()
                                    if name not in INFRASTRUCTURE_TABLES and not name.startswith('sqlite_')}
    files = {name: row for name, row in inventory(root, deadline).items() if not sqlite_file(root / name)}
    write_json(target / 'runtime-baseline.json', {'profile': 'local_runtime_baseline_v1', 'workspaces': workspaces,
                                                'databases': databases, 'files': files})


def probe_data(source, value):
    """Observe only the fixed private app with its own secret; no public user password is required or serialized."""
    import httpx
    config = read_json(checked_file(source / 'runtime.json'))
    if config != value['config']: raise RuntimeReleaseError('Private app configuration differs from the original generation')
    from local_runtime_bootstrap import READY_PATH
    deadline = time.monotonic() + 30
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=2) as client:
        for _ in range(100):
            try:
                result = client.get('http://' + value['target_ip'] + ':8000' + READY_PATH,
                                    headers={'x-fawnimo-private-runtime-ready': config['ready_token']})
                expected = {'profile': 'local_runtime_release_v1', 'slot': config['slot'], 'ready': True, 'paid_generation_enabled': False}
                if result.status_code == 200 and len(result.content) <= 4096 and result.json() == expected:
                    return {'private_readiness_verified': True, 'slot': config['slot'], 'paid_generation_enabled': False}
            except (httpx.HTTPError, ValueError): pass
            if time.monotonic() >= deadline: break
            time.sleep(.25)
    raise RuntimeReleaseError('Selected app did not satisfy its private readiness protocol')


def execute(value, *, source=Path('/source'), current=Path('/current'), target=Path('/target')):
    """Dispatch a single immutable helper action; path injection is trusted test embedding only, never request JSON."""
    value = check_frame(value)
    deadline = time.monotonic() + 120
    action = value['action']
    if action == 'bootstrap': result = install_bootstrap(plain(target), value['bootstrap_sha256'], deadline)
    elif action == 'initialize': result = initialize_data(plain(target), value['config'], value['operation_id'], deadline)
    elif action == 'snapshot': result = snapshot_data(plain(source), plain(target), deadline)
    elif action == 'restore':
        if file_hash(plain(source) / 'manifest.json', deadline)['sha256'] != value['archive_manifest_sha256']:
            raise RuntimeReleaseError('Original sealed archive manifest changed')
        result = restore_data(plain(source), plain(current), plain(target), value['config'], deadline)
    else: result = probe_data(plain(source), value)
    return {'profile': 'local_runtime_helper_v1', 'operation_id': value['operation_id'], 'action': action,
            'ok': True, 'result': result, 'external_model_calls': 0, 'paid_authorization': 0}
