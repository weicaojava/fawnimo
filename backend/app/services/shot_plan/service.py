"""Account-free shot-plan preparation and real immutable input provenance."""
import uuid
from decimal import Decimal
from fastapi import HTTPException
from sqlalchemy import select,update
from app.models.production import ProductionAsset,AssetVersion,ProductionMedia
from app.models.script_preparation import ScriptDraft,ScriptPublication
from app.models.shot_plan import ShotPlanDraft,ShotPlan,PlanJobLink
from app.models.execution import ExecutionJob
from app.models.execution_budget import ExecutionBudget
from app.models.studio import Character,Scene,Shot
from app.schemas.studio.production import VersionWrite
from app.services.studio.production import add_version,project_exists
from app.services.script_preparation.service import chapter_owned,scene_scope,preflight_snapshot
from app.services.worker.execution_budget import transaction,amount,BudgetError
from app.services.shot_plan.compiler import build_payload,validate_document,canonical,content_hash,public_policy,issue,media_evidence,preparation_issues


async def owned_draft(db,project_id,draft_id):
    """Resolve an actual owned chapter draft, never an imported project claim."""
    row=(await db.execute(select(ShotPlanDraft).where(ShotPlanDraft.id==draft_id,ShotPlanDraft.project_id==project_id))).scalar_one_or_none()
    if row is None:raise HTTPException(404,'本工程镜头计划草稿不存在')
    await chapter_owned(db,project_id,row.chapter_id)
    return row


async def owned_plan(db,project_id,plan_id):
    """Check owned immutable content integrity while allowing historical facts to drift."""
    plan=(await db.execute(select(ShotPlan).where(ShotPlan.id==plan_id,ShotPlan.project_id==project_id))).scalar_one_or_none()
    if plan is None:raise HTTPException(404,'本工程执行计划不存在')
    if content_hash(plan.payload)!=plan.content_hash:raise HTTPException(409,'执行计划内容校验失败')
    return plan


def runtime_issues(payload,runtime):
    """Separate frozen binding and live runtime availability from content readiness."""
    binding=payload.get('binding');problems=[]
    if binding is None:problems.append(issue('model_unbound','此准备计划未绑定模型，请保存新修订并重新编译'))
    policy=runtime.policies.get(payload['document']['model_id']) if runtime is not None else None
    if binding and (policy is None or public_policy(policy)!=binding):problems.append(issue('binding_changed','受信服务、账号、报价或能力与原计划不匹配'))
    if runtime is None or not runtime.allow_submission or not runtime.running:problems.append(issue('runtime_off','生成服务未配置或未启动，此准备计划不能提交'))
    if binding:
        try:amount(binding['quote_minor'])
        except BudgetError:problems.append(issue('quote_unknown','准确报价未知，不能提交'))
    return problems


async def validate_current(db,plan,verified=None):
    """Rebuild the same frozen binding against current DB facts and media credentials."""
    draft=await owned_draft(db,plan.project_id,plan.draft_id)
    if draft.revision!=plan.draft_revision:raise HTTPException(409,'计划草稿已更新，请重新编译新修订')
    payload,proof=await build_payload(db,plan.project_id,draft.chapter_id,plan.payload['document'],verified=verified,binding_override=plan.payload['binding'])
    if content_hash(payload)!=plan.content_hash:raise HTTPException(409,'当前事实、资产或媒体已失配，请保存新修订再编译')
    return payload,proof


