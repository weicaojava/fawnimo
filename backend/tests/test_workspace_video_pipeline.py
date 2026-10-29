"""Qualify workspace wiring using actual loopback video HTTP, SQLite, bytes and explicit adoption; no model account."""
import asyncio
import hashlib
import time

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from app.api import editing, shot_plan, video_runtime_setup
from app.core.contracts.seedance25 import Seedance25Configuration
from app.models.execution import ExecutionJob
from app.models.execution_budget import ExecutionBudget
from app.models.production import ProductionMedia, ProductionTake, ShotSelection
from app.services.worker.workspace_video_runtime import build_workspace_video_runtime
from tests.test_durable_execution import Clock
from tests.test_seedance25_independent import authored_mp4, plan_fixture, real_servers
from tests.test_shot_plan_acceptance import compile_plan
from tests.test_workspace_video_runtime import write_config


@pytest.mark.asyncio
async def test_configured_workspace_http_submit_receive_and_explicit_adopt(tmp_path, monkeypatch):
    """A server-file runtime must publish a real video once and only adopt after a separate user intent."""
    video = authored_mp4(tmp_path)
    with real_servers(video) as evidence:
        engine, factory, document = await plan_fixture(tmp_path, monkeypatch)
        runtime = None
        try:
            # This fixture owns its entire database; adapt its TEST ledger to the public CNY surface.
            async with factory() as db:
                budget = await db.get(ExecutionBudget, 'a')
                if budget:
                    budget.currency = 'CNY'
                else:
                    db.add(ExecutionBudget(project_id='a', currency='CNY', limit_minor=1999))
                await db.commit()
            config_file = write_config(tmp_path, enabled=True, quote_minor=10, quote_note='Loopback fixture; not a real price')

            def local_configuration(**values):
                """Bind only this test's explicitly owned HTTP port; production JSON has no origin override."""
                return Seedance25Configuration(**values,
                    base_url=f'http://127.0.0.1:{evidence.control_port}/api/v3',
                    download_hosts=frozenset({'127.0.0.1'}), trusted_local_host='127.0.0.1',
                    trusted_local_port=evidence.control_port)

            runtime, setup = build_workspace_video_runtime(factory, config_file,
                environment={'JELLYFISH_VIDEO_API_KEY': evidence.key}, configuration_factory=local_configuration)
            clock = Clock()
            runtime.worker.clock = clock
            runtime.dispatcher.clock = clock
            runtime.dispatcher.interval = 0.02
            await runtime.start()
            _, plan_id = await compile_plan(factory, document, runtime)
            app = FastAPI()
            app.state.execution_runtime = runtime
            app.state.workspace_video_setup = setup
            app.include_router(video_runtime_setup.router, prefix='/api/v1')
            app.include_router(shot_plan.router, prefix='/api/v1')
            app.include_router(editing.router, prefix='/api/v1')
            for dependency in (video_runtime_setup.get_video_setup_factory, shot_plan.get_shot_plan_factory, editing.get_editing_factory):
                app.dependency_overrides[dependency] = lambda: factory
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://workspace.test') as client:
                status = await client.get('/api/v1/video-runtime/setup')
                assert status.json()['data']['running'] is True
                assert status.json()['data']['real_model_qualified'] is False
                assert status.json()['data']['legacy_actions_available'] is False
                preview = await client.get(f'/api/v1/video-runtime/projects/a/plans/{plan_id}/request-preview')
                assert preview.status_code == 200, preview.text
                ready = preview.json()['data']
                assert ready['can_prepare'] is True and ready['execution_enabled'] is True
                assert ready['can_submit'] is False and ready['model_qualified'] is False
                assert ready['entries'][0]['body']['model'] == document['model_id']
                assert evidence.posts == 0  # Preview is real server validation, never a model call.
                app.state.execution_runtime = None
                blocked = (await client.get(f'/api/v1/video-runtime/projects/a/plans/{plan_id}/request-preview')).json()['data']
                assert blocked['can_prepare'] is False and all(entry['body'] is None for entry in blocked['entries'])
                assert evidence.posts == 0
                app.state.execution_runtime = runtime
                submit_path = f'/api/v1/shot-plans/projects/a/plans/{plan_id}/shots/shot-a1/submissions'
                submitted = await client.post(submit_path, json={'request_key': 'workspace-one-create'})
                assert submitted.status_code == 201, submitted.text
                job_id = submitted.json()['data']['job_id']
                deadline = time.monotonic() + 25
                advanced = False
                while time.monotonic() < deadline:
                    async with factory() as db:
                        job = await db.get(ExecutionJob, job_id)
                        if job.state == 'ready':
                            media_id = job.media_id
                            break
                        assert job.state not in {'failed', 'submission_unknown', 'paused'}, job.state
                        if job.state == 'running' and not advanced:
                            clock.advance(6)
                            advanced = True
                    await asyncio.sleep(0.04)
                else:
                    raise AssertionError('Actual local HTTP pipeline did not publish before the bounded deadline')
                async with factory() as db:
                    assert await db.get(ShotSelection, 'shot-a1') is None
                    media = await db.get(ProductionMedia, media_id)
                    assert media.sha256 == hashlib.sha256(video).hexdigest()
                replay = await client.post(submit_path, json={'request_key': 'workspace-one-create'})
                assert replay.json()['data']['job_id'] == job_id
                assert evidence.posts == 1 and evidence.downloads == 1
                adopted = await client.post('/api/v1/editing/projects/a/selections', json={
                    'request_key': 'workspace-explicit-adopt', 'shot_id': 'shot-a1', 'expected_revision': 0, 'job_id': job_id,
                })
                assert adopted.status_code in (200, 201), adopted.text
                take_id = adopted.json()['data']['take_id']
                async with factory() as db:
                    assert (await db.get(ShotSelection, 'shot-a1')).take_id == take_id
                    take = await db.get(ProductionTake, take_id)
                    assert take.media_id == media_id and take.provenance == 'execution_candidate_unqualified'
                    held = await db.get(ExecutionBudget, 'a')
                    assert held.reserved_minor == 10 and held.spent_minor == 0
                # Explicit adoption and a ready file are not verified provider billing or model quality.
                assert evidence.posts == 1
        finally:
            if runtime is not None:
                await runtime.stop()
            await engine.dispose()
