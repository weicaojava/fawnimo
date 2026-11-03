"""Real stopped-root snapshots and current-security carry-forward, using only new authored identity fixtures."""
import json
from pathlib import Path
import time

import pytest

from app.controlplane.store import IdentityStore
from app.services.community.local_release_files import database_receipt, inventory, read_json
from app.services.community.runtime_release_helper import initialize_data, snapshot_data, restore_data, check_frame, install_bootstrap
from app.services.community.runtime_release_store import RuntimeReleaseError
from app.services.community.runtime_release_safety import inspect_idle_gateway, financial_tables


def config(slot='stable'):
    """Create private synthetic app configuration without using existing accounts or secrets."""
    return {'profile': 'local_runtime_release_v1', 'slot': slot, 'origin': 'http://127.0.0.1:18080' if slot == 'stable' else 'http://127.0.0.2:18081',
            'ready_token': 'a' * 64}


def fresh_data(tmp_path, name='current', slot='stable'):
    """Prepare one new own two-account root, including an ordinary file subtree for later-edit rollback checks."""
    target = tmp_path / name
    target.mkdir(mode=0o700)
    initialize_data(target, config(slot), 'b' * 64, time.monotonic() + 120)
    claims = read_json(target / 'claims.json')
    workspace = claims['a']['workspace_id']
    media = target / 'studio' / 'workspaces' / workspace / 'files'
    # A supported worker owns the actual studio schema; this fixture keeps
    # both spaces uninitialized and adds its ordinary file after snapshot.
    return target, claims, media


def test_new_environments_have_independent_accounts_credentials_and_data(tmp_path):
    """First stable/preview preparation never clones one slot's user IDs, passwords, workspaces or model connections into the other."""
    stable, first, _ = fresh_data(tmp_path)
    preview, second, _ = fresh_data(tmp_path, 'preview', 'preview')
    assert {item['user_id'] for item in first.values()}.isdisjoint({item['user_id'] for item in second.values()})
    assert {item['workspace_id'] for item in first.values()}.isdisjoint({item['workspace_id'] for item in second.values()})
    assert {item['password'] for item in first.values()}.isdisjoint({item['password'] for item in second.values()})
    assert inspect_idle_gateway(stable / 'studio')['workspace_count'] == 2
    assert inspect_idle_gateway(preview / 'studio')['workspace_count'] == 2
    with pytest.raises(RuntimeReleaseError, match='empty'): initialize_data(stable, config(), 'b' * 64, time.monotonic() + 120)


def test_real_snapshot_restore_keeps_current_session_revocation_profile_and_key_rotation(tmp_path):
    """Ordinary business rollback cannot revive old login sessions or old signing keys, or overwrite later current/archived data."""
    current, claims, _ = fresh_data(tmp_path)
    store = IdentityStore(current / 'studio')
    account = claims['a']
    old_session = store.login(account['account'], account['password'], 'authored-local-peer')
    old_key = bytes(range(32))
    (current / 'studio' / '.reference-delivery.key').write_bytes(old_key)
    archive = tmp_path / 'archive'
    archive.mkdir(mode=0o700)
    snapshot = snapshot_data(current, archive, time.monotonic() + 120)
    store.logout(old_session)
    new_session = store.login(account['account'], account['password'], 'authored-local-peer')
    store.profile(new_session, {'display_name': 'Current security profile'})
    new_key = bytes(reversed(range(32)))
    (current / 'studio' / '.reference-delivery.key').write_bytes(new_key)
    current_receipt = database_receipt(current / 'studio' / 'control.db', time.monotonic() + 120)
    before_current = inventory(current, time.monotonic() + 120)
    before_archive = inventory(archive, time.monotonic() + 120)
    restored = tmp_path / 'new-restored'
    restored.mkdir(mode=0o700)
    proof = restore_data(archive, current, restored, config(), time.monotonic() + 120)
    assert proof['current_security_carried_forward'] is True
    assert proof['old_snapshot_security_reactivated'] is False
    assert proof['whole_root_equals_old_snapshot'] is False
    assert database_receipt(restored / 'studio' / 'control.db', time.monotonic() + 120) == current_receipt
    result_store = IdentityStore(restored / 'studio')
    with pytest.raises(Exception): result_store.current(old_session)
    assert result_store.current(new_session)['user']['display_name'] == 'Current security profile'
    assert (restored / 'studio' / '.reference-delivery.key').read_bytes() == new_key
    assert inventory(current, time.monotonic() + 120) == before_current
    assert inventory(archive, time.monotonic() + 120) == before_archive
    assert snapshot['safety']['financial_security_sha256'] == proof['safety']['financial_security_sha256']
    public = json.dumps(proof)
    assert account['password'] not in public and new_session not in public


