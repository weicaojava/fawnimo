"""Additive exact-schema Wan image input guards; no Seedance history or table definition is rewritten."""
from sqlalchemy import text
from sqlalchemy.dialects.sqlite import dialect
from sqlalchemy.schema import CreateTable
from app.core.db import Base
from app.models.wan30_reference_inputs import Wan30ReferenceInputSet,Wan30ReferenceInput


def guard_definitions():
    """Pin complete original-job/price/account joins, one seal and immutable image rows with a separate fixed namespace."""
    fixed=['job_id','project_id','protocol','mode','reference_count','reference_hash','selection_hash','request_hash','snapshot_hash','binding_hash','connection_hash','profile_hash']
    changed=' OR '.join('NEW.'+name+' IS NOT OLD.'+name for name in fixed)
    guards = {
        'wan30_reference_set_initial': """BEFORE INSERT ON wan30_reference_input_sets WHEN NEW.sealed!=0 OR NOT EXISTS(
            SELECT 1 FROM execution_jobs j JOIN native_video_job_charges f ON f.job_id=j.id AND f.project_id=j.project_id
            JOIN native_video_charge_batches b ON b.id=f.batch_id AND b.project_id=f.project_id
            JOIN execution_bindings e ON e.job_id=j.id JOIN model_call_charges c ON c.id=j.id AND c.project_id=j.project_id
            WHERE j.id=NEW.job_id AND j.project_id=NEW.project_id AND j.state='queued' AND c.dispatch_state='planned'
            AND j.request_hash=NEW.request_hash AND f.request_hash=NEW.request_hash AND f.snapshot_hash=NEW.snapshot_hash
            AND f.execution_binding_hash=NEW.binding_hash AND e.binding_hash=NEW.binding_hash AND c.binding_hash=NEW.connection_hash
            AND json_extract(c.price_snapshot,'$.input_protocol')=NEW.protocol
            AND json_extract(b.context,'$.connection.protocol')=NEW.protocol
            AND json_extract(b.context,'$.profile.profile_hash')=NEW.profile_hash
            AND json_extract(j.snapshot,'$.video_reference.schema_version')=1
            AND json_extract(j.snapshot,'$.video_reference.mode')=NEW.mode
            AND json_array_length(j.snapshot,'$.video_reference.references')=NEW.reference_count)
            BEGIN SELECT RAISE(ABORT,'Invalid native reference parent'); END""",
        'wan30_reference_set_no_replace': """BEFORE INSERT ON wan30_reference_input_sets WHEN EXISTS(
            SELECT 1 FROM wan30_reference_input_sets WHERE job_id=NEW.job_id)
            BEGIN SELECT RAISE(ABORT,'Native reference set replacement rejected'); END""",
        'wan30_reference_set_no_delete': """BEFORE DELETE ON wan30_reference_input_sets
            BEGIN SELECT RAISE(ABORT,'Native reference history is retained'); END""",
        'wan30_reference_set_seal': f"""BEFORE UPDATE ON wan30_reference_input_sets WHEN OLD.sealed!=0 OR NEW.sealed!=1 OR {changed}
            OR NOT EXISTS(SELECT 1 FROM execution_jobs WHERE id=OLD.job_id AND state='queued')
            OR (SELECT count(*) FROM wan30_reference_inputs WHERE job_id=OLD.job_id)!=OLD.reference_count
            OR (SELECT min(position) FROM wan30_reference_inputs WHERE job_id=OLD.job_id)!=0
            OR (SELECT max(position) FROM wan30_reference_inputs WHERE job_id=OLD.job_id)!=OLD.reference_count-1
            OR (OLD.mode='first_frame' AND (OLD.reference_count!=1 OR NOT EXISTS(
                SELECT 1 FROM wan30_reference_inputs WHERE job_id=OLD.job_id AND position=0 AND role='first_frame')))
            OR (OLD.mode='first_last_frames' AND (OLD.reference_count!=2 OR NOT EXISTS(
                SELECT 1 FROM wan30_reference_inputs WHERE job_id=OLD.job_id AND position=0 AND role='first_frame') OR NOT EXISTS(
                SELECT 1 FROM wan30_reference_inputs WHERE job_id=OLD.job_id AND position=1 AND role='last_frame')))
            OR (OLD.mode='reference' AND (EXISTS(SELECT 1 FROM wan30_reference_inputs WHERE job_id=OLD.job_id AND role NOT IN ('reference_image','reference_video'))
                OR (SELECT count(*) FROM wan30_reference_inputs WHERE job_id=OLD.job_id AND role='reference_image')>3
                OR (SELECT count(*) FROM wan30_reference_inputs WHERE job_id=OLD.job_id AND role='reference_video')>1))
            BEGIN SELECT RAISE(ABORT,'Invalid complete native reference set'); END""",
        'wan30_reference_item_insert': """BEFORE INSERT ON wan30_reference_inputs WHEN NOT EXISTS(
            SELECT 1 FROM wan30_reference_input_sets h JOIN execution_jobs j ON j.id=h.job_id AND j.project_id=h.project_id
            WHERE h.job_id=NEW.job_id AND h.project_id=NEW.project_id AND h.sealed=0 AND j.state='queued' AND NEW.position<h.reference_count
            AND json_extract(j.snapshot,'$.video_reference.references['||NEW.position||'].media_id')=NEW.media_id
            AND json_extract(j.snapshot,'$.video_reference.references['||NEW.position||'].expected_sha256')=NEW.sha256
            AND json_extract(j.snapshot,'$.video_reference.references['||NEW.position||'].role')=NEW.role)
            OR NOT EXISTS(SELECT 1 FROM production_media m WHERE m.id=NEW.media_id AND m.project_id=NEW.project_id
                AND m.sha256=NEW.sha256 AND m.size=NEW.byte_size AND m.mime=NEW.mime AND m.relative_path=NEW.relative_path)
            OR EXISTS(SELECT 1 FROM wan30_reference_inputs i JOIN wan30_reference_input_sets h ON h.job_id=i.job_id
                WHERE i.job_id=NEW.job_id AND i.media_id=NEW.media_id AND h.mode!='first_last_frames')
            BEGIN SELECT RAISE(ABORT,'Invalid ordered original-project native input'); END""",
        'wan30_reference_item_no_replace': """BEFORE INSERT ON wan30_reference_inputs WHEN EXISTS(
            SELECT 1 FROM wan30_reference_inputs WHERE job_id=NEW.job_id AND position=NEW.position)
            BEGIN SELECT RAISE(ABORT,'Native input replacement rejected'); END""",
        'wan30_reference_item_no_update': """BEFORE UPDATE ON wan30_reference_inputs
            BEGIN SELECT RAISE(ABORT,'Native reference inputs are immutable'); END""",
        'wan30_reference_item_no_delete': """BEFORE DELETE ON wan30_reference_inputs
            BEGIN SELECT RAISE(ABORT,'Native reference input history is retained'); END""",
    }
    return guards


