"""Project-registered manual scripts and immutable chapter preparation snapshots."""
from sqlalchemy import JSON,CheckConstraint,ForeignKey,ForeignKeyConstraint,Integer,String,UniqueConstraint,Index,DDL,event
from sqlalchemy.orm import Mapped,mapped_column
from app.core.db import Base
from app.models.studio import Chapter

# New databases create this parent composite key with their normal metadata.
if not any(index.name=='chapter_id_project_scope' for index in Chapter.__table__.indexes):
    Index('chapter_id_project_scope',Chapter.id,Chapter.project_id,unique=True)


class ScriptDraft(Base):
    """One permanent chapter registration with CAS document revision."""
    __tablename__='script_preparation_drafts'
    id:Mapped[str]=mapped_column(String(64),primary_key=True)
    project_id:Mapped[str]=mapped_column(ForeignKey('projects.id',ondelete='RESTRICT'),nullable=False)
    chapter_id:Mapped[str]=mapped_column(String(64),nullable=False,unique=True)
    revision:Mapped[int]=mapped_column(Integer,nullable=False,default=1)
    document:Mapped[dict]=mapped_column(JSON,nullable=False)
    __table_args__=(ForeignKeyConstraint(['chapter_id','project_id'],['chapters.id','chapters.project_id'],ondelete='RESTRICT'),UniqueConstraint('id','project_id'),CheckConstraint('revision>=1'))


class ScriptPublication(Base):
    """An append-only document, fixed requirement denominator and actual facts."""
    __tablename__='script_preparation_snapshots'
    id:Mapped[str]=mapped_column(String(64),primary_key=True)
    project_id:Mapped[str]=mapped_column(String(64),nullable=False)
    draft_id:Mapped[str]=mapped_column(String(64),nullable=False)
    draft_revision:Mapped[int]=mapped_column(Integer,nullable=False)
    content_hash:Mapped[str]=mapped_column(String(64),nullable=False)
    payload:Mapped[dict]=mapped_column(JSON,nullable=False)
    __table_args__=(ForeignKeyConstraint(['draft_id','project_id'],['script_preparation_drafts.id','script_preparation_drafts.project_id'],ondelete='RESTRICT'),UniqueConstraint('draft_id','draft_revision'),CheckConstraint('draft_revision>=1'))


# Any additive migrator may call metadata.create_all against an old Chapter
# table. Its composite parent key must exist before this new child is created,
# even when init_db or the dedicated script migrator is not the entry point.
event.listen(ScriptDraft.__table__,'before_create',DDL('CREATE UNIQUE INDEX IF NOT EXISTS chapter_id_project_scope ON chapters(id,project_id)').execute_if(dialect='sqlite'))
