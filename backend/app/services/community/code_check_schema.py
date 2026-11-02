"""Optional durable ownership before any isolated container mutation; unknown outcomes keep their reservation until concrete reconciliation."""
import json
from app.core.contracts.community_code_checks import LinuxCodeCheckPolicy
from app.services.community.evaluations import canonical, digest
from app.services.community.rowid_guards import rowid_guard_definitions
from app.services.community.store import CommunityStoreConflict

CHECK_PROFILE = 'linux_pytest_check_v1'
CHECK_TABLES = frozenset({'community_code_check_policy', 'community_code_check_attempts', 'community_code_check_events'})
TERMINAL = frozenset({'passed', 'failed', 'timed_out', 'cancelled', 'interrupted'})


def check_definitions():
    """Keep exact policy/ownership/events immutable and fence global concurrency in the allocation transaction itself."""
    tables = {
        'community_code_check_policy': '''CREATE TABLE community_code_check_policy(singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            version INTEGER NOT NULL CHECK(version=1),profile TEXT NOT NULL CHECK(profile='linux_pytest_check_v1'),
            policy TEXT NOT NULL,policy_hash TEXT NOT NULL CHECK(length(policy_hash)=64))''',
        'community_code_check_attempts': '''CREATE TABLE community_code_check_attempts(id TEXT PRIMARY KEY NOT NULL,
            request_key TEXT NOT NULL UNIQUE,intent_hash TEXT NOT NULL CHECK(length(intent_hash)=64),
            bundle_id TEXT NOT NULL REFERENCES community_change_bundles(id),number INTEGER NOT NULL CHECK(number BETWEEN 1 AND 3),
            candidate_commit TEXT NOT NULL CHECK(length(candidate_commit)=40),candidate_tree TEXT NOT NULL CHECK(length(candidate_tree)=40),
            candidate_parent TEXT NOT NULL CHECK(length(candidate_parent)=40),image_id TEXT NOT NULL CHECK(length(image_id)=71),
            container_name TEXT NOT NULL UNIQUE,owner_token TEXT NOT NULL CHECK(length(owner_token)=64),
            created_at_us INTEGER NOT NULL CHECK(created_at_us>=0),deadline_us INTEGER NOT NULL CHECK(deadline_us=created_at_us+60000000),
            payload TEXT NOT NULL CHECK(length(CAST(payload AS BLOB))<=65536),payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64),
            UNIQUE(bundle_id,number))''',
        'community_code_check_events': '''CREATE TABLE community_code_check_events(sequence INTEGER PRIMARY KEY AUTOINCREMENT CHECK(sequence>0),
            operation_key TEXT NOT NULL UNIQUE,attempt_id TEXT NOT NULL REFERENCES community_code_check_attempts(id),
            phase TEXT NOT NULL CHECK(phase IN('allocated','create_requested','created','start_requested','observed','cancel_requested','cleanup_required','terminal')),
            payload TEXT NOT NULL CHECK(length(CAST(payload AS BLOB))<=65536),payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64),
            created_at_us INTEGER NOT NULL CHECK(created_at_us>=0),UNIQUE(attempt_id,phase))'''}
    guards = {}
    for table in tables:
        for action in ('UPDATE', 'DELETE'):
            name = table + '_no_' + action.lower()
            guards[name] = f"CREATE TRIGGER {name} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Immutable isolated check history'); END"
        name = table + '_no_replace'
        condition = {'community_code_check_policy': 'singleton=NEW.singleton',
            'community_code_check_attempts': 'id=NEW.id OR request_key=NEW.request_key OR container_name=NEW.container_name OR (bundle_id=NEW.bundle_id AND number=NEW.number)',
            'community_code_check_events': 'sequence=NEW.sequence OR operation_key=NEW.operation_key OR (attempt_id=NEW.attempt_id AND phase=NEW.phase)'}[table]
        guards[name] = f"CREATE TRIGGER {name} BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE {condition}) BEGIN SELECT RAISE(ABORT,'Isolated check replacement rejected'); END"
    guards.update(rowid_guard_definitions(tables))
    guards['community_code_check_allocate'] = '''CREATE TRIGGER community_code_check_allocate BEFORE INSERT ON community_code_check_attempts WHEN
        NEW.number!=(SELECT count(*)+1 FROM community_code_check_attempts WHERE bundle_id=NEW.bundle_id)
        OR (SELECT count(*) FROM community_code_check_attempts)>=384
        OR EXISTS(SELECT 1 FROM community_code_check_attempts a WHERE NOT EXISTS(
            SELECT 1 FROM community_code_check_events e WHERE e.attempt_id=a.id AND e.phase='terminal'))
        OR json_extract(NEW.payload,'$.profile') IS NOT 'linux_pytest_check_v1'
        OR json_extract(NEW.payload,'$.attempt_id') IS NOT NEW.id
        OR json_extract(NEW.payload,'$.request_key') IS NOT NEW.request_key
        OR json_extract(NEW.payload,'$.bundle_id') IS NOT NEW.bundle_id
        OR json_extract(NEW.payload,'$.number') IS NOT NEW.number
        OR json_extract(NEW.payload,'$.candidate_commit') IS NOT NEW.candidate_commit
        OR json_extract(NEW.payload,'$.candidate_tree') IS NOT NEW.candidate_tree
        OR json_extract(NEW.payload,'$.image_id') IS NOT NEW.image_id
        OR json_extract(NEW.payload,'$.container_name') IS NOT NEW.container_name
        OR json_extract(NEW.payload,'$.created_at_us') IS NOT NEW.created_at_us
        OR json_extract(NEW.payload,'$.deadline_us') IS NOT NEW.deadline_us
        OR json_extract(NEW.payload,'$.paid_authorization') IS NOT 0
        OR json_extract(NEW.payload,'$.external_model_calls') IS NOT 0
        OR json_extract(NEW.payload,'$.independent_review_completed') IS NOT 0
        OR json_extract(NEW.payload,'$.ci_qualified') IS NOT 0
        OR json_extract(NEW.payload,'$.deployment_authorized') IS NOT 0
        BEGIN SELECT RAISE(ABORT,'Invalid bounded check allocation or unresolved earlier outcome'); END'''
    guards['community_code_check_event_scope'] = '''CREATE TRIGGER community_code_check_event_scope BEFORE INSERT ON community_code_check_events WHEN
        json_extract(NEW.payload,'$.profile') IS NOT 'linux_pytest_check_v1'
        OR json_extract(NEW.payload,'$.attempt_id') IS NOT NEW.attempt_id
        OR json_extract(NEW.payload,'$.phase') IS NOT NEW.phase
        OR json_extract(NEW.payload,'$.paid_authorization') IS NOT 0
        OR json_extract(NEW.payload,'$.external_model_calls') IS NOT 0
        OR json_extract(NEW.payload,'$.independent_review_completed') IS NOT 0
        OR json_extract(NEW.payload,'$.ci_qualified') IS NOT 0
        OR json_extract(NEW.payload,'$.deployment_authorized') IS NOT 0
        OR (NEW.phase='terminal' AND (json_extract(NEW.payload,'$.details.cleanup_verified') IS NOT 1
            OR coalesce(json_extract(NEW.payload,'$.details.outcome'),'') NOT IN('passed','failed','timed_out','cancelled','interrupted')))
        BEGIN SELECT RAISE(ABORT,'Invalid isolated check event scope'); END'''
    guards['community_code_check_event_order'] = '''CREATE TRIGGER community_code_check_event_order BEFORE INSERT ON community_code_check_events WHEN
        EXISTS(SELECT 1 FROM community_code_check_events WHERE attempt_id=NEW.attempt_id AND phase='terminal')
        OR (NEW.phase='allocated' AND EXISTS(SELECT 1 FROM community_code_check_events WHERE attempt_id=NEW.attempt_id))
        OR (NEW.phase!='allocated' AND NOT EXISTS(SELECT 1 FROM community_code_check_events WHERE attempt_id=NEW.attempt_id AND phase='allocated'))
        OR (NEW.phase='create_requested' AND (EXISTS(SELECT 1 FROM community_code_check_events WHERE attempt_id=NEW.attempt_id AND phase IN('create_requested','created','start_requested','observed','cancel_requested','cleanup_required'))))
        OR (NEW.phase='created' AND NOT EXISTS(SELECT 1 FROM community_code_check_events WHERE attempt_id=NEW.attempt_id AND phase='create_requested'))
        OR (NEW.phase='start_requested' AND (NOT EXISTS(SELECT 1 FROM community_code_check_events WHERE attempt_id=NEW.attempt_id AND phase='created')
            OR EXISTS(SELECT 1 FROM community_code_check_events WHERE attempt_id=NEW.attempt_id AND phase IN('cancel_requested','cleanup_required'))))
        OR (NEW.phase='observed' AND NOT EXISTS(SELECT 1 FROM community_code_check_events WHERE attempt_id=NEW.attempt_id AND phase='start_requested'))
        OR (NEW.phase='terminal' AND EXISTS(SELECT 1 FROM community_code_check_events WHERE attempt_id=NEW.attempt_id AND phase='create_requested')
            AND NOT EXISTS(SELECT 1 FROM community_code_check_events WHERE attempt_id=NEW.attempt_id AND phase='created'))
        OR (NEW.phase='terminal' AND json_extract(NEW.payload,'$.details.outcome')='passed' AND (
            NOT EXISTS(SELECT 1 FROM community_code_check_events WHERE attempt_id=NEW.attempt_id AND phase='observed' AND json_extract(payload,'$.details.outcome')='passed')
            OR EXISTS(SELECT 1 FROM community_code_check_events WHERE attempt_id=NEW.attempt_id AND phase='cancel_requested')))
        BEGIN SELECT RAISE(ABORT,'Invalid isolated check phase order or unknown creation outcome'); END'''
    return tables, guards


def validate_check_schema(db):
    """Reopen only this complete profile and immutable fixed policy; runtime history never initializes itself by merely opening proposals."""
    tables, guards = check_definitions()
    for kind, definitions in (('table', tables), ('trigger', guards)):
        for name, expected in definitions.items():
            row = db.execute('SELECT sql FROM sqlite_master WHERE type=? AND name=?', (kind, name)).fetchone()
            if row is None or ' '.join(row[0].strip().rstrip(';').split()) != ' '.join(expected.strip().rstrip(';').split()):
                raise CommunityStoreConflict('Isolated check schema or guard changed')
    policy = LinuxCodeCheckPolicy(); expected = canonical(policy.model_dump())
    if db.execute('SELECT singleton,version,profile,policy,policy_hash FROM community_code_check_policy').fetchall() != [(1, 1, CHECK_PROFILE, expected, digest(policy.model_dump()))]:
        raise CommunityStoreConflict('Isolated check policy is missing or changed')
    if db.execute('PRAGMA foreign_key_check').fetchone():
        raise CommunityStoreConflict('Isolated check history references are inconsistent')
    return policy
