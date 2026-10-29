"""Transactional local SQLite budget accounting using CAS and bounded writer locks.

Every operation owns a fresh session transaction. Never pass a session already in
a transaction. No network requests occur here; submission_unknown must call
mark_unknown and must never call release. Settlement needs trusted actual costs.
"""
from contextlib import asynccontextmanager
from sqlalchemy import text, update
from app.models.execution_budget import ExecutionBudget, ExecutionBudgetEntry

MAX_MINOR = 2**62 - 1


class BudgetError(ValueError):
    """Reject invalid or conflicting accounting without mutating balances."""


def amount(value: int) -> int:
    """Require bounded integer minor units, rejecting bool, floats and unknown quotes."""
    if type(value) is not int or not 0 <= value <= MAX_MINOR:
        raise BudgetError('Known nonnegative integer minor units required')
    return value


@asynccontextmanager
async def transaction(db):
    """Acquire SQLite's writer lock before reads and rollback every failed operation."""
    if db.in_transaction():
        raise BudgetError('Budget operation requires a fresh transaction')
    if db.bind.dialect.name != 'sqlite':
        raise BudgetError('Budget ledger currently supports SQLite only')
    try:
        await db.execute(text('BEGIN IMMEDIATE'))
        yield
        await db.commit()
    except BaseException:
        await db.rollback()
        raise


async def configure(db, project_id: str, currency: str, limit_minor: int):
    """Create a ceiling once; repeated identical configuration is harmless."""
    amount(limit_minor)
    if not project_id or not currency or len(currency) > 16:
        raise BudgetError('Project and currency required')
    async with transaction(db):
        row = await db.get(ExecutionBudget, project_id)
        if row is None:
            db.add(ExecutionBudget(project_id=project_id, currency=currency, limit_minor=limit_minor))
        elif (row.currency, row.limit_minor) != (currency, limit_minor):
            raise BudgetError('Budget configuration conflict')


async def change_balance(db, budget, reserved_delta: int, spent_delta: int):
    """CAS the project revision and available balance in one atomic SQL update."""
    statement = update(ExecutionBudget).where(
        ExecutionBudget.project_id == budget.project_id,
        ExecutionBudget.revision == budget.revision,
        ExecutionBudget.reserved_minor + reserved_delta >= 0,
        ExecutionBudget.spent_minor + spent_delta >= 0,
        ExecutionBudget.reserved_minor + reserved_delta + ExecutionBudget.spent_minor + spent_delta <= ExecutionBudget.limit_minor,
    ).values(reserved_minor=ExecutionBudget.reserved_minor + reserved_delta,
             spent_minor=ExecutionBudget.spent_minor + spent_delta,
             revision=ExecutionBudget.revision + 1).execution_options(synchronize_session=False)
    result = await db.execute(statement)
    if result.rowcount != 1:
        raise BudgetError('Insufficient budget or stale revision')


async def reserve(db, project_id: str, job_id: str, quote_minor: int, currency: str):
    """Reserve once by job; changed quotes or currency/project reuse are conflicts."""
    amount(quote_minor)
    if not job_id:
        raise BudgetError('Job required')
    async with transaction(db):
        budget = await db.get(ExecutionBudget, project_id)
        if budget is None or budget.currency != currency:
            raise BudgetError('Configured budget with matching currency required')
        entry = await db.get(ExecutionBudgetEntry, job_id)
        if entry is not None:
            if (entry.project_id, entry.quote_minor) != (project_id, quote_minor):
                raise BudgetError('Job quote conflict')
            return entry.state
        await change_balance(db, budget, quote_minor, 0)
        db.add(ExecutionBudgetEntry(job_id=job_id, project_id=project_id, quote_minor=quote_minor, state='reserved'))
        return 'reserved'


async def finish(db, project_id: str, job_id: str, action: str, actual_minor=None):
    """Move one reservation once; unknown holds cannot release without reconciliation.

    Action unknown retains the full hold. Only a trusted reconciliation proving
    rejection may use release_reconciled. Identical terminal calls are idempotent.
    """
    if action not in {'unknown', 'release', 'release_reconciled', 'settle'}:
        raise BudgetError('Unsupported accounting action')
    if action == 'settle':
        amount(actual_minor)
    async with transaction(db):
        entry = await db.get(ExecutionBudgetEntry, job_id)
        if entry is None or entry.project_id != project_id:
            raise BudgetError('Project-owned reservation required')
        target = 'released' if action.startswith('release') else 'settled' if action == 'settle' else 'unknown'
        if entry.state == target:
            if target == 'settled' and entry.actual_minor != actual_minor:
                raise BudgetError('Settlement conflict')
            return target
        if entry.state in {'released', 'settled'}:
            raise BudgetError('Reservation already finalized')
        if entry.state == 'unknown' and action == 'release':
            raise BudgetError('Unknown acceptance retains reservation pending reconciliation')
        if action == 'settle' and actual_minor > entry.quote_minor:
            raise BudgetError('Actual cost exceeds reserved quote')
        if target != 'unknown':
            budget = await db.get(ExecutionBudget, project_id)
            await change_balance(db, budget, -entry.quote_minor, actual_minor if target == 'settled' else 0)
        entry.state = target
        if target == 'settled':
            entry.actual_minor = actual_minor
        return target


async def mark_unknown(db, project_id: str, job_id: str):
    """Hold funds when remote submission acceptance cannot be determined."""
    return await finish(db, project_id, job_id, 'unknown')


async def release(db, project_id: str, job_id: str):
    """Release only known unaccepted work; unknown submissions remain reserved."""
    return await finish(db, project_id, job_id, 'release')


async def settle(db, project_id: str, job_id: str, actual_minor: int):
    """Settle a trusted actual amount once, returning any unspent reservation."""
    return await finish(db, project_id, job_id, 'settle', actual_minor)
