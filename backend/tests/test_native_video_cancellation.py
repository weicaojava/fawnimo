"""Repeated cancellation at real SQLite writer boundaries must join physical cleanup before the owner exits."""
import asyncio

import pytest
from sqlalchemy import select,update
from sqlalchemy.ext.asyncio import AsyncSession,async_sessionmaker

from app.models.execution import ExecutionJob,ExecutionSlot,SubmissionAttempt
from app.models.execution_budget import ExecutionBudget,ExecutionBudgetEntry
from app.models.model_charge import ModelCallCharge
from app.services.worker.execution_budget import transaction
from app.services.worker.execution_queries import control_job_atomic
from tests.test_native_video_project_accounting import setup,admit,counters


@pytest.mark.asyncio
@pytest.mark.parametrize('phase',['admission','claim','prepare','response','release','control'])
async def test_repeated_native_owner_cancellation_joins_original_writer_and_closes_both_fences(tmp_path,phase):
    """A pending DB writer may finish, but no canceled descriptor can start another POST or leak a lock to another project."""
    engine,original,quote,accountant,worker,runtime,response,_clock=await setup(tmp_path)
    entered=asyncio.Event();release=asyncio.Event();enabled=False;hit=False
    class GatedSession(AsyncSession):
        """Pause only after the chosen real SQL statement has physically completed and owns its actual SQLite lock."""
        async def execute(self,statement,*args,**kwargs):
            """Record a boundary without retaining parameters, prompts or credential material."""
            nonlocal hit
            result=await super().execute(statement,*args,**kwargs)
            sql=str(statement)
            chosen=(phase in {'admission','prepare'} and sql=='BEGIN IMMEDIATE'
                or phase=='control' and sql.startswith('UPDATE execution_jobs')
                or phase=='claim' and sql.startswith('INSERT INTO execution_slots')
                or phase=='response' and sql.startswith('UPDATE model_call_charges') and 'provider_request_id=' in sql
                or phase=='release' and sql.startswith('UPDATE execution_jobs SET lease_until=') and bool(response.calls))
            if enabled and not hit and chosen:
                hit=True;entered.set();await release.wait()
            return result
    factory=async_sessionmaker(engine,class_=GatedSession,expire_on_commit=False,autoflush=False)
    accountant.factory=factory;worker.factory=factory
    owner=None
    try:
        accepted=None
        if phase!='admission':accepted=await admit(accountant,runtime,quote)
        enabled=True
        if phase=='admission':owner=asyncio.create_task(admit(accountant,runtime,quote))
        elif phase=='control':owner=asyncio.create_task(control_job_atomic(factory,'a',accepted['job_ids'][0],'cancel'))
        else:owner=asyncio.create_task(worker.step(accepted['job_ids'][0]))
        await asyncio.wait_for(entered.wait(),5)
        owner.cancel();await asyncio.sleep(0);owner.cancel();await asyncio.sleep(0)
        assert not owner.done(),'Cancellation must wait for the actual owned DB unit and connection close'
        release.set()
        with pytest.raises(asyncio.CancelledError):await asyncio.wait_for(owner,5)
        enabled=False
        async def sibling_writer():
            """Open a fresh actual writer after cancellation; a leaked BEGIN or connection must be observable here."""
            async with original() as db:
                async with transaction(db):
                    await db.execute(update(ExecutionBudget).where(ExecutionBudget.project_id=='b').values(revision=ExecutionBudget.revision+1))
        await asyncio.wait_for(sibling_writer(),2)
        if phase=='admission':accepted=await admit(accountant,runtime,quote)
        identifier=accepted['job_ids'][0]
        async with original() as db:
            job=await db.get(ExecutionJob,identifier);slot=await db.get(ExecutionSlot,1)
            entry=await db.get(ExecutionBudgetEntry,identifier);charge=await db.get(ModelCallCharge,identifier)
            assert job.lease_until==0 and (slot is None or slot.lease_until==0)
            if phase in {'admission','claim'}:
                assert entry.state=='reserved' and charge.dispatch_state=='planned' and response.calls==[]
                assert slot is None or slot.job_id is None
            elif phase=='control':
                assert job.state=='cancelled' and entry.state=='released' and charge.dispatch_state=='not_sent' and response.calls==[]
            elif phase=='prepare':
                assert job.state=='submitting' and entry.state=='unknown' and charge.dispatch_state=='prepared' and response.calls==[]
            else:
                assert entry.state=='unknown' and charge.dispatch_state=='accepted' and response.calls==['POST']
        assert (await counters(original,'b'))[:2]==(0,0)
    finally:
        release.set()
        if owner is not None and not owner.done():owner.cancel();await asyncio.gather(owner,return_exceptions=True)
        await engine.dispose()
