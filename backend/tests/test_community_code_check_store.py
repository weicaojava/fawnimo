"""Real SQLite ownership/unknown-outcome tests use authored collector facts only; they do not qualify a real OCI runner or source admission."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import json
import sqlite3

import pytest
from pydantic import ValidationError

from app.core.contracts.community_code_checks import (CommunityCodeCheckInput, CodeCheckAdmission, CodeCheckObservation, CodeCheckCleanup, LinuxCodeCheckPolicy)
from app.services.community.candidate_git import CandidateCommit
from app.services.community.change_store import CommunityChangeStore
from app.services.community.code_check_store import CommunityCodeCheckStore
from app.services.community.store import CommunityStoreConflict
from app.services.community.evaluations import canonical, digest
from tests.test_community_change_store import proposal_store
from tests.test_community_change_bundles import prepared_change
from tests.test_community_queue_leases import factory, key
from tests.test_community_evaluations import CONFIG, NOW

IMAGE = 'sha256:' + 'e' * 64
CONTAINER = 'f' * 64


@pytest.fixture
def checks(proposal_store):
    """Each test owns an actual inert bundle and optional three-table journal with a mutable clock, never a daemon or user root."""
    changes, body, artifact, _prepared, _snapshot, _registry = proposal_store
    changes.record(body, artifact); moment = [NOW]; changes.clock_us = lambda: moment[0]
    store = CommunityCodeCheckStore(changes, initialize=True)
    candidate = CandidateCommit(artifact['bundle_id'], artifact['branch'], 'c' * 40, 'd' * 40, artifact['bundle']['scope']['base_commit'], 17, 'authored/fawnimo')
    request = CommunityCodeCheckInput(request_key=key('check'), bundle_id=artifact['bundle_id'])
    admission = CodeCheckAdmission(artifact['bundle_id'], artifact['bundle']['scope_hash'], candidate.commit, candidate.tree, candidate.parent,
        IMAGE, 17, 'authored/fawnimo', artifact['bundle']['scope']['source_fingerprint'], NOW)
    return store, changes, moment, candidate, request, admission


def allocated(checks):
    """Return actual durable allocation plus its private collector-only token, never pretending that it created a container."""
    store, _changes, _moment, candidate, request, admission = checks
    value = store.allocate(request, candidate, IMAGE, admission)
    private = store.private_ownership(value['id'])
    return value, private['owner_token']


def cleanup(value, token, *, container_id=None, **changes):
    """Construct authored trusted-collector facts for journal state tests; real daemon verification remains a separate requirement."""
    data = {'attempt_id': value['id'], 'container_name': value['container_name'], 'image_id': value['image_id'], 'owner_token': token,
        'container_id': container_id, 'engine_observed': True, 'resource_absent': True, **changes}
    return CodeCheckCleanup(**data)


def observed(**changes):
    """Use explicit authored supervisor facts, not candidate stdout, to test that only fully validated observations enter the journal."""
    values = {'outcome': 'passed', 'process_exit_code': 0, 'supervisor_sha256': '1' * 64, 'output_sha256': '2' * 64, 'output_bytes': 20,
        'kernel_limits_verified': True, 'uid_separation_verified': True, 'namespace_isolation_verified': True, **changes}
    return CodeCheckObservation(**values)


def test_optional_profile_preserves_inert_bundles_and_reopens_exact_known_extension(checks):
    """Only explicit enabling adds runtime tables; ordinary opening accepts the complete extension and rejects a missing guard."""
    store, changes, _moment, _candidate, request, _admission = checks
    original = changes.get(request.bundle_id)
    reopened = CommunityChangeStore(changes.root, CONFIG)
    CommunityCodeCheckStore(reopened)
    assert reopened.get(request.bundle_id) == original
    with closing(changes.connect()) as db:
        db.execute('DROP TRIGGER community_code_check_attempts_no_rowid_replace'); db.commit()
    with pytest.raises(CommunityStoreConflict): CommunityChangeStore(changes.root, CONFIG, initialize=True)


def test_same_original_allocation_competition_creates_one_attempt_and_private_token_stays_private(checks):
    """Two controllers sharing the exact request reserve once, without making an external API request or leaking cleanup ownership."""
    store, changes, _moment, candidate, request, admission = checks
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda _index: store.allocate(request, candidate, IMAGE, admission), range(12)))
    assert len({value['id'] for value in values}) == 1
    assert sum(not value['replayed'] for value in values) == 1
    private = store.private_ownership(values[0]['id'])
    assert private['owner_token'] not in json.dumps(values)
    with closing(changes.connect()) as db: assert db.execute('SELECT count(*) FROM community_code_check_attempts').fetchone()[0] == 1


def test_expiry_and_restart_do_not_release_unknown_outcome_or_allow_new_attempt(checks):
    """Unlike preparation leases, an execution reservation survives its deadline until concrete original-resource reconciliation."""
    store, changes, moment, candidate, request, admission = checks
    value, token = allocated(checks)
    store.request_create(value['id'], token)
    moment[0] += 60000001
    restored = CommunityCodeCheckStore(CommunityChangeStore(changes.root, CONFIG))
    assert restored.get(value['id'])['active_reservation'] and not restored.get(value['id'])['automatic_retry_authorized']
    next_input = request.model_copy(update={'request_key': key('new-check')})
    with pytest.raises(CommunityStoreConflict):
        store.allocate(next_input, candidate, IMAGE, replace(admission, checked_at_us=moment[0]))
    # Absence after an unacknowledged create is not proof that a late daemon
    # acceptance cannot still appear. Keep the slot instead of blindly retrying.
    with pytest.raises(CommunityStoreConflict): store.finish(value['id'], token, 'interrupted', cleanup(value, token))
    assert store.get(value['id'])['active_reservation']


def test_original_phase_replay_does_not_issue_new_start_or_extend_time(checks):
    """Response loss recovers the original phase, even after expiry and rollback, without returning to an earlier execution state."""
    store, _changes, moment, _candidate, request, _admission = checks
    value, token = allocated(checks)
    store.request_create(value['id'], token); store.record_created(value['id'], token, CONTAINER)
    first = store.request_start(value['id'], token)
    moment[0] += 60000001
    replay = store.request_start(value['id'], token)
    assert replay['history'] == first['history'] and replay['deadline_us'] == first['deadline_us']
    moment[0] = NOW - 1
    assert store.request_create(value['id'], token)['current_phase'] == 'start_requested'
    assert store.replay(request, IMAGE)['current_phase'] == 'start_requested'


@pytest.mark.parametrize('field', ['scope_hash', 'candidate_commit', 'image_id', 'repository_id', 'source_fingerprint', 'checked_at_us'])
def test_missing_or_changed_internal_admission_cannot_allocate(checks, field):
    """Prepared provenance or client-like approval data does not replace fresh exact source/image/candidate admission."""
    store, changes, _moment, candidate, request, admission = checks
    wrong = {'scope_hash': '0' * 64, 'candidate_commit': '0' * 40, 'image_id': 'sha256:' + '0' * 64,
        'repository_id': 18, 'source_fingerprint': '0' * 64, 'checked_at_us': NOW - 60000001}[field]
    with pytest.raises(CommunityStoreConflict): store.allocate(request, candidate, IMAGE, replace(admission, **{field: wrong}))
    with closing(changes.connect()) as db: assert db.execute('SELECT count(*) FROM community_code_check_attempts').fetchone()[0] == 0


def test_known_owned_cleanup_releases_capacity_and_attempts_remain_finite(checks):
    """Only acknowledged concrete identity can close an attempted creation, and explicit retries stop after three immutable attempts."""
    store, _changes, _moment, candidate, request, admission = checks
    identities = []
    for index in range(3):
        body = request.model_copy(update={'request_key': key('bounded-check')})
        value = store.allocate(body, candidate, IMAGE, admission); token = store.private_ownership(value['id'])['owner_token']
        identities.append(value['id'])
        store.request_create(value['id'], token); store.record_created(value['id'], token, CONTAINER)
        store.request_cancel(value['id'])
        assert store.get(value['id'])['active_reservation']
        closed = store.finish(value['id'], token, 'cancelled', cleanup(value, token, container_id=CONTAINER))
        assert not closed['active_reservation'] and closed['outcome'] == 'cancelled'
    assert len(set(identities)) == 3
    with pytest.raises(CommunityStoreConflict): store.allocate(request.model_copy(update={'request_key': key('fourth-check')}), candidate, IMAGE, admission)


def test_success_requires_supervisor_observation_and_cleanup_not_just_exit_or_expiry(checks):
    """A successful candidate check remains active until exact cleanup, and still cannot self-approve CI, review or deployment."""
    store, _changes, _moment, _candidate, _request, _admission = checks
    value, token = allocated(checks)
    store.request_create(value['id'], token); store.record_created(value['id'], token, CONTAINER)
    with pytest.raises(sqlite3.IntegrityError): store.finish(value['id'], token, 'passed', cleanup(value, token, container_id=CONTAINER))
    store.request_start(value['id'], token); reported = store.observe(value['id'], token, observed())
    assert reported['active_reservation'] and reported['outcome'] is None
    with pytest.raises(CommunityStoreConflict): store.finish(value['id'], token, 'passed', cleanup(value, token, container_id=CONTAINER, resource_absent=False))
    closed = store.finish(value['id'], token, 'passed', cleanup(value, token, container_id=CONTAINER))
    assert closed['outcome'] == 'passed' and not closed['active_reservation']
    assert not closed['independent_review_completed'] and not closed['ci_qualified'] and not closed['deployment_authorized']


@pytest.mark.parametrize('bad', [{'process_exit_code': 1}, {'kernel_limits_verified': False}, {'uid_separation_verified': False},
    {'namespace_isolation_verified': False}, {'output_bytes': 65537}], ids=['exit', 'kernel', 'uid', 'namespace', 'output'])
def test_invalid_candidate_success_does_not_enter_history(checks, bad):
    """Candidate-controlled success text cannot replace the collector's validated exit, kernel, UID and output bounds."""
    store, _changes, _moment, _candidate, _request, _admission = checks
    value, token = allocated(checks)
    store.request_create(value['id'], token); store.record_created(value['id'], token, CONTAINER); store.request_start(value['id'], token)
    with pytest.raises(CommunityStoreConflict): store.observe(value['id'], token, observed(**bad))
    assert not any(event['phase'] == 'observed' for event in store.get(value['id'])['history'])


