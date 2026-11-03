"""Add exact GPT Image sealing/ownership/history guards without rebuilding or weakening historical Seedream tables/triggers."""
from sqlalchemy import text


async def migrate_gpt_image_inputs(engine):
    """Require original queued project reservation and immutable dense input proofs; repeat initialization checks exact trusted trigger definitions."""
    if engine.dialect.name!='sqlite':return
    guards={
        'gpt_image_set_initial':"""BEFORE INSERT ON gpt_image_input_sets WHEN NEW.sealed!=0 OR NOT EXISTS(
            SELECT 1 FROM asset_image_generation_jobs j JOIN model_job_charges f ON f.job_id=j.id AND f.job_kind='image'
            WHERE j.id=NEW.job_id AND j.status='queued' AND f.project_id=NEW.project_id
            AND j.request_hash=NEW.request_hash AND f.request_hash=NEW.request_hash AND j.model_name=NEW.model_name AND j.size=NEW.size
            AND json_extract(j.model_binding,'$.protocol')=NEW.protocol AND json_extract(j.model_binding,'$.gpt_image_profile')=NEW.profile)
            BEGIN SELECT RAISE(ABORT,'Invalid GPT Image input set identity'); END""",
        'gpt_image_set_no_replace':"""BEFORE INSERT ON gpt_image_input_sets WHEN EXISTS(SELECT 1 FROM gpt_image_input_sets WHERE job_id=NEW.job_id)
            BEGIN SELECT RAISE(ABORT,'GPT Image input replacement rejected'); END""",
        'gpt_image_set_no_delete':"""BEFORE DELETE ON gpt_image_input_sets
            BEGIN SELECT RAISE(ABORT,'GPT Image input history is retained'); END""",
        'gpt_image_set_seal':"""BEFORE UPDATE ON gpt_image_input_sets WHEN OLD.sealed!=0 OR NEW.sealed!=1
            OR NEW.job_id IS NOT OLD.job_id OR NEW.project_id IS NOT OLD.project_id
            OR NEW.protocol IS NOT OLD.protocol OR NEW.profile IS NOT OLD.profile OR NEW.model_name IS NOT OLD.model_name OR NEW.size IS NOT OLD.size
            OR NEW.response_format IS NOT OLD.response_format OR NEW.reference_count IS NOT OLD.reference_count
            OR NEW.reference_hash IS NOT OLD.reference_hash OR NEW.request_hash IS NOT OLD.request_hash OR NEW.binding_hash IS NOT OLD.binding_hash
            OR (SELECT count(*) FROM gpt_image_references WHERE job_id=OLD.job_id)!=OLD.reference_count
            OR NOT EXISTS(SELECT 1 FROM asset_image_generation_jobs WHERE id=OLD.job_id AND status='queued')
            BEGIN SELECT RAISE(ABORT,'Invalid GPT Image input sealing'); END""",
        'gpt_image_reference_insert':"""BEFORE INSERT ON gpt_image_references WHEN NOT EXISTS(
            SELECT 1 FROM gpt_image_input_sets h JOIN asset_image_generation_jobs j ON j.id=h.job_id
            WHERE h.job_id=NEW.job_id AND h.project_id=NEW.project_id AND h.sealed=0 AND NEW.position<h.reference_count AND j.status='queued')
            OR NOT EXISTS(SELECT 1 FROM production_media m WHERE m.id=NEW.media_id AND m.project_id=NEW.project_id
            AND m.sha256=NEW.sha256 AND m.size=NEW.byte_size AND m.mime=NEW.mime AND m.relative_path=NEW.relative_path)
            BEGIN SELECT RAISE(ABORT,'Invalid original-project GPT Image reference'); END""",
        'gpt_image_reference_no_replace':"""BEFORE INSERT ON gpt_image_references WHEN EXISTS(
            SELECT 1 FROM gpt_image_references WHERE job_id=NEW.job_id AND (position=NEW.position OR media_id=NEW.media_id))
            BEGIN SELECT RAISE(ABORT,'GPT Image reference replacement rejected'); END""",
        'gpt_image_reference_no_update':"""BEFORE UPDATE ON gpt_image_references
            BEGIN SELECT RAISE(ABORT,'GPT Image references are immutable'); END""",
        'gpt_image_reference_no_delete':"""BEFORE DELETE ON gpt_image_references
            BEGIN SELECT RAISE(ABORT,'GPT Image reference history is retained'); END""",
    }
    async with engine.begin() as connection:
        for name,body in guards.items():
            expected=f'CREATE TRIGGER {name} {body}'
            existing=(await connection.execute(text("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=:name"),{'name':name})).scalar()
            if existing is None:await connection.execute(text(expected))
            elif ' '.join(existing.strip().rstrip(';').split())!=' '.join(expected.strip().rstrip(';').split()):
                raise RuntimeError('GPT Image reference guard is missing or changed; migration refused')
