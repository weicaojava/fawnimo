"""Separate private append-only journal for one local-maintainer advice request; no video project or evaluation table writes."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

from app.core.contracts.community_advice import CommunityAdviceRequest,CommunityAdviceReceipt,CommunityAdviceConfiguration
from app.services.community.advice_child import validate_advice

MAX_HISTORY_BYTES = 16*1024*1024
MAX_RECEIPT_BYTES = 32768


def canonical(value):
    """Bind exact semantic input and receipt bytes without accepting nonfinite JSON."""
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)


def digest(value):
    """Keep immutable intent and result hashes independent of filesystem names or model-written fields."""
    return hashlib.sha256(canonical(value).encode('utf8')).hexdigest()


def require_separate_roots(intake_root, advice_root):
    """Reject mixed or nested intake/advice locations before initializing either private journal directory."""
    intake,advice = Path(intake_root).absolute(),Path(advice_root).absolute()
    if intake == advice or intake.is_relative_to(advice) or advice.is_relative_to(intake):
        raise ValueError('Advice and intake require separate dedicated roots')


def definitions():
    """Freeze the dedicated three-table schema, rowid replacement guards and original result scope."""
    tables = {
        'advice_meta':'CREATE TABLE advice_meta(singleton INTEGER PRIMARY KEY CHECK(singleton=1),identity TEXT NOT NULL)',
        'advice_intents':'CREATE TABLE advice_intents(request_key TEXT PRIMARY KEY NOT NULL,version_id TEXT NOT NULL,request_hash TEXT NOT NULL CHECK(length(request_hash)=64),payload TEXT NOT NULL,payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64),created_us INTEGER NOT NULL CHECK(created_us>=0))',
        'advice_results':"CREATE TABLE advice_results(request_key TEXT PRIMARY KEY NOT NULL REFERENCES advice_intents(request_key),state TEXT NOT NULL CHECK(state IN ('ready','not_submitted','uncertain')),payload TEXT NOT NULL,payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64))",
    }
    guards = {}
    for table in tables:
        for action in ('UPDATE','DELETE'):
            name = table+'_no_'+action.lower()
            guards[name] = f"CREATE TRIGGER {name} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Immutable local advice history'); END"
        key = 'singleton' if table == 'advice_meta' else 'request_key'
        name = table+'_no_replace'
        guards[name] = f"CREATE TRIGGER {name} BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE {key}=NEW.{key} OR rowid=NEW.rowid) BEGIN SELECT RAISE(ABORT,'Local advice replacement rejected'); END"
    guards['advice_result_scope'] = "CREATE TRIGGER advice_result_scope BEFORE INSERT ON advice_results WHEN NOT EXISTS(SELECT 1 FROM advice_intents WHERE request_key=NEW.request_key) OR json_extract(NEW.payload,'$.state') IS NOT NEW.state OR json_extract(NEW.payload,'$.execution_authorized') IS NOT 0 OR json_extract(NEW.payload,'$.external_fee_fen') IS NOT 0 OR json_extract(NEW.payload,'$.model_quality_qualified') IS NOT 0 BEGIN SELECT RAISE(ABORT,'Invalid local advice receipt scope'); END"
    return tables,guards


class CommunityAdviceStore:
    """Keep maintainer inference attempts separate from intake/evaluations, with durable before-send intent and no automatic retry."""
    def __init__(self, root, repository_id, repository_name, *, initialize=False):
        """Open only an explicitly initialized dedicated journal and require its exact original repository and schema."""
        original = Path(root).absolute()
        if original.is_symlink() or original.resolve() != original:
            raise ValueError('Advice journal requires a real dedicated directory')
        self.root = original
        self.path = original/'maintainer-advice.sqlite3'
        self.identity = {'schema_version':1,'repository_id':repository_id,'repository_name':repository_name.casefold()}
        if type(repository_id) is not int or repository_id <= 0:
            raise ValueError('Advice repository identity is required')
        if not self.path.exists():
            if not initialize:
                raise ValueError('Advice journal has not been explicitly initialized')
            original.mkdir(parents=True,exist_ok=True,mode=0o700)
            if any(original.iterdir()):
                raise ValueError('New advice journal requires its own empty directory')
            descriptor = os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            os.close(descriptor)
            with closing(self.connect()) as db:
                db.execute('BEGIN IMMEDIATE')
                tables,guards = definitions()
                for sql in tables.values():
                    db.execute(sql)
                db.execute('INSERT INTO advice_meta VALUES(1,?)',(canonical(self.identity),))
                for sql in guards.values():
                    db.execute(sql)
                db.commit()
        if self.path.is_symlink() or not self.path.is_file() or self.path.stat().st_size > 20*1024*1024:
            raise ValueError('Invalid bounded advice journal')
        allowed = {self.path.name,self.path.name+'-wal',self.path.name+'-shm',self.path.name+'-journal'}
        if any(item.name not in allowed or item.resolve() != item or not item.is_file() for item in original.iterdir()):
            raise ValueError('Advice journal requires a dedicated directory')
        with closing(self.connect()) as db:
            self.validate(db)

    def connect(self):
        """Use finite SQLite contention time and foreign keys without loading studio settings or credentials."""
        db = sqlite3.connect(self.path,timeout=5)
        db.execute('PRAGMA foreign_keys=ON')
        return db

    def validate(self, db):
        """Refuse unknown, missing or altered schema objects and repository substitution before reading or writing advice."""
        tables,guards = definitions()
        actual = {(kind,name):sql for kind,name,sql in db.execute("SELECT type,name,sql FROM sqlite_master WHERE type IN ('table','trigger') AND name NOT LIKE 'sqlite_%'")}
        expected = {**{('table',key):value for key,value in tables.items()},**{('trigger',key):value for key,value in guards.items()}}
        normalize = lambda value:' '.join(value.strip().rstrip(';').split())
        if set(actual) != set(expected) or any(normalize(actual[key]) != normalize(value) for key,value in expected.items()):
            raise ValueError('Advice journal schema or history guards changed')
        if db.execute('SELECT singleton,identity FROM advice_meta').fetchall() != [(1,canonical(self.identity))] or db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('Advice journal original identity or references changed')

    def view(self, db, request):
        """Return original immutable advice or an uncertain before-send record; neither is permission to execute or resubmit."""
        row = db.execute('SELECT request_hash,payload,payload_hash FROM advice_intents WHERE request_key=?',(request.request_key,)).fetchone()
        if row is None:
            return None
        if row[0] != digest(request.model_dump()):
            raise ValueError('Original advice request key belongs to another version')
        intent = json.loads(row[1])
        if digest(intent) != row[2] or intent.get('request') != request.model_dump():
            raise ValueError('Original advice intent changed')
        config = CommunityAdviceConfiguration.model_validate(intent['configuration'])
        if digest(config.model_dump()) != intent['configuration_sha256']:
            raise ValueError('Original advice model configuration changed')
        result = db.execute('SELECT payload,payload_hash FROM advice_results WHERE request_key=?',(request.request_key,)).fetchone()
        receipt = json.loads(result[0]) if result else None
        if receipt is not None and digest(receipt) != result[1]:
            raise ValueError('Original advice receipt changed')
        if receipt is not None:
            checked = CommunityAdviceReceipt.model_validate(receipt)
            if checked.state == 'ready':
                validate_advice(checked.advice.model_dump(),intent['context'])
                if checked.model_digest != config.model_digest:
                    raise ValueError('Advice receipt belongs to another installed model')
        return {'request_key':request.request_key,'version_id':request.version_id,'state':receipt['state'] if receipt else 'uncertain',
            'intent':intent,'receipt':receipt,'advisory_only':True,'execution_authorized':False,'automatic_retry_allowed':False,
            'external_fee_fen':0,'model_quality_qualified':False,'public_feedback_sent':False}

    def replay(self, request):
        """Check durable original identity before any current model, quota, source, enabled switch or network operation."""
        if not isinstance(request,CommunityAdviceRequest):
            raise ValueError('Strict advice request is required')
        with closing(self.connect()) as db:
            self.validate(db)
            return self.view(db,request)

    def reserve(self, request, config, context, proof):
        """Atomically reserve one finite local inference slot; competing callers can never send a second POST for the same key."""
        with closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            self.validate(db)
            previous = self.view(db,request)
            if previous is not None:
                return False,previous
            if not config.enabled or config.max_requests == 0 or config.external_fee_fen != 0:
                raise ValueError('Local model advice is disabled or has no finite quota')
            if db.execute('SELECT count(*) FROM advice_intents').fetchone()[0] >= config.max_requests:
                raise ValueError('Maintainer advice request quota exhausted')
            if db.execute('SELECT count(*) FROM advice_intents WHERE version_id=?',(request.version_id,)).fetchone()[0] >= 20:
                raise ValueError('Original version advice capacity exhausted')
            intent = {'request':request.model_dump(),'configuration':config.model_dump(),'configuration_sha256':digest(config.model_dump()),
                'context':context,'source_proof':proof,'execution_authorized':False,'external_fee_fen':0}
            payload = canonical(intent)
            used = sum(db.execute('SELECT coalesce(sum(length(CAST(payload AS BLOB))),0) FROM '+table).fetchone()[0] for table in ('advice_intents','advice_results'))
            pending = db.execute('SELECT count(*) FROM advice_intents i LEFT JOIN advice_results r ON r.request_key=i.request_key WHERE r.request_key IS NULL').fetchone()[0]
            if len(payload.encode('utf8')) > 65536 or used+len(payload.encode('utf8'))+(pending+1)*MAX_RECEIPT_BYTES > MAX_HISTORY_BYTES:
                raise ValueError('Bounded advice history capacity exhausted')
            db.execute('INSERT INTO advice_intents VALUES(?,?,?,?,?,?)',(request.request_key,request.version_id,digest(request.model_dump()),payload,digest(intent),time.time_ns()//1000))
            db.commit()
            return True,self.view(db,request)

    def finish(self, request, receipt):
        """Append at most one original outcome; lost acknowledgment and failed inference retain the intent without rerunning the model."""
        checked = CommunityAdviceReceipt.model_validate(receipt)
        receipt = checked.model_dump(exclude_unset=True)
        if len(canonical(receipt).encode('utf8')) > MAX_RECEIPT_BYTES:
            raise ValueError('Advice receipt exceeds its bounded profile')
        with closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            self.validate(db)
            previous = self.view(db,request)
            if previous is None:
                raise ValueError('Original advice intent is missing')
            if previous['receipt'] is not None:
                return previous
            if checked.state == 'ready':
                validate_advice(checked.advice.model_dump(),previous['intent']['context'])
                if checked.model_digest != previous['intent']['configuration']['model_digest']:
                    raise ValueError('Advice result belongs to another original model')
            db.execute('INSERT INTO advice_results VALUES(?,?,?,?)',(request.request_key,receipt['state'],canonical(receipt),digest(receipt)))
            db.commit()
            return self.view(db,request)
