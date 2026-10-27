"""Chapter draft, immutable compilation and durable per-shot execution provenance."""
from sqlalchemy import JSON,CheckConstraint,DDL,ForeignKey,ForeignKeyConstraint,Integer,String,UniqueConstraint,event
from sqlalchemy.orm import Mapped,mapped_column
from app.core.db import Base
import app.models.script_preparation  # noqa: F401
import app.models.execution  # noqa: F401


class ShotPlanDraft(Base):
    """CAS preparation document registered once per actual chapter."""
    __tablename__='shot_plan_drafts'
    id:Mapped[str]=mapped_column(String(64),primary_key=True)
    project_id:Mapped[str]=mapped_column(ForeignKey('projects.id',ondelete='RESTRICT'),nullable=False)
    chapter_id:Mapped[str]=mapped_column(String(64),nullable=False,unique=True)
    revision:Mapped[int]=mapped_column(Integer,nullable=False,default=1)
    document:Mapped[dict]=mapped_column(JSON,nullable=False)
    __table_args__=(ForeignKeyConstraint(['chapter_id','project_id'],['chapters.id','chapters.project_id'],ondelete='RESTRICT'),UniqueConstraint('id','project_id'),CheckConstraint('revision>=1'))


class ShotPlan(Base):
    """Append-only deterministic compilation; runtime eligibility is checked separately."""
    __tablename__='shot_plans'
    id:Mapped[str]=mapped_column(String(64),primary_key=True)
    project_id:Mapped[str]=mapped_column(String(64),nullable=False)
    draft_id:Mapped[str]=mapped_column(String(64),nullable=False)
    draft_revision:Mapped[int]=mapped_column(Integer,nullable=False)
    content_hash:Mapped[str]=mapped_column(String(64),nullable=False)
    payload:Mapped[dict]=mapped_column(JSON,nullable=False)
    __table_args__=(ForeignKeyConstraint(['draft_id','project_id'],['shot_plan_drafts.id','shot_plan_drafts.project_id'],ondelete='RESTRICT'),UniqueConstraint('draft_id','draft_revision'),UniqueConstraint('id','project_id'),CheckConstraint('draft_revision>=1'))


class PlanJobLink(Base):
    """One durable job belongs to precisely one immutable plan/shot entry."""
    __tablename__='shot_plan_job_links'
    job_id:Mapped[str]=mapped_column(String(64),primary_key=True)
    project_id:Mapped[str]=mapped_column(String(64),nullable=False)
    plan_id:Mapped[str]=mapped_column(String(64),nullable=False)
    shot_id:Mapped[str]=mapped_column(ForeignKey('shots.id',ondelete='RESTRICT'),nullable=False)
    entry_hash:Mapped[str]=mapped_column(String(64),nullable=False)
    __table_args__=(ForeignKeyConstraint(['job_id','project_id'],['execution_jobs.id','execution_jobs.project_id'],ondelete='RESTRICT'),ForeignKeyConstraint(['plan_id','project_id'],['shot_plans.id','shot_plans.project_id'],ondelete='RESTRICT'))


# Old additive entrypoints must prepare parent composite keys before child DDL.
event.listen(ShotPlanDraft.__table__,'before_create',DDL('CREATE UNIQUE INDEX IF NOT EXISTS chapter_id_project_scope ON chapters(id,project_id)').execute_if(dialect='sqlite'))
event.listen(PlanJobLink.__table__,'before_create',DDL('CREATE UNIQUE INDEX IF NOT EXISTS execution_id_project_scope ON execution_jobs(id,project_id)').execute_if(dialect='sqlite'))
