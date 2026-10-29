"""Project-scoped ledger projections and immutable budget edit receipts."""
import re
import time
import uuid
from fastapi import HTTPException
from sqlalchemy import exists,select
from app.core.contracts.video_runtime_setup import ProjectBudgetEntryRead, ProjectBudgetPage
from app.models.execution_budget import ExecutionBudgetEntry
from app.models.project_budget import ProjectBudgetChange
from app.models.model_charge import ModelCallCharge
from app.models.model_billing import ModelBillingReceipt
from app.models.execution import ExecutionJob
from app.services.studio.production import project_exists


def record_budget_change(db, project_id: str, actor_id: str | None, previous_limit: int | None, limit: int, revision: int) -> None:
    """Append a server-selected local or identity actor within the caller's budget transaction."""
    if actor_id is not None and not re.fullmatch(r'usr_[a-f0-9]{32}', actor_id):
        raise HTTPException(403, '项目预算需要受信的操作者身份')
    db.add(ProjectBudgetChange(id=uuid.uuid4().hex, project_id=project_id, actor_id=actor_id or 'local_operator',
                              previous_limit_minor=previous_limit, limit_minor=limit, revision=revision, created_at=int(time.time())))


async def list_project_entries(db, project_id: str, *, after: str | None, limit: int) -> ProjectBudgetPage:
    """Filter by owned project before pagination; another project's cursor cannot change the scope."""
    await project_exists(db, project_id)
    video=exists(select(ExecutionJob.id).where(ExecutionJob.id==ExecutionBudgetEntry.job_id,ExecutionJob.project_id==project_id))
    statement = (select(ExecutionBudgetEntry,ModelCallCharge.kind,ModelBillingReceipt.id,video).select_from(ExecutionBudgetEntry)
        .outerjoin(ModelCallCharge,(ModelCallCharge.id==ExecutionBudgetEntry.job_id)&(ModelCallCharge.project_id==project_id))
        .outerjoin(ModelBillingReceipt,(ModelBillingReceipt.adjustment_entry_id==ExecutionBudgetEntry.job_id)&(ModelBillingReceipt.project_id==project_id))
        .where(ExecutionBudgetEntry.project_id == project_id).order_by(ExecutionBudgetEntry.job_id).limit(limit+1))
    if after:
        statement = statement.where(ExecutionBudgetEntry.job_id > after)
    rows = (await db.execute(statement)).all()
    items = [ProjectBudgetEntryRead(job_id=entry.job_id, project_id=entry.project_id, state=entry.state,
                                    quote_minor=entry.quote_minor, actual_minor=entry.actual_minor,
                                    quote_minor_exact=str(entry.quote_minor),actual_minor_exact=str(entry.actual_minor) if entry.actual_minor is not None else None,
                                    source='model_call' if kind else 'bill_adjustment' if receipt else 'durable_video' if is_video else 'unclassified',
                                    call_kind=kind) for entry,kind,receipt,is_video in rows[:limit]]
    return ProjectBudgetPage(items=items, next_after=items[-1].job_id if len(rows) > limit else None)