def normalized_sql(value):
    """Compare exact trusted declarations while ignoring formatting only."""
    return ' '.join(value.strip().rstrip(';').split())


async def require_wan30_input_schema(executor):
    """A missing or redirected schema/trigger cannot authorize sealing or replay of a new protocol's input proof."""
    for model in (Wan30ReferenceInputSet,Wan30ReferenceInput):
        previous=(await executor.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:name"),{'name':model.__tablename__})).scalar()
        expected=str(CreateTable(model.__table__).compile(dialect=dialect()))
        if not isinstance(previous,str) or normalized_sql(previous)!=normalized_sql(expected):
            raise RuntimeError('Wan reference schema differs from trusted declaration')
    for name,body in guard_definitions().items():
        previous=(await executor.execute(text("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=:name"),{'name':name})).scalar()
        if not isinstance(previous,str) or normalized_sql(previous)!=normalized_sql('CREATE TRIGGER '+name+' '+body):
            raise RuntimeError('Wan reference guard differs from trusted declaration')


async def migrate_wan30_reference_inputs(engine):
    """Create only the two additive empty Wan tables, refuse changed existing declarations, and finish all guards before use."""
    if engine.dialect.name!='sqlite':return
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync:Base.metadata.create_all(sync,tables=[Wan30ReferenceInputSet.__table__,Wan30ReferenceInput.__table__]))
        for model in (Wan30ReferenceInputSet,Wan30ReferenceInput):
            previous=(await connection.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:name"),{'name':model.__tablename__})).scalar()
            if normalized_sql(previous)!=normalized_sql(str(CreateTable(model.__table__).compile(dialect=dialect()))):
                raise RuntimeError('Wan reference schema changed; migration refused')
        for name,body in guard_definitions().items():
            expected='CREATE TRIGGER '+name+' '+body
            previous=(await connection.execute(text("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=:name"),{'name':name})).scalar()
            if previous is None:await connection.execute(text(expected))
            elif normalized_sql(previous)!=normalized_sql(expected):raise RuntimeError('Wan reference guard changed; migration refused')
        await require_wan30_input_schema(connection)
