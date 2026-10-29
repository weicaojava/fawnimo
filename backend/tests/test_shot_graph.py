"""Real isolated SQLite structural edits, CAS races, rollback and source/adoption protection."""
import asyncio
import copy
import uuid

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import event, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.v1.routes.studio.shots import router, get_shot_graph_factory
from app.core.contracts.shot_graph import ShotGraphWrite
from app.core.db import Base
from app.models.studio import (Project, Chapter, Shot, ShotDetail, ShotDialogLine, ShotFrameImage,
    ShotCharacterLink, ShotExtractedCandidate, ShotExtractedDialogueCandidate, Character,
    Actor, Scene, Prop, Costume, FileItem, ProjectActorLink, ProjectSceneLink, ProjectPropLink, ProjectCostumeLink)
from app.models.production import ProductionMedia, ProductionTake, ShotSelection
from app.models.shot_graph import ShotGraphReceipt, ShotGraphCursor
from app.models.script_preparation import ScriptDraft, ScriptPublication
from app.models.shot_plan import ShotPlanDraft, ShotPlan
from app.models.editing import EditDraft, EditPublication
from app.models.task import GenerationTask
from app.models.task_links import GenerationTaskLink
from app.models.execution import ExecutionJob
from app.models.reverse import ReverseAnalysis
from app.models.video_dna import DnaDraft
from app.models.dna_production import DnaProductionImport
from app.services.studio import shot_graph as service


@pytest.fixture
def graph(tmp_path):
    """Create only a disposable database; no application runtime, model worker or current data is used."""
    engine = create_async_engine('sqlite+aiosqlite:///' + (tmp_path / 'graph.db').as_posix(), poolclass=NullPool)

    @event.listens_for(engine.sync_engine, 'connect')
    def constraints(connection, _record):
        """Exercise real unique/FK checks and serialize concurrent writers predictably."""
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA busy_timeout=5000')

    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def prepare():
        """Seed three editable shots and a foreign project for scope tests."""
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add_all([Project(id='p', name='Audit', style='真人都市'), Project(id='foreign', name='Foreign', style='真人都市')])
            await db.flush()
            db.add_all([Chapter(id='c', project_id='p', index=1, title='Audit chapter'), Chapter(id='other', project_id='foreign', index=1, title='Foreign chapter')])
            await db.flush()
            db.add_all([Shot(id=f's{i}', chapter_id='c', index=i, title=f'Shot {i}', script_excerpt=f'original text {i}', status='pending') for i in range(1, 4)])
            db.add(Shot(id='foreign-shot', chapter_id='other', index=1, title='Foreign shot', status='pending'))
            await db.flush()
            db.add_all([ShotDetail(id=f's{i}', camera_shot='MS', angle='EYE_LEVEL', movement='STATIC', duration=i, key_frame_prompt=f'prompt {i}') for i in range(1, 4)])
            await db.commit()

    asyncio.run(prepare())
    app = FastAPI()
    app.include_router(router, prefix='/shots')
    app.dependency_overrides[get_shot_graph_factory] = lambda: factory
    client = TestClient(app)
    try:
        yield client, factory
    finally:
        client.close()
        asyncio.run(engine.dispose())


def state(client):
    """Read actual complete saved graph via the new route, not a mock fingerprint."""
    response = client.get('/shots/graph', params={'project_id': 'p', 'chapter_id': 'c'})
    assert response.status_code == 200, response.text
    return response.json()['data']


def command(client, action, **fields):
    """Bind one explicit user intent to the exact state the caller inspected."""
    current = state(client)
    return {'project_id': 'p', 'chapter_id': 'c', 'request_key': str(uuid.uuid4()),
            'expected_revision': current['revision'], 'expected_fingerprint': current['fingerprint'], 'action': action, **fields}


def post(client, body, expected=200):
    """Assert actual route status before inspecting response content."""
    response = client.post('/shots/graph/actions', json=body)
    assert response.status_code == expected, response.text
    return response.json()['data'] if expected == 200 else response.json()


