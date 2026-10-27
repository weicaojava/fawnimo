"""Shared editing ownership and immutable source helpers."""
import copy
from fastapi import HTTPException
from sqlalchemy import select
from app.models.editing import TakeOrigin,SelectionCursor,SelectionEvent,EditDraft,EditPublication
from app.models.production import ProductionTake,ShotSelection,TakeVersion,AssetVersion
from app.models.studio import Shot,Chapter
from app.models.script_preparation import ScriptPublication
from app.services.script_preparation.service import canonical,content_hash,chapter_owned
from app.services.shot_plan.service import owned_plan


def issue(code,message,shot_id=None,item_id=None):
    """Create a typed-safe issue without exposing private source locations."""
    return {'code':code,'message':message,'shot_id':shot_id,'item_id':item_id}


async def owned_shot(db,project_id,shot_id):
    """Resolve a shot through its actual chapter rather than client ownership claims."""
    shot=await db.get(Shot,shot_id)
    chapter=await db.get(Chapter,shot.chapter_id) if shot else None
    if chapter is None or chapter.project_id!=project_id:raise HTTPException(404,'本工程镜头不存在')
    return shot


async def owned_take(db,project_id,take_id,shot_id=None):
    """Require both project and optional shot identity for historical selections."""
    take=await db.get(ProductionTake,take_id)
    if take is None or take.project_id!=project_id or (shot_id is not None and take.shot_id!=shot_id):raise HTTPException(422,'采用记录不属于此工程镜头')
    await owned_shot(db,project_id,take.shot_id)
    return take


async def plan_script(db,project_id,plan):
    """Check the script hash whose fixed requirements define coverage."""
    script=await db.get(ScriptPublication,plan.payload['script_snapshot_id'])
    if script is None or script.project_id!=project_id or script.content_hash!=plan.payload['script_snapshot_hash'] or content_hash(script.payload)!=script.content_hash:raise HTTPException(409,'计划原剧本快照校验失败')
    return script.payload['document']


def original_items(plan,script):
    """Derive the stable denominator and permitted entries from the frozen source."""
    result=[]
    for kind,key,mapping in [('beat','beats','beat_ids'),('dialogue','dialogues','dialogue_ids')]:
        for item in script[key]:
            result.append({'kind':kind,'item_id':item['id'],'required':item['required'],'text':item['text'],'speaker_id':item.get('speaker_id'),'shot_ids':[e['shot_id'] for e in plan.payload['entries'] if item['id'] in e['mapping'][mapping]]})
    return result


async def cursor_state(db,project_id,shot_id):
    """Retain legacy current pointers while starting the new CAS revision at zero."""
    await owned_shot(db,project_id,shot_id)
    cursor=await db.get(SelectionCursor,shot_id);selection=await db.get(ShotSelection,shot_id)
    if cursor and cursor.project_id!=project_id:raise HTTPException(409,'采用修订归属异常')
    return {'shot_id':shot_id,'revision':cursor.revision if cursor else 0,'take_id':selection.take_id if selection else None}


async def bounded(db,query,limit,label):
    """Never hide options beyond an arbitrary first page."""
    rows=list((await db.execute(query.limit(limit+1))).scalars())
    if len(rows)>limit:raise HTTPException(409,label+'超过当前处理上限，请缩小工程范围')
    return rows


def public_payload(payload):
    """Return a safe display view; its bytes are not the full private snapshot hash."""
    value=copy.deepcopy(payload)
    for clip in value.get('clips',[]):
        for key in ('relative_path','stat'):clip['source'].pop(key,None)
    return value


async def take_option(db,project_id,take,plan=None):
    """Build lightweight options from immutable origins, with no repeated decoding."""
    from app.models.production import ProductionMedia
    origin=await db.get(TakeOrigin,take.id);media=await db.get(ProductionMedia,take.media_id)
    problems=[]
    entry=next((e for e in plan.payload['entries'] if e['shot_id']==take.shot_id),None) if plan else None
    if origin is None:problems.append(issue('legacy_unlinked','历史采用未关联本计划，请显式导入同媒体创建新的人工采用',take.shot_id))
    elif plan is None or origin.plan_id!=plan.id or entry is None or origin.entry_hash!=entry['entry_hash']:problems.append(issue('other_plan','采用来源不属于当前计划镜头',take.shot_id))
    return {'id':take.id,'media_id':take.media_id,'name':media.name if media else '媒体不可用','origin_kind':origin.origin_kind if origin else take.provenance,'plan_id':origin.plan_id if origin else None,'entry_hash':origin.entry_hash if origin else None,'eligible_for_plan':not problems,'issues':problems,'duration_ms':origin.evidence['video']['duration_ms'] if origin else None}