def test_absent_current_key_does_not_restore_old_signatures(tmp_path):
    """Deleting/rotating away the current signing key is preserved even when an older snapshot contains one."""
    current, _, _ = fresh_data(tmp_path)
    (current / 'studio' / '.reference-delivery.key').write_bytes(b'x' * 32)
    archive = tmp_path / 'archive'
    archive.mkdir(mode=0o700)
    snapshot_data(current, archive, time.monotonic() + 120)
    (current / 'studio' / '.reference-delivery.key').unlink()
    target = tmp_path / 'target'
    target.mkdir(mode=0o700)
    restore_data(archive, current, target, config(), time.monotonic() + 120)
    assert not (target / 'studio' / '.reference-delivery.key').exists()


@pytest.mark.parametrize('change', ['topology', 'private_model_config', 'archive_bytes'])
def test_changed_identity_policy_or_archive_refuses_before_creating_business_root(tmp_path, change):
    """A new volume cannot hide changed identities/models or accept a corrupted older archive as a rollback source."""
    current, _, _ = fresh_data(tmp_path)
    archive = tmp_path / 'archive'
    archive.mkdir(mode=0o700)
    snapshot_data(current, archive, time.monotonic() + 120)
    if change == 'topology': IdentityStore(current / 'studio').provision('new-authored-c', 'new-authored-password-c')
    elif change == 'private_model_config': (current / 'studio' / 'private-model-policy.json').write_text('{"authored_policy":true}')
    else: (archive / 'payload' / '.reference-delivery.key').write_bytes(b'y' * 32)
    target = tmp_path / 'target'
    target.mkdir(mode=0o700)
    with pytest.raises(ValueError): restore_data(archive, current, target, config(), time.monotonic() + 120)
    assert not (target / 'studio').exists()


def test_financial_fingerprint_includes_actual_identity_and_trial_execution_lock_tables():
    """The actual marker/quota table names are protected, rather than only a conceptual boundary name."""
    tables = {'workspace_identity': {'sha': 'a'}, 'workspace_trial_quota': {'sha': 'b'}, 'execution_budgets': {'sha': 'c'}, 'projects': {'sha': 'd'}}
    assert set(financial_tables(tables)) == {'workspace_identity', 'workspace_trial_quota', 'execution_budgets'}


def test_bootstrap_is_pinned_public_code_only_and_fixed_helper_frames_reject_paths(tmp_path):
    """Only this installed single public bootstrap is copied; helper frames cannot choose an arbitrary source, mount or command."""
    import hashlib
    import local_runtime_bootstrap
    source = Path(local_runtime_bootstrap.__file__)
    fingerprint = hashlib.sha256(source.read_bytes()).hexdigest()
    target = tmp_path / 'code'
    target.mkdir()
    result = install_bootstrap(target, fingerprint, time.monotonic() + 120)
    assert result == {'bootstrap_sha256': fingerprint, 'public_code_only': True}
    assert (target / 'runtime.py').read_bytes() == source.read_bytes()
    frame = {'profile': 'local_runtime_release_v1', 'action': 'initialize', 'operation_id': 'c' * 64,
             'config': config(), 'bootstrap_sha256': fingerprint, 'target_ip': None, 'archive_manifest_sha256': None}
    check_frame(frame)
    for extra in ({'source': '/existing-host-data'}, {'command': ['python', 'other.py']}, {'action': 'shell'}, {'target_ip': '8.8.8.8'}):
        with pytest.raises(ValueError): check_frame(frame | extra)
