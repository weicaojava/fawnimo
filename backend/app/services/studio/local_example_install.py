"""Install one entirely new original example inside the caller's single fenced SQLite transaction."""
import time
import uuid

from fastapi import HTTPException
from sqlalchemy import select

from app.core.contracts.demo_example import original_demo_document
from app.core.contracts.editing import SelectionWrite
from app.core.contracts.local_example import LocalExampleRead
from app.models.editing import EditDraft
from app.models.local_example import LocalExampleMedia, LocalExampleReady, LocalExampleTake
from app.models.production import ProductionMedia
from app.models.script_preparation import ScriptDraft, ScriptPublication
from app.models.shot_plan import ShotPlanDraft, ShotPlan
from app.models.studio import Project, Chapter, Shot, ShotDetail, FileItem, ShotCharacterLink, Scene
from app.schemas.studio.production import AssetWrite
from app.services.editing.media import recheck_media
from app.services.editing.selection import apply_verified_selection, resolve_source, history_check
from app.services.editing.timeline import validate_document as validate_timeline
from app.services.script_preparation.service import validate_document as validate_script, checklist, facts, content_hash
from app.services.shot_plan.compiler import build_payload
from app.services.studio.production import create_asset

LOCAL_INTENT = 'local-original-geometry-v1'
NOTICE = '原创几何光影示例：7段无声视频共28秒，可编辑并导出。请先逐项确认剧情保留标记；没有调用模型，也未进行模型质量或成片质量验收。'


def result_view(ready, *, replayed):
    """Return stored identities without claiming the user's later draft or selections still match the preset."""
    return LocalExampleRead(project_id=ready.project_id,chapter_id=ready.chapter_id,
        script_snapshot_id=ready.script_snapshot_id,plan_id=ready.plan_id,draft_id=ready.draft_id,
        replayed=replayed,entry=f'/projects/{ready.project_id}/editor?chapter={ready.chapter_id}',notice=NOTICE).model_dump()


async def new_project(db, request):
    """Require absent reserved targets before creating only self-authored source rows."""
    if await db.get(Project,request.project_id) is not None or await db.get(Chapter,request.chapter_id) is not None:
        raise HTTPException(409,'示例目标已存在，未覆盖任何工程；请核对原请求')
    document = original_demo_document(request.project_id)
    project = dict(document[0]['body'])
    project['name'] = '可导出示例 · 点亮小岛'
    project['description'] = NOTICE+' 重新创建会另建工程，保留旧工程与编辑。'
    db.add(Project(**project)); await db.flush()
    db.add(Chapter(**document[1]['body'])); await db.flush()
    shots = []
    for offset in range(2,len(document),2):
        shot, detail = dict(document[offset]['body']), dict(document[offset+1]['body'])
        # These are fully known new geometry facts, with no user extraction or
        # provider task. Ready retains only its information-confirmation meaning.
        shot['status'] = 'ready'
        db.add(Shot(**shot)); await db.flush()
        db.add(ShotDetail(**detail)); shots.append(shot)
    await db.flush()
    return document, shots


async def register_media(db, project_id, resources):
    """Register only preverified final files; no decoding, hashing or business commit occurs here."""
    for item in resources:
        db.add(FileItem(id=item['id'],type=item['mime'].split('/')[0],name=item['name'],thumbnail='',
            tags=['original_geometry_example','user_import'],storage_key='local-production/'+item['relative_path']))
    await db.flush()
    for item in resources:
        db.add(ProductionMedia(**{key:item[key] for key in ('id','name','mime','sha256','size','relative_path')},project_id=project_id))
    await db.flush()


async def original_scene_name(db):
    """Choose an absent readable name because the legacy global scene library requires unique names."""
    choices = ['透明几何小岛']+[f'透明几何小岛（示例{index}）' for index in range(2,65)]
    existing = set((await db.execute(select(Scene.name).where(Scene.name.in_(choices)))).scalars())
    for name in choices:
        if name not in existing:
            return name
    raise HTTPException(409,'原创示例场景名称已达到本地上限，未认领或修改已有场景')


async def prepare_script(db, request, shots, character_id, scene_id, source):
    """Validate and freeze original requirements using the normal manual-script checks and current facts."""
    data = {'schema_version':1,'title':'点亮小岛 · 原创无声练习','script_text':source[1]['body']['raw_text'],
        'target_duration_ms':28000,'character_ids':[character_id],'scene_ids':[scene_id],'dialogues':[],
        'beats':[{'id':f'light-beat-{index}','text':shot['script_excerpt'],
            'phase':'conflict' if index==1 else 'resolution' if index==7 else 'development','required':True}
            for index,shot in enumerate(shots,1)],
        'bindings':[{'shot_id':shot['id'],'beat_ids':[f'light-beat-{index}'],'dialogue_ids':[],
            'character_ids':[character_id],'scene_id':scene_id,'duration_ms':4000} for index,shot in enumerate(shots,1)]}
    data = await validate_script(db,request.project_id,request.chapter_id,data)
    check = checklist(data)
    if not check['can_publish']:
        raise HTTPException(409,'原创示例剧本准备不完整，未保存部分工程')
    actual = await facts(db,request.project_id,request.chapter_id,data)
    draft_id, snapshot_id = str(uuid.uuid4()), str(uuid.uuid4())
    db.add(ScriptDraft(id=draft_id,project_id=request.project_id,chapter_id=request.chapter_id,revision=1,document=data)); await db.flush()
    payload = {'schema_version':1,'project_id':request.project_id,'chapter_id':request.chapter_id,'document':data,
        'coverage':check['coverage'],'semantic_verification':'manual_preparation_only','acknowledge_requirement_changes':False,
        'requirement_change_note':'','requirement_changes':[],'facts':actual,'facts_hash':content_hash(actual)}
    db.add(ScriptPublication(id=snapshot_id,project_id=request.project_id,draft_id=draft_id,draft_revision=1,
        content_hash=content_hash(payload),payload=payload)); await db.flush()
    return snapshot_id