def test_atomic_reorder_and_middle_insert_keep_real_ids_and_pending_defaults(graph):
    client, factory = graph
    result = post(client, command(client, 'reorder', ordered_shot_ids=['s3', 's1', 's2']))
    assert [(shot['id'], shot['index']) for shot in result['graph']['shots']] == [('s3', 1), ('s1', 2), ('s2', 3)]
    inserted = post(client, command(client, 'insert', after_shot_id='s3', title='Inserted between occupied positions'))
    identity = inserted['receipt']['result_shot_id']
    assert [shot['id'] for shot in inserted['graph']['shots']] == ['s3', identity, 's1', 's2']
    assert [shot['index'] for shot in inserted['graph']['shots']] == [1, 2, 3, 4]

    async def verify():
        """Read committed state from an independent session immediately after HTTP success."""
        async with factory() as db:
            shot, detail = await db.get(Shot, identity), await db.get(ShotDetail, identity)
            assert shot.status == 'pending' and not shot.skip_extraction and shot.generated_video_file_id is None
            assert detail.duration == 0
            assert await db.get(ShotSelection, identity) is None
    asyncio.run(verify())
    assert state(client)['fingerprint'] == inserted['graph']['fingerprint']


def test_merge_copies_preparation_graph_and_retains_full_original_receipt(graph):
    client, factory = graph

    async def seed():
        """Give both source shots real shared relations, distinct dialogue and frame files."""
        async with factory() as db:
            db.add_all([Actor(id='actor', name='Audit actor', style='真人都市'), Scene(id='scene', name='Audit scene', style='真人都市'),
                        Prop(id='prop', name='Audit prop', style='真人都市'), Costume(id='costume', name='Audit costume', style='真人都市')])
            db.add_all([FileItem(id=f'image{i}', type='image', name=f'Original {i}', storage_key=f'files/original{i}.png') for i in range(1, 3)])
            await db.flush()
            db.add(Character(id='character', project_id='p', name='Audit character', style='真人都市', actor_id='actor', costume_id='costume'))
            await db.flush()
            for i in (1, 2):
                identity = f's{i}'
                db.add(ShotDialogLine(shot_detail_id=identity, index=0, text=f'Actual dialogue {i}', speaker_character_id='character'))
                db.add(ShotCharacterLink(shot_id=identity, character_id='character', index=0))
                db.add(ShotFrameImage(shot_detail_id=identity, frame_type='key', file_id=f'image{i}'))
                for model, field, asset in ((ProjectActorLink, 'actor_id', 'actor'), (ProjectSceneLink, 'scene_id', 'scene'), (ProjectPropLink, 'prop_id', 'prop'), (ProjectCostumeLink, 'costume_id', 'costume')):
                    db.add(model(project_id='p', chapter_id='c', shot_id=identity, **{field: asset}))
                db.add(ShotExtractedCandidate(shot_id=identity, candidate_type='prop', candidate_name='Shared prop', candidate_status='linked', linked_entity_id='prop', payload={'original': i}))
                db.add(ShotExtractedDialogueCandidate(shot_id=identity, index=0, text=f'Candidate {i}', candidate_status='ignored', payload={'original': i}))
            await db.commit()
    asyncio.run(seed())
    body = command(client, 'merge', source_shot_ids=['s1', 's2'], title='Actually merged')
    merged = post(client, body)
    identity = merged['receipt']['result_shot_id']
    assert [(item['id'], item['index']) for item in merged['graph']['shots']] == [(identity, 1), ('s3', 2)]
    receipt = client.get('/shots/graph/receipts/' + merged['receipt']['id'], params={'project_id': 'p'}).json()['data']
    assert service.fingerprint(receipt['before']) == body['expected_fingerprint']
    assert len(receipt['before']['shot_frame_images']) == 2
    assert '原片时钟' in receipt['recovery_note']

    async def verify():
        """Merged references are real, de-duplicated, and do not inherit an adopted/ready result."""
        async with factory() as db:
            assert await db.get(Shot, 's1') is None and await db.get(Shot, 's2') is None
            shot, detail = await db.get(Shot, identity), await db.get(ShotDetail, identity)
            assert shot.status == 'pending' and not shot.skip_extraction and shot.last_extracted_at is None and shot.generated_video_file_id is None
            assert await db.get(ShotSelection, identity) is None
            assert detail.duration == 3 and 'prompt 1' in detail.key_frame_prompt and 'prompt 2' in detail.key_frame_prompt
            assert list((await db.execute(select(ShotDialogLine.text).where(ShotDialogLine.shot_detail_id == identity).order_by(ShotDialogLine.index))).scalars()) == ['Actual dialogue 1', 'Actual dialogue 2']
            assert len(list((await db.execute(select(ShotCharacterLink).where(ShotCharacterLink.shot_id == identity))).scalars())) == 1
            for model in service.LINKS:
                links = list((await db.execute(select(model))).scalars())
                assert len(links) == 1 and links[0].shot_id == identity
            assert (await db.scalar(select(ShotFrameImage.file_id).where(ShotFrameImage.shot_detail_id == identity))) == 'image1'
            assert await db.get(FileItem, 'image1') and await db.get(FileItem, 'image2')
            assert all(row.candidate_status == 'pending' and row.linked_entity_id is None for row in (await db.execute(select(ShotExtractedCandidate))).scalars())
            assert all(row.candidate_status == 'pending' and row.linked_dialog_line_id is None for row in (await db.execute(select(ShotExtractedDialogueCandidate))).scalars())
    asyncio.run(verify())


