"""Optional immutable roadmap versions and CAS activation guards in the dedicated public-request database."""
import json
from app.core.contracts.community_queue import CommunityQueuePolicy
from app.services.community.evaluations import digest
from app.services.community.store import CommunityStoreConflict
from app.services.community.rowid_guards import rowid_guard_definitions

QUEUE_TABLES=frozenset({'community_queue_policy','community_queue_plans','community_queue_current','community_queue_plan_events','community_evaluation_order'})


def queue_definitions():
    """Bind plan inputs/policy and pointer changes to immutable matching events, including natural-key replacement guards."""
    tables={
        'community_queue_policy':'CREATE TABLE community_queue_policy(singleton INTEGER PRIMARY KEY CHECK(singleton=1),version INTEGER NOT NULL CHECK(version=1),policy TEXT NOT NULL,policy_hash TEXT NOT NULL CHECK(length(policy_hash)=64))',
        'community_queue_plans':'CREATE TABLE community_queue_plans(id TEXT PRIMARY KEY NOT NULL,request_key TEXT NOT NULL UNIQUE,intent_hash TEXT NOT NULL CHECK(length(intent_hash)=64),payload TEXT NOT NULL,payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64),created_at_us INTEGER NOT NULL CHECK(created_at_us>=0))',
        'community_queue_current':'CREATE TABLE community_queue_current(singleton INTEGER PRIMARY KEY CHECK(singleton=1),revision INTEGER NOT NULL CHECK(revision>=0),plan_id TEXT REFERENCES community_queue_plans(id))',
        'community_queue_plan_events':'CREATE TABLE community_queue_plan_events(id TEXT PRIMARY KEY NOT NULL,request_key TEXT NOT NULL UNIQUE,intent_hash TEXT NOT NULL CHECK(length(intent_hash)=64),plan_id TEXT REFERENCES community_queue_plans(id),from_revision INTEGER NOT NULL CHECK(from_revision>=0),to_revision INTEGER NOT NULL CHECK(to_revision=from_revision+1),created_at_us INTEGER NOT NULL CHECK(created_at_us>=0))',
        'community_evaluation_order':"CREATE TABLE community_evaluation_order(sequence INTEGER PRIMARY KEY AUTOINCREMENT CHECK(sequence>0),evaluation_id TEXT NOT NULL UNIQUE REFERENCES community_evaluations(id),origin TEXT NOT NULL CHECK(origin IN('legacy_rowid_snapshot_v1','commit_trigger_v1')))"}
    guards={}
    for table in ('community_queue_policy','community_queue_plans','community_queue_plan_events'):
        for action in ('UPDATE','DELETE'):
            name=table+'_no_'+action.lower();guards[name]=f"CREATE TRIGGER {name} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Immutable community queue record'); END"
        name=table+'_no_replace'
        condition='singleton=NEW.singleton' if table=='community_queue_policy' else 'id=NEW.id OR request_key=NEW.request_key'
        guards[name]=f"CREATE TRIGGER {name} BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE {condition}) BEGIN SELECT RAISE(ABORT,'Community queue replacement rejected'); END"
    guards['community_queue_current_initial']="CREATE TRIGGER community_queue_current_initial BEFORE INSERT ON community_queue_current WHEN NEW.singleton!=1 OR NEW.revision!=0 OR NEW.plan_id IS NOT NULL OR EXISTS(SELECT 1 FROM community_queue_current) BEGIN SELECT RAISE(ABORT,'Queue pointer must start empty'); END"
    guards['community_queue_current_no_delete']="CREATE TRIGGER community_queue_current_no_delete BEFORE DELETE ON community_queue_current BEGIN SELECT RAISE(ABORT,'Queue pointer history is retained'); END"
    guards['community_queue_current_cas']="""CREATE TRIGGER community_queue_current_cas BEFORE UPDATE ON community_queue_current WHEN
        NEW.singleton IS NOT OLD.singleton OR NEW.revision!=OLD.revision+1 OR NOT EXISTS(SELECT 1 FROM community_queue_plan_events e
            WHERE e.from_revision=OLD.revision AND e.to_revision=NEW.revision AND e.plan_id IS NEW.plan_id)
        BEGIN SELECT RAISE(ABORT,'Queue pointer CAS requires original event'); END"""
    guards['community_queue_plan_scope']="""CREATE TRIGGER community_queue_plan_scope BEFORE INSERT ON community_queue_plans WHEN
        json_extract(NEW.payload,'$.input.request_key') IS NOT NEW.request_key
        OR json_extract(NEW.payload,'$.schema_version') IS NOT 1 OR json_extract(NEW.payload,'$.execution_authorized') IS NOT 0
        OR json_extract(NEW.payload,'$.queue_policy_hash') IS NOT (SELECT policy_hash FROM community_queue_policy WHERE singleton=1)
        BEGIN SELECT RAISE(ABORT,'Invalid queue plan scope'); END"""
    guards['community_queue_plan_event_scope']="""CREATE TRIGGER community_queue_plan_event_scope BEFORE INSERT ON community_queue_plan_events WHEN
        NEW.from_revision IS NOT (SELECT revision FROM community_queue_current WHERE singleton=1)
        OR EXISTS(SELECT 1 FROM community_queue_plan_events WHERE to_revision=NEW.to_revision)
        BEGIN SELECT RAISE(ABORT,'Queue event must advance the current revision'); END"""
    for action in ('UPDATE','DELETE'):
        name='community_evaluation_order_no_'+action.lower()
        guards[name]=f"CREATE TRIGGER {name} BEFORE {action} ON community_evaluation_order BEGIN SELECT RAISE(ABORT,'Immutable evaluation order'); END"
    guards['community_evaluation_order_no_replace']="CREATE TRIGGER community_evaluation_order_no_replace BEFORE INSERT ON community_evaluation_order WHEN EXISTS(SELECT 1 FROM community_evaluation_order WHERE sequence=NEW.sequence OR evaluation_id=NEW.evaluation_id) BEGIN SELECT RAISE(ABORT,'Evaluation order replacement rejected'); END"
    guards['community_evaluation_order_capture']="CREATE TRIGGER community_evaluation_order_capture AFTER INSERT ON community_evaluations BEGIN INSERT INTO community_evaluation_order(evaluation_id,origin) VALUES(NEW.id,'commit_trigger_v1'); END"
    guards.update(rowid_guard_definitions(('community_queue_policy','community_queue_plans','community_queue_plan_events','community_evaluation_order')))
    return tables,guards


