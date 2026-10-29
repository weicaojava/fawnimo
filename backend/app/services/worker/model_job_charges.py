"""Link actual business attempts to complete project reservations before any model transport can run."""
import hashlib
import json
import re

from fastapi import HTTPException
from sqlalchemy import select,text

from app.core.contracts.model_accounting import ModelPriceEnvelope,QuotedModelCall
from app.models.execution_budget import ExecutionBudgetEntry
from app.models.agent_execution import AgentRun,AgentNodeRun
from app.models.asset_image_generation import AssetImageJob
from app.models.model_charge import ModelCallCharge,ModelChargeBatch
from app.models.model_job_charge import ModelJobCharge
from app.services.worker.execution_budget import BudgetError
from app.services.worker.model_charges import actor,manifest,release_unprepared,reserve_in_transaction


async def begin_job_admission(db):
    """Own the writer lock before reading an idempotency key or creating a business attempt.

    The request's normal function-scoped dependency commits after response
    construction and rolls back failures. Internal callers already holding a
    writer transaction use bind_new_job directly instead.
    """
    if db.bind.dialect.name!='sqlite' or db.in_transaction():
        raise BudgetError('Project model job admission requires a fresh SQLite request transaction')
    await db.execute(text('BEGIN IMMEDIATE'))


async def job_charge(db,kind,job_id,*,required=False):
    """Read the saved original binding; old history remains readable but cannot authorize a new HTTP call."""
    binding=await db.scalar(select(ModelJobCharge).where(ModelJobCharge.job_kind==kind,ModelJobCharge.job_id==job_id)
        .execution_options(populate_existing=True))
    if binding is None and required:
        raise HTTPException(409,'原任务没有项目费用授权，不能自动执行；请从业务页面选择项目后新建任务')
    if required:
        await verify_complete_plan(db,kind,job_id,binding)
    return binding


async def verify_complete_plan(db,kind,job_id,binding):
    """Reject an incomplete or inconsistent persisted financial plan before any node can send HTTP.

    Service admission normally creates these rows together. This second check
    also protects scanners from incorrect native maintenance inserts, rather
    than assuming immutable rows were originally written by the right service.
    """
    invalid=HTTPException(409,'原任务的完整项目费用计划不一致，未发起模型请求')
    if kind not in {'agent','image'} or binding.job_kind!=kind or binding.job_id!=job_id:
        raise invalid
    model=AgentRun if kind=='agent' else AssetImageJob
    job=await db.scalar(select(model).where(model.id==job_id).execution_options(populate_existing=True))
    batch=await db.scalar(select(ModelChargeBatch).where(ModelChargeBatch.id==binding.batch_id).execution_options(populate_existing=True))
    if job is None or batch is None or job.request_hash!=binding.request_hash or batch.project_id!=binding.project_id or batch.submitted_by!=binding.submitted_by or batch.scope_key!='model-job:'+kind+':'+job_id:
        raise invalid
    actor(binding.submitted_by)
    expected={node['id'] for node in job.config_snapshot['workflow'] if node['type']=='llm'} if kind=='agent' else {'candidate'}
    call_kind=('model_connection_test' if job.kind=='connection_test' else 'agent_text') if kind=='agent' else 'asset_image'
    rows=(await db.execute(select(ModelCallCharge,ExecutionBudgetEntry).outerjoin(ExecutionBudgetEntry,
        ExecutionBudgetEntry.job_id==ModelCallCharge.id).where(ModelCallCharge.batch_id==batch.id)
        .execution_options(populate_existing=True))).all()
    if not expected or len(rows)!=len(expected) or batch.call_count!=len(expected) or {charge.unit_key for charge,_entry in rows}!=expected:
        raise invalid
    quotes=[]
    for charge,entry in rows:
        try:
            quote=ModelPriceEnvelope.model_validate(charge.price_snapshot)
        except (ValueError,TypeError):
            raise invalid from None
        if entry is None or charge.project_id!=binding.project_id or entry.project_id!=binding.project_id or entry.quote_minor!=quote.max_call_minor or charge.kind!=call_kind or charge.model_id!=job.model_id or charge.provider_id!=job.model_binding.get('provider_id') or charge.provider_connection_version!=job.model_binding.get('provider_connection_version') or charge.binding_hash!=binding_digest(job.model_binding) or (quote.model_name is not None and quote.model_name!=job.model_binding.get('model_name')) or (quote.model_id,quote.provider_id,quote.provider_connection_version,quote.account_version)!=(charge.model_id,charge.provider_id,charge.provider_connection_version,charge.account_version):
            raise invalid
        quotes.append(QuotedModelCall(unit_key=charge.unit_key,kind=charge.kind,binding_hash=charge.binding_hash,quote=quote))
    if len({unit.quote.fingerprint for unit in quotes})!=1 or manifest(binding.project_id,binding.submitted_by,quotes)!=batch.manifest_hash:
        raise invalid


