"""Import explicit operator evidence and settle exact account/request matches without guessed billing."""
import csv
import hashlib
from io import StringIO
import json
import re
import time
import uuid

from fastapi import HTTPException
from sqlalchemy import and_,case,exists,func,or_,select,text

from app.core.contracts.model_accounting import BillingStatementContext,NormalizedBillingLine
from app.models.execution_budget import ExecutionBudget,ExecutionBudgetEntry
from app.models.model_billing import ModelBillingLine,ModelBillingReceipt,ModelBillingReview,ModelBillingStatement,ModelBillingResolution
from app.models.model_charge import ModelCallCharge
from app.services.worker.execution_budget import BudgetError,change_balance,transaction
from app.services.worker.model_charges import actor


def digest(value):
    """Bind canonical non-secret invoice facts without storing an arbitrary original file or its path."""
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def parse_normalized_csv(content):
    """Accept one bounded documented normalized CSV, rejecting duplicate IDs, formulas and coercion.

    Suppliers' native files require an explicit operator normalization step.
    This parser does not claim to authenticate a supplier or infer a fee from
    model output, usage, failed tasks, missing rows or free-form CSV columns.
    """
    try:
        if not isinstance(content,bytes) or not 1<=len(content)<=2*1024*1024:
            raise ValueError()
        reader=csv.DictReader(StringIO(content.decode('utf-8-sig')),strict=True)
        fields=['row_reference','provider_request_id','status','currency','amount_minor']
        if reader.fieldnames is None or len(reader.fieldnames)!=len(fields) or set(reader.fieldnames)!=set(fields):
            raise ValueError()
        result=[]
        for row in reader:
            if len(result)>=1000 or None in row or any(value is None for value in row.values()) or not re.fullmatch(r'[0-9]{1,19}',row['amount_minor'].strip()):
                raise ValueError()
            result.append(NormalizedBillingLine.model_validate({**row,'amount_minor':int(row['amount_minor'])}))
        if not result or len({row.row_reference for row in result})!=len(result) or len({row.provider_request_id for row in result})!=len(result):
            raise ValueError()
        return result
    except Exception:
        raise HTTPException(422,'账单文件不符合规范；请核对列名、整数分金额、币种、明确收费状态及唯一请求标识') from None


async def import_statement(factory,*,context,normalized_csv,original_content,imported_by='local_operator'):
    """Retain source hashes and strict rows atomically; same-key changed evidence cannot replace history."""
    actor(imported_by)
    context=BillingStatementContext.model_validate(context)
    rows=parse_normalized_csv(normalized_csv)
    if not isinstance(original_content,bytes) or not 1<=len(original_content)<=10*1024*1024:
        raise HTTPException(422,'需要不超过10MiB的原始私有账单证据')
    source_hash=hashlib.sha256(original_content).hexdigest()
    normalized_hash=digest([row.model_dump() for row in sorted(rows,key=lambda row:row.row_reference)])
    async with factory() as db:
        async with transaction(db):
            prior=await db.scalar(select(ModelBillingStatement).where(
                ModelBillingStatement.provider_id==context.provider_id,ModelBillingStatement.provider_connection_version==context.provider_connection_version,
                ModelBillingStatement.account_version==context.account_version,ModelBillingStatement.statement_key==context.statement_key))
            if prior is not None:
                if prior.original_sha256!=source_hash or prior.normalized_sha256!=normalized_hash:
                    raise HTTPException(409,'原账单请求已绑定不同证据，历史未被覆盖')
                return prior.id
            identifier=uuid.uuid4().hex
            db.add(ModelBillingStatement(id=identifier,**context.model_dump(),original_sha256=source_hash,normalized_sha256=normalized_hash,
                imported_by=imported_by,created_at=int(time.time())))
            await db.flush()
            for row in rows:
                db.add(ModelBillingLine(id=digest({'statement':identifier,'row':row.row_reference}),statement_id=identifier,**row.model_dump()))
            await db.flush()
            return identifier


async def review(db,line,status,code,charge=None):
    """Append stable review facts; retries neither erase failures nor grow identical duplicate records."""
    identity=digest({'line':line.id,'status':status,'code':code,'charge':charge.id if charge else None})
    if await db.get(ModelBillingReview,identity) is None:
        db.add(ModelBillingReview(id=identity,line_id=line.id,status=status,code=code,charge_id=charge.id if charge else None,
            project_id=charge.project_id if charge else None,price_fingerprint=digest(charge.price_snapshot) if charge else None,created_at=int(time.time())))
        await db.flush()


