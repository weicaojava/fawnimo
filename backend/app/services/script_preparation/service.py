"""Manual script coverage preparation; no text model or production task is executed."""
import hashlib
import json
import uuid
from fastapi import HTTPException
from sqlalchemy import or_,select,update
from app.core.contracts.script_preparation import ScriptDocument
from app.models.production import ProductionAsset
from app.models.script_preparation import ScriptDraft,ScriptPublication
from app.models.studio import Chapter,Character,ProjectSceneLink,Scene,Shot
from app.services.studio.production import project_exists
from app.services.worker.execution_budget import transaction


def canonical(value):
    """Use finite Unicode JSON and reject lone surrogate strings before storage."""
    try:
        encoded=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
        encoded.encode('utf8')
        return encoded
    except (UnicodeError,ValueError,TypeError):raise HTTPException(422,'文档必须是有效的有限Unicode JSON') from None


def content_hash(value):
    """Hash stable facts and documents without timestamps or runtime task state."""
    return hashlib.sha256(canonical(value).encode('utf8')).hexdigest()


def scene_scope(project_id):
    """Global scene identities are usable only through explicit project membership."""
    return or_(Scene.id.in_(select(ProjectSceneLink.scene_id).where(ProjectSceneLink.project_id==project_id)),Scene.id.in_(select(ProductionAsset.entity_id).where(ProductionAsset.project_id==project_id,ProductionAsset.kind=='scene')))


async def catalog(db,project_id):
    """Return complete project choices up to 500 entries, never a hidden partial list."""
    await project_exists(db,project_id)
    characters=(await db.execute(select(Character).where(Character.project_id==project_id).order_by(Character.id).limit(501))).scalars().all()
    scenes=(await db.execute(select(Scene).where(scene_scope(project_id)).order_by(Scene.id).limit(501))).scalars().all()
    if len(characters)>500 or len(scenes)>500:raise HTTPException(409,'工程资产超过500项，请缩小工程范围后编辑')
    return {'characters':[{'id':r.id,'name':r.name} for r in characters],'scenes':[{'id':r.id,'name':r.name} for r in scenes]}


async def chapter_owned(db,project_id,chapter_id):
    """Resolve the actual chapter scope instead of trusting imported project claims."""
    chapter=await db.get(Chapter,chapter_id)
    if chapter is None or chapter.project_id!=project_id:raise HTTPException(422,'章节不存在或不属于本工程')
    return chapter


def unique(values,label):
    """Reject repeated identities/references rather than silently changing coverage."""
    if len(set(values))!=len(values):raise HTTPException(422,label+'不能重复')


async def validate_document(db,project_id,chapter_id,document):
    """Validate format and actual scope, allowing coverage-incomplete drafts."""
    await chapter_owned(db,project_id,chapter_id)
    data=ScriptDocument.model_validate(document).model_dump()
    if len(canonical(data).encode('utf8'))>256*1024:raise HTTPException(422,'剧本文档超过256KiB')
    for key in ['character_ids','scene_ids']:unique(data[key],key)
    beat_ids={item['id'] for item in data['beats']};dialogue_ids={item['id'] for item in data['dialogues']}
    unique([item['id'] for item in data['beats']],'节拍标识');unique([item['id'] for item in data['dialogues']],'对白标识')
    unique([item['shot_id'] for item in data['bindings']],'绑定镜头')
    for identity in data['character_ids']:
        row=await db.get(Character,identity)
        if row is None or row.project_id!=project_id:raise HTTPException(422,'角色不存在或不属于本工程')
    for identity in data['scene_ids']:
        if (await db.execute(select(Scene.id).where(Scene.id==identity,scene_scope(project_id)))).scalar_one_or_none() is None:raise HTTPException(422,'场景不存在或不属于本工程')
    for dialogue in data['dialogues']:
        if dialogue['speaker_id'] not in data['character_ids']:raise HTTPException(422,'对白说话者未在文档中声明')
    for binding in data['bindings']:
        row=await db.get(Shot,binding['shot_id'])
        if row is None or row.chapter_id!=chapter_id:raise HTTPException(422,'镜头不存在或不属于本章')
        for key,allowed in [('beat_ids',beat_ids),('dialogue_ids',dialogue_ids),('character_ids',set(data['character_ids']))]:
            unique(binding[key],key)
            if set(binding[key])-allowed:raise HTTPException(422,'绑定引用未声明的项目')
        if binding['scene_id'] is not None and binding['scene_id'] not in data['scene_ids']:raise HTTPException(422,'绑定场景未在文档中声明')
    return data


async def facts(db,project_id,chapter_id,data):
    """Read sorted current actual stable fields and retain only bounded names plus hashes."""
    await validate_document(db,project_id,chapter_id,data)
    result={'characters':[],'scenes':[],'shots':[]}
    for identity in sorted(data['character_ids']):
        row=await db.get(Character,identity)
        value={key:getattr(row,key) for key in ['name','description','actor_id']}
        result['characters'].append({'id':identity,'name':row.name,'content_hash':content_hash(value)})
    for identity in sorted(data['scene_ids']):
        row=await db.get(Scene,identity)
        result['scenes'].append({'id':identity,'name':row.name,'content_hash':content_hash({'name':row.name,'description':row.description})})
    for identity in sorted(binding['shot_id'] for binding in data['bindings']):
        row=await db.get(Shot,identity)
        value={key:getattr(row,key) for key in ['chapter_id','index','title','script_excerpt']}
        result['shots'].append({'id':identity,'title':row.title,'content_hash':content_hash(value)})
    return result


