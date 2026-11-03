"""Private append-only local runtime intents, immutable generations and atomic current-slot pointers.

This journal is operator state, never an independent review or public deployment
approval. An interrupted intent keeps its slot reserved; reading it cannot resume
Docker mutations. Original-key replay happens before consulting today's policy.
"""
from contextlib import contextmanager, closing
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3

from app.controlplane.maintenance import ProcessLease
from app.controlplane.migration import flush_directory
from app.core.contracts.local_runtime_release import (
    LocalRuntimePolicy, LocalRuntimeActivation, LocalRuntimeRollback, LocalRuntimeStop,
)
from app.services.community.local_release_files import plain, read_json, write_json
from app.services.community.runtime_release_safety import checked_file, digest

PROFILE = 'local_runtime_release_v1'
TERMINAL = frozenset({'committed', 'stopped', 'recovered_previous', 'failed_closed'})
INTENTS = {'activate': LocalRuntimeActivation, 'rollback': LocalRuntimeRollback, 'stop': LocalRuntimeStop}
MAX_RECORD = 32768


class RuntimeReleaseError(ValueError):
    """Return fixed operational errors without reflecting private paths, credentials or daemon output."""


def canonical(value):
    """Bound and canonicalize trusted journal records; nonfinite or duplicate input fields are never silently accepted."""
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(text.encode('utf8')) > MAX_RECORD:
        raise RuntimeReleaseError('Local runtime record exceeds its fixed bound')
    return text


def opaque(value):
    """Only original opaque identifiers may address a runtime operation or archive."""
    if not isinstance(value, str) or re.fullmatch(r'[a-f0-9]{64}', value) is None:
        raise RuntimeReleaseError('An exact original local runtime identifier is required')
    return value


def operation_id(request_key):
    """Use an idempotency key only as a private original-intent identifier, never as a resource name supplied by a caller."""
    return hashlib.sha256(request_key.encode('utf8')).hexdigest()


def release_flags():
    """Local runtime history does not authorize paid generation, independent acceptance or public promotion."""
    return {'historical_only': True, 'fresh_action_authorized': False,
            'independent_review_completed': False, 'public_deployment_verified': False,
            'automatic_public_promotion': False, 'external_model_calls': 0, 'paid_authorization': 0}


