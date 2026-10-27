"""Trusted per-shot admission and first-send validation reuse the original execution core."""
from fastapi import HTTPException
from sqlalchemy import select
from app.models.execution import ExecutionJob
from app.models.execution_binding import ExecutionBinding
from app.models.shot_plan import PlanJobLink
from app.models.task_links import GenerationTaskLink
from app.services.worker.execution_admission import _insert_reserved_job
from app.services.worker.execution_budget import transaction,BudgetError
from app.services.shot_plan.compiler import content_hash,media_evidence
from app.services.shot_plan.service import owned_plan,validate_current,runtime_issues


def entry_for(plan,shot_id):
    """Resolve an immutable actual entry, never client-supplied shot payload."""
    entry=next((entry for entry in plan.payload['entries'] if entry['shot_id']==shot_id),None)
    if entry is None:raise HTTPException(422,'镜头不在此整章执行计划中')
    return entry


def request_identity(plan,entry):
    """Domain-separate planned requests while keeping adapter JSON unchanged."""
    return content_hash({'domain':'m4b-plan-job-v1','plan_id':plan.id,'content_hash':plan.content_hash,'shot_id':entry['shot_id'],'entry_hash':entry['entry_hash'],'request':entry['request']})


async def replay(db,project_id,key,plan,entry):
    """Return only the original exact job before checking current source or runtime eligibility."""
    job=(await db.execute(select(ExecutionJob).where(ExecutionJob.project_id==project_id,ExecutionJob.request_key==key))).scalar_one_or_none()
    if job is None:return None
    link=await db.get(PlanJobLink,job.id)
    if link is None or (link.project_id,link.plan_id,link.shot_id,link.entry_hash)!=(project_id,plan.id,entry['shot_id'],entry['entry_hash']) or job.request_hash!=request_identity(plan,entry):raise HTTPException(409,'请求标识已用于其他计划、镜头或旧任务')
    return job.id


async def admit_planned_shot(factory,project_id,plan_id,shot_id,request_key,runtime=None):
    """Atomically reserve one original-model budget and attach both plan and task-center links."""
    if not isinstance(request_key,str) or not request_key.strip() or len(request_key)>128:raise HTTPException(422,'请求标识必须为1至128字符')
    async with factory() as db:
        plan=await owned_plan(db,project_id,plan_id);entry=entry_for(plan,shot_id)
        old=await replay(db,project_id,request_key,plan,entry)
        if old:return old
        if runtime is None or runtime.worker.factory is not factory:raise HTTPException(503,'生成运行配置未启用或数据库配置不一致')
        problems=runtime_issues(plan.payload,runtime)
        if problems:raise HTTPException(503,problems[0]['message'])
        _,proof=await validate_current(db,plan)
    try:
        async with factory() as db:
            async with transaction(db):
                plan=await owned_plan(db,project_id,plan_id);entry=entry_for(plan,shot_id)
                old=await replay(db,project_id,request_key,plan,entry)
                if old:return old
                if runtime is None or runtime.worker.factory is not factory or runtime_issues(plan.payload,runtime):raise HTTPException(503,'运行配置已改变或未启用')
                await validate_current(db,plan,verified=proof)
                policy=runtime.policies[plan.payload['document']['model_id']]
                async def validate_locked_references(session,project,snapshot):
                    """Recheck only immutable metadata/stat under the admission writer lock."""
                    for reference in snapshot['references']:await media_evidence(session,project,reference['media_id'],proof.get(reference['media_id'],{}))
                job_id=await _insert_reserved_job(db,project_id,request_key,entry['request'],request_identity(plan,entry),policy,reference_validator=validate_locked_references)
                db.add(PlanJobLink(job_id=job_id,project_id=project_id,plan_id=plan.id,shot_id=shot_id,entry_hash=entry['entry_hash']))
                job=await db.get(ExecutionJob,job_id)
                db.add(GenerationTaskLink(task_id=job.task_id,resource_type='video',relation_type='video',relation_entity_id=shot_id))
                await db.flush();return job_id
    except BudgetError:raise HTTPException(409,'预算不足或预留冲突，未创建任务') from None


async def prepare_send_evidence(factory,job_id):
    """Hash planned media before the prepared writer transaction; legacy jobs need no hook."""
    async with factory() as db:
        link=await db.get(PlanJobLink,job_id)
        if link is None:return None
        plan=await owned_plan(db,link.project_id,link.plan_id)
        _,proof=await validate_current(db,plan)
        return proof


async def validate_send_locked(db,job_id,proof):
    """Fail closed before any attempt or unknown charge; no source recheck after prepared."""
    link=await db.get(PlanJobLink,job_id)
    if link is None:return
    if proof is None:raise HTTPException(409,'计划媒体预检凭据缺失')
    plan=await owned_plan(db,link.project_id,link.plan_id);entry=entry_for(plan,link.shot_id)
    job=await db.get(ExecutionJob,job_id);binding=await db.get(ExecutionBinding,job_id)
    if entry['entry_hash']!=link.entry_hash or job.project_id!=link.project_id or job.request_hash!=request_identity(plan,entry) or job.snapshot!=entry['request']:raise HTTPException(409,'计划执行来源校验失败')
    if not plan.payload.get('binding') or binding is None or binding.binding_hash!=plan.payload['binding']['binding_hash']:raise HTTPException(409,'计划执行配置绑定无效')
    await validate_current(db,plan,verified=proof)
