"""Add immutable correction chains and explicit ambiguous-call assignments to existing SQLite history."""
from sqlalchemy import text

from app.models.model_billing_adjustment import ModelBillingAssignment,ModelBillingCorrection


async def guard(connection,name,table,action,condition,message):
    """Install one fixed trigger, rejecting changed existing guards rather than replacing their authority."""
    expected=f"CREATE TRIGGER {name} BEFORE {action} ON {table} WHEN {condition} BEGIN SELECT RAISE(ABORT,'{message}'); END"
    actual=await connection.scalar(text("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=:name"),{'name':name})
    if actual is None:
        await connection.execute(text(expected))
    elif ' '.join(actual.split())!=' '.join(expected.split()):
        raise RuntimeError('Unsupported billing correction guard')


async def migrate_billing_adjustments(connection):
    """Preserve all old balances/evidence; add bounded chain/context/physical-key protections only."""
    await connection.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS model_billing_receipt_scope ON model_billing_receipts(id,project_id)'))
    for model in (ModelBillingCorrection,ModelBillingAssignment):
        await connection.run_sync(lambda sync: model.__table__.create(sync,checkfirst=True))
        table=model.__tablename__
        conflicts=('id=NEW.id OR line_id=NEW.line_id OR (reference_receipt_id=NEW.reference_receipt_id AND revision=NEW.revision)'
            if model is ModelBillingCorrection else 'id=NEW.id OR line_id=NEW.line_id')
        await guard(connection,table+'_no_replace',table,'INSERT',f'EXISTS(SELECT 1 FROM {table} WHERE {conflicts})','Immutable billing decision conflict')
        for action in ('UPDATE','DELETE'):
            await guard(connection,table+'_no_'+action.lower(),table,action,'1','Immutable billing decision')
        fields=('revision','previous_total_minor','new_total_minor','spent_before_minor','spent_after_minor','budget_revision','created_at') if model is ModelBillingCorrection else ('created_at',)
        invalid=' OR '.join(f"typeof(NEW.{field})!='integer' OR NEW.{field}<0 OR NEW.{field}>{2**62-1}" for field in fields)
        if model is ModelBillingCorrection:
            invalid+=f" OR typeof(NEW.delta_minor)!='integer' OR NEW.delta_minor < -{2**62-1} OR NEW.delta_minor>{2**62-1}"
        await guard(connection,table+'_integer_insert',table,'INSERT',invalid,'Billing decisions require bounded integer units')
    # A different logical primary key can still replace an old SQLite rowid
    # without firing DELETE triggers. Preserve physical identities as well.
    for table in ('model_billing_statements','model_billing_lines','model_billing_receipts','model_billing_reviews',
        'model_billing_resolutions','model_billing_corrections','model_billing_assignments'):
        await guard(connection,table+'_physical_no_replace',table,'INSERT',
            f'EXISTS(SELECT 1 FROM {table} WHERE rowid=NEW.rowid)','Immutable billing physical identity')
    correction_context=("NOT EXISTS(SELECT 1 FROM model_billing_receipts r JOIN model_billing_lines original ON original.id=r.line_id "
        "JOIN model_billing_statements os ON os.id=original.statement_id JOIN model_billing_lines l ON l.id=NEW.line_id "
        "JOIN model_billing_statements s ON s.id=l.statement_id JOIN execution_budgets b ON b.project_id=r.project_id "
        "WHERE r.id=NEW.reference_receipt_id AND r.project_id=NEW.project_id AND l.id!=original.id AND l.statement_id!=original.statement_id "
        "AND l.provider_request_id=original.provider_request_id AND l.currency=original.currency AND l.currency=b.currency "
        "AND s.provider_id=os.provider_id AND s.provider_connection_version=os.provider_connection_version AND s.account_version=os.account_version "
        "AND l.amount_minor=NEW.new_total_minor AND b.spent_minor=NEW.spent_after_minor AND b.revision=NEW.budget_revision "
        "AND NOT EXISTS(SELECT 1 FROM model_billing_receipts posted WHERE posted.line_id=l.id) "
        "AND NOT EXISTS(SELECT 1 FROM model_billing_resolutions resolved WHERE resolved.line_id=l.id) "
        "AND NOT EXISTS(SELECT 1 FROM model_billing_assignments assigned WHERE assigned.line_id=l.id AND assigned.charge_id!=r.charge_id) "
        "AND ((NEW.revision=1 AND NEW.previous_correction_id IS NULL AND NEW.previous_total_minor=r.actual_minor "
        "AND NOT EXISTS(SELECT 1 FROM model_billing_corrections old WHERE old.reference_receipt_id=r.id)) "
        "OR EXISTS(SELECT 1 FROM model_billing_corrections old WHERE old.id=NEW.previous_correction_id "
        "AND old.reference_receipt_id=r.id AND old.project_id=r.project_id AND old.revision=NEW.revision-1 "
        "AND old.new_total_minor=NEW.previous_total_minor AND NOT EXISTS(SELECT 1 FROM model_billing_corrections later "
        "WHERE later.reference_receipt_id=r.id AND later.revision>=NEW.revision))))")
    await guard(connection,'model_billing_correction_context','model_billing_corrections','INSERT',correction_context,'Billing correction context mismatch')
    assignment_context=("NOT EXISTS(SELECT 1 FROM model_billing_lines l JOIN model_billing_statements s ON s.id=l.statement_id "
        "JOIN model_call_charges c ON c.id=NEW.charge_id JOIN execution_budget_entries e ON e.job_id=c.id AND e.project_id=c.project_id "
        "WHERE l.id=NEW.line_id AND c.project_id=NEW.project_id AND e.state='unknown' AND c.provider_id=s.provider_id "
        "AND c.provider_connection_version=s.provider_connection_version AND c.account_version=s.account_version "
        "AND c.provider_request_id=l.provider_request_id AND EXISTS(SELECT 1 FROM model_billing_reviews v "
        "WHERE v.line_id=l.id AND v.status='ambiguous' AND v.code='request_account_match_required') "
        "AND (SELECT count(*) FROM model_call_charges peers WHERE peers.provider_id=s.provider_id "
        "AND peers.provider_connection_version=s.provider_connection_version AND peers.account_version=s.account_version "
        "AND peers.provider_request_id=l.provider_request_id)>1 "
        "AND NOT EXISTS(SELECT 1 FROM model_billing_receipts posted WHERE posted.line_id=l.id OR posted.charge_id=c.id) "
        "AND NOT EXISTS(SELECT 1 FROM model_billing_resolutions resolved WHERE resolved.line_id=l.id) "
        "AND NOT EXISTS(SELECT 1 FROM model_billing_corrections corrected WHERE corrected.line_id=l.id))")
    await guard(connection,'model_billing_assignment_context','model_billing_assignments','INSERT',assignment_context,'Billing assignment context mismatch')
    await guard(connection,'model_billing_resolution_uncorrected','model_billing_resolutions','INSERT',
        'EXISTS(SELECT 1 FROM model_billing_corrections c WHERE c.line_id=NEW.line_id)','Corrected billing evidence cannot be reclassified')
    await guard(connection,'model_billing_receipt_assignment','model_billing_receipts','INSERT',
        'EXISTS(SELECT 1 FROM model_billing_assignments a WHERE a.line_id=NEW.line_id AND (a.charge_id!=NEW.charge_id OR a.project_id!=NEW.project_id))',
        'Billing receipt differs from fixed operator assignment')
