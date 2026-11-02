"""Finite, recoverable local preparation leases without starting code, subprocesses, AI or deployment."""
from contextlib import closing, contextmanager
import hmac
import json
import secrets
import uuid

from app.core.contracts.community_queue import (
    QueueMaterializeInput, QueueWorkInput, QueueClaimInput, QueueLeaseInput,
    QueueFinishInput, QueueCancelInput,
)
from app.services.community.evaluations import canonical, digest, source_row, validate_proof
from app.services.community.public_source import verify_public_issue
from app.services.community.queue_lease_schema import LEASE_PROFILE, LEASE_TABLES, lease_definitions, lease_index_definitions, validate_lease_schema
from app.services.community.store import CommunityStoreConflict

MAX_WORK_ITEMS = 2048
MAX_OPERATIONS = 50000


def preparation_flags():
    """Make every receipt's limited authority explicit, including terminal preparation success."""
    return {'profile': LEASE_PROFILE, 'execution_authorized': False, 'development_complete': False,
            'external_model_calls': 0, 'paid_authorization': 0, 'public_deployment_verified': False}


class CommunityPreparationStore:
    """Reserve bounded preparation capacity in the dedicated public-request store, never a personal video workspace."""

    def __init__(self, queue, *, initialize=False):
        """Initialize the exact optional profile once; reopening cannot increase immutable root limits."""
        self.queue = queue
        self.intake = queue.intake
        self.policy = queue.policy
        with self._writer() as db:
            present = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")} & LEASE_TABLES
            if not present:
                if not initialize:
                    raise CommunityStoreConflict('Preparation reservations have not been initialized')
                tables, guards = lease_definitions()
                for definition in tables.values():
                    db.execute(definition)
                for definition in lease_index_definitions().values():
                    db.execute(definition)
                for definition in guards.values():
                    db.execute(definition)
            validate_lease_schema(db)

    @contextmanager
    def _writer(self):
        """Serialize state checks and immutable appends in one short SQLite transaction, with no network inside it."""
        with closing(self.intake.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def _now(self, db, *, required=True):
        """Fail closed on wall-clock rollback; original receipts can still be recovered with an invalid live lease."""
        now = self.queue.clock_us()
        highest = db.execute('''SELECT max(moment) FROM (
            SELECT max(created_at_us) AS moment FROM community_queue_operations
            UNION ALL SELECT max(created_at_us) FROM community_queue_plans
            UNION ALL SELECT max(created_at_us) FROM community_queue_plan_events
            UNION ALL SELECT max(created_at_us) FROM community_queue_attempts)''').fetchone()[0] or 0
        valid = type(now) is int and now >= highest and now >= 0
        if not valid and required:
            raise CommunityStoreConflict('Preparation clock moved backwards or is invalid')
        return now if valid else None

    def _intent(self, kind, body, expected):
        """Validate even trusted model copies and bind each stable key to the exact operation and scope."""
        if type(body) is not expected:
            raise CommunityStoreConflict('Strict preparation operation input required')
        value = expected.model_validate(body.model_dump()).model_dump()
        return digest({'domain': 'community-preparation-operation-v1', 'kind': kind, 'input': value})

    def _work(self, db, identifier):
        """Verify the retained work snapshot against the exact immutable plan entry and selection revision."""
        row = db.execute('SELECT plan_id,selection_revision,entry_key,payload,payload_hash FROM community_queue_work WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise CommunityStoreConflict('Preparation work not found')
        payload = json.loads(row[3])
        plan = db.execute('SELECT payload,payload_hash FROM community_queue_plans WHERE id=?', (row[0],)).fetchone()
        if (digest(payload) != row[4] or payload.get('profile') != LEASE_PROFILE
                or payload.get('plan_id') != row[0] or payload.get('selection_revision') != row[1]
                or payload.get('plan_hash') != plan[1]
                or payload.get('entry') not in json.loads(plan[0])['entries']
                or payload['entry']['key'] != row[2] or not payload['entry']['capacity_selected']):
            raise CommunityStoreConflict('Preparation work receipt changed')
        return payload

    def _state(self, db, identifier):
        """Derive current state from explicit immutable integer event order, never implicit rowid or timestamp ties."""
        row = db.execute('SELECT sequence,payload,payload_hash FROM community_queue_operations WHERE work_id=? ORDER BY sequence DESC LIMIT 1', (identifier,)).fetchone()
        if row is None:
            return {'state': 'queued', 'attempt_id': None, 'lease_until_us': None, 'sequence': None}
        payload = json.loads(row[1])
        if digest(payload) != row[2]:
            raise CommunityStoreConflict('Preparation operation receipt changed')
        return {**payload, 'sequence': row[0]}

    def _scope_current(self, db, work):
        """Reselecting even the same plan is a different selection; stale tokens cannot cross that boundary."""
        current = db.execute('SELECT revision,plan_id FROM community_queue_current WHERE singleton=1').fetchone()
        if current != (work['selection_revision'], work['plan_id']):
            return False
        try:
            view = self.queue.view(db, work['plan_id'])
        except CommunityStoreConflict:
            return False
        return view['feature_catalog_current'] and all(view['current_original_assessments'].values())

    def _attempt(self, db, identifier):
        """Resolve private ownership separately; public/history views never return the saved raw lease token."""
        row = db.execute('SELECT id,work_id,number,owner,token,created_at_us,deadline_us FROM community_queue_attempts WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise CommunityStoreConflict('Preparation attempt not found')
        return dict(zip(('id', 'work_id', 'number', 'owner', 'token', 'created_at_us', 'deadline_us'), row))

    def _lease_valid(self, db, work, state, now):
        """Only a live preparation reservation with the original current scope is a usable lease."""
        if now is None or state['state'] != 'leased' or not self._scope_current(db, work):
            return False
        attempt = self._attempt(db, state['attempt_id'])
        return attempt['created_at_us'] <= now < min(state['lease_until_us'], attempt['deadline_us'])

    def _view(self, db, identifier):
        """Expose immutable work, current preparation status and bounded history without exposing any token."""
        work = self._work(db, identifier)
        state = self._state(db, identifier)
        now = self._now(db, required=False)
        attempts = db.execute('SELECT id,number,owner,created_at_us,deadline_us FROM community_queue_attempts WHERE work_id=? ORDER BY number', (identifier,)).fetchall()
        return {'id': identifier, 'work': work, 'current': state,
                'clock_valid': now is not None, 'lease_valid': self._lease_valid(db, work, state, now),
                'attempts': [dict(zip(('id', 'number', 'owner', 'created_at_us', 'deadline_us'), row)) for row in attempts],
                **preparation_flags()}

    def _operation(self, db, request_key, intent):
        """Original-key recovery precedes new source, clock and current-plan gates; it never creates another attempt."""
        row = db.execute('SELECT sequence,intent_hash,payload,payload_hash FROM community_queue_operations WHERE request_key=?', (request_key,)).fetchone()
        if row is None:
            return None
        if row[1] != intent:
            raise CommunityStoreConflict('Preparation key belongs to another original intent')
        payload = json.loads(row[2])
        if digest(payload) != row[3]:
            raise CommunityStoreConflict('Preparation original receipt changed')
        return row[0], payload

    def _result(self, db, saved, *, replayed, owner=None):
        """Separate original committed state from live state; only original claim recovery may reveal a still-valid token."""
        sequence, payload = saved
        work_id = payload.get('work_id')
        result = {'operation_sequence': sequence, 'receipt': payload, 'replayed': replayed, **preparation_flags()}
        if work_id:
            result['current_work'] = self._view(db, work_id)
            if owner is not None and payload.get('attempt_id'):
                attempt = self._attempt(db, payload['attempt_id'])
                current = result['current_work']
                if current['lease_valid'] and current['current']['attempt_id'] == attempt['id'] and attempt['owner'] == owner:
                    result['lease_token'] = attempt['token']
        return result

    def _append(self, db, kind, body, intent, now, *, attempt_id=None, **payload):
        """Commit one bounded operation; tokens and supplied issue text never enter event payloads or commands."""
        if db.execute('SELECT count(*) FROM community_queue_operations').fetchone()[0] >= MAX_OPERATIONS:
            raise CommunityStoreConflict('Preparation operation history capacity reached')
        work_id = getattr(body, 'work_id', None)
        value = {**payload, 'work_id': work_id, 'attempt_id': attempt_id, 'created_at_us': now, **preparation_flags()}
        saved = db.execute('''INSERT INTO community_queue_operations(request_key,intent_hash,kind,work_id,attempt_id,payload,payload_hash,created_at_us)
            VALUES(?,?,?,?,?,?,?,?)''', (body.request_key, intent, kind, work_id, attempt_id, canonical(value), digest(value), now))
        return saved.lastrowid, value

    def replay(self, kind, body, expected):
        """Recover a committed operation before an async caller considers any external source read."""
        intent = self._intent(kind, body, expected)
        with closing(self.intake.connect()) as db:
            saved = self._operation(db, body.request_key, intent)
            return None if saved is None else self._result(db, saved, replayed=True, owner=body.owner if kind == 'claim' else None)

    def materialize(self, body):
        """Create only capacity-selected preparation records, bound to the exact current plan selection revision."""
        intent = self._intent('materialize', body, QueueMaterializeInput)
        with self._writer() as db:
            saved = self._operation(db, body.request_key, intent)
            if saved:
                return self._result(db, saved, replayed=True)
            now = self._now(db)
            current = db.execute('SELECT revision,plan_id FROM community_queue_current WHERE singleton=1').fetchone()
            if current != (body.expected_revision, body.plan_id):
                raise CommunityStoreConflict('Original preparation selection changed')
            plan = self.queue.view(db, body.plan_id)
            if not plan['feature_catalog_current'] or not all(plan['current_original_assessments'].values()):
                raise CommunityStoreConflict('Original preparation assessments or catalog changed')
            work_ids = []
            for entry in plan['plan']['entries']:
                if not entry['capacity_selected']:
                    continue
                existing = db.execute('SELECT id FROM community_queue_work WHERE plan_id=? AND selection_revision=? AND entry_key=?', (body.plan_id, body.expected_revision, entry['key'])).fetchone()
                if existing:
                    work_ids.append(existing[0])
                    continue
                if db.execute('SELECT count(*) FROM community_queue_work').fetchone()[0] >= MAX_WORK_ITEMS:
                    raise CommunityStoreConflict('Preparation work history capacity reached')
                identifier = str(uuid.uuid4())
                work = {'plan_id': body.plan_id, 'selection_revision': body.expected_revision, 'plan_hash': plan['content_hash'], 'entry': entry, **preparation_flags()}
                db.execute('INSERT INTO community_queue_work VALUES(?,?,?,?,?,?,?)', (identifier, body.plan_id, body.expected_revision, entry['key'], canonical(work), digest(work), now))
                work_ids.append(identifier)
            saved = self._append(db, 'materialize', body, intent, now, work_ids=work_ids, plan_id=body.plan_id, selection_revision=body.expected_revision)
            return self._result(db, saved, replayed=False)

    def _require_claim_scope(self, db, work):
        """Keep predecessor preparation, current source/catalog and real feature qualifications separate."""
        if not self._scope_current(db, work):
            raise CommunityStoreConflict('Preparation plan, assessment, source or catalog changed')
        for key in work['entry']['after']:
            parent = db.execute('SELECT id FROM community_queue_work WHERE plan_id=? AND selection_revision=? AND entry_key=?', (work['plan_id'], work['selection_revision'], key)).fetchone()
            if parent is None or self._state(db, parent[0])['state'] != 'prepared':
                raise CommunityStoreConflict('Predecessor preparation is incomplete')

    def claim(self, body, proof):
        """Serialize exact-source rechecks and both capacity caps; trusted code supplies proof, never caller JSON."""
        intent = self._intent('claim', body, QueueClaimInput)
        with self._writer() as db:
            saved = self._operation(db, body.request_key, intent)
            if saved:
                return self._result(db, saved, replayed=True, owner=body.owner)
            now = self._now(db)
            work = self._work(db, body.work_id)
            self._require_claim_scope(db, work)
            if self._state(db, body.work_id)['state'] != 'queued':
                raise CommunityStoreConflict('Preparation work is not queued')
            validate_proof(self.intake.config, source_row(db, work['entry']['version_id']), proof, now)
            count = db.execute('SELECT count(*) FROM community_queue_attempts WHERE work_id=?', (body.work_id,)).fetchone()[0]
            if count >= self.policy.max_attempts:
                raise CommunityStoreConflict('Preparation attempt limit reached')
            active = []
            for (identifier,) in db.execute('SELECT id FROM community_queue_work'):
                if self._state(db, identifier)['state'] == 'leased':
                    active.append(self._work(db, identifier))
            if len(active) >= self.policy.max_concurrent or sum(item['entry']['effort_minutes'] for item in active) + work['entry']['effort_minutes'] > self.policy.max_active_effort_minutes:
                raise CommunityStoreConflict('Preparation concurrency or effort capacity reached; recover expired reservations first')
            if any(item['entry']['request_id'] == work['entry']['request_id'] for item in active):
                raise CommunityStoreConflict('Original requirement already has an active preparation')
            identifier = str(uuid.uuid4())
            deadline = now + self.policy.attempt_timeout_seconds * 1000000
            token = secrets.token_hex(32)
            db.execute('INSERT INTO community_queue_attempts VALUES(?,?,?,?,?,?,?,?)', (identifier, body.work_id, count + 1, body.request_key, body.owner, token, now, deadline))
            saved = self._append(db, 'claim', body, intent, now, attempt_id=identifier, state='leased', owner=body.owner,
                                 lease_until_us=min(deadline, now + self.policy.lease_seconds * 1000000), source_observation=proof.receipt())
            return self._result(db, saved, replayed=False, owner=body.owner)

    def _owned(self, db, body, now):
        """Fence superseded, expired and wrong-owner/token attempts before any heartbeat or preparation completion."""
        work = self._work(db, body.work_id)
        state = self._state(db, body.work_id)
        attempt = self._attempt(db, body.attempt_id)
        if (attempt['work_id'] != body.work_id or attempt['owner'] != body.owner
                or not hmac.compare_digest(attempt['token'], body.lease_token)
                or state['attempt_id'] != body.attempt_id or not self._lease_valid(db, work, state, now)):
            raise CommunityStoreConflict('Preparation lease is no longer owned and current')
        return work, state, attempt

    def heartbeat(self, body):
        """Renew only the current lease, capped by its immutable hard deadline; replay never extends it again."""
        intent = self._intent('heartbeat', body, QueueLeaseInput)
        with self._writer() as db:
            saved = self._operation(db, body.request_key, intent)
            if saved:
                return self._result(db, saved, replayed=True)
            now = self._now(db)
            _work, _state, attempt = self._owned(db, body, now)
            saved = self._append(db, 'heartbeat', body, intent, now, attempt_id=body.attempt_id, state='leased', owner=body.owner,
                                 lease_until_us=min(attempt['deadline_us'], now + self.policy.lease_seconds * 1000000))
            return self._result(db, saved, replayed=False)

    def finish(self, body, proof=None):
        """Record preparation only; failures may retry finitely, and success needs a fresh exact original source."""
        intent = self._intent('finish', body, QueueFinishInput)
        with self._writer() as db:
            saved = self._operation(db, body.request_key, intent)
            if saved:
                return self._result(db, saved, replayed=True)
            now = self._now(db)
            work, _state, attempt = self._owned(db, body, now)
            if body.outcome == 'prepared':
                validate_proof(self.intake.config, source_row(db, work['entry']['version_id']), proof, now)
            state = 'prepared' if body.outcome == 'prepared' else 'queued' if attempt['number'] < self.policy.max_attempts else 'failed'
            saved = self._append(db, 'finish', body, intent, now, attempt_id=body.attempt_id, state=state, outcome=body.outcome, note=body.note,
                                 lease_until_us=None, source_observation=proof.receipt() if body.outcome == 'prepared' else None)
            return self._result(db, saved, replayed=False)

    def cancel(self, body):
        """Cancel queued/leased preparation without claiming to stop a code process: this profile starts none."""
        intent = self._intent('cancel', body, QueueCancelInput)
        with self._writer() as db:
            saved = self._operation(db, body.request_key, intent)
            if saved:
                return self._result(db, saved, replayed=True)
            now = self._now(db)
            self._work(db, body.work_id)
            state = self._state(db, body.work_id)
            if state['state'] not in {'queued', 'leased'}:
                raise CommunityStoreConflict('Preparation is already terminal')
            saved = self._append(db, 'cancel', body, intent, now, attempt_id=state['attempt_id'], state='cancelled', reason=body.reason, lease_until_us=None)
            return self._result(db, saved, replayed=False)

    def recover(self, body):
        """Expire only side-effect-free preparation reservations; unknown real external outcomes must never use this protocol."""
        intent = self._intent('recover', body, QueueWorkInput)
        with self._writer() as db:
            saved = self._operation(db, body.request_key, intent)
            if saved:
                return self._result(db, saved, replayed=True)
            now = self._now(db)
            work = self._work(db, body.work_id)
            state = self._state(db, body.work_id)
            if state['state'] != 'leased':
                raise CommunityStoreConflict('Preparation has no lease to recover')
            attempt = self._attempt(db, state['attempt_id'])
            current = self._scope_current(db, work)
            if current and now < min(state['lease_until_us'], attempt['deadline_us']):
                raise CommunityStoreConflict('Preparation lease is still current')
            target = 'cancelled' if not current else 'queued' if attempt['number'] < self.policy.max_attempts else 'failed'
            saved = self._append(db, 'recover', body, intent, now, attempt_id=attempt['id'], state=target, lease_until_us=None,
                                 reason='selection_or_source_superseded' if not current else 'lease_or_deadline_expired')
            return self._result(db, saved, replayed=False)

    def get_work(self, identifier):
        """Read exact preparation state after restart without source network or raw tokens."""
        with closing(self.intake.connect()) as db:
            return self._view(db, identifier)

    def list_work(self, plan_id):
        """List bounded records for an exact plan, retaining selection revisions and current preparation state."""
        with closing(self.intake.connect()) as db:
            return [self._view(db, row[0]) for row in db.execute('SELECT id FROM community_queue_work WHERE plan_id=? ORDER BY selection_revision,entry_key', (plan_id,))]

    def history(self, identifier):
        """Return immutable ordered operations, excluding raw tokens even for local maintainers."""
        with closing(self.intake.connect()) as db:
            self._work(db, identifier)
            result = []
            for sequence, kind, raw, checksum in db.execute('SELECT sequence,kind,payload,payload_hash FROM community_queue_operations WHERE work_id=? ORDER BY sequence', (identifier,)):
                payload = json.loads(raw)
                if digest(payload) != checksum:
                    raise CommunityStoreConflict('Preparation history changed')
                result.append({'sequence': sequence, 'kind': kind, 'receipt': payload})
            return result


async def claim_current_public_source(store, body):
    """Use the fixed owned public verifier only for a new claim, then recheck everything under the SQLite writer."""
    original = store.replay('claim', body, QueueClaimInput)
    if original is not None:
        return original
    with closing(store.intake.connect()) as db:
        work = store._work(db, body.work_id)
        store._require_claim_scope(db, work)
        source = source_row(db, work['entry']['version_id'])
    proof = await verify_public_issue(store.intake.config, source['issue_number'])
    return store.claim(body, proof)


async def finish_current_public_source(store, body):
    """Prepared success needs a fresh source; failure and original recovery require no network or external cost."""
    original = store.replay('finish', body, QueueFinishInput)
    if original is not None:
        return original
    proof = None
    if body.outcome == 'prepared':
        with closing(store.intake.connect()) as db:
            work, _state, _attempt = store._owned(db, body, store._now(db))
            source = source_row(db, work['entry']['version_id'])
        proof = await verify_public_issue(store.intake.config, source['issue_number'])
    return store.finish(body, proof)
