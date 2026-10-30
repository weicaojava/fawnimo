"""Adopt native job reservations into complete original-project common billing without reserving twice."""
import uuid

from fastapi import HTTPException
from sqlalchemy import select

from app.core.contracts.model_accounting import ModelPriceEnvelope, QuotedModelCall
from app.models.execution import ExecutionJob, SubmissionAttempt
from app.models.execution_binding import ExecutionBinding
from app.models.execution_budget import ExecutionBudget,ExecutionBudgetEntry
from app.models.model_charge import ModelCallCharge, ModelChargeBatch
from app.models.model_billing import ModelBillingReceipt
from app.models.native_video_charge import NativeVideoChargeBatch, NativeVideoJobCharge
from app.services.worker.model_billing import admission_review_block,project_read_snapshot
from app.services.worker.model_charges import actor, manifest
from app.services.worker.model_job_charges import binding_digest
from app.services.worker.execution_budget import BudgetError,change_balance


async def job_charge(db,job_id,*,required=False):
    """Old native history remains readable; a new bill link is never inferred from its hold or task."""
    link=await db.scalar(select(NativeVideoJobCharge).where(NativeVideoJobCharge.job_id==job_id)
        .execution_options(populate_existing=True))
    if required:
        if link is None:
            raise HTTPException(409,'原生视频任务没有通用项目费用授权，未发送新请求')
        await verify_plan(db,link.batch_id)
    return link


async def verify_lifecycle(db,job,charge,entry,quote):
    """A sibling's released or fabricated settled hold cannot fund a valid first POST; final states need its exact bill receipt."""
    invalid=HTTPException(409,'原生视频完整费用计划的预留、提交与账单状态不一致，未发送请求')
    attempt=await db.scalar(select(SubmissionAttempt).where(SubmissionAttempt.job_id==job.id).execution_options(populate_existing=True))
    receipt=await db.scalar(select(ModelBillingReceipt).where(ModelBillingReceipt.charge_id==job.id).execution_options(populate_existing=True))
    if type(entry.quote_minor) is not int:raise invalid
    if charge.dispatch_state in {'planned','not_sent'}:
        expected='reserved' if charge.dispatch_state=='planned' else 'released'
        if entry.state!=expected or entry.actual_minor is not None or attempt is not None or receipt is not None or charge.provider_request_id is not None:raise invalid
        return
    if charge.dispatch_state not in {'prepared','unknown','accepted'} or attempt is None or attempt.request_hash!=job.request_hash or attempt.state not in {'prepared','accepted','rejected'}:
        raise invalid
    if charge.dispatch_state!='accepted':
        if entry.state!='unknown' or entry.actual_minor is not None or receipt is not None or charge.provider_request_id is not None:raise invalid
        return
    if not charge.provider_request_id or attempt.state=='rejected':raise invalid
    if entry.state=='unknown':
        if entry.actual_minor is not None or receipt is not None:raise invalid
        return
    if (receipt is None or receipt.project_id!=job.project_id or receipt.quote_minor!=quote.max_call_minor
            or type(receipt.actual_minor) is not int or receipt.actual_minor<0 or receipt.action!=entry.state or entry.state not in {'settled','released'}):raise invalid
    if entry.state=='released':
        if receipt.actual_minor!=0 or entry.actual_minor is not None or receipt.adjustment_entry_id is not None:raise invalid
    elif type(entry.actual_minor) is not int or entry.actual_minor!=min(receipt.actual_minor,quote.max_call_minor):raise invalid
    overage=max(0,receipt.actual_minor-quote.max_call_minor)
    if overage:
        adjustment=await db.get(ExecutionBudgetEntry,receipt.adjustment_entry_id) if receipt.adjustment_entry_id else None
        if adjustment is None or adjustment.project_id!=job.project_id or adjustment.state!='settled' or adjustment.quote_minor!=overage or adjustment.actual_minor!=overage:raise invalid
    elif receipt.adjustment_entry_id is not None:raise invalid


