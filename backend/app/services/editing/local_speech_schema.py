"""Offline speech intent and candidate SQL scope guards, additive to all paid accounting."""
from sqlalchemy import text
from app.core.db import Base
import app.models.local_speech  # noqa: F401


async def migrate_local_speech(engine):
    """Create original-scope local journals with immutable intent/output identity and no paid ledger entries."""
    if engine.dialect.name != 'sqlite': return
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        for table in ('local_speech_jobs', 'local_speech_products', 'local_speech_slot'):
            actual = {row[1] for row in (await connection.execute(text(f'PRAGMA table_info({table})'))).all()}
            if actual - {'workspace_id'} != {column.name for column in Base.metadata.tables[table].columns} - {'workspace_id'}: raise RuntimeError('Unsupported local speech schema')
        statements = {
            'local_speech_job_scope': "BEFORE INSERT ON local_speech_jobs WHEN NEW.state!='queued' OR NEW.attempts!=0 OR json_extract(NEW.intent,'$.job_id') IS NOT NEW.id OR json_extract(NEW.intent,'$.project_id') IS NOT NEW.project_id OR json_extract(NEW.intent,'$.chapter_id') IS NOT NEW.chapter_id OR json_extract(NEW.intent,'$.profile') IS NOT 'windows_sapi_pcm_v1' OR json_extract(NEW.intent,'$.external_fee_fen') IS NOT 0 OR NOT EXISTS(SELECT 1 FROM shot_plans p WHERE p.id=json_extract(NEW.intent,'$.plan_id') AND p.project_id=NEW.project_id AND p.content_hash=json_extract(NEW.intent,'$.plan_hash') AND json_extract(p.payload,'$.chapter_id')=NEW.chapter_id) OR NOT EXISTS(SELECT 1 FROM generation_tasks t WHERE t.id=NEW.task_id AND t.task_kind='local_speech' AND t.executor_type='local_speech_v1' AND json_extract(t.payload,'$.project_id')=NEW.project_id AND json_extract(t.payload,'$.chapter_id')=NEW.chapter_id AND json_extract(t.payload,'$.speech_job_id')=NEW.id) OR EXISTS(SELECT 1 FROM local_speech_jobs j WHERE j.id=NEW.id OR (j.project_id=NEW.project_id AND j.request_key=NEW.request_key)) BEGIN SELECT RAISE(ABORT,'Local speech original scope'); END",
            'local_speech_job_no_delete': "BEFORE DELETE ON local_speech_jobs BEGIN SELECT RAISE(ABORT,'Local speech history immutable'); END",
            'local_speech_candidate_scope': "BEFORE INSERT ON sound_candidates WHEN NEW.origin='local_speech' AND NOT EXISTS(SELECT 1 FROM local_speech_jobs j WHERE j.id=json_extract(NEW.evidence,'$.local_speech.job_id') AND j.project_id=NEW.project_id AND j.chapter_id=NEW.chapter_id AND j.intent_hash=NEW.intent_hash AND j.state='running' AND NEW.kind='voice' AND json(json_extract(NEW.evidence,'$.local_speech'))=json(j.intent)) BEGIN SELECT RAISE(ABORT,'Local speech candidate scope'); END",
            'local_speech_product_scope': "BEFORE INSERT ON local_speech_products WHEN NOT EXISTS(SELECT 1 FROM local_speech_jobs j JOIN sound_candidates c ON c.id=NEW.candidate_id WHERE j.id=NEW.job_id AND j.project_id=NEW.project_id AND j.intent_hash=NEW.intent_hash AND c.project_id=j.project_id AND c.chapter_id=j.chapter_id AND c.origin='local_speech' AND c.intent_hash=j.intent_hash AND json_extract(c.evidence,'$.local_speech.job_id')=j.id) OR EXISTS(SELECT 1 FROM local_speech_products p WHERE p.job_id=NEW.job_id OR p.candidate_id=NEW.candidate_id) BEGIN SELECT RAISE(ABORT,'Local speech product scope'); END",
        }
        fields = ('id', 'project_id', 'chapter_id', 'task_id', 'request_key', 'request_hash', 'intent_hash', 'intent')
        changed = ' OR '.join(f'NEW.{field} IS NOT OLD.{field}' for field in fields)
        statements['local_speech_job_identity'] = f"BEFORE UPDATE ON local_speech_jobs WHEN {changed} BEGIN SELECT RAISE(ABORT,'Local speech intent immutable'); END"
        for action in ('UPDATE', 'DELETE'):
            statements['local_speech_product_'+action.lower()] = f"BEFORE {action} ON local_speech_products BEGIN SELECT RAISE(ABORT,'Local speech receipt immutable'); END"
        for name, sql in statements.items():
            expected = 'CREATE TRIGGER '+name+' '+sql
            existing = (await connection.execute(text("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=:name"), {'name': name})).scalar_one_or_none()
            if existing is None: await connection.execute(text(expected))
            elif ' '.join(existing.split()) != ' '.join(expected.split()): raise RuntimeError('Local speech guard changed')
        await connection.execute(text('INSERT OR IGNORE INTO local_speech_slot(id,lease_until) VALUES(1,0)'))
        if (await connection.execute(text('PRAGMA foreign_key_check'))).first(): raise RuntimeError('Local speech foreign key conflict')
