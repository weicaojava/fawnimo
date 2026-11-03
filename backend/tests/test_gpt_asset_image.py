"""Declared GPT Image protocol with actual authored loopback HTTP/decoder/files/SQLite; no real provider, quality or bill qualification."""
import asyncio
import base64
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import select,text,update
from sqlalchemy.exc import IntegrityError,OperationalError
from app.core.contracts.asset_image_protocols import GPT_IMAGE_PROTOCOL,GPT_IMAGE_PROFILE,GPT_IMAGE_MODELS,GPT_IMAGE_SIZES,asset_image_protocol
from app.core.integrations import gpt_asset_image as adapter
from app.core.integrations.asset_image import AssetImageModelError,decode_image
from app.core.integrations.trusted_http import HttpResult
from app.models.asset_image_generation import AssetImageJob
from app.models.gpt_image_inputs import GptImageInputSet,GptImageReference
from app.models.model_charge import ModelCallCharge
from app.services.worker.model_prices import ModelPriceRegistry
from tests.test_asset_image_generation import model_server,terminal,adoption_body
from tests.test_asset_image_references import reference_client,reference_submit,balance,png

MODEL='gpt-image-2.5-sunburst-2026-09-08'


def protocol(model=MODEL,**params):
    """Select only this exact optional reference profile; do not reuse the old compatible text-only declaration."""
    return asset_image_protocol(provider='openai',model=model,params={'asset_image_protocol':GPT_IMAGE_PROTOCOL,**params})


@pytest.mark.parametrize('model',sorted(GPT_IMAGE_MODELS))
def test_only_declared_exact_gpt_snapshots_enable_three_references_and_b64(model):
    """An explicitly chosen model/profile exposes bounded capabilities without interpreting arbitrary model parameters as authority."""
    choice=protocol(model)
    assert choice.profile==GPT_IMAGE_PROFILE and choice.sizes==GPT_IMAGE_SIZES and choice.max_references==3 and choice.response_formats==('b64_json',)


@pytest.mark.parametrize('model',['gpt-image-1.5','gpt-image-2.5-sunburst','unlisted-image'])
def test_other_models_cannot_borrow_the_new_reference_profile(model):
    """Existing model names and moving aliases never silently upgrade into an unverified new snapshot profile."""
    with pytest.raises(ValueError):protocol(model)


@pytest.mark.asyncio
@pytest.mark.parametrize('references,size',[(0,(1024,1024)),(2,(1024,1024)),(2,(1536,1024)),(2,(1024,1536))],
    ids=['text-square','references-square','references-wide','references-tall'])
