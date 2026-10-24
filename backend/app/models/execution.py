"""Durable execution journal, separate from mutable UI task projections."""
from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base


class ExecutionJob(Base):
    """Immutable request snapshot plus a resumable, leased execution state."""
    __tablename__='execution_jobs'
    id: Mapped[str]=mapped_column(String(64),primary_key=True)
    task_id: Mapped[str]=mapped_column(ForeignKey('generation_tasks.id',ondelete='RESTRICT'),unique=True,nullable=False)
    project_id: Mapped[str]=mapped_column(ForeignKey('projects.id',ondelete='RESTRICT'),nullable=False)
    request_key: Mapped[str]=mapped_column(String(128),nullable=False)
    request_hash: Mapped[str]=mapped_column(String(64),nullable=False)
    snapshot: Mapped[dict]=mapped_column(JSON,nullable=False)
    state: Mapped[str]=mapped_column(String(32),nullable=False,default='queued')
    remote_id: Mapped[str | None]=mapped_column(String(255))
    result: Mapped[dict | None]=mapped_column(JSON)
    media_id: Mapped[str | None]=mapped_column(String(64))
    lease_token: Mapped[str | None]=mapped_column(String(64))
    lease_until: Mapped[float]=mapped_column(Float,nullable=False,default=0)
    next_due: Mapped[float]=mapped_column(Float,nullable=False,default=0)
    started_at: Mapped[float | None]=mapped_column(Float)
    query_retries: Mapped[int]=mapped_column(Integer,nullable=False,default=0)
    download_retries: Mapped[int]=mapped_column(Integer,nullable=False,default=0)
    cancel_requested: Mapped[bool]=mapped_column(Boolean,nullable=False,default=False)
    pause_from: Mapped[str | None]=mapped_column(String(32))
    reason: Mapped[str]=mapped_column(String(255),nullable=False,default='')
    __table_args__=(UniqueConstraint('project_id','request_key'),)


class SubmissionAttempt(Base):
    """Committed before sending any request; unknown acceptance never permits a blind resend."""
    __tablename__='submission_attempts'
    id: Mapped[str]=mapped_column(String(64),primary_key=True)
    job_id: Mapped[str]=mapped_column(ForeignKey('execution_jobs.id',ondelete='RESTRICT'),unique=True,nullable=False)
    request_hash: Mapped[str]=mapped_column(String(64),nullable=False)
    state: Mapped[str]=mapped_column(String(32),nullable=False,default='prepared')
    remote_id: Mapped[str | None]=mapped_column(String(255))


class ExecutionEvent(Base):
    """Append-only audit events also deduplicate authenticated callback notifications."""
    __tablename__='execution_events'
    id: Mapped[str]=mapped_column(String(64),primary_key=True)
    job_id: Mapped[str]=mapped_column(ForeignKey('execution_jobs.id',ondelete='RESTRICT'),nullable=False)
    event_key: Mapped[str]=mapped_column(String(128),nullable=False)
    kind: Mapped[str]=mapped_column(String(64),nullable=False)
    detail: Mapped[dict]=mapped_column(JSON,nullable=False)
    __table_args__=(UniqueConstraint('job_id','event_key'),)


class ExecutionSlot(Base):
    """Single-service concurrency lease, independent of each job's fencing lease."""
    __tablename__='execution_slots'
    id: Mapped[int]=mapped_column(Integer,primary_key=True)
    token: Mapped[str | None]=mapped_column(String(64))
    job_id: Mapped[str | None]=mapped_column(String(64))
    lease_until: Mapped[float]=mapped_column(Float,nullable=False,default=0)
