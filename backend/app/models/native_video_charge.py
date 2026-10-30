"""Seal actual native video jobs and their existing holds into one complete model bill batch."""
from sqlalchemy import DDL, ForeignKeyConstraint, JSON, String, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
import app.models.execution  # noqa: F401 -- concrete scoped job parent
import app.models.model_charge  # noqa: F401 -- shared ledger and batch parents


class NativeVideoChargeBatch(Base):
    """Preserve the original actor, accepted request and complete job plan without a second reservation."""
    __tablename__='native_video_charge_batches'
    id: Mapped[str]=mapped_column(String(64),primary_key=True)
    project_id: Mapped[str]=mapped_column(String(64),nullable=False)
    submitted_by: Mapped[str]=mapped_column(String(64),nullable=False)
    request_key: Mapped[str]=mapped_column(String(128),nullable=False)
    request_hash: Mapped[str]=mapped_column(String(64),nullable=False)
    context: Mapped[dict]=mapped_column(JSON,nullable=False)
    __table_args__=(
        ForeignKeyConstraint(['id','project_id'],['model_charge_batches.id','model_charge_batches.project_id'],ondelete='RESTRICT'),
        UniqueConstraint('project_id','request_key'),
    )


class NativeVideoJobCharge(Base):
    """Use the real execution job identity as the common charge identity and the single ledger hold."""
    __tablename__='native_video_job_charges'
    job_id: Mapped[str]=mapped_column(String(64),primary_key=True)
    project_id: Mapped[str]=mapped_column(String(64),nullable=False)
    batch_id: Mapped[str]=mapped_column(String(64),nullable=False)
    unit_key: Mapped[str]=mapped_column(String(128),nullable=False)
    request_hash: Mapped[str]=mapped_column(String(64),nullable=False)
    snapshot_hash: Mapped[str]=mapped_column(String(64),nullable=False)
    execution_binding_hash: Mapped[str]=mapped_column(String(64),nullable=False)
    __table_args__=(
        ForeignKeyConstraint(['job_id','project_id'],['execution_jobs.id','execution_jobs.project_id'],ondelete='RESTRICT'),
        ForeignKeyConstraint(['job_id','project_id'],['model_call_charges.id','model_call_charges.project_id'],ondelete='RESTRICT'),
        ForeignKeyConstraint(['batch_id','project_id'],['model_charge_batches.id','model_charge_batches.project_id'],ondelete='RESTRICT'),
        UniqueConstraint('batch_id','unit_key'),
    )


event.listen(NativeVideoJobCharge.__table__,'before_create',DDL(
    'CREATE UNIQUE INDEX IF NOT EXISTS native_execution_job_scope ON execution_jobs(id,project_id)'
).execute_if(dialect='sqlite'))