async def bind_new_job(db,*,kind,job_id,project_id,submitted_by,request_hash,calls):
    """Atomically reserve and attach a complete model plan to one real attempt in the caller's writer transaction."""
    actor(submitted_by)
    if kind not in {'agent','image'} or not isinstance(job_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',job_id) or not isinstance(request_hash,str) or not re.fullmatch(r'[a-f0-9]{64}',request_hash):
        raise HTTPException(422,'模型任务费用绑定无效')
    prior=await job_charge(db,kind,job_id)
    if prior is not None and (prior.project_id!=project_id or prior.submitted_by!=submitted_by or prior.request_hash!=request_hash):
        raise HTTPException(409,'原任务已经固定其他项目、提交人或输入，费用没有重新绑定')
    model=AgentRun if kind=='agent' else AssetImageJob
    job=await db.scalar(select(model).where(model.id==job_id).execution_options(populate_existing=True))
    if job is None or job.request_hash!=request_hash or (prior is None and job.status!='queued'):
        raise HTTPException(409,'费用必须绑定同事务中已保存且尚未执行的原任务')
    calls=[QuotedModelCall.model_validate(call) for call in calls]
    if kind=='agent':
        nodes=(await db.scalars(select(AgentNodeRun).where(AgentNodeRun.run_id==job_id).order_by(AgentNodeRun.position))).all()
        expected_nodes=[(index,node['id'],node['type']) for index,node in enumerate(job.config_snapshot['workflow'])]
        if [(node.position,node.node_id,node.type) for node in nodes]!=expected_nodes:
            raise HTTPException(409,'原工作流的完整节点尚未保存，未授权模型费用')
    expected={node['id'] for node in job.config_snapshot['workflow'] if node['type']=='llm'} if kind=='agent' else {'candidate'}
    call_kind=('model_connection_test' if job.kind=='connection_test' else 'agent_text') if kind=='agent' else 'asset_image'
    if not expected or len(calls)!=len(expected) or {call.unit_key for call in calls}!=expected or any(
        call.kind!=call_kind or call.quote.model_id!=job.model_id or (call.quote.model_name is None and prior is None) or (call.quote.model_name is not None and call.quote.model_name!=job.model_binding.get('model_name')) or call.binding_hash!=binding_digest(job.model_binding)
        or call.quote.provider_id!=job.model_binding.get('provider_id')
        or call.quote.provider_connection_version!=job.model_binding.get('provider_connection_version') for call in calls):
        raise HTTPException(409,'原任务的模型、连接或完整调用清单与费用授权不一致')
    batch=await reserve_in_transaction(db,project_id=project_id,scope_key='model-job:'+kind+':'+job_id,submitted_by=submitted_by,calls=calls)
    if prior is not None:
        if prior.batch_id!=batch:
            raise HTTPException(409,'原任务的费用批次不一致，未接受新调用')
        await verify_complete_plan(db,kind,job_id,prior)
        return prior
    binding=ModelJobCharge(job_kind=kind,job_id=job_id,project_id=project_id,batch_id=batch,submitted_by=submitted_by,request_hash=request_hash)
    db.add(binding);await db.flush()
    await verify_complete_plan(db,kind,job_id,binding)
    return binding


def binding_digest(binding):
    """Share one credential-free canonical binding identity between admission and dispatch checks."""
    return hashlib.sha256(json.dumps(binding,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


async def charged_job_unit(db,binding,unit_key):
    """Resolve only a unit from the original batch and project; a sibling's unit is never a fallback."""
    charge=await db.scalar(select(ModelCallCharge).where(ModelCallCharge.batch_id==binding.batch_id,
        ModelCallCharge.project_id==binding.project_id,ModelCallCharge.unit_key==unit_key).execution_options(populate_existing=True))
    if charge is None:
        raise HTTPException(409,'原任务的调用费用单元缺失，未发起模型请求')
    return charge


async def release_job_unsent(db,kind,job_id):
    """Release only undispatched units of the saved original job; uncharged legacy history has nothing to release."""
    binding=await job_charge(db,kind,job_id)
    if binding is not None:
        await release_unprepared(db,project_id=binding.project_id,batch_id=binding.batch_id)
