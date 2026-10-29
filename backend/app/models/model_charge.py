"""Typed model-call metadata links to the existing single-project integer ledger."""
from sqlalchemy import CheckConstraint, DDL, event, ForeignKey, ForeignKeyConstraint, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column

from app.core.db import Base


class ModelChargeBatch(Base):
    """Reserve every planned call atomically before a bounded job becomes executable."""

    __tablename__='model_charge_batches'
    id: Mapped[str]=mapped_column(String(64),primary_key=True)
    scope_key: Mapped[str]=mapped_column(String(160),nullable=False,unique=True)
    project_id: Mapped[str]=mapped_column(ForeignKey('projects.id',ondelete='RESTRICT'),nullable=False)
    submitted_by: Mapped[str]=mapped_column(String(64),nullable=False)
    manifest_hash: Mapped[str]=mapped_column(String(64),nullable=False)
    call_count: Mapped[int]=mapped_column(Integer,nullable=False)
    __table_args__=(UniqueConstraint('id','project_id'),CheckConstraint('call_count>=1 AND call_count<=64'),)


class ModelCallCharge(Base):
    """Keep routing/account/quote identity immutable while dispatch evidence advances once."""

    __tablename__='model_call_charges'
    id: Mapped[str]=mapped_column(String(64),primary_key=True)
    project_id: Mapped[str]=mapped_column(String(64),nullable=False)
    batch_id: Mapped[str]=mapped_column(String(64),nullable=False)
    unit_key: Mapped[str]=mapped_column(String(128),nullable=False)
    kind: Mapped[str]=mapped_column(String(32),nullable=False)
    model_id: Mapped[str]=mapped_column(String(64),nullable=False)
    provider_id: Mapped[str]=mapped_column(String(64),nullable=False)
    provider_connection_version: Mapped[int]=mapped_column(Integer,nullable=False)
    account_version: Mapped[str]=mapped_column(String(128),nullable=False)
    binding_hash: Mapped[str]=mapped_column(String(64),nullable=False)
    price_snapshot: Mapped[dict]=mapped_column(JSON,nullable=False)
    dispatch_state: Mapped[str]=mapped_column(String(16),nullable=False,default='planned')
    provider_request_id: Mapped[str | None]=mapped_column(String(128))
    __table_args__=(
        ForeignKeyConstraint(['id','project_id'],['execution_budget_entries.job_id','execution_budget_entries.project_id'],ondelete='RESTRICT'),
        ForeignKeyConstraint(['batch_id','project_id'],['model_charge_batches.id','model_charge_batches.project_id'],ondelete='RESTRICT'),
        UniqueConstraint('batch_id','unit_key'),
        UniqueConstraint('id','project_id'),
        CheckConstraint("dispatch_state IN ('planned','prepared','accepted','unknown','not_sent')"),
    )


# All supported additive schema entry points may create missing ORM tables.
# Ensure the composite parent key before the child exists, including old ledgers.
event.listen(ModelCallCharge.__table__,'before_create',DDL(
    'CREATE UNIQUE INDEX IF NOT EXISTS execution_budget_entry_scope ON execution_budget_entries(job_id,project_id)'
).execute_if(dialect='sqlite'))
