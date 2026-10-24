"""Atomic admission with actual SQLite files, concurrency and response-loss replay."""
import asyncio
import hashlib
from dataclasses import replace

import pytest
from fastapi import HTTPException
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from app.config import settings
from app.core.contracts.execution_capabilities import Capability
from app.core.contracts.execution_binding import binding_fingerprint
from app.models.execution import ExecutionJob, SubmissionAttempt
from app.models.execution_binding import ExecutionBinding
from app.models.execution_budget import ExecutionBudget, ExecutionBudgetEntry
from app.models.production import ProductionMedia
from app.models.studio import FileItem, Project
from app.models.task import GenerationTask
from app.services.worker.execution_admission import AdmissionPolicy, admit
from app.services.worker.execution_schema import migrate_execution

POLICY=AdmissionPolicy(Capability('trusted-model',frozenset({5}),frozenset({'image','audio','video'}),3),4,'TEST',3)
REQUEST={'model_id':'trusted-model','duration':5,'prompt':'An inert scene','prompt_revision':'v1','references':[{'media_id':'image-a','kind':'image'}]}


async def setup(tmp_path, *, limit=100, session_class=AsyncSession):
    """Create a dedicated migrated SQLite database and immutable local file fixtures."""
    engine=create_async_engine('sqlite+aiosqlite:///'+(tmp_path/'admission.db').as_posix())
    @event.listens_for(engine.sync_engine,'connect')
    def configure_sqlite(connection, _):
        """Enforce references and allow bounded concurrent writer waiting."""
        connection.execute('PRAGMA foreign_keys=ON');connection.execute('PRAGMA busy_timeout=20000')
    await migrate_execution(engine)
    factory=async_sessionmaker(engine,expire_on_commit=False)
    async with factory() as db:
        db.add_all([Project(id=p,name=p,style='test') for p in ['a','b']]);await db.flush()
        for key,project,mime in [('image-a','a','image/png'),('image-b','b','image/png'),('audio-a','a','audio/wav')]:
            data=b'local immutable fixture '+key.encode();(tmp_path/key).write_bytes(data)
            db.add(FileItem(id=key,type=mime.split('/')[0],name=key,storage_key=key));await db.flush()
            db.add(ProductionMedia(id=key,project_id=project,name=key,mime=mime,sha256=hashlib.sha256(data).hexdigest(),size=len(data),relative_path=key))
        db.add(ExecutionBudget(project_id='a',currency='TEST',limit_minor=limit,reserved_minor=0,spent_minor=0));await db.commit()
    return engine,async_sessionmaker(engine,expire_on_commit=False,class_=session_class)


async def inspect(factory):
    """Read durable row counts and held funds without relying on return values."""
    async with factory() as db:
        counts=[await db.scalar(select(func.count()).select_from(model)) for model in [GenerationTask,ExecutionJob,ExecutionBudgetEntry,SubmissionAttempt]]
        assert await db.scalar(select(func.count()).select_from(ExecutionBinding))==counts[1]
        budget=await db.get(ExecutionBudget,'a')
        return counts,budget.reserved_minor


def test_admission_commit_response_loss_replays_exactly_once(tmp_path,monkeypatch):
    """A committed job survives lost response, exhausted cap and exhausted budget."""
    monkeypatch.setattr(settings,'production_media_root',str(tmp_path))
    async def case():
        """Simulate only response loss after the service has completed its commit."""
        engine,factory=await setup(tmp_path,limit=4)
        try:
            job_id=await admit(factory,'a','stable-key',REQUEST,replace(POLICY,iteration_limit=1))
            try: raise TimeoutError('response lost after commit')
            except TimeoutError: pass
            replay=await admit(factory,'a','stable-key',dict(REQUEST),replace(POLICY,iteration_limit=1))
            assert replay==job_id
            assert await inspect(factory)==([1,1,1,0],4)
            async with factory() as db:
                job=await db.get(ExecutionJob,job_id)
                assert job.snapshot==REQUEST and job.state=='queued'
                assert (await db.get(ExecutionBudgetEntry,job_id)).state=='reserved'
            with pytest.raises(HTTPException) as conflict:
                await admit(factory,'a','stable-key',{**REQUEST,'prompt':'changed'},POLICY)
            assert conflict.value.status_code==409
            assert await inspect(factory)==([1,1,1,0],4)
        finally: await engine.dispose()
    asyncio.run(case())


def test_binding_fingerprint_changes_with_every_trusted_configuration_dimension():
    """Account/service versions, capabilities, quote and limits cannot silently rebind."""
    baseline=binding_fingerprint(POLICY)
    changed=[replace(POLICY,binding_id='another-account-v2'),replace(POLICY,quote_minor=5),replace(POLICY,currency='OTHER'),replace(POLICY,iteration_limit=4)]
    for field,value in [('model_id','other'),('durations',frozenset({5,10})),('reference_kinds',frozenset({'image'})),('max_references',2)]:
        changed.append(replace(POLICY,capability=replace(POLICY.capability,**{field:value})))
    assert all(binding_fingerprint(policy)!=baseline for policy in changed)
    assert binding_fingerprint(replace(POLICY))==baseline