async def test_actual_http_exact_refs_png_candidate_original_fee_adoption_and_replay(tmp_path,monkeypatch,references,size):
    """Send exact selected in-memory bytes to the correct real local route, decode real PNG, preserve one original project hold and explicitly adopt once."""
    with model_server() as server:
        async with reference_client(tmp_path,monkeypatch,server,model=MODEL,protocol=GPT_IMAGE_PROTOCOL,output_size=size) as (client,factory,slots,runtime,quote,refs,originals):
            selected=refs[:references];dimension=f'{size[0]}x{size[1]}'
            submitted=await reference_submit(client,slots['actor'],quote,selected,size=dimension)
            assert submitted.status_code==201,submitted.text
            job=await terminal(client,submitted.json()['data']['id'])
            assert job['status']=='candidate' and job['protocol']==GPT_IMAGE_PROTOCOL and (job['width'],job['height'])==size,job
            assert [item['media_id'] for item in job['references']]==[item['media_id'] for item in selected]
            assert len(server['calls'])==1 and server['gets']==0 and await balance(factory)==(15,0) and await balance(factory,'other-project')==(0,0)
            sent=server['calls'][0];body=sent['payload']
            assert sent['authenticated'] and sent['path']==('/v1/images/edits' if references else '/v1/images/generations')
            assert body['model']==MODEL and body['size']==dimension and body['quality']=='medium' and body['n']==1 and body['output_format']=='png'
            assert body['background']=='opaque' and body['stream'] is False and 'response_format' not in body and 'image' not in body
            if references:
                assert len(body['images'])==references
                for index,image in enumerate(body['images']):
                    assert set(image)=={'image_url'} and base64.b64decode(image['image_url'].split(',',1)[1],validate=True)==originals[index][0]
                assert '外观与主体细节' in body['prompt'] and '构图与空间关系' in body['prompt']
            else:assert 'images' not in body
            content=await client.get(job['content_url']);assert content.content==server['png'] and job['sha256']==hashlib.sha256(content.content).hexdigest()
            async with factory() as db:
                header=await db.get(GptImageInputSet,job['id']);assert header.sealed and header.reference_count==references and header.profile==GPT_IMAGE_PROFILE
                charge=await db.scalar(select(ModelCallCharge));assert charge.provider_request_id=='fixture-image-1' and charge.dispatch_state=='accepted'
                tables=(await db.execute(text("SELECT name,wr FROM pragma_table_list WHERE name IN ('gpt_image_input_sets','gpt_image_references')"))).all()
                assert len(tables)==2 and all(row.wr==1 for row in tables)
            adopted=await client.post('/api/v1/asset-image-generation/jobs/'+job['id']+'/adopt',json=await adoption_body(client,'actor'))
            assert adopted.status_code==200 and adopted.json()['data']['status']=='adopted'
            runtime.price_registry=ModelPriceRegistry()
            replay=await reference_submit(client,slots['actor'],quote,selected,size=dimension)
            assert replay.status_code==201 and replay.json()['data']['id']==job['id'] and len(server['calls'])==1


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['url','jpeg','bad64','badimage','fail'])
async def test_actual_bad_response_retains_original_post_witness_and_hold_without_fetch_or_retry(tmp_path,monkeypatch,mode):
    """Failed/wrong/URL responses never create substitute media, forget an accepted original request or contact a result host."""
    with model_server() as server:
        server['mode']=mode
        async with reference_client(tmp_path,monkeypatch,server,model=MODEL,protocol=GPT_IMAGE_PROTOCOL,output_size=(1024,1024)) as (client,factory,slots,_runtime,quote,refs,_originals):
            submitted=await reference_submit(client,slots['actor'],quote,refs,size='1024x1024')
            assert submitted.status_code==201,submitted.text
            job=await terminal(client,submitted.json()['data']['id']);assert job['status']=='failed' and job['file_id'] is None
            assert len(server['calls'])==1 and server['gets']==0 and await balance(factory)==(15,0)
            async with factory() as db:
                charge=await db.scalar(select(ModelCallCharge));assert charge.provider_request_id=='fixture-image-1' and charge.dispatch_state=='accepted'
            replay=await reference_submit(client,slots['actor'],quote,refs,size='1024x1024')
            assert replay.status_code==201 and replay.json()['data']['id']==job['id'] and len(server['calls'])==1


@pytest.mark.asyncio
async def test_unsupported_format_and_wrong_project_references_reject_before_project_hold(tmp_path,monkeypatch):
    """The new profile cannot accept URLs or another project's input merely because the API field exists for a different protocol."""
    with model_server() as server:
        async with reference_client(tmp_path,monkeypatch,server,model=MODEL,protocol=GPT_IMAGE_PROTOCOL,output_size=(1024,1024)) as (client,factory,slots,_runtime,quote,refs,_originals):
            rejected=await reference_submit(client,slots['actor'],quote,refs,size='1024x1024',response_format='url')
            assert rejected.status_code==422
            async with factory() as db:
                from app.models.production import ProductionMedia
                await db.execute(update(ProductionMedia).where(ProductionMedia.id==refs[0]['media_id']).values(project_id='other-project'));await db.commit()
            rejected=await reference_submit(client,slots['actor'],quote,refs[:1],size='1024x1024')
            assert rejected.status_code==404 and server['calls']==[] and await balance(factory)==(0,0)


