"""Trusted internal admission: capability, reference, iteration and budget atomicity.

Application code supplies AdmissionPolicy. Imported requests cannot supply prices,
endpoints, credentials or their own iteration counter. No transport is called here.
"""
import hashlib
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import JSON, func, insert, literal, select
from app.core.contracts.execution_capabilities import Capability, preflight
from app.core.contracts.execution_binding import binding_fingerprint
from app.core.contracts.task_safety import validate_task_payload
from app.models.execution import ExecutionJob
from app.models.script_preparation import ScriptDraft
from app.models.execution_binding import ExecutionBinding
from app.models.execution_budget import ExecutionBudget, ExecutionBudgetEntry
from app.models.task import GenerationTask
from app.services.studio.production import project_exists
from app.services.studio.production_media import scoped_media
from app.services.worker.execution_budget import BudgetError, amount, change_balance, transaction


@dataclass(frozen=True)
class AdmissionPolicy:
    """Fixed application-owned capability, minor-unit quote and model iteration cap."""
    capability: Capability
    quote_minor: int | None
    currency: str
    iteration_limit: int
    binding_id: str='unqualified-fixture'


def freeze_request(snapshot: dict, policy: AdmissionPolicy) -> tuple[dict, str]:
    """Validate inert JSON, detach caller-owned objects and calculate a canonical hash."""
    if not isinstance(snapshot,dict): raise HTTPException(422,'任务参数必须为对象')
    validate_task_payload(snapshot)
    if set(snapshot)-{'model_id','duration','references','prompt','prompt_revision'}:
        raise HTTPException(422,'包含未支持的生成参数')
    if not isinstance(snapshot.get('model_id'),str) or not snapshot['model_id'] or type(snapshot.get('duration')) is not int:
        raise HTTPException(422,'模型标识或时长不符合要求')
    if not isinstance(snapshot.get('prompt'),str) or not snapshot['prompt'].strip() or len(snapshot['prompt'])>32000:
        raise HTTPException(422,'提示词须为非空文本且不超过32000字')
    revision=snapshot.get('prompt_revision')
    if revision is not None and (not isinstance(revision,str) or not revision or len(revision)>128):
        raise HTTPException(422,'提示词版本标识不符合要求')
    references=snapshot.get('references',[])
    if not isinstance(references,list) or len(references)>100:
        raise HTTPException(422,'参考素材列表不符合要求')
    for reference in references:
        if not isinstance(reference,dict) or set(reference)!={'media_id','kind'} or not isinstance(reference['kind'],str) or not isinstance(reference['media_id'],str) or not reference['media_id']:
            raise HTTPException(422,'参考素材类型不符合要求')
    if len({reference['media_id'] for reference in references})!=len(references):
        raise HTTPException(422,'参考素材不能重复')
    try:
        encoded=json.dumps(snapshot,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
        if len(encoded.encode('utf8'))>256000: raise ValueError()
        return json.loads(encoded),hashlib.sha256(encoded.encode('utf8')).hexdigest()
    except (TypeError,ValueError,OverflowError): raise HTTPException(422,'任务参数不是有效的有限JSON数据') from None


async def verify_references(db, project_id: str, snapshot: dict) -> None:
    """Require project-owned, hash-verified local files matching their declared kind."""
    allowed={'image':{'image/png','image/jpeg','image/webp'},'video':{'video/mp4'},'audio':{'audio/wav'}}
    for reference in snapshot.get('references',[]):
        row=await scoped_media(db,project_id,reference['media_id'])
        if row.mime not in allowed.get(reference['kind'],set()):
            raise HTTPException(422,'参考素材内容类型与用途不符')


async def admit(factory, project_id: str, request_key: str, snapshot: dict, policy: AdmissionPolicy) -> str:
    """Commit one task/job/hold atomically, or return the previously committed identity.

    BEGIN IMMEDIATE serializes SQLite admission writers. Iteration count is
    derived from the append-only job journal, counting even cancelled jobs. The
    insert repeats its limit predicate, and the balance update uses revision CAS.
    A lost HTTP response is recovered by the same project/key/hash without any
    additional iteration, task, hold, or submission. This service never submits.
    """
    if not isinstance(project_id,str) or not project_id or len(project_id)>64:
        raise HTTPException(422,'工程标识不符合要求')
    if not isinstance(request_key,str) or not request_key.strip() or len(request_key)>128:
        raise HTTPException(422,'请求标识不符合要求')
    frozen,request_hash=freeze_request(snapshot,policy)
    try:
        async with factory() as db:
            async with transaction(db):
                await project_exists(db,project_id)
                existing=(await db.execute(select(ExecutionJob).where(ExecutionJob.project_id==project_id,ExecutionJob.request_key==request_key))).scalar_one_or_none()
                if existing is not None:
                    if existing.request_hash!=request_hash: raise HTTPException(409,'请求标识已被其他版本使用')
                    return existing.id
                if (await db.execute(select(ScriptDraft.id).where(ScriptDraft.project_id==project_id).limit(1))).first():
                    raise HTTPException(409,'本工程已启用结构化剧本，请前往分镜工作室，通过镜头执行计划逐镜提交')
                job_id=await _insert_reserved_job(db,project_id,request_key,frozen,request_hash,policy)
            return job_id
    except BudgetError: raise HTTPException(409,'预算不足或预算状态已改变，未创建任务') from None


async def _insert_reserved_job(db, project_id: str, request_key: str, frozen: dict, request_hash: str, policy: AdmissionPolicy, *, reference_validator=verify_references) -> str:
    """Trusted callers insert one job/binding/hold within their existing writer transaction.

    Public legacy admission retains its script guard; planned callers must have
    loaded and validated a persisted immutable plan before reaching this core.
    This function does not commit and exposes no HTTP guard-bypass parameter.
    """
    freeze_request(frozen,policy)
    # Replay above is purely recovery: new runtime policy must never
    # rebind an existing job, even if its old duration is now retired.
    try:
        amount(policy.quote_minor)
        binding_hash=binding_fingerprint(policy)
    except (BudgetError,ValueError,TypeError):
        raise HTTPException(409,'报价或受信服务配置版本无效，未创建任务') from None
    if not isinstance(policy.currency,str) or not policy.currency or len(policy.currency)>16:
        raise HTTPException(409,'尚未配置有效预算币种')
    preflight(frozen,policy.capability,quoted_cost=Decimal(policy.quote_minor),remaining_budget=Decimal(policy.quote_minor),iteration=0,iteration_limit=policy.iteration_limit)
    await reference_validator(db,project_id,frozen)
    budget=await db.scalar(select(ExecutionBudget).where(ExecutionBudget.project_id==project_id).execution_options(populate_existing=True))
    if budget is None or budget.currency!=policy.currency:
        raise HTTPException(409,'尚未配置对应币种的工程预算')
    count=select(func.count()).select_from(ExecutionJob).where(ExecutionJob.project_id==project_id,ExecutionJob.snapshot['model_id'].as_string()==policy.capability.model_id).scalar_subquery()
    iteration=await db.scalar(select(count))
    preflight(frozen,policy.capability,quoted_cost=Decimal(policy.quote_minor),remaining_budget=Decimal(budget.limit_minor-budget.reserved_minor-budget.spent_minor),iteration=iteration,iteration_limit=policy.iteration_limit)
    job_id=str(uuid.uuid4());task_id=str(uuid.uuid4())
    db.add(GenerationTask(id=task_id,mode='async_polling',task_kind='durable_execution',status='pending',progress=0,payload={'execution_job_id':job_id,'snapshot':frozen},result={'execution_job_id':job_id,'execution_state':'queued'},error=''))
    await db.flush()
    # Conditional insertion is an additional count guard; it is in
    # the same locked transaction as the reservation revision CAS.
    fields=['id','task_id','project_id','request_key','request_hash','snapshot','media_id','state','lease_until','next_due','query_retries','download_retries','cancel_requested','reason']
    values=[literal(job_id),literal(task_id),literal(project_id),literal(request_key),literal(request_hash),literal(frozen,type_=JSON),literal(str(uuid.uuid4())),literal('queued'),literal(0),literal(0),literal(0),literal(0),literal(False),literal('')]
    inserted=await db.execute(insert(ExecutionJob).from_select(fields,select(*values).where(count<policy.iteration_limit),include_defaults=False))
    if inserted.rowcount!=1: raise HTTPException(409,'已达到迭代次数限制')
    await change_balance(db,budget,policy.quote_minor,0)
    db.add(ExecutionBudgetEntry(job_id=job_id,project_id=project_id,quote_minor=policy.quote_minor,state='reserved'))
    db.add(ExecutionBinding(job_id=job_id,binding_hash=binding_hash))
    await db.flush()
    return job_id