async def reconcile_line(factory,line_id,*,recorded_by='local_operator'):
    """Apply a trusted explicit amount once; unknown, mismatched or unaffordable facts keep their holds.

    An above-quote amount is never truncated or used to raise a user's ceiling.
    Existing authorized headroom can fund a separately recorded adjustment. If
    it cannot, retain the full invoice and dispute until the owner explicitly
    changes the project ceiling. The breached price fingerprint is invalidated.
    """
    actor(recorded_by)
    async with factory() as db:
        async with transaction(db):
            line=await db.get(ModelBillingLine,line_id)
            if line is None:
                raise HTTPException(404,'账单行不存在')
            try:
                NormalizedBillingLine.model_validate({key:getattr(line,key) for key in NormalizedBillingLine.model_fields})
            except Exception:
                raise HTTPException(409,'保存的账单行格式或整数金额无效，原项目费用未改动') from None
            previous=await db.scalar(select(ModelBillingReceipt).where(ModelBillingReceipt.line_id==line_id))
            if previous is not None:
                return {'status':'applied','receipt_id':previous.id,'project_id':previous.project_id,'replayed':True}
            duplicate=await db.scalar(select(ModelBillingResolution).where(ModelBillingResolution.line_id==line_id))
            if duplicate is not None:
                reference=await db.get(ModelBillingReceipt,duplicate.reference_receipt_id)
                return {'status':'duplicate_resolved','receipt_id':reference.id,'project_id':reference.project_id,'replayed':True}
            statement=await db.get(ModelBillingStatement,line.statement_id)
            candidates=(await db.scalars(select(ModelCallCharge).where(
                ModelCallCharge.provider_id==statement.provider_id,ModelCallCharge.provider_connection_version==statement.provider_connection_version,
                ModelCallCharge.account_version==statement.account_version,ModelCallCharge.provider_request_id==line.provider_request_id))).all()
            if len(candidates)!=1:
                status='unmatched' if not candidates else 'ambiguous'
                await review(db,line,status,'request_account_match_required')
                return {'status':status,'receipt_id':None,'project_id':None,'replayed':False}
            charge=candidates[0]
            entry=await db.get(ExecutionBudgetEntry,charge.id)
            budget=await db.get(ExecutionBudget,charge.project_id)
            other=await db.scalar(select(ModelBillingReceipt.id).where(ModelBillingReceipt.charge_id==charge.id))
            if other is not None:
                await review(db,line,'conflict','call_already_has_another_bill',charge)
                return {'status':'conflict','receipt_id':None,'project_id':charge.project_id,'replayed':False}
            if entry is None or entry.project_id!=charge.project_id or budget is None or budget.currency!=line.currency or entry.state!='unknown':
                await review(db,line,'conflict','matching_unknown_hold_required',charge)
                return {'status':'conflict','receipt_id':None,'project_id':charge.project_id,'replayed':False}
            actual=line.amount_minor
            overage=max(0,actual-entry.quote_minor)
            if overage:
                await review(db,line,'price_violation','provider_amount_exceeds_reserved_quote',charge)
            try:
                await change_balance(db,budget,-entry.quote_minor,actual)
            except BudgetError:
                await review(db,line,'disputed','project_headroom_required',charge)
                return {'status':'disputed','receipt_id':None,'project_id':charge.project_id,'replayed':False}
            action='released' if line.status=='not_charged' else 'settled'
            entry.state=action
            entry.actual_minor=min(actual,entry.quote_minor) if action=='settled' else None
            adjustment=None
            if overage:
                adjustment='ba_'+uuid.uuid4().hex
                db.add(ExecutionBudgetEntry(job_id=adjustment,project_id=charge.project_id,state='settled',quote_minor=overage,actual_minor=overage))
            await db.flush()
            identifier=uuid.uuid4().hex
            db.add(ModelBillingReceipt(id=identifier,line_id=line.id,charge_id=charge.id,project_id=charge.project_id,action=action,
                actual_minor=actual,quote_minor=entry.quote_minor,adjustment_entry_id=adjustment,recorded_by=recorded_by,created_at=int(time.time())))
            await db.flush()
            return {'status':'applied','receipt_id':identifier,'project_id':charge.project_id,'replayed':False}


