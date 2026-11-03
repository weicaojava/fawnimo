"""Separate immutable publication intent/events; original recovery never silently replays an unknown remote write."""
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid
from app.core.contracts.community_publication import CommunityPublicationPolicy,publication_flags
from app.services.community.evaluations import canonical,digest
from app.services.community.rowid_guards import rowid_guard_definitions
from app.services.community.store import CommunityStoreConflict

PHASES={'reserved','objects_verified','ref_intent','ref_observed','pr_intent','published','reconciled','attention'}


def definitions():
    """Declare immutable exact policy, one intent per bundle and ordered events with logical/physical replacement guards."""
    tables={
        'community_publication_policy':'''CREATE TABLE community_publication_policy(singleton INTEGER PRIMARY KEY CHECK(singleton=1),payload TEXT NOT NULL CHECK(length(CAST(payload AS BLOB))<=8192))''',
        'community_publication_intents':'''CREATE TABLE community_publication_intents(id TEXT PRIMARY KEY NOT NULL,request_key TEXT NOT NULL UNIQUE,bundle_id TEXT NOT NULL UNIQUE,intent_hash TEXT NOT NULL,payload TEXT NOT NULL CHECK(length(CAST(payload AS BLOB))<=32768),created_at_us INTEGER NOT NULL CHECK(created_at_us>=0))''',
        'community_publication_events':'''CREATE TABLE community_publication_events(seq INTEGER PRIMARY KEY AUTOINCREMENT,publication_id TEXT NOT NULL REFERENCES community_publication_intents(id),phase TEXT NOT NULL CHECK(phase IN ('reserved','objects_verified','ref_intent','ref_observed','pr_intent','published','reconciled','attention')),payload TEXT NOT NULL CHECK(length(CAST(payload AS BLOB))<=32768),created_at_us INTEGER NOT NULL CHECK(created_at_us>=0))'''}
    guards={}
    for table in tables:
        for action in ('UPDATE','DELETE'):
            name=table+'_no_'+action.lower(); guards[name]=f"CREATE TRIGGER {name} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Immutable publication history'); END"
        where={'community_publication_policy':'singleton=NEW.singleton','community_publication_intents':'id=NEW.id OR request_key=NEW.request_key OR bundle_id=NEW.bundle_id','community_publication_events':'seq=NEW.seq'}[table]
        name=table+'_no_replace'; guards[name]=f"CREATE TRIGGER {name} BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE {where}) BEGIN SELECT RAISE(ABORT,'Publication replacement rejected'); END"
    guards.update(rowid_guard_definitions(tables))
    guards['community_publication_limits']='''CREATE TRIGGER community_publication_limits BEFORE INSERT ON community_publication_intents WHEN (SELECT count(*) FROM community_publication_intents)>=64 OR (SELECT coalesce(sum(length(CAST(payload AS BLOB))),0) FROM community_publication_intents)+(SELECT coalesce(sum(length(CAST(payload AS BLOB))),0) FROM community_publication_events)+length(CAST(NEW.payload AS BLOB))>16777216 BEGIN SELECT RAISE(ABORT,'Publication capacity reached'); END'''
    guards['community_publication_event_limits']='''CREATE TRIGGER community_publication_event_limits BEFORE INSERT ON community_publication_events WHEN (SELECT count(*) FROM community_publication_events)>=512 OR (SELECT coalesce(sum(length(CAST(payload AS BLOB))),0) FROM community_publication_intents)+(SELECT coalesce(sum(length(CAST(payload AS BLOB))),0) FROM community_publication_events)+length(CAST(NEW.payload AS BLOB))>16777216 BEGIN SELECT RAISE(ABORT,'Publication event capacity reached'); END'''
    guards['community_publication_intent_scope']='''CREATE TRIGGER community_publication_intent_scope BEFORE INSERT ON community_publication_intents WHEN
        json_valid(NEW.payload) IS NOT 1 OR json_extract(NEW.payload,'$.input.request_key') IS NOT NEW.request_key
        OR json_extract(NEW.payload,'$.input.bundle_id') IS NOT NEW.bundle_id OR json_extract(NEW.payload,'$.candidate.bundle_id') IS NOT NEW.bundle_id
        OR json_extract(NEW.payload,'$.independent_review_completed') IS NOT 0 OR json_extract(NEW.payload,'$.ci_qualified') IS NOT 0
        OR json_extract(NEW.payload,'$.merge_authorized') IS NOT 0 OR json_extract(NEW.payload,'$.deployment_authorized') IS NOT 0
        OR json_extract(NEW.payload,'$.external_model_calls') IS NOT 0 OR json_extract(NEW.payload,'$.paid_authorization') IS NOT 0
        BEGIN SELECT RAISE(ABORT,'Invalid publication intent scope'); END'''
    guards['community_publication_event_scope']='''CREATE TRIGGER community_publication_event_scope BEFORE INSERT ON community_publication_events WHEN
        json_valid(NEW.payload) IS NOT 1 OR json_extract(NEW.payload,'$.independent_review_completed') IS NOT 0 OR json_extract(NEW.payload,'$.ci_qualified') IS NOT 0
        OR json_extract(NEW.payload,'$.merge_authorized') IS NOT 0 OR json_extract(NEW.payload,'$.deployment_authorized') IS NOT 0
        OR json_extract(NEW.payload,'$.external_model_calls') IS NOT 0 OR json_extract(NEW.payload,'$.paid_authorization') IS NOT 0
        BEGIN SELECT RAISE(ABORT,'Invalid publication event scope'); END'''
    return tables,guards


