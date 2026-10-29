"""Retain original job/project/batch ownership without changing any historical business attempt."""
from sqlalchemy import text

from app.models.model_job_charge import ModelJobCharge


async def migrate_model_job_charges(engine):
    """Add fixed linkage and prevent replacement or reassigning an admitted batch to another job."""
    if engine.dialect.name!='sqlite':
        raise RuntimeError('Project-bound model jobs currently require SQLite')
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync:ModelJobCharge.__table__.create(sync,checkfirst=True))
        guards={
            'model_job_charge_no_replace':"BEFORE INSERT ON model_job_charges WHEN EXISTS(SELECT 1 FROM model_job_charges WHERE (job_kind=NEW.job_kind AND job_id=NEW.job_id) OR batch_id=NEW.batch_id) BEGIN SELECT RAISE(ABORT,'Model job charge cannot be replaced'); END",
            'model_job_charge_batch_context':"BEFORE INSERT ON model_job_charges WHEN NOT EXISTS(SELECT 1 FROM model_charge_batches b WHERE b.id=NEW.batch_id AND b.project_id=NEW.project_id AND b.submitted_by=NEW.submitted_by AND b.scope_key='model-job:'||NEW.job_kind||':'||NEW.job_id AND b.call_count=(SELECT count(*) FROM model_call_charges c WHERE c.batch_id=b.id AND c.project_id=b.project_id)) BEGIN SELECT RAISE(ABORT,'Model job batch context mismatch'); END",
            'model_job_charge_attempt_context':"BEFORE INSERT ON model_job_charges WHEN NOT ((NEW.job_kind='agent' AND EXISTS(SELECT 1 FROM agent_execution_runs j WHERE j.id=NEW.job_id AND j.request_hash=NEW.request_hash AND j.status='queued')) OR (NEW.job_kind='image' AND EXISTS(SELECT 1 FROM asset_image_generation_jobs j WHERE j.id=NEW.job_id AND j.request_hash=NEW.request_hash AND j.status='queued'))) BEGIN SELECT RAISE(ABORT,'Model job attempt context mismatch'); END",
        }
        agent_plan=("EXISTS(SELECT 1 FROM model_charge_batches b WHERE b.id=NEW.batch_id "
            "AND b.call_count=(SELECT count(*) FROM agent_execution_nodes n WHERE n.run_id=NEW.job_id AND n.type='llm') "
            "AND NOT EXISTS(SELECT 1 FROM agent_execution_nodes n WHERE n.run_id=NEW.job_id AND n.type='llm' "
            "AND NOT EXISTS(SELECT 1 FROM model_call_charges c WHERE c.batch_id=b.id AND c.unit_key=n.node_id)))")
        image_plan="EXISTS(SELECT 1 FROM model_charge_batches b JOIN model_call_charges c ON c.batch_id=b.id WHERE b.id=NEW.batch_id AND b.call_count=1 AND c.unit_key='candidate')"
        guards['model_job_charge_whole_plan']=(f"BEFORE INSERT ON model_job_charges WHEN (NEW.job_kind='agent' AND NOT {agent_plan}) "
            f"OR (NEW.job_kind='image' AND NOT {image_plan}) BEGIN SELECT RAISE(ABORT,'Complete model job plan required'); END")
        guards['priced_call_plan_no_insert']="BEFORE INSERT ON model_call_charges WHEN EXISTS(SELECT 1 FROM model_job_charges b WHERE b.batch_id=NEW.batch_id) BEGIN SELECT RAISE(ABORT,'Priced model call plan is complete'); END"
        for action in ('UPDATE','DELETE'):
            guards['model_job_charge_no_'+action.lower()]=f"BEFORE {action} ON model_job_charges BEGIN SELECT RAISE(ABORT,'Model job project and batch are immutable'); END"
        fields={
            'agent':('agent_execution_runs',('id','kind','scope_key','request_key','request_hash','agent_id','agent_name','agent_version','config_snapshot','model_id','model_name','model_binding','input_text','max_tokens','attempt','retry_of','created_at')),
            'image':('asset_image_generation_jobs',('id','scope_key','request_key','request_hash','asset_kind','asset_id','slot_id','model_id','model_name','model_binding','prompt','size','attempt','retry_of','created_at')),
        }
        for kind,(table,identity) in fields.items():
            bound=f"EXISTS(SELECT 1 FROM model_job_charges b WHERE b.job_kind='{kind}' AND b.job_id=OLD.id)"
            changed=' OR '.join('NEW.'+field+' IS NOT OLD.'+field for field in identity)
            guards['priced_'+kind+'_attempt_identity']=f"BEFORE UPDATE ON {table} WHEN {bound} AND ({changed}) BEGIN SELECT RAISE(ABORT,'Priced model attempt identity is immutable'); END"
            guards['priced_'+kind+'_attempt_no_delete']=f"BEFORE DELETE ON {table} WHEN {bound} BEGIN SELECT RAISE(ABORT,'Priced model attempt history is retained'); END"
            conflict=(f"EXISTS(SELECT 1 FROM {table} j JOIN model_job_charges b ON b.job_kind='{kind}' AND b.job_id=j.id "
                "WHERE j.id=NEW.id OR (j.scope_key=NEW.scope_key AND j.request_key=NEW.request_key))")
            guards['priced_'+kind+'_attempt_no_replace']=f"BEFORE INSERT ON {table} WHEN {conflict} BEGIN SELECT RAISE(ABORT,'Priced model attempt cannot be replaced'); END"
        bound="EXISTS(SELECT 1 FROM model_job_charges b WHERE b.job_kind='agent' AND b.job_id=OLD.run_id)"
        changed=' OR '.join('NEW.'+field+' IS NOT OLD.'+field for field in ('id','run_id','node_id','position','label','type'))
        guards['priced_agent_node_identity']=f"BEFORE UPDATE ON agent_execution_nodes WHEN {bound} AND ({changed}) BEGIN SELECT RAISE(ABORT,'Priced model node identity is immutable'); END"
        guards['priced_agent_node_no_delete']=f"BEFORE DELETE ON agent_execution_nodes WHEN {bound} BEGIN SELECT RAISE(ABORT,'Priced model node history is retained'); END"
        conflict=("EXISTS(SELECT 1 FROM model_job_charges b WHERE b.job_kind='agent' AND b.job_id=NEW.run_id) OR "
            "EXISTS(SELECT 1 FROM agent_execution_nodes n JOIN model_job_charges b ON b.job_kind='agent' AND b.job_id=n.run_id WHERE n.id=NEW.id)")
        guards['priced_agent_node_no_insert']=f"BEFORE INSERT ON agent_execution_nodes WHEN {conflict} BEGIN SELECT RAISE(ABORT,'Priced model node plan is complete'); END"
        for name,body in guards.items():
            expected=f'CREATE TRIGGER {name} {body}'
            actual=await connection.scalar(text("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=:name"),{'name':name})
            if actual is None:
                await connection.execute(text(expected))
            elif ' '.join(actual.split())!=' '.join(expected.split()):
                raise RuntimeError('Model job charge ownership guard differs from supported schema')
