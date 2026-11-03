"""Dedicated append-only local coding-model journal, separating before-send intent, inert generated text and existing change-bundle linkage."""
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from app.core.contracts.community_model_code import CommunityCodeModelRequest,CommunityCodeModelConfiguration,CommunityCodeModelResult
from app.services.community.advice_store import canonical,digest
from app.services.community.development_model_child import validate_proposal
from app.services.community.development_model_protocol import change_input,validate_bundle_binding
from app.services.community.change_bundles import original_change_hash,verify_change_bundle

MAX_HISTORY_BYTES=32*1024*1024
MAX_RESULT_BYTES=65536
MAX_LINK_BYTES=4096


def definitions():
    """Freeze four dedicated tables and logical/physical replacement guards; generated text alone never creates a bundle link."""
    tables={
        'code_model_meta':'CREATE TABLE code_model_meta(singleton INTEGER PRIMARY KEY CHECK(singleton=1),identity TEXT NOT NULL)',
        'code_model_intents':'CREATE TABLE code_model_intents(request_key TEXT PRIMARY KEY NOT NULL,work_id TEXT NOT NULL,request_hash TEXT NOT NULL CHECK(length(request_hash)=64),payload TEXT NOT NULL CHECK(length(CAST(payload AS BLOB))<=98304),payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64),created_us INTEGER NOT NULL CHECK(created_us>=0))',
        'code_model_results':"CREATE TABLE code_model_results(request_key TEXT PRIMARY KEY NOT NULL REFERENCES code_model_intents(request_key),state TEXT NOT NULL CHECK(state IN ('generated','not_submitted','uncertain')),payload TEXT NOT NULL CHECK(length(CAST(payload AS BLOB))<=65536),payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64))",
        'code_model_bundles':'CREATE TABLE code_model_bundles(request_key TEXT PRIMARY KEY NOT NULL REFERENCES code_model_results(request_key),bundle_id TEXT NOT NULL CHECK(length(bundle_id)=64),payload TEXT NOT NULL CHECK(length(CAST(payload AS BLOB))<=4096),payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64))'}
    guards={}
    for table in tables:
        for action in ('UPDATE','DELETE'):
            name=table+'_no_'+action.lower()
            guards[name]=f"CREATE TRIGGER {name} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Immutable local coding-model history'); END"
        key='singleton' if table=='code_model_meta' else 'request_key'
        name=table+'_no_replace'
        guards[name]=f"CREATE TRIGGER {name} BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE {key}=NEW.{key} OR rowid=NEW.rowid) BEGIN SELECT RAISE(ABORT,'Local coding-model replacement rejected'); END"
    guards['code_model_result_scope']="CREATE TRIGGER code_model_result_scope BEFORE INSERT ON code_model_results WHEN json_extract(NEW.payload,'$.state') IS NOT NEW.state OR json_extract(NEW.payload,'$.execution_authorized') IS NOT 0 OR json_extract(NEW.payload,'$.external_fee_fen') IS NOT 0 OR json_extract(NEW.payload,'$.model_quality_qualified') IS NOT 0 OR json_extract(NEW.payload,'$.independent_review_completed') IS NOT 0 BEGIN SELECT RAISE(ABORT,'Invalid inert coding-model result'); END"
    guards['code_model_bundle_scope']="CREATE TRIGGER code_model_bundle_scope BEFORE INSERT ON code_model_bundles WHEN NOT EXISTS(SELECT 1 FROM code_model_results r WHERE r.request_key=NEW.request_key AND r.state='generated' AND r.payload_hash=json_extract(NEW.payload,'$.model_result_sha256')) OR json_extract(NEW.payload,'$.request_key') IS NOT NEW.request_key OR json_extract(NEW.payload,'$.bundle_id') IS NOT NEW.bundle_id OR json_extract(NEW.payload,'$.execution_authorized') IS NOT 0 BEGIN SELECT RAISE(ABORT,'Invalid original coding-model bundle link'); END"
    return tables,guards


