"""Chapter structural CAS and immutable original-graph receipts, separate from source evidence."""
from sqlalchemy import JSON, DDL, ForeignKey, Integer, String, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base


class ShotGraphCursor(Base):
    """Increment once per accepted structure command; legacy writes are also caught by the graph hash."""
    __tablename__ = 'shot_graph_cursors'
    chapter_id: Mapped[str] = mapped_column(ForeignKey('chapters.id', ondelete='RESTRICT'), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id', ondelete='RESTRICT'), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)


class ShotGraphReceipt(Base):
    """Preserve exact pre-edit graph data; string identities intentionally survive source-row removal."""
    __tablename__ = 'shot_graph_receipts'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id', ondelete='RESTRICT'), nullable=False)
    chapter_id: Mapped[str] = mapped_column(ForeignKey('chapters.id', ondelete='RESTRICT'), nullable=False)
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    source_shot_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    result_shot_id: Mapped[str | None] = mapped_column(String(64))
    before: Mapped[dict] = mapped_column(JSON, nullable=False)
    after_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (UniqueConstraint('project_id', 'request_key'), UniqueConstraint('chapter_id', 'revision'))


for action in ('UPDATE', 'DELETE'):
    event.listen(ShotGraphReceipt.__table__, 'after_create', DDL(
        f"CREATE TRIGGER IF NOT EXISTS shot_graph_receipts_{action.lower()}_immutable BEFORE {action} ON shot_graph_receipts BEGIN SELECT RAISE(ABORT, 'Immutable shot graph receipt'); END"
    ).execute_if(dialect='sqlite'))


SQLITE_GRAPH_TRIGGERS = {
    'shot_graph_cursor_insert_scope': "CREATE TRIGGER IF NOT EXISTS shot_graph_cursor_insert_scope BEFORE INSERT ON shot_graph_cursors WHEN NEW.revision!=1 OR NOT EXISTS(SELECT 1 FROM chapters WHERE id=NEW.chapter_id AND project_id=NEW.project_id) BEGIN SELECT RAISE(ABORT, 'Shot graph chapter scope'); END",
    'shot_graph_cursor_update_cas': "CREATE TRIGGER IF NOT EXISTS shot_graph_cursor_update_cas BEFORE UPDATE ON shot_graph_cursors WHEN NEW.project_id IS NOT OLD.project_id OR NEW.chapter_id IS NOT OLD.chapter_id OR NEW.revision!=OLD.revision+1 BEGIN SELECT RAISE(ABORT, 'Shot graph revision CAS'); END",
    'shot_graph_receipt_insert_scope': "CREATE TRIGGER IF NOT EXISTS shot_graph_receipt_insert_scope BEFORE INSERT ON shot_graph_receipts WHEN NEW.revision<1 OR NEW.action NOT IN ('reorder','insert','merge') OR json_extract(NEW.before,'$.project_id') IS NOT NEW.project_id OR json_extract(NEW.before,'$.chapter_id') IS NOT NEW.chapter_id OR NOT EXISTS(SELECT 1 FROM shot_graph_cursors c JOIN chapters h ON h.id=c.chapter_id AND h.project_id=c.project_id WHERE c.chapter_id=NEW.chapter_id AND c.project_id=NEW.project_id AND c.revision=NEW.revision) BEGIN SELECT RAISE(ABORT, 'Shot graph receipt scope'); END",
}

for name, statement in SQLITE_GRAPH_TRIGGERS.items():
    table = ShotGraphCursor.__table__ if name.startswith('shot_graph_cursor_') else ShotGraphReceipt.__table__
    event.listen(table, 'after_create', DDL(statement).execute_if(dialect='sqlite'))
