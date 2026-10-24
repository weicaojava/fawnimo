"""Actual ASGI routes with a test-only admission runtime; no transport or real model calls."""
from copy import deepcopy
from types import SimpleNamespace
import json
import pytest
from httpx import ASGITransport,AsyncClient
from app.main import app
from app.api.script_preparation import get_script_factory
from tests.test_script_preparation_acceptance import fixture,document
from tests.test_execution_admission import POLICY,REQUEST,inspect


@pytest.mark.asyncio
async def test_story_http_scope_strict_inputs_and_registered_admission(tmp_path,monkeypatch):
    """HTTP registration blocks new jobs before budget mutation while old requests replay."""
    engine,factory=await fixture(tmp_path,monkeypatch)
    previous=getattr(app.state,'execution_runtime',None)
    app.state.execution_runtime=SimpleNamespace(allow_submission=True,running=True,policies={POLICY.capability.model_id:POLICY},worker=SimpleNamespace(factory=factory))
    app.dependency_overrides[get_script_factory]=lambda:factory
    base='/api/v1/script-preparation/projects/a'
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',trust_env=False) as client:
            body={**REQUEST,'request_key':'before-registration'}
            original=await client.post('/api/v1/execution/projects/a/submissions',json=body)
            assert original.status_code==201,original.text
            counts=await inspect(factory)
            catalogue=await client.get(base+'/catalog')
            assert catalogue.status_code==200
            assert {row['id'] for row in catalogue.json()['data']['characters']}=={'shared-hero','shared-other'}
            assert {row['id'] for row in catalogue.json()['data']['scenes']}=={'scene-a'}
            data=document()
            created=await client.post(base+'/drafts',json={'chapter_id':'chapter-a1','document':data})
            assert created.status_code==201,created.text
            draft=created.json()['data']
            assert (await client.post(base+'/drafts',json={'chapter_id':'chapter-a1','document':data})).status_code==409
            blocked=await client.post('/api/v1/execution/projects/a/submissions',json={**body,'request_key':'after-registration'})
            assert blocked.status_code==409,blocked.text
            replay=await client.post('/api/v1/execution/projects/a/submissions',json=body)
            assert replay.status_code==201 and replay.json()['data']==original.json()['data']
            assert (await client.post('/api/v1/execution/projects/a/submissions',json={**body,'prompt':'changed'})).status_code==409
            assert await inspect(factory)==counts
            publish=await client.post(base+'/drafts/'+draft['id']+'/publish',json={'expected_revision':1})
            assert publish.status_code==200,publish.text
            snapshot=publish.json()['data']
            assert (await client.get(base+'/snapshots/'+snapshot['id']+'/preflight')).json()['data']['usable']
            assert (await client.get(base.replace('/a','/b')+'/snapshots/'+snapshot['id'])).status_code==404
            assert (await client.get(base.replace('/a','/b')+'/drafts/'+draft['id'])).status_code==404
            for extra in [{'target_duration_ms':True},{'endpoint':'http://localhost/'},{'character_ids':['foreign-character']},{'scene_ids':['scene-b']},{'bindings':[{**data['bindings'][0],'shot_id':'shot-a2'}]}]:
                response=await client.patch(base+'/drafts/'+draft['id'],json={'expected_revision':1,'document':{**data,**extra}})
                assert response.status_code==422,response.text
            unicode_data=deepcopy(data);unicode_data['title']='\ud800'
            response=await client.patch(base+'/drafts/'+draft['id'],content=json.dumps({'expected_revision':1,'document':unicode_data},ensure_ascii=True).encode(),headers={'Content-Type':'application/json'})
            assert response.status_code==422,response.text
            edited=await client.patch(base+'/drafts/'+draft['id'],json={'expected_revision':1,'document':data})
            assert edited.status_code==200 and edited.json()['data']['revision']==2
            assert not (await client.get(base+'/snapshots/'+snapshot['id']+'/preflight')).json()['data']['usable']
            assert (await client.patch(base+'/drafts/'+draft['id'],json={'expected_revision':1,'document':data})).status_code==409
    finally:
        app.state.execution_runtime=previous
        app.dependency_overrides.pop(get_script_factory,None)
        await engine.dispose()