def changes(previous,current):
    """Compare old required identities and meanings independently of new bindings."""
    if previous is None:return []
    found=[]
    for group,fields in [('beats',['text','phase']),('dialogues',['text','speaker_id'])]:
        old={item['id']:item for item in previous[group] if item['required']}
        new={item['id']:item for item in current[group]}
        for identity,item in old.items():
            other=new.get(identity)
            if other is None or not other['required'] or any(item[field]!=other[field] for field in fields):found.append(group+':changed_or_removed:'+identity)
        for identity,item in new.items():
            if item['required'] and identity not in old:found.append(group+':required_added:'+identity)
    return found


def checklist(data,previous=None):
    """Measure original required-set coverage and all declared item completeness."""
    issues=[]
    def need(condition,code,message):
        """Record a concrete preparation deficiency without asserting semantic quality."""
        if not condition:issues.append({'code':code,'message':message})
    need(bool(data['title'].strip()) and bool(data['script_text'].strip()),'script_text','请填写标题与制作剧本正文')
    need(bool(data['character_ids']) and bool(data['scene_ids']),'assets','至少声明一个角色与场景')
    for group in ['beats','dialogues']:
        for item in data[group]:need(bool(item['text'].strip()),group+'_text_'+item['id'],'所有声明节拍与对白均需非空文本')
    for phase in ['conflict','development','resolution']:need(any(b['required'] and b['phase']==phase for b in data['beats']),'phase_'+phase,'冲突、发展、收束均需必需节拍')
    required_beats={b['id'] for b in data['beats'] if b['required']};required_dialogues={d['id'] for d in data['dialogues'] if d['required']}
    mapped_beats={i for b in data['bindings'] for i in b['beat_ids']};mapped_dialogues={i for b in data['bindings'] for i in b['dialogue_ids']}
    coverage={'required_beats':len(required_beats),'covered_beats':len(required_beats&mapped_beats),'required_dialogues':len(required_dialogues),'covered_dialogues':len(required_dialogues&mapped_dialogues)}
    need(required_beats<=mapped_beats,'beat_coverage','必需节拍尚未覆盖全部镜头映射')
    need(required_dialogues<=mapped_dialogues,'dialogue_coverage','必需对白尚未覆盖全部镜头映射')
    need(bool(data['bindings']),'bindings','至少绑定一个本章镜头')
    dialogue_by_id={item['id']:item for item in data['dialogues']}
    for binding in data['bindings']:
        need(bool(binding['character_ids']) and binding['scene_id'] is not None,'binding_assets_'+binding['shot_id'],'每个绑定镜头须明确角色与场景')
        for identifier in binding['dialogue_ids']:
            dialogue=dialogue_by_id[identifier]
            need(not dialogue['required'] or dialogue['speaker_id'] in binding['character_ids'],'speaker_'+binding['shot_id']+'_'+identifier,'必需对白原说话角色须在每个绑定镜头中')
    need(sum(b['duration_ms'] for b in data['bindings'])==data['target_duration_ms'],'duration','绑定镜头时长合计须精确等于目标时长')
    need(set(data['character_ids'])<={i for b in data['bindings'] for i in b['character_ids']},'character_usage','每个声明角色至少使用一次')
    need(set(data['scene_ids'])<={b['scene_id'] for b in data['bindings']},'scene_usage','每个声明场景至少使用一次')
    return {'can_publish':not issues,'issues':issues,'coverage':coverage,'requirement_changes':changes(previous,data),'latest_snapshot_usable':False}


async def owned_draft(db,project_id,draft_id):
    """Resolve fixed project/chapter registration and fail safely if scope was changed."""
    draft=(await db.execute(select(ScriptDraft).where(ScriptDraft.id==draft_id,ScriptDraft.project_id==project_id))).scalar_one_or_none()
    if draft is None:raise HTTPException(404,'本工程剧本草稿不存在')
    await chapter_owned(db,project_id,draft.chapter_id)
    return draft


async def preflight_snapshot(db,project_id,snapshot_id):
    """Recheck current actual facts and revision; historical exports remain immutable."""
    row=(await db.execute(select(ScriptPublication).where(ScriptPublication.id==snapshot_id,ScriptPublication.project_id==project_id))).scalar_one_or_none()
    if row is None:raise HTTPException(404,'本工程剧本准备快照不存在')
    draft=await owned_draft(db,project_id,row.draft_id);issues=[]
    if draft.revision!=row.draft_revision:issues.append({'code':'draft_revision_changed','message':'草稿已有新修订，请重新发布'})
    try:
        current=await facts(db,project_id,draft.chapter_id,row.payload['document'])
        if content_hash(current)!=row.payload['facts_hash']:issues.append({'code':'facts_changed','message':'角色、场景或镜头事实已改变，请保存新修订再发布'})
    except HTTPException:issues.append({'code':'facts_missing','message':'引用事实已删除或不再属于本工程章节'})
    return {'usable':not issues,'issues':issues}


