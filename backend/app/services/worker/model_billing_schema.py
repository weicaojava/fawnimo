"""Append-only billing source, line, review and settlement receipt guards."""
from sqlalchemy import text

from app.models.model_billing import ModelBillingLine,ModelBillingReceipt,ModelBillingReview,ModelBillingStatement,ModelBillingResolution


async def migrate_model_billing(engine):
    """Install immutable accounting evidence after the existing project/call parents are available."""
    if engine.dialect.name!='sqlite':
        raise RuntimeError('Model billing reconciliation currently requires SQLite')
    async with engine.begin() as connection:
        await connection.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS model_call_charge_scope ON model_call_charges(id,project_id)'))
        for model in (ModelBillingStatement,ModelBillingLine,ModelBillingReceipt,ModelBillingReview,ModelBillingResolution):
            await connection.run_sync(lambda sync: model.__table__.create(sync,checkfirst=True))
            # SQLite REPLACE may delete without running DELETE triggers. Protect
            # every unique conflict at insertion, even when a different PK is used.
            conflicts={
                'model_billing_statements':'id=NEW.id OR (provider_id=NEW.provider_id AND provider_connection_version=NEW.provider_connection_version AND account_version=NEW.account_version AND statement_key=NEW.statement_key)',
                'model_billing_lines':'id=NEW.id OR (statement_id=NEW.statement_id AND row_reference=NEW.row_reference)',
                'model_billing_receipts':'id=NEW.id OR line_id=NEW.line_id OR charge_id=NEW.charge_id OR (NEW.adjustment_entry_id IS NOT NULL AND adjustment_entry_id=NEW.adjustment_entry_id)',
                'model_billing_reviews':'id=NEW.id',
                'model_billing_resolutions':'id=NEW.id OR line_id=NEW.line_id',
            }
            name=model.__tablename__+'_no_replace'
            expected=(f"CREATE TRIGGER {name} BEFORE INSERT ON {model.__tablename__} WHEN EXISTS(SELECT 1 FROM {model.__tablename__} "
                f"WHERE {conflicts[model.__tablename__]}) BEGIN SELECT RAISE(ABORT,'Model billing evidence cannot be replaced'); END")
            actual=await connection.scalar(text("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=:name"),{'name':name})
            if actual is None:
                await connection.execute(text(expected))
            elif ' '.join(actual.split())!=' '.join(expected.split()):
                raise RuntimeError('Model billing replacement guard differs from supported schema')
            fields={
                'model_billing_statements':(('provider_connection_version',9223372036854775806),('created_at',9223372036854775807)),
                'model_billing_lines':(('amount_minor',4611686018427387903),),
                'model_billing_receipts':(('actual_minor',4611686018427387903),('quote_minor',4611686018427387903),('created_at',9223372036854775807)),
                'model_billing_reviews':(('created_at',9223372036854775807),),
                'model_billing_resolutions':(('created_at',9223372036854775807),),
            }[model.__tablename__]
            invalid=' OR '.join(f"typeof(NEW.{field})!='integer' OR NEW.{field}<0 OR NEW.{field}>{maximum}" for field,maximum in fields)
            name=model.__tablename__+'_integer_insert'
            expected=f"CREATE TRIGGER {name} BEFORE INSERT ON {model.__tablename__} WHEN {invalid} BEGIN SELECT RAISE(ABORT,'Model billing requires bounded integer units'); END"
            actual=await connection.scalar(text("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=:name"),{'name':name})
            if actual is None:
                await connection.execute(text(expected))
            elif ' '.join(actual.split())!=' '.join(expected.split()):
                raise RuntimeError('Model billing integer guard differs from supported schema')
            for action in ('UPDATE','DELETE'):
                name=model.__tablename__+'_no_'+action.lower()
                expected=f"CREATE TRIGGER {name} BEFORE {action} ON {model.__tablename__} BEGIN SELECT RAISE(ABORT,'Immutable model billing evidence'); END"
                actual=await connection.scalar(text("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=:name"),{'name':name})
                if actual is None:
                    await connection.execute(text(expected))
                elif ' '.join(actual.split())!=' '.join(expected.split()):
                    raise RuntimeError('Model billing guard differs from supported schema')
        await connection.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS model_billing_adjustment_unique ON model_billing_receipts(adjustment_entry_id)'))
        condition=("NOT EXISTS(SELECT 1 FROM model_billing_lines l JOIN model_billing_statements s ON s.id=l.statement_id "
            "JOIN model_call_charges c ON c.id=NEW.charge_id JOIN execution_budget_entries e ON e.job_id=c.id AND e.project_id=c.project_id "
            "WHERE l.id=NEW.line_id AND c.project_id=NEW.project_id AND c.provider_id=s.provider_id "
            "AND c.provider_connection_version=s.provider_connection_version AND c.account_version=s.account_version "
            "AND c.provider_request_id=l.provider_request_id AND NEW.actual_minor=l.amount_minor AND NEW.quote_minor=e.quote_minor "
            "AND NEW.action=CASE WHEN l.status='not_charged' THEN 'released' ELSE 'settled' END AND e.state=NEW.action "
            "AND e.actual_minor IS CASE WHEN NEW.action='settled' THEN min(NEW.actual_minor,e.quote_minor) ELSE NULL END "
            "AND ((NEW.actual_minor<=e.quote_minor AND NEW.adjustment_entry_id IS NULL) OR (NEW.actual_minor>e.quote_minor "
            "AND EXISTS(SELECT 1 FROM execution_budget_entries a WHERE a.job_id=NEW.adjustment_entry_id AND a.project_id=c.project_id "
            "AND a.state='settled' AND a.quote_minor=NEW.actual_minor-e.quote_minor AND a.actual_minor=a.quote_minor))))")
        name='model_billing_receipt_context'
        expected=f"CREATE TRIGGER {name} BEFORE INSERT ON model_billing_receipts WHEN {condition} BEGIN SELECT RAISE(ABORT,'Model billing evidence context mismatch'); END"
        actual=await connection.scalar(text("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=:name"),{'name':name})
        if actual is None:
            await connection.execute(text(expected))
        elif ' '.join(actual.split())!=' '.join(expected.split()):
            raise RuntimeError('Model billing receipt context guard differs from supported schema')
        condition=("NOT EXISTS(SELECT 1 FROM model_billing_lines l JOIN model_billing_statements s ON s.id=l.statement_id "
            "JOIN model_billing_receipts r ON r.id=NEW.reference_receipt_id JOIN model_billing_lines prior ON prior.id=r.line_id "
            "JOIN model_billing_statements ps ON ps.id=prior.statement_id JOIN model_billing_reviews v ON v.line_id=l.id "
            "WHERE l.id=NEW.line_id AND l.id!=prior.id AND l.statement_id!=prior.statement_id "
            "AND s.provider_id=ps.provider_id AND s.provider_connection_version=ps.provider_connection_version "
            "AND s.account_version=ps.account_version AND l.provider_request_id=prior.provider_request_id "
            "AND l.status=prior.status AND l.currency=prior.currency AND l.amount_minor=prior.amount_minor "
            "AND v.status='conflict' AND v.code='call_already_has_another_bill' AND v.charge_id=r.charge_id "
            "AND NOT EXISTS(SELECT 1 FROM model_billing_receipts posted WHERE posted.line_id=l.id))")
        name='model_billing_resolution_context'
        expected=f"CREATE TRIGGER {name} BEFORE INSERT ON model_billing_resolutions WHEN {condition} BEGIN SELECT RAISE(ABORT,'Model billing duplicate resolution context mismatch'); END"
        actual=await connection.scalar(text("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=:name"),{'name':name})
        if actual is None:
            await connection.execute(text(expected))
        elif ' '.join(actual.split())!=' '.join(expected.split()):
            raise RuntimeError('Model billing duplicate resolution guard differs from supported schema')
