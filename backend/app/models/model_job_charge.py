"""Immutable linkage between a business attempt and its original project model-call batch."""
from sqlalchemy import CheckConstraint,ForeignKeyConstraint,String
from sqlalchemy.orm import Mapped,mapped_column

from app.core.db import Base
import app.models.model_charge  # noqa: F401 -- register composite batch parent for standalone users


class ModelJobCharge(Base):
    """Keep retries, cancellation and late responses tied to the originally authorized job and project."""

    __tablename__='model_job_charges'
    job_kind: Mapped[str]=mapped_column(String(32),primary_key=True)
    job_id: Mapped[str]=mapped_column(String(64),primary_key=True)
    project_id: Mapped[str]=mapped_column(String(64),nullable=False)
    batch_id: Mapped[str]=mapped_column(String(64),nullable=False,unique=True)
    submitted_by: Mapped[str]=mapped_column(String(64),nullable=False)
    request_hash: Mapped[str]=mapped_column(String(64),nullable=False)
    __table_args__=(ForeignKeyConstraint(['batch_id','project_id'],['model_charge_batches.id','model_charge_batches.project_id'],ondelete='RESTRICT'),
        CheckConstraint("job_kind IN ('agent','image') AND length(job_id) BETWEEN 1 AND 64 AND length(request_hash)=64"),)
