"""Persistent image attempts whose successful bytes and provenance are immutable."""
from datetime import datetime
from sqlalchemy import Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base


class AssetImageJob(Base):
    """Track one authorized image HTTP request independently from adoption into a user slot."""
    __tablename__ = 'asset_image_generation_jobs'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(160), nullable=False)
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    asset_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    slot_id: Mapped[int] = mapped_column(Integer, nullable=False)
    model_id: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    model_binding: Mapped[dict] = mapped_column(JSON, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    size: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    error: Mapped[str] = mapped_column(Text, nullable=False, default='')
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    retry_of: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    storage_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    byte_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mime: Mapped[str | None] = mapped_column(String(32), nullable=True)
    file_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    adopt_request_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    adopt_request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    adopted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    __table_args__ = (UniqueConstraint('scope_key', 'request_key', name='uq_asset_image_request'),)
