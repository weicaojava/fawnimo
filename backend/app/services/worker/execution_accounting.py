"""Trusted internal accounting completion; no prices or free execution are inferred."""
from sqlalchemy import select
from app.core.db import async_session_maker
from app.models.execution import ExecutionJob, SubmissionAttempt
from app.models.execution_budget import ExecutionBudget, ExecutionBudgetEntry
from app.services.worker.execution_budget import BudgetError, amount, change_balance, transaction


async def recover_unaccepted_holds(*, factory=async_session_maker, limit=20) -> int:
    """Retry proven releases after a crash between terminal state and accounting."""
    if type(limit) is not int or not 1<=limit<=100:raise ValueError('Invalid recovery batch size')
    async with factory() as db:
        rejected=select(SubmissionAttempt.job_id).where(SubmissionAttempt.state=='rejected')
        attempted=select(SubmissionAttempt.job_id)
        candidates=(await db.execute(select(ExecutionJob.project_id,ExecutionJob.id).join(ExecutionBudgetEntry,ExecutionBudgetEntry.job_id==ExecutionJob.id).where(ExecutionBudgetEntry.state.in_({'reserved','unknown'}),((ExecutionJob.state=='failed') & ExecutionJob.id.in_(rejected)) | ((ExecutionJob.state=='cancelled') & ExecutionJob.id.not_in(attempted) & (ExecutionBudgetEntry.state=='reserved'))).order_by(ExecutionJob.id).limit(limit))).all()
    released=0
    for project_id,job_id in candidates:
        try:
            await release_unaccepted(project_id,job_id,factory=factory);released+=1
        except BudgetError:
            pass  # Accepted/unknown cancellation has no release authority.
    return released


async def owned_hold(db, project_id: str, job_id: str):
    """Resolve job and matching hold under the caller's SQLite writer transaction."""
    job=await db.get(ExecutionJob,job_id)
    entry=await db.get(ExecutionBudgetEntry,job_id)
    budget=await db.get(ExecutionBudget,project_id)
    if job is None or job.project_id!=project_id or entry is None or entry.project_id!=project_id or budget is None:
        raise BudgetError('Matching project job and reservation required')
    return job,entry,budget


async def release_unaccepted(project_id: str, job_id: str, *, factory=async_session_maker) -> str:
    """Release only proven rejection or cancellation before any submission attempt.

    BEGIN IMMEDIATE covers evidence checks and balance writes together, so a
    competing submit transaction cannot turn reserved into unknown between them.
    Cancellation intent, ready output and timeouts are not rejection evidence.
    """
    async with factory() as db:
        async with transaction(db):
            job,entry,budget=await owned_hold(db,project_id,job_id)
            attempt=(await db.execute(select(SubmissionAttempt).where(SubmissionAttempt.job_id==job_id))).scalars().one_or_none()
            rejected=job.state=='failed' and attempt is not None and attempt.state=='rejected'
            unsent=job.state=='cancelled' and attempt is None
            if not (rejected or unsent):raise BudgetError('No durable proof of non-acceptance')
            if entry.state=='released':return 'released'
            if entry.state=='settled':raise BudgetError('Reservation already settled')
            if unsent and entry.state!='reserved':raise BudgetError('Unsent cancellation requires reserved funds')
            if rejected and entry.state not in {'reserved','unknown'}:raise BudgetError('Invalid reservation lifecycle')
            await change_balance(db,budget,-entry.quote_minor,0)
            entry.state='released'
            return 'released'


async def settle_actual(project_id: str, job_id: str, actual_minor: int, *, factory=async_session_maker) -> str:
    """Settle an explicitly trusted actual amount once, never inferred from ready.

    Only internal qualified billing/reconciliation callers may invoke this service;
    it deliberately exposes no HTTP endpoint or automatic provider-status charge.
    """
    amount(actual_minor)
    async with factory() as db:
        async with transaction(db):
            _job,entry,budget=await owned_hold(db,project_id,job_id)
            if actual_minor>entry.quote_minor:raise BudgetError('Actual cost exceeds reserved quote')
            if entry.state=='settled':
                if entry.actual_minor!=actual_minor:raise BudgetError('Settlement conflict')
                return 'settled'
            if entry.state not in {'reserved','unknown'}:raise BudgetError('Reservation already finalized')
            await change_balance(db,budget,-entry.quote_minor,actual_minor)
            entry.state='settled';entry.actual_minor=actual_minor
            return 'settled'
