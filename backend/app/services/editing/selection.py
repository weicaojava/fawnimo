"""Explicit idempotent adoption with immutable provenance and selection CAS."""
import uuid
from fastapi import HTTPException
from sqlalchemy import select
from app.models.editing import TakeOrigin,SelectionCursor,SelectionEvent
from app.models.production import ProductionTake,TakeVersion,ShotSelection
from app.models.execution import ExecutionJob
from app.models.shot_plan import PlanJobLink
from app.services.worker.execution_budget import transaction
from app.services.shot_plan.admission import entry_for,request_identity
from app.services.shot_plan.service import owned_plan,validate_current
from app.services.editing.common import owned_shot,owned_take,cursor_state,content_hash,bounded,take_option


def event_view(event,replayed=False):
    """Replay describes its historical event and never claims current selection."""
    return {'event_id':event.id,'shot_id':event.shot_id,'take_id':event.take_id,'revision':event.revision,'origin_kind':event.origin_kind,'replayed':replayed}


async def old_event(db,project_id,body):
    """Resolve exact replay before checking present sources or selection eligibility."""
    row=(await db.execute(select(SelectionEvent).where(SelectionEvent.project_id==project_id,SelectionEvent.request_key==body.request_key))).scalar_one_or_none()
    if row and row.request_hash!=content_hash(body.model_dump()):raise HTTPException(409,'请求标识已用于不同采用意图')
    return row


async def resolve_source(db,project_id,body):
    """Derive fixed media, plan and versions from owned records, never client provenance."""
    shot=await owned_shot(db,project_id,body.shot_id)
    if body.take_id:
        take=await owned_take(db,project_id,body.take_id,shot.id)
        origin=await db.get(TakeOrigin,take.id)
        if body.plan_id:
            context_plan=await owned_plan(db,project_id,body.plan_id)
            if context_plan.payload['chapter_id']!=shot.chapter_id:raise HTTPException(422,'查看计划不属于此镜头章节')
        # A viewing plan is not an assertion that historical media gains its origin.
        # Timeline qualification still requires an exact immutable Origin match.
        return {'shot':shot,'take':take,'origin':origin,'media_id':take.media_id,'plan':None,'entry':None,'kind':origin.origin_kind if origin else take.provenance}
    job=None
    if body.job_id:
        job=await db.get(ExecutionJob,body.job_id);link=await db.get(PlanJobLink,body.job_id)
        if job is None or job.project_id!=project_id or job.state!='ready' or link is None or link.project_id!=project_id or link.shot_id!=shot.id:raise HTTPException(422,'候选不是此镜头已接收的计划结果')
        if body.plan_id and body.plan_id!=link.plan_id:raise HTTPException(422,'候选与指定计划不一致')
        plan=await owned_plan(db,project_id,link.plan_id);entry=entry_for(plan,shot.id)
        if link.entry_hash!=entry['entry_hash'] or job.snapshot!=entry['request'] or job.request_hash!=request_identity(plan,entry):raise HTTPException(409,'候选执行来源校验失败')
        existing=(await db.execute(select(TakeOrigin).where(TakeOrigin.job_id==job.id))).scalar_one_or_none()
        take=await owned_take(db,project_id,existing.take_id,shot.id) if existing else None
        return {'shot':shot,'take':take,'origin':existing,'media_id':job.media_id,'plan':plan,'entry':entry,'kind':'execution_candidate_unqualified'}
    plan=await owned_plan(db,project_id,body.plan_id);entry=entry_for(plan,shot.id)
    if plan.payload['chapter_id']!=shot.chapter_id:raise HTTPException(422,'计划不属于当前镜头章节')
    return {'shot':shot,'take':None,'origin':None,'media_id':body.media_id,'plan':plan,'entry':entry,'kind':'user_import'}


async def history_check(db,resolved,body,proof=None):
    """Content drift needs explicit consent; runtime availability is irrelevant here."""
    if resolved['plan'] is None:return None
    try:
        _,evidence=await validate_current(db,resolved['plan'],verified=proof)
        return evidence
    except HTTPException:
        if not body.accept_historical or not body.change_note.strip():raise HTTPException(409,'来源计划已是历史版本，请明确接受历史来源并填写说明') from None
        return None


