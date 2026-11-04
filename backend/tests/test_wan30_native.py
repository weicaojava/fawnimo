"""Actual project accounting and immutable Wan image inputs using authored SQLite/media/HTTP facts only."""
from contextlib import asynccontextmanager
from io import BytesIO
import asyncio
import hashlib
import json
from types import SimpleNamespace

from fastapi import HTTPException
from PIL import Image
import pytest
from sqlalchemy import select,text,update
from sqlalchemy.exc import IntegrityError,OperationalError

from app.config import settings
from app.core.contracts.model_accounting import ModelPriceConfiguration,ModelPriceRule
from app.core.contracts.model_accounting import BillingStatementContext
from app.core.contracts.video_runtime_setup import VideoRuntimeConfiguration
from app.core.contracts.wan30 import MODEL_ID,TEXT_PROTOCOL,REFERENCE_PROTOCOL
from app.core.integrations.trusted_http import HttpResult
from app.core.integrations.wan30 import Wan30DurableTransport
from app.models.execution import ExecutionJob
from app.models.execution_budget import ExecutionBudgetEntry
from app.models.llm import Model,Provider
from app.models.model_charge import ModelCallCharge
from app.models.production import ProductionMedia
from app.models.studio import FileItem
from app.models.wan30_reference_inputs import Wan30ReferenceInputSet
from app.services.llm.connection_versions import connection_version
from app.services.worker.durable_execution import DurableExecution
from app.services.worker.execution_schema import migrate_execution
from app.services.worker.model_billing_schema import migrate_model_billing
from app.services.worker.model_prices import ModelPriceRegistry
from app.services.worker.model_billing import import_statement,reconcile_statement
from app.services.worker.native_video_accounting import NativeVideoAccounting
from app.services.worker.native_video_charge_schema import migrate_native_video_charges
from app.services.worker.video_reference_schema import migrate_video_reference_inputs
from app.services.worker.video_reference_inputs import prepare_saved_video_inputs,saved_video_input_set
from app.services.worker.wan30_reference_schema import migrate_wan30_reference_inputs,require_wan30_input_schema
from app.services.worker.workspace_video_runtime import build_managed_video_runtime
from tests.test_durable_execution import Clock
from tests.test_model_charges import ACTOR
from tests.test_native_video_model_connections import setup as model_setup
from tests.test_native_video_project_accounting import counters


class WanResponses:
    """Observe method counts and outgoing fixed parameters without a socket or retained credentials."""
    def __init__(self):
        """Keep only authored task state and bounded public request facts."""
        self.calls=[];self.bodies=[];self.status='RUNNING';self.error=False

    async def request(self,_url,**options):
        """Supply a JSON-body billing witness distinct from the task identifier."""
        method=options.get('method','GET');self.calls.append(method)
        if method=='POST':
            self.bodies.append(json.loads(options['data']))
            payload={'request_id':'authored-wan-bill','output':{'task_id':'authored-wan-task','task_status':'PENDING'}}
            return HttpResult(500 if self.error else 200,{},json.dumps(payload).encode())
        return HttpResult(200,{},json.dumps({'output':{'task_id':'authored-wan-task','task_status':self.status}}).encode())


