"""Immutable operator-imported billing evidence and exact-once project settlement receipts."""
from sqlalchemy import BigInteger,CheckConstraint,DDL,event,ForeignKey,ForeignKeyConstraint,String,UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column

from app.core.db import Base
import app.models.model_charge  # noqa: F401 -- register receipt FK parents for standalone metadata users


class ModelBillingStatement(Base):
    """Bind normalized lines to the original private statement hash and explicit account context."""

    __tablename__='model_billing_statements'
    id: Mapped[str]=mapped_column(String(64),primary_key=True)
    provider_id: Mapped[str]=mapped_column(String(64),nullable=False)
    provider_connection_version: Mapped[int]=mapped_column(BigInteger,nullable=False)
    account_version: Mapped[str]=mapped_column(String(128),nullable=False)
    statement_key: Mapped[str]=mapped_column(String(128),nullable=False)
    original_sha256: Mapped[str]=mapped_column(String(64),nullable=False)
    normalized_sha256: Mapped[str]=mapped_column(String(64),nullable=False)
    imported_by: Mapped[str]=mapped_column(String(64),nullable=False)
    created_at: Mapped[int]=mapped_column(BigInteger,nullable=False)
    __table_args__=(UniqueConstraint('provider_id','provider_connection_version','account_version','statement_key'),)


class ModelBillingLine(Base):
    """An explicit amount or no-charge row; absent rows can never imply a refund or rejection."""

    __tablename__='model_billing_lines'
    id: Mapped[str]=mapped_column(String(64),primary_key=True)
    statement_id: Mapped[str]=mapped_column(ForeignKey('model_billing_statements.id',ondelete='RESTRICT'),nullable=False)
    row_reference: Mapped[str]=mapped_column(String(128),nullable=False)
    provider_request_id: Mapped[str]=mapped_column(String(128),nullable=False)
    status: Mapped[str]=mapped_column(String(16),nullable=False)
    currency: Mapped[str]=mapped_column(String(16),nullable=False)
    amount_minor: Mapped[int]=mapped_column(BigInteger,nullable=False)
    __table_args__=(UniqueConstraint('statement_id','row_reference'),
        CheckConstraint("status IN ('charged','not_charged') AND currency='CNY' AND amount_minor>=0 AND (status!='not_charged' OR amount_minor=0)"),)


class ModelBillingReceipt(Base):
    """Settle one actual invoice line and call once, including any separately funded overage entry."""

    __tablename__='model_billing_receipts'
    id: Mapped[str]=mapped_column(String(64),primary_key=True)
    line_id: Mapped[str]=mapped_column(ForeignKey('model_billing_lines.id',ondelete='RESTRICT'),nullable=False,unique=True)
    charge_id: Mapped[str]=mapped_column(String(64),nullable=False,unique=True)
    project_id: Mapped[str]=mapped_column(String(64),nullable=False)
    action: Mapped[str]=mapped_column(String(16),nullable=False)
    actual_minor: Mapped[int]=mapped_column(BigInteger,nullable=False)
    quote_minor: Mapped[int]=mapped_column(BigInteger,nullable=False)
    adjustment_entry_id: Mapped[str | None]=mapped_column(String(64),unique=True)
    recorded_by: Mapped[str]=mapped_column(String(64),nullable=False)
    created_at: Mapped[int]=mapped_column(BigInteger,nullable=False)
    __table_args__=(
        ForeignKeyConstraint(['charge_id','project_id'],['model_call_charges.id','model_call_charges.project_id'],ondelete='RESTRICT'),
        ForeignKeyConstraint(['adjustment_entry_id','project_id'],['execution_budget_entries.job_id','execution_budget_entries.project_id'],ondelete='RESTRICT'),
        CheckConstraint("action IN ('settled','released') AND actual_minor>=0 AND quote_minor>=0"),)


class ModelBillingReview(Base):
    """Retain unmatched, ambiguous or unaffordable facts without changing an existing financial history."""

    __tablename__='model_billing_reviews'
    id: Mapped[str]=mapped_column(String(64),primary_key=True)
    line_id: Mapped[str]=mapped_column(ForeignKey('model_billing_lines.id',ondelete='RESTRICT'),nullable=False)
    charge_id: Mapped[str | None]=mapped_column(ForeignKey('model_call_charges.id',ondelete='RESTRICT'))
    project_id: Mapped[str | None]=mapped_column(ForeignKey('projects.id',ondelete='RESTRICT'))
    price_fingerprint: Mapped[str | None]=mapped_column(String(64))
    status: Mapped[str]=mapped_column(String(24),nullable=False)
    code: Mapped[str]=mapped_column(String(64),nullable=False)
    created_at: Mapped[int]=mapped_column(BigInteger,nullable=False)
    __table_args__=(
        ForeignKeyConstraint(['charge_id','project_id'],['model_call_charges.id','model_call_charges.project_id'],ondelete='RESTRICT'),
        CheckConstraint("status IN ('unmatched','ambiguous','disputed','conflict','price_violation')"),
        CheckConstraint('(charge_id IS NULL AND project_id IS NULL) OR (charge_id IS NOT NULL AND project_id IS NOT NULL)'),
    )


class ModelBillingResolution(Base):
    """Explicitly identify duplicate evidence without erasing a conflict or making another expense."""

    __tablename__='model_billing_resolutions'
    id: Mapped[str]=mapped_column(String(64),primary_key=True)
    line_id: Mapped[str]=mapped_column(ForeignKey('model_billing_lines.id',ondelete='RESTRICT'),nullable=False,unique=True)
    reference_receipt_id: Mapped[str]=mapped_column(ForeignKey('model_billing_receipts.id',ondelete='RESTRICT'),nullable=False)
    action: Mapped[str]=mapped_column(String(32),nullable=False)
    reason: Mapped[str]=mapped_column(String(500),nullable=False)
    recorded_by: Mapped[str]=mapped_column(String(64),nullable=False)
    created_at: Mapped[int]=mapped_column(BigInteger,nullable=False)
    __table_args__=(CheckConstraint("action='duplicate_evidence' AND length(reason) BETWEEN 4 AND 500"),)


# Existing ledgers gain the composite scope index before a new receipt can refer
# to an old call table; no quoted amount, balance or dispatch fact is rewritten.
event.listen(ModelBillingReceipt.__table__,'before_create',DDL(
    'CREATE UNIQUE INDEX IF NOT EXISTS model_call_charge_scope ON model_call_charges(id,project_id)'
).execute_if(dialect='sqlite'))