class CommunityPublicationStore:
    """Use an explicitly created dedicated directory, distinct from intake, changes, review gates and all studio data."""
    def __init__(self,root,policy=None,*,initialize=False,isolated_from=(),clock_us=None):
        """Reopening validates the exact schema/policy and rejects symlinks, mixed files, silent migrations and nested state roots."""
        self.root=Path(root).absolute(); self.database=self.root/'publication.sqlite3'; self.clock_us=clock_us or (lambda:time.time_ns()//1000)
        others=[Path(item).absolute().resolve() for item in isolated_from]
        if self.root.resolve()!=self.root or any(self.root.is_relative_to(item) or item.is_relative_to(self.root) for item in others):
            raise CommunityStoreConflict('Publication root must be a separate canonical directory')
        if self.root.exists():
            allowed={self.database.name+suffix for suffix in ('','-wal','-shm','-journal')}
            if any(item.name not in allowed or item.resolve()!=item or not item.is_file() for item in self.root.iterdir()):
                raise CommunityStoreConflict('Publication root contains unrelated files')
        elif initialize: self.root.mkdir(parents=True)
        if self.database.resolve()!=self.database or not initialize and not self.database.is_file():
            raise CommunityStoreConflict('Publication root has not been initialized')
        with closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                tables,guards=definitions(); present=db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                if not present:
                    if not initialize or policy is None: raise CommunityStoreConflict('Explicit immutable publication policy required')
                    for sql in tables.values(): db.execute(sql)
                    db.execute('INSERT INTO community_publication_policy VALUES(1,?)',(canonical(policy.model_dump(mode='json')),))
                    for sql in guards.values(): db.execute(sql)
                actual={name for name, in db.execute("SELECT name FROM sqlite_master WHERE type='table'") if not name.startswith('sqlite_')}
                if actual!=set(tables): raise CommunityStoreConflict('Unknown publication tables')
                for kind,items in (('table',tables),('trigger',guards)):
                    actual={name:sql for name,sql in db.execute('SELECT name,sql FROM sqlite_master WHERE type=?',(kind,)) if not name.startswith('sqlite_')}
                    if {key:' '.join(value.split()) for key,value in actual.items()}!={key:' '.join(value.split()) for key,value in items.items()}:
                        raise CommunityStoreConflict('Publication schema or guards changed')
                rows=db.execute('SELECT singleton,payload FROM community_publication_policy').fetchall()
                if len(rows)!=1 or rows[0][0]!=1: raise CommunityStoreConflict('Invalid publication policy')
                self.policy=CommunityPublicationPolicy.model_validate_json(rows[0][1])
                if policy is not None and policy!=self.policy: raise CommunityStoreConflict('Publication policy is immutable; create a separate root')
                db.commit()
            except BaseException: db.rollback(); raise

    def connect(self):
        """Keep one short local connection per transaction; no environment/studio database or model settings are imported."""
        db=sqlite3.connect(self.database,timeout=1.5); db.row_factory=sqlite3.Row; db.execute('PRAGMA foreign_keys=ON'); db.execute('PRAGMA busy_timeout=1500'); return db

    def _time(self,db):
        """New work rejects backward/invalid clocks; historical recovery has no clock dependency."""
        now=self.clock_us(); last=db.execute('SELECT max(created_at_us) FROM community_publication_events').fetchone()[0]
        if type(now) is not int or now<0 or last is not None and now<last: raise CommunityStoreConflict('Publication clock is not current')
        return now

    def replay(self,body):
        """Return the exact original immutable record before current source, credentials, capacity or network work."""
        with closing(self.connect()) as db:
            row=db.execute('SELECT id,intent_hash FROM community_publication_intents WHERE request_key=?',(body.request_key,)).fetchone()
        if row is None: return None
        if row['intent_hash']!=digest(body.model_dump()): raise CommunityStoreConflict('Original publication key belongs to another input')
        return self.get(row['id'])

    def reserve(self,body,candidate):
        """Reserve one exact bundle/commit only once before any remote mutation; losers and later invocations receive no write permission."""
        original=self.replay(body)
        if original is not None: return original,False
        if not self.policy.enabled: raise CommunityStoreConflict('Remote publication is disabled')
        if (candidate.bundle_id!=body.bundle_id or candidate.repository_id!=self.policy.repository_id or candidate.repository_name.casefold()!=self.policy.repository_name
                or candidate.branch!='community/change-'+body.bundle_id[:24] or any(re.fullmatch(r'[0-9a-f]{40}',value) is None for value in (candidate.commit,candidate.tree,candidate.parent))):
            raise CommunityStoreConflict('Publication candidate differs from immutable repository/bundle scope')
        identifier=str(uuid.uuid4()); payload={'input':body.model_dump(),'candidate':candidate.receipt(),**publication_flags()}
        with closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                prior=db.execute('SELECT id,intent_hash FROM community_publication_intents WHERE request_key=?',(body.request_key,)).fetchone()
                if prior is not None:
                    if prior['intent_hash']!=digest(body.model_dump()): raise CommunityStoreConflict('Original publication key conflict')
                    db.rollback(); return self.get(prior['id']),False
                if db.execute('SELECT 1 FROM community_publication_intents WHERE bundle_id=?',(body.bundle_id,)).fetchone():
                    raise CommunityStoreConflict('This bundle already has an original publication; recover it')
                now=self._time(db)
                db.execute('INSERT INTO community_publication_intents VALUES(?,?,?,?,?,?)',(identifier,body.request_key,body.bundle_id,digest(body.model_dump()),canonical(payload),now))
                db.execute('INSERT INTO community_publication_events(publication_id,phase,payload,created_at_us) VALUES(?,?,?,?)',(identifier,'reserved',canonical(publication_flags()),now)); db.commit()
            except BaseException: db.rollback(); raise
        return self.get(identifier),True

    def append(self,identifier,phase,details):
        """Append bounded redacted service-owned facts only; network operations never run inside the writer."""
        if phase not in PHASES or not isinstance(details,dict): raise CommunityStoreConflict('Invalid publication event')
        # Only fixed typed public trace fields are persisted; transport errors,
        # credentials, source text and candidate output cannot enter receipts.
        allowed={'candidate_commit','candidate_tree','branch','pr_number','remote_head','reason','base_commit'}
        if set(details)-allowed or len(canonical(details).encode())>8192: raise CommunityStoreConflict('Invalid publication event details')
        with closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                now=self._time(db)
                if not db.execute('SELECT 1 FROM community_publication_intents WHERE id=?',(identifier,)).fetchone(): raise CommunityStoreConflict('Unknown publication')
                db.execute('INSERT INTO community_publication_events(publication_id,phase,payload,created_at_us) VALUES(?,?,?,?)',(identifier,phase,canonical({**details,**publication_flags()}),now)); db.commit()
            except BaseException: db.rollback(); raise
        return self.get(identifier)

    def get(self,identifier):
        """Recover historical status after any crash without granting a new POST/ref/PR permission."""
        with closing(self.connect()) as db:
            row=db.execute('SELECT * FROM community_publication_intents WHERE id=?',(identifier,)).fetchone()
            if row is None: raise CommunityStoreConflict('Unknown original publication')
            events=[{'sequence':item['seq'],'phase':item['phase'],'details':json.loads(item['payload']),'created_at_us':item['created_at_us']} for item in db.execute('SELECT * FROM community_publication_events WHERE publication_id=? ORDER BY seq',(identifier,))]
        payload=json.loads(row['payload'])
        from app.core.contracts.community_publication import CommunityPublicationInput
        original=CommunityPublicationInput.model_validate(payload['input']); candidate=payload['candidate']
        if (original.request_key!=row['request_key'] or original.bundle_id!=row['bundle_id'] or digest(original.model_dump())!=row['intent_hash']
                or candidate['bundle_id']!=row['bundle_id'] or candidate['repository_id']!=self.policy.repository_id or candidate['repository_name'].casefold()!=self.policy.repository_name
                or candidate['branch']!='community/change-'+row['bundle_id'][:24] or not events
                or any(payload.get(key)!=value or type(payload.get(key)) is not type(value) for key,value in publication_flags().items())
                or any(any(event['details'].get(key)!=value or type(event['details'].get(key)) is not type(value) for key,value in publication_flags().items()) for event in events)):
            raise CommunityStoreConflict('Original publication receipt does not match stored scope')
        return {'id':identifier,'publication':payload,'phase':events[-1]['phase'],'history':events,
            'historical_only':True,'new_write_authorized':False,'unknown_write_must_not_repeat':True,**publication_flags()}
