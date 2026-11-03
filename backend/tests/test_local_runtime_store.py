"""Real SQLite durability, original-key replay and physical row-id replacement guards for local runtime operations."""
import json
import sqlite3

import pytest

from app.core.contracts.local_runtime_release import LocalRuntimeActivation, LocalRuntimeStop, LocalRuntimePolicy
from app.services.community.runtime_release_store import RuntimeReleaseStore, RuntimeReleaseError, operation_id

IMAGE = 'sha256:' + '1' * 64
COMMIT = '2' * 40


def enabled_store(tmp_path):
    """Create an independent private operator journal; no Docker engine or application data is touched."""
    store = RuntimeReleaseStore(tmp_path / 'operator', initialize=True)
    policy = LocalRuntimePolicy(enabled=True, operator_allowed_images=({'image_id': IMAGE, 'source_commit': COMMIT},),
                                helper_image_id=IMAGE, bootstrap_sha256='3' * 64)
    store.set_policy(policy, expected_revision=1, request_key='enable-local-only')
    return store


def activate(key='activate-stable-01', generation=0, slot='stable'):
    """Bind one finite original request to a known generation and immutable image."""
    return LocalRuntimeActivation(request_key=key, slot=slot, expected_generation=generation, image_id=IMAGE, reason='authored local test')


def finish(store, body):
    """Exercise the trusted controller's atomic commit boundary without claiming actual container qualification."""
    identifier = operation_id(body.request_key)
    store.finish(identifier, {'operation_id': identifier, 'slot': body.slot, 'generation': body.expected_generation + 1,
                              'test_fixture_only': True})


def test_disabled_does_not_reserve_or_contact_an_engine(tmp_path):
    """Default initialization stays inert and does not consume capacity for denied activation."""
    store = RuntimeReleaseStore(tmp_path / 'operator', initialize=True)
    with pytest.raises(RuntimeReleaseError, match='disabled'): store.reserve('activate', activate())
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM engines').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM operations').fetchone()[0] == 0


def test_atomic_generations_old_replay_and_old_stop_cannot_stop_new_generation(tmp_path):
    """A repeated old operation returns history even after later activations and explicit disabling of today's policy."""
    store = enabled_store(tmp_path)
    first = activate()
    assert store.reserve('activate', first)[0]
    finish(store, first)
    stop = LocalRuntimeStop(request_key='stop-stable-once', slot='stable', expected_generation=1, reason='test stop')
    store.reserve('stop', stop)
    store.finish(operation_id(stop.request_key))
    second = activate('activate-stable-02', 1)
    store.reserve('activate', second)
    finish(store, second)
    store.set_policy(LocalRuntimePolicy(), expected_revision=2, request_key='disable-local-only')
    assert not store.reserve('activate', first)[0]
    assert not store.reserve('stop', stop)[0]
    assert store.slot('stable')['generation'] == 2
    assert store.slot('stable')['state'] == 'running'
    assert store.get(operation_id(stop.request_key))['phase'] == 'stopped'
    assert store.get(operation_id(first.request_key))['fresh_action_authorized'] is False
    reopened = RuntimeReleaseStore(store.root)
    assert reopened.slot('stable') == store.slot('stable')


def test_interrupted_intent_keeps_slot_reserved_and_policy_locked(tmp_path):
    """A process crash cannot let another original request repeat an unknown mutation or change the policy under it."""
    store = enabled_store(tmp_path)
    first = activate()
    store.reserve('activate', first)
    store.event(operation_id(first.request_key), 'create_requested', {'resource': 'new_owned_only'})
    reopened = RuntimeReleaseStore(store.root)
    with pytest.raises(RuntimeReleaseError, match='unfinished'): reopened.reserve('activate', activate('different-key-01'))
    with pytest.raises(RuntimeReleaseError, match='reconciled'):
        reopened.set_policy(LocalRuntimePolicy(), expected_revision=2, request_key='disable-during-unknown')
    assert not reopened.reserve('activate', first)[0]
    assert reopened.slot('stable')['state'] == 'empty'
    assert reopened.reserve('activate', activate('preview-separate-01', slot='preview'))[0]


def test_mismatched_replay_and_stale_generation_refused(tmp_path):
    """The original key binds every intent field, while a new key must observe the actual generation."""
    store = enabled_store(tmp_path)
    first = activate()
    store.reserve('activate', first)
    finish(store, first)
    with pytest.raises(RuntimeReleaseError, match='another input'):
        store.reserve('activate', first.model_copy(update={'reason': 'different'}))
    with pytest.raises(RuntimeReleaseError, match='generation changed'):
        store.reserve('activate', activate('stale-generation-01'))


def test_terminal_event_and_pointer_are_one_sqlite_transaction(tmp_path, monkeypatch):
    """Failure after inserting a new generation but before the pointer update rolls back both immutable rows and terminal event."""
    store = enabled_store(tmp_path)
    first = activate()
    store.reserve('activate', first)
    real = store._event

    def failed_event(db, identifier, phase, details):
        """Raise after the event is inserted, modeling a lost operator before the final SQLite commit."""
        real(db, identifier, phase, details)
        if phase == 'committed': raise RuntimeError('authored interruption')

    monkeypatch.setattr(store, '_event', failed_event)
    with pytest.raises(RuntimeError): finish(store, first)
    assert store.slot('stable')['generation'] == 0
    with store.connect() as db: assert db.execute('SELECT count(*) FROM generations').fetchone()[0] == 0
    assert store.get(operation_id(first.request_key))['phase'] == 'intent'


