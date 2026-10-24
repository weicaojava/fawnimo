"""Independent hand-authored software fixtures; no real text-model qualification."""
from copy import deepcopy
import hashlib
import json
import pytest
from pydantic import ValidationError
from fastapi import HTTPException
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from app.config import settings
from app.models.production import ProductionAsset
from app.models.studio import Chapter, Character, Scene, Shot
from app.services.worker.execution_admission import admit
from tests.test_execution_admission import setup as admission_setup, inspect, POLICY, REQUEST
from app.core.contracts.script_preparation import ScriptCreate, ScriptPatch, ScriptPublish
from app.models.script_preparation import ScriptPublication
from app.services.script_preparation.schema import migrate_script_preparation
from app.services.script_preparation.service import create_draft, patch_draft, publish_draft, draft_view, snapshot_view, preflight_snapshot


def document(shot='shot-a1'):
    """Define fixed required statements separately from their removable shot mappings."""
    return {'schema_version':1,'title':'Manual software fixture, not model output',
        'script_text':'A disagreement develops, then reaches a resolution.', 'target_duration_ms':30000,
        'character_ids':['shared-hero','shared-other'],'scene_ids':['scene-a'],
        'beats':[{'id':phase,'text':phase,'phase':phase,'required':True} for phase in ['conflict','development','resolution']],
        'dialogues':[{'id':'line-a','speaker_id':'shared-hero','text':'Manual required line','required':True}],
        'bindings':[{'shot_id':shot,'beat_ids':['conflict','development','resolution'],'dialogue_ids':['line-a'],
                     'character_ids':['shared-hero','shared-other'],'scene_id':'scene-a','duration_ms':30000}]}


async def fixture(tmp_path,monkeypatch):
    """Use real migrated SQLite and two chapters sharing owned character identities."""
    monkeypatch.setattr(settings,'production_media_root',str(tmp_path))
    engine,factory=await admission_setup(tmp_path)
    await migrate_script_preparation(engine)
    async with factory() as db:
        db.add_all([Chapter(id='chapter-a1',project_id='a',index=1,title='Episode1',raw_text='ORIGINAL ONE'),
                    Chapter(id='chapter-a2',project_id='a',index=2,title='Episode2',raw_text='ORIGINAL TWO'),
                    Chapter(id='chapter-b',project_id='b',index=1,title='Foreign')])
        db.add_all([Character(id=i,project_id=p,name=i,style='真人古装',visual_style='现实') for i,p in
                    [('shared-hero','a'),('shared-other','a'),('foreign-character','b')]])
        db.add_all([Scene(id=i,name=i,style='真人古装',visual_style='现实') for i in ['scene-a','scene-b']])
        await db.flush()
        db.add_all([ProductionAsset(id='asset-'+p,project_id=p,kind='scene',name='scene-'+p,entity_id='scene-'+p) for p in ['a','b']])
        db.add_all([Shot(id=s,chapter_id=c,index=1,title=s,script_excerpt='original shot') for s,c in
                    [('shot-a1','chapter-a1'),('shot-a2','chapter-a2'),('shot-b','chapter-b')]])
        await db.commit()
    return engine,factory


async def rejected(awaitable,status):
    """Require the precise refusal status without accepting unrelated crashes."""
    with pytest.raises(HTTPException) as error:
        await awaitable
    assert error.value.status_code==status


async def view(factory,identity):
    """Read fresh committed draft readiness, not an identity-map cached object."""
    async with factory() as db:
        return await draft_view(db,'a',identity)


async def publish(factory,identity,revision,ack=False,note=''):
    """Exercise the same typed explicit acknowledgment consumed by the API layer."""
    return await publish_draft(factory,'a',identity,ScriptPublish(expected_revision=revision,
        acknowledge_requirement_changes=ack,requirement_change_note=note))


@pytest.mark.asyncio
@pytest.mark.parametrize('missing',['beat','dialogue','speaker','duration'])
async def test_required_coverage_denominator_and_speaker_cannot_be_bypassed(tmp_path,monkeypatch,missing):
    """Removing mappings must create deficiencies, never reduce declared requirements."""
    engine,factory=await fixture(tmp_path,monkeypatch)
    try:
        data=document()
        if missing=='beat':data['bindings'][0]['beat_ids'].remove('development')
        if missing=='dialogue':data['bindings'][0]['dialogue_ids']=[]
        if missing=='speaker':data['bindings'][0]['character_ids']=['shared-other']
        if missing=='duration':data['bindings'][0]['duration_ms']=29999
        identity=await create_draft(factory,'a',ScriptCreate(chapter_id='chapter-a1',document=data))
        state=await view(factory,identity)
        assert not state['readiness']['can_publish']
        assert state['readiness']['coverage']['required_beats']==3
        assert state['readiness']['coverage']['required_dialogues']==1
        await rejected(publish(factory,identity,1),409)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['delete_dialogue','optional_dialogue','text','speaker','add_required','delete_beat','optional_beat','phase'])
