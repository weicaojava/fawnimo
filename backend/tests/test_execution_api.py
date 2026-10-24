"""SQLite-backed requests through the registered application; zero provider calls."""
import asyncio
import json
import pathlib
import sys
import time
import uuid

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.main import app
from app.core.db import Base
from app.dependencies import get_db
from app.models.studio import Project
from app.models.task import GenerationTask
from app.models.execution import ExecutionJob, ExecutionSlot, SubmissionAttempt
from app.models.execution_budget import ExecutionBudget, ExecutionBudgetEntry


async def run_api_checks(run):
    engine=create_async_engine('sqlite+aiosqlite:///'+(run/'test.db').as_posix())
    factory=async_sessionmaker(engine,expire_on_commit=False)
    async with engine.begin() as c: await c.run_sync(Base.metadata.create_all)
    async def dependency():
        async with factory() as db:
            try: yield db; await db.commit()
            except Exception: await db.rollback(); raise
    app.dependency_overrides[get_db]=dependency
    states={'q':'queued','r':'running','d':'downloading','u':'submission_unknown','p':'paused','active':'running','bad':'paused','foreign':'running','done':'ready','qa':'queued'}
    async with factory() as db:
        db.add_all([Project(id=p,name=p,style='test') for p in ['a','b']]);await db.flush()
        for key,state in states.items():
            db.add(GenerationTask(id='task-'+key,mode='async_polling',task_kind='durable_execution',payload={'secret':'DO_NOT_ECHO'},status='pending'))
        await db.flush()
        for key,state in states.items():
            db.add(ExecutionJob(id=key,task_id='task-'+key,project_id='b' if key=='foreign' else 'a',request_key=key,request_hash='f'*64,snapshot={'token':'DO_NOT_ECHO'},result={'url':'https://private.invalid/SIGNED_SECRET'},remote_id=None if key in {'q','qa','u'} else 'REMOTE_SECRET',state=state,pause_from='queued' if key=='bad' else ('downloading' if key=='p' else None),reason='https://private.invalid/REASON_SECRET',lease_until=time.time()+120 if key=='active' else 0))
        await db.flush()
        db.add(SubmissionAttempt(id='attempt-qa',job_id='qa',request_hash='f'*64,state='prepared'))
        db.add(ExecutionBudget(project_id='a',currency='TEST',limit_minor=100,reserved_minor=15,spent_minor=0));await db.flush()
        db.add_all([ExecutionBudgetEntry(job_id='q',project_id='a',quote_minor=5,state='reserved'),ExecutionBudgetEntry(job_id='qa',project_id='a',quote_minor=10,state='unknown')])
        db.add(ExecutionSlot(id=1,job_id='q',token='LEASE_SECRET',lease_until=time.time()+120));await db.commit()
    base='/api/v1/execution/projects/'
    count=0
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        async def req(path,method='GET',code=200):
            nonlocal count
            res=await client.request(method,base+path)
            assert res.status_code==code,(path,res.status_code,res.text)
            assert all(secret not in res.text for secret in ['DO_NOT_ECHO','SIGNED_SECRET','REASON_SECRET','REMOTE_SECRET','LEASE_SECRET','snapshot','remote_id']),res.text
            count+=1; print(f'PASS {method} {path} HTTP {code}',flush=True)
            return res.json().get('data')
        items=await req('a/jobs');assert len(items)==9 and all(x['project_id']=='a' for x in items)
        assert len(await req('a/jobs?limit=2&offset=1'))==2
        await req('a/jobs?limit=201',code=422)
        await req('a/jobs/foreign',code=404)
        for action in ['pause','resume','cancel']: await req('a/jobs/foreign/'+action,'POST',404)
        await req('missing/jobs',code=404)
        assert (await req('a/jobs/r/pause','POST'))['state']=='paused'
        assert (await req('a/jobs/r/resume','POST'))['state']=='running'
        assert (await req('a/jobs/p/resume','POST'))['state']=='downloading'
        assert (await req('a/jobs/u/pause','POST'))['state']=='paused'
        assert (await req('a/jobs/u/resume','POST'))['state']=='submission_unknown'
        for job,action in [('r','resume'),('bad','resume'),('active','pause'),('q','pause'),('done','resume')]: await req('a/jobs/'+job+'/'+action,'POST',409)
        item=await req('a/jobs/r/cancel','POST');assert item['state']=='running' and item['cancel_requested']
        item=await req('a/jobs/q/cancel','POST');assert item['state']=='cancelled'
        assert (await req('a/jobs/q/cancel','POST'))['state']=='cancelled'
        item=await req('a/jobs/qa/cancel','POST');assert item['state']=='queued' and item['cancel_requested']
        assert (await req('a/jobs/done/cancel','POST'))['state']=='ready'
        legacy=await client.post('/api/v1/film/tasks/task-r/cancel',json={'reason':'test'})
        assert legacy.status_code==409 and '所属工程' in legacy.json()['message']
        count+=1
        print('PASS legacy cancellation refuses durable projection mutation HTTP 409',flush=True)
        await req('a/jobs','POST',405)
        await req('a/jobs/r/step','POST',404)
        schema=(await client.get('/openapi.json')).json()
        assert 'ExecutionRead' in schema['components']['schemas']
    async with factory() as db:
        slot=await db.get(ExecutionSlot,1);assert slot.job_id is None and slot.token is None and slot.lease_until==0
        assert (await db.get(ExecutionJob,'r')).remote_id=='REMOTE_SECRET'
        assert (await db.get(ExecutionJob,'p')).result['url'].endswith('SIGNED_SECRET')
        assert (await db.scalar(select(func.count()).select_from(SubmissionAttempt)))==1
        assert (await db.scalar(select(func.count()).select_from(ExecutionJob)))==10
        assert (await db.get(ExecutionBudget,'a')).reserved_minor==10
        assert (await db.get(ExecutionBudgetEntry,'q')).state=='released'
        assert (await db.get(ExecutionBudgetEntry,'qa')).state=='unknown'
        assert (await db.get(GenerationTask,'task-q')).status=='cancelled'
        assert (await db.get(GenerationTask,'task-r')).status=='running'
        assert (await db.get(GenerationTask,'task-r')).cancel_requested is True
        assert (await db.get(GenerationTask,'task-p')).result['execution_state']=='downloading'
    app.dependency_overrides.clear();await engine.dispose()
    print(json.dumps({'status':'PASSED','api_checks':count,'sqlite':str(run/'test.db'),'model_calls':0,'assertions':['project isolation','safe field allowlist','bounded list','pause/resume original state','active lease rejection','cancel intent vs queued terminal','atomic slot and unsubmitted budget release','unknown budget retained','legacy cancellation refused','task projection synchronization','no new attempt or job','no public submit or worker route']}),flush=True)


def test_execution_api(tmp_path):
    asyncio.run(run_api_checks(tmp_path))
