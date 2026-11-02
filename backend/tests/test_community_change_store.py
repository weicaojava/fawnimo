"""Real dedicated SQLite and process-restart checks for inert proposals, with original-key recovery and no execution authority."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest

from app.core.contracts.community_queue import QueueFinishInput
from app.services.community.change_bundles import build_change_bundle
from app.services.community.change_store import CommunityChangeStore
from app.services.community.change_preparation import assemble_current_change, commit_current_change
from app.services.community.public_code import PublicCodeObservation
from app.services.community.evaluations import canonical
from app.services.community.store import CommunityStoreConflict
from tests.test_community_evaluations import CONFIG, NOW, event, add, proof
from tests.test_community_queue_leases import factory, claim_body, lease_body, key
from tests.test_community_change_bundles import prepared_change
from tests.test_community_candidate_git import candidate_repo, assert_original


@pytest.fixture
def proposal_store(prepared_change, tmp_path):
    """Keep inert proposals in their own two-table root, independent of the actual preparation database and all user data."""
    body, prepared, snapshot, registry = prepared_change
    store = CommunityChangeStore(tmp_path / 'changes', CONFIG, initialize=True, clock_us=lambda: NOW)
    artifact = build_change_bundle(body, prepared, snapshot, registry)
    return store, body, artifact, prepared, snapshot, registry


def test_store_reopens_and_original_key_survives_restart(proposal_store):
    """Committed source bytes and original identities remain recoverable without reopening any source or network connection."""
    store, body, artifact, _prepared, _snapshot, _registry = proposal_store
    first = store.record(body, artifact)
    reopened = CommunityChangeStore(store.root, CONFIG)
    replay = reopened.replay(body)
    assert not first['replayed'] and replay['replayed'] and replay['artifact'] == artifact
    assert reopened.get(artifact['bundle_id']) == artifact
    assert reopened.history(body.work_id)[0]['bundle_id'] == artifact['bundle_id']
    assert not artifact['bundle']['execution_authorized']


def test_competing_identical_records_append_one_bundle(proposal_store):
    """Competing controllers cannot create duplicate source proposals for the original intent."""
    store, body, artifact, _prepared, _snapshot, _registry = proposal_store
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _index: store.record(body, artifact), range(12)))
    assert len({value['artifact']['bundle_id'] for value in results}) == 1
    with closing(store.connect()) as db:
        assert db.execute('SELECT count(*) FROM community_change_bundles').fetchone()[0] == 1


def test_original_key_cannot_change_source_intent(proposal_store):
    """Same-key title or source changes are conflicts, while a new explicit request may save a new immutable proposal."""
    store, body, artifact, prepared, snapshot, registry = proposal_store
    store.record(body, artifact)
    changed = body.model_copy(update={'title': '另一个明确意图'})
    with pytest.raises(CommunityStoreConflict): store.replay(changed)
    with pytest.raises(CommunityStoreConflict): store.record(changed, build_change_bundle(changed, prepared, snapshot, registry))
    fresh = changed.model_copy(update={'request_key': key('new-change')})
    saved = store.record(fresh, build_change_bundle(fresh, prepared, snapshot, registry))
    assert saved['artifact']['bundle_id'] != artifact['bundle_id'] and store.replay(body)['artifact'] == artifact


@pytest.mark.parametrize('limit', ['records', 'per_work', 'bytes'])
def test_capacity_rejection_preserves_original_recovery(proposal_store, monkeypatch, limit):
    """History limits stop new writes atomically and do not discard or block original-key ACK recovery."""
    store, body, artifact, prepared, snapshot, registry = proposal_store
    store.record(body, artifact)
    monkeypatch.setattr('app.services.community.change_store.' + {'records': 'MAX_BUNDLES', 'per_work': 'MAX_WORK_BUNDLES', 'bytes': 'MAX_STORED_BYTES'}[limit], 1)
    changed = body.model_copy(update={'request_key': key('new-change')})
    with pytest.raises(CommunityStoreConflict): store.record(changed, build_change_bundle(changed, prepared, snapshot, registry))
    assert store.replay(body)['artifact'] == artifact
    with closing(store.connect()) as db: assert db.execute('SELECT count(*) FROM community_change_bundles').fetchone()[0] == 1


@pytest.mark.parametrize('table', ['community_change_schema', 'community_change_bundles'])
@pytest.mark.parametrize('action', ['update', 'delete', 'replace', 'rowid_replace'])
def test_sql_cannot_rewrite_inert_originals(proposal_store, table, action):
    """Explicit different-key physical replacement is rejected even when recursive DELETE triggers are disabled."""
    store, body, artifact, prepared, snapshot, registry = proposal_store
    store.record(body, artifact)
    alternate = body.model_copy(update={'request_key': key('alternate-change')})
    other = build_change_bundle(alternate, prepared, snapshot, registry)
    with closing(store.connect()) as db:
        db.execute('PRAGMA recursive_triggers=OFF')
        before = db.execute('SELECT rowid,* FROM ' + table).fetchall()
        with pytest.raises(sqlite3.IntegrityError):
            if action == 'update':
                column = 'profile' if table.endswith('schema') else 'payload'
                db.execute('UPDATE ' + table + ' SET ' + column + '=' + column)
            elif action == 'delete': db.execute('DELETE FROM ' + table)
            else:
                row = list(before[0])
                if action == 'rowid_replace' and table.endswith('bundles'):
                    row[1:] = [other['bundle_id'], alternate.request_key, other['bundle']['intent_hash'], alternate.work_id, canonical(other), NOW]
                columns = [item[1] for item in db.execute('PRAGMA table_info(' + table + ')')]
                db.execute('INSERT OR REPLACE INTO ' + table + '(rowid,' + ','.join(columns) + ') VALUES(' + ','.join('?' for _ in row) + ')', row)
        db.rollback()
        assert db.execute('SELECT rowid,* FROM ' + table).fetchall() == before


def test_mixed_root_wrong_source_and_missing_guards_are_refused_without_repair(proposal_store, tmp_path):
    """Default opening cannot initialize over another product or silently replace history protection."""
    store, body, artifact, _prepared, _snapshot, _registry = proposal_store
    store.record(body, artifact)
    mixed = tmp_path / 'mixed'; mixed.mkdir(); retained = mixed / 'requirements.sqlite3'; retained.write_bytes(b'authored source remains untouched')
    with pytest.raises(CommunityStoreConflict): CommunityChangeStore(mixed, CONFIG, initialize=True)
    assert retained.read_bytes() == b'authored source remains untouched' and not (mixed / 'changes.sqlite3').exists()
    wrong = CONFIG.model_copy(update={'repository_id': 18})
    with pytest.raises(CommunityStoreConflict): CommunityChangeStore(store.root, wrong)
    with closing(store.connect()) as db:
        db.execute('DROP TRIGGER community_change_bundles_no_rowid_replace'); db.commit()
    with pytest.raises(CommunityStoreConflict): CommunityChangeStore(store.root, CONFIG, initialize=True)


def test_actual_cli_reads_and_original_recovery_without_source_root(proposal_store, tmp_path):
    """Three new CLI processes recover an original artifact and history even when no intake directory is supplied."""
    store, body, artifact, _prepared, _snapshot, _registry = proposal_store
    store.record(body, artifact)
    original = tmp_path / 'original-input.json'; original.write_text(canonical(body.model_dump()), 'utf8')
    cli = Path(__file__).resolve().parents[1] / 'develop_community.py'
    allowed = {'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATH', 'PATHEXT', 'TEMP', 'TMP', 'TMPDIR', 'LANG', 'LC_ALL', 'TZ'}
    env = {name: value for name, value in os.environ.items() if name.upper() in allowed}
    env.update(PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', JELLYFISH_SKIP_ENV_FILE='1')
    outputs = []
    for action in [('--prepare-file', str(original)), ('--get-bundle', artifact['bundle_id']), ('--history', body.work_id)]:
        result = subprocess.run([sys.executable, str(cli), '--changes-root', str(store.root), '--repository-id', '17',
            '--repository-name', 'authored/fawnimo', *action], capture_output=True, text=True, encoding='utf8', env=env, timeout=25, cwd=cli.parent)
        assert result.returncode == 0, result.stderr
        outputs.append(json.loads(result.stdout))
    assert outputs[0]['replayed'] and outputs[0]['artifact'] == outputs[1] == artifact
    assert outputs[2][0]['bundle_id'] == artifact['bundle_id']


@pytest.mark.asyncio
async def test_original_service_replay_does_not_observe_source_again(proposal_store, monkeypatch):
    """Original artifact recovery can succeed without a preparation service at all, before network or source revalidation."""
    store, body, artifact, _prepared, _snapshot, _registry = proposal_store
    store.record(body, artifact)
    async def forbidden(*_args):
        """Any public source attempt on original recovery is a regression, regardless of availability."""
        raise AssertionError('Unexpected source observation')
    monkeypatch.setattr('app.services.community.change_preparation.verify_public_issue', forbidden)
    monkeypatch.setattr('app.services.community.change_preparation.verify_public_code', forbidden)
    assert (await assemble_current_change(None, store, body))['artifact'] == artifact


@pytest.mark.asyncio
async def test_new_service_rechecks_selection_after_public_reads(factory, prepared_change, tmp_path, monkeypatch):
    """Proof gathered outside a writer cannot save a bundle after a concurrent operator changes the selected preparation scope."""
    intake, queue, preparation, _moment, values, _plan, ids, _body = factory()
    claim = preparation.claim(claim_body(ids[0]), proof(values[0]))
    preparation.finish(lease_body(claim, contract=QueueFinishInput, outcome='prepared', note='等待新变更。'), proof(values[0]))
    original_body, _prepared, snapshot, _registry = prepared_change
    body = original_body.model_copy(update={'work_id': ids[0], 'request_key': key('race-change')})
    changes = CommunityChangeStore(tmp_path / 'race-changes', CONFIG, initialize=True)
    async def issue(_config, number):
        """Project actual authored source bytes at the current observation clock, without network or private profiles."""
        assert number == 3
        return proof(values[0], now=time.time_ns() // 1000)
    async def code(_config, _features, _paths):
        """Change selection while a source read is in flight, demonstrating no SQLite writer spans the network await."""
        queue.activate(None, key('deactivate'), 1)
        return PublicCodeObservation(snapshot, time.time_ns() // 1000, tuple('a' * 64 for _ in range(7)))
    monkeypatch.setattr('app.services.community.change_preparation.verify_public_issue', issue)
    monkeypatch.setattr('app.services.community.change_preparation.verify_public_code', code)
    with pytest.raises(CommunityStoreConflict): await assemble_current_change(preparation, changes, body)
    assert changes.history(body.work_id) == []


@pytest.mark.asyncio
async def test_new_service_records_both_fresh_observations_then_replays_after_withdrawal(factory, prepared_change, tmp_path, monkeypatch):
    """The actual assembly service writes one inert original, then recovers it without repeating either public read after withdrawal."""
    intake, _queue, preparation, _moment, values, _plan, ids, _body = factory()
    claim = preparation.claim(claim_body(ids[0]), proof(values[0]))
    preparation.finish(lease_body(claim, contract=QueueFinishInput, outcome='prepared', note='等待有界提案。'), proof(values[0]))
    original_body, _prepared, snapshot, _registry = prepared_change
    body = original_body.model_copy(update={'work_id': ids[0], 'request_key': key('new-current-change')})
    changes = CommunityChangeStore(tmp_path / 'current-changes', CONFIG, initialize=True)
    calls = []
    async def issue(_config, number):
        """Observe exact authored Issue data at a fresh real clock without live network or a model."""
        calls.append(('issue', number))
        return proof(values[0], now=time.time_ns() // 1000)
    async def code(_config, features, paths):
        """Observe exact authored public file bytes at the same source epoch; new-file tests remain absent from the base."""
        calls.append(('code', tuple(features), tuple(paths)))
        return PublicCodeObservation(snapshot, time.time_ns() // 1000, tuple('a' * 64 for _ in range(7)))
    monkeypatch.setattr('app.services.community.change_preparation.verify_public_issue', issue)
    monkeypatch.setattr('app.services.community.change_preparation.verify_public_code', code)
    saved = await assemble_current_change(preparation, changes, body)
    assert len(calls) == 2 and not saved['artifact']['bundle']['execution_authorized']
    observed = saved['artifact']['bundle']['source_observations']
    assert observed['issue']['fingerprint'] == proof(values[0]).issue.fingerprint and observed['code']['commit'] == snapshot.commit
    add(intake, event(time='2026-10-30T01:01:00Z', state='closed'))
    replay = await assemble_current_change(preparation, changes, body)
    assert replay['replayed'] and replay['artifact'] == saved['artifact'] and len(calls) == 2


@pytest.mark.asyncio
async def test_current_commit_service_creates_actual_ref_then_recovers_without_source(factory, candidate_repo, tmp_path, monkeypatch):
    """The shipped service produces a real exact local commit and later recovers it without source, while preserving the dirty clone."""
    repository, original_body, _prepared, _artifact, observation, protected = candidate_repo
    intake, _queue, preparation, _moment, values, _plan, ids, _body = factory()
    claimed = preparation.claim(claim_body(ids[0]), proof(values[0]))
    preparation.finish(lease_body(claimed, contract=QueueFinishInput, outcome='prepared', note='准备实际本机提案提交。'), proof(values[0]))
    body = original_body.model_copy(update={'work_id': ids[0], 'request_key': key('local-commit')})
    registry = json.loads(observation.snapshot.files['capabilities/features.json'])
    artifact = build_change_bundle(body, preparation.get_work(ids[0]), observation.snapshot, registry)
    changes = CommunityChangeStore(tmp_path / 'local-commit-changes', CONFIG, initialize=True)
    changes.record(body, artifact)
    calls = []
    async def issue(_config, number):
        """Supply fresh authored public Issue proof without calling GitHub or using model credentials."""
        calls.append('issue')
        return proof(values[0], now=time.time_ns() // 1000)
    async def code(_config, _features, _paths):
        """Supply the actual temporary Git base and exact committed public bytes for local object construction."""
        calls.append('code')
        return replace(observation, checked_at_us=time.time_ns() // 1000)
    monkeypatch.setattr('app.services.community.change_preparation.verify_public_issue', issue)
    monkeypatch.setattr('app.services.community.change_preparation.verify_public_code', code)
    first = await commit_current_change(preparation, changes, artifact['bundle_id'], repository=repository)
    add(intake, event(time='2026-10-30T01:01:00Z', state='closed'))
    replay = await commit_current_change(None, changes, artifact['bundle_id'], repository=repository)
    assert len(calls) == 2 and not first['replayed'] and replay['replayed'] and first['candidate'] == replay['candidate']
    assert not first['candidate']['github_push_performed'] and not first['candidate']['code_executed']
    assert_original(repository, protected)
