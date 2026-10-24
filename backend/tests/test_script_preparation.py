"""Manual fixture scripts test software preparation, never real model or M4 output."""
import hashlib
import pytest
from fastapi import HTTPException
from sqlalchemy import text,update
from sqlalchemy.exc import IntegrityError
from app.config import settings
from app.core.contracts.script_preparation import ScriptDocument,ScriptCreate,ScriptPatch,ScriptPublish
from app.models.production import ProductionAsset
from app.models.studio import Chapter,Character,Scene,Shot
from app.services.script_preparation.schema import migrate_script_preparation
from app.services.script_preparation.service import canonical,catalog,create_draft,patch_draft,publish_draft,draft_view,snapshot_view,preflight_snapshot
from app.services.worker.execution_admission import admit
from tests.test_execution_admission import setup,POLICY,REQUEST,inspect


def document(shot='s1'):
    """Use one explicit shot to cover a small complete manual narrative fixture."""
    return ScriptDocument(title='手工软件样例',script_text='冲突、发展、收束及对白。',character_ids=['hero'],scene_ids=['scene'],beats=[{'id':phase,'phase':phase,'text':phase,'required':True} for phase in ['conflict','development','resolution']],dialogues=[{'id':'line','speaker_id':'hero','text':'手工对白','required':True}],bindings=[{'shot_id':shot,'beat_ids':['conflict','development','resolution'],'dialogue_ids':['line'],'character_ids':['hero'],'scene_id':'scene','duration_ms':30000}])


async def fixture(tmp_path):
    """Build actual shared project entities and two chapters in isolated SQLite."""
    engine,factory=await setup(tmp_path)
    await migrate_script_preparation(engine);await migrate_script_preparation(engine)
    async with factory() as db:
        db.add_all([Chapter(id='c1',project_id='a',index=1,title='第一集',raw_text='original'),Chapter(id='c2',project_id='a',index=2,title='第二集')]);await db.flush()
        db.add_all([Character(id='hero',project_id='a',name='角色',description='identity',style='test'),Scene(id='scene',name='场景',description='stable',style='test')]);await db.flush()
        db.add(ProductionAsset(id='owned-scene',project_id='a',kind='scene',name='场景',entity_id='scene'))
        db.add_all([Shot(id='s1',chapter_id='c1',index=0,title='镜头一'),Shot(id='s2',chapter_id='c2',index=0,title='镜头二')]);await db.commit()
    return engine,factory


