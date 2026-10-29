"""Durable source and connection snapshots for explicitly requested video-understanding calls."""
from sqlalchemy import JSON, Boolean, CheckConstraint, Float, ForeignKey, ForeignKeyConstraint, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base
import app.models.reverse  # noqa: F401
import app.models.video_dna  # noqa: F401


class UnderstandingConfig(Base):
    """Store no key values; a project explicitly opts into one dedicated vision/ASR connection."""
    __tablename__ = 'video_understanding_configs'
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    connection: Mapped[dict] = mapped_column(JSON, nullable=False)
    __table_args__ = (CheckConstraint('revision >= 1'),)


class UnderstandingJob(Base):
    """Retain fixed original clocks, immutable submitted setup and a reviewable machine candidate."""
    __tablename__ = 'video_understanding_jobs'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    analysis_id: Mapped[str] = mapped_column(String(64), nullable=False)
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[dict] = mapped_column(JSON, nullable=False)
    connection: Mapped[dict] = mapped_column(JSON, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default='queued')
    phase: Mapped[str] = mapped_column(String(32), nullable=False, default='queued')
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_until: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    reason: Mapped[str] = mapped_column(String(255), nullable=False, default='')
    document: Mapped[dict | None] = mapped_column(JSON)
    transcripts: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    warnings: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    draft_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(['analysis_id', 'project_id'], ['reverse_analyses.id', 'reverse_analyses.project_id'], ondelete='RESTRICT'),
        ForeignKeyConstraint(['draft_id', 'project_id'], ['video_dna_drafts.id', 'video_dna_drafts.project_id'], ondelete='RESTRICT'),
        UniqueConstraint('project_id', 'request_key'), UniqueConstraint('id', 'project_id'),
        CheckConstraint("state IN ('queued','running','succeeded','failed','cancelled')"), CheckConstraint('attempt >= 1'),
    )


class UnderstandingAttempt(Base):
    """Record each explicit attempt, including requests that fail before any publishable candidate."""
    __tablename__ = 'video_understanding_attempts'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[float | None] = mapped_column(Float)
    finished_at: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(255), nullable=False, default='')
    input_receipt: Mapped[dict | None] = mapped_column(JSON)
    response_hash: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (
        ForeignKeyConstraint(['job_id', 'project_id'], ['video_understanding_jobs.id', 'video_understanding_jobs.project_id'], ondelete='RESTRICT'),
        UniqueConstraint('job_id', 'attempt'),
    )