async def draft_view(db,project_id,draft_id):
    """Expose preparation checklist separately from generation/model readiness."""
    draft=await owned_draft(db,project_id,draft_id)
    snapshots=(await db.execute(select(ScriptPublication).where(ScriptPublication.draft_id==draft_id).order_by(ScriptPublication.draft_revision.desc()))).scalars().all()
    previous=snapshots[0].payload['document'] if snapshots else None
    check=checklist(draft.document,previous)
    if snapshots:
        check['latest_snapshot_usable']=(await preflight_snapshot(db,project_id,snapshots[0].id))['usable']
        if snapshots[0].draft_revision==draft.revision and not check['latest_snapshot_usable']:
            check['can_publish']=False
            check['issues'].append({'code':'save_new_revision','message':'当前修订的历史快照已失配，请先保存为新修订再发布'})
    try:await validate_document(db,project_id,draft.chapter_id,draft.document)
    except HTTPException:
        check['can_publish']=False;check['issues'].append({'code':'facts_missing','message':'引用事实已删除或归属改变，请修复草稿'})
    return {'id':draft.id,'chapter_id':draft.chapter_id,'revision':draft.revision,'document':draft.document,'readiness':check,'snapshots':[{'id':s.id,'draft_revision':s.draft_revision,'content_hash':s.content_hash} for s in snapshots]}


async def create_draft(factory,project_id,body):
    """Register a chapter once; registration also activates new-submission protection."""
    async with factory() as db:
        async with transaction(db):
            data=await validate_document(db,project_id,body.chapter_id,body.document)
            if (await db.execute(select(ScriptDraft.id).where(ScriptDraft.chapter_id==body.chapter_id))).first():raise HTTPException(409,'本章已有草稿，请编辑新修订')
            identifier=str(uuid.uuid4());db.add(ScriptDraft(id=identifier,project_id=project_id,chapter_id=body.chapter_id,revision=1,document=data))
            return identifier


async def patch_draft(factory,project_id,draft_id,body):
    """Keep changed requirements editable while protecting against lost updates."""
    async with factory() as db:
        async with transaction(db):
            draft=await owned_draft(db,project_id,draft_id)
            data=await validate_document(db,project_id,draft.chapter_id,body.document)
            changed=await db.execute(update(ScriptDraft).where(ScriptDraft.id==draft_id,ScriptDraft.revision==body.expected_revision).values(document=data,revision=ScriptDraft.revision+1))
            if not changed.rowcount:raise HTTPException(409,'剧本修订冲突，请刷新')


async def publish_draft(factory,project_id,draft_id,body):
    """Freeze coverage and actual facts, requiring explicit requirement-change consent."""
    canonical(body.model_dump())
    async with factory() as db:
        async with transaction(db):
            draft=await owned_draft(db,project_id,draft_id)
            if draft.revision!=body.expected_revision:raise HTTPException(409,'剧本修订冲突，请刷新')
            snapshots=(await db.execute(select(ScriptPublication).where(ScriptPublication.draft_id==draft_id).order_by(ScriptPublication.draft_revision.desc()))).scalars().all()
            if snapshots and snapshots[0].draft_revision==draft.revision:return snapshots[0].id
            data=await validate_document(db,project_id,draft.chapter_id,draft.document)
            check=checklist(data,snapshots[0].payload['document'] if snapshots else None)
            if not check['can_publish']:raise HTTPException(409,'剧本准备仍有缺项，不能发布')
            if check['requirement_changes'] and not (body.acknowledge_requirement_changes and body.requirement_change_note.strip()):raise HTTPException(409,'必需要求已改变，须显式确认并填写变更说明')
            actual=await facts(db,project_id,draft.chapter_id,data)
            payload={'schema_version':1,'project_id':project_id,'chapter_id':draft.chapter_id,'document':data,'coverage':check['coverage'],'semantic_verification':'manual_preparation_only','acknowledge_requirement_changes':body.acknowledge_requirement_changes,'requirement_change_note':body.requirement_change_note,'requirement_changes':check['requirement_changes'],'facts':actual,'facts_hash':content_hash(actual)}
            identifier=str(uuid.uuid4());db.add(ScriptPublication(id=identifier,project_id=project_id,draft_id=draft_id,draft_revision=draft.revision,content_hash=content_hash(payload),payload=payload))
            return identifier


async def snapshot_view(db,project_id,snapshot_id):
    """Export a historical snapshot even when current source facts have drifted."""
    row=(await db.execute(select(ScriptPublication).where(ScriptPublication.id==snapshot_id,ScriptPublication.project_id==project_id))).scalar_one_or_none()
    if row is None:raise HTTPException(404,'本工程剧本快照不存在')
    await owned_draft(db,project_id,row.draft_id)
    return {'id':row.id,'content_hash':row.content_hash,'payload':row.payload}
