"""Display names come from the original plan, never mutable characters, credentials or replacement dialogue identities."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update
from app.api.editing import router, get_editing_factory
from app.models.studio import Character
from app.models.shot_plan import ShotPlan
from app.models.script_preparation import ScriptPublication
from app.services.editing.common import original_items
from app.services.editing.service import display_items
from tests.test_editing import fixture


@pytest.mark.asyncio
async def test_actual_catalog_response_uses_frozen_speaker_name_after_live_rename(tmp_path, monkeypatch):
    """The response exposes the original readable name and stable identity while both immutable snapshots and foreign ownership remain protected."""
    engine, factory, plan_id, _media = await fixture(tmp_path, monkeypatch, audio=False)
    try:
        async with factory() as db:
            plan = await db.get(ShotPlan, plan_id)
            script = await db.get(ScriptPublication, plan.payload['script_snapshot_id'])
            before = deepcopy((plan.payload, plan.content_hash, script.payload, script.content_hash))
            frozen = next(row['name'] for row in plan.payload['facts']['characters'] if row['id'] == 'hero')
            original = deepcopy(original_items(plan, script.payload['document']))
            assert all('speaker_name' not in row for row in original)
            await db.execute(update(Character).where(Character.id == 'hero').values(name='后来改名，不应替代原计划'))
            await db.commit()
        app = FastAPI()
        app.include_router(router, prefix='/api/v1')
        app.dependency_overrides[get_editing_factory] = lambda: factory
        query = {'chapter_id': 'c1', 'plan_id': plan_id}
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://authored.local') as client:
            response = await client.get('/api/v1/editing/projects/a/catalog', params=query)
            assert response.status_code == 200
            rows = response.json()['data']['items']
            dialogue = next(row for row in rows if row['item_id'] == 'line')
            assert dialogue['speaker_id'] == 'hero' and dialogue['speaker_name'] == frozen
            assert dialogue['speaker_name'] != '后来改名，不应替代原计划'
            assert all(row['speaker_name'] is None for row in rows if row['kind'] == 'beat')
            denied = await client.get('/api/v1/editing/projects/b/catalog', params=query)
            # This standalone known-project API reports its existing chapter
            # mismatch as422; the managed foreign-account gateway separately
            # returns404. Neither response may contain the original labels.
            assert denied.status_code == 422
            assert 'speaker_name' not in denied.text and frozen not in denied.text
        async with factory() as db:
            plan = await db.get(ShotPlan, plan_id)
            script = await db.get(ScriptPublication, plan.payload['script_snapshot_id'])
            assert (plan.payload, plan.content_hash, script.payload, script.content_hash) == before
            assert original_items(plan, script.payload['document']) == original
    finally:
        await engine.dispose()


@pytest.mark.parametrize('characters', [[], [{'id': 'different', 'name': '别的角色'}], [{'id': 'original-id', 'name': '   '}]])
def test_missing_original_speaker_name_does_not_invent_or_replace_identity(characters):
    """Legacy or incomplete name facts yield nullable display data, preserving the exact original ID and source payload."""
    plan = SimpleNamespace(payload={'facts': {'characters': characters},
        'entries': [{'shot_id': 'shot', 'mapping': {'beat_ids': [], 'dialogue_ids': ['line']}}]})
    script = {'beats': [], 'dialogues': [{'id': 'line', 'required': True, 'text': '原对白', 'speaker_id': 'original-id'}]}
    before = deepcopy((plan.payload, script))
    row = display_items(plan, script)[0]
    assert row['speaker_id'] == 'original-id' and row['speaker_name'] is None and row['text'] == '原对白'
    assert (plan.payload, script) == before
