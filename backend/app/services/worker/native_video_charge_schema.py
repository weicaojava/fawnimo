"""Add sealed native project billing contexts without editing old video journals or amounts."""
from sqlalchemy import text

from app.models.native_video_charge import NativeVideoChargeBatch, NativeVideoJobCharge


async def migrate_native_video_charges(engine):
    """Install exact scoped keys, whole-batch sealing and replacement protection before scanners start."""
    if engine.dialect.name!='sqlite':raise RuntimeError('Native project charges require SQLite')
    async with engine.begin() as connection:
        await connection.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS native_execution_job_scope ON execution_jobs(id,project_id)'))
        for table in (NativeVideoJobCharge.__table__,NativeVideoChargeBatch.__table__):
            await connection.run_sync(lambda sync,table=table:table.create(sync,checkfirst=True))
            columns={row[1] for row in (await connection.execute(text(f'PRAGMA table_info({table.name})'))).all()}
            if columns!={column.name for column in table.columns}:raise RuntimeError('Unsupported native billing table layout')
        guards={
            'native_batch_no_replace':"BEFORE INSERT ON native_video_charge_batches WHEN EXISTS(SELECT 1 FROM native_video_charge_batches WHERE id=NEW.id OR (project_id=NEW.project_id AND request_key=NEW.request_key)) BEGIN SELECT RAISE(ABORT,'Native batch cannot be replaced'); END",
            'native_job_charge_no_replace':"BEFORE INSERT ON native_video_job_charges WHEN EXISTS(SELECT 1 FROM native_video_job_charges WHERE job_id=NEW.job_id OR (batch_id=NEW.batch_id AND unit_key=NEW.unit_key)) BEGIN SELECT RAISE(ABORT,'Native job charge cannot be replaced'); END",
            'native_job_charge_no_append':"BEFORE INSERT ON native_video_job_charges WHEN EXISTS(SELECT 1 FROM native_video_charge_batches WHERE id=NEW.batch_id) BEGIN SELECT RAISE(ABORT,'Native plan is sealed'); END",
            'native_call_no_append':"BEFORE INSERT ON model_call_charges WHEN EXISTS(SELECT 1 FROM native_video_charge_batches WHERE id=NEW.batch_id) BEGIN SELECT RAISE(ABORT,'Native call plan is sealed'); END",
            'native_job_charge_context':"BEFORE INSERT ON native_video_job_charges WHEN NOT EXISTS(SELECT 1 FROM execution_jobs j JOIN execution_budget_entries e ON e.job_id=j.id AND e.project_id=j.project_id JOIN model_call_charges c ON c.id=j.id AND c.project_id=j.project_id JOIN execution_bindings b ON b.job_id=j.id WHERE j.id=NEW.job_id AND j.project_id=NEW.project_id AND j.request_hash=NEW.request_hash AND j.state='queued' AND e.state='reserved' AND c.dispatch_state='planned' AND c.kind='native_video' AND c.batch_id=NEW.batch_id AND c.unit_key=NEW.unit_key AND b.binding_hash=NEW.execution_binding_hash AND NOT EXISTS(SELECT 1 FROM submission_attempts a WHERE a.job_id=j.id)) BEGIN SELECT RAISE(ABORT,'Native job charge requires its original unsent job and hold'); END",
            'native_batch_complete':"BEFORE INSERT ON native_video_charge_batches WHEN NOT EXISTS(SELECT 1 FROM model_charge_batches b WHERE b.id=NEW.id AND b.project_id=NEW.project_id AND b.submitted_by=NEW.submitted_by AND b.scope_key='native-video:'||b.id AND b.call_count=json_array_length(NEW.context,'$.units') AND b.call_count=(SELECT count(*) FROM native_video_job_charges l WHERE l.batch_id=b.id AND l.project_id=b.project_id) AND b.call_count=(SELECT count(*) FROM model_call_charges c WHERE c.batch_id=b.id AND c.project_id=b.project_id)) BEGIN SELECT RAISE(ABORT,'Complete native bill plan required'); END",
            'native_attempt_no_replace':"BEFORE INSERT ON submission_attempts WHEN EXISTS(SELECT 1 FROM submission_attempts a JOIN native_video_job_charges n ON n.job_id=a.job_id WHERE a.id=NEW.id OR a.job_id=NEW.job_id) BEGIN SELECT RAISE(ABORT,'Priced native attempt cannot be replaced'); END",
            'native_attempt_prepared_context':"BEFORE INSERT ON submission_attempts WHEN EXISTS(SELECT 1 FROM native_video_job_charges n WHERE n.job_id=NEW.job_id) AND NOT EXISTS(SELECT 1 FROM execution_jobs j JOIN model_call_charges c ON c.id=j.id AND c.project_id=j.project_id JOIN execution_budget_entries e ON e.job_id=j.id AND e.project_id=j.project_id WHERE j.id=NEW.job_id AND j.request_hash=NEW.request_hash AND j.state='submitting' AND c.kind='native_video' AND c.dispatch_state='prepared' AND e.state='unknown' AND NEW.state='prepared' AND NEW.remote_id IS NULL) BEGIN SELECT RAISE(ABORT,'Native attempt requires its prepared common hold'); END",
            'native_attempt_lifecycle':"BEFORE UPDATE OF state,remote_id ON submission_attempts WHEN EXISTS(SELECT 1 FROM native_video_job_charges n WHERE n.job_id=OLD.job_id) AND (NOT (NEW.state=OLD.state OR (OLD.state='prepared' AND NEW.state IN ('accepted','rejected'))) OR (OLD.remote_id IS NOT NULL AND NEW.remote_id IS NOT OLD.remote_id) OR (NEW.remote_id IS NOT NULL AND NEW.state!='accepted')) BEGIN SELECT RAISE(ABORT,'Native submission evidence cannot rewind'); END",
        }
        for table in ('native_video_charge_batches','native_video_job_charges'):
            for action in ('UPDATE','DELETE'):
                guards[table+'_no_'+action.lower()]=f"BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Native bill identity is immutable'); END"
        identities={
            'execution_jobs':"EXISTS(SELECT 1 FROM execution_jobs j JOIN native_video_job_charges n ON n.job_id=j.id WHERE j.id=NEW.id OR (j.project_id=NEW.project_id AND j.request_key=NEW.request_key))",
            'execution_bindings':"EXISTS(SELECT 1 FROM native_video_job_charges n WHERE n.job_id=NEW.job_id)",
            'execution_budget_entries':"EXISTS(SELECT 1 FROM native_video_job_charges n WHERE n.job_id=NEW.job_id)",
        }
        for table,predicate in identities.items():
            guards['native_'+table+'_no_replace']=f"BEFORE INSERT ON {table} WHEN {predicate} BEGIN SELECT RAISE(ABORT,'Priced native history cannot be replaced'); END"
        for name,body in guards.items():
            expected=f'CREATE TRIGGER {name} {body}'
            actual=await connection.scalar(text("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=:name"),{'name':name})
            if actual is None:await connection.execute(text(expected))
            elif ' '.join(actual.split())!=' '.join(expected.split()):raise RuntimeError('Native billing guard differs from supported schema')
