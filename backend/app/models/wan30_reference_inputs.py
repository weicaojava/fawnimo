"""Additive immutable Wan image-input tables; the existing Seedance input schema and history remain untouched."""
from sqlalchemy import Boolean,CheckConstraint,DDL,ForeignKeyConstraint,Integer,String,UniqueConstraint,event
from sqlalchemy.orm import Mapped,mapped_column

from app.core.db import Base
import app.models.execution
import app.models.production


class Wan30ReferenceInputSet(Base):
    """Seal a complete Wan image set inside the actual native job/project reservation transaction."""
    __tablename__='wan30_reference_input_sets'
    job_id:Mapped[str]=mapped_column(String(64),primary_key=True)
    project_id:Mapped[str]=mapped_column(String(64),nullable=False)
    protocol:Mapped[str]=mapped_column(String(64),nullable=False)
    mode:Mapped[str]=mapped_column(String(32),nullable=False)
    reference_count:Mapped[int]=mapped_column(Integer,nullable=False)
    reference_hash:Mapped[str]=mapped_column(String(64),nullable=False)
    selection_hash:Mapped[str]=mapped_column(String(64),nullable=False)
    request_hash:Mapped[str]=mapped_column(String(64),nullable=False)
    snapshot_hash:Mapped[str]=mapped_column(String(64),nullable=False)
    binding_hash:Mapped[str]=mapped_column(String(64),nullable=False)
    connection_hash:Mapped[str]=mapped_column(String(64),nullable=False)
    profile_hash:Mapped[str]=mapped_column(String(64),nullable=False)
    sealed:Mapped[bool]=mapped_column(Boolean,nullable=False,default=False)
    __table_args__=(
        ForeignKeyConstraint(['job_id','project_id'],['execution_jobs.id','execution_jobs.project_id'],ondelete='RESTRICT'),
        UniqueConstraint('job_id','project_id'),
        CheckConstraint("protocol='wan30_reference_v1' AND mode IN ('reference','first_frame','first_last_frames') AND typeof(reference_count)='integer' AND reference_count BETWEEN 1 AND 3 AND sealed IN (0,1) AND length(reference_hash)=64 AND length(selection_hash)=64 AND length(request_hash)=64 AND length(snapshot_hash)=64 AND length(binding_hash)=64 AND length(connection_hash)=64 AND length(profile_hash)=64"),
        {'sqlite_with_rowid':False},
    )


class Wan30ReferenceInput(Base):
    """Retain one exact ordered original-project image; no rowid alias, video URL or delivery authority exists."""
    __tablename__='wan30_reference_inputs'
    job_id:Mapped[str]=mapped_column(String(64),primary_key=True)
    position:Mapped[int]=mapped_column(Integer,primary_key=True)
    project_id:Mapped[str]=mapped_column(String(64),nullable=False)
    media_id:Mapped[str]=mapped_column(String(64),nullable=False)
    role:Mapped[str]=mapped_column(String(32),nullable=False)
    sha256:Mapped[str]=mapped_column(String(64),nullable=False)
    byte_size:Mapped[int]=mapped_column(Integer,nullable=False)
    mime:Mapped[str]=mapped_column(String(32),nullable=False)
    relative_path:Mapped[str]=mapped_column(String(255),nullable=False)
    width:Mapped[int]=mapped_column(Integer,nullable=False)
    height:Mapped[int]=mapped_column(Integer,nullable=False)
    duration_us:Mapped[int|None]=mapped_column(Integer)
    frames:Mapped[int|None]=mapped_column(Integer)
    fps:Mapped[int|None]=mapped_column(Integer)
    frame_clock_sha256:Mapped[str|None]=mapped_column(String(64))
    has_audio:Mapped[bool|None]=mapped_column(Boolean)
    audio_samples:Mapped[int|None]=mapped_column(Integer)
    audio_channels:Mapped[int|None]=mapped_column(Integer)
    __table_args__=(
        ForeignKeyConstraint(['job_id','project_id'],['wan30_reference_input_sets.job_id','wan30_reference_input_sets.project_id'],ondelete='RESTRICT'),
        ForeignKeyConstraint(['media_id','project_id'],['production_media.id','production_media.project_id'],ondelete='RESTRICT'),
        CheckConstraint("typeof(position)='integer' AND position BETWEEN 0 AND 2 AND length(sha256)=64 AND typeof(byte_size)='integer' AND byte_size BETWEEN 1 AND 4194304 AND typeof(width)='integer' AND typeof(height)='integer' AND width BETWEEN 300 AND 4096 AND height BETWEEN 300 AND 4096 AND width*height<=16000000 AND width*5>=height*2 AND width*2<=height*5 AND role IN ('first_frame','last_frame','reference_image') AND mime IN ('image/png','image/jpeg') AND duration_us IS NULL AND frames IS NULL AND fps IS NULL AND frame_clock_sha256 IS NULL AND has_audio IS NULL AND audio_samples IS NULL AND audio_channels IS NULL AND (role='reference_image' OR width*16=height*9)"),
        {'sqlite_with_rowid':False},
    )


event.listen(Wan30ReferenceInputSet.__table__,'before_create',DDL(
    'CREATE UNIQUE INDEX IF NOT EXISTS native_execution_job_scope ON execution_jobs(id,project_id)'
).execute_if(dialect='sqlite'))
