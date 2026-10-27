"""Editing service facade and bounded real options for the existing editor."""
from sqlalchemy import select
from app.models.editing import TakeOrigin,EditDraft,EditPublication
from app.models.production import ProductionTake,ProductionMedia
from app.models.shot_plan import ShotPlan,ShotPlanDraft,PlanJobLink
from app.models.execution import ExecutionJob
from app.models.studio import Shot
from app.services.editing.common import chapter_owned,owned_plan,plan_script,original_items,bounded,take_option,cursor_state
from app.services.editing.selection import write_selection,selection_history
from app.services.editing.timeline import create_draft,patch_draft,freeze_draft,draft_view,publication_view,preflight_publication,owned_publication


async def catalog(db,project_id,chapter_id,plan_id=None):
    """List actual bounded chapter plans, sources and candidates without decoding on GET."""
    await chapter_owned(db,project_id,chapter_id)
    plans=await bounded(db,select(ShotPlan).join(ShotPlanDraft,ShotPlan.draft_id==ShotPlanDraft.id).where(ShotPlan.project_id==project_id,ShotPlanDraft.chapter_id==chapter_id).order_by(ShotPlan.id),200,'本章计划')
    plan=await owned_plan(db,project_id,plan_id) if plan_id else None
    if plan and plan.payload['chapter_id']!=chapter_id:
        from fastapi import HTTPException
        raise HTTPException(422,'计划不属于本章')
    shots=await bounded(db,select(Shot).where(Shot.chapter_id==chapter_id).order_by(Shot.index,Shot.id),100,'章节镜头')
    takes=await bounded(db,select(ProductionTake).join(Shot,Shot.id==ProductionTake.shot_id).where(ProductionTake.project_id==project_id,Shot.chapter_id==chapter_id).order_by(ProductionTake.id),500,'章节采用记录')
    media=await bounded(db,select(ProductionMedia).where(ProductionMedia.project_id==project_id,ProductionMedia.mime=='video/mp4').order_by(ProductionMedia.id),500,'工程视频')
    candidates=list((await db.execute(select(ExecutionJob,PlanJobLink).join(PlanJobLink,PlanJobLink.job_id==ExecutionJob.id).join(Shot,Shot.id==PlanJobLink.shot_id).where(ExecutionJob.project_id==project_id,Shot.chapter_id==chapter_id).order_by(ExecutionJob.id).limit(501))).all())
    if len(candidates)>500:
        from fastapi import HTTPException
        raise HTTPException(409,'章节候选超过500项，请缩小范围')
    by_shot={s.id:s for s in shots};clips=[]
    if plan:
        for entry in plan.payload['entries']:
            shot=by_shot.get(entry['shot_id'])
            if shot is None:
                from fastapi import HTTPException
                raise HTTPException(409,'历史计划引用的实际镜头已不存在')
            clips.append({'shot_id':shot.id,'title':shot.title,'index':shot.index,'duration_ms':entry['duration_ms'],'takes':[await take_option(db,project_id,t,plan) for t in takes if t.shot_id==shot.id]})
    return {'project_id':project_id,'chapter_id':chapter_id,'plans':[{'id':p.id,'content_hash':p.content_hash,'title':str(p.payload['facts'].get('chapter',{}).get('title','')) or p.id[:8]} for p in plans],'plan_id':plan.id if plan else None,'clips':clips,'items':original_items(plan,await plan_script(db,project_id,plan)) if plan else [],'selections':[await cursor_state(db,project_id,s.id) for s in shots],'media':[{'id':m.id,'name':m.name,'sha256':m.sha256,'size':m.size,'mime':m.mime} for m in media],'candidates':[{'job_id':j.id,'shot_id':l.shot_id,'plan_id':l.plan_id,'state':j.state,'media_id':j.media_id if j.state=='ready' else None} for j,l in candidates]}