async def preflight_plan(db,project_id,plan_id,runtime=None):
    """Inspect current content and live eligibility without reserving or charging."""
    plan=await owned_plan(db,project_id,plan_id);problems=[]
    try:await validate_current(db,plan)
    except HTTPException as error:problems.append(issue('plan_stale',str(error.detail)))
    runtime_problems=runtime_issues(plan.payload,runtime)
    binding=plan.payload.get('binding')
    if binding and not runtime_problems:
        budget=await db.get(ExecutionBudget,project_id)
        if budget is None or budget.currency!=binding['currency'] or budget.limit_minor-budget.reserved_minor-budget.spent_minor<binding['quote_minor']:runtime_problems.append(issue('budget_unavailable','本工程预算不存在、币种不符或不足'))
        from sqlalchemy import func
        count=await db.scalar(select(func.count()).select_from(ExecutionJob).where(ExecutionJob.project_id==project_id,ExecutionJob.snapshot['model_id'].as_string()==binding['model_id']))
        if count>=binding['iteration_limit']:runtime_problems.append(issue('iteration_limit','已达到本工程模型迭代上限'))
    return {'usable':not problems,'can_submit':not problems and not runtime_problems,'issues':problems,'runtime_issues':runtime_problems}


async def draft_view(db,project_id,draft_id,runtime=None):
    """Return per-document preparation issues and immutable compilation history."""
    draft=await owned_draft(db,project_id,draft_id)
    plans=(await db.execute(select(ShotPlan).where(ShotPlan.draft_id==draft_id).order_by(ShotPlan.draft_revision.desc()))).scalars().all()
    problems=[];runtime_problems=[]
    try:
        problems=await preparation_issues(db,project_id,draft.chapter_id,draft.document)
        if not problems:
            payload,_=await build_payload(db,project_id,draft.chapter_id,draft.document,runtime)
            runtime_problems=runtime_issues(payload,runtime)
    except HTTPException as error:problems.append(issue('preparation_incomplete',str(error.detail)))
    if plans and plans[0].draft_revision==draft.revision:
        check=await preflight_plan(db,project_id,plans[0].id,runtime)
        runtime_problems=check['runtime_issues']
        if not check['usable']:problems.append(issue('save_new_revision','当前修订历史计划已失配，请先保存新修订再编译'))
        policy=runtime.policies.get(draft.document['model_id']) if runtime is not None else None
        if policy is not None and plans[0].payload.get('binding')!=public_policy(policy):problems.append(issue('save_new_binding_revision','当前修订已锁定不同或空模型绑定，请先保存新修订再编译'))
    return {'id':draft.id,'chapter_id':draft.chapter_id,'revision':draft.revision,'document':draft.document,'readiness':{'can_compile':not problems,'issues':problems,'runtime_issues':runtime_problems},'plans':[{'id':p.id,'draft_revision':p.draft_revision,'content_hash':p.content_hash,'model_bound':p.payload.get('binding') is not None} for p in plans]}


async def create_draft(factory,project_id,body):
    """Register one incomplete plan draft for an existing chapter."""
    async with factory() as db:
        async with transaction(db):
            data=await validate_document(db,project_id,body.chapter_id,body.document)
            if (await db.execute(select(ShotPlanDraft.id).where(ShotPlanDraft.chapter_id==body.chapter_id))).first():raise HTTPException(409,'本章已有镜头计划草稿')
            identifier=str(uuid.uuid4());db.add(ShotPlanDraft(id=identifier,project_id=project_id,chapter_id=body.chapter_id,revision=1,document=data));return identifier


async def patch_draft(factory,project_id,draft_id,body):
    """Save a CAS revision without mutating prior plan inputs or old task identities."""
    async with factory() as db:
        async with transaction(db):
            draft=await owned_draft(db,project_id,draft_id)
            data=await validate_document(db,project_id,draft.chapter_id,body.document)
            changed=await db.execute(update(ShotPlanDraft).where(ShotPlanDraft.id==draft_id,ShotPlanDraft.revision==body.expected_revision).values(document=data,revision=ShotPlanDraft.revision+1))
            if not changed.rowcount:raise HTTPException(409,'计划草稿修订冲突，请刷新')