def test_request_replay_does_not_repeat_insert_and_reused_key_conflicts(graph):
    client, _factory = graph
    body = command(client, 'insert', after_shot_id='s1')
    first, replay = post(client, body), post(client, body)
    assert replay['replayed'] and replay['receipt'] == first['receipt']
    assert len(state(client)['shots']) == 4 and state(client)['revision'] == 1
    post(client, {**body, 'after_shot_id': 's2'}, 409)


def test_stale_detail_fingerprint_rejects_even_when_structure_revision_did_not_change(graph):
    client, factory = graph
    body = command(client, 'reorder', ordered_shot_ids=['s3', 's2', 's1'])

    async def external_edit():
        """Represent a legitimate legacy detail editor that does not know the new chapter cursor."""
        async with factory() as db:
            await db.execute(update(ShotDetail).where(ShotDetail.id == 's1').values(key_frame_prompt='New saved content'))
            await db.commit()
    asyncio.run(external_edit())
    post(client, body, 409)
    assert state(client)['revision'] == 0
    assert [shot['id'] for shot in state(client)['shots']] == ['s1', 's2', 's3']


@pytest.mark.parametrize('fields', [{'ordered_shot_ids': ['s2', 's1']}, {'ordered_shot_ids': ['s1', 's2', 'foreign-shot']}, {'ordered_shot_ids': ['s1', 's1', 's2']}])
def test_invalid_reorder_is_all_or_nothing(graph, fields):
    client, _factory = graph
    before = state(client)
    post(client, command(client, 'reorder', **fields), 422)
    assert state(client) == before


def test_nonadjacent_merge_rejects_without_new_rows(graph):
    client, _factory = graph
    before = state(client)
    post(client, command(client, 'merge', source_shot_ids=['s1', 's3']), 422)
    assert state(client) == before


def test_two_concurrent_writers_have_one_winner(graph):
    client, factory = graph
    body = command(client, 'reorder', ordered_shot_ids=['s3', 's2', 's1'])

    async def race():
        """Use separate real connections under contention; a single stale token cannot win twice."""
        writes = [ShotGraphWrite.model_validate({**body, 'request_key': str(uuid.uuid4())}) for _ in range(2)]
        results = await asyncio.gather(*(service.mutate(factory, write) for write in writes), return_exceptions=True)
        assert sum(isinstance(item, dict) for item in results) == 1
        errors = [item for item in results if isinstance(item, HTTPException)]
        assert len(errors) == 1 and errors[0].status_code == 409
    asyncio.run(race())
    assert state(client)['revision'] == 1


def test_midmerge_failure_rolls_back_originals_relations_cursor_and_receipt(graph, monkeypatch):
    client, factory = graph
    before = state(client)

    async def fail_after_cloning(_db, _order):
        """Force an error after the clone/delete stage to prove transaction rollback, not compensation."""
        raise RuntimeError('Injected before index normalization')
    monkeypatch.setattr(service, 'normalize_order', fail_after_cloning)
    with pytest.raises(RuntimeError, match='Injected'):
        asyncio.run(service.mutate(factory, ShotGraphWrite.model_validate(command(client, 'merge', source_shot_ids=['s1', 's2']))))
    assert state(client) == before