class RuntimeReleaseStore:
    """Maintain one new private finite journal, separate from Git, application roots and host Docker configuration."""

    def __init__(self, root, *, initialize=False):
        """Initialize only an empty canonical directory; reopening validates ownership, schema and immutable hash chains."""
        self.root = plain(root, exists=False)
        source = Path(__file__).resolve().parents[4]
        if self.root.is_relative_to(source) or source.is_relative_to(self.root):
            raise RuntimeReleaseError('Runtime journal and served source require independent directories')
        # A directory merely named .git is not necessarily a checkout (some
        # Windows profiles contain an empty placeholder). Refuse worktree
        # pointer files and real/incomplete repository metadata alike.
        markers = [parent / '.git' for parent in (self.root, *self.root.parents)]
        if any(marker.is_file() or marker.is_symlink() or any((marker / name).exists() for name in ('HEAD', 'config', 'objects')) for marker in markers):
            raise RuntimeReleaseError('Runtime journal must be outside Git checkouts')
        self.path = self.root / 'runtime.db'
        if initialize:
            if self.root.exists() and (not self.root.is_dir() or any(self.root.iterdir())):
                raise RuntimeReleaseError('Runtime initialization requires a new empty directory')
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.name != 'nt': self.root.chmod(0o700)
            marker = {'profile': PROFILE, 'version': 1, 'owner': secrets.token_hex(32),
                      'root_sha256': digest(os.path.normcase(str(self.root)))}
            write_json(self.root / 'runtime.json', marker)
            self._initialize_database()
            flush_directory(self.root)
        self._check_root()
        self.marker = read_json(checked_file(self.root / 'runtime.json'))
        if (set(self.marker) != {'profile', 'version', 'owner', 'root_sha256'}
                or self.marker['profile'] != PROFILE or type(self.marker['version']) is not int or self.marker['version'] != 1
                or self.marker['root_sha256'] != digest(os.path.normcase(str(self.root)))):
            raise RuntimeReleaseError('Unknown or relocated runtime journal')
        opaque(self.marker['owner'])
        with self.connect() as db: self._verify(db)

    def _check_root(self):
        """Refuse foreign entries, hardlinks and redirected paths before opening private SQLite or lease files."""
        plain(self.root)
        allowed = {'runtime.json', 'runtime.db', 'runtime.db-journal', 'runtime.db-wal', 'runtime.db-shm', '.operation.lock'}
        if not self.root.is_dir() or any(item.name not in allowed for item in self.root.iterdir()):
            raise RuntimeReleaseError('Mixed runtime journal directory')
        if os.name != 'nt' and self.root.stat().st_mode & 0o077:
            raise RuntimeReleaseError('Runtime journal requires a private directory')
        for item in self.root.iterdir(): checked_file(item)

    def _initialize_database(self):
        """Create append-only records with physical row-id replacement guards, not only ordinary update/delete guards."""
        schema = '''
        PRAGMA journal_mode=DELETE;
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
        CREATE TABLE policies (sequence INTEGER PRIMARY KEY CHECK(sequence>0), record TEXT NOT NULL, sha TEXT UNIQUE NOT NULL);
        CREATE TABLE engines (sequence INTEGER PRIMARY KEY CHECK(sequence=1), record TEXT NOT NULL, sha TEXT UNIQUE NOT NULL);
        CREATE TABLE operations (sequence INTEGER PRIMARY KEY CHECK(sequence>0 AND sequence<=16),
            id TEXT UNIQUE NOT NULL, slot TEXT NOT NULL CHECK(slot IN ('stable','preview')),
            record TEXT NOT NULL, sha TEXT UNIQUE NOT NULL);
        CREATE TABLE events (sequence INTEGER PRIMARY KEY CHECK(sequence>0 AND sequence<=2048),
            operation_id TEXT NOT NULL REFERENCES operations(id), ordinal INTEGER NOT NULL CHECK(ordinal>0 AND ordinal<=128),
            phase TEXT NOT NULL, record TEXT NOT NULL, sha TEXT UNIQUE NOT NULL, UNIQUE(operation_id,ordinal));
        CREATE TABLE generations (sequence INTEGER PRIMARY KEY CHECK(sequence>0 AND sequence<=16),
            slot TEXT NOT NULL CHECK(slot IN ('stable','preview')), generation INTEGER NOT NULL CHECK(generation>0 AND generation<=8),
            operation_id TEXT UNIQUE NOT NULL REFERENCES operations(id), record TEXT NOT NULL, sha TEXT UNIQUE NOT NULL,
            UNIQUE(slot,generation));
        CREATE TABLE archives (sequence INTEGER PRIMARY KEY CHECK(sequence>0 AND sequence<=16),
            id TEXT UNIQUE NOT NULL REFERENCES operations(id), slot TEXT NOT NULL CHECK(slot IN ('stable','preview')),
            generation INTEGER NOT NULL, record TEXT NOT NULL, sha TEXT UNIQUE NOT NULL);
        CREATE TABLE private_configs (sequence INTEGER PRIMARY KEY CHECK(sequence>0 AND sequence<=16),
            operation_id TEXT UNIQUE NOT NULL REFERENCES operations(id), record TEXT NOT NULL, sha TEXT UNIQUE NOT NULL);
        CREATE TABLE slots (slot TEXT PRIMARY KEY CHECK(slot IN ('stable','preview')),
            generation INTEGER NOT NULL CHECK(generation>=0 AND generation<=8), state TEXT NOT NULL CHECK(state IN ('empty','running','stopped'))) WITHOUT ROWID;
        INSERT INTO slots VALUES ('stable',0,'empty'),('preview',0,'empty');
        '''
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript(schema)
            identities = {'policies': 'NEW.sha=sha', 'engines': 'NEW.sha=sha',
                          'operations': 'NEW.id=id OR NEW.sha=sha',
                          'events': 'NEW.sha=sha OR (NEW.operation_id=operation_id AND NEW.ordinal=ordinal)',
                          'generations': 'NEW.sha=sha OR NEW.operation_id=operation_id OR (NEW.slot=slot AND NEW.generation=generation)',
                          'archives': 'NEW.sha=sha OR NEW.id=id',
                          'private_configs': 'NEW.sha=sha OR NEW.operation_id=operation_id'}
            for table, identity in identities.items():
                db.executescript(f'''
                CREATE TRIGGER {table}_immutable_update BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT,'immutable runtime record'); END;
                CREATE TRIGGER {table}_immutable_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT,'immutable runtime record'); END;
                CREATE TRIGGER {table}_immutable_replace BEFORE INSERT ON {table}
                WHEN EXISTS(SELECT 1 FROM {table} WHERE NEW.sequence=sequence OR {identity})
                BEGIN SELECT RAISE(ABORT,'immutable runtime record'); END;
                ''')
            fingerprint = self._schema(db)
            db.execute('INSERT INTO metadata VALUES (?,?)', ('schema', fingerprint))
            disabled = LocalRuntimePolicy().model_dump(mode='json')
            record = {'revision': 1, 'previous_sha256': None, 'policy': disabled, 'request_key': 'initial-disabled-policy'}
            db.execute('INSERT INTO policies(sequence,record,sha) VALUES(1,?,?)', (canonical(record), digest(record)))
            db.commit()
        if os.name != 'nt': self.path.chmod(0o600)

    def _schema(self, db):
        """Fingerprint the installed exact journal schema without persisting business or secret values."""
        return digest([tuple(row) for row in db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')])

    @contextmanager
    def connect(self, *, write=False):
        """Open one bounded real SQLite snapshot, with FULL durability and atomic pointer/event updates on writes."""
        self._check_root()
        with closing(sqlite3.connect(checked_file(self.path), timeout=1.5)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA synchronous=FULL')
            db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_RECORD * 2)
            if not write: db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            try:
                yield db
                if write: db.commit()
            except BaseException:
                db.rollback()
                raise

    @contextmanager
    def exclusive(self):
        """Hold a native process lease over an entire local operation; a crashed operator cannot leave a stale PID lock."""
        self._check_root()
        with ProcessLease(self.root / '.operation.lock'): yield

    def _rows(self, db, table):
        """Read bounded immutable envelopes and reject changed checksums before interpreting their contents."""
        rows = db.execute(f'SELECT * FROM {table} ORDER BY sequence').fetchall()
        if len(rows) > {'policies': 16, 'engines': 1, 'operations': 16, 'events': 2048, 'generations': 16, 'archives': 16, 'private_configs': 16}[table]:
            raise RuntimeReleaseError('Runtime journal capacity exceeded')
        result = []
        for row in rows:
            record = json.loads(row['record'])
            if canonical(record) != row['record'] or digest(record) != row['sha']:
                raise RuntimeReleaseError('Runtime journal record changed')
            result.append((dict(row), record))
        return result

    def _verify(self, db):
        """Validate all finite chains, original intent identities and slot references before granting fresh local work."""
        stored = db.execute("SELECT value FROM metadata WHERE key='schema'").fetchone()
        if not stored or stored[0] != self._schema(db) or db.execute('PRAGMA quick_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchone():
            raise RuntimeReleaseError('Runtime journal integrity differs from its installed profile')
        previous = None
        for ordinal, (row, value) in enumerate(self._rows(db, 'policies'), 1):
            if row['sequence'] != ordinal or value['revision'] != ordinal or value['previous_sha256'] != previous:
                raise RuntimeReleaseError('Runtime policy history changed')
            LocalRuntimePolicy.model_validate(value['policy'])
            previous = row['sha']
        operations = {}
        for row, value in self._rows(db, 'operations'):
            body = INTENTS[value['kind']].model_validate(value['input'])
            if row['id'] != operation_id(body.request_key) or row['slot'] != body.slot or value['id'] != row['id']:
                raise RuntimeReleaseError('Original runtime intent changed')
            operations[row['id']] = value
        chains = {}
        for row, value in self._rows(db, 'events'):
            prior = chains.get(row['operation_id'])
            if (value['operation_id'] != row['operation_id'] or value['phase'] != row['phase'] or value['ordinal'] != row['ordinal']
                    or value['ordinal'] != (prior[0] + 1 if prior else 1) or value['previous_sha256'] != (prior[1] if prior else None)):
                raise RuntimeReleaseError('Runtime event history changed')
            chains[row['operation_id']] = (value['ordinal'], row['sha'])
        for table in ('engines', 'generations', 'archives', 'private_configs'): self._rows(db, table)
        for row in db.execute('SELECT * FROM slots'):
            if row['generation'] and not db.execute('SELECT 1 FROM generations WHERE slot=? AND generation=?', (row['slot'], row['generation'])).fetchone():
                raise RuntimeReleaseError('Current runtime generation is missing')

    def policy(self):
        """Return the immutable current explicit policy and its revision, without contacting Docker."""
        with self.connect() as db:
            self._verify(db)
            row, value = self._rows(db, 'policies')[-1]
            return value['revision'], LocalRuntimePolicy.model_validate(value['policy']), row['sha']

    def set_policy(self, policy, *, expected_revision, request_key):
        """Append a CAS policy revision; existing keys replay only their original policy, and pending operations prohibit changes."""
        policy = LocalRuntimePolicy.model_validate(policy)
        if not isinstance(request_key, str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}', request_key) is None:
            raise RuntimeReleaseError('An original policy request key is required')
        with self.exclusive(), self.connect(write=True) as db:
            self._verify(db)
            policies = self._rows(db, 'policies')
            for _, value in policies:
                if value['request_key'] == request_key:
                    if value['policy'] != policy.model_dump(mode='json'): raise RuntimeReleaseError('Policy key belongs to different input')
                    return {'revision': value['revision'], **release_flags()}
            row, current = policies[-1]
            if type(expected_revision) is not int or expected_revision != current['revision'] or len(policies) >= 16:
                raise RuntimeReleaseError('Runtime policy revision or capacity changed')
            old = LocalRuntimePolicy.model_validate(current['policy'])
            first_configuration = expected_revision == 1 and not db.execute('SELECT 1 FROM operations LIMIT 1').fetchone() and not db.execute('SELECT 1 FROM engines LIMIT 1').fetchone()
            if not first_configuration and (old.stable_port, old.preview_port) != (policy.stable_port, policy.preview_port):
                raise RuntimeReleaseError('Slot origins are immutable after initialization')
            if self._pending(db): raise RuntimeReleaseError('An interrupted operation must be reconciled before changing policy')
            value = {'revision': expected_revision + 1, 'previous_sha256': row['sha'], 'request_key': request_key,
                     'policy': policy.model_dump(mode='json')}
            db.execute('INSERT INTO policies(sequence,record,sha) VALUES(?,?,?)', (value['revision'], canonical(value), digest(value)))
            return {'revision': value['revision'], **release_flags()}

    def bind_engine(self, engine):
        """Pin the first checked local endpoint/engine identity; never silently adopt a newly selected daemon."""
        if set(engine) != {'endpoint_sha256', 'engine_id'} or not isinstance(engine['engine_id'], str) or not 1 <= len(engine['engine_id']) <= 128:
            raise RuntimeReleaseError('A checked exact local engine identity is required')
        opaque(engine['endpoint_sha256'])
        with self.connect(write=True) as db:
            self._verify(db)
            rows = self._rows(db, 'engines')
            if rows:
                if rows[0][1] != engine: raise RuntimeReleaseError('Original local runtime engine changed')
            else: db.execute('INSERT INTO engines VALUES(1,?,?)', (canonical(engine), digest(engine)))

    def _pending(self, db):
        """Find unfinished original reservations from their latest event, including crash-before-first-event cases."""
        return db.execute('''SELECT o.id,o.slot FROM operations o WHERE COALESCE(
            (SELECT phase FROM events e WHERE e.operation_id=o.id ORDER BY ordinal DESC LIMIT 1),'intent')
            NOT IN ('committed','stopped','recovered_previous','failed_closed')''').fetchall()

    def slot(self, slot):
        """Read the atomic current generation and its immutable stack; no resource is started or stopped."""
        if slot not in {'stable', 'preview'}: raise RuntimeReleaseError('Unknown local runtime slot')
        with self.connect() as db:
            self._verify(db)
            row = dict(db.execute('SELECT * FROM slots WHERE slot=?', (slot,)).fetchone())
            generation = db.execute('SELECT record FROM generations WHERE slot=? AND generation=?', (slot, row['generation'])).fetchone()
            row['stack'] = json.loads(generation[0]) if generation else None
            return row

    def reserve(self, kind, body):
        """Persist a unique original intent before any daemon mutation, checking policy, CAS generation and slot reservation once."""
        if kind not in INTENTS: raise RuntimeReleaseError('Unknown local runtime action')
        body = INTENTS[kind].model_validate(body)
        identifier = operation_id(body.request_key)
        with self.connect(write=True) as db:
            self._verify(db)
            saved = db.execute('SELECT record FROM operations WHERE id=?', (identifier,)).fetchone()
            if saved:
                original = json.loads(saved[0])
                if original['kind'] != kind or original['input'] != body.model_dump(mode='json'):
                    raise RuntimeReleaseError('Original runtime key belongs to another input')
                return False, original
            row, policy_record = self._rows(db, 'policies')[-1]
            policy = LocalRuntimePolicy.model_validate(policy_record['policy'])
            if not policy.enabled and kind != 'stop': raise RuntimeReleaseError('Local runtime activation is disabled by explicit operator policy')
            current = db.execute('SELECT * FROM slots WHERE slot=?', (body.slot,)).fetchone()
            if current['generation'] != body.expected_generation:
                raise RuntimeReleaseError('Current local runtime generation changed')
            if body.slot in {item['slot'] for item in self._pending(db)}:
                raise RuntimeReleaseError('This slot has an unfinished original runtime operation')
            count = db.execute('SELECT count(*) FROM operations').fetchone()[0]
            if count >= 16 or kind != 'stop' and current['generation'] >= 8:
                raise RuntimeReleaseError('Local runtime operation or generation capacity reached')
            if kind == 'activate': policy.image(body.image_id)
            value = {'id': identifier, 'kind': kind, 'input': body.model_dump(mode='json'),
                     'policy_revision': policy_record['revision'], 'policy_sha256': row['sha']}
            db.execute('INSERT INTO operations(id,slot,record,sha) VALUES(?,?,?,?)', (identifier, body.slot, canonical(value), digest(value)))
            self._event(db, identifier, 'intent', {'policy_revision': policy_record['revision']})
            return True, value

    def _event(self, db, identifier, phase, details):
        """Append one bounded per-operation hash-chain event in the caller's transaction."""
        if re.fullmatch(r'[a-z][a-z0-9_]{0,63}', phase) is None or not isinstance(details, dict):
            raise RuntimeReleaseError('Invalid runtime event')
        prior = db.execute('SELECT ordinal,sha,phase FROM events WHERE operation_id=? ORDER BY ordinal DESC LIMIT 1', (identifier,)).fetchone()
        if prior and (prior['ordinal'] >= 128 or prior['phase'] in TERMINAL):
            raise RuntimeReleaseError('Original runtime operation is terminal or its event capacity is exhausted')
        ordinal = prior['ordinal'] + 1 if prior else 1
        value = {'operation_id': identifier, 'ordinal': ordinal, 'phase': phase,
                 'previous_sha256': prior['sha'] if prior else None, 'details': details}
        db.execute('INSERT INTO events(operation_id,ordinal,phase,record,sha) VALUES(?,?,?,?,?)',
                   (identifier, ordinal, phase, canonical(value), digest(value)))

    def event(self, identifier, phase, details):
        """Record intent before a single mutation, or its checked outcome; unknown outcomes remain reserved for read-only reconciliation."""
        opaque(identifier)
        if phase in {'committed', 'stopped'}: raise RuntimeReleaseError('Current pointer changes require an atomic finish')
        with self.connect(write=True) as db:
            self._verify(db)
            self._event(db, identifier, phase, details)

    def finish(self, identifier, stack=None):
        """Atomically publish a verified immutable generation, or mark the observed current generation stopped, with its terminal event."""
        opaque(identifier)
        with self.connect(write=True) as db:
            self._verify(db)
            original = json.loads(db.execute('SELECT record FROM operations WHERE id=?', (identifier,)).fetchone()[0])
            body = INTENTS[original['kind']].model_validate(original['input'])
            current = db.execute('SELECT generation FROM slots WHERE slot=?', (body.slot,)).fetchone()[0]
            if current != body.expected_generation: raise RuntimeReleaseError('Current generation changed before runtime commit')
            if original['kind'] == 'stop':
                if stack is not None: raise RuntimeReleaseError('Stop cannot install a generation')
                self._event(db, identifier, 'stopped', {'generation': current})
                db.execute("UPDATE slots SET state='stopped' WHERE slot=? AND generation=?", (body.slot, current))
            else:
                if not isinstance(stack, dict) or stack.get('operation_id') != identifier or stack.get('slot') != body.slot or stack.get('generation') != current + 1:
                    raise RuntimeReleaseError('Verified stack must match the exact original generation')
                db.execute('INSERT INTO generations(slot,generation,operation_id,record,sha) VALUES(?,?,?,?,?)',
                           (body.slot, current + 1, identifier, canonical(stack), digest(stack)))
                self._event(db, identifier, 'committed', {'generation': current + 1, 'stack_sha256': digest(stack)})
                db.execute("UPDATE slots SET generation=?,state='running' WHERE slot=? AND generation=?", (current + 1, body.slot, current))

    def save_archive(self, identifier, slot, generation, record):
        """Bind a verified same-slot stopped-root archive to the original operation; never accept a caller's arbitrary volume/path."""
        opaque(identifier)
        with self.connect(write=True) as db:
            self._verify(db)
            original = db.execute('SELECT slot FROM operations WHERE id=?', (identifier,)).fetchone()
            if not original or original[0] != slot or type(generation) is not int or not 1 <= generation <= 8:
                raise RuntimeReleaseError('Archive requires the exact original slot generation')
            value = {'id': identifier, 'slot': slot, 'generation': generation, **record}
            db.execute('INSERT INTO archives(id,slot,generation,record,sha) VALUES(?,?,?,?,?)',
                       (identifier, slot, generation, canonical(value), digest(value)))

    def archive(self, identifier, slot):
        """Read an immutable same-slot archive record; data bytes are independently verified inside an owned helper before restore."""
        opaque(identifier)
        with self.connect() as db:
            self._verify(db)
            row = db.execute('SELECT record FROM archives WHERE id=? AND slot=?', (identifier, slot)).fetchone()
            if not row: raise RuntimeReleaseError('An existing same-slot archive is required')
            return json.loads(row[0])

    def get(self, identifier):
        """Return original history without policy/engine/resource checks, resumption, activation or cleanup."""
        opaque(identifier)
        with self.connect() as db:
            self._verify(db)
            row = db.execute('SELECT record FROM operations WHERE id=?', (identifier,)).fetchone()
            if not row: raise RuntimeReleaseError('Unknown original runtime operation')
            original = json.loads(row[0])
            events = self._rows(db, 'events')
            own = [value for item, value in events if item['operation_id'] == identifier]
            # Retain free-text original input privately for exact replay, but
            # do not reflect it through ordinary CLI history.
            own = [{**value, 'details': {'request_key_sha256': operation_id(value['details']['input']['request_key']),
                                        'expected_observation_sha256': value['details']['input']['expected_observation_sha256']}}
                   if value['phase'] == 'quiesce_intent' else value for value in own]
            return {'id': identifier, 'kind': original['kind'], 'slot': original['input']['slot'],
                    'phase': own[-1]['phase'] if own else 'intent', 'events': own, **release_flags()}

    def original(self, identifier):
        """Supply immutable original input to the trusted controller, never to an untrusted resource command."""
        opaque(identifier)
        with self.connect() as db:
            self._verify(db)
            row = db.execute('SELECT record FROM operations WHERE id=?', (identifier,)).fetchone()
            if not row: raise RuntimeReleaseError('Unknown original runtime operation')
            return json.loads(row[0])

    def private_config(self, identifier, value=None):
        """Persist/read one private app token before creation; public history never serializes these envelopes."""
        opaque(identifier)
        with self.connect(write=value is not None) as db:
            self._verify(db)
            row = db.execute('SELECT record FROM private_configs WHERE operation_id=?', (identifier,)).fetchone()
            if row:
                saved = json.loads(row[0])
                if value is not None and value != saved: raise RuntimeReleaseError('Original private runtime configuration changed')
                return saved
            if value is None: raise RuntimeReleaseError('Original private runtime configuration is unavailable')
            from local_runtime_bootstrap import configuration
            configuration(canonical(value).encode())
            if 'target_ip' in value: raise RuntimeReleaseError('Private app configuration cannot contain a proxy target')
            db.execute('INSERT INTO private_configs(operation_id,record,sha) VALUES(?,?,?)', (identifier, canonical(value), digest(value)))
            return value

    def policy_at(self, revision):
        """Read an original immutable policy for ownership checks and stopping retained resources after a later disable decision."""
        if type(revision) is not int or not 1 <= revision <= 16: raise RuntimeReleaseError('Unknown original runtime policy revision')
        with self.connect() as db:
            self._verify(db)
            row = db.execute('SELECT record FROM policies WHERE sequence=?', (revision,)).fetchone()
            if not row: raise RuntimeReleaseError('Original runtime policy is missing')
            return LocalRuntimePolicy.model_validate(json.loads(row[0])['policy'])

    def begin_quiescence(self, body):
        """Persist a separate bounded explicit cleanup intent; an old cleanup key returns history and can never stop a later generation."""
        with self.connect(write=True) as db:
            self._verify(db)
            own = [value for row, value in self._rows(db, 'events') if row['operation_id'] == body.operation_id]
            intents = [value for value in own if value['phase'] == 'quiesce_intent']
            for value in intents:
                if value['details']['input']['request_key'] == body.request_key:
                    if value['details']['input'] != body.model_dump(mode='json'): raise RuntimeReleaseError('Original quiescence key belongs to different input')
                    return False
            if not own or own[-1]['phase'] in TERMINAL or len(intents) >= 3:
                raise RuntimeReleaseError('Original operation is terminal or quiescence capacity is exhausted')
            self._event(db, body.operation_id, 'quiesce_intent', {'input': body.model_dump(mode='json')})
            return True

    def finish_closed(self, identifier, observation_sha256):
        """Atomically retain the original generation as stopped and release its reservation only after exact owned workloads are verified stopped."""
        opaque(identifier)
        opaque(observation_sha256)
        with self.connect(write=True) as db:
            self._verify(db)
            row = db.execute('SELECT record FROM operations WHERE id=?', (identifier,)).fetchone()
            if not row: raise RuntimeReleaseError('Unknown original runtime operation')
            body = INTENTS[json.loads(row[0])['kind']].model_validate(json.loads(row[0])['input'])
            current = db.execute('SELECT generation FROM slots WHERE slot=?', (body.slot,)).fetchone()[0]
            if current != body.expected_generation: raise RuntimeReleaseError('Current generation changed before quiescence')
            self._event(db, identifier, 'failed_closed', {'all_known_workloads_stopped': True, 'data_and_archives_retained': True,
                                                        'observation_sha256': observation_sha256})
            if current: db.execute("UPDATE slots SET state='stopped' WHERE slot=? AND generation=?", (body.slot, current))
