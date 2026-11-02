"""Add complete original-project reference sets without rewriting old native execution or billing records."""
from sqlalchemy import text


async def migrate_video_reference_inputs(engine):
    """Check immutable identities, dense roles and the actual original financial parent inside SQLite."""
    if engine.dialect.name != 'sqlite':
        return
    fixed = ['job_id', 'project_id', 'protocol', 'mode', 'reference_count', 'reference_hash', 'selection_hash',
             'request_hash', 'snapshot_hash', 'binding_hash', 'connection_hash', 'profile_hash']
    changed = ' OR '.join('NEW.'+name+' IS NOT OLD.'+name for name in fixed)
    guards = {
        'video_reference_set_initial': """BEFORE INSERT ON video_reference_input_sets WHEN NEW.sealed!=0 OR NOT EXISTS(
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
        'video_reference_set_no_replace': """BEFORE INSERT ON video_reference_input_sets WHEN EXISTS(
            SELECT 1 FROM video_reference_input_sets WHERE job_id=NEW.job_id)
            BEGIN SELECT RAISE(ABORT,'Native reference set replacement rejected'); END""",
        'video_reference_set_no_delete': """BEFORE DELETE ON video_reference_input_sets
            BEGIN SELECT RAISE(ABORT,'Native reference history is retained'); END""",
        'video_reference_set_seal': f"""BEFORE UPDATE ON video_reference_input_sets WHEN OLD.sealed!=0 OR NEW.sealed!=1 OR {changed}
            OR NOT EXISTS(SELECT 1 FROM execution_jobs WHERE id=OLD.job_id AND state='queued')
            OR (SELECT count(*) FROM video_reference_inputs WHERE job_id=OLD.job_id)!=OLD.reference_count
            OR (SELECT min(position) FROM video_reference_inputs WHERE job_id=OLD.job_id)!=0
            OR (SELECT max(position) FROM video_reference_inputs WHERE job_id=OLD.job_id)!=OLD.reference_count-1
            OR (OLD.mode='first_frame' AND (OLD.reference_count!=1 OR NOT EXISTS(
                SELECT 1 FROM video_reference_inputs WHERE job_id=OLD.job_id AND position=0 AND role='first_frame')))
            OR (OLD.mode='first_last_frames' AND (OLD.reference_count!=2 OR NOT EXISTS(
                SELECT 1 FROM video_reference_inputs WHERE job_id=OLD.job_id AND position=0 AND role='first_frame') OR NOT EXISTS(
                SELECT 1 FROM video_reference_inputs WHERE job_id=OLD.job_id AND position=1 AND role='last_frame')))
            OR (OLD.mode='reference' AND (EXISTS(SELECT 1 FROM video_reference_inputs WHERE job_id=OLD.job_id AND role NOT IN ('reference_image','reference_video'))
                OR (SELECT count(*) FROM video_reference_inputs WHERE job_id=OLD.job_id AND role='reference_image')>3
                OR (SELECT count(*) FROM video_reference_inputs WHERE job_id=OLD.job_id AND role='reference_video')>1))
            BEGIN SELECT RAISE(ABORT,'Invalid complete native reference set'); END""",
        'video_reference_item_insert': """BEFORE INSERT ON video_reference_inputs WHEN NOT EXISTS(
            SELECT 1 FROM video_reference_input_sets h JOIN execution_jobs j ON j.id=h.job_id AND j.project_id=h.project_id
            WHERE h.job_id=NEW.job_id AND h.project_id=NEW.project_id AND h.sealed=0 AND j.state='queued' AND NEW.position<h.reference_count
            AND json_extract(j.snapshot,'$.video_reference.references['||NEW.position||'].media_id')=NEW.media_id
            AND json_extract(j.snapshot,'$.video_reference.references['||NEW.position||'].expected_sha256')=NEW.sha256
            AND json_extract(j.snapshot,'$.video_reference.references['||NEW.position||'].role')=NEW.role)
            OR NOT EXISTS(SELECT 1 FROM production_media m WHERE m.id=NEW.media_id AND m.project_id=NEW.project_id
                AND m.sha256=NEW.sha256 AND m.size=NEW.byte_size AND m.mime=NEW.mime AND m.relative_path=NEW.relative_path)
            OR EXISTS(SELECT 1 FROM video_reference_inputs i JOIN video_reference_input_sets h ON h.job_id=i.job_id
                WHERE i.job_id=NEW.job_id AND i.media_id=NEW.media_id AND h.mode!='first_last_frames')
            BEGIN SELECT RAISE(ABORT,'Invalid ordered original-project native input'); END""",
        'video_reference_item_no_replace': """BEFORE INSERT ON video_reference_inputs WHEN EXISTS(
            SELECT 1 FROM video_reference_inputs WHERE job_id=NEW.job_id AND position=NEW.position)
            BEGIN SELECT RAISE(ABORT,'Native input replacement rejected'); END""",
        'video_reference_item_no_update': """BEFORE UPDATE ON video_reference_inputs
            BEGIN SELECT RAISE(ABORT,'Native reference inputs are immutable'); END""",
        'video_reference_item_no_delete': """BEFORE DELETE ON video_reference_inputs
            BEGIN SELECT RAISE(ABORT,'Native reference input history is retained'); END""",
    }
    delivery_fixed = ['id', 'job_id', 'position', 'project_id', 'attempt_id', 'token_sha256', 'created_at', 'expires_at']
    delivery_changed = ' OR '.join('NEW.'+name+' IS NOT OLD.'+name for name in delivery_fixed)
    guards.update({
        'execution_reference_delivery_initial': """BEFORE INSERT ON execution_reference_deliveries WHEN NEW.access_count!=0 OR NOT EXISTS(
            SELECT 1 FROM video_reference_input_sets h JOIN video_reference_inputs i ON i.job_id=h.job_id AND i.project_id=h.project_id
            JOIN execution_jobs j ON j.id=h.job_id AND j.project_id=h.project_id
            JOIN submission_attempts a ON a.job_id=j.id JOIN model_call_charges c ON c.id=j.id AND c.project_id=j.project_id
            WHERE h.job_id=NEW.job_id AND h.project_id=NEW.project_id AND h.sealed=1 AND h.protocol='seedance25_reference_v1'
            AND i.position=NEW.position AND i.role='reference_video' AND j.state='submitting' AND j.cancel_requested=0
            AND j.lease_until>NEW.created_at AND a.id=NEW.attempt_id AND a.state='prepared'
            AND c.dispatch_state='prepared')
            BEGIN SELECT RAISE(ABORT,'Delivery needs its original prepared attempt'); END""",
        'execution_reference_delivery_no_replace': """BEFORE INSERT ON execution_reference_deliveries WHEN EXISTS(
            SELECT 1 FROM execution_reference_deliveries WHERE id=NEW.id OR token_sha256=NEW.token_sha256
                OR (attempt_id=NEW.attempt_id AND position=NEW.position))
            BEGIN SELECT RAISE(ABORT,'Reference delivery replacement rejected'); END""",
        'execution_reference_delivery_no_delete': """BEFORE DELETE ON execution_reference_deliveries
            BEGIN SELECT RAISE(ABORT,'Reference delivery history is retained'); END""",
        'execution_reference_delivery_access': f"""BEFORE UPDATE ON execution_reference_deliveries WHEN {delivery_changed}
            OR NEW.access_count!=OLD.access_count+1 OR NEW.access_count>16
            BEGIN SELECT RAISE(ABORT,'Invalid bounded delivery access'); END""",
    })
    async with engine.begin() as connection:
        for name, body in guards.items():
            expected = f'CREATE TRIGGER {name} {body}'
            previous = (await connection.execute(text('SELECT sql FROM sqlite_master WHERE type=\'trigger\' AND name=:name'), {'name': name})).scalar()
            if previous is None:
                await connection.execute(text(expected))
            elif ' '.join(previous.strip().rstrip(';').split()) != ' '.join(expected.strip().rstrip(';').split()):
                raise RuntimeError('Native reference guard changed; migration refused')
