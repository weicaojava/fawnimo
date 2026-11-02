"""Real SQLite roadmap/replay/CAS tests keep decisions and selection separate from any development executor."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
import json
import sqlite3
import uuid
import pytest
from app.core.contracts.community_queue import CommunityQueuePlanInput,CommunityQueuePolicy
from app.services.community.store import CommunityRequestStore,CommunityStoreConflict
from app.services.community.evaluations import CommunityEvaluationStore
from app.services.community.evaluations import canonical,digest
from app.services.community.queue_store import CommunityQueueStore
from app.services.community.queue_schema import QUEUE_TABLES
from tests.test_community_evaluations import CONFIG,NOW,event,add,proof,evaluation


def intent(evaluation_id,**changes):
    """One explicit original plan uses only a saved local assessment and typed estimates, never supplied approval metadata."""
    return CommunityQueuePlanInput.model_validate({'request_key':'plan-'+uuid.uuid4().hex,'title':'明确的本机路线图',
        'proposals':[{'key':'first','evaluation_id':evaluation_id,'value_score':50,'value_reason':'范围和验收条目已经明确。','effort_minutes':10}],**changes})


@pytest.fixture
def queue(tmp_path):
    """Each test owns public authored source/review/queue data and fixed clocks without an AI, worker or user workspace."""
    intake=CommunityRequestStore(tmp_path/'requests',CONFIG)
    reviews=CommunityEvaluationStore(intake,initialize=True,clock_us=lambda:NOW)
    value=event();version=add(intake,value);review=reviews.record(evaluation(version),proof(value))
    store=CommunityQueueStore(intake,initialize=True,clock_us=lambda:NOW)
    return intake,reviews,store,value,version,review


def test_optional_queue_requires_review_and_explicit_initialization_then_reopens_exact_schema(tmp_path):
    """Neither default intake nor merely opening reviews creates planning tables; unknown/tampered extensions remain blocked."""
    intake=CommunityRequestStore(tmp_path/'requests',CONFIG)
    with pytest.raises(CommunityStoreConflict):CommunityQueueStore(intake,initialize=True)
    CommunityEvaluationStore(intake,initialize=True)
    with pytest.raises(CommunityStoreConflict):CommunityQueueStore(intake)
    with closing(intake.connect()) as db:assert not ({row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}&QUEUE_TABLES)
    CommunityQueueStore(intake,initialize=True)
    CommunityRequestStore(intake.root,CONFIG,initialize=False)
    with closing(intake.connect()) as db:db.execute('DROP TRIGGER community_queue_current_cas');db.commit()
    with pytest.raises(CommunityStoreConflict):CommunityRequestStore(intake.root,CONFIG,initialize=False)


def test_original_plan_replay_survives_source_change_and_preserves_saved_ranking(queue):
    """A committed plan remains recoverable, while a changed source blocks new plans and activation instead of rewriting history."""
    intake,_reviews,store,_value,_version,review=queue;body=intent(review['id'])
    first=store.create_plan(body);assert first['feature_catalog_current'] and not first['selected']
    add(intake,event(time='2026-10-30T01:01:00Z',state='closed'))
    replay=store.create_plan(body)
    assert replay['id']==first['id'] and replay['content_hash']==first['content_hash']
    assert replay['current_original_assessments']=={'first':False}
    with pytest.raises(CommunityStoreConflict):store.create_plan(intent(review['id']))
    with pytest.raises(CommunityStoreConflict):store.activate(first['id'],'activate-original-001',0)


def test_original_selection_replay_does_not_reset_later_selection_or_deactivation(queue):
    """A response lost on selection A cannot undo the later B/disabled pointer; original event identity stays recoverable."""
    _intake,_reviews,store,_value,_version,review=queue
    first=store.create_plan(intent(review['id']));second=store.create_plan(intent(review['id'],title='另一个明确版本'))
    a=store.activate(first['id'],'select-first-original',0)
    b=store.activate(second['id'],'select-second-original',1)
    replay=store.activate(first['id'],'select-first-original',0)
    assert replay['event_id']==a['event_id'] and replay['committed_revision']==1 and replay['current_plan_id']==second['id']
    assert not store.get_plan(first['id'])['selected'] and store.get_plan(second['id'])['selected']
    store.activate(None,'deactivate-original',2)
    replay=store.activate(second['id'],'select-second-original',1)
    assert replay['event_id']==b['event_id'] and replay['current_plan_id'] is None and replay['current_revision']==3
    with pytest.raises(CommunityStoreConflict):store.activate(second['id'],'select-first-original',0)


def test_concurrent_original_plan_and_activation_each_have_one_receipt(queue):
    """Independent scheduler/operator threads with the same original intent cannot create duplicate versions or pointer events."""
    intake,_reviews,store,_value,_version,review=queue;body=intent(review['id'])
    with ThreadPoolExecutor(max_workers=8) as pool:plans=list(pool.map(lambda _index:store.create_plan(body),range(10)))
    assert len({row['id'] for row in plans})==1
    with ThreadPoolExecutor(max_workers=8) as pool:events=list(pool.map(lambda _index:store.activate(plans[0]['id'],'same-selection-original',0),range(10)))
    assert len({row['event_id'] for row in events})==1
    with closing(intake.connect()) as db:
        assert db.execute('SELECT count(*) FROM community_queue_plans').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM community_queue_plan_events').fetchone()[0]==1
        assert db.execute('SELECT revision FROM community_queue_current').fetchone()[0]==1


def test_newer_assessment_with_same_source_and_clock_supersedes_old_reasonable_decision(queue):
    """Commit order, not tied wall-clock timestamps or random IDs, prevents a stale reasonable decision from reentering the queue."""
    _intake,reviews,store,value,version,review=queue
    old=store.create_plan(intent(review['id']))
    later=reviews.record(evaluation(version,decision='needs_clarification',feature_ids=[],acceptance=[],questions=['请确认新边界。']),proof(value))
    assert not store.get_plan(old['id'])['current_original_assessments']['first']
    with pytest.raises(CommunityStoreConflict):store.create_plan(intent(review['id']))
    with pytest.raises(CommunityStoreConflict):store.create_plan(intent(later['id']))
    accepted=reviews.record(evaluation(version,appeal_of=later['id'],appeal_reason='已经明确原验收条件。'),proof(value))
    assert store.create_plan(intent(accepted['id']))['current_original_assessments']['first']


def test_explicit_evaluation_sequence_survives_vacuum_and_records_legacy_vs_new_commit_origin(queue):
    """A durable INTEGER sequence, rather than mutable implicit rowids or tied timestamps, orders later decisions."""
    intake,reviews,store,value,version,review=queue
    initial=store.create_plan(intent(review['id']))['plan']['entries'][0]
    assert initial['evaluation_sequence']==1 and initial['evaluation_order_origin']=='legacy_rowid_snapshot_v1'
    later=reviews.record(evaluation(version),proof(value));entry=store.create_plan(intent(later['id']))['plan']['entries'][0]
    assert entry['evaluation_sequence']==2 and entry['evaluation_order_origin']=='commit_trigger_v1'
    with closing(intake.connect()) as db:
        before=db.execute('SELECT sequence,evaluation_id,origin FROM community_evaluation_order ORDER BY sequence').fetchall()
        db.execute('VACUUM')
        assert db.execute('SELECT sequence,evaluation_id,origin FROM community_evaluation_order ORDER BY sequence').fetchall()==before
    assert store.create_plan(intent(later['id']))['current_original_assessments']['first']
    with pytest.raises(CommunityStoreConflict):store.create_plan(intent(review['id']))


def test_current_catalog_change_is_visible_and_blocks_activation_without_reinterpreting_old_snapshot(queue,monkeypatch):
    """New feature evidence/status/catalog versions must not silently grant or retain a previous roadmap's dependency qualification."""
    _intake,_reviews,store,_value,_version,review=queue;body=intent(review['id']);first=store.create_plan(body)
    registry=deepcopy(store.catalog());registry['register_version']='new-authored-version'
    monkeypatch.setattr(store,'catalog',lambda:registry)
    old=store.create_plan(body)
    assert old['id']==first['id'] and old['content_hash']==first['content_hash'] and not old['feature_catalog_current']
    with pytest.raises(CommunityStoreConflict):store.activate(old['id'],'catalog-stale-selection',0)


