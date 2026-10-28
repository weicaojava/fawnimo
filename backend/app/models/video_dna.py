"""Source-bound editable Video DNA and immutable portable hypothesis snapshots."""
from sqlalchemy import JSON,Integer,String,ForeignKeyConstraint,UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column
from app.core.db import Base
from app.models import reverse


class DnaDraft(Base):
    """Freeze server source identity while allowing CAS revisions of the human document."""
    __tablename__='video_dna_drafts'
    id: Mapped[str]=mapped_column(String(64),primary_key=True)
    project_id: Mapped[str]=mapped_column(String(64),nullable=False)
    analysis_id: Mapped[str]=mapped_column(String(64),nullable=False)
    source: Mapped[dict]=mapped_column(JSON,nullable=False)
    request_key: Mapped[str]=mapped_column(String(128),nullable=False)
    initial_hash: Mapped[str]=mapped_column(String(64),nullable=False)
    revision: Mapped[int]=mapped_column(Integer,nullable=False)
    document: Mapped[dict]=mapped_column(JSON,nullable=False)
    __table_args__=(ForeignKeyConstraint(['analysis_id','project_id'],['reverse_analyses.id','reverse_analyses.project_id'],ondelete='RESTRICT'),UniqueConstraint('id','project_id'),UniqueConstraint('project_id','request_key'))


class DnaSnapshot(Base):
    """Append-only safe integer content whose historical source never follows new cuts."""
    __tablename__='video_dna_snapshots'
    id: Mapped[str]=mapped_column(String(64),primary_key=True)
    project_id: Mapped[str]=mapped_column(String(64),nullable=False)
    draft_id: Mapped[str]=mapped_column(String(64),nullable=False)
    draft_revision: Mapped[int]=mapped_column(Integer,nullable=False)
    payload: Mapped[dict]=mapped_column(JSON,nullable=False)
    content_hash: Mapped[str]=mapped_column(String(64),nullable=False)
    __table_args__=(ForeignKeyConstraint(['draft_id','project_id'],['video_dna_drafts.id','video_dna_drafts.project_id'],ondelete='RESTRICT'),UniqueConstraint('draft_id','draft_revision'),UniqueConstraint('id','project_id'))
