"""Independent sound documents and immutable decoded candidate receipts."""
from sqlalchemy import Boolean, Float, ForeignKey, JSON, CheckConstraint, ForeignKeyConstraint, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base
from app.models.base import TimestampMixin
import app.models.editing  # noqa: F401


class SoundCandidate(Base):
    """An append-only candidate; importing does not modify a picture take or an audio track."""
    __tablename__ = 'sound_candidates'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    chapter_id: Mapped[str] = mapped_column(String(64), nullable=False)
    media_id: Mapped[str] = mapped_column(String(64), nullable=False)
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    intent_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    origin: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False)
    __table_args__ = (UniqueConstraint('id', 'project_id'), UniqueConstraint('project_id', 'request_key'),
        CheckConstraint("kind IN ('voice','effect','music')"), CheckConstraint("origin IN ('user_import','local_speech')"),
        ForeignKeyConstraint(['chapter_id', 'project_id'], ['chapters.id', 'chapters.project_id'], ondelete='RESTRICT'),
        ForeignKeyConstraint(['media_id', 'project_id'], ['production_media.id', 'production_media.project_id'], ondelete='RESTRICT'))


class SoundDraft(Base):
    """A whole chapter sound form with its own picture copy and CAS revision."""
    __tablename__ = 'sound_drafts'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    chapter_id: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    document: Mapped[dict] = mapped_column(JSON, nullable=False)
    __table_args__ = (UniqueConstraint('id', 'project_id'), UniqueConstraint('chapter_id', 'project_id'), CheckConstraint('revision>=1'),
        ForeignKeyConstraint(['chapter_id', 'project_id'], ['chapters.id', 'chapters.project_id'], ondelete='RESTRICT'))


class SoundPublication(Base):
    """One immutable sound revision, pinning every picture and audio byte identity."""
    __tablename__ = 'sound_publications'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    chapter_id: Mapped[str] = mapped_column(String(64), nullable=False)
    draft_id: Mapped[str] = mapped_column(String(64), nullable=False)
    draft_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    __table_args__ = (UniqueConstraint('id', 'project_id'), UniqueConstraint('draft_id', 'draft_revision'),
        ForeignKeyConstraint(['draft_id', 'project_id'], ['sound_drafts.id', 'sound_drafts.project_id'], ondelete='RESTRICT'),
        ForeignKeyConstraint(['chapter_id', 'project_id'], ['chapters.id', 'chapters.project_id'], ondelete='RESTRICT'))


class SoundExportJob(Base, TimestampMixin):
    """A resumable v3 local render intention, never a provider execution job."""
    __tablename__ = 'sound_export_jobs'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id', ondelete='RESTRICT'), nullable=False)
    chapter_id: Mapped[str] = mapped_column(ForeignKey('chapters.id', ondelete='RESTRICT'), nullable=False)
    publication_id: Mapped[str] = mapped_column(String(64), nullable=False)
    publication_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[str] = mapped_column(ForeignKey('generation_tasks.id', ondelete='RESTRICT'), nullable=False, unique=True)
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    intent_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default='queued', index=True)
    reason: Mapped[str] = mapped_column(String(255), nullable=False, default='')
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_until: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    __table_args__ = (UniqueConstraint('project_id', 'request_key'), UniqueConstraint('id', 'project_id'), ForeignKeyConstraint(['publication_id', 'project_id'], ['sound_publications.id', 'sound_publications.project_id'], ondelete='RESTRICT'))


class SoundExportProduct(Base, TimestampMixin):
    """An immutable v3 output receipt, independently downloadable from v1 outputs."""
    __tablename__ = 'sound_export_products'
    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    media_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    artifacts: Mapped[dict] = mapped_column(JSON, nullable=False)
    facts: Mapped[dict] = mapped_column(JSON, nullable=False)
    __table_args__ = (ForeignKeyConstraint(['job_id', 'project_id'], ['sound_export_jobs.id', 'sound_export_jobs.project_id'], ondelete='RESTRICT'), ForeignKeyConstraint(['media_id', 'project_id'], ['production_media.id', 'production_media.project_id'], ondelete='RESTRICT'))


class SoundExportSlot(Base):
    """One leased v3 encoder lane, separate from the unchanged v1 render queue."""
    __tablename__ = 'sound_export_slot'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey('sound_export_jobs.id', ondelete='RESTRICT'))
    token: Mapped[str | None] = mapped_column(String(64))
    lease_until: Mapped[float] = mapped_column(Float, nullable=False, default=0)
