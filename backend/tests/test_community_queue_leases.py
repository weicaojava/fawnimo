"""Actual SQLite preparation ownership, replay, restart and finite-capacity checks, with authored public sources only."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
import json
import sqlite3
import uuid

import pytest
from pydantic import ValidationError

from app.core.contracts.community_queue import (CommunityQueuePolicy, CommunityQueuePlanInput, QueueMaterializeInput,
    QueueClaimInput, QueueLeaseInput, QueueFinishInput, QueueCancelInput, QueueWorkInput)
from app.services.community.store import CommunityRequestStore, CommunityStoreConflict
from app.services.community.evaluations import CommunityEvaluationStore, canonical, digest
from app.services.community.queue_store import CommunityQueueStore
from app.services.community.queue_lease_schema import LEASE_TABLES
from app.services.community.queue_leases import CommunityPreparationStore, claim_current_public_source, finish_current_public_source
from tests.test_community_evaluations import CONFIG, NOW, event, add, proof, evaluation


def key(prefix):
    """Generate unique short operation keys without accidental cross-test collisions or huge pytest parameter IDs."""
    return prefix + '-' + uuid.uuid4().hex


@pytest.fixture
def factory(tmp_path):
    """Build real independent stores with mutable clocks, registered prerequisite-free features and no workers or models."""
    def build(count=1, *, policy=None, after=False):
        """Every selected entry has an actual retained reasonable decision and exact original source proof."""
        intake = CommunityRequestStore(tmp_path / key('public-only'), CONFIG)
        moment = [NOW]
        reviews = CommunityEvaluationStore(intake, initialize=True, clock_us=lambda: moment[0])
        values, proposals = [], []
        for index in range(count):
            value = event(index + 3)
            version = add(intake, value)
            reviewed = reviews.record(evaluation(version, feature_ids=['F-015.01']), proof(value))
            values.append(value)
            proposals.append({'key': 'item-' + str(index), 'evaluation_id': reviewed['id'], 'value_score': 50,
                'value_reason': '实际功能登记和确定验收范围。', 'effort_minutes': 10,
                'after': ['item-' + str(index - 1)] if after and index else []})
        queue = CommunityQueueStore(intake, initialize=True, policy=policy, clock_us=lambda: moment[0])
        plan = queue.create_plan(CommunityQueuePlanInput.model_validate({'request_key': key('plan'), 'title': '仅限准备的路线图', 'proposals': proposals}))
        queue.activate(plan['id'], key('select'), 0)
        store = CommunityPreparationStore(queue, initialize=True)
        body = QueueMaterializeInput(request_key=key('materialize'), plan_id=plan['id'], expected_revision=1)
        ids = store.materialize(body)['receipt']['work_ids']
        return intake, queue, store, moment, values, plan, ids, body
    return build


def claim_body(identifier, **changes):
    """Bind one exact work/owner to a stable key; no source proof or authority appears in caller JSON."""
    return QueueClaimInput.model_validate({'request_key': key('claim'), 'work_id': identifier, 'owner': 'local-owner', **changes})


def lease_body(claim, *, contract=QueueLeaseInput, **changes):
    """Use the private token from a real claim result for the exact current attempt only."""
    value = {'request_key': key('lease'), 'work_id': claim['receipt']['work_id'], 'owner': claim['receipt']['owner'],
             'attempt_id': claim['receipt']['attempt_id'], 'lease_token': claim['lease_token'], **changes}
    return contract.model_validate(value)


def test_preparation_is_optional_and_exact_schema_reopens(factory):
    """The dedicated intake validates the complete extension, and missing guards never silently recreate history."""
    intake, queue, store, _moment, _values, _plan, ids, _body = factory()
    reopened = CommunityRequestStore(intake.root, CONFIG, initialize=False)
    restored = CommunityPreparationStore(CommunityQueueStore(reopened))
    assert restored.get_work(ids[0])['current']['state'] == 'queued'
    assert not restored.get_work(ids[0])['execution_authorized']
    with closing(intake.connect()) as db:
        assert LEASE_TABLES <= {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        db.execute('DROP TRIGGER community_queue_attempts_no_rowid_replace')
        db.commit()
    with pytest.raises(CommunityStoreConflict):
        CommunityRequestStore(intake.root, CONFIG, initialize=False)


def test_materialize_original_recovery_after_selection_change_retains_ids(factory):
    """A lost materialization response recovers its original selection and never targets the newly selected plan."""
    intake, queue, store, _moment, _values, plan, ids, body = factory()
    queue.activate(None, key('deactivate'), 1)
    replay = store.materialize(body)
    assert replay['replayed'] and replay['receipt']['work_ids'] == ids
    assert replay['receipt']['selection_revision'] == 1
    with pytest.raises(CommunityStoreConflict):
        store.materialize(QueueMaterializeInput(request_key=key('new-materialize'), plan_id=plan['id'], expected_revision=1))
    with closing(intake.connect()) as db:
        assert db.execute('SELECT count(*) FROM community_queue_work').fetchone()[0] == 1


def test_same_original_claim_competition_creates_one_attempt(factory):
    """Two schedulers replay the same committed claim and token, without producing duplicate attempts or extra leases."""
    intake, _queue, store, _moment, values, _plan, ids, _body = factory()
    body = claim_body(ids[0])
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _index: store.claim(body, proof(values[0])), range(12)))
    assert len({value['receipt']['attempt_id'] for value in results}) == 1
    assert len({value['lease_token'] for value in results}) == 1
    with closing(intake.connect()) as db:
        assert db.execute('SELECT count(*) FROM community_queue_attempts').fetchone()[0] == 1
    assert all(not value['development_complete'] and not value['execution_authorized'] for value in results)


def test_different_claim_competition_has_one_winner(factory):
    """Distinct schedulers cannot both own the same queued work; the losing intent never leaves an attempt row."""
    intake, _queue, store, _moment, values, _plan, ids, _body = factory()
    def compete(index):
        """Return expected ownership conflicts without hiding unexpected database or runtime errors."""
        try:
            return store.claim(claim_body(ids[0], owner='scheduler-' + str(index)), proof(values[0]))
        except CommunityStoreConflict:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(compete, range(8)))
    assert sum(value is not None for value in results) == 1
    with closing(intake.connect()) as db:
        assert db.execute('SELECT count(*) FROM community_queue_attempts').fetchone()[0] == 1


@pytest.mark.parametrize('changes', [{'owner': 'wrong-owner'}, {'lease_token': '0' * 64}, {'attempt_id': 'wrong-attempt'}], ids=['owner', 'token', 'attempt'])
def test_heartbeat_fences_wrong_ownership(factory, changes):
    """No owner label, token or attempt identity alone is sufficient to renew another preparation reservation."""
    intake, _queue, store, _moment, values, _plan, ids, _body = factory()
    claim = store.claim(claim_body(ids[0]), proof(values[0]))
    with pytest.raises(CommunityStoreConflict):
        store.heartbeat(lease_body(claim, **changes))
    with closing(intake.connect()) as db:
        assert db.execute("SELECT count(*) FROM community_queue_operations WHERE kind='heartbeat'").fetchone()[0] == 0


def test_heartbeat_replay_cannot_extend_past_original_result_or_hard_deadline(factory):
    """Restart and ACK loss retain the first heartbeat result; successive renewals still stop at the immutable deadline."""
    _intake, _queue, store, moment, values, _plan, ids, _body = factory(policy=CommunityQueuePolicy(lease_seconds=5, attempt_timeout_seconds=10))
    claim = store.claim(claim_body(ids[0]), proof(values[0]))
    moment[0] += 4000000
    body = lease_body(claim)
    first = store.heartbeat(body)
    moment[0] += 4000000
    replay = store.heartbeat(body)
    assert replay['operation_sequence'] == first['operation_sequence']
    assert replay['current_work']['current']['lease_until_us'] == NOW + 9000000
    next_result = store.heartbeat(lease_body(claim))
    assert next_result['receipt']['lease_until_us'] == NOW + 10000000
    moment[0] = NOW + 10000000
    with pytest.raises(CommunityStoreConflict):
        store.heartbeat(lease_body(claim))
    assert not store.get_work(ids[0])['lease_valid']


def test_expired_attempt_recovery_retries_finitely_and_fences_all_old_tokens(factory):
    """Only the side-effect-free preparation profile requeues; each of three attempts has a distinct permanent identity."""
    intake, queue, store, moment, values, _plan, ids, _body = factory(policy=CommunityQueuePolicy(lease_seconds=5, attempt_timeout_seconds=10))
    prior = []
    for number in range(1, 4):
        body = claim_body(ids[0])
        claimed = store.claim(body, proof(values[0], now=moment[0]))
        prior.append((body, claimed))
        moment[0] += 5000000
        replay = store.claim(body, None)
        assert 'lease_token' not in replay and replay['receipt']['attempt_id'] == claimed['receipt']['attempt_id']
        recovered = store.recover(QueueWorkInput(request_key=key('recover'), work_id=ids[0]))
        assert recovered['receipt']['state'] == ('queued' if number < 3 else 'failed')
        for _old_body, old in prior:
            with pytest.raises(CommunityStoreConflict):
                store.heartbeat(lease_body(old))
        # Actually reopen the SQLite store between attempts, rather than only
        # reusing an in-memory scheduler's attempt counter.
        store = CommunityPreparationStore(CommunityQueueStore(CommunityRequestStore(intake.root, CONFIG, initialize=False), clock_us=lambda: moment[0]))
    with pytest.raises(CommunityStoreConflict):
        store.claim(claim_body(ids[0]), proof(values[0], now=moment[0]))
    assert [value['number'] for value in store.get_work(ids[0])['attempts']] == [1, 2, 3]
    assert len({value['receipt']['attempt_id'] for _body, value in prior}) == 3
    assert queue.policy.max_attempts == 3


@pytest.mark.parametrize('mode', ['queued', 'leased'])
def test_cancel_is_terminal_replayable_and_releases_only_preparation(factory, mode):
    """Cancellation has no process-stop claim: this protocol never started a process or an external model call."""
    _intake, _queue, store, _moment, values, _plan, ids, _body = factory()
    claim = store.claim(claim_body(ids[0]), proof(values[0])) if mode == 'leased' else None
    body = QueueCancelInput(request_key=key('cancel'), work_id=ids[0], reason='停止这次准备，保留原记录。')
    first = store.cancel(body)
    assert first['receipt']['state'] == 'cancelled' and store.cancel(body)['operation_sequence'] == first['operation_sequence']
    assert not first['current_work']['lease_valid']
    with pytest.raises(CommunityStoreConflict):
        store.claim(claim_body(ids[0]), proof(values[0]))
    if claim:
        with pytest.raises(CommunityStoreConflict):
            store.finish(lease_body(claim, contract=QueueFinishInput, outcome='prepared', note='旧领取不得完成。'), proof(values[0]))


def test_concurrency_and_effort_are_separate_hard_caps(factory):
    """A free concurrency slot does not bypass the summed active effort cap; cancellation releases the reservation."""
    _intake, _queue, store, _moment, values, _plan, ids, _body = factory(3, policy=CommunityQueuePolicy(max_concurrent=3, max_active_effort_minutes=20))
    store.claim(claim_body(ids[0]), proof(values[0]))
    store.claim(claim_body(ids[1]), proof(values[1]))
    with pytest.raises(CommunityStoreConflict):
        store.claim(claim_body(ids[2]), proof(values[2]))
    store.cancel(QueueCancelInput(request_key=key('cancel'), work_id=ids[0], reason='释放准备容量。'))
    assert store.claim(claim_body(ids[2]), proof(values[2]))['current_work']['lease_valid']


def test_expired_reservation_holds_capacity_until_explicit_recovery(factory):
    """Claim does not silently guess what another reservation did; explicit preparation-only recovery releases its slot."""
    _intake, _queue, store, moment, values, _plan, ids, _body = factory(2, policy=CommunityQueuePolicy(lease_seconds=5, attempt_timeout_seconds=10))
    store.claim(claim_body(ids[0]), proof(values[0]))
    moment[0] += 5000000
    with pytest.raises(CommunityStoreConflict):
        store.claim(claim_body(ids[1]), proof(values[1], now=moment[0]))
    store.recover(QueueWorkInput(request_key=key('recover'), work_id=ids[0]))
    assert store.claim(claim_body(ids[1]), proof(values[1], now=moment[0]))['current_work']['lease_valid']


def test_concurrent_different_work_respects_count_cap_even_with_effort_available(factory):
    """Competing claims for distinct ready work cannot exceed the immutable one-reservation concurrency limit."""
    intake, _queue, store, _moment, values, _plan, ids, _body = factory(2)
    def compete(index):
        """Count only actual claim winners; propagate any error outside the documented conflict type."""
        try:
            return store.claim(claim_body(ids[index]), proof(values[index]))
        except CommunityStoreConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, range(2)))
    assert sum(item is not None for item in results) == 1
    with closing(intake.connect()) as db:
        assert db.execute('SELECT count(*) FROM community_queue_attempts').fetchone()[0] == 1


def test_same_requirement_cannot_overlap_across_selection_revisions(factory):
    """Even an invalidated older lease reserves its logical requirement until explicit preparation recovery releases it."""
    _intake, queue, store, _moment, values, plan, ids, _body = factory(policy=CommunityQueuePolicy(max_concurrent=2))
    store.claim(claim_body(ids[0]), proof(values[0]))
    queue.activate(plan['id'], key('reselect'), 1)
    new_ids = store.materialize(QueueMaterializeInput(request_key=key('materialize'), plan_id=plan['id'], expected_revision=2))['receipt']['work_ids']
    assert new_ids != ids
    with pytest.raises(CommunityStoreConflict):
        store.claim(claim_body(new_ids[0]), proof(values[0]))
    store.recover(QueueWorkInput(request_key=key('recover'), work_id=ids[0]))
    assert store.claim(claim_body(new_ids[0]), proof(values[0]))['current_work']['lease_valid']


def test_history_limit_failure_rolls_back_attempt_and_materialization_atomically(factory, monkeypatch):
    """Exhausted audit capacity cannot leave an attempt without its claim event or orphan work without its original receipt."""
    intake, queue, store, _moment, values, plan, ids, _body = factory()
    monkeypatch.setattr('app.services.community.queue_leases.MAX_OPERATIONS', 1)
    with pytest.raises(CommunityStoreConflict):
        store.claim(claim_body(ids[0]), proof(values[0]))
    queue.activate(plan['id'], key('reselect'), 1)
    with pytest.raises(CommunityStoreConflict):
        store.materialize(QueueMaterializeInput(request_key=key('materialize'), plan_id=plan['id'], expected_revision=2))
    with closing(intake.connect()) as db:
        assert db.execute('SELECT count(*) FROM community_queue_attempts').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM community_queue_work').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM community_queue_operations').fetchone()[0] == 1


def test_dependencies_require_preparation_success_without_qualifying_feature(factory):
    """A child waits for the original predecessor; preparation success never rewrites feature status or independent gates."""
    _intake, queue, store, _moment, values, _plan, ids, _body = factory(2, after=True)
    catalog = deepcopy(queue.catalog())
    with pytest.raises(CommunityStoreConflict):
        store.claim(claim_body(ids[1]), proof(values[1]))
    claim = store.claim(claim_body(ids[0]), proof(values[0]))
    done = store.finish(lease_body(claim, contract=QueueFinishInput, outcome='prepared', note='仅完成准备，尚未改代码。'), proof(values[0]))
    assert done['receipt']['state'] == 'prepared' and not done['development_complete'] and not done['execution_authorized']
    assert queue.catalog() == catalog
    assert store.claim(claim_body(ids[1]), proof(values[1]))['current_work']['lease_valid']


def test_failure_result_retries_finitely_and_original_finish_does_not_finish_new_attempt(factory):
    """A lost failure response stays tied to the failed attempt and cannot release its later replacement."""
    _intake, _queue, store, _moment, values, _plan, ids, _body = factory()
    first = store.claim(claim_body(ids[0]), proof(values[0]))
    failed_body = lease_body(first, contract=QueueFinishInput, outcome='failed', note='准备校验未通过。')
    failed = store.finish(failed_body)
    second = store.claim(claim_body(ids[0]), proof(values[0]))
    replay = store.finish(failed_body)
    assert replay['operation_sequence'] == failed['operation_sequence']
    assert replay['receipt']['state'] == 'queued'
    assert replay['current_work']['current']['attempt_id'] == second['receipt']['attempt_id'] and replay['current_work']['lease_valid']
    assert 'lease_token' not in replay


def test_prepared_without_fresh_source_does_not_release_or_finish_the_lease(factory):
    """A missing final source proof rolls back the success request and retains the original capacity reservation."""
    intake, _queue, store, _moment, values, _plan, ids, _body = factory()
    claim = store.claim(claim_body(ids[0]), proof(values[0]))
    with pytest.raises(CommunityStoreConflict):
        store.finish(lease_body(claim, contract=QueueFinishInput, outcome='prepared', note='缺少新的公开源复查。'))
    assert store.get_work(ids[0])['current']['state'] == 'leased' and store.get_work(ids[0])['lease_valid']
    with closing(intake.connect()) as db:
        assert db.execute("SELECT count(*) FROM community_queue_operations WHERE kind='finish'").fetchone()[0] == 0


@pytest.mark.parametrize('change', ['selection', 'source', 'assessment', 'catalog'])
def test_stale_scope_revokes_token_and_recovery_cancels_it(factory, change, monkeypatch):
    """Current pointer, original source, latest decision and feature evidence all fence old preparation ownership."""
    intake, queue, store, _moment, values, plan, ids, _body = factory()
    claim = store.claim(claim_body(ids[0]), proof(values[0]))
    if change == 'selection':
        queue.activate(None, key('deactivate'), 1)
        queue.activate(plan['id'], key('reselect'), 2)
    elif change == 'source':
        add(intake, event(time='2026-10-30T01:01:00Z', state='closed'))
    elif change == 'assessment':
        version = plan['plan']['entries'][0]['version_id']
        CommunityEvaluationStore(intake, clock_us=lambda: NOW).record(evaluation(version, decision='needs_clarification',
            feature_ids=[], acceptance=[], questions=['请补充具体范围。']), proof(values[0]))
    else:
        registry = deepcopy(queue.catalog())
        registry['register_version'] = 'another-evidence-version'
        monkeypatch.setattr(queue, 'catalog', lambda: registry)
    assert not store.get_work(ids[0])['lease_valid']
    with pytest.raises(CommunityStoreConflict):
        store.heartbeat(lease_body(claim))
    recovered = store.recover(QueueWorkInput(request_key=key('recover'), work_id=ids[0]))
    assert recovered['receipt']['state'] == 'cancelled'


def test_clock_rollback_rejects_new_mutation_but_recovers_original_without_token(factory):
    """A backward clock cannot extend a lease or create an attempt, while original ACK recovery still reports its saved result."""
    intake, _queue, store, moment, values, _plan, ids, _body = factory()
    body = claim_body(ids[0])
    original = store.claim(body, proof(values[0]))
    moment[0] -= 1
    replay = store.claim(body, None)
    assert replay['receipt']['attempt_id'] == original['receipt']['attempt_id'] and 'lease_token' not in replay
    assert not replay['current_work']['clock_valid']
    with pytest.raises(CommunityStoreConflict):
        store.cancel(QueueCancelInput(request_key=key('cancel'), work_id=ids[0], reason='回拨期间不能提交。'))
    with closing(intake.connect()) as db:
        assert db.execute('SELECT count(*) FROM community_queue_attempts').fetchone()[0] == 1


@pytest.mark.parametrize('kind', ['missing', 'stale', 'different'])
def test_new_claim_requires_exact_fresh_public_proof(factory, kind):
    """No archived local reasonable decision can replace the fixed verifier's fresh original identity proof."""
    intake, _queue, store, _moment, values, _plan, ids, _body = factory()
    observed = None if kind == 'missing' else proof(values[0], now=NOW - 60000001) if kind == 'stale' else proof(event(99))
    with pytest.raises(CommunityStoreConflict):
        store.claim(claim_body(ids[0]), observed)
    with closing(intake.connect()) as db:
        assert db.execute('SELECT count(*) FROM community_queue_attempts').fetchone()[0] == 0


