"""Bind complete vision/ASR attempts to one original project before media can leave a worker."""
from fastapi import HTTPException
from sqlalchemy import select

from app.core.contracts.model_accounting import ModelPriceEnvelope,QuotedModelCall
from app.core.contracts.video_understanding import UnderstandingConnection
from app.models.execution_budget import ExecutionBudgetEntry
from app.models.model_charge import ModelCallCharge,ModelChargeBatch
from app.models.understanding_charge import UnderstandingAttemptCharge
from app.models.video_understanding import UnderstandingAttempt,UnderstandingJob
from app.services.worker.model_charges import actor,manifest,release_unprepared,reserve_in_transaction
from app.services.worker.model_job_charges import binding_digest


def units_for(source,connection):
    """Reserve ASR only for an actually present original audio track that the user explicitly selected."""
    checked=UnderstandingConnection.model_validate(connection)
    return {'vision':'video_vision',**({'asr':'video_asr'} if checked.transcribe_audio and source.get('audio_present') is True else {})}


async def attempt_charge(db,job_id,attempt,project_id,*,required=False):
    """Read only this concrete original-project attempt; legacy history never silently acquires new authority."""
    row=await db.scalar(select(UnderstandingAttemptCharge).where(UnderstandingAttemptCharge.job_id==job_id,
        UnderstandingAttemptCharge.attempt==attempt,UnderstandingAttemptCharge.project_id==project_id).execution_options(populate_existing=True))
    if required:
        if row is None:
            raise HTTPException(409,'原理解任务没有项目费用授权，未发送模型请求；请按当前项目配置新建任务')
        await verify_plan(db,row)
    return row


async def verify_plan(db,link):
    """Validate all persisted units, connections, price/entry pairs and the original manifest before the first request."""
    invalid=HTTPException(409,'原理解尝试的完整项目费用计划不一致，未发送模型请求')
    job=await db.scalar(select(UnderstandingJob).where(UnderstandingJob.id==link.job_id,
        UnderstandingJob.project_id==link.project_id).execution_options(populate_existing=True))
    attempt=await db.scalar(select(UnderstandingAttempt).where(UnderstandingAttempt.job_id==link.job_id,
        UnderstandingAttempt.attempt==link.attempt,UnderstandingAttempt.project_id==link.project_id))
    batch=await db.scalar(select(ModelChargeBatch).where(ModelChargeBatch.id==link.batch_id).execution_options(populate_existing=True))
    if job is None or attempt is None or batch is None or job.request_hash!=link.job_request_hash or batch.project_id!=link.project_id or batch.submitted_by!=link.submitted_by or batch.scope_key!=f'model-job:understanding:{link.job_id}:{link.attempt}':
        raise invalid
    actor(link.submitted_by)
    expected=units_for(job.source,job.connection)
    if not isinstance(link.bindings,dict) or set(link.bindings)!=set(expected.values()):
        raise invalid
    rows=(await db.execute(select(ModelCallCharge,ExecutionBudgetEntry).outerjoin(ExecutionBudgetEntry,
        ExecutionBudgetEntry.job_id==ModelCallCharge.id).where(ModelCallCharge.batch_id==link.batch_id)
        .execution_options(populate_existing=True))).all()
    if batch.call_count!=len(expected) or len(rows)!=len(expected) or {charge.unit_key for charge,_entry in rows}!=set(expected):
        raise invalid
    planned=[]
    for charge,entry in rows:
        binding=link.bindings.get(expected[charge.unit_key])
        try:
            quote=ModelPriceEnvelope.model_validate(charge.price_snapshot)
        except (ValueError,TypeError):
            raise invalid from None
        model_field='audio_model_id' if charge.unit_key=='asr' else 'vision_model_id'
        if not isinstance(binding,dict) or entry is None or charge.kind!=expected[charge.unit_key] or charge.project_id!=link.project_id or entry.project_id!=link.project_id or entry.quote_minor!=quote.max_call_minor or charge.model_id!=job.connection.get(model_field) or charge.model_id!=binding.get('model_id') or quote.model_name!=binding.get('model_name') or charge.provider_id!=binding.get('provider_id') or charge.provider_connection_version!=binding.get('provider_connection_version') or charge.binding_hash!=binding_digest(binding) or (quote.model_id,quote.provider_id,quote.provider_connection_version,quote.account_version)!=(charge.model_id,charge.provider_id,charge.provider_connection_version,charge.account_version):
            raise invalid
        planned.append(QuotedModelCall(unit_key=charge.unit_key,kind=charge.kind,binding_hash=charge.binding_hash,quote=quote))
    if manifest(link.project_id,link.submitted_by,planned)!=batch.manifest_hash:
        raise invalid
    return job


async def bind_attempt(db,job,*,submitted_by,request_key,request_hash,bindings,quotes):
    """Reserve the full checked attempt in its caller-owned writer transaction, then freeze its actual job linkage."""
    actor(submitted_by)
    if job.state!='queued':
        raise HTTPException(409,'只有新保存且未执行的理解尝试可以绑定费用')
    expected=units_for(job.source,job.connection)
    if set(bindings)!=set(expected.values()) or set(quotes)!=set(expected.values()):
        raise HTTPException(409,'理解费用没有覆盖完整视觉/语音调用计划')
    planned=[]
    for unit,kind in expected.items():
        quote=ModelPriceEnvelope.model_validate(quotes[kind])
        binding=bindings[kind]
        if quote.model_name!=binding.get('model_name') or quote.model_id!=job.connection.get('audio_model_id' if unit=='asr' else 'vision_model_id') or (quote.model_id,quote.provider_id,quote.provider_connection_version)!=(binding.get('model_id'),binding.get('provider_id'),binding.get('provider_connection_version')):
            raise HTTPException(409,'理解模型连接与受信价格不一致，未预留费用')
        planned.append(QuotedModelCall(unit_key=unit,kind=kind,binding_hash=binding_digest(binding),quote=quote))
    batch=await reserve_in_transaction(db,project_id=job.project_id,scope_key=f'model-job:understanding:{job.id}:{job.attempt}',submitted_by=submitted_by,calls=planned)
    prior=await attempt_charge(db,job.id,job.attempt,job.project_id)
    if prior is not None:
        if prior.batch_id!=batch or prior.submitted_by!=submitted_by or prior.request_key!=request_key or prior.request_hash!=request_hash or prior.job_request_hash!=job.request_hash or prior.bindings!=bindings:
            raise HTTPException(409,'原理解尝试已绑定其他内容或费用，未重新分配')
        await verify_plan(db,prior)
        return prior
    link=UnderstandingAttemptCharge(job_id=job.id,attempt=job.attempt,project_id=job.project_id,batch_id=batch,
        submitted_by=submitted_by,request_key=request_key,request_hash=request_hash,job_request_hash=job.request_hash,bindings=bindings)
    db.add(link);await db.flush();await verify_plan(db,link)
    return link


async def release_unsent(db,job_id,attempt,project_id):
    """Cancel/failure/recovery may release only untouched units from this original project and attempt."""
    link=await attempt_charge(db,job_id,attempt,project_id)
    if link is not None:
        await release_unprepared(db,project_id=link.project_id,batch_id=link.batch_id)
