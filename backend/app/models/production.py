"""Project-owned immutable asset snapshots and local media for production work."""
from sqlalchemy import JSON, Boolean, ForeignKey, ForeignKeyConstraint, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base
from app.models.base import TimestampMixin


class ProductionAsset(Base, TimestampMixin):
    """Stable asset identity; archiving preserves all versions and adopted references."""
    __tablename__ = 'production_assets'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id', ondelete='RESTRICT'), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    subject_id: Mapped[str | None] = mapped_column(ForeignKey('characters.id', ondelete='RESTRICT'))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    __table_args__ = (UniqueConstraint('id', 'project_id'),)


class ProductionMedia(Base, TimestampMixin):
    """Validated immutable bytes owned by one project and identified by a file ID."""
    __tablename__ = 'production_media'
    id: Mapped[str] = mapped_column(ForeignKey('files.id', ondelete='RESTRICT'), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id', ondelete='RESTRICT'), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime: Mapped[str] = mapped_column(String(64), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    relative_path: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    __table_args__ = (UniqueConstraint('id', 'project_id'),)


class AssetVersion(Base, TimestampMixin):
    """Append-only snapshot; revision is allocated under the asset row's lock."""
    __tablename__ = 'production_asset_versions'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    asset_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (ForeignKeyConstraint(['asset_id','project_id'], ['production_assets.id','production_assets.project_id'], ondelete='RESTRICT'), UniqueConstraint('asset_id','revision'), UniqueConstraint('id','project_id'))


class VersionMedia(Base):
    """Composite foreign keys prevent cross-project media references even in direct SQL."""
    __tablename__ = 'production_version_media'
    version_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    media_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (ForeignKeyConstraint(['version_id','project_id'], ['production_asset_versions.id','production_asset_versions.project_id'], ondelete='RESTRICT'), ForeignKeyConstraint(['media_id','project_id'], ['production_media.id','production_media.project_id'], ondelete='RESTRICT'))


class ProductionTake(Base, TimestampMixin):
    """Immutable imported/adopted take; this record is not proof of AI generation."""
    __tablename__ = 'production_takes'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id', ondelete='RESTRICT'), nullable=False)
    shot_id: Mapped[str] = mapped_column(ForeignKey('shots.id', ondelete='RESTRICT'), nullable=False)
    media_id: Mapped[str] = mapped_column(String(64), nullable=False)
    provenance: Mapped[str] = mapped_column(String(32), nullable=False, default='user_import')
    version_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    __table_args__ = (ForeignKeyConstraint(['media_id','project_id'], ['production_media.id','production_media.project_id'], ondelete='RESTRICT'), UniqueConstraint('id','project_id'))


class TakeVersion(Base):
    """Pins each take to exact snapshot IDs, never a mutable latest-version pointer."""
    __tablename__ = 'production_take_versions'
    take_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (ForeignKeyConstraint(['take_id','project_id'], ['production_takes.id','production_takes.project_id'], ondelete='RESTRICT'), ForeignKeyConstraint(['version_id','project_id'], ['production_asset_versions.id','production_asset_versions.project_id'], ondelete='RESTRICT'))


class ShotSelection(Base, TimestampMixin):
    """Current adoption pointer; changing it never modifies any historical take."""
    __tablename__ = 'production_shot_selections'
    shot_id: Mapped[str] = mapped_column(ForeignKey('shots.id', ondelete='RESTRICT'), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id', ondelete='RESTRICT'), nullable=False)
    take_id: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (ForeignKeyConstraint(['take_id','project_id'], ['production_takes.id','production_takes.project_id'], ondelete='RESTRICT'),)