@pytest.mark.asyncio
async def test_async_verification_occurs_before_writer_and_rechecks_selection_race(factory, monkeypatch):
    """Public proof gathered outside SQLite cannot authorize a plan that changed before the writer acquired ownership."""
    intake, queue, store, _moment, values, _plan, ids, _body = factory()
    async def verify(config, number):
        """A real writer succeeds while verification is running; its later selection invalidates the claim."""
        assert config == CONFIG and number == 3
        queue.activate(None, key('deactivate'), 1)
        return proof(values[0])
    monkeypatch.setattr('app.services.community.queue_leases.verify_public_issue', verify)
    with pytest.raises(CommunityStoreConflict):
        await claim_current_public_source(store, claim_body(ids[0]))
    with closing(intake.connect()) as db:
        assert db.execute('SELECT count(*) FROM community_queue_attempts').fetchone()[0] == 0


@pytest.mark.asyncio
async def test_original_claim_and_success_recovery_never_read_network_or_reexpose_old_token(factory, monkeypatch):
    """Original-key retries survive source withdrawal and retain current terminal state without repeating external observations."""
    intake, _queue, store, _moment, values, _plan, ids, _body = factory()
    body = claim_body(ids[0])
    claim = store.claim(body, proof(values[0]))
    finished_body = lease_body(claim, contract=QueueFinishInput, outcome='prepared', note='仅准备完成。')
    first = store.finish(finished_body, proof(values[0]))
    add(intake, event(time='2026-10-30T01:01:00Z', state='closed'))
    async def forbidden(*_args):
        """Any attempted source read on original recovery is a regression, independent of network availability."""
        raise AssertionError('Unexpected public source read')
    monkeypatch.setattr('app.services.community.queue_leases.verify_public_issue', forbidden)
    replay = await claim_current_public_source(store, body)
    recovered = await finish_current_public_source(store, finished_body)
    assert 'lease_token' not in replay and replay['current_work']['current']['state'] == 'prepared'
    assert recovered['operation_sequence'] == first['operation_sequence']
    assert claim['lease_token'] not in json.dumps(store.history(ids[0]))
    assert claim['lease_token'] not in json.dumps(store.get_work(ids[0]))