@pytest.mark.asyncio
async def test_publish_revision_scope_facts_and_required_ack(tmp_path,monkeypatch):
    """Verify shared cast, immutable publications, fact drift and explicit denominator change."""
    monkeypatch.setattr(settings,'production_media_root',str(tmp_path))
    engine,factory=await fixture(tmp_path)
    try:
        first=await create_draft(factory,'a',ScriptCreate(chapter_id='c1',document=document()))
        second=await create_draft(factory,'a',ScriptCreate(chapter_id='c2',document=document('s2')))
        async with factory() as db:
            assert (await catalog(db,'a'))['scenes'][0]['id']=='scene'
            assert (await draft_view(db,'a',first))['readiness']['can_publish']
        published=await publish_draft(factory,'a',first,ScriptPublish(expected_revision=1))
        assert await publish_draft(factory,'a',first,ScriptPublish(expected_revision=1))==published
        await publish_draft(factory,'a',second,ScriptPublish(expected_revision=1))
        async with factory() as db:
            snapshot=await snapshot_view(db,'a',published)
            assert snapshot['content_hash']==hashlib.sha256(canonical(snapshot['payload']).encode()).hexdigest()
            assert (await preflight_snapshot(db,'a',published))['usable']
            await db.execute(update(Character).where(Character.id=='hero').values(description='changed actual fact'));await db.commit()
        async with factory() as db:assert not (await preflight_snapshot(db,'a',published))['usable']
        changed=document().model_dump();changed['dialogues'][0]['text']='明确改变的必需对白'
        await patch_draft(factory,'a',first,ScriptPatch(expected_revision=1,document=changed))
        with pytest.raises(HTTPException):await publish_draft(factory,'a',first,ScriptPublish(expected_revision=2))
        await publish_draft(factory,'a',first,ScriptPublish(expected_revision=2,acknowledge_requirement_changes=True,requirement_change_note='人工确认变更'))
        with pytest.raises(HTTPException):await patch_draft(factory,'a',first,ScriptPatch(expected_revision=1,document=changed))
        async with factory() as db:
            assert (await snapshot_view(db,'a',published))==snapshot
            with pytest.raises(IntegrityError):await db.execute(text('DELETE FROM script_preparation_snapshots WHERE id=:id'),{'id':published})
            await db.rollback()
        with pytest.raises(HTTPException):await create_draft(factory,'b',ScriptCreate(chapter_id='c1',document=document()))
        with pytest.raises(HTTPException):await create_draft(factory,'a',ScriptCreate(chapter_id='c1',document=document()))
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_uncovered_required_speaker_duration_and_admission_replay(tmp_path,monkeypatch):
    """Unmapped original requirements and new unbound submissions cannot bypass preparation."""
    monkeypatch.setattr(settings,'production_media_root',str(tmp_path))
    engine,factory=await fixture(tmp_path)
    try:
        old=await admit(factory,'a','old-key',REQUEST,POLICY);before=await inspect(factory)
        value=document().model_dump();value['bindings'][0]['beat_ids'].remove('development')
        draft=await create_draft(factory,'a',ScriptCreate(chapter_id='c1',document=value))
        async with factory() as db:
            check=(await draft_view(db,'a',draft))['readiness']
            assert check['coverage']['required_beats']==3 and check['coverage']['covered_beats']==2
        with pytest.raises(HTTPException):await publish_draft(factory,'a',draft,ScriptPublish(expected_revision=1))
        assert await admit(factory,'a','old-key',REQUEST,POLICY)==old
        with pytest.raises(HTTPException) as failure:await admit(factory,'a','new-key',REQUEST,POLICY)
        assert failure.value.status_code==409 and await inspect(factory)==before
        value=document().model_dump();value['bindings'][0]['character_ids']=[]
        await patch_draft(factory,'a',draft,ScriptPatch(expected_revision=1,document=value))
        async with factory() as db:assert any(i['code'].startswith('speaker_') for i in (await draft_view(db,'a',draft))['readiness']['issues'])
        value=document().model_dump();value['bindings'][0]['duration_ms']=29999
        await patch_draft(factory,'a',draft,ScriptPatch(expected_revision=2,document=value))
        with pytest.raises(HTTPException):await publish_draft(factory,'a',draft,ScriptPublish(expected_revision=3))
        value=document('s2')
        with pytest.raises(HTTPException):await patch_draft(factory,'a',draft,ScriptPatch(expected_revision=3,document=value))
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_same_revision_fact_drift_requires_save_before_republication(tmp_path,monkeypatch):
    """A stale current publication prompts an explicit revision rather than relocking history."""
    monkeypatch.setattr(settings,'production_media_root',str(tmp_path))
    engine,factory=await fixture(tmp_path)
    try:
        draft=await create_draft(factory,'a',ScriptCreate(chapter_id='c1',document=document()))
        old=await publish_draft(factory,'a',draft,ScriptPublish(expected_revision=1))
        async with factory() as db:
            original=await snapshot_view(db,'a',old)
            await db.execute(update(Character).where(Character.id=='hero').values(description='new stable character fact'));await db.commit()
        async with factory() as db:
            check=(await draft_view(db,'a',draft))['readiness']
            assert not check['can_publish'] and not check['latest_snapshot_usable']
            assert any(issue['code']=='save_new_revision' for issue in check['issues'])
        assert await publish_draft(factory,'a',draft,ScriptPublish(expected_revision=1))==old
        await patch_draft(factory,'a',draft,ScriptPatch(expected_revision=1,document=document()))
        new=await publish_draft(factory,'a',draft,ScriptPublish(expected_revision=2))
        assert new!=old
        async with factory() as db:
            assert (await preflight_snapshot(db,'a',new))['usable']
            assert (await snapshot_view(db,'a',old))==original
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_draft_list_rejects_201_rows_instead_of_silent_truncation(tmp_path,monkeypatch):
    """A real SQLite project exceeding the UI list bound returns explicit 409."""
    from fastapi import FastAPI
    from httpx import AsyncClient,ASGITransport
    from app.api.script_preparation import router,get_script_factory
    from app.models.script_preparation import ScriptDraft
    monkeypatch.setattr(settings,'production_media_root',str(tmp_path))
    engine,factory=await fixture(tmp_path)
    try:
        async with factory() as db:
            db.add_all([Chapter(id=f'bulk-c-{i}',project_id='a',index=i+10,title=f'chapter{i}') for i in range(201)])
            await db.flush()
            db.add_all([ScriptDraft(id=f'bulk-d-{i}',project_id='a',chapter_id=f'bulk-c-{i}',revision=1,document=ScriptDocument().model_dump()) for i in range(201)])
            await db.commit()
        application=FastAPI();application.include_router(router,prefix='/api/v1')
        application.dependency_overrides[get_script_factory]=lambda:factory
        async with AsyncClient(transport=ASGITransport(app=application),base_url='http://test') as client:
            response=await client.get('/api/v1/script-preparation/projects/a/drafts')
            assert response.status_code==409 and '200' in response.json()['detail']
    finally:await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('entrypoint',['benchmark','metadata'])
