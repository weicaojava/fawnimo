"""Workspace budget administration; all paid admission still requires an enabled trusted runtime."""
import re
import time
import uuid
from fastapi import HTTPException
from sqlalchemy import func, select, update
from app.core.contracts.workspace_budget import WorkspaceBudgetRead, WorkspaceBudgetWrite, WorkspaceBudgetPage, WorkspaceBudgetEntryRead
from app.models.execution_budget import ExecutionBudget, ExecutionBudgetEntry
from app.models.studio import Project
from app.models.workspace_budget import WorkspaceBudget, WorkspaceBudgetChange
from app.services.worker.execution_budget import transaction


async def read_workspace_budget(db) -> WorkspaceBudgetRead:
    """Read ceiling, aggregate balances and counts in one consistent SQLite statement."""
    reserved = select(func.coalesce(func.sum(ExecutionBudget.reserved_minor), 0)).scalar_subquery()
    spent = select(func.coalesce(func.sum(ExecutionBudget.spent_minor), 0)).scalar_subquery()
    unsettled = select(func.count()).select_from(ExecutionBudgetEntry).where(ExecutionBudgetEntry.state.in_({'reserved', 'unknown'})).scalar_subquery()
    uncertain = select(func.count()).select_from(ExecutionBudgetEntry).where(ExecutionBudgetEntry.state == 'unknown').scalar_subquery()
    row = (await db.execute(select(WorkspaceBudget, reserved, spent, unsettled, uncertain).where(WorkspaceBudget.id == 1))).first()
    if row is None:
        return WorkspaceBudgetRead(managed=False, limit_minor=0, reserved_minor=0, spent_minor=0, available_minor=0, revision=0, unsettled_jobs=0, uncertain_jobs=0)
    budget, held, paid, pending, unknown = row
    return WorkspaceBudgetRead(managed=True, limit_minor=budget.limit_minor, reserved_minor=held, spent_minor=paid,
                               available_minor=budget.limit_minor-held-paid, revision=budget.revision, unsettled_jobs=pending, uncertain_jobs=unknown)


async def save_workspace_budget(factory, body: WorkspaceBudgetWrite, actor_id: str) -> WorkspaceBudgetRead:
    """CAS the shared ceiling and immutable actor receipt in the same locked transaction."""
    if not re.fullmatch(r'usr_[a-f0-9]{32}', actor_id):
        raise HTTPException(403, '需要受信的空间所有者身份')
    async with factory() as db:
        async with transaction(db):
            current = await read_workspace_budget(db)
            if not current.managed:
                raise HTTPException(503, '此入口尚未启用工作空间预算')
            if current.revision != body.expected_revision:
                raise HTTPException(409, '空间预算或任务预留已更新，请刷新后重新确认')
            if body.limit_minor < current.reserved_minor + current.spent_minor:
                raise HTTPException(409, '空间上限不能低于已预留与已结算金额；待核实费用继续保留')
            result = await db.execute(update(WorkspaceBudget).where(WorkspaceBudget.id == 1, WorkspaceBudget.revision == body.expected_revision).values(
                limit_minor=body.limit_minor, revision=WorkspaceBudget.revision+1).execution_options(synchronize_session=False))
            if result.rowcount != 1:
                raise HTTPException(409, '空间预算已改变，请刷新后重新确认')
            db.add(WorkspaceBudgetChange(id=uuid.uuid4().hex, actor_id=actor_id, previous_limit_minor=current.limit_minor,
                                        limit_minor=body.limit_minor, revision=current.revision+1, created_at=int(time.time())))
            await db.flush()
            # A prior select loaded the ORM row; force a current projection after the SQL CAS.
            db.expire_all()
            return await read_workspace_budget(db)


async def list_workspace_entries(db, *, after: str | None, limit: int) -> WorkspaceBudgetPage:
    """Read only this fixed database's ledger; cursor and batch size cannot select another space."""
    if not (await read_workspace_budget(db)).managed:
        raise HTTPException(503, '此入口尚未启用工作空间预算')
    statement = select(ExecutionBudgetEntry, Project.name).outerjoin(Project, Project.id == ExecutionBudgetEntry.project_id).order_by(ExecutionBudgetEntry.job_id).limit(limit+1)
    if after:
        statement = statement.where(ExecutionBudgetEntry.job_id > after)
    rows = (await db.execute(statement)).all()
    items = [WorkspaceBudgetEntryRead(job_id=entry.job_id, project_id=entry.project_id, project_name=name, state=entry.state,
                                      quote_minor=entry.quote_minor, actual_minor=entry.actual_minor) for entry, name in rows[:limit]]
    return WorkspaceBudgetPage(items=items, next_after=items[-1].job_id if len(rows) > limit else None)
