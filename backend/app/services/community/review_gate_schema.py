"""A dedicated immutable policy/observation schema, independent of personal, intake and owned-container databases."""
from app.services.community.rowid_guards import rowid_guard_definitions


def gate_definitions():
    """Fence exact policy/source, natural/physical replacement, retained observation order, finite storage and all non-authority flags in SQLite."""
    tables={
        'community_pr_gate_policy':'''CREATE TABLE community_pr_gate_policy(singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            version INTEGER NOT NULL CHECK(version=1),profile TEXT NOT NULL CHECK(profile='github_exact_pr_review_v1'),
            repository_id INTEGER NOT NULL CHECK(repository_id>0),repository_name TEXT NOT NULL,
            policy TEXT NOT NULL CHECK(json_valid(policy)),policy_hash TEXT NOT NULL UNIQUE CHECK(length(policy_hash)=64))''',
        'community_pr_gate_observations':'''CREATE TABLE community_pr_gate_observations(sequence INTEGER PRIMARY KEY AUTOINCREMENT CHECK(sequence>0),
            id TEXT NOT NULL UNIQUE CHECK(length(id)=64),request_key TEXT NOT NULL UNIQUE,request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
            policy_hash TEXT NOT NULL REFERENCES community_pr_gate_policy(policy_hash),payload TEXT NOT NULL CHECK(json_valid(payload) AND length(CAST(payload AS BLOB))<=65536),
            recorded_at_us INTEGER NOT NULL CHECK(recorded_at_us>=0))'''}
    guards={}
    for table in tables:
        for action in ('UPDATE','DELETE'):
            name=table+'_no_'+action.lower()
            guards[name]=f"CREATE TRIGGER {name} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Immutable PR gate record'); END"
        name=table+'_no_replace'
        condition='singleton=NEW.singleton OR policy_hash=NEW.policy_hash' if table.endswith('_policy') else 'id=NEW.id OR request_key=NEW.request_key'
        guards[name]=f"CREATE TRIGGER {name} BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE {condition}) BEGIN SELECT RAISE(ABORT,'PR gate replacement rejected'); END"
    guards.update(rowid_guard_definitions(tables))
    guards['community_pr_gate_scope']='''CREATE TRIGGER community_pr_gate_scope BEFORE INSERT ON community_pr_gate_observations WHEN
        NEW.policy_hash IS NOT (SELECT policy_hash FROM community_pr_gate_policy WHERE singleton=1)
        OR json_extract(NEW.payload,'$.id') IS NOT NEW.id OR json_extract(NEW.payload,'$.request_hash') IS NOT NEW.request_hash
        OR json_extract(NEW.payload,'$.input.request_key') IS NOT NEW.request_key
        OR json_extract(NEW.payload,'$.policy_hash') IS NOT NEW.policy_hash
        OR json_extract(NEW.payload,'$.recorded_at_us') IS NOT NEW.recorded_at_us
        OR json_extract(NEW.payload,'$.profile') IS NOT 'github_exact_pr_review_v1'
        OR json_extract(NEW.payload,'$.decision.profile') IS NOT 'github_exact_pr_review_v1'
        OR json_extract(NEW.payload,'$.decision.policy_hash') IS NOT NEW.policy_hash
        OR json_extract(NEW.payload,'$.decision.request_key') IS NOT NEW.request_key
        OR json_extract(NEW.payload,'$.decision.bundle_id') IS NOT json_extract(NEW.payload,'$.input.bundle_id')
        OR json_extract(NEW.payload,'$.decision.check_attempt_id') IS NOT json_extract(NEW.payload,'$.input.check_attempt_id')
        OR json_extract(NEW.payload,'$.decision.pr_number') IS NOT json_extract(NEW.payload,'$.input.pr_number')
        OR (json_type(NEW.payload,'$.decision.eligible_at_observation') IS NOT 'true' AND json_type(NEW.payload,'$.decision.eligible_at_observation') IS NOT 'false')
        OR json_type(NEW.payload,'$.decision.promotion_authorized') IS NOT 'false'
        OR json_type(NEW.payload,'$.decision.merge_authorized') IS NOT 'false'
        OR json_type(NEW.payload,'$.decision.deployment_authorized') IS NOT 'false'
        OR json_type(NEW.payload,'$.decision.candidate_tests_are_independent') IS NOT 'false'
        OR json_type(NEW.payload,'$.decision.paid_authorization') IS NOT 'integer' OR json_extract(NEW.payload,'$.decision.paid_authorization') IS NOT 0
        OR json_type(NEW.payload,'$.decision.external_model_calls') IS NOT 'integer' OR json_extract(NEW.payload,'$.decision.external_model_calls') IS NOT 0
        BEGIN SELECT RAISE(ABORT,'Invalid non-authority gate scope'); END'''
    guards['community_pr_gate_capacity']='''CREATE TRIGGER community_pr_gate_capacity BEFORE INSERT ON community_pr_gate_observations WHEN
        (SELECT count(*) FROM community_pr_gate_observations)>=(SELECT json_extract(policy,'$.max_saved_observations') FROM community_pr_gate_policy WHERE singleton=1)
        OR (SELECT coalesce(sum(length(CAST(payload AS BLOB))),0) FROM community_pr_gate_observations)+length(CAST(NEW.payload AS BLOB))>
            (SELECT json_extract(policy,'$.max_saved_bytes') FROM community_pr_gate_policy WHERE singleton=1)
        BEGIN SELECT RAISE(ABORT,'PR gate history capacity reached'); END'''
    return tables,guards
