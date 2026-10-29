"""Append-only receipts preserve exactly which DNA revision produced editable records."""
from sqlalchemy import DDL, JSON, ForeignKeyConstraint, Integer, String, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base
import app.models.video_dna  # noqa: F401


class DnaProductionImport(Base):
    """Keep original declarations and returned identities despite later editing or source drift."""
    __tablename__ = 'dna_production_imports'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    draft_id: Mapped[str] = mapped_column(String(64), nullable=False)
    draft_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    document_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[dict] = mapped_column(JSON, nullable=False)
    document: Mapped[dict] = mapped_column(JSON, nullable=False)
    request: Mapped[dict] = mapped_column(JSON, nullable=False)
    result: Mapped[dict] = mapped_column(JSON, nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(['draft_id', 'project_id'], ['video_dna_drafts.id', 'video_dna_drafts.project_id'], ondelete='RESTRICT'),
        UniqueConstraint('project_id', 'draft_id'), UniqueConstraint('project_id', 'request_key'),
    )


for action in ('UPDATE', 'DELETE'):
    event.listen(DnaProductionImport.__table__, 'after_create', DDL(
        f"CREATE TRIGGER IF NOT EXISTS dna_production_imports_{action.lower()}_immutable BEFORE {action} ON dna_production_imports BEGIN SELECT RAISE(ABORT, 'Immutable DNA import receipt'); END"
    ).execute_if(dialect='sqlite'))
