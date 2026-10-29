"""Add immutable model-call ownership without altering existing quotes, balances or holds."""
from sqlalchemy import text

from app.models.model_charge import ModelChargeBatch,ModelCallCharge


async def prepare_model_charge_parent(connection):
    """Install the scoped parent index before any new child table or existing FK check is used."""
    exists=await connection.scalar(text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='execution_budget_entries'"))
    if exists:
        await connection.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS execution_budget_entry_scope ON execution_budget_entries(job_id,project_id)'))


async def migrate_model_charges(engine):
    """Enforce fixed batch/charge identities and one-way prepared-call evidence in SQLite."""
    if engine.dialect.name!='sqlite':
        raise RuntimeError('Project model charges currently require SQLite')
    async with engine.begin() as connection:
        await prepare_model_charge_parent(connection)
        await connection.run_sync(lambda sync: ModelChargeBatch.__table__.create(sync,checkfirst=True))
        await connection.run_sync(lambda sync: ModelCallCharge.__table__.create(sync,checkfirst=True))
        guards={}
        numeric_tables={
            'model_charge_batches':(('call_count',1,64),),
            'model_call_charges':(('provider_connection_version',0,9223372036854775806),),
            'execution_budgets':tuple((field,0,4611686018427387903) for field in ('limit_minor','reserved_minor','spent_minor','revision')),
            'execution_budget_entries':(('quote_minor',0,4611686018427387903),),
        }
        for table,fields in numeric_tables.items():
            invalid=' OR '.join(f"typeof(NEW.{field})!='integer' OR NEW.{field}<{minimum} OR NEW.{field}>{maximum}" for field,minimum,maximum in fields)
            if table=='execution_budget_entries':
                invalid+=" OR (NEW.actual_minor IS NOT NULL AND (typeof(NEW.actual_minor)!='integer' OR NEW.actual_minor<0 OR NEW.actual_minor>4611686018427387903))"
            for action in ('INSERT','UPDATE'):
                guards[table+'_integer_'+action.lower()]=f"BEFORE {action} ON {table} WHEN {invalid} BEGIN SELECT RAISE(ABORT,'Project accounting requires bounded integer units'); END"
        guards['model_charge_batch_no_replace']=("BEFORE INSERT ON model_charge_batches WHEN EXISTS(SELECT 1 FROM model_charge_batches "
            "WHERE id=NEW.id OR scope_key=NEW.scope_key) BEGIN SELECT RAISE(ABORT,'Model charge batch cannot be replaced'); END")
        guards['model_call_charge_no_replace']=("BEFORE INSERT ON model_call_charges WHEN EXISTS(SELECT 1 FROM model_call_charges "
            "WHERE id=NEW.id OR (batch_id=NEW.batch_id AND unit_key=NEW.unit_key)) BEGIN SELECT RAISE(ABORT,'Model call history cannot be replaced'); END")
        for action in ('UPDATE','DELETE'):
            guards['model_charge_batch_no_'+action.lower()]=f'BEFORE {action} ON model_charge_batches BEGIN SELECT RAISE(ABORT,\'Immutable model charge batch\'); END'
        guards['model_call_charge_no_delete']='BEFORE DELETE ON model_call_charges BEGIN SELECT RAISE(ABORT,\'Model call history is retained\'); END'
        fields=('id','project_id','batch_id','unit_key','kind','model_id','provider_id','provider_connection_version','account_version','binding_hash','price_snapshot')
        changed=' OR '.join('NEW.'+field+' IS NOT OLD.'+field for field in fields)
        guards['model_call_charge_identity']=f'BEFORE UPDATE ON model_call_charges WHEN {changed} BEGIN SELECT RAISE(ABORT,\'Model call identity is immutable\'); END'
        guards['model_call_charge_lifecycle']=("BEFORE UPDATE OF dispatch_state,provider_request_id ON model_call_charges WHEN "
            "NOT (NEW.dispatch_state=OLD.dispatch_state OR (OLD.dispatch_state='planned' AND NEW.dispatch_state IN ('prepared','not_sent')) "
            "OR (OLD.dispatch_state IN ('prepared','unknown') AND NEW.dispatch_state IN ('accepted','unknown'))) "
            "OR (OLD.provider_request_id IS NOT NULL AND NEW.provider_request_id IS NOT OLD.provider_request_id) "
            "OR (NEW.provider_request_id IS NOT NULL AND NEW.dispatch_state!='accepted') "
            "BEGIN SELECT RAISE(ABORT,'Model dispatch evidence cannot rewind'); END")
        for name,body in guards.items():
            expected=f'CREATE TRIGGER {name} {body}'
            actual=await connection.scalar(text("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=:name"),{'name':name})
            if actual is None:
                await connection.execute(text(expected))
            elif ' '.join(actual.split())!=' '.join(expected.split()):
                raise RuntimeError('Model charge guard differs from supported schema')