@asynccontextmanager
async def owned_native(tmp_path,monkeypatch,*,reference=False):
    """Compose real migrations, two independent project ledgers and one explicitly priced Wan model, never a supplier account."""
    engine,factory,old_quote,_old_registry=await model_setup(tmp_path)
    try:
        for migrate in (migrate_execution,migrate_model_billing,migrate_native_video_charges,
                        migrate_video_reference_inputs,migrate_wan30_reference_inputs):await migrate(engine)
        protocol=REFERENCE_PROTOCOL if reference else TEXT_PROTOCOL
        async with factory() as db:
            await db.execute(update(Provider).where(Provider.id=='p').values(name='aliyun_bailian',
                base_url='https://authored-space.cn-beijing.maas.aliyuncs.com/api/v1'))
            await db.execute(update(Model).where(Model.id=='m').values(name=MODEL_ID,params={'native_video_protocol':protocol}))
            await db.commit()
        async with factory() as db:version=await connection_version(db,'p')
        quote=old_quote.model_copy(update={'model_name':MODEL_ID,'provider_connection_version':version,
            'input_protocol':REFERENCE_PROTOCOL if reference else None})
        registry=ModelPriceRegistry(ModelPriceConfiguration(enabled=True,rules=[ModelPriceRule(kinds=['native_video'],quote=quote)]))
        responses=WanResponses();clock=Clock()
        def transport(config,**options):
            """Inject authored HTTP facts at server composition while keeping the production durable transport implementation."""
            return Wan30DurableTransport(config,requester=responses.request,**options)
        config=VideoRuntimeConfiguration(enabled=True,model_id='m',service_version='authored-wan-native-v1')
        accountant=NativeVideoAccounting(factory,config,registry,transport_factory=transport)
        async with factory() as db:_connection,_quote,_profile,policy=await accountant.current(db)
        accountant.allow_new_post=True
        worker=DurableExecution(None,factory=factory,clock=clock,native_accounting=accountant)
        runtime=SimpleNamespace(worker=worker,policies={MODEL_ID:policy},allow_submission=True,running=True)
        snapshot={'model_id':MODEL_ID,'duration':4,'prompt':'Authored geometric native fixture','references':[],'prompt_revision':'1'}
        content=None
        if reference:
            root=tmp_path/'owned-images';root.mkdir();monkeypatch.setattr(settings,'production_media_root',str(root))
            buffer=BytesIO();Image.new('RGB',(360,640),'navy').save(buffer,format='PNG');content=buffer.getvalue()
            (root/'authored.png').write_bytes(content);sha=hashlib.sha256(content).hexdigest()
            async with factory() as db:
                db.add(FileItem(id='wan-image',type='image',name='Authored image',storage_key='local-production/authored.png'))
                await db.flush()
                db.add(ProductionMedia(id='wan-image',project_id='a',name='Authored image',mime='image/png',
                    sha256=sha,size=len(content),relative_path='authored.png'));await db.commit()
            snapshot.update(references=[{'media_id':'wan-image','kind':'image'},{'media_id':'wan-image','kind':'image'}],
                video_reference={'schema_version':1,'mode':'first_last_frames','references':[
                    {'media_id':'wan-image','expected_sha256':sha,'role':role} for role in ('first_frame','last_frame')]})
        yield SimpleNamespace(engine=engine,factory=factory,quote=quote,accountant=accountant,worker=worker,runtime=runtime,
            responses=responses,clock=clock,selection={'kind':'direct','snapshot':snapshot},content=content,config=config)
    finally:await engine.dispose()


async def accept(fixture,*,key='authored-wan-intent',project='a',selection=None):
    """Use the actual native confirmation/admission route with the exact trusted price fingerprint."""
    return await fixture.accountant.admit(project,key,selection or fixture.selection,submitted_by=ACTOR,confirmed=True,
        price_fingerprint=fixture.quote.fingerprint,runtime=fixture.runtime)


@pytest.mark.asyncio
async def test_wan_text_concurrent_exact_replay_and_project_budget_never_borrow(tmp_path,monkeypatch):
    """Identical intent reserves once; distinct work cannot exceed this project's limit or consume the other project's funds."""
    async with owned_native(tmp_path,monkeypatch) as f:
        receipts=await asyncio.gather(*(accept(f) for _ in range(4)))
        assert all(receipt==receipts[0] for receipt in receipts)
        for index in range(3):await accept(f,key='other-'+str(index))
        with pytest.raises(HTTPException) as rejected:await accept(f,key='over-limit')
        assert rejected.value.status_code==409
        assert await counters(f.factory)==(100,0,4,4) and await counters(f.factory,'b')==(0,0,0,0)
        await accept(f,project='b');assert await counters(f.factory,'b')==(25,0,1,1)
        assert f.responses.calls==[]


@pytest.mark.asyncio
async def test_wan_json_body_witness_and_original_account_recovery(tmp_path,monkeypatch):
    """A successful POST records one real synthetic body witness; rotating the saved key blocks all further original-account reads."""
    async with owned_native(tmp_path,monkeypatch) as f:
        identifier=(await accept(f))['job_ids'][0];await f.worker.step(identifier)
        assert f.responses.calls==['POST']
        async with f.factory() as db:
            row=await db.get(ExecutionBudgetEntry,identifier)
            assert row.state in ('reserved','unknown')
            charge=await db.get(ModelCallCharge,identifier)
            assert charge.provider_request_id=='authored-wan-bill' and charge.dispatch_state=='accepted'
            assert f.responses.bodies[0]['model']==MODEL_ID and f.responses.bodies[0]['parameters']['audio'] is False
            await db.execute(update(Provider).where(Provider.id=='p').values(api_key='rotated-authored-key'));await db.commit()
        f.clock.advance();await f.worker.step(identifier)
        assert f.responses.calls==['POST'] and await counters(f.factory)==(25,0,1,1)
        async with f.factory() as db:assert (await db.get(ExecutionJob,identifier)).reason=='native_original_connection_unavailable'


@pytest.mark.asyncio
async def test_wan_error_with_witness_never_reposts_or_refunds(tmp_path,monkeypatch):
    """A bounded HTTP error after dispatch remains uncertain with one original hold and cannot be treated as a free retry."""
    async with owned_native(tmp_path,monkeypatch) as f:
        f.responses.error=True;identifier=(await accept(f))['job_ids'][0]
        await f.worker.step(identifier);f.clock.advance();await f.worker.step(identifier)
        assert f.responses.calls==['POST'] and await counters(f.factory)==(25,0,1,1)
        async with f.factory() as db:assert (await db.get(ExecutionBudgetEntry,identifier)).state=='unknown'


