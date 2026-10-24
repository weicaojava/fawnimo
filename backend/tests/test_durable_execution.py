"""Real SQLite worker tests with a test-only adapter; no generated media is claimed."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.core.db import Base
from app.models.studio import Project
from app.models.execution import ExecutionJob, SubmissionAttempt
from app.services.worker.durable_execution import DurableExecution


class Clock:
    """Advance deadlines deterministically instead of sleeping in fault tests."""
    def __init__(self):self.value=1000
    def __call__(self):return self.value
    def advance(self,n=100):self.value+=n


class Adapter:
    """Fixture adapter deliberately has no external model or credential access."""
    def __init__(self,factory):
        self.factory=factory;self.sent=0;self.known={};self.fail_query=False;self.fail_download=False;self.cancel_confirmed=False
    async def submit(self,snapshot,request_id):
        """Assert the attempt really committed before the simulated remote acceptance."""
        async with self.factory() as db:assert await db.get(SubmissionAttempt,request_id) is not None
        self.sent+=1;self.known[request_id]='remote-fixture';return 'remote-fixture'
    async def reconcile(self,request_id):return self.known.get(request_id)
    async def query(self,remote_id):
        if self.fail_query:raise TimeoutError()
        return {'status':'succeeded','artifact_kind':'test_fixture'}
    async def download(self,result,media_id,project_id):
        if self.fail_download:raise TimeoutError()
        return media_id
    async def cancel(self,remote_id):return self.cancel_confirmed


async def fixture_publisher(factory,clock,job_id,token,artifact):
    """State-machine-only seam; real artifact fencing is tested separately."""
    return artifact


async def setup(tmp_path):
    """Create an isolated real database with the same FK enforcement as the app."""
    url='sqlite+aiosqlite:///'+(tmp_path/'execution.db').as_posix();engine=create_async_engine(url)
    @event.listens_for(engine.sync_engine,'connect')
    def enable_fk(connection,_record):
        """Enable constraints for every test connection."""
        connection.execute('PRAGMA foreign_keys=ON')
    async with engine.begin() as conn:await conn.run_sync(Base.metadata.create_all)
    factory=async_sessionmaker(engine,expire_on_commit=False)
    async with factory() as db:
        db.add(Project(id='p',name='test fixture project',style='真人古装',visual_style='现实',description='',seed=0,unify_style=True,progress=0,stats={}));await db.commit()
    adapter=Adapter(factory);clock=Clock();worker=DurableExecution(adapter,factory=factory,clock=clock,publisher=fixture_publisher,budget_required=False)
    return engine,factory,adapter,clock,worker,url


async def read(factory,job_id):
    """Read durable state through a fresh session, not a cached ORM instance."""
    async with factory() as db:return await db.get(ExecutionJob,job_id)


@pytest.mark.asyncio
async def test_expired_owner_cannot_write_or_renew_and_slot_stays_reserved(tmp_path):
    """Expiry fences the old owner without admitting another billable job."""
    engine,factory,adapter,clock,worker,_=await setup(tmp_path)
    try:
        first=await worker.create('p','first',{'prompt_revision':1})
        second=await worker.create('p','second',{'prompt_revision':1})
        _,token=await worker._claim(first)
        clock.advance()
        assert not await worker._update(first,token,state='ready')
        assert not await worker._renew(first,token)
        assert await worker._claim(second) is None
        assert await worker._claim(first) is not None
        assert adapter.sent==0
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_terminal_commit_releases_slot_without_finally(tmp_path):
    """A worker dying immediately after terminal commit cannot block the queue."""
    engine,factory,adapter,clock,worker,_=await setup(tmp_path)
    try:
        first=await worker.create('p','terminal',{'prompt_revision':1})
        second=await worker.create('p','next',{'prompt_revision':1})
        _,token=await worker._claim(first)
        assert await worker._update(first,token,state='cancelled')
        # Deliberately skip step/finally, as if the process died at this boundary.
        assert await worker._claim(second) is not None
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_default_worker_requires_reserved_budget_and_keeps_unknown_hold(tmp_path):
    """The default production worker cannot send without an atomic spending hold."""
    from app.services.worker.execution_budget import configure,reserve,release,BudgetError
    from app.models.execution_budget import ExecutionBudgetEntry
    engine,factory,adapter,clock,_,_=await setup(tmp_path)
    worker=DurableExecution(adapter,factory=factory,clock=clock,publisher=fixture_publisher,enforce_binding=False)
    try:
        job_id=await worker.create('p','budget-enforced',{'prompt_revision':1})
        await worker.step(job_id)
        assert adapter.sent==0
        async with factory() as db:await configure(db,'p','TEST',100)
        async with factory() as db:await reserve(db,'p',job_id,40,'TEST')
        await worker.step(job_id)
        assert adapter.sent==1
        async with factory() as db:
            hold=await db.get(ExecutionBudgetEntry,job_id)
            assert hold.state=='unknown'
        async with factory() as db:
            with pytest.raises(BudgetError):await release(db,'p',job_id)
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_deduplication_and_competing_workers(tmp_path):
    """Concurrent duplicate requests and workers cause one provider submission."""
    engine,factory,adapter,clock,worker,_=await setup(tmp_path)
    try:
        ids=await asyncio.gather(worker.create('p','same',{'prompt_revision':1}),worker.create('p','same',{'prompt_revision':1}))
        assert ids[0]==ids[1]
        await asyncio.gather(worker.step(ids[0]),worker.step(ids[0]))
        assert adapter.sent==1 and (await read(factory,ids[0])).state=='running'
        with pytest.raises(HTTPException):await worker.create('p','same',{'prompt_revision':2})
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_unknown_acceptance_never_resubmits(tmp_path):
    """An accepted request with a lost response reconciles by durable attempt identity."""
    engine,factory,adapter,clock,worker,_=await setup(tmp_path)
    submit=adapter.submit
    async def lost(snapshot,request_id):
        """Simulate a network response lost after the fixture server accepted."""
        await submit(snapshot,request_id);raise TimeoutError()
    adapter.submit=lost
    try:
        job=await worker.create('p','unknown',{'prompt_revision':1});await worker.step(job)
        assert (await read(factory,job)).state=='submission_unknown'
        await worker.step(job);assert (await read(factory,job)).state=='running' and adapter.sent==1
        clock.advance();await worker.step(job);await worker.step(job)
        assert (await read(factory,job)).state=='ready' and adapter.sent==1
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_unreconcilable_acceptance_stays_unknown(tmp_path):
    """Absence of reconciliation information never becomes permission to resend."""
    engine,factory,adapter,clock,worker,_=await setup(tmp_path)
    async def lost(snapshot,request_id):
        """Remote acceptance cannot be established in this fixture."""
        adapter.sent+=1;raise TimeoutError()
    adapter.submit=lost
    try:
        job=await worker.create('p','lost',{});await worker.step(job)
        for _ in range(4):clock.advance();await worker.step(job)
        row=await read(factory,job)
        assert row.state=='paused' and row.pause_from=='submission_unknown' and adapter.sent==1
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_download_restart_and_revision_isolation(tmp_path):
    """Download retries retain remote ID and cannot overwrite another prompt revision."""
    engine,factory,adapter,clock,worker,_=await setup(tmp_path)
    try:
        first=await worker.create('p','v1',{'prompt_revision':1});second=await worker.create('p','v2',{'prompt_revision':2})
        await worker.step(first);clock.advance();await worker.step(first);adapter.fail_download=True
        await worker.step(first);assert (await read(factory,first)).state=='downloading'
        replacement=DurableExecution(adapter,factory=factory,clock=clock,publisher=fixture_publisher,budget_required=False)
        adapter.fail_download=False;clock.advance();await replacement.step(first)
        assert (await read(factory,first)).state=='ready' and adapter.sent==1
        assert (await read(factory,second)).state=='queued' and (await read(factory,second)).snapshot['prompt_revision']==2
        await worker.step(first);assert adapter.sent==1
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_cancel_request_is_not_confirmation(tmp_path):
    """A refusal to confirm cancellation cannot be presented as a cancelled remote task."""
    engine,factory,adapter,clock,worker,_=await setup(tmp_path)
    try:
        job=await worker.create('p','cancel',{});await worker.step(job);await worker.request_cancel(job);clock.advance()
        await worker.step(job);assert (await read(factory,job)).state!='cancelled'
        adapter.cancel_confirmed=True;await worker.step(job);assert (await read(factory,job)).state=='cancelled'
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_wait_limit_pause_resume_and_retry_limit(tmp_path):
    """Resource limits pause, preserving provider identity for a later query."""
    engine,factory,adapter,clock,worker,_=await setup(tmp_path)
    try:
        job=await worker.create('p','wait',{});await worker.step(job);clock.advance(1900)
        await worker.step(job);assert (await read(factory,job)).state=='paused'
        await worker.resume(job);adapter.fail_query=True
        for _ in range(3):clock.advance(10);await worker.step(job)
        assert (await read(factory,job)).state=='paused' and adapter.sent==1
        await worker.resume(job);adapter.fail_query=False;await worker.step(job);await worker.step(job)
        assert (await read(factory,job)).state=='ready' and adapter.sent==1
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_actual_process_death_after_acceptance(tmp_path):
    """A real worker process dies after fixture acceptance and before persisting the ID."""
    engine,factory,adapter,clock,worker,url=await setup(tmp_path)
    try:
        job=await worker.create('p','process-crash',{})
        script=tmp_path/'crashing_worker.py';remote=tmp_path/'remote-accepted.json'
        script.write_text('''import asyncio,json,os,sys
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from app.services.worker.durable_execution import DurableExecution
class CrashTransport:
 async def submit(self,snapshot,request_id):
  Path(sys.argv[3]).write_text(json.dumps({'request_id':request_id,'id':'remote-after-crash'}))
  os._exit(77)
async def main():
 e=create_async_engine(sys.argv[1]);f=async_sessionmaker(e,expire_on_commit=False)
 await DurableExecution(CrashTransport(),factory=f,clock=lambda:1000,budget_required=False).step(sys.argv[2])
asyncio.run(main())
''',encoding='utf8')
        env=os.environ.copy();env['PYTHONPATH']=str(Path(__file__).resolve().parents[1])
        result=subprocess.run([sys.executable,str(script),url,job,str(remote)],env=env,timeout=30)
        assert result.returncode==77 and (await read(factory,job)).state=='submitting'
        accepted=json.loads(remote.read_text());adapter.known[accepted['request_id']]=accepted['id'];clock.advance()
        await worker.step(job)
        assert (await read(factory,job)).remote_id=='remote-after-crash' and adapter.sent==0
        async with factory() as db:assert len((await db.execute(select(SubmissionAttempt))).scalars().all())==1
    finally:await engine.dispose()
