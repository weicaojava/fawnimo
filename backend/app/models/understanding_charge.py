"""Retain one immutable original-project model batch for each explicit vision/ASR attempt."""
from sqlalchemy import CheckConstraint,ForeignKeyConstraint,Integer,JSON,String,UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column

from app.core.db import Base
import app.models.model_charge  # noqa: F401 -- register the scoped batch parent
import app.models.video_understanding  # noqa: F401 -- register the concrete journal parents


class UnderstandingAttemptCharge(Base):
    """Retries get distinct complete batches while preserving the job's original source and project."""
    __tablename__='understanding_attempt_charges'
    job_id: Mapped[str]=mapped_column(String(64),primary_key=True)
    attempt: Mapped[int]=mapped_column(Integer,primary_key=True)
    project_id: Mapped[str]=mapped_column(String(64),nullable=False)
    batch_id: Mapped[str]=mapped_column(String(64),nullable=False,unique=True)
    submitted_by: Mapped[str]=mapped_column(String(64),nullable=False)
    request_key: Mapped[str]=mapped_column(String(128),nullable=False)
    request_hash: Mapped[str]=mapped_column(String(64),nullable=False)
    job_request_hash: Mapped[str]=mapped_column(String(64),nullable=False)
    bindings: Mapped[dict]=mapped_column(JSON,nullable=False)
    __table_args__=(
        ForeignKeyConstraint(['job_id','attempt'],['video_understanding_attempts.job_id','video_understanding_attempts.attempt'],ondelete='RESTRICT'),
        ForeignKeyConstraint(['job_id','project_id'],['video_understanding_jobs.id','video_understanding_jobs.project_id'],ondelete='RESTRICT'),
        ForeignKeyConstraint(['batch_id','project_id'],['model_charge_batches.id','model_charge_batches.project_id'],ondelete='RESTRICT'),
        UniqueConstraint('job_id','request_key'),
        CheckConstraint('attempt>=1 AND attempt<=2147483647 AND length(request_key) BETWEEN 1 AND 128 AND length(request_hash)=64 AND length(job_request_hash)=64'),
    )