def test_cancel_before_create_fences_late_submission_and_cancel_after_result_blocks_pass(checks):
    """Cancel intent holds capacity and cannot race a new create/start into a released slot or a later passed outcome."""
    store, _changes, _moment, _candidate, _request, _admission = checks
    value, token = allocated(checks); store.request_cancel(value['id'])
    with pytest.raises(sqlite3.IntegrityError): store.request_create(value['id'], token)
    assert store.get(value['id'])['active_reservation']
    assert store.finish(value['id'], token, 'cancelled', cleanup(value, token))['outcome'] == 'cancelled'


def test_cancel_after_observed_success_prevents_pass_and_old_cancel_cannot_touch_new_attempt(checks):
    """A late result cannot overrule cancellation; repeating an older cancel only recovers that terminal attempt."""
    store, _changes, _moment, candidate, request, admission = checks
    value, token = allocated(checks)
    store.request_create(value['id'], token); store.record_created(value['id'], token, CONTAINER); store.request_start(value['id'], token)
    store.observe(value['id'], token, observed()); store.request_cancel(value['id'])
    assert store.finish(value['id'], token, 'passed', cleanup(value, token, container_id=CONTAINER))['outcome'] == 'cancelled'
    later = store.allocate(request.model_copy(update={'request_key': key('after-cancel')}), candidate, IMAGE, admission)
    assert store.request_cancel(value['id'])['outcome'] == 'cancelled'
    assert store.get(later['id'])['active_reservation'] and not store.get(later['id'])['cancel_requested']


