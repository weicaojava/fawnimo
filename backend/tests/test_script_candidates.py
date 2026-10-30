"""Actual authored HTTP, project fee holds and explicit script adoption; no supplier/model qualification."""
import asyncio
from contextlib import asynccontextmanager,contextmanager
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import threading
import pytest
import httpx
from fastapi import FastAPI
from sqlalchemy import func,select,text,update
from sqlalchemy.exc import IntegrityError
from app.api.script_candidates import router,candidate_factory
from app.api.v1.routes.agent_execution import router as agent_router
from app.dependencies import get_db
from app.models.agent_execution import AgentRun
from app.models.chapter_text_history import ChapterTextRevision
from app.models.execution_budget import ExecutionBudget,ExecutionBudgetEntry
from app.models.model_charge import ModelCallCharge
from app.models.script_candidate import ScriptCandidate
from app.models.studio import Chapter,Shot,ShotDetail,ShotExtractedCandidate,ShotExtractedDialogueCandidate,Character
from app.services.script_preparation.candidate_schema import migrate_script_candidates
from app.services.studio.chapter_text_history import chapter_for_edit,save_fields
from app.services.worker.model_prices import ModelPriceRegistry
from tests.test_agent_execution import FIXTURE_KEY,fixture_price,isolated_client

SOURCE='小鹿推开木门。\n小鹿说：我回来了。'
ROOT='/api/v1/script-preparation/projects/fixture-project/chapters/chapter-a/candidates'


@contextmanager
def authored_model(output):
    """Return chosen JSON through the actual compatible text transport, without external accounts."""
    state={'calls':[],'output':output,'received':threading.Event(),'release':threading.Event(),'block':False}
    class Handler(BaseHTTPRequestHandler):
        """Record authored requests without headers or secrets and expose a controllable late result."""
        def log_message(self,*_):
            """Keep fixture payloads and HTTP logs out of test output."""
        def do_POST(self):
            """Produce one real provider-shaped response with an independent private request ID."""
            payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state['calls'].append({'path':self.path,'body':payload,'authenticated':self.headers.get('Authorization')=='Bearer '+FIXTURE_KEY})
            state['received'].set()
            if state['block']:state['release'].wait(5)
            data=json.dumps({'choices':[{'message':{'content':state['output']}}],'usage':{'total_tokens':12}},ensure_ascii=False).encode()
            try:
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)))
                self.send_header('X-Request-Id','authored-script-'+str(len(state['calls'])));self.end_headers();self.wfile.write(data)
            except (ConnectionError,OSError):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();state['port']=server.server_port
    try:yield state
    finally:state['release'].set();server.shutdown();server.server_close();thread.join(timeout=2)


@asynccontextmanager
async def script_client(tmp_path,server,*,automatic=True):
    """Create two new projects, their chapters and a fully migrated private proposal journal."""
    async with isolated_client(tmp_path,server) as (_,factory,runtimes):
        await migrate_script_candidates(factory.kw['bind'])
        async with factory() as db:
            db.add_all([Chapter(id='chapter-a',project_id='fixture-project',index=1,title='Authored A',raw_text=SOURCE),
                Chapter(id='chapter-b',project_id='other-project',index=1,title='Authored B',raw_text='Other authored project')])
            await db.commit()
        app=FastAPI();app.include_router(router,prefix='/api/v1');app.include_router(agent_router,prefix='/api/v1')
        app.dependency_overrides[candidate_factory]=lambda:factory
        async def database():
            """Keep generic old cancellation/retry routes on the same private database."""
            async with factory() as db:
                try:yield db;await db.commit()
                except BaseException:await db.rollback();raise
        app.dependency_overrides[get_db]=database;app.state.agent_execution_runtime=runtimes[0]
        if not automatic:
            await runtimes[0].stop()
            # Admission is ready, but this fixture explicitly controls dispatch with tick().
            runtimes[0].running=True
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://authored-app') as client:
            yield client,factory,runtimes[0],app


async def body_for(client,operation='optimize',**extra):
    """Read actual saved-source and price fingerprints instead of inventing client fee authority."""
    query={'operation':operation,**({'shot_id':extra['shot_id']} if 'shot_id' in extra else {})}
    response=await client.get(ROOT+'/availability',params=query);assert response.status_code==200,response.text
    source=response.json()['data']
    return {'request_key':'original-'+operation,'operation':operation,'source_field':'raw_text',
        'expected_source_revision':source['source_revision'],'expected_source_hash':source['source_hash'],
        'model_id':'fixture-model','expected_price_fingerprint':fixture_price().fingerprint,'confirm_model_call':True,**extra}