@pytest.mark.asyncio
async def test_wan_image_seal_survives_reopen_and_preserves_seedance_tables(tmp_path,monkeypatch):
    """The actual two-frame intent and exact bytes remain in separate immutable tables, leaving the original schema untouched."""
    async with owned_native(tmp_path,monkeypatch,reference=True) as f:
        async with f.factory() as db:
            before=(await db.execute(text("SELECT name,sql FROM sqlite_master WHERE name LIKE 'video_reference_%' ORDER BY name"))).all()
        identifier=(await accept(f))['job_ids'][0]
        async with f.factory() as db:
            header=await db.get(Wan30ReferenceInputSet,identifier)
            assert header.sealed and header.protocol==REFERENCE_PROTOCOL and header.reference_count==2
            job=await db.get(ExecutionJob,identifier);selection,fields=await saved_video_input_set(db,job)
            assert [row['role'] for row in fields]==['first_frame','last_frame']
            assert 'WITHOUT ROWID' in await db.scalar(text("SELECT sql FROM sqlite_master WHERE name='wan30_reference_input_sets'"))
            assert not (await db.execute(text('PRAGMA foreign_key_check'))).first()
        saved=await prepare_saved_video_inputs(f.factory,job)
        assert saved.selection==selection and all(row.content==f.content for row in saved.references)
        await migrate_wan30_reference_inputs(f.engine)
        async with f.factory() as db:
            assert (await db.execute(text("SELECT name,sql FROM sqlite_master WHERE name LIKE 'video_reference_%' ORDER BY name"))).all()==before
        await f.worker.step(identifier)
        assert f.responses.calls==['POST'] and await counters(f.factory)==(25,0,1,1)


@pytest.mark.asyncio
@pytest.mark.parametrize('operation',['update-header','delete-header','replace-header','update-item','delete-item','append-item','replace-item','rowid'])
async def test_wan_sealed_input_direct_sql_cannot_rewrite_original_conditions(tmp_path,monkeypatch,operation):
    """Exercise actual SQLite guards, including replacement and the absence of an alias rowid, against an admitted project hold."""
    async with owned_native(tmp_path,monkeypatch,reference=True) as f:
        identifier=(await accept(f))['job_ids'][0]
        statements={
            'update-header':"UPDATE wan30_reference_input_sets SET protocol='seedance25_reference_v1' WHERE job_id=:job",
            'delete-header':'DELETE FROM wan30_reference_input_sets WHERE job_id=:job',
            'replace-header':'INSERT OR REPLACE INTO wan30_reference_input_sets SELECT * FROM wan30_reference_input_sets WHERE job_id=:job',
            'update-item':"UPDATE wan30_reference_inputs SET role='reference_image' WHERE job_id=:job",
            'delete-item':'DELETE FROM wan30_reference_inputs WHERE job_id=:job',
            'append-item':'INSERT INTO wan30_reference_inputs SELECT job_id,2,project_id,media_id,role,sha256,byte_size,mime,relative_path,width,height,duration_us,frames,fps,frame_clock_sha256,has_audio,audio_samples,audio_channels FROM wan30_reference_inputs WHERE job_id=:job AND position=0',
            'replace-item':'INSERT OR REPLACE INTO wan30_reference_inputs SELECT * FROM wan30_reference_inputs WHERE job_id=:job',
            'rowid':'UPDATE wan30_reference_input_sets SET rowid=99 WHERE job_id=:job',
        }
        async with f.factory() as db:
            with pytest.raises((IntegrityError,OperationalError)):await db.execute(text(statements[operation]),{'job':identifier})
            await db.rollback()
        async with f.factory() as db:await saved_video_input_set(db,await db.get(ExecutionJob,identifier))
        assert await counters(f.factory)==(25,0,1,1) and f.responses.calls==[]


@pytest.mark.asyncio
async def test_wan_missing_guard_refuses_admission_and_no_partial_hold(tmp_path,monkeypatch):
    """A redirected guard cannot be repaired silently by an admission reader or grant new image authority."""
    async with owned_native(tmp_path,monkeypatch,reference=True) as f:
        async with f.engine.begin() as db:
            await db.execute(text('DROP TRIGGER wan30_reference_item_no_update'))
            await db.execute(text('CREATE TRIGGER wan30_reference_item_no_update BEFORE UPDATE ON wan30_reference_inputs BEGIN SELECT 1; END'))
        with pytest.raises(RuntimeError):await migrate_wan30_reference_inputs(f.engine)
        with pytest.raises(HTTPException) as rejected:await accept(f)
        assert rejected.value.status_code==409
        assert await counters(f.factory)==(0,0,0,0) and f.responses.calls==[]