async def test_required_change_needs_explicit_ack_and_nonblank_note(tmp_path,monkeypatch,change):
    """New requirement semantics cannot silently replace an already published contract."""
    engine,factory=await fixture(tmp_path,monkeypatch)
    try:
        initial=document()
        if change in {'delete_beat','optional_beat','phase'}:
            initial['beats'].append({'id':'extra-conflict','text':'Additional fixed requirement','phase':'conflict','required':True})
            initial['bindings'][0]['beat_ids'].append('extra-conflict')
        identity=await create_draft(factory,'a',ScriptCreate(chapter_id='chapter-a1',document=initial))
        first=await publish(factory,identity,1)
        async with factory() as db:old=await snapshot_view(db,'a',first)
        data=deepcopy(initial)
        if change=='delete_dialogue':
            data['dialogues']=[];data['bindings'][0]['dialogue_ids']=[]
        if change=='optional_dialogue':data['dialogues'][0]['required']=False
        if change=='text':data['beats'][0]['text']='Changed conflict, explicit user revision'
        if change=='speaker':data['dialogues'][0]['speaker_id']='shared-other'
        if change=='delete_beat':
            data['beats'].pop();data['bindings'][0]['beat_ids'].remove('extra-conflict')
        if change=='optional_beat':data['beats'][-1]['required']=False
        if change=='phase':data['beats'][-1]['phase']='development'
        if change=='add_required':
            data['dialogues'].append({'id':'new-line','speaker_id':'shared-hero','text':'New required utterance','required':True})
            data['bindings'][0]['dialogue_ids'].append('new-line')
        await patch_draft(factory,'a',identity,ScriptPatch(expected_revision=1,document=data))
        assert (await view(factory,identity))['readiness']['requirement_changes']
        await rejected(publish(factory,identity,2),409)
        await rejected(publish(factory,identity,2,True,'  '),409)
        second=await publish(factory,identity,2,True,'Explicit software-fixture change acknowledgment')
        assert second!=first
        async with factory() as db:
            assert await snapshot_view(db,'a',first)==old
            assert not (await preflight_snapshot(db,'a',first))['usable']
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_two_episodes_scope_fact_drift_cas_and_snapshot_immutability(tmp_path,monkeypatch):
    """Shared character identity never permits cross-episode shots or mutable history."""
    engine,factory=await fixture(tmp_path,monkeypatch)
    try:
        first=await create_draft(factory,'a',ScriptCreate(chapter_id='chapter-a1',document=document()))
        second=await create_draft(factory,'a',ScriptCreate(chapter_id='chapter-a2',document=document('shot-a2')))
        one=await publish(factory,first,1);two=await publish(factory,second,1)
        for shot in ['shot-a2','shot-b']:
            await rejected(patch_draft(factory,'a',first,ScriptPatch(expected_revision=1,document=document(shot))),422)
        foreign=document();foreign['character_ids'][0]='foreign-character';foreign['dialogues'][0]['speaker_id']='foreign-character';foreign['bindings'][0]['character_ids'][0]='foreign-character'
        await rejected(patch_draft(factory,'a',first,ScriptPatch(expected_revision=1,document=foreign)),422)
        await rejected(create_draft(factory,'b',ScriptCreate(chapter_id='chapter-a1',document=document())),422)
        async with factory() as db:
            old=await snapshot_view(db,'a',one)
            assert (await preflight_snapshot(db,'a',one))['usable']
            digest=hashlib.sha256(json.dumps(old['payload'],sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()
            assert digest==old['content_hash']
        with pytest.raises(ValidationError):
            ScriptCreate.model_validate({'chapter_id':'chapter-a2','document':old['payload']['document'],'readiness':{'can_publish':True}})
        for operation in ['UPDATE '+ScriptPublication.__tablename__+" SET content_hash='bad' WHERE id=:id",'DELETE FROM '+ScriptPublication.__tablename__+' WHERE id=:id']:
            async with factory() as db:
                with pytest.raises(IntegrityError):
                    await db.execute(text(operation),{'id':one})
                await db.rollback()
        await patch_draft(factory,'a',first,ScriptPatch(expected_revision=1,document=document()))
        await rejected(patch_draft(factory,'a',first,ScriptPatch(expected_revision=1,document=document())),409)
        async with factory() as db:
            assert not (await preflight_snapshot(db,'a',one))['usable']
            assert (await preflight_snapshot(db,'a',two))['usable']
            await db.execute(update(Character).where(Character.id=='shared-hero').values(description='Changed actual shared identity'))
            await db.commit()
        async with factory() as db:
            assert not (await preflight_snapshot(db,'a',two))['usable']
            assert await snapshot_view(db,'a',one)==old
            assert (await db.get(Chapter,'chapter-a1')).raw_text=='ORIGINAL ONE'
            assert (await db.get(Chapter,'chapter-a2')).raw_text=='ORIGINAL TWO'
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_registered_script_blocks_new_admission_but_preserves_original_replay(tmp_path,monkeypatch):
    """Replay cannot rebill, and new generic requests cannot bypass script preparation."""
    engine,factory=await fixture(tmp_path,monkeypatch)
    try:
        original=await admit(factory,'a','existing-request',REQUEST,POLICY)
        identity=await create_draft(factory,'a',ScriptCreate(chapter_id='chapter-a1',document=document()))
        baseline=await inspect(factory)
        assert await admit(factory,'a','existing-request',REQUEST,POLICY)==original
        changed={**REQUEST,'prompt':'conflicting request body'}
        await rejected(admit(factory,'a','existing-request',changed,POLICY),409)
        await rejected(admit(factory,'a','new-request',REQUEST,POLICY),409)
        assert await inspect(factory)==baseline
        await patch_draft(factory,'a',identity,ScriptPatch(expected_revision=1,document=document()))
        assert await admit(factory,'a','existing-request',REQUEST,POLICY)==original
        assert await inspect(factory)==baseline
    finally:
        await engine.dispose()
