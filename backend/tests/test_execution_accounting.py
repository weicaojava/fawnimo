"""Real SQLite accounting tests with synthetic units and no model invocation."""
import asyncio
import uuid
import pytest
from sqlalchemy import update
from app.models.execution import ExecutionJob, SubmissionAttempt
from app.models.execution_budget import ExecutionBudget, ExecutionBudgetEntry
from app.services.worker.execution_budget import BudgetError, configure, reserve
from app.services.worker.execution_accounting import release_unaccepted, settle_actual
from tests.test_durable_execution import setup


@pytest.mark.asyncio
async def test_accounting_recovery_skips_accepted_cancelled_holds(tmp_path):
    """An unreleasable cancellation cannot fill every bounded recovery batch."""
    from app.services.worker.execution_accounting import recover_unaccepted_holds
    from app.services.worker.durable_execution import DurableExecution
    engine,factory,accepted_job=await prepared(tmp_path,'cancelled','accepted')
    try:
        worker=DurableExecution(None,factory=factory,budget_required=False)
        unsent=await worker.create('p','unsent-recovery',{})
        async with factory() as db:await reserve(db,'p',unsent,40,'TEST')
        async with factory() as db:
            await db.execute(update(ExecutionJob).where(ExecutionJob.id==unsent).values(state='cancelled'));await db.commit()
        assert await recover_unaccepted_holds(factory=factory,limit=1)==1
        assert await recover_unaccepted_holds(factory=factory,limit=1)==0
        async with factory() as db:
            assert (await db.get(ExecutionBudget,'p')).reserved_minor==60
            assert (await db.get(ExecutionBudgetEntry,accepted_job)).state=='unknown'
            assert (await db.get(ExecutionBudgetEntry,unsent)).state=='released'
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_rejected_journal_recovers_terminal_and_accounting(tmp_path):
    """Restart after durable rejection restores failed state and releases once."""
    from app.services.worker.execution_accounting import recover_unaccepted_holds
    from app.services.worker.durable_execution import DurableExecution
    from tests.test_durable_execution import Adapter,read
    engine,factory,job=await prepared(tmp_path,'submitting','rejected')
    adapter=Adapter(factory)
    try:
        worker=DurableExecution(adapter,factory=factory,clock=lambda:1000,budget_required=False)
        await worker.step(job)
        assert (await read(factory,job)).state=='failed' and adapter.sent==0
        assert await recover_unaccepted_holds(factory=factory)==1
        assert await recover_unaccepted_holds(factory=factory)==0
    finally:await engine.dispose()


async def prepared(tmp_path, state='queued', attempt_state=None):
    """Build an owned real job and reservation, optionally with durable evidence."""
    engine,factory,adapter,clock,worker,_=await setup(tmp_path)
    job_id=await worker.create('p','accounting',{'fixture':True})
    async with factory() as db:await configure(db,'p','TEST',100)
    async with factory() as db:await reserve(db,'p',job_id,60,'TEST')
    async with factory() as db:
        await db.execute(update(ExecutionJob).where(ExecutionJob.id==job_id).values(state=state,cancel_requested=True))
        if attempt_state:
            db.add(SubmissionAttempt(id=str(uuid.uuid4()),job_id=job_id,request_hash='fixture',state=attempt_state))
            await db.execute(update(ExecutionBudgetEntry).where(ExecutionBudgetEntry.job_id==job_id).values(state='unknown'))
        await db.commit()
    return engine,factory,job_id


@pytest.mark.asyncio
@pytest.mark.parametrize('state,attempt',[('cancelled',None),('failed','rejected')])
async def test_release_proven_unaccepted_once(tmp_path,state,attempt):
    """Concurrent evidence-based releases return one reservation exactly once."""
    engine,factory,job=await prepared(tmp_path,state,attempt)
    try:
        assert await asyncio.gather(*(release_unaccepted('p',job,factory=factory) for _ in range(4)))==['released']*4
        async with factory() as db:
            budget=await db.get(ExecutionBudget,'p')
            assert (budget.reserved_minor,budget.spent_minor)==(0,0)
    finally:await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('state,attempt',[('queued',None),('ready','accepted'),('submission_unknown','prepared'),('cancelled','accepted'),('failed','accepted'),('cancelled','prepared')])
async def test_no_release_from_intent_timeout_or_ready(tmp_path,state,attempt):
    """Intent, provider success and uncertain acceptance never imply free execution."""
    engine,factory,job=await prepared(tmp_path,state,attempt)
    try:
        with pytest.raises(BudgetError):await release_unaccepted('p',job,factory=factory)
        async with factory() as db:assert (await db.get(ExecutionBudget,'p')).reserved_minor==60
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_explicit_settlement_scope_amount_and_idempotence(tmp_path):
    """Trusted costs settle once; wrong scope, changed cost and overspend fail."""
    engine,factory,job=await prepared(tmp_path,'ready','accepted')
    try:
        for project,identity,cost in [('other',job,40),('p','absent',40),('p',job,61),('p',job,True)]:
            with pytest.raises(BudgetError):await settle_actual(project,identity,cost,factory=factory)
        assert await asyncio.gather(*(settle_actual('p',job,40,factory=factory) for _ in range(4)))==['settled']*4
        with pytest.raises(BudgetError):await settle_actual('p',job,41,factory=factory)
        async with factory() as db:
            budget=await db.get(ExecutionBudget,'p')
            assert (budget.reserved_minor,budget.spent_minor)==(0,40)
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_release_serializes_against_submit_transition(tmp_path):
    """A simultaneous guarded submit cannot consume released cancelled-job funds."""
    engine,factory,job=await prepared(tmp_path,'cancelled')
    async def submit_transaction():
        """Mirror the production submit CAS and unknown hold in one real transaction."""
        async with factory() as db:
            held=await db.execute(update(ExecutionBudgetEntry).where(ExecutionBudgetEntry.job_id==job,ExecutionBudgetEntry.state=='reserved').values(state='unknown'))
            changed=await db.execute(update(ExecutionJob).where(ExecutionJob.id==job,ExecutionJob.state=='queued').values(state='submitting'))
            if not held.rowcount or not changed.rowcount:await db.rollback();return False
            await db.commit();return True
    try:
        released,submitted=await asyncio.gather(release_unaccepted('p',job,factory=factory),submit_transaction())
        assert released=='released' and submitted is False
        async with factory() as db:assert (await db.get(ExecutionBudget,'p')).reserved_minor==0
    finally:await engine.dispose()
