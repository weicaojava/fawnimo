"""Actual dedicated SQLite guard/replay/order tests use authored source/review/CI facts, not real independent approvals."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
import json
import sqlite3

import pytest

from app.core.contracts.community_review_gates import CommunityReviewGatePolicy
from app.services.community.evaluations import canonical,digest
from app.services.community.review_gate_store import CommunityReviewGateStore
from app.services.community.store import CommunityStoreConflict
from tests.test_community_review_gate_decisions import gate
from tests.test_community_change_bundles import prepared_change
from tests.test_community_queue_leases import factory
from tests.test_community_evaluations import NOW


@pytest.fixture
def gate_store(gate,tmp_path):
    """Own an actual new root and immutable policy; companion paths are separate siblings, never personal/source/check data."""
    policy,body,artifact,facts,observation=gate; moment=[NOW]
    store=CommunityReviewGateStore(tmp_path/'gates',policy,initialize=True,
        isolated_from=(tmp_path/'source-reference',tmp_path/'changes-reference'),clock_us=lambda:moment[0])
    return store,moment,body,artifact,facts,observation


def test_observation_reopens_and_original_replay_is_historical_not_action_authority(gate_store):
    """A previously eligible snapshot remains recoverable without current source/Git/engine/reviewer availability and never grants an action now."""
    store,_moment,body,artifact,facts,observed=gate_store
    saved=store.record(body,artifact,facts,observed); value=saved['observation']
    assert not saved['replayed'] and value['decision']['eligible_at_observation']
    reopened=CommunityReviewGateStore(store.root)
    recovered=reopened.record(body)
    assert recovered['replayed'] and recovered['observation']==value and recovered['historical_only']
    assert recovered['fresh_action_authorized'] is False and recovered['source_recheck_required_for_action']
    assert reopened.get(value['id'])['observation']==value


def test_identical_competition_records_one_original_and_changed_key_intent_is_refused(gate_store):
    """Multiple callers serialize one receipt; a stable request key cannot switch to another PR/check/bundle after a response is lost."""
    store,_moment,body,artifact,facts,observed=gate_store
    with ThreadPoolExecutor(max_workers=8) as pool: values=list(pool.map(lambda _index:store.record(body,artifact,facts,observed),range(12)))
    assert len({value['observation']['id'] for value in values})==1 and sum(not value['replayed'] for value in values)==1
    with pytest.raises(CommunityStoreConflict): store.replay(body.model_copy(update={'pr_number':8}))
    assert len(store.history(body.bundle_id))==1


def test_disabled_policy_records_no_source_proof_and_keeps_all_authority_closed(gate_store,tmp_path):
    """Unconfigured independent reviewer/image/CI policy does not touch any source or grant authority just because JSON says eligible."""
    _store,_moment,body,_artifact,_facts,_observed=gate_store
    policy=CommunityReviewGatePolicy(repository_id=17,repository_name='authored/fawnimo')
    store=CommunityReviewGateStore(tmp_path/'disabled',policy,initialize=True,isolated_from=(tmp_path/'s',tmp_path/'c'),clock_us=lambda:NOW)
    result=store.record(body)['observation']['decision']
    assert result['blocking_reasons']==['review_gate_disabled'] and not result['eligible_at_observation'] and not result['merge_authorized']


def test_complete_local_check_without_public_observation_is_retained_as_blocked(gate_store):
    """Missing real public identity/CI proof cannot be substituted with local candidate-test success."""
    store,_moment,body,artifact,facts,_observed=gate_store
    result=store.record(body,artifact,facts,None)['observation']['decision']
    assert not result['eligible_at_observation'] and result['blocking_reasons'] and not result['promotion_authorized']


@pytest.mark.parametrize('table',['community_pr_gate_policy','community_pr_gate_observations'])
@pytest.mark.parametrize('action',['update','delete','replace'])
def test_raw_sql_cannot_rewrite_policy_or_original_observation(gate_store,table,action):
    """Natural identity and update/delete guards do not rely on recursive DELETE triggers being enabled for REPLACE."""
    store,_moment,body,artifact,facts,observed=gate_store; original=store.record(body,artifact,facts,observed)['observation']
    with closing(store.connect()) as db:
        db.execute('PRAGMA recursive_triggers=OFF')
        sql=f'UPDATE {table} SET '+('policy=policy' if table.endswith('_policy') else 'payload=payload') if action=='update' else f'DELETE FROM {table}' if action=='delete' else f'INSERT OR REPLACE INTO {table} SELECT * FROM {table}'
        with pytest.raises(sqlite3.IntegrityError): db.execute(sql)
        db.rollback()
    assert store.get(original['id'])['observation']==original


def test_physical_rowid_replace_with_different_valid_identity_cannot_erase_old_receipt(gate_store,tmp_path):
    """A fully valid independent receipt with distinct logical keys still cannot replace an earlier physical sequence row."""
    store,_moment,body,artifact,facts,observed=gate_store; first=store.record(body,artifact,facts,observed)
    other=CommunityReviewGateStore(tmp_path/'other-gates',store.policy,initialize=True,isolated_from=(tmp_path/'s',tmp_path/'c'),clock_us=lambda:NOW)
    changed=body.model_copy(update={'request_key':'another-valid-observation'}); other.record(changed,artifact,facts,observed)
    with closing(other.connect()) as source: valid=source.execute('SELECT * FROM community_pr_gate_observations').fetchone()
    with closing(store.connect()) as db:
        replacement=(first['sequence'],)+valid[1:]
        with pytest.raises(sqlite3.IntegrityError): db.execute('INSERT OR REPLACE INTO community_pr_gate_observations VALUES(?,?,?,?,?,?,?)',replacement)
        db.rollback()
    assert store.get(first['observation']['id'])['observation']==first['observation'] and store.replay(changed) is None


def test_recomputed_public_hash_cannot_insert_merge_or_deployment_permission(gate_store,tmp_path):
    """An attacker can recompute public content hashes but cannot make a gate receipt an executable approval through SQLite insertion."""
    store,_moment,body,artifact,facts,observed=gate_store
    other=CommunityReviewGateStore(tmp_path/'other-gates',store.policy,initialize=True,isolated_from=(tmp_path/'s',tmp_path/'c'),clock_us=lambda:NOW)
    value=other.record(body,artifact,facts,observed)['observation']; value=deepcopy(value); value['decision']['merge_authorized']=True
    core={name:item for name,item in value.items() if name!='id'}; value['id']=digest({'domain':'community_review_gate_record_v1','record':core})
    with closing(store.connect()) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute('INSERT INTO community_pr_gate_observations(id,request_key,request_hash,policy_hash,payload,recorded_at_us) VALUES(?,?,?,?,?,?)',
                (value['id'],body.request_key,value['request_hash'],store.policy_hash,canonical(value),NOW))
        db.rollback()
    assert store.replay(body) is None


def test_explicit_sequence_order_survives_vacuum_and_clock_rollback_keeps_original_reads(gate_store):
    """Observation order uses a declared INTEGER sequence, while invalid clocks block new records without breaking original recovery."""
    store,moment,body,artifact,facts,observed=gate_store
    for index in range(3): store.record(body.model_copy(update={'request_key':'order-'+str(index)+'-observation'}),artifact,facts,observed)
    before=store.history(body.bundle_id)
    with closing(store.connect()) as db: db.execute('VACUUM')
    reopened=CommunityReviewGateStore(store.root,clock_us=lambda:moment[0]); assert reopened.history(body.bundle_id)==before
    moment[0]-=1
    original=body.model_copy(update={'request_key':'order-0-observation'}); assert reopened.record(original)['replayed']
    with pytest.raises(CommunityStoreConflict): reopened.record(body,artifact,facts,observed)


def test_different_policy_missing_guard_mixed_or_nested_root_is_refused_without_repair(gate_store,tmp_path):
    """Configuration changes need a new explicit root; reopening cannot silently enable policy, rebuild guards or mix private/source databases."""
    store,_moment,body,artifact,facts,observed=gate_store; saved=store.record(body,artifact,facts,observed)
    changed=store.policy.model_copy(update={'independent_reviewer_ids':(400,)})
    with pytest.raises(CommunityStoreConflict): CommunityReviewGateStore(store.root,changed)
    with closing(store.connect()) as db: db.execute('DROP TRIGGER community_pr_gate_observations_no_rowid_replace'); db.commit()
    with pytest.raises(CommunityStoreConflict): CommunityReviewGateStore(store.root,store.policy,initialize=True,isolated_from=(tmp_path/'s',tmp_path/'c'))
    mixed=tmp_path/'personal'; mixed.mkdir(); marker=mixed/'studio.sqlite3'; marker.write_bytes(b'authored-original-data')
    with pytest.raises(CommunityStoreConflict): CommunityReviewGateStore(mixed,store.policy,initialize=True,isolated_from=(tmp_path/'s',tmp_path/'c'))
    assert marker.read_bytes()==b'authored-original-data' and not (mixed/'gates.sqlite3').exists()
    nested=tmp_path/'source'/'nested-gates'
    with pytest.raises(CommunityStoreConflict): CommunityReviewGateStore(nested,store.policy,initialize=True,isolated_from=(tmp_path/'source',tmp_path/'changes'))
    assert not nested.exists()


def test_finite_capacity_never_evicts_original_and_replay_precedes_limit_or_clock(gate_store,tmp_path):
    """After384 explicit disabled-policy observations, further new records stop while original receipt recovery stays available."""
    _other,_moment,body,_artifact,_facts,_observed=gate_store; moment=[NOW]
    policy=CommunityReviewGatePolicy(repository_id=17,repository_name='authored/fawnimo')
    store=CommunityReviewGateStore(tmp_path/'finite',policy,initialize=True,isolated_from=(tmp_path/'s',tmp_path/'c'),clock_us=lambda:moment[0])
    first=None
    for index in range(policy.max_saved_observations):
        changed=body.model_copy(update={'request_key':f'bounded-observation-{index}'})
        result=store.record(changed)
        if first is None: first=(changed,result['observation'])
    with pytest.raises(CommunityStoreConflict): store.record(body)
    moment[0]-=1
    assert store.record(first[0])['observation']==first[1] and len(store.history(body.bundle_id))==384


@pytest.mark.parametrize('nested',[False,True],ids=['same-companions','nested-companions'])
def test_initialization_requires_three_separate_roots(gate_store,tmp_path,nested):
    """A gate root cannot legitimize overlapping intake/change paths or create files before that structural error is rejected."""
    store,*_rest=gate_store; source=tmp_path/'declared-source'; change=source/'nested-change' if nested else source
    root=tmp_path/'uncreated-gates'
    with pytest.raises(CommunityStoreConflict): CommunityReviewGateStore(root,store.policy,initialize=True,isolated_from=(source,change))
    assert not root.exists()
