"""Install one additive, immutable script-proposal journal without touching old chapter text."""
from sqlalchemy import text
from app.models.script_candidate import ScriptCandidate


async def migrate_script_candidates(engine):
    """Protect original input and actual text-run ownership against rewrites and replacement."""
    if engine.dialect.name!='sqlite':raise RuntimeError('Script proposals currently require SQLite')
    async with engine.begin() as connection:
        await connection.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS chapter_id_project_scope ON chapters(id,project_id)'))
        await connection.run_sync(lambda sync:ScriptCandidate.__table__.create(sync,checkfirst=True))
        identities=('id','project_id','chapter_id','shot_id','operation','source_field','source_revision','source_hash','source_snapshot')
        changed=' OR '.join('NEW.'+name+' IS NOT OLD.'+name for name in identities)
        run_fields=('id','kind','scope_key','request_key','request_hash','model_id','model_name','model_binding','config_snapshot','input_text','max_tokens')
        run_changed=' OR '.join('NEW.'+name+' IS NOT OLD.'+name for name in run_fields)
        guards={
            'script_candidate_identity':f"BEFORE UPDATE ON script_candidates WHEN {changed} BEGIN SELECT RAISE(ABORT,'Script proposal source is immutable'); END",
            'script_candidate_no_delete':"BEFORE DELETE ON script_candidates BEGIN SELECT RAISE(ABORT,'Script proposals retain their decisions'); END",
            'script_candidate_no_replace':"BEFORE INSERT ON script_candidates WHEN EXISTS(SELECT 1 FROM script_candidates WHERE id=NEW.id) BEGIN SELECT RAISE(ABORT,'Script proposal cannot be replaced'); END",
            'script_candidate_scope':"BEFORE INSERT ON script_candidates WHEN NEW.decision!='pending' OR NEW.adopted_output_hash IS NOT NULL OR NEW.source_revision<0 OR NEW.operation NOT IN ('optimize','simplify','divide','extract_shot') OR NEW.source_field NOT IN ('raw_text','condensed_text') OR ((NEW.operation='extract_shot')!=(NEW.shot_id IS NOT NULL)) OR NOT EXISTS(SELECT 1 FROM agent_execution_runs r JOIN model_job_charges c ON c.job_id=r.id AND c.job_kind='agent' WHERE r.id=NEW.id AND r.kind='script_candidate' AND c.project_id=NEW.project_id) OR (NEW.shot_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM shots s WHERE s.id=NEW.shot_id AND s.chapter_id=NEW.chapter_id)) BEGIN SELECT RAISE(ABORT,'Script proposal requires its original project text run'); END",
            'script_candidate_decision':"BEFORE UPDATE OF decision,adopted_output_hash ON script_candidates WHEN NOT (NEW.decision=OLD.decision AND NEW.adopted_output_hash IS OLD.adopted_output_hash) AND NOT (OLD.decision='pending' AND ((NEW.decision='adopted' AND NEW.adopted_output_hash IS NOT NULL AND length(NEW.adopted_output_hash)=64) OR (NEW.decision='dismissed' AND NEW.adopted_output_hash IS NULL))) BEGIN SELECT RAISE(ABORT,'Script proposal decision cannot rewind'); END",
            'script_candidate_run_identity':f"BEFORE UPDATE ON agent_execution_runs WHEN EXISTS(SELECT 1 FROM script_candidates c WHERE c.id=OLD.id) AND ({run_changed}) BEGIN SELECT RAISE(ABORT,'Script proposal run identity is immutable'); END",
            'script_candidate_run_no_replace':"BEFORE INSERT ON agent_execution_runs WHEN EXISTS(SELECT 1 FROM script_candidates c WHERE c.id=NEW.id) BEGIN SELECT RAISE(ABORT,'Script proposal run cannot be replaced'); END",
            'script_candidate_run_terminal':"BEFORE UPDATE ON agent_execution_runs WHEN EXISTS(SELECT 1 FROM script_candidates c WHERE c.id=OLD.id) AND OLD.status IN ('succeeded','failed','cancelled') AND (NEW.status IS NOT OLD.status OR NEW.output_text IS NOT OLD.output_text) BEGIN SELECT RAISE(ABORT,'Script proposal terminal output cannot change'); END",
        }
        for name,body in guards.items():
            expected=f'CREATE TRIGGER {name} {body}'
            actual=await connection.scalar(text("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=:name"),{'name':name})
            if actual is None:await connection.execute(text(expected))
            elif ' '.join(actual.split())!=' '.join(expected.split()):raise RuntimeError('Unsupported script proposal guard')
