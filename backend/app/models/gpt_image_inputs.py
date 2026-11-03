"""Separate additive GPT Image input proofs preserve the existing Seedream schema and all historical attempts unchanged."""
from sqlalchemy import Boolean,CheckConstraint,ForeignKey,ForeignKeyConstraint,Integer,String,UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column
from app.core.db import Base
import app.models.asset_image_generation  # noqa: F401 -- register the immutable original job parent
import app.models.production  # noqa: F401 -- register original-project media ownership


class GptImageInputSet(Base):
    """One sealed fixed-profile input set accompanies every new GPT Image attempt, including explicitly text-only requests."""
    __tablename__='gpt_image_input_sets'
    job_id:Mapped[str]=mapped_column(ForeignKey('asset_image_generation_jobs.id',ondelete='RESTRICT'),primary_key=True)
    project_id:Mapped[str]=mapped_column(ForeignKey('projects.id',ondelete='RESTRICT'),nullable=False)
    protocol:Mapped[str]=mapped_column(String(64),nullable=False)
    profile:Mapped[str]=mapped_column(String(32),nullable=False)
    model_name:Mapped[str]=mapped_column(String(255),nullable=False)
    size:Mapped[str]=mapped_column(String(32),nullable=False)
    response_format:Mapped[str]=mapped_column(String(16),nullable=False)
    reference_count:Mapped[int]=mapped_column(Integer,nullable=False)
    reference_hash:Mapped[str]=mapped_column(String(64),nullable=False)
    request_hash:Mapped[str]=mapped_column(String(64),nullable=False)
    binding_hash:Mapped[str]=mapped_column(String(64),nullable=False)
    sealed:Mapped[bool]=mapped_column(Boolean,nullable=False,default=False)
    __table_args__=(UniqueConstraint('job_id','project_id'),CheckConstraint(
        "protocol='openai_gpt_image_json_v1' AND profile='gpt25_single_medium_png_v1' AND response_format='b64_json' "
        "AND model_name IN ('gpt-image-2.5-sunburst-2026-09-08','gpt-image-2.5-flare-2026-09-08') "
        "AND size IN ('1024x1024','1536x1024','1024x1536') AND typeof(reference_count)='integer' "
        "AND reference_count BETWEEN 0 AND 3 AND sealed IN (0,1) AND length(reference_hash)=64 AND length(request_hash)=64 AND length(binding_hash)=64"),{'sqlite_with_rowid':False})


class GptImageReference(Base):
    """Retain ordered exact original-project PNG/JPEG proofs; these rows cannot authorize paths, URLs or another project's media."""
    __tablename__='gpt_image_references'
    job_id:Mapped[str]=mapped_column(String(64),primary_key=True)
    position:Mapped[int]=mapped_column(Integer,primary_key=True)
    project_id:Mapped[str]=mapped_column(String(64),nullable=False)
    media_id:Mapped[str]=mapped_column(String(64),nullable=False)
    purpose:Mapped[str]=mapped_column(String(16),nullable=False)
    sha256:Mapped[str]=mapped_column(String(64),nullable=False)
    byte_size:Mapped[int]=mapped_column(Integer,nullable=False)
    width:Mapped[int]=mapped_column(Integer,nullable=False)
    height:Mapped[int]=mapped_column(Integer,nullable=False)
    mime:Mapped[str]=mapped_column(String(32),nullable=False)
    relative_path:Mapped[str]=mapped_column(String(255),nullable=False)
    __table_args__=(ForeignKeyConstraint(['job_id','project_id'],['gpt_image_input_sets.job_id','gpt_image_input_sets.project_id'],ondelete='RESTRICT'),
        ForeignKeyConstraint(['media_id','project_id'],['production_media.id','production_media.project_id'],ondelete='RESTRICT'),
        UniqueConstraint('job_id','media_id'),CheckConstraint("typeof(position)='integer' AND position BETWEEN 0 AND 2 "
            "AND purpose IN ('appearance','composition','style') AND length(sha256)=64 AND typeof(byte_size)='integer' AND byte_size BETWEEN 1 AND 4194304 "
            "AND typeof(width)='integer' AND typeof(height)='integer' AND width BETWEEN 15 AND 4096 AND height BETWEEN 15 AND 4096 "
            "AND width*height<=16000000 AND mime IN ('image/png','image/jpeg')"),{'sqlite_with_rowid':False})