@pytest.mark.parametrize('kind', ['script_draft', 'script_publication', 'plan_draft', 'plan', 'edit_draft', 'edit_publication', 'execution', 'legacy_task', 'json_object_key'])
def test_json_only_source_references_protect_merge(graph, kind):
    client, factory = graph

    async def seed_reference():
        """No shot foreign key exists in these documents; exact nested JSON identity must still protect it."""
        async with factory() as db:
            value = {'nested': [{'shot_id': 's1'}]}
            if kind.startswith('script'):
                db.add(ScriptDraft(id='script', project_id='p', chapter_id='c', revision=1, document=value if kind == 'script_draft' else {}))
                await db.flush()
                if kind == 'script_publication': db.add(ScriptPublication(id='script-pub', project_id='p', draft_id='script', draft_revision=1, content_hash='a' * 64, payload=value))
            elif kind.startswith('plan'):
                db.add(ShotPlanDraft(id='plan-draft', project_id='p', chapter_id='c', revision=1, document=value if kind == 'plan_draft' else {}))
                await db.flush()
                if kind == 'plan': db.add(ShotPlan(id='plan', project_id='p', draft_id='plan-draft', draft_revision=1, content_hash='a' * 64, payload=value))
            elif kind.startswith('edit'):
                db.add(EditDraft(id='edit', project_id='p', chapter_id='c', revision=1, document=value if kind == 'edit_draft' else {}))
                await db.flush()
                if kind == 'edit_publication': db.add(EditPublication(id='edit-pub', project_id='p', chapter_id='c', draft_id='edit', draft_revision=1, content_hash='a' * 64, payload=value))
            elif kind in ('execution', 'legacy_task'):
                db.add(GenerationTask(id='task', mode='async_polling', status='succeeded', payload=value if kind == 'legacy_task' else {}))
                await db.flush()
                if kind == 'execution': db.add(ExecutionJob(id='job', project_id='p', task_id='task', request_key='key', request_hash='a' * 64, snapshot=value, state='ready'))
            else:
                project = await db.get(Project, 'p')
                project.stats = {'references': {'s1': {'source_clock': 'preserved evidence'}}}
            await db.commit()
    asyncio.run(seed_reference())
    before = state(client)
    post(client, command(client, 'merge', source_shot_ids=['s1', 's2']), 409)
    assert state(client) == before


def test_adopted_source_cannot_merge_but_reorder_preserves_its_exact_take(graph):
    client, factory = graph

    async def adopted():
        """Pin real immutable take/media rows without calling a provider or decoding fabricated bytes."""
        async with factory() as db:
            db.add(FileItem(id='video', name='Source', type='video', storage_key='files/metadata-only.mp4'))
            await db.flush()
            db.add(ProductionMedia(id='video', project_id='p', name='Source', mime='video/mp4', sha256='a' * 64, size=1, relative_path='metadata-only.mp4'))
            await db.flush()
            db.add(ProductionTake(id='take', project_id='p', shot_id='s1', media_id='video', provenance='user_import', version_ids=[]))
            await db.flush()
            db.add(ShotSelection(shot_id='s1', project_id='p', take_id='take'))
            await db.commit()
    asyncio.run(adopted())
    post(client, command(client, 'merge', source_shot_ids=['s1', 's2']), 409)
    post(client, command(client, 'reorder', ordered_shot_ids=['s2', 's1', 's3']))

    async def check_take():
        """Changing production order must not reseat selection or rewrite take provenance."""
        async with factory() as db:
            assert (await db.get(ShotSelection, 's1')).take_id == 'take'
            assert (await db.get(ProductionTake, 'take')).shot_id == 's1'
    asyncio.run(check_take())


