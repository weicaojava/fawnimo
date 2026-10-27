"""Project-scoped, safe execution views and intent-only user controls."""
import time
from fastapi import HTTPException
from sqlalchemy import select, update, exists
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.execution import ExecutionJob, ExecutionSlot, SubmissionAttempt
from app.models.execution_budget import ExecutionBudget, ExecutionBudgetEntry
from app.models.task import GenerationTask
from app.services.studio.production import project_exists

TERMINAL = {'ready', 'failed', 'cancelled'}
RESUMABLE = {'running', 'downloading', 'submission_unknown'}
STATE_LABELS = {'queued':'等待执行', 'submitting':'正在提交', 'submission_unknown':'提交结果待核实', 'running':'远端处理中', 'downloading':'正在接收结果', 'paused':'已暂停', 'ready':'结果已就绪', 'failed':'执行失败', 'cancelled':'已取消'}
REASONS = {'provider_rejected':'服务明确拒绝了请求', 'submission_acceptance_unknown':'尚不能确认服务是否已接受请求，请勿重复提交', 'manual_reconciliation_required':'需要核实原请求的接收结果', 'unknown_acceptance_requires_manual_resolution':'接收结果仍不确定，已暂停核实', 'wait_limit_reached':'达到等待时限，可继续查询原任务', 'query_retry_limit':'查询重试达到上限，可恢复查询', 'download_retry_limit':'结果接收重试达到上限，可恢复接收', 'provider_failed':'服务报告执行失败', 'user_paused':'已按要求暂停，可恢复原任务', 'cancelled_before_submission':'提交前已取消'}


REASONS['runtime_binding_unavailable']='原服务配置已改变，请恢复原配置后继续处理；未重新提交'
REASONS['plan_stale_unsent']='镜头计划或准备资料已失配，已在提交前取消；未发起新的模型请求'


def safe_view(job: ExecutionJob) -> dict:
    """Allowlist fields; never echo provider IDs, arbitrary reasons, URLs or snapshots."""
    return {'id':job.id, 'project_id':job.project_id, 'state':job.state if job.state in STATE_LABELS else 'unknown', 'state_label':STATE_LABELS.get(job.state,'状态待核实'), 'reason':REASONS.get(job.reason, '任务信息已更新，请检查执行状态' if job.reason else ''), 'cancel_requested':job.cancel_requested, 'query_retries':job.query_retries, 'download_retries':job.download_retries, 'can_resume':job.state=='paused', 'can_cancel':job.state not in TERMINAL, 'can_pause':job.state in RESUMABLE and job.lease_until<=time.time()}


async def scoped_job(db: AsyncSession, project_id: str, job_id: str) -> ExecutionJob:
    """A foreign project's identifier is indistinguishable from a missing job."""
    await project_exists(db, project_id)
    job=(await db.execute(select(ExecutionJob).where(ExecutionJob.id==job_id,ExecutionJob.project_id==project_id))).scalar_one_or_none()
    if job is None: raise HTTPException(404,'任务不存在或不属于当前工程')
    return job


async def list_jobs(db: AsyncSession, project_id: str, offset: int, limit: int) -> list[dict]:
    """Bounded stable list, without joining unsafe legacy task payloads."""
    await project_exists(db, project_id)
    rows=(await db.execute(select(ExecutionJob).where(ExecutionJob.project_id==project_id).order_by(ExecutionJob.id).offset(offset).limit(limit))).scalars()
    return [safe_view(job) for job in rows]


async def control_job(db: AsyncSession, project_id: str, job_id: str, action: str) -> dict:
    """CAS state changes; this function never calls a transport or a worker."""
    job=await scoped_job(db,project_id,job_id)
    scope=(ExecutionJob.id==job_id,ExecutionJob.project_id==project_id)
    now=time.time()
    if action=='pause':
        if job.state not in RESUMABLE: raise HTTPException(409,'当前任务不可暂停')
        changed=await db.execute(update(ExecutionJob).where(*scope,ExecutionJob.state==job.state,ExecutionJob.lease_until<=now).values(state='paused',pause_from=job.state,reason='user_paused'))
        if not changed.rowcount: raise HTTPException(409,'任务正在处理或状态已改变，请稍后重试暂停')
    elif action=='resume':
        if job.state!='paused': raise HTTPException(409,'仅已暂停的任务可以恢复')
        target=job.pause_from or ('running' if job.remote_id else 'submission_unknown')
        if target not in RESUMABLE or (target in {'running','downloading'} and not job.remote_id):
            raise HTTPException(409,'原任务恢复状态需要核实，不能重新提交')
        changed=await db.execute(update(ExecutionJob).where(*scope,ExecutionJob.state=='paused',ExecutionJob.lease_until<=now).values(state=target,query_retries=0,download_retries=0,started_at=now,next_due=0,reason='',pause_from=None))
        if not changed.rowcount: raise HTTPException(409,'任务正在处理或状态已改变，请稍后重试恢复')
    elif action=='cancel':
        # A queued job with no journaled submission can safely finish locally.
        # The state CAS fences a worker that has not yet moved to submitting.
        attempt=exists(select(SubmissionAttempt.id).where(SubmissionAttempt.job_id==job_id))
        changed=await db.execute(update(ExecutionJob).where(*scope,ExecutionJob.state=='queued',ExecutionJob.lease_until<=now,~attempt).values(state='cancelled',cancel_requested=True,reason='cancelled_before_submission',lease_token=None,lease_until=0))
        if changed.rowcount:
            entry=await db.get(ExecutionBudgetEntry,job_id)
            if entry is not None:
                if entry.project_id!=project_id or entry.state not in {'reserved','released'}:
                    raise HTTPException(409,'预算记录需要核实，任务尚未取消')
                if entry.state=='reserved':
                    balance=await db.execute(update(ExecutionBudget).where(ExecutionBudget.project_id==project_id,ExecutionBudget.reserved_minor>=entry.quote_minor).values(reserved_minor=ExecutionBudget.reserved_minor-entry.quote_minor,revision=ExecutionBudget.revision+1))
                    if not balance.rowcount: raise HTTPException(409,'预算记录需要核实，任务尚未取消')
                    entry.state='released'
            await db.execute(update(ExecutionSlot).where(ExecutionSlot.job_id==job_id).values(job_id=None,token=None,lease_until=0))
        else:
            await db.execute(update(ExecutionJob).where(*scope,ExecutionJob.state.not_in(TERMINAL)).values(cancel_requested=True))
    else: raise HTTPException(400,'不支持的任务操作')
    await db.flush()
    await db.refresh(job)
    status={'queued':'pending','submitting':'running','submission_unknown':'running','running':'running','downloading':'running','paused':'pending','ready':'succeeded','failed':'failed','cancelled':'cancelled'}.get(job.state,'pending')
    await db.execute(update(GenerationTask).where(GenerationTask.id==job.task_id).values(status=status,progress=100 if job.state=='ready' else 0,cancel_requested=job.cancel_requested,result={'execution_job_id':job.id,'execution_state':job.state},error=REASONS.get(job.reason,'执行失败') if job.state=='failed' else ''))
    return safe_view(job)