def validate_queue_schema(db):
    """Reopen only complete known schemas, semantic policy hashes and one valid current pointer."""
    tables,guards=queue_definitions()
    for kind,definitions in [('table',tables),('trigger',guards)]:
        for name,expected in definitions.items():
            row=db.execute('SELECT sql FROM sqlite_master WHERE type=? AND name=?',(kind,name)).fetchone()
            if row is None or ' '.join(row[0].strip().rstrip(';').split())!=' '.join(expected.strip().rstrip(';').split()):
                raise CommunityStoreConflict('Community queue schema or guard changed')
    rows=db.execute('SELECT singleton,version,policy,policy_hash FROM community_queue_policy').fetchall()
    if len(rows)!=1 or rows[0][:2]!=(1,1):raise CommunityStoreConflict('Invalid community queue policy')
    policy=CommunityQueuePolicy.model_validate(json.loads(rows[0][2]))
    if digest(policy.model_dump())!=rows[0][3]:raise CommunityStoreConflict('Queue policy hash mismatch')
    current=db.execute('SELECT singleton,revision,plan_id FROM community_queue_current').fetchall()
    if len(current)!=1 or current[0][0]!=1:raise CommunityStoreConflict('Invalid current queue pointer')
    if current[0][1] and not db.execute('SELECT 1 FROM community_queue_plan_events WHERE to_revision=? AND plan_id IS ?',(current[0][1],current[0][2])).fetchone():
        raise CommunityStoreConflict('Queue pointer does not match its immutable event')
    if db.execute('SELECT 1 FROM community_evaluations e LEFT JOIN community_evaluation_order o ON o.evaluation_id=e.id WHERE o.evaluation_id IS NULL LIMIT 1').fetchone():
        raise CommunityStoreConflict('Evaluation order is incomplete')
    return policy
