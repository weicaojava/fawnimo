"""Append-only chapter text snapshots for real editing and restore operations."""
from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base
import app.models.studio_projects  # noqa: F401


class ChapterTextRevision(Base):
    """Retain saved text; restoring a revision creates another row instead of mutating it."""
    __tablename__ = 'chapter_text_revisions'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    chapter_id: Mapped[str] = mapped_column(ForeignKey('chapters.id', ondelete='CASCADE'), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    condensed_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    origin: Mapped[str] = mapped_column(String(32), nullable=False)
    restored_from_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (UniqueConstraint('chapter_id', 'revision'), CheckConstraint('revision >= 1'))