async def verify_plan(db,batch_id,*,review_bills=False):
    """Check every actual job, immutable source, original hold and call before any unit in a batch sends."""
    await project_read_snapshot(db)
    invalid=HTTPException(409,'原生视频完整项目费用计划不一致，未发送请求')
    native=await db.scalar(select(NativeVideoChargeBatch).where(NativeVideoChargeBatch.id==batch_id)
        .execution_options(populate_existing=True))
    batch=await db.scalar(select(ModelChargeBatch).where(ModelChargeBatch.id==batch_id).execution_options(populate_existing=True))
    if native is None or batch is None or (native.project_id,native.submitted_by)!=(batch.project_id,batch.submitted_by) or batch.scope_key!='native-video:'+batch.id:
        raise invalid
    actor(native.submitted_by)
    context=native.context
    if not isinstance(context,dict) or set(context)!={'connection','profile','selection','units'} or not isinstance(context['units'],list) or not 1<=len(context['units'])<=64:
        raise invalid
    expected=context['units']
    if any(not isinstance(unit,dict) or set(unit)!={'job_id','unit_key','request_hash','snapshot_hash','execution_binding_hash'} for unit in expected):
        raise invalid
    expected_map={unit['job_id']:unit for unit in expected}
    rows=(await db.scalars(select(NativeVideoJobCharge).where(NativeVideoJobCharge.batch_id==batch_id)
        .execution_options(populate_existing=True))).all()
    charges=(await db.scalars(select(ModelCallCharge).where(ModelCallCharge.batch_id==batch_id)
        .execution_options(populate_existing=True))).all()
    if len(expected_map)!=len(expected) or len(rows)!=len(expected) or len(charges)!=len(expected) or batch.call_count!=len(expected) or {row.job_id for row in rows}!=set(expected_map) or {charge.id for charge in charges}!=set(expected_map):
        raise invalid
    calls=[];connection=context['connection'];charge_map={charge.id:charge for charge in charges}
    profile=context['profile'];selection=context['selection']
    if (not isinstance(connection,dict) or not isinstance(profile,dict) or not isinstance(selection,dict)
            or set(profile)!={'schema','service_version','account_version','profile_hash','iteration_limit','quote_fingerprint','execution_binding_hash'}
            or profile['schema']!='native-managed-v1' or selection.get('kind') not in {'direct','plan'}):raise invalid
    if native.request_hash!=binding_digest({'domain':'native-video-project-request-v1','project_id':native.project_id,
            'submitted_by':native.submitted_by,'selection':selection,'price_fingerprint':profile['quote_fingerprint'],'confirm_project_charges':True}):raise invalid
    if selection['kind']=='direct' and (len(expected)!=1 or expected[0]['unit_key']!='video'):raise invalid
    if selection['kind']=='plan' and (not isinstance(selection.get('shot_ids'),list) or len(selection['shot_ids'])!=len(expected)
            or set(selection['shot_ids'])!={unit['unit_key'] for unit in expected}):raise invalid
    for link in rows:
        job=await db.scalar(select(ExecutionJob).where(ExecutionJob.id==link.job_id).execution_options(populate_existing=True))
        entry=await db.scalar(select(ExecutionBudgetEntry).where(ExecutionBudgetEntry.job_id==link.job_id).execution_options(populate_existing=True))
        execution=await db.get(ExecutionBinding,link.job_id)
        charge=charge_map[link.job_id]
        unit={field:getattr(link,field) for field in ('job_id','unit_key','request_hash','snapshot_hash','execution_binding_hash')}
        try:quote=ModelPriceEnvelope.model_validate(charge.price_snapshot)
        except (ValueError,TypeError):raise invalid from None
        if (unit!=expected_map[link.job_id] or link.project_id!=native.project_id or job is None or entry is None or execution is None
                or job.project_id!=native.project_id or job.request_hash!=link.request_hash or binding_digest(job.snapshot)!=link.snapshot_hash
                or job.request_key!='native:'+binding_digest({'key':native.request_key,'unit':link.unit_key})
                or execution.binding_hash!=link.execution_binding_hash or entry.project_id!=native.project_id or entry.quote_minor!=quote.max_call_minor
                or profile['execution_binding_hash']!=link.execution_binding_hash or profile['quote_fingerprint']!=quote.fingerprint or profile['account_version']!=quote.account_version
                or charge.project_id!=native.project_id or charge.kind!='native_video' or charge.unit_key!=link.unit_key
                or charge.binding_hash!=binding_digest(connection) or quote.model_name!=connection.get('model_name')
                or (quote.model_id,quote.provider_id,quote.provider_connection_version)!=(connection.get('model_id'),connection.get('provider_id'),connection.get('provider_connection_version'))
                or (quote.model_id,quote.provider_id,quote.provider_connection_version,quote.account_version)!=(charge.model_id,charge.provider_id,charge.provider_connection_version,charge.account_version)):
            raise invalid
        await verify_lifecycle(db,job,charge,entry,quote)
        if selection['kind']=='direct':
            if selection.get('snapshot')!=job.snapshot:raise invalid
        else:
            from app.models.shot_plan import PlanJobLink
            from app.services.shot_plan.admission import entry_for,request_identity
            from app.services.shot_plan.service import owned_plan
            provenance=await db.get(PlanJobLink,job.id)
            plan=await owned_plan(db,native.project_id,selection.get('plan_id'))
            original=entry_for(plan,link.unit_key)
            if provenance is None or (provenance.project_id,provenance.plan_id,provenance.shot_id,provenance.entry_hash)!=(native.project_id,plan.id,link.unit_key,original['entry_hash']) or job.request_hash!=request_identity(plan,original) or job.snapshot!=original['request']:
                raise invalid
        calls.append(QuotedModelCall(unit_key=link.unit_key,kind='native_video',binding_hash=charge.binding_hash,quote=quote))
    if len({call.quote.fingerprint for call in calls})!=1 or manifest(native.project_id,native.submitted_by,calls)!=batch.manifest_hash:
        raise invalid
    if review_bills:
        await admission_review_block(db,native.project_id,calls)
    return native,calls


