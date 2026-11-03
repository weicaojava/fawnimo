"""Explicit local billing corrections and ambiguous-call decisions; never contact a provider."""
import time
import uuid

from fastapi import HTTPException
from sqlalchemy import select

from app.core.contracts.model_accounting import NormalizedBillingLine
from app.core.contracts.model_billing_adjustment import BillingCorrectionWrite
from app.models.execution_budget import ExecutionBudget,ExecutionBudgetEntry
from app.models.model_billing import ModelBillingLine,ModelBillingReceipt,ModelBillingResolution,ModelBillingReview,ModelBillingStatement
from app.models.model_billing_adjustment import ModelBillingAssignment,ModelBillingCorrection
from app.models.model_charge import ModelCallCharge
from app.services.worker.execution_budget import BudgetError,change_balance,transaction
from app.services.worker.model_charges import actor


async def receipt_head(db,receipt):
    """Read the latest immutable correction inside the caller's actual SQLite snapshot or writer lease."""
    return await db.scalar(select(ModelBillingCorrection).where(ModelBillingCorrection.reference_receipt_id==receipt.id)
        .order_by(ModelBillingCorrection.revision.desc()).limit(1))


async def matching_calls(db,line,statement):
    """Match only historical provider/connection/account/request facts; never guess from output or a task name."""
    return (await db.scalars(select(ModelCallCharge).where(ModelCallCharge.provider_id==statement.provider_id,
        ModelCallCharge.provider_connection_version==statement.provider_connection_version,
        ModelCallCharge.account_version==statement.account_version,ModelCallCharge.provider_request_id==line.provider_request_id))).all()


async def correct_billing_line(factory,*,line_id,reference_receipt_id,expected_revision,reason,recorded_by='local_operator'):
    """Post one exact replacement total once, keeping gross history and the unchanged project ceiling.

    A negative delta records operator-confirmed bill credit; it does not request
    or authenticate a supplier refund. Positive deltas need existing project
    headroom. Unknown acceptance is never released by a correction decision.
    """
    actor(recorded_by)
    try:
        request=BillingCorrectionWrite(line_id=line_id,reference_receipt_id=reference_receipt_id,expected_revision=expected_revision,reason=reason)
    except Exception:
        raise HTTPException(422,'请提供原结算记录、当前更正版本与4至500字单行核对原因') from None
    from app.services.worker.model_billing import review
    async with factory() as db:
        async with transaction(db):
            prior=await db.scalar(select(ModelBillingCorrection).where(ModelBillingCorrection.line_id==request.line_id))
            if prior is not None:
                if (prior.reference_receipt_id,prior.revision-1,prior.reason)!=(request.reference_receipt_id,request.expected_revision,request.reason):
                    raise HTTPException(409,'原账单行已绑定不同更正决定，历史未被覆盖')
                return {'status':'correction_applied','correction_id':prior.id,'receipt_id':prior.reference_receipt_id,'project_id':prior.project_id,'revision':prior.revision,'replayed':True}
            line=await db.get(ModelBillingLine,request.line_id)
            receipt=await db.get(ModelBillingReceipt,request.reference_receipt_id)
            if line is None or receipt is None:
                raise HTTPException(404,'更正账单行或原结算记录不存在')
            try:
                NormalizedBillingLine.model_validate({key:getattr(line,key) for key in NormalizedBillingLine.model_fields})
            except Exception:
                raise HTTPException(409,'保存的更正账单行无效，项目费用未改动') from None
            original=await db.get(ModelBillingLine,receipt.line_id)
            statement=await db.get(ModelBillingStatement,line.statement_id)
            original_statement=await db.get(ModelBillingStatement,original.statement_id)
            charge=await db.get(ModelCallCharge,receipt.charge_id)
            budget=await db.get(ExecutionBudget,receipt.project_id)
            assignment=await db.scalar(select(ModelBillingAssignment).where(ModelBillingAssignment.line_id==line.id))
            posted=await db.scalar(select(ModelBillingReceipt.id).where(ModelBillingReceipt.line_id==line.id))
            resolved=await db.scalar(select(ModelBillingResolution.id).where(ModelBillingResolution.line_id==line.id))
            if line.statement_id==original.statement_id or posted or resolved or budget is None or charge is None or budget.currency!=line.currency or any(
                getattr(statement,key)!=getattr(original_statement,key) for key in ('provider_id','provider_connection_version','account_version')) or any(
                getattr(line,key)!=getattr(original,key) for key in ('provider_request_id','currency')) or assignment is not None and assignment.charge_id!=charge.id:
                raise HTTPException(409,'更正必须指向同一历史账户和请求的新账单，不能重分配已有决定')
            head=await receipt_head(db,receipt)
            revision=head.revision if head else 0
            if revision!=request.expected_revision:
                raise HTTPException(409,'账单更正版本已变化，请先核对当前记录')
            previous=head.new_total_minor if head else receipt.actual_minor
            delta=line.amount_minor-previous
            if not delta:
                raise HTTPException(409,'金额没有变化；相同证据请使用重复账单核对')
            if line.amount_minor>receipt.quote_minor:
                await review(db,line,'price_violation','provider_amount_exceeds_reserved_quote',charge)
            try:
                await change_balance(db,budget,0,delta)
            except BudgetError:
                await review(db,line,'disputed','project_headroom_required',charge)
                return {'status':'disputed','correction_id':None,'receipt_id':receipt.id,'project_id':receipt.project_id,'revision':revision,'replayed':False}
            identifier=uuid.uuid4().hex
            db.add(ModelBillingCorrection(id=identifier,line_id=line.id,reference_receipt_id=receipt.id,project_id=receipt.project_id,
                previous_correction_id=head.id if head else None,revision=revision+1,previous_total_minor=previous,new_total_minor=line.amount_minor,
                delta_minor=delta,spent_before_minor=budget.spent_minor,spent_after_minor=budget.spent_minor+delta,budget_revision=budget.revision+1,
                reason=request.reason,recorded_by=recorded_by,created_at=int(time.time())))
            await db.flush()
            return {'status':'correction_applied','correction_id':identifier,'receipt_id':receipt.id,'project_id':receipt.project_id,'revision':revision+1,'replayed':False}