@pytest.mark.asyncio
async def test_gpt_input_history_has_no_rowid_alias_and_rejects_replacement_or_mutation(tmp_path,monkeypatch):
    """Actual SQLite prevents changes/deletion/REPLACE and exposes no hidden physical key for either newly added proof table."""
    with model_server() as server:
        async with reference_client(tmp_path,monkeypatch,server,model=MODEL,protocol=GPT_IMAGE_PROTOCOL,output_size=(1024,1024)) as (client,factory,slots,runtime,quote,refs,_originals):
            await runtime.stop();runtime.running=True
            submitted=await reference_submit(client,slots['actor'],quote,refs,size='1024x1024')
            assert submitted.status_code==201,submitted.text
            identity=submitted.json()['data']['id']
            for statement in ("UPDATE gpt_image_input_sets SET sealed=0 WHERE job_id=:id","DELETE FROM gpt_image_input_sets WHERE job_id=:id",
                    "INSERT OR REPLACE INTO gpt_image_input_sets SELECT * FROM gpt_image_input_sets WHERE job_id=:id",
                    "UPDATE gpt_image_references SET purpose='style' WHERE job_id=:id","DELETE FROM gpt_image_references WHERE job_id=:id",
                    "INSERT OR REPLACE INTO gpt_image_references SELECT * FROM gpt_image_references WHERE job_id=:id"):
                async with factory() as db:
                    with pytest.raises(IntegrityError):await db.execute(text(statement),{'id':identity})
                    await db.rollback()
            for table in ('gpt_image_input_sets','gpt_image_references'):
                async with factory() as db:
                    with pytest.raises(OperationalError):await db.execute(text('SELECT rowid FROM '+table))
                    await db.rollback()
            runtime.running=False
            assert server['calls']==[] and await balance(factory)==(15,0)


@pytest.mark.asyncio
async def test_explicit_gpt_retry_keeps_original_sealed_refs_and_separate_fee_holds(tmp_path,monkeypatch):
    """An explicitly confirmed new attempt copies original GPT proofs, retains the accepted first hold and replays its own original key without another charge."""
    with model_server() as server:
        server['mode']='badimage'
        async with reference_client(tmp_path,monkeypatch,server,model=MODEL,protocol=GPT_IMAGE_PROTOCOL,output_size=(1024,1024)) as (client,factory,slots,runtime,quote,refs,_originals):
            first=await reference_submit(client,slots['actor'],quote,refs,size='1024x1024')
            original=await terminal(client,first.json()['data']['id']);assert original['status']=='failed' and await balance(factory)==(15,0)
            server['mode']='ok'
            body={'request_key':'explicit-gpt-original-retry','expected_price_fingerprint':quote.fingerprint,'acknowledge_duplicate_charge':True}
            retried=await client.post('/api/v1/asset-image-generation/jobs/'+original['id']+'/retry',json=body)
            assert retried.status_code==201,retried.text
            candidate=await terminal(client,retried.json()['data']['id'])
            assert candidate['status']=='candidate' and candidate['retry_of']==original['id'] and candidate['references']==original['references']
            assert candidate['attempt']==2 and len(server['calls'])==2 and await balance(factory)==(30,0) and await balance(factory,'other-project')==(0,0)
            async with factory() as db:assert (await db.get(GptImageInputSet,candidate['id'])).sealed
            runtime.price_registry=ModelPriceRegistry()
            old=await client.post('/api/v1/asset-image-generation/jobs/'+original['id']+'/retry',json=body)
            assert old.status_code==201 and old.json()['data']['id']==candidate['id'] and len(server['calls'])==2


