"""Append-only operator corrections and explicit selections for ambiguous bill evidence."""
from sqlalchemy import BigInteger,CheckConstraint,DDL,event,ForeignKey,ForeignKeyConstraint,String,UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column

from app.core.db import Base
from app.models.model_billing import ModelBillingReceipt


class ModelBillingCorrection(Base):
    """Retain one versioned replacement total without rewriting its original invoice or settlement."""

    __tablename__='model_billing_corrections'
    id: Mapped[str]=mapped_column(String(64),primary_key=True)
    line_id: Mapped[str]=mapped_column(ForeignKey('model_billing_lines.id',ondelete='RESTRICT'),nullable=False,unique=True)
    reference_receipt_id: Mapped[str]=mapped_column(String(64),nullable=False)
    project_id: Mapped[str]=mapped_column(String(64),nullable=False)
    previous_correction_id: Mapped[str | None]=mapped_column(String(64))
    revision: Mapped[int]=mapped_column(BigInteger,nullable=False)
    previous_total_minor: Mapped[int]=mapped_column(BigInteger,nullable=False)
    new_total_minor: Mapped[int]=mapped_column(BigInteger,nullable=False)
    delta_minor: Mapped[int]=mapped_column(BigInteger,nullable=False)
    spent_before_minor: Mapped[int]=mapped_column(BigInteger,nullable=False)
    spent_after_minor: Mapped[int]=mapped_column(BigInteger,nullable=False)
    budget_revision: Mapped[int]=mapped_column(BigInteger,nullable=False)
    reason: Mapped[str]=mapped_column(String(500),nullable=False)
    recorded_by: Mapped[str]=mapped_column(String(64),nullable=False)
    created_at: Mapped[int]=mapped_column(BigInteger,nullable=False)
    __table_args__=(
        UniqueConstraint('reference_receipt_id','revision'),
        UniqueConstraint('id','reference_receipt_id','project_id'),
        ForeignKeyConstraint(['reference_receipt_id','project_id'],['model_billing_receipts.id','model_billing_receipts.project_id'],ondelete='RESTRICT'),
        ForeignKeyConstraint(['previous_correction_id','reference_receipt_id','project_id'],['model_billing_corrections.id','model_billing_corrections.reference_receipt_id','model_billing_corrections.project_id'],ondelete='RESTRICT'),
        CheckConstraint('revision>=1 AND previous_total_minor>=0 AND new_total_minor>=0 AND delta_minor!=0 AND delta_minor=new_total_minor-previous_total_minor'),
        CheckConstraint('spent_before_minor>=0 AND spent_after_minor>=0 AND spent_after_minor-spent_before_minor=delta_minor AND budget_revision>=1'),
        CheckConstraint('length(reason) BETWEEN 4 AND 500'),
    )


class ModelBillingAssignment(Base):
    """Bind an explicit operator choice to one already matching ambiguous call; do not move another hold."""

    __tablename__='model_billing_assignments'
    id: Mapped[str]=mapped_column(String(64),primary_key=True)
    line_id: Mapped[str]=mapped_column(ForeignKey('model_billing_lines.id',ondelete='RESTRICT'),nullable=False,unique=True)
    charge_id: Mapped[str]=mapped_column(String(64),nullable=False)
    project_id: Mapped[str]=mapped_column(String(64),nullable=False)
    reason: Mapped[str]=mapped_column(String(500),nullable=False)
    recorded_by: Mapped[str]=mapped_column(String(64),nullable=False)
    created_at: Mapped[int]=mapped_column(BigInteger,nullable=False)
    __table_args__=(
        ForeignKeyConstraint(['charge_id','project_id'],['model_call_charges.id','model_call_charges.project_id'],ondelete='RESTRICT'),
        CheckConstraint('length(reason) BETWEEN 4 AND 500'),
    )


event.listen(ModelBillingCorrection.__table__,'before_create',DDL(
    'CREATE UNIQUE INDEX IF NOT EXISTS model_billing_receipt_scope ON model_billing_receipts(id,project_id)'
).execute_if(dialect='sqlite'))
