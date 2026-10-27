"""Local assembly jobs and immutable output receipts, independent of paid model execution."""
from sqlalchemy import JSON, Boolean, Float, ForeignKey, ForeignKeyConstraint, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base
from app.models.base import TimestampMixin
import app.models.editing  # noqa: F401


class EditExportJob(Base, TimestampMixin):
    """Durable local render intention; retries retain the identical locked publication."""
    __tablename__ = 'edit_export_jobs'
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
    __table_args__ = (ForeignKeyConstraint(['publication_id', 'project_id'], ['editing_publications.id', 'editing_publications.project_id'], ondelete='RESTRICT'), UniqueConstraint('project_id', 'request_key'), UniqueConstraint('id', 'project_id'))


class EditExportProduct(Base, TimestampMixin):
    """Append-only ready receipt pins MP4, subtitles, package and decoded output facts."""
    __tablename__ = 'edit_export_products'
    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    media_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    artifacts: Mapped[dict] = mapped_column(JSON, nullable=False)
    facts: Mapped[dict] = mapped_column(JSON, nullable=False)
    __table_args__ = (ForeignKeyConstraint(['job_id', 'project_id'], ['edit_export_jobs.id', 'edit_export_jobs.project_id'], ondelete='RESTRICT'), ForeignKeyConstraint(['media_id', 'project_id'], ['production_media.id', 'production_media.project_id'], ondelete='RESTRICT'))


class EditExportSlot(Base):
    """A database-wide single renderer slot with a lease token fencing all publications."""
    __tablename__ = 'edit_export_slot'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey('edit_export_jobs.id', ondelete='RESTRICT'))
    token: Mapped[str | None] = mapped_column(String(64))
    lease_until: Mapped[float] = mapped_column(Float, nullable=False, default=0)
