"""Atomically retain only public repository requirements, immutable versions and delivery acknowledgements."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid

from app.core.contracts.community_requests import CommunityDeliveryReceipt, CommunityRequestSummary
from app.services.community.events import PublicIssueVersion


class CommunityStoreConflict(ValueError):
    """Expose a fixed conflict type without returning SQL, paths or public issue contents in error messages."""


class CommunityRequestStore:
    """Bind a dedicated new SQLite store to one public repository; never import personal studio data or settings."""
    def __init__(self, root: Path, config, *, initialize=True):
        """Refuse redirected or mixed-use roots and validate repository identity before any additive schema setup."""
        self.root = Path(root).absolute()
        self.config = config
        if self.root.resolve() != self.root or config.repository_id is None:
            raise CommunityStoreConflict('Invalid community store setup')
        self.database = self.root / 'requirements.sqlite3'
        if self.database.resolve() != self.database:
            raise CommunityStoreConflict('Invalid community store setup')
        if not initialize and not self.database.is_file():
            raise CommunityStoreConflict('Community store has not been initialized')
        allowed = {'requirements.sqlite3', 'requirements.sqlite3-wal', 'requirements.sqlite3-shm', 'requirements.sqlite3-journal'}
        if self.root.exists():
            for item in self.root.iterdir():
                if item.name not in allowed or item.resolve() != item or not item.is_file():
                    raise CommunityStoreConflict('Community store requires a dedicated directory')
        elif initialize:
            self.root.mkdir(parents=True)
        with closing(self.connect()) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'") if not row[0].startswith('sqlite_')}
            expected = {'community_repository', 'community_requests', 'community_versions', 'community_deliveries', 'community_source_clocks'}
            if tables and tables != expected:
                raise CommunityStoreConflict('Community store schema is not recognized')
            if not tables:
                if not initialize:
                    raise CommunityStoreConflict('Community store has not been initialized')
                self.create_schema(db)
            identity = db.execute('SELECT singleton,repository_id,repository_name,proposal_label,schema_version FROM community_repository').fetchall()
            if identity != [(1, config.repository_id, config.repository_name.casefold(), config.proposal_label, 1)]:
                raise CommunityStoreConflict('Community store belongs to another source configuration')
            self.check_guards(db)
            if db.execute('PRAGMA foreign_key_check').fetchone():
                raise CommunityStoreConflict('Community source references are inconsistent')

    def connect(self):
        """Keep connections short-lived, bounded on contention and always enforcing source-version foreign keys."""
        db = sqlite3.connect(self.database, timeout=1.5)
        db.execute('PRAGMA foreign_keys=ON'); db.execute('PRAGMA busy_timeout=1500')
        return db

    def guard_definitions(self):
        """Seal source rows against update/delete/replace and keep the current pointer on an actual newest saved version."""
        guards = {}
        for table in ('community_repository', 'community_versions', 'community_deliveries'):
            for action in ('UPDATE', 'DELETE'):
                name = table + '_no_' + action.lower()
                guards[name] = f"CREATE TRIGGER {name} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Immutable community source'); END"
        for table, condition in [('community_repository', 'singleton=NEW.singleton'),
                ('community_versions', 'id=NEW.id OR (request_id=NEW.request_id AND fingerprint=NEW.fingerprint)'),
                ('community_deliveries', 'delivery_id=NEW.delivery_id'),
                ('community_requests', 'id=NEW.id OR issue_number=NEW.issue_number OR github_issue_id=NEW.github_issue_id'),
                ('community_source_clocks', 'issue_number=NEW.issue_number OR github_issue_id=NEW.github_issue_id')]:
            name = table + '_no_replace'
            guards[name] = f"CREATE TRIGGER {name} BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE {condition}) BEGIN SELECT RAISE(ABORT,'Community source replacement rejected'); END"
        guards['community_request_identity'] = "CREATE TRIGGER community_request_identity BEFORE UPDATE ON community_requests WHEN NEW.id IS NOT OLD.id OR NEW.issue_number IS NOT OLD.issue_number OR NEW.github_issue_id IS NOT OLD.github_issue_id OR NEW.author_id IS NOT OLD.author_id BEGIN SELECT RAISE(ABORT,'Immutable community request identity'); END"
        guards['community_request_no_delete'] = "CREATE TRIGGER community_request_no_delete BEFORE DELETE ON community_requests BEGIN SELECT RAISE(ABORT,'Community request history is retained'); END"
        guards['community_request_initial'] = "CREATE TRIGGER community_request_initial BEFORE INSERT ON community_requests WHEN NEW.current_version IS NOT NULL OR NEW.current_updated_us!=-1 OR NEW.requires_refresh!=0 BEGIN SELECT RAISE(ABORT,'Community request must start without a selected version'); END"
        guards['community_request_current_version'] = """CREATE TRIGGER community_request_current_version BEFORE UPDATE ON community_requests WHEN
            NOT EXISTS(SELECT 1 FROM community_versions v WHERE v.id=NEW.current_version AND v.request_id=NEW.id AND v.updated_us=NEW.current_updated_us)
            OR NEW.current_updated_us<OLD.current_updated_us
            OR EXISTS(SELECT 1 FROM community_versions v WHERE v.request_id=NEW.id AND v.updated_us>NEW.current_updated_us)
            OR (NEW.requires_refresh=0 AND (SELECT count(*) FROM community_versions v WHERE v.request_id=NEW.id AND v.updated_us=NEW.current_updated_us)>1)
            OR NOT EXISTS(SELECT 1 FROM community_source_clocks c JOIN community_versions v ON v.id=NEW.current_version
                WHERE c.issue_number=NEW.issue_number AND c.github_issue_id=NEW.github_issue_id AND c.author_id=NEW.author_id
                AND c.updated_us=NEW.current_updated_us AND c.conflict=NEW.requires_refresh AND (c.conflict=1 OR c.fingerprint=v.fingerprint))
            BEGIN SELECT RAISE(ABORT,'Invalid current community source'); END"""
        guards['community_clock_no_delete'] = "CREATE TRIGGER community_clock_no_delete BEFORE DELETE ON community_source_clocks BEGIN SELECT RAISE(ABORT,'Source clock history is retained'); END"
        guards['community_clock_monotonic'] = """CREATE TRIGGER community_clock_monotonic BEFORE UPDATE ON community_source_clocks WHEN
            NEW.issue_number IS NOT OLD.issue_number OR NEW.github_issue_id IS NOT OLD.github_issue_id OR NEW.author_id IS NOT OLD.author_id
            OR NEW.updated_us<OLD.updated_us OR (NEW.conflict=1 AND NEW.source_eligible!=0)
            OR (NEW.updated_us=OLD.updated_us AND (NEW.fingerprint IS NOT OLD.fingerprint OR NEW.conflict<OLD.conflict
                OR (NEW.conflict=OLD.conflict AND NEW.source_eligible!=OLD.source_eligible)))
            BEGIN SELECT RAISE(ABORT,'Invalid source clock transition'); END"""
        guards['community_delivery_source_pair'] = """CREATE TRIGGER community_delivery_source_pair BEFORE INSERT ON community_deliveries WHEN
            NEW.outcome NOT IN ('recorded','duplicate_version','stale_version','source_conflict','ignored')
            OR ((NEW.request_id IS NULL)!=(NEW.version_id IS NULL))
            OR (NEW.request_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM community_versions v WHERE v.id=NEW.version_id AND v.request_id=NEW.request_id))
            OR (NEW.outcome IN ('recorded','duplicate_version') AND NEW.request_id IS NULL)
            OR (NEW.outcome='ignored' AND NEW.request_id IS NOT NULL)
            OR (NEW.outcome='recorded' AND NOT EXISTS(SELECT 1 FROM community_requests r WHERE r.id=NEW.request_id AND r.current_version=NEW.version_id))
            BEGIN SELECT RAISE(ABORT,'Invalid delivery source pair'); END"""
        return guards

    def create_schema(self, db):
        """Initialize only an empty dedicated database, atomically binding the target and immutable source guards."""
        db.execute('BEGIN IMMEDIATE')
        try:
            db.execute('CREATE TABLE community_repository(singleton INTEGER PRIMARY KEY CHECK(singleton=1),repository_id INTEGER NOT NULL,repository_name TEXT NOT NULL,proposal_label TEXT NOT NULL,schema_version INTEGER NOT NULL CHECK(schema_version=1))')
            db.execute('INSERT INTO community_repository VALUES(1,?,?,?,1)', (self.config.repository_id, self.config.repository_name.casefold(), self.config.proposal_label))
            db.execute('CREATE TABLE community_source_clocks(issue_number INTEGER PRIMARY KEY,github_issue_id INTEGER NOT NULL UNIQUE,author_id INTEGER NOT NULL,updated_us INTEGER NOT NULL CHECK(updated_us>=0),fingerprint TEXT NOT NULL CHECK(length(fingerprint)=64),source_eligible INTEGER NOT NULL CHECK(source_eligible IN(0,1)),conflict INTEGER NOT NULL DEFAULT 0 CHECK(conflict IN(0,1)),UNIQUE(issue_number,github_issue_id,author_id))')
            db.execute('CREATE TABLE community_requests(id TEXT PRIMARY KEY NOT NULL,issue_number INTEGER NOT NULL UNIQUE,github_issue_id INTEGER NOT NULL UNIQUE,author_id INTEGER NOT NULL,current_version TEXT REFERENCES community_versions(id),current_updated_us INTEGER NOT NULL DEFAULT -1 CHECK(current_updated_us>=-1),requires_refresh INTEGER NOT NULL DEFAULT 0 CHECK(requires_refresh IN(0,1)),FOREIGN KEY(issue_number,github_issue_id,author_id) REFERENCES community_source_clocks(issue_number,github_issue_id,author_id))')
            db.execute('CREATE TABLE community_versions(id TEXT PRIMARY KEY NOT NULL,request_id TEXT NOT NULL REFERENCES community_requests(id),fingerprint TEXT NOT NULL CHECK(length(fingerprint)=64),updated_us INTEGER NOT NULL CHECK(updated_us>=0),title TEXT NOT NULL,body TEXT NOT NULL,labels TEXT NOT NULL,source_state TEXT NOT NULL CHECK(source_state IN(\'open\',\'closed\')),source_eligible INTEGER NOT NULL CHECK(source_eligible IN(0,1)),UNIQUE(request_id,fingerprint))')
            db.execute('CREATE TABLE community_deliveries(delivery_id TEXT PRIMARY KEY NOT NULL,body_sha256 TEXT NOT NULL CHECK(length(body_sha256)=64),event TEXT NOT NULL,action TEXT NOT NULL,outcome TEXT NOT NULL,request_id TEXT REFERENCES community_requests(id),version_id TEXT REFERENCES community_versions(id),received_at REAL NOT NULL)')
            for sql in self.guard_definitions().values():
                db.execute(sql)
            db.commit()
        except BaseException:
            db.rollback(); raise

    def check_guards(self, db):
        """Refuse a reused database with missing or changed guard definitions instead of trusting trigger names alone."""
        for name, expected in self.guard_definitions().items():
            saved = db.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (name,)).fetchone()
            if saved is None or ' '.join(saved[0].strip().rstrip(';').split()) != ' '.join(expected.strip().rstrip(';').split()):
                raise CommunityStoreConflict('Community source guard is missing or changed')

    def observe_source_clock(self, db, version):
        """Remember only source identity/clock/fingerprint for unrelated or withdrawn issues, never their body text.

        This minimal tombstone exists before any proposal is retained, so a late
        old opened event cannot resurrect a newer observed withdrawal. Equal
        clocks with different facts remain conflicted until a newer clock.
        """
        row = db.execute('SELECT github_issue_id,author_id,updated_us,fingerprint,source_eligible,conflict FROM community_source_clocks WHERE issue_number=?', (version.issue_number,)).fetchone()
        if row is None:
            db.execute('INSERT INTO community_source_clocks VALUES(?,?,?,?,?,?,0)', (version.issue_number, version.github_issue_id,
                version.author_id, version.updated_us, version.fingerprint, int(version.eligible)))
        else:
            if row[:2] != (version.github_issue_id, version.author_id):
                raise CommunityStoreConflict('Issue number belongs to another source identity')
            if version.updated_us > row[2]:
                db.execute('UPDATE community_source_clocks SET updated_us=?,fingerprint=?,source_eligible=?,conflict=0 WHERE issue_number=?',
                    (version.updated_us, version.fingerprint, int(version.eligible), version.issue_number))
            elif version.updated_us == row[2] and version.fingerprint != row[3]:
                db.execute('UPDATE community_source_clocks SET source_eligible=0,conflict=1 WHERE issue_number=?', (version.issue_number,))
        return db.execute('SELECT updated_us,fingerprint,source_eligible,conflict FROM community_source_clocks WHERE issue_number=?', (version.issue_number,)).fetchone()

    def ingest(self, delivery_id: str, content: bytes, event: str, action: str, version: PublicIssueVersion | None):
        """Deduplicate exact deliveries and source versions, preserving stale events and pausing same-clock conflicts."""
        digest = hashlib.sha256(content).hexdigest()
        with closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                previous = db.execute('SELECT body_sha256,event,action,outcome,request_id,version_id FROM community_deliveries WHERE delivery_id=?', (delivery_id,)).fetchone()
                if previous:
                    if previous[:3] != (digest, event, action):
                        raise CommunityStoreConflict('Delivery identity already belongs to another event')
                    db.commit()
                    return CommunityDeliveryReceipt(delivery_id=delivery_id, replayed=True, outcome=previous[3], request_id=previous[4], version_id=previous[5])
                request_id = version_id = None; outcome = 'ignored'
                if version is not None:
                    clock = self.observe_source_clock(db, version)
                    row = db.execute('SELECT id,github_issue_id,author_id,current_version,current_updated_us,requires_refresh FROM community_requests WHERE issue_number=?', (version.issue_number,)).fetchone()
                    # Do not persist unrelated issue bodies: only an explicit current proposal or its already-known history enters this store.
                    if row is None and version.updated_us < clock[0]:
                        outcome = 'stale_version'
                    elif row is None and clock[3]:
                        outcome = 'source_conflict'
                    elif row is not None or version.eligible:
                        if row is None:
                            request_id = str(uuid.uuid4())
                            db.execute('INSERT INTO community_requests(id,issue_number,github_issue_id,author_id) VALUES(?,?,?,?)',
                                (request_id, version.issue_number, version.github_issue_id, version.author_id))
                            row = (request_id, version.github_issue_id, version.author_id, None, -1, 0)
                        if row[1:3] != (version.github_issue_id, version.author_id):
                            raise CommunityStoreConflict('Issue number belongs to another source identity')
                        request_id = row[0]
                        existing = db.execute('SELECT id FROM community_versions WHERE request_id=? AND fingerprint=?', (request_id, version.fingerprint)).fetchone()
                        if existing:
                            version_id = existing[0]; outcome = 'duplicate_version'
                        else:
                            version_id = str(uuid.uuid4())
                            db.execute('INSERT INTO community_versions VALUES(?,?,?,?,?,?,?,?,?)', (version_id, request_id, version.fingerprint,
                                version.updated_us, version.title, version.body, json.dumps(version.labels, ensure_ascii=False), version.state, int(version.eligible)))
                            if version.updated_us > row[4]:
                                db.execute('UPDATE community_requests SET current_version=?,current_updated_us=?,requires_refresh=? WHERE id=?',
                                    (version_id, version.updated_us, clock[3], request_id)); outcome = 'source_conflict' if clock[3] else 'recorded'
                            elif version.updated_us == row[4]:
                                db.execute('UPDATE community_requests SET requires_refresh=1 WHERE id=?', (request_id,)); outcome = 'source_conflict'
                            else:
                                outcome = 'stale_version'
                db.execute('INSERT INTO community_deliveries VALUES(?,?,?,?,?,?,?,?)',
                    (delivery_id, digest, event, action, outcome, request_id, version_id, time.time()))
                db.commit()
                return CommunityDeliveryReceipt(delivery_id=delivery_id, replayed=False, outcome=outcome, request_id=request_id, version_id=version_id)
            except BaseException:
                db.rollback(); raise

    def summaries(self, *, limit=50, offset=0):
        """Read bounded public requirement metadata only; source history never claims software acceptance or deployment."""
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 100000:
            raise CommunityStoreConflict('Invalid requirement page')
        with closing(self.connect()) as db:
            rows = db.execute('SELECT r.id,r.issue_number,r.current_version,v.title,v.source_state,v.source_eligible,r.requires_refresh,(SELECT count(*) FROM community_versions h WHERE h.request_id=r.id),c.updated_us,c.fingerprint,c.source_eligible,c.conflict,v.updated_us,v.fingerprint,r.current_updated_us FROM community_requests r JOIN community_source_clocks c ON c.issue_number=r.issue_number AND c.github_issue_id=r.github_issue_id AND c.author_id=r.author_id LEFT JOIN community_versions v ON v.id=r.current_version AND v.request_id=r.id ORDER BY r.issue_number DESC LIMIT ? OFFSET ?', (limit, offset)).fetchall()
        result = []
        for row in rows:
            current = row[8] == row[12] == row[14] and row[9] == row[13]
            refresh = bool(row[6] or row[11] or not current)
            result.append(CommunityRequestSummary(id=row[0], issue_number=row[1], source_url=f'https://github.com/{self.config.repository_name}/issues/{row[1]}',
                current_version=row[2], title=row[3], source_state=row[4], source_eligible=bool(row[5] and row[10] and not refresh), requires_refresh=refresh, received_versions=row[7]))
        return result