def test_active_legacy_task_blocks_structure_and_receipts_are_immutable(graph):
    client, factory = graph

    async def busy():
        """Represent existing work only in this temporary database; no worker is started."""
        async with factory() as db:
            db.add(GenerationTask(id='active', mode='async_polling', status='running', payload={}))
            await db.flush()
            db.add(GenerationTaskLink(task_id='active', relation_type='shot', relation_entity_id='s1'))
            await db.commit()
    asyncio.run(busy())
    post(client, command(client, 'insert', after_shot_id='s1'), 409)

    async def finish():
        """Finish the fixture task so a reorder can produce a real immutable receipt."""
        async with factory() as db:
            await db.execute(update(GenerationTask).where(GenerationTask.id == 'active').values(status='succeeded'))
            await db.commit()
    asyncio.run(finish())
    result = post(client, command(client, 'reorder', ordered_shot_ids=['s2', 's1', 's3']))

    async def tamper():
        """The database itself rejects receipt rewriting, independently of route design."""
        async with factory() as db:
            with pytest.raises(IntegrityError):
                await db.execute(update(ShotGraphReceipt).where(ShotGraphReceipt.id == result['receipt']['id']).values(action='merge'))
            await db.rollback()
    asyncio.run(tamper())
    assert client.get('/shots/graph/receipts/' + result['receipt']['id'], params={'project_id': 'foreign'}).status_code == 404


@pytest.mark.parametrize('column', ['source', 'document', 'result'])
def test_dna_import_json_reference_and_original_clock_are_preserved(graph, column):
    client, factory = graph
    source_clock = {'frame_pts_us': [0, 41667, 83333], 'source_fps_num': 24, 'source_fps_den': 1}

    async def seed():
        """Keep a source-clock receipt in the isolated database; these metadata fixtures are not decoded media."""
        async with factory() as db:
            db.add(FileItem(id='dna-video', name='Source clock fixture', type='video', storage_key='files/fixture.mp4'))
            await db.flush()
            db.add(ProductionMedia(id='dna-video', project_id='p', name='Source clock fixture', mime='video/mp4', sha256='a' * 64, size=1, relative_path='fixture.mp4'))
            await db.flush()
            db.add(ReverseAnalysis(id='analysis', project_id='p', media_id='dna-video', request_key='analysis-key', source_hash='a' * 64, state='ready'))
            await db.flush()
            db.add(DnaDraft(id='dna', project_id='p', analysis_id='analysis', source=source_clock, request_key='dna-key', initial_hash='a' * 64, revision=1, document={}))
            await db.flush()
            values = {'source': copy.deepcopy(source_clock), 'document': {}, 'result': {}}
            values[column]['nested_shots'] = [{'actual_shot_id': 's1'}]
            db.add(DnaProductionImport(id='dna-import', project_id='p', draft_id='dna', draft_revision=1, request_key='import-key', request_hash='a' * 64, document_hash='a' * 64, request={}, **values))
            await db.commit()
            return values
    original = asyncio.run(seed())
    post(client, command(client, 'merge', source_shot_ids=['s1', 's2']), 409)
    post(client, command(client, 'reorder', ordered_shot_ids=['s2', 's1', 's3']))

    async def unchanged():
        """Moving preparation order never rewrites frame PTS or immutable source declarations."""
        async with factory() as db:
            row = await db.get(DnaProductionImport, 'dna-import')
            assert all(getattr(row, key) == value for key, value in original.items())
    asyncio.run(unchanged())


def test_reorder_makes_original_script_facts_stale_without_rewriting_publication(graph):
    client, factory = graph
    from app.core.contracts.script_preparation import ScriptDocument
    from app.services.script_preparation.service import facts, content_hash, preflight_snapshot

    async def publish():
        """Freeze valid current facts so order drift is detected by the existing readiness contract."""
        async with factory() as db:
            document = ScriptDocument(title='Original requirements', bindings=[{'shot_id': f's{index}', 'duration_ms': 1000} for index in (1, 2, 3)]).model_dump()
            payload = {'chapter_id': 'c', 'document': document, 'facts_hash': content_hash(await facts(db, 'p', 'c', document))}
            db.add(ScriptDraft(id='source-script', project_id='p', chapter_id='c', revision=1, document=document))
            await db.flush()
            db.add(ScriptPublication(id='source-publication', project_id='p', draft_id='source-script', draft_revision=1, payload=payload, content_hash=content_hash(payload)))
            await db.commit()
            assert (await preflight_snapshot(db, 'p', 'source-publication'))['usable']
            return copy.deepcopy(payload)
    original = asyncio.run(publish())
    post(client, command(client, 'reorder', ordered_shot_ids=['s2', 's1', 's3']))

    async def stale():
        """An old plan cannot silently inherit a changed chapter ordering or fake source facts."""
        async with factory() as db:
            check = await preflight_snapshot(db, 'p', 'source-publication')
            assert not check['usable'] and any(issue['code'] == 'facts_changed' for issue in check['issues'])
            assert (await db.get(ScriptPublication, 'source-publication')).payload == original
    asyncio.run(stale())


