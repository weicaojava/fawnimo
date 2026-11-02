"""Optional append-only preparation reservations; no table or token authorizes code, models or deployment."""
import json

from app.services.community.evaluations import digest
from app.services.community.rowid_guards import rowid_guard_definitions
from app.services.community.store import CommunityStoreConflict

LEASE_PROFILE = 'preparation_lease_v1'
LEASE_TABLES = frozenset({'community_queue_work', 'community_queue_attempts', 'community_queue_operations'})


def lease_index_definitions():
    """Bound current-state lookups to one work item's ordered history instead of repeatedly scanning all operations."""
    return {'community_queue_operations_work_sequence': 'CREATE INDEX community_queue_operations_work_sequence ON community_queue_operations(work_id,sequence DESC)'}


def lease_definitions():
    """Retain exact selection/attempt identity and immutable ordered operation receipts, including raw rowid collisions."""
    tables = {
        'community_queue_work': '''CREATE TABLE community_queue_work(
            id TEXT PRIMARY KEY NOT NULL,plan_id TEXT NOT NULL REFERENCES community_queue_plans(id),
            selection_revision INTEGER NOT NULL CHECK(selection_revision>0),entry_key TEXT NOT NULL,
            payload TEXT NOT NULL,payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64),
            created_at_us INTEGER NOT NULL CHECK(created_at_us>=0),UNIQUE(plan_id,selection_revision,entry_key))''',
        'community_queue_attempts': '''CREATE TABLE community_queue_attempts(
            id TEXT PRIMARY KEY NOT NULL,work_id TEXT NOT NULL REFERENCES community_queue_work(id),
            number INTEGER NOT NULL CHECK(number BETWEEN 1 AND 3),claim_key TEXT NOT NULL UNIQUE,
            owner TEXT NOT NULL,token TEXT NOT NULL CHECK(length(token)=64),
            created_at_us INTEGER NOT NULL CHECK(created_at_us>=0),deadline_us INTEGER NOT NULL CHECK(deadline_us>created_at_us),
            UNIQUE(work_id,number))''',
        'community_queue_operations': '''CREATE TABLE community_queue_operations(
            sequence INTEGER PRIMARY KEY AUTOINCREMENT CHECK(sequence>0),request_key TEXT NOT NULL UNIQUE,
            intent_hash TEXT NOT NULL CHECK(length(intent_hash)=64),
            kind TEXT NOT NULL CHECK(kind IN('materialize','claim','heartbeat','finish','cancel','recover')),
            work_id TEXT REFERENCES community_queue_work(id),attempt_id TEXT REFERENCES community_queue_attempts(id),
            payload TEXT NOT NULL,payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64),
            created_at_us INTEGER NOT NULL CHECK(created_at_us>=0))''',
    }
    guards = {}
    for table in LEASE_TABLES:
        for action in ('UPDATE', 'DELETE'):
            name = table + '_no_' + action.lower()
            guards[name] = f"CREATE TRIGGER {name} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Immutable preparation history'); END"
        condition = {
            'community_queue_work': 'id=NEW.id OR (plan_id=NEW.plan_id AND selection_revision=NEW.selection_revision AND entry_key=NEW.entry_key)',
            'community_queue_attempts': 'id=NEW.id OR claim_key=NEW.claim_key OR (work_id=NEW.work_id AND number=NEW.number)',
            'community_queue_operations': 'sequence=NEW.sequence OR request_key=NEW.request_key',
        }[table]
        name = table + '_no_replace'
        guards[name] = f"CREATE TRIGGER {name} BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE {condition}) BEGIN SELECT RAISE(ABORT,'Preparation replacement rejected'); END"
    guards.update(rowid_guard_definitions(LEASE_TABLES))
    guards['community_queue_work_scope'] = '''CREATE TRIGGER community_queue_work_scope BEFORE INSERT ON community_queue_work WHEN
        json_extract(NEW.payload,'$.profile') IS NOT 'preparation_lease_v1'
        OR json_extract(NEW.payload,'$.execution_authorized') IS NOT 0
        OR json_extract(NEW.payload,'$.development_complete') IS NOT 0
        OR json_extract(NEW.payload,'$.external_model_calls') IS NOT 0
        OR json_extract(NEW.payload,'$.paid_authorization') IS NOT 0
        OR json_extract(NEW.payload,'$.public_deployment_verified') IS NOT 0
        OR json_extract(NEW.payload,'$.entry.key') IS NOT NEW.entry_key
        OR json_extract(NEW.payload,'$.entry.capacity_selected') IS NOT 1
        OR json_extract(NEW.payload,'$.plan_id') IS NOT NEW.plan_id
        OR json_extract(NEW.payload,'$.selection_revision') IS NOT NEW.selection_revision
        OR NOT EXISTS(SELECT 1 FROM community_queue_plan_events e WHERE e.to_revision=NEW.selection_revision AND e.plan_id=NEW.plan_id)
        BEGIN SELECT RAISE(ABORT,'Invalid preparation selection scope'); END'''
    guards['community_queue_attempt_scope'] = '''CREATE TRIGGER community_queue_attempt_scope BEFORE INSERT ON community_queue_attempts WHEN
        NEW.number!=(SELECT count(*)+1 FROM community_queue_attempts WHERE work_id=NEW.work_id)
        OR NEW.number>(SELECT json_extract(policy,'$.max_attempts') FROM community_queue_policy WHERE singleton=1)
        OR NEW.deadline_us-NEW.created_at_us>(SELECT json_extract(policy,'$.attempt_timeout_seconds')*1000000 FROM community_queue_policy WHERE singleton=1)
        BEGIN SELECT RAISE(ABORT,'Invalid bounded preparation attempt'); END'''
    guards['community_queue_operation_scope'] = '''CREATE TRIGGER community_queue_operation_scope BEFORE INSERT ON community_queue_operations WHEN
        json_extract(NEW.payload,'$.profile') IS NOT 'preparation_lease_v1'
        OR json_extract(NEW.payload,'$.execution_authorized') IS NOT 0
        OR json_extract(NEW.payload,'$.development_complete') IS NOT 0
        OR json_extract(NEW.payload,'$.external_model_calls') IS NOT 0
        OR json_extract(NEW.payload,'$.paid_authorization') IS NOT 0
        OR json_extract(NEW.payload,'$.public_deployment_verified') IS NOT 0
        OR json_extract(NEW.payload,'$.work_id') IS NOT NEW.work_id
        OR json_extract(NEW.payload,'$.attempt_id') IS NOT NEW.attempt_id
        OR (NEW.kind='materialize' AND (NEW.work_id IS NOT NULL OR NEW.attempt_id IS NOT NULL))
        OR (NEW.kind!='materialize' AND NEW.work_id IS NULL)
        OR (NEW.attempt_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM community_queue_attempts a WHERE a.id=NEW.attempt_id AND a.work_id=NEW.work_id))
        BEGIN SELECT RAISE(ABORT,'Invalid preparation operation scope'); END'''
    return tables, guards


