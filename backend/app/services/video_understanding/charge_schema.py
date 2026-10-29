"""Protect complete per-attempt vision/ASR plans without modifying historical understanding journals."""
from sqlalchemy import text

from app.models.understanding_charge import UnderstandingAttemptCharge


async def migrate_understanding_charges(engine):
    """Install additive SQLite context, whole-plan, replacement and immutability guards before workers start."""
    if engine.dialect.name!='sqlite':
        raise RuntimeError('Understanding project charges currently require SQLite')
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync:UnderstandingAttemptCharge.__table__.create(sync,checkfirst=True))
        columns={row[1] for row in (await connection.execute(text('PRAGMA table_info(understanding_attempt_charges)'))).all()}
        if columns!={column.name for column in UnderstandingAttemptCharge.__table__.columns}:
            raise RuntimeError('Unsupported understanding charge table layout')
        guards={
            'understanding_charge_integer':"BEFORE INSERT ON understanding_attempt_charges WHEN typeof(NEW.attempt)!='integer' OR NEW.attempt<1 OR NEW.attempt>2147483647 BEGIN SELECT RAISE(ABORT,'Understanding charge attempt requires bounded integer'); END",
            'understanding_charge_no_replace':"BEFORE INSERT ON understanding_attempt_charges WHEN EXISTS(SELECT 1 FROM understanding_attempt_charges b WHERE (b.job_id=NEW.job_id AND b.attempt=NEW.attempt) OR b.batch_id=NEW.batch_id OR (b.job_id=NEW.job_id AND b.request_key=NEW.request_key)) BEGIN SELECT RAISE(ABORT,'Understanding charge identity cannot be replaced'); END",
            'understanding_charge_context':"BEFORE INSERT ON understanding_attempt_charges WHEN NOT EXISTS(SELECT 1 FROM video_understanding_jobs j JOIN video_understanding_attempts a ON a.job_id=j.id AND a.attempt=NEW.attempt AND a.project_id=j.project_id JOIN model_charge_batches b ON b.id=NEW.batch_id AND b.project_id=j.project_id WHERE j.id=NEW.job_id AND j.project_id=NEW.project_id AND j.attempt=NEW.attempt AND j.request_hash=NEW.job_request_hash AND j.state='queued' AND a.state='queued' AND b.submitted_by=NEW.submitted_by AND b.scope_key='model-job:understanding:'||j.id||':'||NEW.attempt AND b.call_count=(SELECT count(*) FROM model_call_charges c WHERE c.batch_id=b.id AND c.project_id=b.project_id)) BEGIN SELECT RAISE(ABORT,'Understanding charge attempt context mismatch'); END",
            'understanding_call_plan_no_append':"BEFORE INSERT ON model_call_charges WHEN EXISTS(SELECT 1 FROM understanding_attempt_charges b WHERE b.batch_id=NEW.batch_id) BEGIN SELECT RAISE(ABORT,'Understanding call plan is complete'); END",
        }
        needs_audio="(json_extract(j.connection,'$.transcribe_audio')=1 AND json_extract(j.source,'$.audio_present')=1)"
        guards['understanding_charge_whole_plan']=("BEFORE INSERT ON understanding_attempt_charges WHEN NOT EXISTS(SELECT 1 FROM video_understanding_jobs j JOIN model_charge_batches b ON b.id=NEW.batch_id "
            f"WHERE j.id=NEW.job_id AND b.call_count=1+CASE WHEN {needs_audio} THEN 1 ELSE 0 END "
            "AND EXISTS(SELECT 1 FROM model_call_charges c WHERE c.batch_id=b.id AND c.unit_key='vision' AND c.kind='video_vision') "
            f"AND (NOT {needs_audio} OR EXISTS(SELECT 1 FROM model_call_charges c WHERE c.batch_id=b.id AND c.unit_key='asr' AND c.kind='video_asr'))) "
            "BEGIN SELECT RAISE(ABORT,'Complete understanding model plan required'); END")
        for action in ('UPDATE','DELETE'):
            guards['understanding_charge_no_'+action.lower()]=f"BEFORE {action} ON understanding_attempt_charges BEGIN SELECT RAISE(ABORT,'Understanding project charge is immutable'); END"
        guards['priced_understanding_job_no_replace']=("BEFORE INSERT ON video_understanding_jobs WHEN EXISTS(SELECT 1 FROM video_understanding_jobs j JOIN understanding_attempt_charges b ON b.job_id=j.id "
            "WHERE j.id=NEW.id OR (j.project_id=NEW.project_id AND j.request_key=NEW.request_key)) BEGIN SELECT RAISE(ABORT,'Priced understanding job cannot be replaced'); END")
        guards['priced_understanding_attempt_no_replace']=("BEFORE INSERT ON video_understanding_attempts WHEN EXISTS(SELECT 1 FROM video_understanding_attempts a JOIN understanding_attempt_charges b ON b.job_id=a.job_id AND b.attempt=a.attempt "
            "WHERE a.id=NEW.id OR (a.job_id=NEW.job_id AND a.attempt=NEW.attempt)) BEGIN SELECT RAISE(ABORT,'Priced understanding attempt cannot be replaced'); END")
        for name,body in guards.items():
            expected=f'CREATE TRIGGER {name} {body}'
            actual=await connection.scalar(text("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=:name"),{'name':name})
            if actual is None:
                await connection.execute(text(expected))
            elif ' '.join(actual.split())!=' '.join(expected.split()):
                raise RuntimeError('Understanding charge guard differs from supported schema')