class CommunityCodeModelStore:
    """Keep finite separately configured maintainer model requests outside public intake, changes and all personal project data."""
    def __init__(self,root,repository_id,repository_name,*,initialize=False):
        """Initialize only a dedicated empty root and bind immutable repository/profile identity; never open a studio database."""
        self.root=Path(root).absolute();self.path=self.root/'local-code-model.sqlite3'
        self.identity={'schema_version':1,'profile':'maintainer_local_ollama_code_v1','repository_id':repository_id,'repository_name':repository_name.casefold()}
        if self.root.resolve()!=self.root or self.root.is_symlink() or type(repository_id) is not int or repository_id<=0:
            raise ValueError('Invalid dedicated local code-model journal')
        if not self.path.exists():
            if not initialize:raise ValueError('Local code-model journal requires explicit initialization')
            self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
            if any(self.root.iterdir()):raise ValueError('New local code-model journal requires an empty dedicated directory')
            fd=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
            with closing(self.connect()) as db:
                db.execute('BEGIN IMMEDIATE')
                tables,guards=definitions()
                for statement in tables.values():db.execute(statement)
                db.execute('INSERT INTO code_model_meta VALUES(1,?)',(canonical(self.identity),))
                for statement in guards.values():db.execute(statement)
                db.commit()
        allowed={self.path.name,self.path.name+'-wal',self.path.name+'-shm',self.path.name+'-journal'}
        if self.path.is_symlink() or not self.path.is_file() or self.path.stat().st_size>36*1024*1024 or any(item.name not in allowed or item.resolve()!=item or not item.is_file() for item in self.root.iterdir()):
            raise ValueError('Invalid or mixed local code-model journal')
        with closing(self.connect()) as db:self.validate(db)

    def connect(self):
        """Use a finite writer wait and foreign-key validation without application/project configuration."""
        db=sqlite3.connect(self.path,timeout=5);db.execute('PRAGMA foreign_keys=ON')
        return db

    def validate(self,db):
        """Refuse unknown, damaged or substituted schema/identity before reading or appending inert source."""
        tables,guards=definitions()
        actual={(kind,name):text for kind,name,text in db.execute("SELECT type,name,sql FROM sqlite_master WHERE type IN ('table','trigger') AND name NOT LIKE 'sqlite_%'")}
        expected={**{('table',name):text for name,text in tables.items()},**{('trigger',name):text for name,text in guards.items()}}
        normalize=lambda value:' '.join(value.strip().rstrip(';').split())
        if set(actual)!=set(expected) or any(normalize(actual[key])!=normalize(value) for key,value in expected.items()):raise ValueError('Local code-model history schema changed')
        if db.execute('SELECT singleton,identity FROM code_model_meta').fetchall()!=[(1,canonical(self.identity))] or db.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('Local code-model repository identity changed')

    def view(self,db,request):
        """Recover exact original source/configuration and immutable results before any current network, model or preparation gates."""
        row=db.execute('SELECT request_hash,payload,payload_hash FROM code_model_intents WHERE request_key=?',(request.request_key,)).fetchone()
        if row is None:return None
        intent=json.loads(row[1])
        if row[0]!=digest(request.model_dump()) or digest(intent)!=row[2] or intent.get('request')!=request.model_dump():raise ValueError('Original local coding-model request changed')
        config=CommunityCodeModelConfiguration.model_validate(intent['configuration'])
        if digest(config.model_dump())!=intent['configuration_sha256'] or intent['context']['paths']!=request.paths or intent['context']['test_files']!=request.test_files or intent['context']['scope']['work_id']!=request.work_id:raise ValueError('Original code-model configuration or selected scope changed')
        result_row=db.execute('SELECT payload,payload_hash FROM code_model_results WHERE request_key=?',(request.request_key,)).fetchone()
        result=json.loads(result_row[0]) if result_row else None
        if result is not None:
            typed=CommunityCodeModelResult.model_validate(result)
            if digest(result)!=result_row[1]:raise ValueError('Original generated-text receipt changed')
            if typed.state=='generated':
                validate_proposal(typed.proposal.model_dump(),intent['context'])
                if typed.model_digest!=config.model_digest:raise ValueError('Generated text belongs to another installed model')
        linked=db.execute('SELECT bundle_id,payload,payload_hash FROM code_model_bundles WHERE request_key=?',(request.request_key,)).fetchone()
        link=json.loads(linked[1]) if linked else None
        if link is not None:
            if digest(link)!=linked[2] or result is None or result['state']!='generated' or link['bundle_id']!=linked[0] or link['model_result_sha256']!=result_row[1] or link['input_hash']!=original_change_hash(change_input(intent,result)) or re.fullmatch(r'[a-f0-9]{64}',link['bundle_id']) is None:raise ValueError('Original model-to-bundle link changed')
        return {'request_key':request.request_key,'work_id':request.work_id,'state':'bundled' if link else result['state'] if result else 'uncertain',
            'intent':intent,'model_result':result,'bundle':link,'automatic_model_retry_allowed':False,
            'execution_authorized':False,'model_quality_qualified':False,'independent_review_completed':False,'external_fee_fen':0}

    def replay(self,request):
        """Read the same original request without enabling a model, source fetch, code execution or publication."""
        if not isinstance(request,CommunityCodeModelRequest):raise ValueError('Strict original code-model request required')
        with closing(self.connect()) as db:
            self.validate(db)
            return self.view(db,request)

    def reserve(self,request,configuration,context,provenance):
        """Atomically reserve one original local inference and every pending result/link byte before its POST gate opens."""
        request=CommunityCodeModelRequest.model_validate(request.model_dump())
        with closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE');self.validate(db)
            previous=self.view(db,request)
            if previous is not None:return False,previous
            config=CommunityCodeModelConfiguration.model_validate(configuration.model_dump())
            if not config.enabled or config.max_requests<1:raise ValueError('Local coding model is disabled or has no explicit finite quota')
            if db.execute('SELECT count(*) FROM code_model_intents').fetchone()[0]>=config.max_requests:raise ValueError('Local coding-model request quota exhausted')
            if db.execute('SELECT count(*) FROM code_model_intents WHERE work_id=?',(request.work_id,)).fetchone()[0]>=8:raise ValueError('Original prepared-work model capacity exhausted')
            if context['paths']!=request.paths or context['test_files']!=request.test_files or context['scope']['work_id']!=request.work_id:raise ValueError('Selected local code-model context changed')
            intent={'request':request.model_dump(),'configuration':config.model_dump(),'configuration_sha256':digest(config.model_dump()),
                'context':context,'provenance':provenance,'execution_authorized':False,'external_fee_fen':0}
            payload=canonical(intent)
            used=sum(db.execute('SELECT coalesce(sum(length(CAST(payload AS BLOB))),0) FROM '+table).fetchone()[0] for table in ('code_model_intents','code_model_results','code_model_bundles'))
            pending_model=db.execute('SELECT count(*) FROM code_model_intents i LEFT JOIN code_model_results r ON r.request_key=i.request_key WHERE r.request_key IS NULL').fetchone()[0]
            pending_link=db.execute("SELECT count(*) FROM code_model_intents i LEFT JOIN code_model_results r ON r.request_key=i.request_key LEFT JOIN code_model_bundles b ON b.request_key=i.request_key WHERE b.request_key IS NULL AND (r.request_key IS NULL OR r.state='generated')").fetchone()[0]
            if len(payload.encode('utf8'))>98304 or used+len(payload.encode('utf8'))+(pending_model+1)*MAX_RESULT_BYTES+(pending_link+1)*MAX_LINK_BYTES>MAX_HISTORY_BYTES:raise ValueError('Local coding-model byte capacity exhausted')
            db.execute('INSERT INTO code_model_intents VALUES(?,?,?,?,?,?)',(request.request_key,request.work_id,digest(request.model_dump()),payload,digest(intent),time.time_ns()//1000))
            db.commit()
            return True,self.view(db,request)

    def record_result(self,request,result):
        """Append a single validated original inference outcome; never replace or regenerate a lost or ambiguous model response."""
        checked=CommunityCodeModelResult.model_validate(result);receipt=checked.model_dump()
        payload=canonical(receipt)
        if len(payload.encode('utf8'))>MAX_RESULT_BYTES:raise ValueError('Local coding-model outcome exceeds its reserved capacity')
        with closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE');self.validate(db)
            previous=self.view(db,request)
            if previous is None:raise ValueError('Original coding-model intent is missing')
            if previous['model_result'] is not None:
                if digest(previous['model_result'])!=digest(receipt):raise ValueError('Original model result already belongs to another outcome')
                return previous
            if checked.state=='generated':
                validate_proposal(checked.proposal.model_dump(),previous['intent']['context'])
                if checked.model_digest!=previous['intent']['configuration']['model_digest']:raise ValueError('Generated source belongs to another configured model')
            db.execute('INSERT INTO code_model_results VALUES(?,?,?,?)',(request.request_key,checked.state,payload,digest(receipt)))
            db.commit()
            return self.view(db,request)

    def attach_bundle(self,request,artifact):
        """Link only the exact already-saved inert original bundle, without executing files or granting test/PR/publication authority."""
        bundle=verify_change_bundle(artifact)
        with closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE');self.validate(db)
            previous=self.view(db,request)
            if previous is None or previous['model_result'] is None or previous['model_result']['state']!='generated':raise ValueError('Original generated model text is required')
            body=change_input(previous['intent'],previous['model_result'])
            validate_bundle_binding(previous['intent'],previous['model_result'],artifact)
            if bundle['input']!=body.model_dump() or bundle['scope']['work_id']!=request.work_id:raise ValueError('Inert bundle differs from the original model result and scope')
            link={'request_key':request.request_key,'bundle_id':artifact['bundle_id'],'input_hash':original_change_hash(body),
                'model_result_sha256':digest(previous['model_result']),'execution_authorized':False,'external_fee_fen':0,
                'model_quality_qualified':False,'independent_review_completed':False}
            payload=canonical(link)
            if len(payload.encode('utf8'))>MAX_LINK_BYTES:raise ValueError('Original model/bundle link exceeds its reservation')
            if previous['bundle'] is not None:
                if digest(previous['bundle'])!=digest(link):raise ValueError('Original model result is already linked to another bundle')
                return previous
            db.execute('INSERT INTO code_model_bundles VALUES(?,?,?,?)',(request.request_key,artifact['bundle_id'],payload,digest(link)))
            db.commit()
            return self.view(db,request)
