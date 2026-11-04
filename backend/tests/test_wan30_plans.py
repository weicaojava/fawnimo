"""Compile actual owned chapter plans and preview both optional Wan profiles without sending a model request."""
from copy import deepcopy
from dataclasses import replace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport,AsyncClient

from app.core.contracts.wan30 import MODEL_ID,REFERENCE_PROTOCOL
from app.core.contracts.shot_plan import PlanCompile,PlanPatch
from app.services.shot_plan.service import patch_draft,compile_draft,plan_view
from app.services.worker.workspace_video_runtime import preview_executable_plan
from app.api.shot_plan import router,get_shot_plan_factory
from tests.test_shot_plan import fixture
from tests.test_video_reference_plans import images,choice


@pytest.mark.asyncio
@pytest.mark.parametrize('reference',[False,True],ids=['text','first-last-images'])
async def test_wan_actual_compiled_plan_keeps_exact_model_protocol_and_read_only_preview(tmp_path,monkeypatch,reference):
    """The same real chapter compiler binds Wan metadata; a preview cannot inherit Seedance's model or produce inline private images."""
    engine,factory,draft,value,runtime,transport=await fixture(tmp_path,monkeypatch)
    try:
        old=runtime.policies['trusted-model']
        policy=replace(old,capability=replace(old.capability,model_id=MODEL_ID,durations=frozenset(range(4,31)),
            reference_protocol=REFERENCE_PROTOCOL if reference else None,
            reference_kinds=frozenset({'image'}) if reference else frozenset(),max_references=3 if reference else 0))
        runtime.policies={MODEL_ID:policy}
        app=FastAPI();app.include_router(router,prefix='/api/v1')
        app.state.execution_runtime=runtime;app.dependency_overrides[get_shot_plan_factory]=lambda:factory
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://owned.test') as client:
            response=await client.get('/api/v1/shot-plans/projects/a/catalog',params={'chapter_id':'c1'})
            assert response.status_code==200
            option=response.json()['data']['models'][0]
            assert option['model_id']==MODEL_ID and option['reference_protocol']==(REFERENCE_PROTOCOL if reference else None)
        document=deepcopy(value);document['model_id']=MODEL_ID
        if reference:
            picked,_source=await images(factory,tmp_path);document['shots'][0]['video_reference']=choice(picked)
        await patch_draft(factory,'a',draft,PlanPatch(expected_revision=1,document=document))
        identifier=await compile_draft(factory,'a',draft,PlanCompile(expected_revision=2),runtime)
        async with factory() as db:
            stored=await plan_view(db,'a',identifier)
            preview=await preview_executable_plan(db,'a',identifier,runtime)
        assert stored['payload']['document']['model_id']==MODEL_ID and preview['can_prepare']
        assert not preview['can_submit'] and not preview['model_qualified'] and transport.sent==0
        entry=preview['entries'][0]
        if reference:
            assert preview['wire_hash'] is None and len(preview['summary_hash'])==64
            assert entry['body'] is None and entry['request_summary']['model']==MODEL_ID
            assert entry['request_summary']['input_protocol']==REFERENCE_PROTOCOL
            assert entry['request_summary']['prompt_extend'] is False and 'data:image/' not in str(preview)
        else:
            assert len(preview['wire_hash'])==64 and preview['summary_hash'] is None
            assert entry['body']['model']==MODEL_ID and entry['body']['parameters']['audio'] is False
            assert entry['body']['parameters']['prompt_extend'] is False and 'media' not in entry['body']['input']
    finally:await engine.dispose()
