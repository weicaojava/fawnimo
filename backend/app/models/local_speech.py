"""Durable offline speech intentions and append-only sound candidate linkage."""
from sqlalchemy import Boolean, CheckConstraint, Float, ForeignKey, ForeignKeyConstraint, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base
from app.models.base import TimestampMixin
import app.models.sound_timeline  # noqa: F401


class LocalSpeechJob(Base, TimestampMixin):
    """The immutable selected engine/voice/text intent is separate from recoverable local execution state."""
    __tablename__ = 'local_speech_jobs'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    chapter_id: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[str] = mapped_column(ForeignKey('generation_tasks.id', ondelete='RESTRICT'), unique=True, nullable=False)
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    intent_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    intent: Mapped[dict] = mapped_column(JSON, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default='queued')
    reason: Mapped[str] = mapped_column(String(255), nullable=False, default='')
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_until: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    __table_args__ = (UniqueConstraint('id', 'project_id'), UniqueConstraint('project_id', 'request_key'),
        CheckConstraint("state IN ('queued','running','ready','failed','cancelled')"), CheckConstraint('attempts>=0 AND attempts<=3'),
        ForeignKeyConstraint(['chapter_id', 'project_id'], ['chapters.id', 'chapters.project_id'], ondelete='RESTRICT'))


class LocalSpeechProduct(Base):
    """Append-only linkage to the one published original-project audio candidate."""
    __tablename__ = 'local_speech_products'
    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    intent_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (ForeignKeyConstraint(['job_id', 'project_id'], ['local_speech_jobs.id', 'local_speech_jobs.project_id'], ondelete='RESTRICT'),
        ForeignKeyConstraint(['candidate_id', 'project_id'], ['sound_candidates.id', 'sound_candidates.project_id'], ondelete='RESTRICT'))


class LocalSpeechSlot(Base):
    """One server-owned local synthesis lane, fenced independently from movie encoders."""
    __tablename__ = 'local_speech_slot'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey('local_speech_jobs.id', ondelete='RESTRICT'))
    token: Mapped[str | None] = mapped_column(String(64))
    lease_until: Mapped[float] = mapped_column(Float, nullable=False, default=0)