@pytest.mark.parametrize('table',['community_queue_policy','community_queue_plans','community_queue_plan_events'])
@pytest.mark.parametrize('action',['UPDATE','DELETE','REPLACE'])
def test_sql_cannot_rewrite_plan_policy_or_selection_events(queue,table,action):
    """Saved operating limits, roadmap intent and selection history reject replacement as well as ordinary mutation."""
    intake,_reviews,store,_value,_version,review=queue
    plan=store.create_plan(intent(review['id']));store.activate(plan['id'],'immutable-event-original',0)
    with closing(intake.connect()) as db:
        before=db.execute('SELECT * FROM '+table).fetchall()
        with pytest.raises(sqlite3.IntegrityError):
            if action=='DELETE':db.execute('DELETE FROM '+table)
            elif action=='UPDATE':
                column={'community_queue_policy':'version','community_queue_plans':'payload','community_queue_plan_events':'intent_hash'}[table]
                db.execute('UPDATE '+table+' SET '+column+'='+column)
            else:
                row=before[0];db.execute('INSERT OR REPLACE INTO '+table+' VALUES('+','.join('?' for _ in row)+')',row)
        db.rollback();assert db.execute('SELECT * FROM '+table).fetchall()==before


def test_root_policy_cannot_be_expanded_on_reopen_and_pointer_cannot_advance_without_event(queue):
    """A new policy argument or direct pointer update cannot bypass immutable root capacity and original selection audit."""
    intake,_reviews,_store,_value,_version,_review=queue
    with pytest.raises(CommunityStoreConflict):CommunityQueueStore(intake,policy=CommunityQueuePolicy(max_concurrent=2))
    with closing(intake.connect()) as db:
        with pytest.raises(sqlite3.IntegrityError):db.execute('UPDATE community_queue_current SET revision=1')
        db.rollback();assert db.execute('SELECT revision,plan_id FROM community_queue_current').fetchone()==(0,None)