async def bind_new_jobs(db,*,project_id,submitted_by,request_key,request_hash,connection,profile,selection,jobs,quote):
    """Seal all newly inserted jobs/holds in their existing writer transaction; no balance increment occurs here."""
    if not db.in_transaction() or db.bind.dialect.name!='sqlite':
        raise HTTPException(409,'原生视频费用需要同一SQLite写入事务')
    actor(submitted_by)
    quote=ModelPriceEnvelope.model_validate(quote)
    if (not 1<=len(jobs)<=64 or len({identity for identity,_unit in jobs})!=len(jobs) or len({unit for _identity,unit in jobs})!=len(jobs)
            or (quote.model_id,quote.model_name,quote.provider_id,quote.provider_connection_version)!=(connection.get('model_id'),connection.get('model_name'),connection.get('provider_id'),connection.get('provider_connection_version'))):
        raise HTTPException(409,'原生视频费用没有覆盖完整的原模型调用计划')
    calls=[QuotedModelCall(unit_key=unit,kind='native_video',binding_hash=binding_digest(connection),quote=quote) for _identity,unit in jobs]
    await admission_review_block(db,project_id,calls)
    identifier=uuid.uuid4().hex
    batch=ModelChargeBatch(id=identifier,project_id=project_id,submitted_by=submitted_by,scope_key='native-video:'+identifier,
        manifest_hash=manifest(project_id,submitted_by,calls),call_count=len(calls))
    db.add(batch);await db.flush()
    units=[]
    for (job_id,unit),call in zip(jobs,calls):
        job=await db.get(ExecutionJob,job_id);entry=await db.get(ExecutionBudgetEntry,job_id);execution=await db.get(ExecutionBinding,job_id)
        attempt=await db.scalar(select(SubmissionAttempt.id).where(SubmissionAttempt.job_id==job_id))
        if job is None or entry is None or execution is None or job.project_id!=project_id or entry.project_id!=project_id or job.state!='queued' or entry.state!='reserved' or entry.quote_minor!=quote.max_call_minor or attempt is not None:
            raise HTTPException(409,'只能关联同事务中新建且尚未发送的原生视频任务与预留')
        db.add(ModelCallCharge(id=job_id,project_id=project_id,batch_id=identifier,unit_key=unit,kind='native_video',
            model_id=quote.model_id,provider_id=quote.provider_id,provider_connection_version=quote.provider_connection_version,
            account_version=quote.account_version,binding_hash=call.binding_hash,price_snapshot=quote.model_dump(),dispatch_state='planned'))
        await db.flush()
        evidence={'job_id':job_id,'unit_key':unit,'request_hash':job.request_hash,'snapshot_hash':binding_digest(job.snapshot),
            'execution_binding_hash':execution.binding_hash}
        db.add(NativeVideoJobCharge(project_id=project_id,batch_id=identifier,**evidence));await db.flush();units.append(evidence)
    native=NativeVideoChargeBatch(id=identifier,project_id=project_id,submitted_by=submitted_by,request_key=request_key,
        request_hash=request_hash,context={'connection':connection,'profile':profile,'selection':selection,'units':units})
    db.add(native);await db.flush();await verify_plan(db,identifier)
    return native


async def release_unsent_job(db,project_id,job_id):
    """Release exactly one original native planned call only after durable local cancellation and no preparation."""
    link=await job_charge(db,job_id,required=True)
    if link.project_id!=project_id:raise BudgetError('Native cancellation project mismatch')
    job=await db.get(ExecutionJob,job_id);entry=await db.get(ExecutionBudgetEntry,job_id)
    charge=await db.scalar(select(ModelCallCharge).where(ModelCallCharge.id==job_id).execution_options(populate_existing=True))
    attempt=await db.scalar(select(SubmissionAttempt.id).where(SubmissionAttempt.job_id==job_id))
    if job.state!='cancelled' or attempt is not None:raise BudgetError('Only an unsent native cancellation can release its hold')
    if charge.dispatch_state=='not_sent' and entry.state=='released':return 'released'
    if charge.dispatch_state!='planned' or entry.state!='reserved':raise BudgetError('Prepared native fees require explicit bill evidence')
    budget=await db.scalar(select(ExecutionBudget).where(ExecutionBudget.project_id==project_id).execution_options(populate_existing=True))
    if budget is None:raise BudgetError('Original project budget missing')
    await change_balance(db,budget,-entry.quote_minor,0)
    charge.dispatch_state='not_sent';entry.state='released';await db.flush()
    return 'released'