@pytest.mark.asyncio
async def test_wan_foreign_project_reference_rejects_without_hold(tmp_path,monkeypatch):
    """Valid image bytes owned by A do not become B's conditions, even with B's separate available budget."""
    async with owned_native(tmp_path,monkeypatch,reference=True) as f:
        with pytest.raises(HTTPException) as rejected:await accept(f,project='b')
        assert rejected.value.status_code==404
        assert await counters(f.factory)==(0,0,0,0) and await counters(f.factory,'b')==(0,0,0,0)


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled',[True,False])
async def test_wan_managed_setup_declares_exact_model_and_image_only_profile(tmp_path,monkeypatch,enabled):
    """Both enabled and disabled setup views identify Wan; neither receives Seedance's reference-video authority."""
    async with owned_native(tmp_path,monkeypatch,reference=True) as f:
        path=tmp_path/'authored-config.json';path.write_text(f.config.model_copy(update={'enabled':enabled}).model_dump_json(),'utf-8')
        runtime,view=await build_managed_video_runtime(f.factory,str(path),f.accountant.registry)
        assert view['model_id']==MODEL_ID and view['reference_protocol']==REFERENCE_PROTOCOL
        assert view['reference_kinds']==['image'] and not view['reference_video_delivery_available']
        assert runtime.allow_submission is enabled and not runtime.running
        assert set(runtime.policies)==({MODEL_ID} if enabled else set()) and f.responses.calls==[]


@pytest.mark.asyncio
async def test_wan_transparent_image_is_rejected_before_financial_admission(tmp_path,monkeypatch):
    """A valid project-owned PNG outside this vendor subset cannot leave a new job, input seal or hold."""
    async with owned_native(tmp_path,monkeypatch,reference=True) as f:
        image=BytesIO();Image.new('RGBA',(360,640),(20,40,60,120)).save(image,format='PNG');raw=image.getvalue()
        (tmp_path/'owned-images/authored.png').write_bytes(raw);sha=hashlib.sha256(raw).hexdigest()
        async with f.factory() as db:
            await db.execute(update(ProductionMedia).where(ProductionMedia.id=='wan-image').values(sha256=sha,size=len(raw)));await db.commit()
        for reference in f.selection['snapshot']['video_reference']['references']:reference['expected_sha256']=sha
        with pytest.raises(HTTPException) as rejected:await accept(f)
        assert rejected.value.status_code==422
        assert await counters(f.factory)==(0,0,0,0) and f.responses.calls==[]


@pytest.mark.asyncio
@pytest.mark.parametrize('reference',[False,True],ids=['text','images'])
async def test_wan_unsent_cancel_releases_original_hold_once_without_provider_io(tmp_path,monkeypatch,reference):
    """Cancel before POST returns the sole project hold; a second cancellation cannot issue a remote request or another refund."""
    async with owned_native(tmp_path,monkeypatch,reference=reference) as f:
        identifier=(await accept(f))['job_ids'][0]
        await f.worker.request_cancel(identifier);await f.worker.step(identifier)
        await f.worker.request_cancel(identifier);f.clock.advance();await f.worker.step(identifier)
        assert await counters(f.factory)==(0,0,1,1) and f.responses.calls==[]
        async with f.factory() as db:
            assert (await db.get(ExecutionBudgetEntry,identifier)).state=='released'
            assert (await db.get(ModelCallCharge,identifier)).dispatch_state=='not_sent'


@pytest.mark.asyncio
async def test_wan_original_body_witness_matches_one_authored_bill_without_settling_from_job_state(tmp_path,monkeypatch):
    """Only an explicitly imported matching account statement can turn the original hold into actual spend, once."""
    async with owned_native(tmp_path,monkeypatch) as f:
        identifier=(await accept(f))['job_ids'][0];await f.worker.step(identifier)
        assert await counters(f.factory)==(25,0,1,1)
        statement=await import_statement(f.factory,context=BillingStatementContext(provider_id=f.quote.provider_id,
            provider_connection_version=f.quote.provider_connection_version,account_version=f.quote.account_version,statement_key='authored-wan-statement'),
            normalized_csv=b'row_reference,provider_request_id,status,currency,amount_minor\nowned-row,authored-wan-bill,charged,CNY,17\n',
            original_content=b'Authored Wan bill fixture, not a supplier observation',imported_by=ACTOR)
        assert (await reconcile_statement(f.factory,statement,recorded_by=ACTOR))[0]['status']=='applied'
        await reconcile_statement(f.factory,statement,recorded_by=ACTOR)
        assert await counters(f.factory)==(0,17,1,1) and await counters(f.factory,'b')==(0,0,0,0)
        assert f.responses.calls==['POST']
