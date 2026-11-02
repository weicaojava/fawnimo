"""Versioned local-maintainer roadmap snapshots and recoverable selection, without starting development attempts."""
from contextlib import closing
import json
from pathlib import Path
import time
import uuid
from app.core.contracts.community_queue import CommunityQueuePlanInput,CommunityQueuePolicy
from app.services.community.evaluations import CommunityEvaluationStore,canonical,digest,source_row
from app.services.community.queue_planning import build_queue_plan
from app.services.community.queue_schema import QUEUE_TABLES,queue_definitions,validate_queue_schema
from app.services.community.store import CommunityStoreConflict


class CommunityQueueStore:
    """Own optional planning tables beside existing public source/review records, never personal studio or model data."""
    def __init__(self,intake,*,initialize=False,policy=None,clock_us=None):
        """Initialize only after review schema exists; immutable root policy cannot be changed by a new plan or restart."""
        self.intake=intake;self.clock_us=clock_us or (lambda:time.time_ns()//1000)
        self.reviews=CommunityEvaluationStore(intake)
        with closing(intake.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                present={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}&QUEUE_TABLES
                if not present:
                    if not initialize:raise CommunityStoreConflict('Queue planning has not been initialized')
                    chosen=policy or CommunityQueuePolicy()
                    if not isinstance(chosen,CommunityQueuePolicy):raise CommunityStoreConflict('Strict queue policy required')
                    tables,guards=queue_definitions()
                    for sql in tables.values():db.execute(sql)
                    db.execute('INSERT INTO community_queue_policy VALUES(1,1,?,?)',(canonical(chosen.model_dump()),digest(chosen.model_dump())))
                    db.execute('INSERT INTO community_queue_current VALUES(1,0,NULL)')
                    # Old supported SQLite backups preserve their retained row
                    # order. Record that legacy origin once; future decisions
                    # receive an explicit INTEGER sequence inside their commit.
                    db.execute("INSERT INTO community_evaluation_order(evaluation_id,origin) SELECT id,'legacy_rowid_snapshot_v1' FROM community_evaluations ORDER BY rowid")
                    for sql in guards.values():db.execute(sql)
                self.policy=validate_queue_schema(db)
                if policy is not None and policy!=self.policy:raise CommunityStoreConflict('Existing queue has another immutable operational policy')
                db.commit()
            except BaseException:db.rollback();raise

    def evaluation_context(self,db,identifier):
        """Only the latest saved local decision for the logical requirement can enter a new plan, even with tied wall clocks."""
        reviewed=self.reviews.view(db,identifier);source=source_row(db,reviewed['receipt']['input']['version_id'])
        latest=db.execute('SELECT e.id,o.sequence,o.origin FROM community_evaluations e JOIN community_evaluation_order o ON o.evaluation_id=e.id WHERE e.request_id=? ORDER BY o.sequence DESC LIMIT 1',(source['request_id'],)).fetchone()
        if latest is None or latest[0]!=identifier:raise CommunityStoreConflict('A later assessment supersedes this queue decision')
        body=reviewed['receipt']['input']
        return {'decision':body['decision'],'source_current':reviewed['source_current'],'feature_ids':body['feature_ids'],
            'acceptance':body['acceptance'],'request_id':source['request_id'],'version_id':source['version_id'],
            'fingerprint':source['fingerprint'],'evaluation_hash':digest(reviewed['receipt']),
            'evaluation_sequence':latest[1],'evaluation_order_origin':latest[2]}

    def catalog(self):
        """Read only the shipped public feature register; callers cannot select a catalog file or private data root."""
        return json.loads((Path(__file__).resolve().parents[4]/'capabilities/features.json').read_text('utf8'))

    def view(self,db,identifier):
        """Keep a saved ranking immutable and expose its selected/current-source state separately."""
        row=db.execute('SELECT payload,payload_hash,intent_hash FROM community_queue_plans WHERE id=?',(identifier,)).fetchone()
        if row is None:raise CommunityStoreConflict('Queue plan not found')
        payload=json.loads(row[0]);body=CommunityQueuePlanInput.model_validate(payload['input'])
        if digest(payload)!=row[1] or digest({'domain':'community-queue-plan-input-v1',**body.model_dump()})!=row[2]:
            raise CommunityStoreConflict('Queue plan receipt changed')
        current=db.execute('SELECT revision,plan_id FROM community_queue_current WHERE singleton=1').fetchone()
        freshness={}
        for entry in payload['entries']:
            try:context=self.evaluation_context(db,entry['evaluation_id'])
            except CommunityStoreConflict:freshness[entry['key']]=False
            else:freshness[entry['key']]=context['evaluation_hash']==entry['evaluation_hash'] and context['fingerprint']==entry['source_fingerprint'] and context['evaluation_sequence']==entry['evaluation_sequence']
        return {'id':identifier,'content_hash':row[1],'plan':payload,'current_revision':current[0],'selected':current[1]==identifier,
            'current_original_assessments':freshness,'feature_catalog_current':digest(self.catalog())==payload['feature_catalog_hash'],
            'execution_authorized':False,'public_deployment_verified':False}

    def create_plan(self,body):
        """Replay original snapshots before current assessment/catalog gates; new versions freeze only trusted saved contexts."""
        if not isinstance(body,CommunityQueuePlanInput):raise CommunityStoreConflict('Strict queue plan input required')
        intent=digest({'domain':'community-queue-plan-input-v1',**body.model_dump()})
        with closing(self.intake.connect()) as db:
            old=db.execute('SELECT id,intent_hash FROM community_queue_plans WHERE request_key=?',(body.request_key,)).fetchone()
            if old:
                if old[1]!=intent:raise CommunityStoreConflict('Original plan key belongs to another intent')
                return self.view(db,old[0])
        registry=self.catalog()
        with closing(self.intake.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                old=db.execute('SELECT id,intent_hash FROM community_queue_plans WHERE request_key=?',(body.request_key,)).fetchone()
                if old:
                    if old[1]!=intent:raise CommunityStoreConflict('Original plan key belongs to another intent')
                    result=self.view(db,old[0]);db.commit();return result
                if db.execute('SELECT count(*) FROM community_queue_plans').fetchone()[0]>=1000:raise CommunityStoreConflict('Queue plan history capacity reached')
                contexts={entry.evaluation_id:self.evaluation_context(db,entry.evaluation_id) for entry in body.proposals}
                payload=build_queue_plan(body,self.policy,registry,contexts)
                payload['queue_policy_hash']=digest(self.policy.model_dump())
                if len(canonical(payload).encode('utf8'))>2097152:raise CommunityStoreConflict('Complete queue plan exceeds2MiB')
                identifier=str(uuid.uuid4());now=self.clock_us()
                if type(now) is not int or now<0:raise CommunityStoreConflict('Invalid queue clock')
                db.execute('INSERT INTO community_queue_plans VALUES(?,?,?,?,?,?)',(identifier,body.request_key,intent,canonical(payload),digest(payload),now))
                result=self.view(db,identifier);db.commit();return result
            except BaseException:db.rollback();raise

    def activate(self,plan_id,request_key,expected_revision):
        """CAS select/deactivate one roadmap; replay of an older selection must never reset a later current pointer."""
        import re
        if (plan_id is not None and (not isinstance(plan_id,str) or not 1<=len(plan_id)<=64)) or not isinstance(request_key,str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}',request_key) is None or type(expected_revision) is not int or expected_revision<0:
            raise CommunityStoreConflict('Invalid queue selection input')
        intent=digest({'domain':'community-queue-selection-v1','plan_id':plan_id,'expected_revision':expected_revision})
        with closing(self.intake.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                old=db.execute('SELECT id,intent_hash,plan_id,to_revision FROM community_queue_plan_events WHERE request_key=?',(request_key,)).fetchone()
                if old:
                    if old[1]!=intent:raise CommunityStoreConflict('Selection key belongs to another original intent')
                    current=db.execute('SELECT revision,plan_id FROM community_queue_current WHERE singleton=1').fetchone()
                    db.commit();return {'event_id':old[0],'plan_id':old[2],'committed_revision':old[3],'replayed':True,'current_revision':current[0],'current_plan_id':current[1],'execution_authorized':False}
                current=db.execute('SELECT revision FROM community_queue_current WHERE singleton=1').fetchone()[0]
                if current!=expected_revision:raise CommunityStoreConflict('Current queue selection changed')
                if plan_id is not None:
                    selected=self.view(db,plan_id)
                    if not all(selected['current_original_assessments'].values()) or not selected['feature_catalog_current']:
                        raise CommunityStoreConflict('Queue original assessment/source or feature catalog changed')
                identifier=str(uuid.uuid4());now=self.clock_us()
                if type(now) is not int or now<0:raise CommunityStoreConflict('Invalid queue clock')
                db.execute('INSERT INTO community_queue_plan_events VALUES(?,?,?,?,?,?,?)',(identifier,request_key,intent,plan_id,current,current+1,now))
                db.execute('UPDATE community_queue_current SET revision=?,plan_id=? WHERE singleton=1',(current+1,plan_id))
                db.commit();return {'event_id':identifier,'plan_id':plan_id,'committed_revision':current+1,'replayed':False,'current_revision':current+1,'current_plan_id':plan_id,'execution_authorized':False}
            except BaseException:db.rollback();raise

    def get_plan(self,identifier):
        """Read exact saved plan/current pointer with bounded original requirement freshness checks."""
        with closing(self.intake.connect()) as db:return self.view(db,identifier)