async def terminal(client,identifier):
    """Wait for actual durable completion, never substitute an authored terminal database state."""
    for _ in range(200):
        response=await client.get(ROOT+'/'+identifier);assert response.status_code==200,response.text
        result=response.json()['data']
        if result['status'] in ('succeeded','failed','cancelled'):return result
        await asyncio.sleep(.05)
    raise AssertionError('Script fixture did not finish')


def decision(result):
    """Name exactly the source and validated output the user reviewed."""
    return {'expected_source_revision':result['source_revision'],'expected_source_hash':result['source_hash'],'expected_output_hash':result['output_hash']}


@pytest.mark.asyncio
@pytest.mark.parametrize('operation,field',[('optimize','raw_text'),('simplify','condensed_text')])
async def test_text_proposal_does_not_write_until_review_and_adopts_once_with_history(tmp_path,operation,field):
    """One original-project request survives refresh/full cap; explicit adoption creates recoverable text history once."""
    proposal='小鹿推门而入，说：我回来了。'
    with authored_model(json.dumps({'text':proposal,'summary':'Authored fixture only'},ensure_ascii=False)) as server:
        async with script_client(tmp_path,server) as (client,factory,runtime,app):
            body=await body_for(client,operation)
            first=await client.post(ROOT,json=body);assert first.status_code==201,first.text
            identifier=first.json()['data']['id'];result=await terminal(client,identifier)
            assert result['status']=='succeeded' and result['can_adopt'] and result['result']['text']==proposal
            async with factory() as db:
                chapter=await db.get(Chapter,'chapter-a');assert chapter.raw_text==SOURCE and chapter.condensed_text==''
                assert await db.scalar(select(func.count()).select_from(ChapterTextRevision))==0
                assert (await db.get(ExecutionBudget,'fixture-project')).reserved_minor==10
                assert (await db.get(ExecutionBudget,'other-project')).reserved_minor==0
                assert (await db.get(ModelCallCharge,await db.scalar(select(ModelCallCharge.id)))).dispatch_state=='accepted'
                await db.execute(update(ExecutionBudget).where(ExecutionBudget.project_id=='fixture-project').values(limit_minor=10));await db.commit()
            runtime.price_registry=ModelPriceRegistry();app.state.agent_execution_runtime=None
            assert (await client.post(ROOT,json=body)).json()['data']['id']==identifier
            adopted=await client.post(ROOT+'/'+identifier+'/adopt',json=decision(result));assert adopted.status_code==200,adopted.text
            assert (await client.post(ROOT+'/'+identifier+'/adopt',json=decision(result))).status_code==200
            async with factory() as db:
                chapter=await db.get(Chapter,'chapter-a');assert getattr(chapter,field)==proposal
                rows=(await db.scalars(select(ChapterTextRevision).order_by(ChapterTextRevision.revision))).all()
                assert len(rows)==2 and rows[0].raw_text==SOURCE and rows[1].origin=='ai_'+operation
                assert (await db.get(ExecutionBudget,'fixture-project')).reserved_minor==10
                assert (await db.get(Chapter,'chapter-b')).raw_text=='Other authored project'
            assert len(server['calls'])==1 and server['calls'][0]['authenticated']


@pytest.mark.asyncio
async def test_division_is_reviewed_owned_and_idempotent_without_overwriting_existing_shots(tmp_path):
    """The model cannot allocate shot IDs or write until the exact reviewed complete division is accepted."""
    output={'shots':[{'title':'进门','script_excerpt':'小鹿推开木门。'},{'title':'说话','script_excerpt':'小鹿说：我回来了。'}]}
    with authored_model(json.dumps(output,ensure_ascii=False)) as server:
        async with script_client(tmp_path,server) as (client,factory,_,__):
            body=await body_for(client,'divide');response=await client.post(ROOT,json=body);assert response.status_code==201,response.text
            identifier=response.json()['data']['id'];result=await terminal(client,identifier);assert result['can_adopt']
            async with factory() as db:assert await db.scalar(select(func.count()).select_from(Shot))==0
            wrong=ROOT.replace('chapter-a','chapter-b')+'/'+identifier
            assert (await client.get(wrong)).status_code==404
            assert (await client.post(wrong+'/adopt',json=decision(result))).status_code==404
            for _ in range(2):
                response=await client.post(ROOT+'/'+identifier+'/adopt',json=decision(result));assert response.status_code==200,response.text
            async with factory() as db:
                rows=(await db.scalars(select(Shot).order_by(Shot.index))).all()
                assert [row.script_excerpt for row in rows]==[item['script_excerpt'] for item in output['shots']]
                assert all(row.chapter_id=='chapter-a' for row in rows)
                assert await db.scalar(select(func.count()).select_from(ShotDetail))==2
            assert (await client.post(ROOT,json=body)).json()['data']['id']==identifier
            assert (await client.post('/api/v1/agent-execution/runs/'+identifier+'/retry',json={
                'request_key':'bypass','expected_price_fingerprint':fixture_price().fingerprint,'acknowledge_duplicate_charge':True})).status_code==409
            assert len(server['calls'])==1


