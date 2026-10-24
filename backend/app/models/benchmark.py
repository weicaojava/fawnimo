"""Project-scoped benchmark drafts and immutable evidence snapshots."""
from sqlalchemy import JSON,ForeignKey,ForeignKeyConstraint,Integer,String,UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column
from app.core.db import Base


class BenchmarkDraft(Base):
    """Mutable CAS document with permanently fixed project/source association."""
    __tablename__='benchmark_drafts'
    id:Mapped[str]=mapped_column(String(64),primary_key=True)
    project_id:Mapped[str]=mapped_column(ForeignKey('projects.id',ondelete='RESTRICT'),nullable=False)
    analysis_id:Mapped[str]=mapped_column(String(64),nullable=False)
    request_key:Mapped[str]=mapped_column(String(128),nullable=False)
    initial_hash:Mapped[str]=mapped_column(String(64),nullable=False)
    revision:Mapped[int]=mapped_column(Integer,nullable=False,default=1)
    document:Mapped[dict]=mapped_column(JSON,nullable=False)
    __table_args__=(ForeignKeyConstraint(['analysis_id','project_id'],['reverse_analyses.id','reverse_analyses.project_id'],ondelete='RESTRICT'),UniqueConstraint('project_id','request_key'),UniqueConstraint('id','project_id'))


class BenchmarkSnapshot(Base):
    """Canonical evidence bytes are appended once per draft revision."""
    __tablename__='benchmark_snapshots'
    id:Mapped[str]=mapped_column(String(64),primary_key=True)
    project_id:Mapped[str]=mapped_column(String(64),nullable=False)
    draft_id:Mapped[str]=mapped_column(String(64),nullable=False)
    draft_revision:Mapped[int]=mapped_column(Integer,nullable=False)
    content_hash:Mapped[str]=mapped_column(String(64),nullable=False)
    payload:Mapped[dict]=mapped_column(JSON,nullable=False)
    __table_args__=(ForeignKeyConstraint(['draft_id','project_id'],['benchmark_drafts.id','benchmark_drafts.project_id'],ondelete='RESTRICT'),UniqueConstraint('draft_id','draft_revision'))
