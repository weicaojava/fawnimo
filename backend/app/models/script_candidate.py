"""Immutable source identity for text-run proposals, separate from human-owned chapter fields."""
from sqlalchemy import DDL,ForeignKey,ForeignKeyConstraint,Integer,JSON,String,UniqueConstraint,event
from sqlalchemy.orm import Mapped,mapped_column
from app.core.db import Base
import app.models.agent_execution  # noqa: F401
import app.models.script_preparation  # noqa: F401


class ScriptCandidate(Base):
    """One common-fee text run proposes a change; only an explicit decision can adopt it."""
    __tablename__='script_candidates'
    id:Mapped[str]=mapped_column(ForeignKey('agent_execution_runs.id',ondelete='RESTRICT'),primary_key=True)
    project_id:Mapped[str]=mapped_column(String(64),nullable=False)
    chapter_id:Mapped[str]=mapped_column(String(64),nullable=False)
    shot_id:Mapped[str|None]=mapped_column(ForeignKey('shots.id',ondelete='RESTRICT'))
    operation:Mapped[str]=mapped_column(String(32),nullable=False)
    source_field:Mapped[str]=mapped_column(String(32),nullable=False)
    source_revision:Mapped[int]=mapped_column(Integer,nullable=False)
    source_hash:Mapped[str]=mapped_column(String(64),nullable=False)
    source_snapshot:Mapped[dict]=mapped_column(JSON,nullable=False)
    decision:Mapped[str]=mapped_column(String(32),nullable=False,default='pending')
    adopted_output_hash:Mapped[str|None]=mapped_column(String(64))
    __table_args__=(ForeignKeyConstraint(['chapter_id','project_id'],['chapters.id','chapters.project_id'],ondelete='RESTRICT'),UniqueConstraint('id','project_id'))


event.listen(ScriptCandidate.__table__,'before_create',DDL(
    'CREATE UNIQUE INDEX IF NOT EXISTS chapter_id_project_scope ON chapters(id,project_id)'
).execute_if(dialect='sqlite'))