@pytest.mark.asyncio
async def test_saved_source_change_before_dispatch_releases_unsent_hold_without_http(tmp_path):
    """A real queued fee cannot spend on an original source the user has replaced before dispatch."""
    with authored_model('{"text":"unused"}') as server:
        async with script_client(tmp_path,server,automatic=False) as (client,factory,runtime,_):
            body=await body_for(client);response=await client.post(ROOT,json=body);assert response.status_code==201,response.text
            identifier=response.json()['data']['id']
            async with factory() as db:
                chapter=await chapter_for_edit(db,'chapter-a');await save_fields(db,chapter,{'raw_text':'User saved newer text'},expected_revision=0);await db.commit()
            await runtime.tick();result=await terminal(client,identifier)
            assert result['status']=='failed' and not result['can_adopt'] and not server['calls']
            async with factory() as db:
                assert (await db.get(ExecutionBudget,'fixture-project')).reserved_minor==0
                assert (await db.scalar(select(ModelCallCharge))).dispatch_state=='not_sent'


@pytest.mark.asyncio
async def test_newer_manual_text_blocks_old_result_adoption_and_never_replaces_unsaved_body(tmp_path):
    """Result confirmation is a CAS against server text; supplied replacement text is rejected as an extra field."""
    with authored_model('{"text":"old proposal"}') as server:
        async with script_client(tmp_path,server) as (client,factory,_,__):
            response=await client.post(ROOT,json=await body_for(client));identifier=response.json()['data']['id']
            result=await terminal(client,identifier);assert result['can_adopt']
            assert (await client.post(ROOT+'/'+identifier+'/adopt',json={**decision(result),'text':'injected'})).status_code==422
            async with factory() as db:
                chapter=await chapter_for_edit(db,'chapter-a');await save_fields(db,chapter,{'raw_text':'Later human edit'},expected_revision=0);await db.commit()
            assert (await client.post(ROOT+'/'+identifier+'/adopt',json=decision(result))).status_code==409
            assert not (await client.get(ROOT+'/'+identifier)).json()['data']['can_adopt']
            async with factory() as db:assert (await db.get(Chapter,'chapter-a')).raw_text=='Later human edit'
            assert len(server['calls'])==1


@pytest.mark.asyncio
@pytest.mark.parametrize('output',[
    '{"text":"first","text":"hidden duplicate"}',
    '{"text":"proposal","file_id":"foreign"}',
    '{"text":"proposal","summary":NaN}',
    '```json\n{"text":"proposal"}\n```',
])
async def test_malformed_proposal_is_not_adoptable_and_does_not_refund_sent_call(tmp_path,output):
    """Invalid model output remains actual paid-attempt evidence; it never becomes a fake successful chapter change."""
    with authored_model(output) as server:
        async with script_client(tmp_path,server) as (client,factory,_,__):
            response=await client.post(ROOT,json=await body_for(client));result=await terminal(client,response.json()['data']['id'])
            assert result['status']=='succeeded' and result['result'] is None and not result['can_adopt']
            async with factory() as db:
                assert (await db.get(Chapter,'chapter-a')).raw_text==SOURCE
                assert (await db.get(ExecutionBudget,'fixture-project')).reserved_minor==10
            assert len(server['calls'])==1


@pytest.mark.asyncio
async def test_extraction_creates_only_pending_original_shot_candidates_after_review(tmp_path):
    """Name-only model output cannot allocate assets; the established per-candidate human confirmation remains required."""
    output={'characters':[{'name':'小鹿','description':'原文角色'}],'scenes':[],'props':[],'costumes':[],
        'dialogues':[{'text':'我回来了。','speaker_name':'小鹿','line_mode':'DIALOGUE'}]}
    with authored_model(json.dumps(output,ensure_ascii=False)) as server:
        async with script_client(tmp_path,server) as (client,factory,_,__):
            async with factory() as db:
                db.add(Shot(id='shot-a',chapter_id='chapter-a',index=1,title='回家',script_excerpt=SOURCE));await db.commit()
            response=await client.post(ROOT,json=await body_for(client,'extract_shot',shot_id='shot-a'))
            assert response.status_code==201,response.text
            result=await terminal(client,response.json()['data']['id']);assert result['can_adopt'],result
            async with factory() as db:
                assert await db.scalar(select(func.count()).select_from(ShotExtractedCandidate))==0
                assert await db.scalar(select(func.count()).select_from(Character))==0
            response=await client.post(ROOT+'/'+result['id']+'/adopt',json=decision(result));assert response.status_code==200,response.text
            async with factory() as db:
                assets=(await db.scalars(select(ShotExtractedCandidate))).all();lines=(await db.scalars(select(ShotExtractedDialogueCandidate))).all()
                assert len(assets)==len(lines)==1 and assets[0].shot_id==lines[0].shot_id=='shot-a'
                assert assets[0].candidate_status=='pending' and lines[0].candidate_status=='pending'
                assert await db.scalar(select(func.count()).select_from(Character))==0
            assert len(server['calls'])==1