@pytest.mark.parametrize('table', sorted(LEASE_TABLES))
@pytest.mark.parametrize('action', ['update', 'delete', 'replace', 'rowid_replace'])
def test_sql_history_is_immutable_even_with_recursive_triggers_off(factory, table, action):
    """Both natural keys and physical rowid collisions reject replacement with different valid logical identities."""
    intake, queue, store, _moment, values, plan, ids, _body = factory()
    store.claim(claim_body(ids[0]), proof(values[0]))
    if table == 'community_queue_work' and action == 'rowid_replace':
        queue.activate(plan['id'], key('new-valid-selection'), 1)
    with closing(intake.connect()) as db:
        db.execute('PRAGMA recursive_triggers=OFF')
        before = db.execute('SELECT rowid,* FROM ' + table).fetchall()
        expected_message = 'Community rowid replacement rejected' if action == 'rowid_replace' and table != 'community_queue_operations' else 'replacement rejected' if action == 'rowid_replace' else None
        with pytest.raises(sqlite3.IntegrityError, match=expected_message):
            if action == 'update':
                column = {'community_queue_work': 'payload', 'community_queue_attempts': 'owner', 'community_queue_operations': 'payload'}[table]
                db.execute('UPDATE ' + table + ' SET ' + column + '=' + column)
            elif action == 'delete':
                db.execute('DELETE FROM ' + table)
            else:
                row = list(before[0])
                if action == 'rowid_replace':
                    # Keep the colliding physical rowid while changing all
                    # applicable logical unique keys to avoid testing only the
                    # older natural-key replacement protection.
                    if table == 'community_queue_work':
                        row[1] = str(uuid.uuid4()); row[3] = 2
                        payload = json.loads(row[5]); payload['selection_revision'] = 2
                        row[5] = canonical(payload); row[6] = digest(payload)
                    elif table == 'community_queue_attempts':
                        row[1] = str(uuid.uuid4()); row[3] = 2; row[4] = key('different-claim')
                    else:
                        row[2] = key('different-operation')
                db.execute('INSERT OR REPLACE INTO ' + table + '(rowid,' + ','.join(item[1] for item in db.execute('PRAGMA table_info(' + table + ')')) + ') VALUES(' + ','.join('?' for _ in row) + ')', row)
        db.rollback()
        assert db.execute('SELECT rowid,* FROM ' + table).fetchall() == before


def test_operation_key_cannot_change_kind_owner_or_target(factory):
    """Stable keys identify one original action, and never act as a generic command slot."""
    _intake, _queue, store, _moment, values, _plan, ids, _body = factory(2)
    body = claim_body(ids[0])
    store.claim(body, proof(values[0]))
    for replacement in (body.model_copy(update={'owner': 'different-owner'}), body.model_copy(update={'work_id': ids[1]})):
        with pytest.raises(CommunityStoreConflict):
            store.claim(replacement, proof(values[0]))
    with pytest.raises(CommunityStoreConflict):
        store.cancel(QueueCancelInput(request_key=body.request_key, work_id=ids[0], reason='不能复用领取键。'))


@pytest.mark.parametrize('extra', ['command', 'source_proof', 'execution_authorized', 'host', 'external_fee_limit_fen'])
def test_claim_json_cannot_supply_execution_network_or_source_authority(extra):
    """The shipped strict input has no command, host, price override or source-verification bypass field."""
    with pytest.raises(ValidationError):
        QueueClaimInput.model_validate({'request_key': key('claim'), 'work_id': 'authored-work', 'owner': 'local-owner', extra: 'untrusted'})