def test_replay_with_changed_policy_retains_persisted_original_binding(tmp_path,monkeypatch):
    """Same-key replay returns the old job without assigning it to a new runtime."""
    monkeypatch.setattr(settings,'production_media_root',str(tmp_path))
    async def case():
        """Inspect binding and hold after replay under another trusted account/quote."""
        engine,factory=await setup(tmp_path)
        try:
            old=replace(POLICY,binding_id='service-account-v1')
            job_id=await admit(factory,'a','binding-key',REQUEST,old)
            changed=replace(old,binding_id='service-account-v2',quote_minor=8)
            assert await admit(factory,'a','binding-key',REQUEST,changed)==job_id
            retired=replace(changed,capability=replace(changed.capability,durations=frozenset({10})))
            assert await admit(factory,'a','binding-key',REQUEST,retired)==job_id
            async with factory() as db:
                row=await db.get(ExecutionBinding,job_id)
                assert row.binding_hash==binding_fingerprint(old)
                assert row.binding_hash!=binding_fingerprint(changed)
            assert await inspect(factory)==([1,1,1,0],4)
        finally: await engine.dispose()
    asyncio.run(case())


@pytest.mark.parametrize('mode',['same-key','budget','iterations'])
def test_concurrent_admission_never_exceeds_budget_or_iterations(tmp_path,monkeypatch,mode):
    """Concurrent writers deduplicate or reject atomically without partial rows."""
    monkeypatch.setattr(settings,'production_media_root',str(tmp_path))
    async def case():
        """Race eight independent SQLite sessions against one fixed trusted policy."""
        engine,factory=await setup(tmp_path,limit=10 if mode=='budget' else 100)
        policy=replace(POLICY,quote_minor=6 if mode=='budget' else 1,iteration_limit=2 if mode=='iterations' else 20)
        try:
            results=await asyncio.gather(*(admit(factory,'a','same' if mode=='same-key' else str(i),REQUEST,policy) for i in range(8)),return_exceptions=True)
            errors=[x for x in results if isinstance(x,BaseException)]
            assert all(isinstance(x,HTTPException) and x.status_code==409 for x in errors),errors
            expected=1 if mode in {'same-key','budget'} else 2
            assert len({x for x in results if isinstance(x,str)})==expected
            assert await inspect(factory)==([expected,expected,expected,0],expected*policy.quote_minor)
        finally: await engine.dispose()
    asyncio.run(case())


@pytest.mark.parametrize('failure',['unknown-quote','project','cross-project','mime','missing','corrupt','endpoint','request-price','bad-model','bad-duration','bad-prompt','duplicate-ref','malformed-kind'])
def test_preflight_and_reference_failures_leave_no_rows(tmp_path,monkeypatch,failure):
    """Unsafe or unsupported inputs cannot create tasks, entries or budget holds."""
    monkeypatch.setattr(settings,'production_media_root',str(tmp_path))
    async def case():
        """Change one validated input or owned file and assert transaction rollback."""
        engine,factory=await setup(tmp_path)
        request={**REQUEST,'references':[dict(REQUEST['references'][0])]};policy=POLICY;project='a'
        if failure=='unknown-quote': policy=replace(POLICY,quote_minor=None)
        elif failure=='project': project='missing'
        elif failure=='cross-project': request['references'][0]['media_id']='image-b'
        elif failure=='mime': request['references'][0]['media_id']='audio-a'
        elif failure=='missing': (tmp_path/'image-a').unlink()
        elif failure=='corrupt': (tmp_path/'image-a').write_bytes(b'changed')
        elif failure=='endpoint': request['endpoint']='https://untrusted.invalid'
        elif failure=='request-price': request['quote_minor']=0
        elif failure=='bad-model': request['model_id']='untrusted'
        elif failure=='bad-duration': request['duration']=True
        elif failure=='bad-prompt': request['prompt']={'command':'bad'}
        elif failure=='duplicate-ref': request['references']*=2
        elif failure=='malformed-kind': request['references'][0]['kind']=[]
        try:
            with pytest.raises(HTTPException): await admit(factory,project,'blocked',request,policy)
            assert await inspect(factory)==([0,0,0,0],0)
        finally: await engine.dispose()
    asyncio.run(case())


def test_failure_before_commit_rolls_back_all_records_and_hold(tmp_path,monkeypatch):
    """Failure after rows are flushed cannot persist a task without its budget hold."""
    monkeypatch.setattr(settings,'production_media_root',str(tmp_path))
    class FailingCommit(AsyncSession):
        """Inject a local storage failure before committing the admission transaction."""
        async def commit(self):
            """Fail after flushes while leaving rollback to the service transaction."""
            raise RuntimeError('injected commit failure')
    async def case():
        """Verify the entire transaction disappears after the injected commit failure."""
        engine,factory=await setup(tmp_path,session_class=FailingCommit)
        try:
            with pytest.raises(RuntimeError): await admit(factory,'a','failed',REQUEST,POLICY)
            assert await inspect(factory)==([0,0,0,0],0)
        finally: await engine.dispose()
    asyncio.run(case())


def test_iteration_limit_is_per_fixed_project_model_and_counts_cancelled(tmp_path,monkeypatch):
    """Cancelled work consumes its iteration; another trusted model has its own cap."""
    monkeypatch.setattr(settings,'production_media_root',str(tmp_path))
    async def case():
        """Exercise persisted model counting rather than caller supplied counters."""
        engine,factory=await setup(tmp_path)
        first=replace(POLICY,iteration_limit=1)
        try:
            key=await admit(factory,'a','first',REQUEST,first)
            async with factory() as db:
                job=await db.get(ExecutionJob,key);job.state='cancelled';await db.commit()
            with pytest.raises(HTTPException): await admit(factory,'a','second',REQUEST,first)
            second=replace(first,capability=replace(first.capability,model_id='another-trusted'))
            await admit(factory,'a','third',{**REQUEST,'model_id':'another-trusted'},second)
            assert await inspect(factory)==([2,2,2,0],8)
        finally: await engine.dispose()
    asyncio.run(case())