@pytest.mark.asyncio
async def test_cancelled_inflight_output_is_never_adopted_and_unknown_fee_is_retained(tmp_path):
    """Dismissal cancels actual HTTP work and rejects its late response without inferring a refund."""
    with authored_model('{"text":"late result"}') as server:
        server['block']=True
        async with script_client(tmp_path,server) as (client,factory,_,__):
            response=await client.post(ROOT,json=await body_for(client));identifier=response.json()['data']['id']
            assert await asyncio.to_thread(server['received'].wait,4)
            response=await client.post(ROOT+'/'+identifier+'/dismiss');assert response.status_code==200,response.text
            server['release'].set();await asyncio.sleep(.2)
            result=await terminal(client,identifier);assert result['status']=='cancelled' and result['decision']=='dismissed' and not result['can_adopt']
            async with factory() as db:
                assert (await db.get(Chapter,'chapter-a')).raw_text==SOURCE
                assert (await db.get(ExecutionBudget,'fixture-project')).reserved_minor==10
            assert len(server['calls'])==1


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid',['boolean','source','price','budget'])
async def test_new_calls_require_exact_confirmation_source_price_and_only_their_project_budget(tmp_path,invalid):
    """No rejected confirmation or insufficient project can leave a partial job/hold or borrow the other project's money."""
    with authored_model('{"text":"unused"}') as server:
        async with script_client(tmp_path,server,automatic=False) as (client,factory,_,__):
            body=await body_for(client)
            if invalid=='boolean':body['confirm_model_call']=1
            if invalid=='source':body['expected_source_hash']='f'*64
            if invalid=='price':body['expected_price_fingerprint']='f'*64
            if invalid=='budget':
                async with factory() as db:
                    await db.execute(update(ExecutionBudget).where(ExecutionBudget.project_id=='fixture-project').values(limit_minor=9));await db.commit()
            response=await client.post(ROOT,json=body)
            assert response.status_code==(422 if invalid=='boolean' else 409),response.text
            async with factory() as db:
                assert await db.scalar(select(func.count()).select_from(AgentRun))==0
                assert await db.scalar(select(func.count()).select_from(ScriptCandidate))==0
                assert await db.scalar(select(func.count()).select_from(ModelCallCharge))==0
                assert (await db.get(ExecutionBudget,'fixture-project')).reserved_minor==0
                assert (await db.get(ExecutionBudget,'other-project')).reserved_minor==0
            assert not server['calls']


@pytest.mark.asyncio
async def test_sql_cannot_rewrite_candidate_or_run_identity_and_dismissal_is_final(tmp_path):
    """The immutable source, its real text run and one-way human decision survive direct native maintenance attempts."""
    with authored_model('{"text":"unused"}') as server:
        async with script_client(tmp_path,server,automatic=False) as (client,factory,_,__):
            response=await client.post(ROOT,json=await body_for(client));assert response.status_code==201,response.text
            identifier=response.json()['data']['id']
            for statement in ("UPDATE script_candidates SET source_hash='changed' WHERE id=:id",
                    'DELETE FROM script_candidates WHERE id=:id',
                    "UPDATE agent_execution_runs SET input_text='new source' WHERE id=:id",
                    "UPDATE agent_execution_runs SET kind='workflow' WHERE id=:id",
                    "UPDATE script_candidates SET decision='adopted',adopted_output_hash=NULL WHERE id=:id"):
                with pytest.raises(IntegrityError):
                    async with factory() as db:
                        await db.execute(text(statement),{'id':identifier});await db.commit()
            response=await client.post(ROOT+'/'+identifier+'/dismiss');assert response.status_code==200,response.text
            assert (await client.post(ROOT+'/'+identifier+'/dismiss')).status_code==200
            with pytest.raises(IntegrityError):
                async with factory() as db:
                    await db.execute(text("UPDATE script_candidates SET decision='pending' WHERE id=:id"),{'id':identifier});await db.commit()
            async with factory() as db:assert (await db.get(ExecutionBudget,'fixture-project')).reserved_minor==0
            assert not server['calls']
