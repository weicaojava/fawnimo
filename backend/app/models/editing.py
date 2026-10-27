"""Immutable editing provenance and chapter-scoped assembly revisions."""
from sqlalchemy import JSON, Integer, String, ForeignKey, ForeignKeyConstraint, UniqueConstraint, event, DDL
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base
from app.models import production, shot_plan


class TakeOrigin(Base):
    """Freeze trusted plan linkage and decoded source facts for one adopted take."""
    __tablename__='editing_take_origins'
    take_id: Mapped[str]=mapped_column(String(64),primary_key=True)
    project_id: Mapped[str]=mapped_column(String(64),nullable=False)
    job_id: Mapped[str|None]=mapped_column(ForeignKey('execution_jobs.id',ondelete='RESTRICT'),unique=True)
    plan_id: Mapped[str]=mapped_column(String(64),nullable=False)
    shot_id: Mapped[str]=mapped_column(ForeignKey('shots.id',ondelete='RESTRICT'),nullable=False)
    entry_hash: Mapped[str]=mapped_column(String(64),nullable=False)
    origin_kind: Mapped[str]=mapped_column(String(40),nullable=False)
    evidence: Mapped[dict]=mapped_column(JSON,nullable=False)
    __table_args__=(ForeignKeyConstraint(['take_id','project_id'],['production_takes.id','production_takes.project_id'],ondelete='RESTRICT'),ForeignKeyConstraint(['plan_id','project_id'],['shot_plans.id','shot_plans.project_id'],ondelete='RESTRICT'))


class SelectionCursor(Base):
    """Monotonic CAS revision for an actual shot's current selection."""
    __tablename__='editing_selection_cursors'
    shot_id: Mapped[str]=mapped_column(ForeignKey('shots.id',ondelete='RESTRICT'),primary_key=True)
    project_id: Mapped[str]=mapped_column(ForeignKey('projects.id',ondelete='RESTRICT'),nullable=False)
    revision: Mapped[int]=mapped_column(Integer,nullable=False)


class SelectionEvent(Base):
    """Append-only idempotent user intent; replay never changes current selection."""
    __tablename__='editing_selection_events'
    id: Mapped[str]=mapped_column(String(64),primary_key=True)
    project_id: Mapped[str]=mapped_column(ForeignKey('projects.id',ondelete='RESTRICT'),nullable=False)
    request_key: Mapped[str]=mapped_column(String(128),nullable=False)
    request_hash: Mapped[str]=mapped_column(String(64),nullable=False)
    shot_id: Mapped[str]=mapped_column(ForeignKey('shots.id',ondelete='RESTRICT'),nullable=False)
    take_id: Mapped[str]=mapped_column(String(64),nullable=False)
    revision: Mapped[int]=mapped_column(Integer,nullable=False)
    origin_kind: Mapped[str]=mapped_column(String(40),nullable=False)
    change_note: Mapped[str]=mapped_column(String(1000),nullable=False)
    __table_args__=(UniqueConstraint('project_id','request_key'),ForeignKeyConstraint(['take_id','project_id'],['production_takes.id','production_takes.project_id'],ondelete='RESTRICT'))


class EditDraft(Base):
    """One mutable CAS-controlled chapter assembly, with no mutable media pointers."""
    __tablename__='editing_drafts'
    id: Mapped[str]=mapped_column(String(64),primary_key=True)
    project_id: Mapped[str]=mapped_column(ForeignKey('projects.id',ondelete='RESTRICT'),nullable=False)
    chapter_id: Mapped[str]=mapped_column(String(64),nullable=False,unique=True)
    revision: Mapped[int]=mapped_column(Integer,nullable=False)
    document: Mapped[dict]=mapped_column(JSON,nullable=False)
    __table_args__=(UniqueConstraint('id','project_id'),ForeignKeyConstraint(['chapter_id','project_id'],['chapters.id','chapters.project_id'],ondelete='RESTRICT'))


class EditPublication(Base):
    """Complete immutable assembly and private source evidence for deterministic export."""
    __tablename__='editing_publications'
    id: Mapped[str]=mapped_column(String(64),primary_key=True)
    project_id: Mapped[str]=mapped_column(String(64),nullable=False)
    chapter_id: Mapped[str]=mapped_column(String(64),nullable=False)
    draft_id: Mapped[str]=mapped_column(String(64),nullable=False)
    draft_revision: Mapped[int]=mapped_column(Integer,nullable=False)
    payload: Mapped[dict]=mapped_column(JSON,nullable=False)
    content_hash: Mapped[str]=mapped_column(String(64),nullable=False)
    __table_args__=(UniqueConstraint('draft_id','draft_revision'),UniqueConstraint('id','project_id'),ForeignKeyConstraint(['draft_id','project_id'],['editing_drafts.id','editing_drafts.project_id'],ondelete='RESTRICT'),ForeignKeyConstraint(['chapter_id','project_id'],['chapters.id','chapters.project_id'],ondelete='RESTRICT'))


event.listen(EditDraft.__table__,'before_create',DDL('CREATE UNIQUE INDEX IF NOT EXISTS chapter_id_project_scope ON chapters(id,project_id)').execute_if(dialect='sqlite'))
