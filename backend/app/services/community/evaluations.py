"""Immutable local maintainer assessments with bounded duplicate advice and fresh original-public-source checks."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import time
import unicodedata
import uuid

from app.core.contracts.community_evaluations import CommunityEvaluationInput
from app.services.community.public_source import PublicSourceProof, PROTOCOL, verify_public_issue
from app.services.community.store import CommunityStoreConflict
from app.services.community.rowid_guards import rowid_guard_definitions

REVIEW_TABLES = frozenset({'community_review_schema', 'community_evaluations'})


def canonical(value):
    """Hash exact semantic JSON using the same bounded public data, never command strings."""
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)


def digest(value):
    """Bind the original input and public observation to immutable receipt bytes."""
    return hashlib.sha256(canonical(value).encode('utf8')).hexdigest()


def review_definitions():
    """Keep the additive schema and every guard explicit, including raw SQL replacement and original-source scope."""
    tables={
        'community_review_schema':'CREATE TABLE community_review_schema(singleton INTEGER PRIMARY KEY CHECK(singleton=1),version INTEGER NOT NULL CHECK(version=1))',
        'community_evaluations':"CREATE TABLE community_evaluations(id TEXT PRIMARY KEY NOT NULL,request_id TEXT NOT NULL REFERENCES community_requests(id),version_id TEXT NOT NULL REFERENCES community_versions(id),request_key TEXT NOT NULL UNIQUE,intent_hash TEXT NOT NULL CHECK(length(intent_hash)=64),payload TEXT NOT NULL,payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64),appeal_of TEXT REFERENCES community_evaluations(id),duplicate_version_id TEXT REFERENCES community_versions(id),created_at_us INTEGER NOT NULL CHECK(created_at_us>=0))"}
    guards={}
    for table in REVIEW_TABLES:
        for action in ('UPDATE','DELETE'):
            name=table+'_no_'+action.lower()
            guards[name]=f"CREATE TRIGGER {name} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Immutable community evaluation'); END"
        name=table+'_no_replace'
        condition='singleton=NEW.singleton' if table=='community_review_schema' else 'id=NEW.id OR request_key=NEW.request_key'
        guards[name]=f"CREATE TRIGGER {name} BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE {condition}) BEGIN SELECT RAISE(ABORT,'Evaluation replacement rejected'); END"
    guards['community_evaluation_scope']="""CREATE TRIGGER community_evaluation_scope BEFORE INSERT ON community_evaluations WHEN
        NOT EXISTS(SELECT 1 FROM community_versions v JOIN community_requests r ON r.id=v.request_id
            JOIN community_source_clocks c ON c.issue_number=r.issue_number AND c.github_issue_id=r.github_issue_id AND c.author_id=r.author_id
            JOIN community_repository repo ON repo.singleton=1
            WHERE v.id=NEW.version_id AND r.id=NEW.request_id AND r.current_version=v.id AND v.source_eligible=1
            AND r.requires_refresh=0 AND c.source_eligible=1 AND c.conflict=0 AND c.updated_us=v.updated_us AND c.fingerprint=v.fingerprint
            AND json_extract(NEW.payload,'$.input.version_id')=v.id
            AND json_extract(NEW.payload,'$.source.repository_id')=repo.repository_id
            AND json_extract(NEW.payload,'$.source.repository_name')=repo.repository_name
            AND json_extract(NEW.payload,'$.source.issue_number')=r.issue_number
            AND json_extract(NEW.payload,'$.source.github_issue_id')=r.github_issue_id
            AND json_extract(NEW.payload,'$.source.author_id')=r.author_id
            AND json_extract(NEW.payload,'$.source.fingerprint')=v.fingerprint
            AND json_extract(NEW.payload,'$.source.updated_us')=v.updated_us
            AND json_extract(NEW.payload,'$.source.checked_at_us')<=NEW.created_at_us
            AND json_extract(NEW.payload,'$.source.checked_at_us')>=NEW.created_at_us-60000000)
        OR (NEW.appeal_of IS NOT NULL AND NOT EXISTS(SELECT 1 FROM community_evaluations e WHERE e.id=NEW.appeal_of AND e.request_id=NEW.request_id))
        OR json_extract(NEW.payload,'$.input.request_key') IS NOT NEW.request_key
        OR json_extract(NEW.payload,'$.input.appeal_of') IS NOT NEW.appeal_of
        OR json_extract(NEW.payload,'$.input.duplicate_version_id') IS NOT NEW.duplicate_version_id
        OR json_extract(NEW.payload,'$.decision_author') IS NOT 'local_maintainer'
        OR json_extract(NEW.payload,'$.execution_authorized') IS NOT 0
        OR (NEW.duplicate_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM community_versions v JOIN community_requests r ON r.id=v.request_id
            JOIN community_source_clocks c ON c.issue_number=r.issue_number AND c.github_issue_id=r.github_issue_id AND c.author_id=r.author_id
            WHERE v.id=NEW.duplicate_version_id AND v.request_id!=NEW.request_id AND r.current_version=v.id
            AND v.source_eligible=1 AND r.requires_refresh=0 AND c.source_eligible=1 AND c.conflict=0
            AND c.updated_us=v.updated_us AND c.fingerprint=v.fingerprint
            AND json_extract(NEW.payload,'$.duplicate_source.fingerprint')=v.fingerprint))
        BEGIN SELECT RAISE(ABORT,'Invalid evaluation source scope'); END"""
    guards.update(rowid_guard_definitions(REVIEW_TABLES))
    return tables,guards


def validate_review_schema(db, *, allow_legacy_rowid_guards=False):
    """Refuse partial, substituted or downgraded extensions when an intake store is reopened."""
    tables,guards=review_definitions()
    for kind,definitions in [('table',tables),('trigger',guards)]:
        for name,expected in definitions.items():
            row=db.execute('SELECT sql FROM sqlite_master WHERE type=? AND name=?',(kind,name)).fetchone()
            if row is None and kind=='trigger' and allow_legacy_rowid_guards and name.endswith('_no_rowid_replace'):
                continue
            if row is None or ' '.join(row[0].strip().rstrip(';').split())!=' '.join(expected.strip().rstrip(';').split()):
                raise CommunityStoreConflict('Community evaluation schema or guard changed')
    if db.execute('SELECT singleton,version FROM community_review_schema').fetchall()!=[(1,1)]:
        raise CommunityStoreConflict('Unsupported community evaluation schema')


def source_row(db, version_id, *, eligible=True):
    """Resolve one exact original requirement and latest clock before considering any new assessment."""
    row=db.execute('''SELECT r.id,r.issue_number,r.github_issue_id,r.author_id,v.id,v.fingerprint,v.updated_us,v.title,v.body,
        v.source_eligible,r.current_version,r.current_updated_us,r.requires_refresh,c.updated_us,c.fingerprint,c.source_eligible,c.conflict
        FROM community_versions v JOIN community_requests r ON r.id=v.request_id
        JOIN community_source_clocks c ON c.issue_number=r.issue_number AND c.github_issue_id=r.github_issue_id AND c.author_id=r.author_id
        WHERE v.id=?''',(version_id,)).fetchone()
    if row is None:raise CommunityStoreConflict('Original community requirement version not found')
    current=row[9] and row[10]==row[4] and row[6]==row[11]==row[13] and row[5]==row[14] and not row[12] and row[15] and not row[16]
    if eligible and not current:raise CommunityStoreConflict('Requirement is withdrawn, changed or needs source refresh')
    return {'request_id':row[0],'issue_number':row[1],'github_issue_id':row[2],'author_id':row[3],
        'version_id':row[4],'fingerprint':row[5],'updated_us':row[6],'title':row[7],'body':row[8],'source_current':bool(current)}


def validate_proof(config, row, proof, now):
    """A current public observation must match every original identity and exact version, with a finite freshness bound."""
    if (not isinstance(proof,PublicSourceProof) or proof.protocol!=PROTOCOL
        or proof.repository_id!=config.repository_id or proof.repository_name.casefold()!=config.repository_name.casefold()
        or type(proof.checked_at_us) is not int or not 0<=now-proof.checked_at_us<=60000000
        or len(proof.response_sha256)!=2 or any(re.fullmatch(r'[0-9a-f]{64}',value) is None for value in proof.response_sha256)):
        raise CommunityStoreConflict('A fresh original public source observation is required')
    issue=proof.issue
    if not issue.eligible or any(getattr(issue,key)!=row[key] for key in ('issue_number','github_issue_id','author_id','fingerprint','updated_us')):
        raise CommunityStoreConflict('Public source no longer matches the saved selected requirement')


def text_grams(text):
    """Rank only bounded Unicode title/body prefixes; lexical resemblance is advice, never duplicate or acceptance authority."""
    normalized=''.join(c for c in unicodedata.normalize('NFKC',text[:4096]).casefold() if c.isalnum())
    return {normalized[index:index+2] for index in range(max(0,len(normalized)-1))} or set(normalized)


def resemblance(left, right):
    """Use a deterministic integer Dice score so a stored ranking can be reproduced without an AI request."""
    a,b=text_grams(left),text_grams(right)
    return 0 if not a or not b else 20000*len(a&b)//(len(a)+len(b))


def duplicate_suggestions(db, row):
    """Search at most100 latest eligible requirements, title plus4096 body characters, and retain at most5 advisory matches."""
    candidates=db.execute('''SELECT v.id,v.title,substr(v.body,1,4096),v.fingerprint FROM community_versions v
        JOIN community_requests r ON r.id=v.request_id JOIN community_source_clocks c ON c.issue_number=r.issue_number
        AND c.github_issue_id=r.github_issue_id AND c.author_id=r.author_id WHERE v.request_id!=? AND r.current_version=v.id
        AND v.source_eligible=1 AND r.requires_refresh=0 AND c.source_eligible=1 AND c.conflict=0
        AND c.updated_us=v.updated_us AND c.fingerprint=v.fingerprint ORDER BY r.issue_number DESC LIMIT 101''',(row['request_id'],)).fetchall()
    matches=[]
    for version,title,body,fingerprint in candidates[:100]:
        score=(3*resemblance(row['title'],title)+resemblance(row['body'],body))//4
        if score>=3500:matches.append({'version_id':version,'fingerprint':fingerprint,'score_basis_points':score,'title':title})
    return {'algorithm':'unicode-bigram-dice-title3-body1-v1','body_prefix_characters':4096,'searched':min(len(candidates),100),
        'limited':len(candidates)>100,'matches':sorted(matches,key=lambda item:(-item['score_basis_points'],item['version_id']))[:5],
        'advisory_only':True,'ai_model_called':False}


class CommunityEvaluationStore:
    """Add explicit local maintainer review to an existing dedicated intake database; no user studio or execution system is imported."""
    def __init__(self, intake, *, initialize=False, clock_us=None):
        """Install the optional extension atomically only when an operator explicitly requests it."""
        self.intake=intake
        self.clock_us=clock_us or (lambda:time.time_ns()//1000)
        with closing(intake.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                present={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}&REVIEW_TABLES
                if not present:
                    if not initialize:raise CommunityStoreConflict('Community evaluation has not been initialized')
                    tables,guards=review_definitions()
                    for sql in tables.values():db.execute(sql)
                    db.execute('INSERT INTO community_review_schema VALUES(1,1)')
                    for sql in guards.values():db.execute(sql)
                validate_review_schema(db);db.commit()
            except BaseException:db.rollback();raise

    def view(self, db, identifier):
        """Return immutable decision history and current-source status separately, never a scheduling/deployment claim."""
        row=db.execute('SELECT payload,payload_hash,intent_hash,version_id,duplicate_version_id FROM community_evaluations WHERE id=?',(identifier,)).fetchone()
        if row is None:raise CommunityStoreConflict('Evaluation not found')
        payload=json.loads(row[0])
        CommunityEvaluationInput.model_validate(payload['input'])
        if digest(payload)!=row[1] or digest({'domain':'community-evaluation-input-v1',**payload['input']})!=row[2]:
            raise CommunityStoreConflict('Evaluation receipt content changed')
        return {'id':identifier,'receipt':payload,'source_current':source_row(db,row[3],eligible=False)['source_current'],
            'duplicate_source_current':source_row(db,row[4],eligible=False)['source_current'] if row[4] else None,
            'requires_fresh_source_for_new_phase':True,'execution_authorized':False,'model_quality_qualified':False,'public_feedback_sent':False}

    def replay(self, body):
        """Recover the same original assessment before any current source/network/schema-eligibility gate."""
        with closing(self.intake.connect()) as db:
            row=db.execute('SELECT id,intent_hash FROM community_evaluations WHERE request_key=?',(body.request_key,)).fetchone()
            if row is None:return None
            if row[1]!=digest({'domain':'community-evaluation-input-v1',**body.model_dump()}):raise CommunityStoreConflict('Request key belongs to another evaluation')
            return self.view(db,row[0])

    def source(self, version_id):
        """Inspect exact selected source metadata without trusting a caller-provided issue number."""
        with closing(self.intake.connect()) as db:return source_row(db,version_id)

    def suggestions(self, version_id):
        """Read advisory resemblance only; this never accepts a requirement or writes a model/worker task."""
        with closing(self.intake.connect()) as db:return duplicate_suggestions(db,source_row(db,version_id))

    def record(self, body, proof, *, duplicate_proof=None):
        """Freeze a reviewed decision, source evidence and optional appeal in one short CAS-style source transaction."""
        if not isinstance(body,CommunityEvaluationInput):raise CommunityStoreConflict('Evaluation input must use the strict contract')
        previous=self.replay(body)
        if previous:return previous
        registry_path=Path(__file__).resolve().parents[4]/'capabilities/features.json'
        registry_raw=registry_path.read_bytes();registry=json.loads(registry_raw)
        known={item['id'] for item in registry['features']}
        if not set(body.feature_ids)<=known:raise CommunityStoreConflict('Unknown registered feature identity')
        with closing(self.intake.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                old=db.execute('SELECT id,intent_hash FROM community_evaluations WHERE request_key=?',(body.request_key,)).fetchone()
                intent=digest({'domain':'community-evaluation-input-v1',**body.model_dump()})
                if old:
                    if old[1]!=intent:raise CommunityStoreConflict('Request key belongs to another evaluation')
                    result=self.view(db,old[0]);db.commit();return result
                row=source_row(db,body.version_id);now=self.clock_us()
                if type(now) is not int or now<0:raise CommunityStoreConflict('Invalid evaluation clock')
                validate_proof(self.intake.config,row,proof,now)
                if db.execute('SELECT count(*) FROM community_evaluations').fetchone()[0]>=10000 or db.execute('SELECT count(*) FROM community_evaluations WHERE request_id=?',(row['request_id'],)).fetchone()[0]>=200:
                    raise CommunityStoreConflict('Local evaluation history capacity reached')
                if body.appeal_of:
                    old=db.execute('SELECT request_id FROM community_evaluations WHERE id=?',(body.appeal_of,)).fetchone()
                    if old is None or old[0]!=row['request_id']:raise CommunityStoreConflict('Reconsideration must reference the same original requirement')
                duplicate=None
                if body.duplicate_version_id:
                    other=source_row(db,body.duplicate_version_id)
                    if other['request_id']==row['request_id']:raise CommunityStoreConflict('A requirement cannot duplicate its own history')
                    validate_proof(self.intake.config,other,duplicate_proof,now);duplicate=duplicate_proof.receipt()
                identifier=str(uuid.uuid4())
                payload={'schema_version':1,'input':body.model_dump(),'source':proof.receipt(),'duplicate_source':duplicate,
                    'duplicate_advice':duplicate_suggestions(db,row),'feature_register_sha256':hashlib.sha256(registry_raw).hexdigest(),
                    'decision_author':'local_maintainer','advisory_note_is_unverified_data':True,'ai_model_called':False,
                    'created_at_us':now,'execution_authorized':False,'priority_assigned':False,'public_feedback_sent':False}
                db.execute('INSERT INTO community_evaluations VALUES(?,?,?,?,?,?,?,?,?,?)',(identifier,row['request_id'],body.version_id,
                    body.request_key,intent,canonical(payload),digest(payload),body.appeal_of,body.duplicate_version_id,now))
                result=self.view(db,identifier);db.commit();return result
            except BaseException:db.rollback();raise

    def history(self, version_id):
        """List at most200 retained assessments for the same logical requirement, across clarifications and new source versions."""
        with closing(self.intake.connect()) as db:
            row=source_row(db,version_id,eligible=False)
            identifiers=db.execute('SELECT id FROM community_evaluations WHERE request_id=? ORDER BY created_at_us,id LIMIT 201',(row['request_id'],)).fetchall()
            if len(identifiers)>200:raise CommunityStoreConflict('Evaluation history exceeds capacity')
            return [self.view(db,item[0]) for item in identifiers]


async def evaluate_current_public_source(store, body):
    """Only the maintainer command performs live source reads; repeated original intents bypass current network gates."""
    previous=store.replay(body)
    if previous:return previous
    row=store.source(body.version_id)
    proof=await verify_public_issue(store.intake.config,row['issue_number'])
    duplicate=None
    if body.duplicate_version_id:
        other=store.source(body.duplicate_version_id)
        duplicate=await verify_public_issue(store.intake.config,other['issue_number'])
    return store.record(body,proof,duplicate_proof=duplicate)
