"""Project-owned reverse video analyses with fenced, restartable local processing."""
from sqlalchemy import JSON, CheckConstraint, Float, ForeignKey, ForeignKeyConstraint, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base


class ReverseAnalysis(Base):
    """Freeze input identity while keeping machine candidates and human cuts separate."""
    __tablename__='reverse_analyses'
    id: Mapped[str]=mapped_column(String(64),primary_key=True)
    project_id: Mapped[str]=mapped_column(ForeignKey('projects.id',ondelete='RESTRICT'),nullable=False)
    media_id: Mapped[str]=mapped_column(String(64),nullable=False)
    request_key: Mapped[str]=mapped_column(String(128),nullable=False)
    source_hash: Mapped[str]=mapped_column(String(64),nullable=False)
    state: Mapped[str]=mapped_column(String(16),nullable=False,default='queued')
    manifest: Mapped[dict | None]=mapped_column(JSON)
    confirmed_cuts: Mapped[list | None]=mapped_column(JSON)
    artifact_dir: Mapped[str | None]=mapped_column(String(255))
    revision: Mapped[int]=mapped_column(Integer,nullable=False,default=1)
    lease_token: Mapped[str | None]=mapped_column(String(64))
    lease_until: Mapped[float]=mapped_column(Float,nullable=False,default=0)
    reason: Mapped[str]=mapped_column(String(255),nullable=False,default='')
    __table_args__=(ForeignKeyConstraint(['media_id','project_id'],['production_media.id','production_media.project_id'],ondelete='RESTRICT'),UniqueConstraint('project_id','request_key'),UniqueConstraint('id','project_id'),CheckConstraint("state IN ('queued','analyzing','ready','failed','degraded')"),CheckConstraint('revision>=1'))
