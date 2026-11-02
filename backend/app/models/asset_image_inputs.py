"""Sealed image request sets retain exact ordered project-owned reference proofs."""
from sqlalchemy import Boolean, CheckConstraint, ForeignKey, ForeignKeyConstraint, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
import app.models.asset_image_generation  # noqa: F401 -- register the actual immutable attempt parent
import app.models.production  # noqa: F401 -- register project-owned media composite parent


class AssetImageInputSet(Base):
    """One mandatory sealed protocol-specific request set for each new Seedream attempt."""
    __tablename__ = 'asset_image_input_sets'
    job_id: Mapped[str] = mapped_column(ForeignKey('asset_image_generation_jobs.id', ondelete='RESTRICT'), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id', ondelete='RESTRICT'), nullable=False)
    protocol: Mapped[str] = mapped_column(String(64), nullable=False)
    profile: Mapped[str] = mapped_column(String(32), nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    size: Mapped[str] = mapped_column(String(32), nullable=False)
    response_format: Mapped[str] = mapped_column(String(16), nullable=False)
    reference_count: Mapped[int] = mapped_column(Integer, nullable=False)
    reference_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    binding_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    sealed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    __table_args__ = (UniqueConstraint('job_id', 'project_id'),
        CheckConstraint("protocol='volcengine_seedream_json_v1' AND profile IN ('single_v4','single_v5') AND response_format IN ('b64_json','url') AND size IN ('2048x2048','2848x1600','1600x2848') AND typeof(reference_count)='integer' AND reference_count BETWEEN 0 AND 3 AND sealed IN (0,1) AND length(reference_hash)=64 AND length(request_hash)=64 AND length(binding_hash)=64"),)


class AssetImageReference(Base):
    """Immutable ordered original-project bytes; purpose is an explicit instruction, never a quality claim."""
    __tablename__ = 'asset_image_references'
    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    media_id: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    mime: Mapped[str] = mapped_column(String(32), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(255), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(['job_id', 'project_id'], ['asset_image_input_sets.job_id', 'asset_image_input_sets.project_id'], ondelete='RESTRICT'),
        ForeignKeyConstraint(['media_id', 'project_id'], ['production_media.id', 'production_media.project_id'], ondelete='RESTRICT'),
        UniqueConstraint('job_id', 'media_id'),
        CheckConstraint("typeof(position)='integer' AND position BETWEEN 0 AND 2 AND purpose IN ('appearance','composition','style') AND length(sha256)=64 AND typeof(byte_size)='integer' AND byte_size BETWEEN 1 AND 4194304 AND typeof(width)='integer' AND typeof(height)='integer' AND width BETWEEN 15 AND 4096 AND height BETWEEN 15 AND 4096 AND width*height<=16000000 AND mime IN ('image/png','image/jpeg')"),
    )