def validate_lease_schema(db):
    """Accept only this complete non-executing profile; a later development executor must use a distinct protocol."""
    tables, guards = lease_definitions()
    for kind, definitions in (('table', tables), ('trigger', guards), ('index', lease_index_definitions())):
        for name, expected in definitions.items():
            saved = db.execute('SELECT sql FROM sqlite_master WHERE type=? AND name=?', (kind, name)).fetchone()
            if saved is None or ' '.join(saved[0].strip().rstrip(';').split()) != ' '.join(expected.strip().rstrip(';').split()):
                raise CommunityStoreConflict('Preparation reservation schema or guard changed')
    # Reopening validates retained receipts too, rather than accepting a
    # syntactically correct table containing a substituted permission profile.
    for table in ('community_queue_work', 'community_queue_operations'):
        for raw, checksum in db.execute('SELECT payload,payload_hash FROM ' + table):
            payload = json.loads(raw)
            if (digest(payload) != checksum or payload.get('profile') != LEASE_PROFILE
                    or payload.get('execution_authorized') is not False or payload.get('development_complete') is not False
                    or payload.get('public_deployment_verified') is not False
                    or type(payload.get('external_model_calls')) is not int or payload['external_model_calls'] != 0
                    or type(payload.get('paid_authorization')) is not int or payload['paid_authorization'] != 0):
                raise CommunityStoreConflict('Preparation reservation receipt changed')