async def write_selection(factory,project_id,body):
    """Probe without a writer lock, then atomically append origin/event and select once."""
    from app.services.editing.media import probe_owned,recheck_media
    async with factory() as db:
        old=await old_event(db,project_id,body)
        if old:return event_view(old,True)
        source=await resolve_source(db,project_id,body)
        proof=await history_check(db,source,body)
        evidence=await probe_owned(db,project_id,source['media_id'])
        if evidence['mime']!='video/mp4':raise HTTPException(422,'采用需要可完整解码的视频，图片不能作为片段')
        fingerprint=(source['media_id'],source['plan'].content_hash if source['plan'] else None,source['entry']['entry_hash'] if source['entry'] else None,source['take'].id if source['take'] else None)
    async with factory() as db:
        async with transaction(db):
            old=await old_event(db,project_id,body)
            if old:return event_view(old,True)
            source=await resolve_source(db,project_id,body)
            current_fingerprint=(source['media_id'],source['plan'].content_hash if source['plan'] else None,source['entry']['entry_hash'] if source['entry'] else None,source['take'].id if source['take'] else None)
            # Another exact candidate adoption can safely supply its canonical Take.
            if current_fingerprint[:3]!=fingerprint[:3]:raise HTTPException(409,'候选身份已改变，请重新核对')
            if source['plan'] is not None and proof is not None:await history_check(db,source,body,proof)
            await recheck_media(db,project_id,evidence)
            state=await cursor_state(db,project_id,body.shot_id)
            if state['revision']!=body.expected_revision:raise HTTPException(409,'当前采用已改变，请刷新后确认')
            take=source['take']
            if take is None:
                versions=[v['version_id'] for v in source['entry']['versions']]
                take=ProductionTake(id=str(uuid.uuid4()),project_id=project_id,shot_id=body.shot_id,media_id=source['media_id'],provenance=source['kind'],version_ids=versions)
                db.add(take);await db.flush()
                for version in versions:db.add(TakeVersion(take_id=take.id,version_id=version,project_id=project_id))
                db.add(TakeOrigin(take_id=take.id,project_id=project_id,job_id=body.job_id,plan_id=source['plan'].id,shot_id=body.shot_id,entry_hash=source['entry']['entry_hash'],origin_kind=source['kind'],evidence=evidence))
                await db.flush()
            selection=await db.get(ShotSelection,body.shot_id)
            if selection:selection.take_id=take.id
            else:db.add(ShotSelection(shot_id=body.shot_id,project_id=project_id,take_id=take.id))
            cursor=await db.get(SelectionCursor,body.shot_id)
            revision=state['revision']+1
            if cursor:cursor.revision=revision
            else:db.add(SelectionCursor(shot_id=body.shot_id,project_id=project_id,revision=revision))
            event=SelectionEvent(id=str(uuid.uuid4()),project_id=project_id,request_key=body.request_key,request_hash=content_hash(body.model_dump()),shot_id=body.shot_id,take_id=take.id,revision=revision,origin_kind=source['kind'],change_note=body.change_note)
            db.add(event);await db.flush();return event_view(event)


async def selection_history(db,project_id,shot_id):
    """Read complete bounded event and Take histories without redecoding media."""
    state=await cursor_state(db,project_id,shot_id)
    takes=await bounded(db,select(ProductionTake).where(ProductionTake.project_id==project_id,ProductionTake.shot_id==shot_id).order_by(ProductionTake.id),200,'镜头采用历史')
    events=await bounded(db,select(SelectionEvent).where(SelectionEvent.project_id==project_id,SelectionEvent.shot_id==shot_id).order_by(SelectionEvent.revision.desc()),200,'镜头选择事件')
    return {'shot_id':shot_id,'current_take_id':state['take_id'],'revision':state['revision'],'takes':[await take_option(db,project_id,t) for t in takes],'events':[event_view(e) for e in events]}
