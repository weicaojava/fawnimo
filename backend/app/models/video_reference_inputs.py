"""Typed immutable original-project conditions for the separately declared native video profile."""
from sqlalchemy import Boolean, CheckConstraint, DDL, Float, ForeignKey, ForeignKeyConstraint, Integer, String, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
import app.models.execution  # noqa: F401 -- exact original job parent
import app.models.production  # noqa: F401 -- exact project-media parent


class VideoReferenceInputSet(Base):
    """One complete protocol-specific input set sealed with its real native job and original financial binding."""
    __tablename__ = 'video_reference_input_sets'
    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    protocol: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    reference_count: Mapped[int] = mapped_column(Integer, nullable=False)
    reference_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    selection_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    binding_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    connection_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    sealed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    __table_args__ = (
        ForeignKeyConstraint(['job_id', 'project_id'], ['execution_jobs.id', 'execution_jobs.project_id'], ondelete='RESTRICT'),
        UniqueConstraint('job_id', 'project_id'),
        CheckConstraint("protocol='seedance25_reference_v1' AND mode IN ('reference','first_frame','first_last_frames') AND typeof(reference_count)='integer' AND reference_count BETWEEN 1 AND 4 AND sealed IN (0,1) AND length(reference_hash)=64 AND length(selection_hash)=64 AND length(request_hash)=64 AND length(snapshot_hash)=64 AND length(binding_hash)=64 AND length(connection_hash)=64 AND length(profile_hash)=64"),
    )


class VideoReferenceInput(Base):
    """One original ordered condition; the same media can serve both explicit frame roles without losing either position."""
    __tablename__ = 'video_reference_inputs'
    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    media_id: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    mime: Mapped[str] = mapped_column(String(32), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(255), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_us: Mapped[int | None] = mapped_column(Integer)
    frames: Mapped[int | None] = mapped_column(Integer)
    fps: Mapped[int | None] = mapped_column(Integer)
    frame_clock_sha256: Mapped[str | None] = mapped_column(String(64))
    has_audio: Mapped[bool | None] = mapped_column(Boolean)
    audio_samples: Mapped[int | None] = mapped_column(Integer)
    audio_channels: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (
        UniqueConstraint('job_id', 'position', 'project_id'),
        ForeignKeyConstraint(['job_id', 'project_id'], ['video_reference_input_sets.job_id', 'video_reference_input_sets.project_id'], ondelete='RESTRICT'),
        ForeignKeyConstraint(['media_id', 'project_id'], ['production_media.id', 'production_media.project_id'], ondelete='RESTRICT'),
        CheckConstraint("typeof(position)='integer' AND position BETWEEN 0 AND 3 AND length(sha256)=64 AND typeof(byte_size)='integer' AND byte_size>0 AND typeof(width)='integer' AND typeof(height)='integer' AND width BETWEEN 300 AND 4096 AND height BETWEEN 300 AND 4096 AND width*height<=16000000 AND width*5>=height*2 AND width*2<=height*5"),
        CheckConstraint("(role IN ('first_frame','last_frame','reference_image') AND mime IN ('image/png','image/jpeg') AND byte_size<=4194304 AND duration_us IS NULL AND frames IS NULL AND fps IS NULL AND frame_clock_sha256 IS NULL AND has_audio IS NULL AND audio_samples IS NULL AND audio_channels IS NULL AND (role='reference_image' OR width*16=height*9)) OR (role='reference_video' AND mime='video/mp4' AND byte_size<=33554432 AND width<=1920 AND height<=1920 AND width*height BETWEEN 407696 AND 2073600 AND typeof(duration_us)='integer' AND duration_us BETWEEN 2000000 AND 10000000 AND typeof(frames)='integer' AND frames BETWEEN 1 AND 600 AND typeof(fps)='integer' AND fps IN (24,25,30) AND frame_clock_sha256 IS NOT NULL AND length(frame_clock_sha256)=64 AND has_audio IS NOT NULL AND has_audio IN (0,1) AND typeof(audio_samples)='integer' AND audio_samples BETWEEN 0 AND 528000 AND typeof(audio_channels)='integer' AND audio_channels BETWEEN 0 AND 2 AND ((has_audio=0 AND audio_samples=0 AND audio_channels=0) OR (has_audio=1 AND audio_samples>0 AND audio_channels>0)))"),
    )


event.listen(VideoReferenceInputSet.__table__, 'before_create', DDL(
    'CREATE UNIQUE INDEX IF NOT EXISTS native_execution_job_scope ON execution_jobs(id,project_id)'
).execute_if(dialect='sqlite'))


class ExecutionReferenceDelivery(Base):
    """One short-lived read capability for a video's actual original attempt; raw capability tokens are never persisted."""
    __tablename__ = 'execution_reference_deliveries'
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(64), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_id: Mapped[str] = mapped_column(ForeignKey('submission_attempts.id', ondelete='RESTRICT'), nullable=False)
    token_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    expires_at: Mapped[float] = mapped_column(Float, nullable=False)
    access_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    __table_args__ = (
        ForeignKeyConstraint(['job_id', 'position', 'project_id'],
            ['video_reference_inputs.job_id', 'video_reference_inputs.position', 'video_reference_inputs.project_id'], ondelete='RESTRICT'),
        UniqueConstraint('attempt_id', 'position'),
        CheckConstraint("length(id)=32 AND length(token_sha256)=64 AND typeof(position)='integer' AND position BETWEEN 0 AND 3 AND typeof(access_count)='integer' AND access_count BETWEEN 0 AND 16 AND expires_at-created_at=3600"),
    )


event.listen(ExecutionReferenceDelivery.__table__, 'before_create', DDL(
    'CREATE UNIQUE INDEX IF NOT EXISTS native_reference_item_scope ON video_reference_inputs(job_id,position,project_id)'
).execute_if(dialect='sqlite'))
