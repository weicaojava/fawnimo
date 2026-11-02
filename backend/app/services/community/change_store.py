"""Dedicated append-only inert source proposals, separate from both public intake and personal video databases."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time

from app.services.community.change_bundles import original_change_hash, verify_change_bundle, CHANGE_PROFILE
from app.services.community.evaluations import canonical
from app.services.community.rowid_guards import rowid_guard_definitions
from app.services.community.store import CommunityStoreConflict

MAX_BUNDLES = 128
MAX_WORK_BUNDLES = 16
MAX_STORED_BYTES = 67108864


def change_definitions():
    """Freeze exact source identity, inert profile and natural/physical-key history guards, independent of recursive triggers."""
    tables = {
        'community_change_schema': '''CREATE TABLE community_change_schema(singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            version INTEGER NOT NULL CHECK(version=1),repository_id INTEGER NOT NULL CHECK(repository_id>0),
            repository_name TEXT NOT NULL,profile TEXT NOT NULL CHECK(profile='public_python_change_v1'))''',
        'community_change_bundles': '''CREATE TABLE community_change_bundles(id TEXT PRIMARY KEY NOT NULL CHECK(length(id)=64),
            request_key TEXT NOT NULL UNIQUE,intent_hash TEXT NOT NULL CHECK(length(intent_hash)=64),work_id TEXT NOT NULL,
            payload TEXT NOT NULL CHECK(length(CAST(payload AS BLOB))<=8388608),created_at_us INTEGER NOT NULL CHECK(created_at_us>=0))'''}
    guards = {}
    for table in tables:
        for action in ('UPDATE', 'DELETE'):
            name = table + '_no_' + action.lower()
            guards[name] = f"CREATE TRIGGER {name} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Immutable public change record'); END"
        name = table + '_no_replace'
        condition = 'singleton=NEW.singleton' if table == 'community_change_schema' else 'id=NEW.id OR request_key=NEW.request_key'
        guards[name] = f"CREATE TRIGGER {name} BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE {condition}) BEGIN SELECT RAISE(ABORT,'Public change replacement rejected'); END"
    guards.update(rowid_guard_definitions(tables))
    guards['community_change_bundle_scope'] = '''CREATE TRIGGER community_change_bundle_scope BEFORE INSERT ON community_change_bundles WHEN
        json_extract(NEW.payload,'$.bundle_id') IS NOT NEW.id OR json_extract(NEW.payload,'$.content_hash') IS NOT NEW.id
        OR json_extract(NEW.payload,'$.bundle.input.request_key') IS NOT NEW.request_key
        OR json_extract(NEW.payload,'$.bundle.intent_hash') IS NOT NEW.intent_hash
        OR json_extract(NEW.payload,'$.bundle.input.work_id') IS NOT NEW.work_id
        OR json_extract(NEW.payload,'$.bundle.scope.work_id') IS NOT NEW.work_id
        OR json_extract(NEW.payload,'$.bundle.scope.repository_id') IS NOT (SELECT repository_id FROM community_change_schema WHERE singleton=1)
        OR json_extract(NEW.payload,'$.bundle.scope.repository_name') IS NOT (SELECT repository_name FROM community_change_schema WHERE singleton=1)
        OR json_extract(NEW.payload,'$.bundle.profile') IS NOT 'public_python_change_v1'
        OR json_extract(NEW.payload,'$.bundle.execution_authorized') IS NOT 0
        OR json_extract(NEW.payload,'$.bundle.development_complete') IS NOT 0
        OR json_extract(NEW.payload,'$.bundle.independent_review_completed') IS NOT 0
        OR json_extract(NEW.payload,'$.bundle.ci_qualified') IS NOT 0
        OR json_extract(NEW.payload,'$.bundle.public_deployment_verified') IS NOT 0
        OR json_extract(NEW.payload,'$.bundle.external_model_calls') IS NOT 0
        OR json_extract(NEW.payload,'$.bundle.paid_authorization') IS NOT 0
        BEGIN SELECT RAISE(ABORT,'Invalid inert public change scope'); END'''
    return tables, guards


class CommunityChangeStore:
    """Save bounded inert proposals in a new dedicated directory with no model configuration, user media or executor authority."""

    def __init__(self, root, config, *, initialize=False, clock_us=None):
        """Reject mixed/redirected roots before writing, and validate the complete immutable schema and source binding on reopen."""
        self.root = Path(root).absolute(); self.config = config
        self.clock_us = clock_us or (lambda: time.time_ns() // 1000)
        if self.root.resolve() != self.root or config.repository_id is None:
            raise CommunityStoreConflict('Invalid dedicated public change root')
        self.database = self.root / 'changes.sqlite3'
        allowed = {'changes.sqlite3', 'changes.sqlite3-wal', 'changes.sqlite3-shm', 'changes.sqlite3-journal'}
        if self.root.exists():
            if any(item.name not in allowed or item.resolve() != item or not item.is_file() for item in self.root.iterdir()):
                raise CommunityStoreConflict('Public changes require their own dedicated directory')
        elif initialize:
            self.root.mkdir(parents=True)
        if self.database.resolve() != self.database or not initialize and not self.database.is_file():
            raise CommunityStoreConflict('Public change store is redirected or not initialized')
        with closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                tables, guards = change_definitions()
                present = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not present:
                    if not initialize:
                        raise CommunityStoreConflict('Public changes have not been initialized')
                    for definition in tables.values(): db.execute(definition)
                    db.execute('INSERT INTO community_change_schema VALUES(1,1,?,?,?)', (config.repository_id, config.repository_name.casefold(), CHANGE_PROFILE))
                    for definition in guards.values(): db.execute(definition)
                self.validate(db)
                db.commit()
            except BaseException:
                db.rollback(); raise

    def connect(self):
        """Use short owned SQLite connections; no studio session factory, environment database URL or model settings is imported."""
        db = sqlite3.connect(self.database, timeout=1.5)
        db.execute('PRAGMA busy_timeout=1500'); db.execute('PRAGMA foreign_keys=ON')
        return db

    def validate(self, db):
        """Accept only the complete known store, and verify all retained original-source relationships rather than just JSON hashes."""
        tables, guards = change_definitions()
        from app.services.community.code_check_schema import CHECK_TABLES, validate_check_schema
        present = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'") if not row[0].startswith('sqlite_')}
        if present not in (set(tables), set(tables) | CHECK_TABLES):
            raise CommunityStoreConflict('Unknown public change schema')
        for kind, definitions in (('table', tables), ('trigger', guards)):
            for name, expected in definitions.items():
                row = db.execute('SELECT sql FROM sqlite_master WHERE type=? AND name=?', (kind, name)).fetchone()
                if row is None or ' '.join(row[0].strip().rstrip(';').split()) != ' '.join(expected.strip().rstrip(';').split()):
                    raise CommunityStoreConflict('Public change schema or guard changed')
        if db.execute('SELECT * FROM community_change_schema').fetchall() != [(1, 1, self.config.repository_id, self.config.repository_name.casefold(), CHANGE_PROFILE)]:
            raise CommunityStoreConflict('Public change store belongs to another source or profile')
        for row in db.execute('SELECT id,request_key,intent_hash,work_id,payload FROM community_change_bundles'):
            self._decode(row)
        if present & CHECK_TABLES:
            validate_check_schema(db)

    def _decode(self, row):
        """Bind the immutable SQLite keys to the verified bundle, its original intent and the root's public source."""
        artifact = json.loads(row[4]); payload = verify_change_bundle(artifact)
        if (artifact['bundle_id'] != row[0] or payload['input']['request_key'] != row[1] or payload['intent_hash'] != row[2]
                or payload['input']['work_id'] != row[3] or payload['scope']['repository_id'] != self.config.repository_id
                or payload['scope']['repository_name'] != self.config.repository_name.casefold()):
            raise CommunityStoreConflict('Original public change receipt changed')
        return artifact

    def replay(self, body):
        """Recover original inert proposals before any current source/plan/network gate, never creating another branch or execution."""
        intent = original_change_hash(body)
        with closing(self.connect()) as db:
            row = db.execute('SELECT id,request_key,intent_hash,work_id,payload FROM community_change_bundles WHERE request_key=?', (body.request_key,)).fetchone()
            if row is None: return None
            if row[2] != intent: raise CommunityStoreConflict('Original change key belongs to another intent')
            return {'artifact': self._decode(row), 'replayed': True, 'source_recheck_required_for_new_execution': True}

    def record(self, body, artifact):
        """Append a verified inert bundle once, atomically enforcing per-work/global history and64MiB total stored-byte limits."""
        intent = original_change_hash(body); payload = verify_change_bundle(artifact)
        if payload['input'] != body.model_dump() or payload['intent_hash'] != intent:
            raise CommunityStoreConflict('Change artifact does not match the exact original input')
        encoded = canonical(artifact)
        with closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                row = db.execute('SELECT id,request_key,intent_hash,work_id,payload FROM community_change_bundles WHERE request_key=?', (body.request_key,)).fetchone()
                if row:
                    if row[2] != intent: raise CommunityStoreConflict('Original change key belongs to another intent')
                    result = {'artifact': self._decode(row), 'replayed': True, 'source_recheck_required_for_new_execution': True}
                    db.commit(); return result
                count, size = db.execute('SELECT count(*),coalesce(sum(length(CAST(payload AS BLOB))),0) FROM community_change_bundles').fetchone()
                per_work = db.execute('SELECT count(*) FROM community_change_bundles WHERE work_id=?', (body.work_id,)).fetchone()[0]
                if count >= MAX_BUNDLES or per_work >= MAX_WORK_BUNDLES or size + len(encoded.encode('utf8')) > MAX_STORED_BYTES:
                    raise CommunityStoreConflict('Public change history capacity reached')
                now = self.clock_us()
                if type(now) is not int or now < 0: raise CommunityStoreConflict('Invalid public change clock')
                db.execute('INSERT INTO community_change_bundles VALUES(?,?,?,?,?,?)', (artifact['bundle_id'], body.request_key, intent, body.work_id, encoded, now))
                db.commit()
                return {'artifact': artifact, 'replayed': False, 'source_recheck_required_for_new_execution': True}
            except BaseException:
                db.rollback(); raise

    def get(self, identifier):
        """Read exact saved source bytes without touching intake, a network origin or an executor."""
        with closing(self.connect()) as db:
            row = db.execute('SELECT id,request_key,intent_hash,work_id,payload FROM community_change_bundles WHERE id=?', (identifier,)).fetchone()
            if row is None: raise CommunityStoreConflict('Public change not found')
            return self._decode(row)

    def history(self, work_id):
        """Return bounded immutable artifact identities for one original preparation, without interpreting candidate source."""
        with closing(self.connect()) as db:
            return [{'bundle_id': row[0], 'request_key': row[1], 'intent_hash': row[2], 'created_at_us': row[3]}
                for row in db.execute('SELECT id,request_key,intent_hash,created_at_us FROM community_change_bundles WHERE work_id=? ORDER BY created_at_us,id', (work_id,))]