@pytest.mark.asyncio
async def test_gpt_queued_cancel_and_actual_inflight_cancel_preserve_distinct_original_holds(tmp_path,monkeypatch):
    """Cancellation before a POST releases a planned hold, while actual possible acceptance retains it and does not produce a candidate."""
    with model_server() as server:
        async with reference_client(tmp_path,monkeypatch,server,model=MODEL,protocol=GPT_IMAGE_PROTOCOL,output_size=(1024,1024)) as (client,factory,slots,runtime,quote,refs,_originals):
            await runtime.stop();runtime.running=True
            queued=(await reference_submit(client,slots['actor'],quote,refs,size='1024x1024')).json()['data']
            cancelled=await client.post('/api/v1/asset-image-generation/jobs/'+queued['id']+'/cancel')
            assert cancelled.status_code==200 and await balance(factory)==(0,0) and server['calls']==[]
            runtime.running=False;await runtime.start();server['mode']='block'
            submitted=await reference_submit(client,slots['actor'],quote,refs,key='gpt-cancel-after-post',size='1024x1024')
            assert submitted.status_code==201,submitted.text
            for _ in range(300):
                if server['received'].is_set():break
                await asyncio.sleep(.02)
            assert server['received'].is_set()
            identity=submitted.json()['data']['id']
            assert (await client.post('/api/v1/asset-image-generation/jobs/'+identity+'/cancel')).status_code==200
            await runtime.stop();server['release'].set()
            assert await balance(factory)==(15,0) and len(server['calls'])==1
            saved=(await client.get('/api/v1/asset-image-generation/jobs/'+identity)).json()['data']
            assert saved['status']=='cancelled' and saved['content_url'] is None and len(saved['references'])==2


@pytest.mark.asyncio
@pytest.mark.parametrize('drift',['model','reference'])
async def test_gpt_queued_connection_or_actual_reference_drift_rejects_before_post_and_releases_hold(tmp_path,monkeypatch,drift):
    """A sealed old job cannot silently move to another configured model or reread changed reference bytes under its original authorization."""
    with model_server() as server:
        async with reference_client(tmp_path,monkeypatch,server,model=MODEL,protocol=GPT_IMAGE_PROTOCOL,output_size=(1024,1024)) as (client,factory,slots,runtime,quote,refs,originals):
            await runtime.stop();runtime.running=True
            submitted=await reference_submit(client,slots['actor'],quote,refs,size='1024x1024')
            assert submitted.status_code==201,submitted.text
            if drift=='reference':originals[0][1].write_bytes(png((128,160),(2,3,4)))
            else:
                from app.models.llm import Model
                async with factory() as db:
                    await db.execute(update(Model).where(Model.id=='image-model').values(name='gpt-image-2.5-flare-2026-09-08'));await db.commit()
            runtime.running=False;await runtime.start()
            result=await terminal(client,submitted.json()['data']['id'])
            assert result['status']=='failed' and server['calls']==[] and await balance(factory)==(0,0)


@pytest.mark.asyncio
@pytest.mark.parametrize('raw',[b'{"data":[],"data":[]}',b'{"data":[],"usage":1e9999}',b'{"data":[{"b64_json":"bad"}],"size":"256x256"}'],ids=['duplicate-fields','nonfinite','wrong-size'])
async def test_strict_response_validation_keeps_witness_before_decode_failure(monkeypatch,raw):
    """Pure authored response parsing is separate from actual HTTP tests; malformed documents cannot erase the original POST witness."""
    witnesses=[]
    async def response(*_args,**_kwargs):
        """Return authored bytes only; do not claim that these fixtures came from any provider."""
        return HttpResult(200,{'content-type':'application/json','x-request-id':'gpt-authored-id'},raw)
    async def witness(value):
        """Record controller ordering before body parsing or decoder admission."""
        witnesses.append(value)
    monkeypatch.setattr(adapter,'owned_request',response)
    with pytest.raises(AssetImageModelError):await adapter.invoke_gpt_image(base_url='https://api.openai.com/v1',api_key='authored-no-secret',model=MODEL,
        prompt='原创测试',size='1024x1024',protocol=protocol(),on_response=witness)
    assert witnesses==['gpt-authored-id']
