"""Persist editable Agent configurations and their revision history without executing models."""
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin


class AgentConfig(Base, TimestampMixin):
    """Store local extraction workflow settings independently from real provider execution."""

    __tablename__ = 'agent_configs'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default='')
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_favorite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    prompt: Mapped[str] = mapped_column(Text, nullable=False, default='')
    temperature: Mapped[float] = mapped_column(nullable=False, default=0.7)
    top_p: Mapped[float] = mapped_column(nullable=False, default=0.9)
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    workflow: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default='local')
    updated_by: Mapped[str] = mapped_column(String(64), nullable=False, default='local')
    __table_args__ = (Index('uq_agent_config_default_type', 'type', unique=True, sqlite_where=text('is_default = 1'), postgresql_where=text('is_default = true')),)


class AgentConfigRevision(Base):
    """Retain the exact saved configuration for each numbered revision."""

    __tablename__ = 'agent_config_revisions'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey('agent_configs.id', ondelete='CASCADE'), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    document: Mapped[dict] = mapped_column(JSON, nullable=False)
    saved_at: Mapped[datetime] = mapped_column(nullable=False)
    __table_args__ = (UniqueConstraint('agent_id', 'version', name='uq_agent_config_revision'),)