async def compile_draft(factory,project_id,draft_id,body,runtime=None):
    """Hash bytes outside the writer lock; recheck short-lived evidence before freezing."""
    async with factory() as db:
        draft=await owned_draft(db,project_id,draft_id)
        if draft.revision!=body.expected_revision:raise HTTPException(409,'计划草稿修订冲突')
        old=(await db.execute(select(ShotPlan).where(ShotPlan.draft_id==draft_id,ShotPlan.draft_revision==draft.revision))).scalar_one_or_none()
        if old:return old.id
        payload,proof=await build_payload(db,project_id,draft.chapter_id,draft.document,runtime)
    async with factory() as db:
        async with transaction(db):
            draft=await owned_draft(db,project_id,draft_id)
            if draft.revision!=body.expected_revision:raise HTTPException(409,'编译期间计划草稿已改变')
            old=(await db.execute(select(ShotPlan).where(ShotPlan.draft_id==draft_id,ShotPlan.draft_revision==draft.revision))).scalar_one_or_none()
            if old:return old.id
            current,_=await build_payload(db,project_id,draft.chapter_id,draft.document,runtime,verified=proof)
            if content_hash(current)!=content_hash(payload):raise HTTPException(409,'编译期间事实或配置改变，请重试')
            identifier=str(uuid.uuid4());db.add(ShotPlan(id=identifier,project_id=project_id,draft_id=draft_id,draft_revision=draft.revision,content_hash=content_hash(payload),payload=payload));return identifier


async def plan_view(db,project_id,plan_id):
    """Export historical immutable content without treating it as current qualification."""
    row=await owned_plan(db,project_id,plan_id)
    return {'id':row.id,'draft_revision':row.draft_revision,'content_hash':row.content_hash,'payload':row.payload}


async def register_asset(factory,project_id,body):
    """Add a first version for an actual legacy entity without duplicating that entity."""
    async with factory() as db:
        async with transaction(db):
            await project_exists(db,project_id)
            if body.kind=='character':
                entity=await db.get(Character,body.entity_id)
                if entity is None or entity.project_id!=project_id:raise HTTPException(422,'角色不属于本工程')
            else:entity=(await db.execute(select(Scene).where(Scene.id==body.entity_id,scene_scope(project_id)))).scalar_one_or_none()
            if entity is None:raise HTTPException(422,'场景不属于本工程')
            existing=(await db.execute(select(ProductionAsset).where(ProductionAsset.project_id==project_id,ProductionAsset.kind==body.kind,ProductionAsset.entity_id==body.entity_id))).scalars().all()
            if len(existing)>1:raise HTTPException(409,'同身份存在多份资产登记，须人工消歧')
            if existing:
                version=(await db.execute(select(AssetVersion).where(AssetVersion.asset_id==existing[0].id).order_by(AssetVersion.revision.desc()).limit(1))).scalar_one_or_none()
                if version is None:raise HTTPException(409,'已登记资产缺少版本')
                return {'asset_id':existing[0].id,'version_id':version.id}
            asset=ProductionAsset(id=str(uuid.uuid4()),project_id=project_id,kind=body.kind,name=entity.name,entity_id=entity.id)
            db.add(asset);await db.flush()
            version=await add_version(db,asset,VersionWrite(description=entity.description or '',expected_revision=0,media_ids=[]))
            return {'asset_id':asset.id,'version_id':version.id}


