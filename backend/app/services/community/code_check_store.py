"""Durable owned-check allocation and immutable phases; unknown creation/start/cleanup never expires into automatic rerun."""
from contextlib import closing, contextmanager
from dataclasses import asdict
import hmac
import hashlib
import json
import re
import secrets
import uuid

from app.core.contracts.community_code_checks import (CommunityCodeCheckInput, CodeCheckAdmission, CodeCheckObservation, CodeCheckCleanup)
from app.services.community.candidate_git import CandidateCommit
from app.services.community.code_check_schema import CHECK_PROFILE, CHECK_TABLES, TERMINAL, check_definitions, validate_check_schema
from app.services.community.evaluations import canonical, digest
from app.services.community.store import CommunityStoreConflict


def check_flags():
    """A bounded container check is a distinct operation, without independent review, model expense, CI promotion or deployment authority."""
    return {'profile': CHECK_PROFILE, 'authorization_scope': 'owned_linux_container_check_only', 'paid_authorization': 0,
        'external_model_calls': 0, 'independent_review_completed': False, 'ci_qualified': False, 'deployment_authorized': False}


class CommunityCodeCheckStore:
    """Reserve one real check independently of preparation leases, before any Docker mutation, in the dedicated proposal database."""

    def __init__(self, changes, *, initialize=False):
        """Explicitly initialize the known optional three-table extension; ordinary proposal opening remains side-effect free."""
        self.changes = changes; self.config = changes.config
        with self._writer() as db:
            present = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")} & CHECK_TABLES
            if not present:
                if not initialize: raise CommunityStoreConflict('Isolated checks have not been explicitly initialized')
                from app.core.contracts.community_code_checks import LinuxCodeCheckPolicy
                policy = LinuxCodeCheckPolicy(); tables, guards = check_definitions()
                for definition in tables.values(): db.execute(definition)
                db.execute('INSERT INTO community_code_check_policy VALUES(1,1,?,?,?)', (CHECK_PROFILE, canonical(policy.model_dump()), digest(policy.model_dump())))
                for definition in guards.values(): db.execute(definition)
            self.policy = validate_check_schema(db)
            self._validate_history(db)

    @contextmanager
    def _writer(self):
        """Serialize identity/capacity/CAS appends with no daemon, source-network or candidate-code operation inside the transaction."""
        with closing(self.changes.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback(); raise

    def _now(self, db, *, reducing=False):
        """Reject new execution on rollback, but permit cancellation/audit/verified cleanup at the retained maximum logical timestamp."""
        now = self.changes.clock_us()
        previous = db.execute('''SELECT max(moment) FROM(
            SELECT max(created_at_us) AS moment FROM community_code_check_attempts
            UNION ALL SELECT max(created_at_us) FROM community_code_check_events)''').fetchone()[0] or 0
        valid = type(now) is int and now >= previous and now >= 0
        if not valid and not reducing: raise CommunityStoreConflict('Isolated check clock is invalid or moved backwards')
        return (now, 'wall_clock_us') if valid else (previous, 'retained_max_on_clock_rollback')

    def _private(self, db, identifier, owner=None):
        """Resolve private daemon ownership separately; public views never expose its unpredictable cleanup token."""
        row = db.execute('SELECT id,request_key,intent_hash,bundle_id,number,candidate_commit,candidate_tree,candidate_parent,image_id,container_name,owner_token,created_at_us,deadline_us,payload,payload_hash FROM community_code_check_attempts WHERE id=?', (identifier,)).fetchone()
        if row is None: raise CommunityStoreConflict('Isolated check not found')
        fields = ('id','request_key','intent_hash','bundle_id','number','candidate_commit','candidate_tree','candidate_parent','image_id','container_name','owner_token','created_at_us','deadline_us','payload','payload_hash')
        value = dict(zip(fields, row)); payload = json.loads(value['payload'])
        identity = {'attempt_id': value['id'], 'request_key': value['request_key'], 'bundle_id': value['bundle_id'], 'number': value['number'],
            'candidate_commit': value['candidate_commit'], 'candidate_tree': value['candidate_tree'], 'candidate_parent': value['candidate_parent'],
            'image_id': value['image_id'], 'container_name': value['container_name'], 'created_at_us': value['created_at_us'], 'deadline_us': value['deadline_us']}
        if (digest(payload) != value['payload_hash'] or payload.get('profile') != CHECK_PROFILE
                or any(payload.get(key) != expected for key, expected in identity.items())
                or payload.get('owner_token_sha256') != hashlib.sha256(value['owner_token'].encode('ascii')).hexdigest()
                or payload.get('input') != {'request_key': value['request_key'], 'bundle_id': value['bundle_id']}
                or digest({'domain': CHECK_PROFILE, 'input': payload.get('input'), 'candidate': payload.get('candidate'), 'image_id': value['image_id']}) != value['intent_hash']):
            raise CommunityStoreConflict('Isolated check ownership receipt changed')
        if owner is not None and (not isinstance(owner, str) or not hmac.compare_digest(value['owner_token'], owner)):
            raise CommunityStoreConflict('Isolated check is not owned by this controller')
        value['payload'] = payload
        return value

    def _events(self, db, identifier):
        """Use explicit integer event order and verify every saved payload hash before using it as control state."""
        result = []
        for sequence, phase, raw, checksum in db.execute('SELECT sequence,phase,payload,payload_hash FROM community_code_check_events WHERE attempt_id=? ORDER BY sequence', (identifier,)):
            payload = json.loads(raw)
            if digest(payload) != checksum or payload.get('attempt_id') != identifier or payload.get('phase') != phase:
                raise CommunityStoreConflict('Isolated check history changed')
            result.append({'sequence': sequence, 'phase': phase, 'receipt': payload})
        return result

    def _validate_history(self, db):
        """Refuse incomplete or substituted saved ownership/events on restart instead of assuming that an expired clock freed capacity."""
        for (identifier,) in db.execute('SELECT id FROM community_code_check_attempts'):
            value = self._private(db, identifier); events = self._events(db, identifier)
            if not events or events[0]['phase'] != 'allocated' or value['payload'].get('bundle_id') != value['bundle_id']:
                raise CommunityStoreConflict('Incomplete isolated check allocation')

    def _view(self, db, identifier):
        """Expose original check identity/current phase without treating candidate-test success as independent verification."""
        value = self._private(db, identifier); events = self._events(db, identifier)
        last = events[-1]
        created = next((event['receipt']['details']['container_id'] for event in events if event['phase'] == 'created'), None)
        terminal = last['phase'] == 'terminal'
        return {key: value[key] for key in ('id','request_key','bundle_id','number','candidate_commit','candidate_tree','candidate_parent','image_id','container_name','created_at_us','deadline_us')} | {
            'current_phase': last['phase'], 'current_receipt': last['receipt'], 'active_reservation': not terminal,
            'outcome': last['receipt']['details']['outcome'] if terminal else None, 'container_id': created,
            'cancel_requested': any(event['phase'] == 'cancel_requested' for event in events),
            'history': events, 'automatic_retry_authorized': False, **check_flags()}

    def _append(self, db, attempt, phase, details, *, reducing=False):
        """Recover an original phase before clock/order gates; a repeated phase cannot rewind state or replace its first immutable details."""
        key = attempt['id'] + ':' + phase
        previous = db.execute('SELECT payload FROM community_code_check_events WHERE operation_key=?', (key,)).fetchone()
        if previous:
            if json.loads(previous[0])['details'] != details: raise CommunityStoreConflict('Original check phase belongs to different details')
            return {**self._view(db, attempt['id']), 'phase_replayed': True}
        now, clock = self._now(db, reducing=reducing)
        if db.execute('SELECT count(*) FROM community_code_check_events').fetchone()[0] >= self.policy.max_events:
            raise CommunityStoreConflict('Isolated check audit capacity reached; existing reservation retained')
        payload = {'attempt_id': attempt['id'], 'phase': phase, 'details': details, 'recorded_at_us': now, 'clock_source': clock, **check_flags()}
        if len(canonical(payload).encode('utf8')) > 65536: raise CommunityStoreConflict('Isolated check event exceeds64KiB')
        db.execute('INSERT INTO community_code_check_events(operation_key,attempt_id,phase,payload,payload_hash,created_at_us) VALUES(?,?,?,?,?,?)',
            (key, attempt['id'], phase, canonical(payload), digest(payload), now))
        return {**self._view(db, attempt['id']), 'phase_replayed': False}

    def _intent(self, body, candidate, image_id):
        """Bind stable input to exact local commit/tree/base and immutable maintainer-selected image, with no arbitrary command field."""
        if type(body) is not CommunityCodeCheckInput or not isinstance(candidate, CandidateCommit) or not isinstance(image_id, str) or re.fullmatch(r'sha256:[0-9a-f]{64}', image_id) is None:
            raise CommunityStoreConflict('Strict check input, candidate reference and immutable image ID required')
        typed = CommunityCodeCheckInput.model_validate(body.model_dump())
        artifact = self.changes.get(typed.bundle_id); scope = artifact['bundle']['scope']
        if (candidate.bundle_id != typed.bundle_id or candidate.branch != artifact['branch'] or candidate.parent != scope['base_commit']
                or candidate.repository_id != self.config.repository_id or candidate.repository_name != self.config.repository_name.casefold()
                or re.fullmatch(r'[0-9a-f]{40}', candidate.commit) is None or re.fullmatch(r'[0-9a-f]{40}', candidate.tree) is None):
            raise CommunityStoreConflict('Check candidate differs from its original public bundle')
        return digest({'domain': CHECK_PROFILE, 'input': typed.model_dump(), 'candidate': candidate.receipt(), 'image_id': image_id}), artifact

    def replay(self, body, image_id):
        """Return the original allocation before source/Git/engine checks; recovery does not repeat Docker create or start."""
        if type(body) is not CommunityCodeCheckInput: raise CommunityStoreConflict('Strict original check input required')
        CommunityCodeCheckInput.model_validate(body.model_dump())
        with closing(self.changes.connect()) as db:
            row = db.execute('SELECT id,bundle_id,image_id FROM community_code_check_attempts WHERE request_key=?', (body.request_key,)).fetchone()
            if row is None: return None
            if row[1:] != (body.bundle_id, image_id): raise CommunityStoreConflict('Original check key belongs to another bundle or image')
            return {**self._view(db, row[0]), 'replayed': True}

    def allocate(self, body, candidate, image_id, admission):
        """Commit one owned reservation before Docker mutation; finite retries require a new key and every earlier outcome resolved."""
        intent, artifact = self._intent(body, candidate, image_id)
        with self._writer() as db:
            previous = db.execute('SELECT id,intent_hash FROM community_code_check_attempts WHERE request_key=?', (body.request_key,)).fetchone()
            if previous:
                if previous[1] != intent: raise CommunityStoreConflict('Original check key belongs to another exact candidate intent')
                return {**self._view(db, previous[0]), 'replayed': True}
            now, _clock = self._now(db)
            scope = artifact['bundle']['scope']
            if (not isinstance(admission, CodeCheckAdmission) or admission.bundle_id != body.bundle_id or admission.scope_hash != artifact['bundle']['scope_hash']
                    or admission.candidate_commit != candidate.commit or admission.candidate_tree != candidate.tree or admission.candidate_parent != candidate.parent
                    or admission.image_id != image_id or admission.repository_id != self.config.repository_id
                    or admission.repository_name.casefold() != self.config.repository_name.casefold() or admission.source_fingerprint != scope['source_fingerprint']
                    or type(admission.checked_at_us) is not int or not 0 <= now - admission.checked_at_us <= 60000000):
                raise CommunityStoreConflict('Fresh internal exact-source check admission required; preparation is not execution authority')
            number = db.execute('SELECT count(*)+1 FROM community_code_check_attempts WHERE bundle_id=?', (body.bundle_id,)).fetchone()[0]
            if number > self.policy.max_attempts_per_bundle: raise CommunityStoreConflict('Isolated check attempt limit reached')
            if db.execute("SELECT 1 FROM community_code_check_attempts a WHERE NOT EXISTS(SELECT 1 FROM community_code_check_events e WHERE e.attempt_id=a.id AND e.phase='terminal') LIMIT 1").fetchone():
                raise CommunityStoreConflict('Earlier isolated check outcome or cleanup remains unresolved')
            identifier = str(uuid.uuid4()); owner = secrets.token_hex(32); name = 'fawnimo-code-' + uuid.UUID(identifier).hex
            deadline = now + self.policy.controller_seconds * 1000000
            payload = {'attempt_id': identifier, 'request_key': body.request_key, 'bundle_id': body.bundle_id, 'number': number,
                'candidate_commit': candidate.commit, 'candidate_tree': candidate.tree, 'candidate_parent': candidate.parent,
                'image_id': image_id, 'container_name': name, 'created_at_us': now, 'deadline_us': deadline,
                'owner_token_sha256': hashlib.sha256(owner.encode('ascii')).hexdigest(),
                'input': body.model_dump(), 'candidate': candidate.receipt(), 'source_admission': asdict(admission), **check_flags()}
            db.execute('INSERT INTO community_code_check_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (identifier, body.request_key, intent, body.bundle_id, number, candidate.commit, candidate.tree, candidate.parent, image_id, name, owner,
                 now, deadline, canonical(payload), digest(payload)))
            attempt = self._private(db, identifier)
            return {**self._append(db, attempt, 'allocated', {'candidate_commit': candidate.commit, 'image_id': image_id}), 'replayed': False}

    def private_ownership(self, identifier):
        """Trusted local controller access only; no HTTP/CLI output should serialize this private cleanup token."""
        with closing(self.changes.connect()) as db: return self._private(db, identifier)

    def request_create(self, identifier, owner):
        """Record acceptance-unknown intent before the sole Docker create call; a missing future resource cannot alone free it."""
        with self._writer() as db:
            attempt = self._private(db, identifier, owner)
            if db.execute("SELECT 1 FROM community_code_check_events WHERE attempt_id=? AND phase='create_requested'", (identifier,)).fetchone():
                return self._append(db, attempt, 'create_requested', {})
            now, _clock = self._now(db)
            if now >= attempt['deadline_us']: raise CommunityStoreConflict('Check creation deadline spent; reconcile original ownership')
            return self._append(db, attempt, 'create_requested', {})

    def record_created(self, identifier, owner, container_id):
        """Save acknowledged or concretely rediscovered owned identity, even after cancellation/clock rollback, without starting it."""
        if not isinstance(container_id, str) or re.fullmatch(r'[0-9a-f]{64}', container_id) is None: raise CommunityStoreConflict('Exact owned container ID required')
        with self._writer() as db:
            return self._append(db, self._private(db, identifier, owner), 'created', {'container_id': container_id}, reducing=True)

    def request_start(self, identifier, owner):
        """Persist start intent before attaching input; cancellation and expired time stop new execution instead of silently clearing a slot."""
        with self._writer() as db:
            attempt = self._private(db, identifier, owner)
            if db.execute("SELECT 1 FROM community_code_check_events WHERE attempt_id=? AND phase='start_requested'", (identifier,)).fetchone():
                return self._append(db, attempt, 'start_requested', {})
            now, _clock = self._now(db)
            if now >= attempt['deadline_us']: raise CommunityStoreConflict('Check start deadline spent; reconcile original container')
            return self._append(db, attempt, 'start_requested', {})

    def observe(self, identifier, owner, observation):
        """Save only trusted-supervisor/OCI observations, with finite output and no candidate-written independent approval."""
        if (not isinstance(observation, CodeCheckObservation) or observation.outcome not in {'passed','failed','timed_out'}
                or type(observation.process_exit_code) is not int or not -128 <= observation.process_exit_code <= 255
                or type(observation.output_bytes) is not int or not 0 <= observation.output_bytes <= 65537
                or re.fullmatch(r'[0-9a-f]{64}', observation.supervisor_sha256) is None or re.fullmatch(r'[0-9a-f]{64}', observation.output_sha256) is None
                or any(getattr(observation, field) is not True for field in ('kernel_limits_verified','uid_separation_verified','namespace_isolation_verified'))
                or observation.outcome == 'passed' and (observation.process_exit_code != 0 or observation.output_bytes > self.policy.max_candidate_output_bytes)):
            raise CommunityStoreConflict('Valid owned supervisor, kernel and namespace observation required')
        with self._writer() as db:
            return self._append(db, self._private(db, identifier, owner), 'observed', asdict(observation), reducing=True)

    def request_cancel(self, identifier):
        """Persist cancellation intent immediately but keep capacity until daemon cleanup is actually verified."""
        with self._writer() as db:
            attempt = self._private(db, identifier)
            current = self._view(db, identifier)
            if not current['active_reservation']: return current
            return self._append(db, attempt, 'cancel_requested', {}, reducing=True)

    def cleanup_required(self, identifier, owner, reason):
        """Retain an unresolved reservation; this state is never an invitation to replay an unknown external create/start."""
        if reason not in {'controller_interrupted','create_outcome_unknown','start_outcome_unknown','daemon_unavailable','cleanup_unverified'}:
            raise CommunityStoreConflict('Unknown check reconciliation reason')
        with self._writer() as db:
            attempt = self._private(db, identifier, owner)
            if db.execute("SELECT 1 FROM community_code_check_events WHERE attempt_id=? AND phase='cleanup_required'", (identifier,)).fetchone():
                return {**self._view(db, identifier), 'phase_replayed': True}
            return self._append(db, attempt, 'cleanup_required', {'reason': reason}, reducing=True)

    def finish(self, identifier, owner, outcome, cleanup):
        """Release capacity only after exact owned cleanup, never from lease expiry or absence after unacknowledged creation."""
        if outcome not in TERMINAL or not isinstance(cleanup, CodeCheckCleanup): raise CommunityStoreConflict('Verified terminal check outcome and cleanup required')
        with self._writer() as db:
            attempt = self._private(db, identifier, owner); current = self._view(db, identifier)
            if (cleanup.attempt_id != identifier or cleanup.container_name != attempt['container_name'] or cleanup.image_id != attempt['image_id']
                    or not isinstance(cleanup.owner_token, str) or not hmac.compare_digest(cleanup.owner_token, attempt['owner_token'])
                    or cleanup.engine_observed is not True or cleanup.resource_absent is not True or cleanup.container_id != current['container_id']):
                raise CommunityStoreConflict('Cleanup does not prove exact owned resource absence')
            if current['cancel_requested'] and outcome == 'passed': outcome = 'cancelled'
            if any(event['phase'] == 'create_requested' for event in current['history']) and current['container_id'] is None:
                raise CommunityStoreConflict('Container creation acceptance is unknown; absence alone cannot release its reservation')
            return self._append(db, attempt, 'terminal', {'outcome': outcome, 'cleanup_verified': True, 'container_id': cleanup.container_id}, reducing=True)

    def get(self, identifier):
        """Read durable original/current check state after restart, without daemon contact or automatic execution."""
        with closing(self.changes.connect()) as db: return self._view(db, identifier)