async def assign_ambiguous_line(factory,*,line_id,charge_id,reason,recorded_by='local_operator'):
    """Choose one actual matching unknown call after ambiguity was recorded; leave all other holds untouched."""
    actor(recorded_by)
    try:
        request=BillingCorrectionWrite(line_id=line_id,reference_receipt_id=charge_id,expected_revision=0,reason=reason)
    except Exception:
        raise HTTPException(422,'请明确选择待核对调用并提供4至500字单行原因') from None
    async with factory() as db:
        async with transaction(db):
            prior=await db.scalar(select(ModelBillingAssignment).where(ModelBillingAssignment.line_id==line_id))
            if prior is not None:
                if (prior.charge_id,prior.reason)!=(charge_id,request.reason):
                    raise HTTPException(409,'该账单行已绑定另一项人工归属决定')
                return prior.id
            line=await db.get(ModelBillingLine,line_id)
            if line is None:
                raise HTTPException(404,'账单行不存在')
            statement=await db.get(ModelBillingStatement,line.statement_id)
            candidates=await matching_calls(db,line,statement)
            chosen=next((item for item in candidates if item.id==charge_id),None)
            reviewed=await db.scalar(select(ModelBillingReview.id).where(ModelBillingReview.line_id==line.id,
                ModelBillingReview.status=='ambiguous',ModelBillingReview.code=='request_account_match_required'))
            entry=await db.get(ExecutionBudgetEntry,charge_id)
            posted=await db.scalar(select(ModelBillingReceipt.id).where((ModelBillingReceipt.line_id==line.id)|(ModelBillingReceipt.charge_id==charge_id)))
            resolved=await db.scalar(select(ModelBillingResolution.id).where(ModelBillingResolution.line_id==line.id))
            corrected=await db.scalar(select(ModelBillingCorrection.id).where(ModelBillingCorrection.line_id==line.id))
            if len(candidates)<2 or chosen is None or not reviewed or entry is None or entry.project_id!=chosen.project_id or entry.state!='unknown' or posted or resolved or corrected:
                raise HTTPException(409,'仅能核对已记录歧义中实际匹配、尚未结算的调用；其他预留保持不变')
            identifier=uuid.uuid4().hex
            db.add(ModelBillingAssignment(id=identifier,line_id=line.id,charge_id=chosen.id,project_id=chosen.project_id,
                reason=request.reason,recorded_by=recorded_by,created_at=int(time.time())))
            await db.flush()
            return identifier