async def catalog(db,project_id,chapter_id,runtime=None):
    """Return bounded complete local choices, including latest actual script publication."""
    await chapter_owned(db,project_id,chapter_id)
    shots=(await db.execute(select(Shot).where(Shot.chapter_id==chapter_id).order_by(Shot.index,Shot.id).limit(201))).scalars().all()
    versions=(await db.execute(select(AssetVersion,ProductionAsset).join(ProductionAsset,ProductionAsset.id==AssetVersion.asset_id).where(ProductionAsset.project_id==project_id,ProductionAsset.archived.is_(False)).order_by(AssetVersion.id).limit(501))).all()
    media=(await db.execute(select(ProductionMedia).where(ProductionMedia.project_id==project_id).order_by(ProductionMedia.id).limit(501))).scalars().all()
    if len(shots)>200 or len(versions)>500 or len(media)>500:raise HTTPException(409,'本章镜头或工程版本/媒体超过目录上限')
    script=(await db.execute(select(ScriptPublication).join(ScriptDraft,ScriptDraft.id==ScriptPublication.draft_id).where(ScriptDraft.project_id==project_id,ScriptDraft.chapter_id==chapter_id).order_by(ScriptPublication.draft_revision.desc()).limit(1))).scalar_one_or_none()
    option=None
    if script:
        check=await preflight_snapshot(db,project_id,script.id)
        option={'id':script.id,'content_hash':script.content_hash,'draft_revision':script.draft_revision,'title':script.payload['document']['title'],'document':script.payload['document'],'usable':check['usable'],'issues':[issue(i['code'],i['message']) for i in check['issues']]}
    return {'script':option,'shots':[{'id':s.id,'index':s.index,'title':s.title,'status':s.status} for s in shots],'versions':[{'asset_id':a.id,'version_id':v.id,'kind':a.kind,'name':v.payload['name'],'entity_id':a.entity_id,'subject_id':a.subject_id,'revision':v.revision,'media_count':len(v.payload.get('media_ids',[]))} for v,a in versions],'media':[{'id':m.id,'name':m.name,'mime':m.mime,'size':m.size,'sha256':m.sha256} for m in media],'models':[public_policy(p) for p in runtime.policies.values()] if runtime is not None else []}


async def list_jobs(db,project_id,plan_id):
    """Expose owned lightweight provenance, never provider URLs or automatically adopted takes."""
    await owned_plan(db,project_id,plan_id)
    rows=(await db.execute(select(PlanJobLink,ExecutionJob).join(ExecutionJob,ExecutionJob.id==PlanJobLink.job_id).where(PlanJobLink.plan_id==plan_id,PlanJobLink.project_id==project_id).order_by(ExecutionJob.id).limit(201))).all()
    if len(rows)>200:raise HTTPException(409,'计划执行记录超过200项，请通过任务历史查询')
    result=[]
    for link,job in rows:
        available=False
        if job.state=='ready' and job.media_id:
            try:await media_evidence(db,project_id,job.media_id);available=True
            except HTTPException:pass
        from app.services.worker.execution_queries import safe_view
        safe=safe_view(job)
        result.append({'job_id':job.id,'task_id':job.task_id,'plan_id':plan_id,'shot_id':link.shot_id,'entry_hash':link.entry_hash,'state':safe['state'],'reason':safe['reason'],'media_id':job.media_id if job.state=='ready' else None,'media_available':available})
    return result


async def admit_planned_shot(factory,project_id,plan_id,shot_id,request_key,runtime=None,*,submitted_by='local_operator',confirmed=False,price_fingerprint=None):
    """Expose the one trusted planned admission service without circular import initialization."""
    from app.services.shot_plan.admission import admit_planned_shot as admit
    from app.services.worker.native_video_accounting import identity,recover_request
    selection={'kind':'plan','plan_id':plan_id,'shot_ids':[shot_id]}
    accountant=getattr(getattr(runtime,'worker',None),'native_accounting',None)
    if accountant is not None:
        if accountant.factory is not factory:raise HTTPException(503,'原生视频运行配置与当前数据库不一致，未接受调用')
        accepted=await accountant.admit(project_id,request_key,selection,submitted_by=submitted_by,confirmed=confirmed,
            price_fingerprint=price_fingerprint,runtime=runtime)
        return accepted['job_ids'][0]
    old=await recover_request(factory,project_id,request_key,identity(project_id,submitted_by,selection,price_fingerprint,confirmed=confirmed))
    if old:return old['job_ids'][0]
    return await admit(factory,project_id,plan_id,shot_id,request_key,runtime)
