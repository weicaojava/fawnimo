"""Retain observation-time eligibility in a separate bounded immutable root; original reads are never continuing action authority."""
from contextlib import closing,contextmanager
import json
from pathlib import Path
import sqlite3
import time

from app.core.contracts.community_review_gates import CommunityReviewGatePolicy,CommunityPRGateInput
from app.services.community.evaluations import canonical,digest
from app.services.community.review_gate_decisions import decide_pr_gate,gate_flags
from app.services.community.review_gate_schema import gate_definitions
from app.services.community.store import CommunityStoreConflict


class CommunityReviewGateStore:
    """A fixed source/policy root with immutable append-only receipts, logical/physical guards and stable explicit sequence order."""

    def __init__(self,root,policy=None,*,initialize=False,isolated_from=(),clock_us=None):
        """Explicit creation needs two separate companion-root paths; recovery can load saved policy without reopening old source/check roots."""
        self.root=Path(root).absolute(); self.database=self.root/'gates.sqlite3'; self.clock_us=clock_us or (lambda:time.time_ns()//1000)
        if self.root.resolve()!=self.root: raise CommunityStoreConflict('Dedicated gate root is redirected')
        if initialize and type(policy) is not CommunityReviewGatePolicy: raise CommunityStoreConflict('Explicit strict gate policy required before creating a root')
        if initialize and len(isolated_from)!=2: raise CommunityStoreConflict('Gate creation requires separate original intake/change roots')
        companions=[]
        for companion in isolated_from:
            other=Path(companion).absolute()
            if other.resolve()!=other or self.root.is_relative_to(other) or other.is_relative_to(self.root):
                raise CommunityStoreConflict('Gate/source/change roots overlap or redirect')
            if any(other.is_relative_to(previous) or previous.is_relative_to(other) for previous in companions):
                raise CommunityStoreConflict('Original intake and change roots must also be separate')
            companions.append(other)
        allowed={'gates.sqlite3','gates.sqlite3-wal','gates.sqlite3-shm','gates.sqlite3-journal'}
        if self.root.exists():
            if any(item.name not in allowed or item.resolve()!=item or not item.is_file() for item in self.root.iterdir()):
                raise CommunityStoreConflict('Gate observations require their own dedicated root')
        elif initialize: self.root.mkdir(parents=True)
        if self.database.resolve()!=self.database or not initialize and not self.database.is_file():
            raise CommunityStoreConflict('Gate root is redirected or not initialized')
        if policy is not None and type(policy) is not CommunityReviewGatePolicy: raise CommunityStoreConflict('Strict maintainer gate policy required')
        with self._writer() as db:
            present={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'") if not row[0].startswith('sqlite_')}
            if not present:
                if not initialize or policy is None: raise CommunityStoreConflict('Gate policy has not been explicitly initialized')
                self.policy=CommunityReviewGatePolicy.model_validate(policy.model_dump()); tables,guards=gate_definitions()
                for definition in tables.values(): db.execute(definition)
                encoded=canonical(self.policy.model_dump()); checksum=digest(self.policy.model_dump())
                db.execute('INSERT INTO community_pr_gate_policy VALUES(1,1,?,?,?,?,?)',
                    (self.policy.profile,self.policy.repository_id,self.policy.repository_name,encoded,checksum))
                for definition in guards.values(): db.execute(definition)
            row=db.execute('SELECT policy FROM community_pr_gate_policy WHERE singleton=1').fetchone()
            if row is None: raise CommunityStoreConflict('Saved exact gate policy required')
            self.policy=CommunityReviewGatePolicy.model_validate(json.loads(row[0])); self.policy_hash=digest(self.policy.model_dump())
            if policy is not None and digest(policy.model_dump())!=self.policy_hash:
                raise CommunityStoreConflict('Gate root belongs to another immutable source/policy; retain old history and use a new root')
            self.validate(db)

    def connect(self):
        """Use short dedicated SQLite connections only; no studio database URL, credential profile or model setting is read."""
        db=sqlite3.connect(self.database,timeout=1.5); db.execute('PRAGMA busy_timeout=1500'); db.execute('PRAGMA foreign_keys=ON'); return db

    @contextmanager
    def _writer(self):
        """Keep network/Git/Docker outside transactions; serialize finite local integrity/CAS appends."""
        with closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try: yield db; db.commit()
            except BaseException: db.rollback(); raise

    def validate(self,db):
        """Refuse partial/unknown schemas or edited policy/history rather than silently enabling a disabled gate or repairing authority."""
        tables,guards=gate_definitions(); present={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'") if not row[0].startswith('sqlite_')}
        if present!=set(tables) or {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}!=set(guards):
            raise CommunityStoreConflict('Unknown or incomplete gate schema')
        for kind,definitions in (('table',tables),('trigger',guards)):
            for name,expected in definitions.items():
                row=db.execute('SELECT sql FROM sqlite_master WHERE type=? AND name=?',(kind,name)).fetchone()
                if row is None or ' '.join(row[0].strip().rstrip(';').split())!=' '.join(expected.strip().rstrip(';').split()):
                    raise CommunityStoreConflict('Gate schema or guard changed')
        expected=(1,1,self.policy.profile,self.policy.repository_id,self.policy.repository_name,canonical(self.policy.model_dump()),self.policy_hash)
        if db.execute('SELECT * FROM community_pr_gate_policy').fetchall()!=[expected] or db.execute('PRAGMA foreign_key_check').fetchall():
            raise CommunityStoreConflict('Gate policy/source identity changed')
        for row in db.execute('SELECT sequence,id,request_key,request_hash,policy_hash,payload,recorded_at_us FROM community_pr_gate_observations'):
            self._decode(row)

    def request_hash(self,body):
        """Bind the strict original check/bundle/PR input to this immutable source/policy; a key cannot silently select a later head."""
        if type(body) is not CommunityPRGateInput: raise CommunityStoreConflict('Strict original gate request required')
        value=CommunityPRGateInput.model_validate(body.model_dump())
        return digest({'domain':self.policy.profile,'policy_hash':self.policy_hash,'input':value.model_dump()})

    def _decode(self,row):
        """Verify saved identity/decision hashes and exact non-authority flags; the returned eligibility remains historical."""
        sequence,identifier,key,intent,policy_hash,raw,recorded=row; payload=json.loads(raw)
        if not isinstance(payload,dict) or set(payload)!={'id','profile','policy_hash','input','request_hash','recorded_at_us','decision'}:
            raise CommunityStoreConflict('Unknown saved gate receipt shape')
        core={name:value for name,value in payload.items() if name!='id'}; body=CommunityPRGateInput.model_validate(payload['input']); decision=payload['decision']
        expected={'request_key':key,'bundle_id':body.bundle_id,'check_attempt_id':body.check_attempt_id,'pr_number':body.pr_number,
            'policy_hash':self.policy_hash,**gate_flags()}
        if (digest({'domain':'community_review_gate_record_v1','record':core})!=identifier or payload['id']!=identifier
                or payload['profile']!=self.policy.profile or payload['policy_hash']!=policy_hash or policy_hash!=self.policy_hash
                or payload['recorded_at_us']!=recorded or type(recorded) is not int or recorded<0
                or payload['request_hash']!=intent or self.request_hash(body)!=intent or body.request_key!=key
                or not isinstance(decision,dict) or any(decision.get(name)!=value for name,value in expected.items())
                or type(decision.get('eligible_at_observation')) is not bool or not isinstance(decision.get('blocking_reasons'),list)
                or decision['eligible_at_observation']!=(not bool(decision['blocking_reasons']))
                or any(decision.get(name) is not False for name in ('promotion_authorized','merge_authorized','deployment_authorized','candidate_tests_are_independent'))
                or any(type(decision.get(name)) is not int or decision[name]!=0 for name in ('external_model_calls','paid_authorization'))):
            raise CommunityStoreConflict('Original immutable gate receipt changed')
        return {'sequence':sequence,'observation':payload,'historical_only':True,'fresh_action_authorized':False,'source_recheck_required_for_action':True}

    def replay(self,body):
        """Return original immutable observation before source/current policy/clock/network checks; never reinterpret a saved pass as authority now."""
        intent=self.request_hash(body)
        with closing(self.connect()) as db:
            row=db.execute('SELECT sequence,id,request_key,request_hash,policy_hash,payload,recorded_at_us FROM community_pr_gate_observations WHERE request_key=?',(body.request_key,)).fetchone()
            if row is None: return None
            if row[3]!=intent: raise CommunityStoreConflict('Original gate key belongs to another check/bundle/PR')
            return {**self._decode(row),'replayed':True}

    def record(self,body,artifact=None,facts=None,observation=None):
        """Derive one observed decision from trusted collector/local facts, preserve original-key recovery, and enforce finite audit capacity without external operations."""
        intent=self.request_hash(body)
        with self._writer() as db:
            old=db.execute('SELECT sequence,id,request_key,request_hash,policy_hash,payload,recorded_at_us FROM community_pr_gate_observations WHERE request_key=?',(body.request_key,)).fetchone()
            if old:
                if old[3]!=intent: raise CommunityStoreConflict('Original gate key belongs to another check/bundle/PR')
                return {**self._decode(old),'replayed':True}
            now=self.clock_us(); previous=db.execute('SELECT max(recorded_at_us) FROM community_pr_gate_observations').fetchone()[0] or 0
            if type(now) is not int or now<previous or now<0: raise CommunityStoreConflict('Gate observation clock is invalid or moved backwards')
            decision=decide_pr_gate(self.policy,body,artifact,facts,observation,now)
            core={'profile':self.policy.profile,'policy_hash':self.policy_hash,'input':body.model_dump(),'request_hash':intent,'recorded_at_us':now,'decision':decision}
            identifier=digest({'domain':'community_review_gate_record_v1','record':core}); payload={'id':identifier,**core}; encoded=canonical(payload)
            count,size=db.execute('SELECT count(*),coalesce(sum(length(CAST(payload AS BLOB))),0) FROM community_pr_gate_observations').fetchone()
            if count>=self.policy.max_saved_observations or size+len(encoded.encode('utf8'))>self.policy.max_saved_bytes or len(encoded.encode('utf8'))>self.policy.max_receipt_bytes:
                raise CommunityStoreConflict('Gate audit history capacity reached; originals remain readable')
            db.execute('INSERT INTO community_pr_gate_observations(id,request_key,request_hash,policy_hash,payload,recorded_at_us) VALUES(?,?,?,?,?,?)',
                (identifier,body.request_key,intent,self.policy_hash,encoded,now))
            row=db.execute('SELECT sequence,id,request_key,request_hash,policy_hash,payload,recorded_at_us FROM community_pr_gate_observations WHERE id=?',(identifier,)).fetchone()
            return {**self._decode(row),'replayed':False}

    def get(self,identifier):
        """Read a historical observation without source roots, network, policy mutation or promotion authority."""
        with closing(self.connect()) as db:
            row=db.execute('SELECT sequence,id,request_key,request_hash,policy_hash,payload,recorded_at_us FROM community_pr_gate_observations WHERE id=?',(identifier,)).fetchone()
            if row is None: raise CommunityStoreConflict('Gate observation not found')
            return self._decode(row)

    def history(self,bundle_id):
        """Return at most the fixed384 saved observations in stable explicit sequence order, including after VACUUM or a restart."""
        with closing(self.connect()) as db:
            rows=db.execute("SELECT sequence,id,request_key,request_hash,policy_hash,payload,recorded_at_us FROM community_pr_gate_observations WHERE json_extract(payload,'$.input.bundle_id')=? ORDER BY sequence LIMIT 384",(bundle_id,)).fetchall()
            return [self._decode(row) for row in rows]