async def install(db, request, token, resources, proofs, videos):
    """Create everything or roll back everything; the caller holds a fresh BEGIN IMMEDIATE and live lease.

    All file bytes were checked before this transaction. Existing validators use
    those proofs to recheck stats only. No committing service wrapper is called.
    """
    if request.state!='running' or request.lease_token!=token or request.lease_until<=int(time.time()):
        raise HTTPException(409,'示例任务归属已失效，请使用原请求核对')
    source, shots = await new_project(db,request)
    await register_media(db,request.project_id,resources)
    pictures = {item['clip_index']:item['id'] for item in resources if item['kind']=='picture'}
    video_ids = {item['clip_index']:item['id'] for item in resources if item['kind']=='video'}
    character = await create_asset(db,request.project_id,AssetWrite(kind='character',name='金色光点',
        description='本项目原创几何角色，用圆形暖光绘制；不是人物、模型输出或用户素材。',media_ids=[pictures[3]]),verified_media=proofs)
    scene = await create_asset(db,request.project_id,AssetWrite(kind='scene',name=await original_scene_name(db),
        description='本项目原创几何场景，用多边形与线条绘制。',media_ids=[pictures[1]]),verified_media=proofs)
    character_id, scene_id = character['asset']['entity_id'], scene['asset']['entity_id']
    versions = [character['version']['id'],scene['version']['id']]
    for shot in shots:
        detail = await db.get(ShotDetail,shot['id']); detail.scene_id = scene_id
        db.add(ShotCharacterLink(shot_id=shot['id'],character_id=character_id,index=0,note='原创几何示例预置事实'))
    await db.flush()
    snapshot_id = await prepare_script(db,request,shots,character_id,scene_id,source)
    plan_draft, plan_id = str(uuid.uuid4()), str(uuid.uuid4())
    document = {'schema_version':1,'script_snapshot_id':snapshot_id,'model_id':LOCAL_INTENT,
        'shots':[{'shot_id':shot['id'],'version_ids':versions,'reference_media_ids':[pictures[index]],'prompt_addition':''}
            for index,shot in enumerate(shots,1)]}
    payload, verified = await build_payload(db,request.project_id,request.chapter_id,document,runtime=None,verified=proofs)
    if payload['binding'] is not None:
        raise HTTPException(409,'原创本地示例不得绑定或调用模型')
    db.add(ShotPlanDraft(id=plan_draft,project_id=request.project_id,chapter_id=request.chapter_id,revision=1,document=payload['document'])); await db.flush()
    db.add(ShotPlan(id=plan_id,project_id=request.project_id,draft_id=plan_draft,draft_revision=1,content_hash=content_hash(payload),payload=payload)); await db.flush()
    takes = []
    for index, shot in enumerate(shots,1):
        body = SelectionWrite(request_key=f'{request.id}-take-{index}',shot_id=shot['id'],expected_revision=0,
            media_id=video_ids[index],plan_id=plan_id,change_note='用户建立原创示例：本地几何代码制作的无声片段，非模型生成。')
        resolved = await resolve_source(db,request.project_id,body)
        await history_check(db,resolved,body,verified)
        evidence = videos[video_ids[index]]
        await recheck_media(db,request.project_id,evidence)
        event = await apply_verified_selection(db,request.project_id,body,resolved,evidence)
        takes.append(event['take_id'])
    timeline = {'schema_version':1,'plan_id':plan_id,'title':'点亮小岛 · 请确认剧情保留标记',
        'clips':[{'shot_id':shot['id'],'take_id':takes[index-1],'source_in_ms':0,'source_out_ms':4000,'source_audio':False}
            for index,shot in enumerate(shots,1)],
        'anchors':[{'kind':'beat','item_id':f'light-beat-{index}','take_id':take,'start_ms':0,'end_ms':4000,'confirmed':False}
            for index,take in enumerate(takes,1)]}
    timeline = await validate_timeline(db,request.project_id,request.chapter_id,timeline)
    draft_id = str(uuid.uuid4())
    db.add(EditDraft(id=draft_id,project_id=request.project_id,chapter_id=request.chapter_id,revision=1,document=timeline)); await db.flush()
    ready = LocalExampleReady(request_id=request.id,project_id=request.project_id,chapter_id=request.chapter_id,
        script_snapshot_id=snapshot_id,plan_id=plan_id,draft_id=draft_id)
    db.add(ready); await db.flush()
    for item in resources:
        db.add(LocalExampleMedia(request_id=request.id,project_id=request.project_id,clip_index=item['clip_index'],kind=item['kind'],media_id=item['id']))
    await db.flush()
    for index,take in enumerate(takes,1):
        db.add(LocalExampleTake(request_id=request.id,project_id=request.project_id,clip_index=index,take_id=take))
    await db.flush()
    # The SQL transition guard repeats the live-lease and complete-context checks
    # at the final write, so crossing expiry inside this transaction rolls it back.
    request.state, request.lease_token, request.lease_until = 'ready', None, None
    request.updated_at, request.reason = int(time.time()), ''
    await db.flush()
    return result_view(ready,replayed=False)
