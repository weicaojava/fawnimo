"""Atomic project reservations and no-resend dispatch evidence for bounded model-call batches."""
import hashlib
import json
import re
import uuid

from fastapi import HTTPException
from sqlalchemy import select,update

from app.core.contracts.model_accounting import QuotedModelCall
from app.models.execution_budget import ExecutionBudget,ExecutionBudgetEntry
from app.models.llm import Model,Provider
from app.models.model_charge import ModelCallCharge,ModelChargeBatch
import app.models.model_billing  # noqa: F401 -- register immutable evidence before any metadata creation
from app.models.studio import Project
from app.services.llm.connection_versions import connection_version
from app.services.worker.execution_budget import BudgetError,change_balance,transaction


def manifest(project_id,submitted_by,calls):
    """Bind the complete planned batch, independent of incidental list ordering."""
    document={'project_id':project_id,'submitted_by':submitted_by,'calls':[item.model_dump() for item in sorted(calls,key=lambda item:item.unit_key)]}
    return hashlib.sha256(json.dumps(document,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def actor(value):
    """Accept only a trusted gateway identity or an explicit local operator marker."""
    if value!='local_operator' and not (isinstance(value,str) and re.fullmatch(r'usr_[a-f0-9]{32}',value)):
        raise HTTPException(403,'模型费用需要受信的提交人身份')
    return value


async def reserve_in_transaction(db,*,project_id,scope_key,submitted_by,calls):
    """Reserve every call with the new job in its caller-owned SQLite writer transaction.

    The caller first opens BEGIN IMMEDIATE. Quotes are supplied by trusted server
    configuration, never by a public request body. A raised error rolls back the
    whole job, batch and reservation together at that outer boundary.
    """
    if db.bind.dialect.name!='sqlite' or not db.in_transaction():
        raise BudgetError('A caller-owned SQLite writer transaction is required')
    actor(submitted_by)
    if not isinstance(scope_key,str) or not 1<=len(scope_key)<=160 or not scope_key.strip():
        raise HTTPException(422,'模型费用批次标识无效')
    if not 1<=len(calls)<=64:
        raise HTTPException(422,'模型费用必须有1至64个互不重复的调用单元')
    calls=[QuotedModelCall.model_validate(item) for item in calls]
    if len({item.unit_key for item in calls})!=len(calls):
        raise HTTPException(422,'模型费用必须有1至64个互不重复的调用单元')
    fingerprint=manifest(project_id,submitted_by,calls)
    prior=await db.scalar(select(ModelChargeBatch).where(ModelChargeBatch.scope_key==scope_key))
    if prior is not None:
        if prior.project_id!=project_id or prior.manifest_hash!=fingerprint or prior.submitted_by!=submitted_by:
            raise HTTPException(409,'原批次已绑定其他项目、调用或费用版本')
        return prior.id
    if await db.get(Project,project_id) is None:
        raise HTTPException(404,'费用所属项目不存在')
    from app.services.worker.model_billing import admission_review_block
    await admission_review_block(db,project_id,calls)
    budget=await db.scalar(select(ExecutionBudget).where(ExecutionBudget.project_id==project_id).execution_options(populate_existing=True))
    if budget is None or budget.currency!='CNY':
        raise HTTPException(409,'请先设置该项目的人民币预算')
    for item in calls:
        model=await db.get(Model,item.quote.model_id)
        provider=await db.get(Provider,item.quote.provider_id)
        if model is None or provider is None or model.provider_id!=provider.id or provider.status=='disabled':
            raise HTTPException(409,'报价对应的项目空间模型连接不可用')
        if await connection_version(db,provider.id)!=item.quote.provider_connection_version:
            raise HTTPException(409,'供应商连接已变化，请核对新的受信报价')
    total=sum(item.quote.max_call_minor for item in calls)
    try:
        await change_balance(db,budget,total,0)
    except BudgetError:
        raise HTTPException(409,'本项目剩余额度不足，未创建模型费用批次') from None
    batch=ModelChargeBatch(id=uuid.uuid4().hex,scope_key=scope_key,project_id=project_id,submitted_by=submitted_by,
        manifest_hash=fingerprint,call_count=len(calls))
    db.add(batch);await db.flush()
    for item in calls:
        identity='mc_'+uuid.uuid4().hex
        db.add(ExecutionBudgetEntry(job_id=identity,project_id=project_id,quote_minor=item.quote.max_call_minor,state='reserved'))
        await db.flush()
        db.add(ModelCallCharge(id=identity,project_id=project_id,batch_id=batch.id,unit_key=item.unit_key,kind=item.kind,
            model_id=item.quote.model_id,provider_id=item.quote.provider_id,provider_connection_version=item.quote.provider_connection_version,
            account_version=item.quote.account_version,binding_hash=item.binding_hash,price_snapshot=item.quote.model_dump(),dispatch_state='planned'))
    await db.flush()
    return batch.id


async def reserve_batch(factory,**kwargs):
    """Expose a complete fresh transaction for operator/tests; job admission uses the internal variant."""
    async with factory() as db:
        async with transaction(db):
            return await reserve_in_transaction(db,**kwargs)


async def owned_call(db,project_id,charge_id):
    """Resolve the matching typed call and ledger hold before exposing or changing either."""
    charge=await db.scalar(select(ModelCallCharge).where(ModelCallCharge.id==charge_id).execution_options(populate_existing=True))
    entry=await db.scalar(select(ExecutionBudgetEntry).where(ExecutionBudgetEntry.job_id==charge_id).execution_options(populate_existing=True))
    if charge is None or entry is None or charge.project_id!=project_id or entry.project_id!=project_id:
        raise HTTPException(404,'本项目模型调用费用不存在')
    return charge,entry


async def prepare_call(db,*,project_id,charge_id,binding_hash):
    """Commit possible acceptance before HTTP; an already prepared call must never be sent again.

    The caller owns the same writer transaction as lease checks and coherent
    credential capture. Once committed, interruption or cancellation retains the
    unknown hold until trusted reconciliation proves the actual outcome.
    """
    if not db.in_transaction():
        raise BudgetError('Dispatch preparation requires a writer transaction')
    charge,entry=await owned_call(db,project_id,charge_id)
    if charge.dispatch_state!='planned' or entry.state!='reserved':
        raise HTTPException(409,'原调用已准备或结果未知，不能重新发送')
    if binding_hash!=charge.binding_hash or await connection_version(db,charge.provider_id)!=charge.provider_connection_version:
        raise HTTPException(409,'调用连接版本已变化，未向新连接发送')
    provider=await db.get(Provider,charge.provider_id)
    model=await db.get(Model,charge.model_id)
    if provider is None or provider.status=='disabled' or model is None or model.provider_id!=provider.id:
        raise HTTPException(409,'原调用模型连接不可用')
    changed=await db.execute(update(ModelCallCharge).where(ModelCallCharge.id==charge_id,ModelCallCharge.project_id==project_id,
        ModelCallCharge.dispatch_state=='planned').values(dispatch_state='prepared').execution_options(synchronize_session=False))
    if changed.rowcount!=1:
        raise HTTPException(409,'原调用已由其他执行器准备，不能重新发送')
    held=await db.execute(update(ExecutionBudgetEntry).where(ExecutionBudgetEntry.job_id==charge_id,ExecutionBudgetEntry.project_id==project_id,
        ExecutionBudgetEntry.state=='reserved').values(state='unknown').execution_options(synchronize_session=False))
    if held.rowcount!=1:
        raise BudgetError('Dispatch and reservation evidence changed')
    await db.flush()


async def record_accepted_call(db,*,project_id,charge_id,provider_request_id):
    """Record a sanitized trusted response identity without inferring or settling a real bill."""
    if not db.in_transaction():
        raise BudgetError('Response evidence requires a writer transaction')
    if not isinstance(provider_request_id,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',provider_request_id):
        raise HTTPException(422,'供应商请求标识无效，原费用继续保留')
    charge,entry=await owned_call(db,project_id,charge_id)
    if charge.dispatch_state=='accepted' and charge.provider_request_id==provider_request_id:
        return
    if charge.dispatch_state not in {'prepared','unknown'} or entry.state!='unknown':
        raise HTTPException(409,'原调用不接受此响应记录')
    if charge.provider_request_id and charge.provider_request_id!=provider_request_id:
        raise HTTPException(409,'原调用已有不同的供应商请求标识')
    changed=await db.execute(update(ModelCallCharge).where(ModelCallCharge.id==charge_id,ModelCallCharge.project_id==project_id,
        ModelCallCharge.dispatch_state.in_({'prepared','unknown'}),ModelCallCharge.provider_request_id.is_(None)).values(
        provider_request_id=provider_request_id,dispatch_state='accepted').execution_options(synchronize_session=False))
    if changed.rowcount!=1:
        current=(await db.execute(select(ModelCallCharge.dispatch_state,ModelCallCharge.provider_request_id).where(ModelCallCharge.id==charge_id))).one()
        if current!=('accepted',provider_request_id):
            raise HTTPException(409,'原调用响应已由其他执行器记录')
    await db.flush()


async def release_unprepared(db,*,project_id,batch_id):
    """Release only still-planned calls; prepared/accepted/unknown units retain their full holds."""
    if not db.in_transaction():
        raise BudgetError('Unsent release requires a writer transaction')
    batch=await db.get(ModelChargeBatch,batch_id)
    if batch is None or batch.project_id!=project_id:
        raise HTTPException(404,'本项目模型费用批次不存在')
    rows=(await db.scalars(select(ModelCallCharge).where(ModelCallCharge.batch_id==batch_id,ModelCallCharge.project_id==project_id,
        ModelCallCharge.dispatch_state=='planned'))).all()
    budget=await db.scalar(select(ExecutionBudget).where(ExecutionBudget.project_id==project_id).execution_options(populate_existing=True))
    total=0
    for charge in rows:
        entry=await db.get(ExecutionBudgetEntry,charge.id)
        if entry is None or entry.project_id!=project_id or entry.state!='reserved':
            raise BudgetError('Unsent call and reservation evidence disagree')
        total+=entry.quote_minor
        entry.state='released'
        charge.dispatch_state='not_sent'
    if total:
        await change_balance(db,budget,-total,0)
    await db.flush()
    return len(rows)