@pytest.mark.parametrize('table', ['policies', 'engines', 'operations', 'events', 'generations', 'archives', 'private_configs'])
def test_actual_rowid_replace_and_update_delete_guards(tmp_path, table):
    """Even INSERT OR REPLACE with a retained physical rowid cannot rewrite original history when recursive triggers are off."""
    store = enabled_store(tmp_path)
    body = activate()
    identifier = operation_id(body.request_key)
    store.reserve('activate', body)
    store.bind_engine({'endpoint_sha256': '4' * 64, 'engine_id': 'owned-fixture-engine'})
    store.private_config(identifier, {'profile': 'local_runtime_release_v1', 'slot': 'stable', 'origin': 'http://127.0.0.1:18080', 'ready_token': '6' * 64})
    store.save_archive(identifier, 'stable', 1, {'fixture_only': True})
    finish(store, body)
    with sqlite3.connect(store.path) as db:
        columns = [item[1] for item in db.execute(f'PRAGMA table_info({table})')]
        values = list(db.execute(f'SELECT * FROM {table} ORDER BY sequence LIMIT 1').fetchone())
        before = list(values)
        # Retain only the physical primary key; try bypassing all other unique-key guards.
        for field in ('id', 'operation_id', 'sha'):
            if field in columns: values[columns.index(field)] = '9' * 64
        if 'ordinal' in columns: values[columns.index('ordinal')] = 42
        if 'generation' in columns: values[columns.index('generation')] = 7
        values[columns.index('record')] = json.dumps({'changed': True})
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            db.execute(f'INSERT OR REPLACE INTO {table} VALUES({",".join("?" for _ in columns)})', values)
        with pytest.raises(sqlite3.IntegrityError, match='immutable'): db.execute(f'UPDATE {table} SET record=record')
        with pytest.raises(sqlite3.IntegrityError, match='immutable'): db.execute(f'DELETE FROM {table}')
        assert list(db.execute(f'SELECT * FROM {table} ORDER BY sequence LIMIT 1').fetchone()) == before


def test_engine_origin_and_policy_revision_are_pinned(tmp_path):
    """Changing the user's local Docker context or slot cookie origins cannot silently retarget owned resources."""
    store = enabled_store(tmp_path)
    store.bind_engine({'endpoint_sha256': '4' * 64, 'engine_id': 'owned-fixture-engine'})
    store.bind_engine({'endpoint_sha256': '4' * 64, 'engine_id': 'owned-fixture-engine'})
    with pytest.raises(RuntimeReleaseError, match='engine changed'):
        store.bind_engine({'endpoint_sha256': '5' * 64, 'engine_id': 'owned-fixture-engine'})
    with pytest.raises(RuntimeReleaseError, match='revision'):
        store.set_policy(LocalRuntimePolicy(), expected_revision=1, request_key='stale-policy-key')
    with pytest.raises(RuntimeReleaseError, match='origins'):
        store.set_policy(LocalRuntimePolicy(stable_port=18180), expected_revision=2, request_key='changed-origin-key')


def test_policy_replay_does_not_disable_later_policy(tmp_path):
    """Historical configuration replay cannot replace a later operator decision."""
    store = enabled_store(tmp_path)
    store.set_policy(LocalRuntimePolicy(), expected_revision=2, request_key='disable-local-only')
    assert store.set_policy(LocalRuntimePolicy(), expected_revision=999, request_key='disable-local-only')['revision'] == 3
    assert store.policy()[0] == 3


def test_private_root_refuses_mixed_files_redirects_and_relocation(tmp_path):
    """An existing private directory never becomes an implicitly adopted operator journal."""
    mixed = tmp_path / 'mixed'
    mixed.mkdir()
    (mixed / 'existing.txt').write_text('authored fixture')
    with pytest.raises(RuntimeReleaseError, match='empty'): RuntimeReleaseStore(mixed, initialize=True)
    store = enabled_store(tmp_path)
    moved = tmp_path / 'moved'
    store.root.rename(moved)
    with pytest.raises(RuntimeReleaseError, match='relocated'): RuntimeReleaseStore(moved)


def test_same_slot_archive_only_and_corrupted_chain_refused(tmp_path):
    """Rollback cannot select another environment's archive, and modified original checksums are detected on reopen."""
    store = enabled_store(tmp_path)
    first = activate()
    store.reserve('activate', first)
    identifier = operation_id(first.request_key)
    store.save_archive(identifier, 'stable', 1, {'fixture_only': True})
    with pytest.raises(RuntimeReleaseError, match='same-slot'): store.archive(identifier, 'preview')
    assert store.archive(identifier, 'stable')['generation'] == 1
    with sqlite3.connect(store.path) as db:
        db.execute('DROP TRIGGER operations_immutable_update')
        db.execute("UPDATE operations SET record='{}'")
    with pytest.raises(RuntimeReleaseError, match='integrity'): RuntimeReleaseStore(store.root)
