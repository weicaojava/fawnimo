"""Independent transition-v2 documents and local-render journals; v1 tables are untouched."""
from sqlalchemy import JSON, Boolean, CheckConstraint, Float, ForeignKey, ForeignKeyConstraint, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base
from app.models.base import TimestampMixin
import app.models.editing  # noqa: F401


class TransitionDraft(Base):
    """A CAS boundary variant pinned permanently to one owned v1 timeline."""
    __tablename__ = 'transition_drafts'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    chapter_id: Mapped[str] = mapped_column(String(64), nullable=False)
    base_publication_id: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    document: Mapped[dict] = mapped_column(JSON, nullable=False)
    __table_args__ = (UniqueConstraint('id', 'project_id'), UniqueConstraint('base_publication_id', 'project_id'), CheckConstraint('revision>=1'), ForeignKeyConstraint(['base_publication_id', 'project_id'], ['editing_publications.id', 'editing_publications.project_id'], ondelete='RESTRICT'), ForeignKeyConstraint(['chapter_id', 'project_id'], ['chapters.id', 'chapters.project_id'], ondelete='RESTRICT'))


class TransitionPublication(Base):
    """Append-only v2 composition; original clocks and adoption provenance are carried unchanged."""
    __tablename__ = 'transition_publications'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    chapter_id: Mapped[str] = mapped_column(String(64), nullable=False)
    draft_id: Mapped[str] = mapped_column(String(64), nullable=False)
    draft_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    __table_args__ = (UniqueConstraint('id', 'project_id'), UniqueConstraint('draft_id', 'draft_revision'), ForeignKeyConstraint(['draft_id', 'project_id'], ['transition_drafts.id', 'transition_drafts.project_id'], ondelete='RESTRICT'), ForeignKeyConstraint(['chapter_id', 'project_id'], ['chapters.id', 'chapters.project_id'], ondelete='RESTRICT'))


class TransitionExportJob(Base, TimestampMixin):
    """A resumable v2 local render intention, never a provider execution job."""
    __tablename__ = 'transition_export_jobs'
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
    __table_args__ = (UniqueConstraint('project_id', 'request_key'), UniqueConstraint('id', 'project_id'), ForeignKeyConstraint(['publication_id', 'project_id'], ['transition_publications.id', 'transition_publications.project_id'], ondelete='RESTRICT'))


class TransitionExportProduct(Base, TimestampMixin):
    """An immutable v2 output receipt, independently downloadable from v1 outputs."""
    __tablename__ = 'transition_export_products'
    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    media_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    artifacts: Mapped[dict] = mapped_column(JSON, nullable=False)
    facts: Mapped[dict] = mapped_column(JSON, nullable=False)
    __table_args__ = (ForeignKeyConstraint(['job_id', 'project_id'], ['transition_export_jobs.id', 'transition_export_jobs.project_id'], ondelete='RESTRICT'), ForeignKeyConstraint(['media_id', 'project_id'], ['production_media.id', 'production_media.project_id'], ondelete='RESTRICT'))


class TransitionExportSlot(Base):
    """One leased v2 encoder lane, separate from the unchanged v1 render queue."""
    __tablename__ = 'transition_export_slot'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey('transition_export_jobs.id', ondelete='RESTRICT'))
    token: Mapped[str | None] = mapped_column(String(64))
    lease_until: Mapped[float] = mapped_column(Float, nullable=False, default=0)