def test_replay_after_a_later_edit_returns_old_receipt_without_resetting_new_order(graph):
    client, _factory = graph
    old = command(client, 'insert', after_shot_id='s1')
    inserted = post(client, old)
    current_ids = [shot['id'] for shot in inserted['graph']['shots']]
    latest = post(client, command(client, 'reorder', ordered_shot_ids=list(reversed(current_ids))))
    replay = post(client, old)
    assert replay['replayed'] and replay['receipt']['id'] == inserted['receipt']['id']
    assert replay['graph'] == latest['graph'] and state(client) == latest['graph']


@pytest.mark.parametrize('kind', ['chapter_link', 'unlinked_task', 'unlinked_execution'])
def test_active_chapter_or_unlinked_json_work_blocks_structural_change(graph, kind):
    client, factory = graph

    async def seed():
        """An executor must not race structure edits merely because its legacy link is chapter-scoped or absent."""
        async with factory() as db:
            db.add(GenerationTask(id='background', mode='async_polling', status='succeeded' if kind == 'unlinked_execution' else 'running', payload={'nested': {'chapter_id': 'c'}} if kind == 'unlinked_task' else {}))
            await db.flush()
            if kind == 'chapter_link': db.add(GenerationTaskLink(task_id='background', relation_type='chapter', relation_entity_id='c'))
            if kind == 'unlinked_execution': db.add(ExecutionJob(id='background-job', project_id='p', task_id='background', request_key='background-key', request_hash='a' * 64, snapshot={'chapter_id': 'c'}, state='queued'))
            await db.commit()
    asyncio.run(seed())
    before = state(client)
    post(client, command(client, 'insert', after_shot_id='s1'), 409)
    assert state(client) == before


def test_database_guards_reject_forged_scope_and_revision_jumps(graph):
    client, factory = graph

    async def forge_scope():
        """Enforce chapter/project scope even for a direct writer that bypasses route validation."""
        async with factory() as db:
            db.add(ShotGraphCursor(chapter_id='c', project_id='foreign', revision=1))
            with pytest.raises(IntegrityError, match='chapter scope'):
                await db.flush()
            await db.rollback()
            db.add(ShotGraphCursor(chapter_id='c', project_id='p', revision=1))
            await db.flush()
            db.add(ShotGraphReceipt(id='forged', project_id='p', chapter_id='c', request_key='forged-key', request_hash='a' * 64,
                revision=1, action='merge', source_shot_ids=[], result_shot_id=None, before={'project_id': 'foreign', 'chapter_id': 'c'}, after_fingerprint='a' * 64))
            with pytest.raises(IntegrityError, match='receipt scope'):
                await db.flush()
            await db.rollback()
    asyncio.run(forge_scope())
    result = post(client, command(client, 'insert', after_shot_id='s1'))

    async def skip_revision():
        """A persisted cursor can increment once and cannot be reseated to another project."""
        async with factory() as db:
            with pytest.raises(IntegrityError, match='revision CAS'):
                await db.execute(update(ShotGraphCursor).where(ShotGraphCursor.chapter_id == 'c').values(revision=99))
            await db.rollback()
    asyncio.run(skip_revision())
    assert state(client) == result['graph']


def test_graph_guard_migration_is_repeatable_and_preserves_saved_graph(graph):
    client, factory = graph
    from app.services.studio.shot_graph_schema import migrate_shot_graph
    before = state(client)

    async def migrate():
        """Additive startup is safe for both brand-new and existing same-column graph journals."""
        await migrate_shot_graph(factory.kw['bind'])
        await migrate_shot_graph(factory.kw['bind'])
    asyncio.run(migrate())
    assert state(client) == before