def test_unknown_cleanup_holds_slot_until_known_resource_is_reconciled(checks):
    """A failed cleanup keeps durable ownership after restart; acknowledging the discovered resource permits only exact cleanup."""
    store, changes, _moment, _candidate, _request, _admission = checks
    value, token = allocated(checks)
    store.request_create(value['id'], token)
    held = store.cleanup_required(value['id'], token, 'create_outcome_unknown')
    assert held['active_reservation'] and held['container_id'] is None
    restored = CommunityCodeCheckStore(CommunityChangeStore(changes.root, CONFIG))
    restored.record_created(value['id'], token, CONTAINER)
    assert restored.finish(value['id'], token, 'interrupted', cleanup(value, token, container_id=CONTAINER))['outcome'] == 'interrupted'


@pytest.mark.parametrize('table', ['community_code_check_policy', 'community_code_check_attempts', 'community_code_check_events'])
@pytest.mark.parametrize('action', ['update', 'delete', 'replace'])
def test_raw_sql_cannot_rewrite_check_identity_policy_or_events(checks, table, action):
    """Normal mutations and logical replacement cannot erase original ownership/history even with recursive triggers disabled."""
    store, changes, _moment, _candidate, _request, _admission = checks
    allocated(checks)
    with closing(changes.connect()) as db:
        db.execute('PRAGMA recursive_triggers=OFF')
        rows = db.execute('SELECT * FROM ' + table).fetchall()
        with pytest.raises(sqlite3.IntegrityError):
            if action == 'update':
                column = 'policy' if table.endswith('policy') else 'payload'
                db.execute('UPDATE ' + table + ' SET ' + column + '=' + column)
            elif action == 'delete': db.execute('DELETE FROM ' + table)
            else: db.execute('INSERT OR REPLACE INTO ' + table + ' VALUES(' + ','.join('?' for _ in rows[0]) + ')', rows[0])
        db.rollback(); assert db.execute('SELECT * FROM ' + table).fetchall() == rows