@pytest.mark.parametrize('table',['community_queue_plans','community_queue_plan_events'])
def test_queue_receipts_also_reject_explicit_physical_rowid_replacement_with_valid_different_keys(queue,table):
    """Extend the confirmed community-history counterexample to unreferenced plans and otherwise-valid next pointer events."""
    intake,_reviews,store,_value,_version,review=queue;plan=store.create_plan(intent(review['id']))
    if table=='community_queue_plan_events':store.activate(plan['id'],'rowid-event-original',0)
    with closing(sqlite3.connect(intake.database)) as db:
        db.execute('PRAGMA foreign_keys=ON');db.execute('PRAGMA recursive_triggers=OFF')
        values=list(db.execute('SELECT rowid,* FROM '+table).fetchone());values[1]=str(uuid.uuid4());values[2]='replacement-'+uuid.uuid4().hex
        if table=='community_queue_plans':
            payload=json.loads(values[4]);payload['input']['request_key']=values[2]
            values[3]=digest({'domain':'community-queue-plan-input-v1',**payload['input']});values[4]=canonical(payload);values[5]=digest(payload)
        else:
            values[5],values[6]=1,2
            values[3]=digest({'domain':'community-queue-selection-v1','plan_id':values[4],'expected_revision':1})
        columns=['rowid',*(row[1] for row in db.execute('PRAGMA table_info('+table+')'))]
        with pytest.raises(sqlite3.IntegrityError,match='rowid replacement'):
            db.execute('INSERT OR REPLACE INTO '+table+'('+','.join(columns)+') VALUES('+','.join('?' for _ in values)+')',values)
        db.rollback()
    assert store.get_plan(plan['id'])['id']==plan['id']