async def reconcile_statement(factory,statement_id,*,recorded_by='local_operator'):
    """Bound the scan to the imported statement and retain each independent line result durably."""
    async with factory() as db:
        if await db.get(ModelBillingStatement,statement_id) is None:
            raise HTTPException(404,'账单不存在')
        identifiers=(await db.scalars(select(ModelBillingLine.id).where(ModelBillingLine.statement_id==statement_id).order_by(ModelBillingLine.row_reference))).all()
    result=[]
    for identifier in identifiers:
        result.append(await reconcile_line(factory,identifier,recorded_by=recorded_by))
    return result


async def resolve_duplicate(factory,line_id,reference_receipt_id,*,reason,recorded_by='local_operator'):
    """Record a deliberate exact duplicate decision; corrections, refunds and ambiguous calls stay open.

    Only a previously reviewed conflict naming the same already-settled call
    can be resolved. The duplicate must repeat its historical account, request,
    status, currency and exact amount. This never changes a ledger counter.
    """
    actor(recorded_by)
    if not isinstance(reason,str) or not 4<=len(reason.strip())<=500 or any(ord(char)<32 for char in reason):
        raise HTTPException(422,'请提供4至500字的单行人工核对原因')
    reason=reason.strip()
    async with factory() as db:
        async with transaction(db):
            prior=await db.scalar(select(ModelBillingResolution).where(ModelBillingResolution.line_id==line_id))
            if prior is not None:
                if prior.reference_receipt_id!=reference_receipt_id or prior.reason!=reason:
                    raise HTTPException(409,'该账单行已绑定另一份人工核对决定')
                return prior.id
            line=await db.get(ModelBillingLine,line_id)
            receipt=await db.get(ModelBillingReceipt,reference_receipt_id)
            if line is None or receipt is None:
                raise HTTPException(404,'待核对账单行或原结算记录不存在')
            original=await db.get(ModelBillingLine,receipt.line_id)
            statement=await db.get(ModelBillingStatement,line.statement_id)
            original_statement=await db.get(ModelBillingStatement,original.statement_id)
            conflict=await db.scalar(select(ModelBillingReview.id).where(ModelBillingReview.line_id==line_id,
                ModelBillingReview.charge_id==receipt.charge_id,ModelBillingReview.status=='conflict',
                ModelBillingReview.code=='call_already_has_another_bill'))
            posted=await db.scalar(select(ModelBillingReceipt.id).where(ModelBillingReceipt.line_id==line_id))
            fields=('provider_request_id','status','currency','amount_minor')
            account_fields=('provider_id','provider_connection_version','account_version')
            if not conflict or posted or line.statement_id==original.statement_id or any(getattr(line,key)!=getattr(original,key) for key in fields) or any(
                getattr(statement,key)!=getattr(original_statement,key) for key in account_fields):
                raise HTTPException(409,'仅可确认同一历史账户、请求及完全相同金额状态的重复证据；差额或更正需另行核对')
            identifier=uuid.uuid4().hex
            db.add(ModelBillingResolution(id=identifier,line_id=line_id,reference_receipt_id=reference_receipt_id,
                action='duplicate_evidence',reason=reason,recorded_by=recorded_by,created_at=int(time.time())))
            await db.flush()
            return identifier


def resolved_review():
    """Keep historical reviews while excluding lines explicitly settled or confirmed as exact duplicates."""
    return or_(exists(select(ModelBillingReceipt.id).where(ModelBillingReceipt.line_id==ModelBillingReview.line_id).correlate(ModelBillingReview)),
        exists(select(ModelBillingResolution.id).where(ModelBillingResolution.line_id==ModelBillingReview.line_id).correlate(ModelBillingReview)))


async def admission_review_block(db,project_id,calls):
    """Stop unresolved project liabilities and permanently breached old price envelopes from new admission."""
    resolved=resolved_review()
    pending=await db.scalar(select(ModelBillingReview.id).where(ModelBillingReview.project_id==project_id,
        ModelBillingReview.status.in_({'disputed','conflict'}),~resolved).limit(1))
    identities=[and_(ModelBillingReview.price_fingerprint==call.quote.fingerprint,ModelCallCharge.kind==call.kind) for call in calls]
    violated=await db.scalar(select(ModelBillingReview.id).join(ModelCallCharge,ModelCallCharge.id==ModelBillingReview.charge_id).where(
        ModelBillingReview.status=='price_violation',or_(*identities)).limit(1))
    if pending or violated:
        raise HTTPException(409,'项目账单或原费用上界需要核实，未接受新的模型调用')
    for call in calls:
        quote=call.quote
        observed=await db.scalar(select(func.max(ModelBillingLine.amount_minor)).join(ModelBillingReview,ModelBillingReview.line_id==ModelBillingLine.id)
            .join(ModelCallCharge,ModelCallCharge.id==ModelBillingReview.charge_id).where(ModelBillingReview.status=='price_violation',
                ModelCallCharge.kind==call.kind,ModelCallCharge.model_id==quote.model_id,ModelCallCharge.provider_id==quote.provider_id,
                ModelCallCharge.provider_connection_version==quote.provider_connection_version,ModelCallCharge.account_version==quote.account_version))
        if observed is not None and quote.max_call_minor<observed:
            raise HTTPException(409,'新费用上界仍低于已观察到账单，请先核对该模型的受信报价')