def test_explicit_different_key_rowid_replace_does_not_erase_closed_attempt(checks):
    """An actually valid new logical identity at an old physical rowid cannot bypass immutable audit history."""
    store, changes, _moment, candidate, request, admission = checks
    first, first_token = allocated(checks)
    store.request_cancel(first['id']); store.finish(first['id'], first_token, 'cancelled', cleanup(first, first_token))
    second = store.allocate(request.model_copy(update={'request_key': key('second-check')}), candidate, IMAGE, admission)
    second_token = store.private_ownership(second['id'])['owner_token']
    store.request_cancel(second['id']); store.finish(second['id'], second_token, 'cancelled', cleanup(second, second_token))
    with closing(changes.connect()) as db:
        db.execute('PRAGMA recursive_triggers=OFF')
        rowid = db.execute('SELECT rowid FROM community_code_check_attempts WHERE id=?', (first['id'],)).fetchone()[0]
        row = list(db.execute('SELECT * FROM community_code_check_attempts WHERE id=?', (second['id'],)).fetchone())
        row[0] = 'new-authored-attempt-id'; row[1] = key('new-physical-replace'); row[4] = 3
        row[9] = 'fawnimo-code-authored-different-name'
        payload = json.loads(row[13]); payload.update(attempt_id=row[0], request_key=row[1], number=row[4], container_name=row[9])
        payload['input'] = {'request_key': row[1], 'bundle_id': row[3]}
        row[2] = digest({'domain': payload['profile'], 'input': payload['input'], 'candidate': payload['candidate'], 'image_id': row[8]})
        row[13] = canonical(payload); row[14] = digest(payload)
        columns = [item[1] for item in db.execute('PRAGMA table_info(community_code_check_attempts)')]
        with pytest.raises(sqlite3.IntegrityError, match='Community rowid replacement rejected'):
            db.execute('INSERT OR REPLACE INTO community_code_check_attempts(rowid,' + ','.join(columns) + ') VALUES(' + ','.join('?' for _ in range(len(row) + 1)) + ')', [rowid, *row])
        db.rollback()
        assert db.execute('SELECT count(*) FROM community_code_check_attempts').fetchone()[0] == 2
        assert db.execute('SELECT 1 FROM community_code_check_attempts WHERE id=?', (first['id'],)).fetchone()


def test_clock_rollback_stops_start_but_allows_cancel_and_verified_cleanup(checks):
    """Cleanup that reduces risk must remain possible after a clock fault, without granting more execution time or a fresh attempt."""
    store, _changes, moment, candidate, request, admission = checks
    value, token = allocated(checks)
    store.request_create(value['id'], token); store.record_created(value['id'], token, CONTAINER)
    moment[0] = NOW - 1
    with pytest.raises(CommunityStoreConflict): store.request_start(value['id'], token)
    cancelled = store.request_cancel(value['id'])
    assert cancelled['current_receipt']['clock_source'] == 'retained_max_on_clock_rollback'
    assert store.finish(value['id'], token, 'cancelled', cleanup(value, token, container_id=CONTAINER))['outcome'] == 'cancelled'
    with pytest.raises(CommunityStoreConflict): store.allocate(request.model_copy(update={'request_key': key('after-rollback')}), candidate, IMAGE, admission)


@pytest.mark.parametrize('field', ['owner_token', 'container_name', 'image_id', 'container_id'])
def test_cleanup_cannot_target_another_owned_or_foreign_resource(checks, field):
    """A cleanup proof must match the original exact daemon identity and private ownership token, not just a generated name prefix."""
    store, _changes, _moment, _candidate, _request, _admission = checks
    value, token = allocated(checks)
    store.request_create(value['id'], token); store.record_created(value['id'], token, CONTAINER)
    wrong = {'owner_token': '0' * 64, 'container_name': 'foreign-container', 'image_id': 'sha256:' + '0' * 64, 'container_id': '0' * 64}[field]
    proof = cleanup(value, token, container_id=CONTAINER)
    with pytest.raises(CommunityStoreConflict): store.finish(value['id'], token, 'interrupted', replace(proof, **{field: wrong}))
    assert store.get(value['id'])['active_reservation']


@pytest.mark.parametrize('extra', ['command', 'image_id', 'lease_token', 'network', 'approved', 'policy'])
def test_check_request_cannot_supply_execution_policy(extra):
    """The local operation input names original bundle/key only; issue data and preparation tokens cannot configure the daemon."""
    with pytest.raises(ValidationError): CommunityCodeCheckInput.model_validate({'request_key': key('check'), 'bundle_id': 'a' * 64, extra: True})


@pytest.mark.parametrize('field', ['max_concurrent', 'controller_seconds', 'memory_bytes', 'external_fee_limit_fen'])
def test_fixed_server_policy_rejects_expansion_or_bool_coercion(field):
    """This profile cannot be expanded by a new task, restart or a truthy integer in an operator JSON file."""
    with pytest.raises(ValidationError): LinuxCodeCheckPolicy.model_validate({field: True})
    with pytest.raises(ValidationError): LinuxCodeCheckPolicy.model_validate({field: getattr(LinuxCodeCheckPolicy(), field) + 1})