async def test_actual_r1_database_additive_entrypoints_create_parent_key(tmp_path,entrypoint):
    """Old real R1 copies upgrade safely even without init_db's index bootstrap."""
    import json
    import sqlite3
    from pathlib import Path
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
    from app.core.db import Base
    from app.models.script_preparation import ScriptDraft
    from app.services.benchmark.schema import migrate_benchmark
    root=Path(__file__).resolve().parents[5]
    evidence=json.loads((root/'outputs/r1/recovery-result.json').read_text(encoding='utf8'))
    source=Path(evidence['run_dir'])/'restored.db'
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    copied=tmp_path/'r1-copy.db'
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as old,sqlite3.connect(copied) as new:
        old.backup(new)
        assert not new.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='chapter_id_project_scope'").fetchone()
        old_chapters=new.execute('SELECT id,project_id FROM chapters ORDER BY id').fetchall()
        original_project=new.execute('SELECT id FROM projects ORDER BY id LIMIT 1').fetchone()[0]
    engine=create_async_engine('sqlite+aiosqlite:///'+copied.as_posix())
    @event.listens_for(engine.sync_engine,'connect')
    def enforce_foreign_keys(connection,_):
        """Exercise the real SQLite mismatch path rather than disabling constraints."""
        connection.execute('PRAGMA foreign_keys=ON')
    factory=async_sessionmaker(engine,expire_on_commit=False)
    try:
        if entrypoint=='benchmark':await migrate_benchmark(engine)
        else:
            async with engine.begin() as connection:await connection.run_sync(Base.metadata.create_all)
        async with factory() as db:
            assert (await db.execute(text("SELECT 1 FROM sqlite_master WHERE type='index' AND name='chapter_id_project_scope'"))).first()
            assert not (await db.execute(text('PRAGMA foreign_key_check'))).first()
            chapter_id,project_id='upgrade-fixture-chapter',original_project
            db.add(Chapter(id=chapter_id,project_id=project_id,index=999,title='copy-only migration fixture'))
            await db.flush()
            db.add(ScriptDraft(id='copied-r1-registration',project_id=project_id,chapter_id=chapter_id,revision=1,document=ScriptDocument().model_dump()))
            await db.commit()
            preserved=(await db.execute(text("SELECT id,project_id FROM chapters WHERE id!='upgrade-fixture-chapter' ORDER BY id"))).all()
            assert preserved==old_chapters
        assert hashlib.sha256(source.read_bytes()).hexdigest()==before
    finally:await engine.dispose()