async def project_read_snapshot(db):
    """Keep multi-query financial projections in one real SQLite snapshot, including legacy driver mode.

    SQLAlchemy autobegin alone does not start a SQLite read transaction on
    legacy pysqlite/aiosqlite connections. Reuse any actual writer/read
    transaction, otherwise start one that the caller's session closes.
    """
    if db.bind.dialect.name!='sqlite':
        raise RuntimeError('Project model accounting currently requires SQLite')
    connection=await db.connection()
    raw=await connection.get_raw_connection()
    if not raw.driver_connection.in_transaction:
        await db.execute(text('BEGIN'))


async def billing_summary(db,project_id):
    """Project only: expose full observed liabilities separately from posted or still-held ledger amounts."""
    await project_read_snapshot(db)
    resolved=resolved_review()
    pending=select(ModelBillingReview.line_id).where(ModelBillingReview.project_id==project_id,
        ModelBillingReview.status.in_({'disputed','conflict'}),~resolved).distinct().subquery()
    # Each normalized line states the TOTAL for a request, not an incremental
    # charge. Overlapping evidence retains every review but contributes only
    # the highest observed total per call. Already posted full receipts (which
    # include adjustments) or still-held quotes cover part of that liability.
    covered=case((ModelBillingReceipt.id.is_not(None),ModelBillingReceipt.actual_minor),
        (ExecutionBudgetEntry.state.in_({'reserved','unknown'}),ExecutionBudgetEntry.quote_minor),else_=0)
    unfunded=(select(ExecutionBudgetEntry.job_id.label('charge_id'),func.max(ModelBillingLine.amount_minor).label('observed'),covered.label('covered')).select_from(ModelBillingLine)
        .join(ModelBillingReview,ModelBillingReview.line_id==ModelBillingLine.id)
        .join(ExecutionBudgetEntry,ExecutionBudgetEntry.job_id==ModelBillingReview.charge_id)
        .outerjoin(ModelBillingReceipt,(ModelBillingReceipt.charge_id==ExecutionBudgetEntry.job_id)&(ModelBillingReceipt.project_id==project_id))
        .where(ModelBillingReview.project_id==project_id,ExecutionBudgetEntry.project_id==project_id,
            ModelBillingReview.status.in_({'disputed','conflict'}),~resolved)
        .group_by(ExecutionBudgetEntry.job_id,ExecutionBudgetEntry.state,ExecutionBudgetEntry.quote_minor,
            ModelBillingReceipt.id,ModelBillingReceipt.actual_minor).subquery())
    observed,delta=0,0
    # An unaffordable original bill may exceed JavaScript/SQLite aggregate
    # integer ranges. Sum in bounded streaming Python integers, then project
    # monetary totals as decimal strings at the HTTP boundary.
    stream=await db.stream(select(unfunded.c.observed,unfunded.c.covered).execution_options(yield_per=256))
    async for row in stream:
        observed+=int(row.observed);delta+=max(0,int(row.observed)-int(row.covered))
    receipts,total=(await db.execute(select(func.count(),func.coalesce(func.sum(ModelBillingReceipt.actual_minor),0)).where(ModelBillingReceipt.project_id==project_id))).one()
    return {'bill_receipt_count':receipts,'bill_receipt_total_minor':total,
        'pending_bill_reviews':await db.scalar(select(func.count()).select_from(pending)),
        'observed_pending_bill_minor':observed,'unfunded_bill_minor':delta,
        'price_violation_count':await db.scalar(select(func.count()).select_from(ModelBillingReview).where(
            ModelBillingReview.project_id==project_id,ModelBillingReview.status=='price_violation'))}
