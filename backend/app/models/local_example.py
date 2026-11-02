"""Reserved new-example targets and typed immutable receipts; no pending project or mutable progress JSON."""
from sqlalchemy import CheckConstraint, DDL, ForeignKey, ForeignKeyConstraint, Integer, String, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
import app.models.editing  # noqa: F401


class LocalExampleRequest(Base):
    """Reserve an absent target before rendering, with bounded integer attempts and a fenced owner."""
    __tablename__ = 'local_example_requests'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    chapter_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    submitted_by: Mapped[str] = mapped_column(String(128), nullable=False)
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    template_version: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_until: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(500), nullable=False, default='')
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (
        UniqueConstraint('submitted_by', 'request_key'), UniqueConstraint('id', 'project_id'),
        CheckConstraint("state IN ('running','ready','failedunknown')"),
        CheckConstraint("typeof(attempts)='integer' AND attempts BETWEEN 1 AND 3"),
        CheckConstraint("typeof(created_at)='integer' AND typeof(updated_at)='integer' AND updated_at>=created_at"),
        CheckConstraint("(state='running' AND lease_token IS NOT NULL AND length(lease_token)=64 AND lease_until IS NOT NULL AND typeof(lease_until)='integer') OR (state!='running' AND lease_token IS NULL AND lease_until IS NULL)"),
    )


class LocalExampleReady(Base):
    """Pin the installation to owned immutable script/plan and editable draft identities."""
    __tablename__ = 'local_example_ready'
    request_id: Mapped[str] = mapped_column(ForeignKey('local_example_requests.id', ondelete='RESTRICT'), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id', ondelete='RESTRICT'), nullable=False)
    chapter_id: Mapped[str] = mapped_column(ForeignKey('chapters.id', ondelete='RESTRICT'), nullable=False)
    script_snapshot_id: Mapped[str] = mapped_column(ForeignKey('script_preparation_snapshots.id', ondelete='RESTRICT'), nullable=False)
    plan_id: Mapped[str] = mapped_column(ForeignKey('shot_plans.id', ondelete='RESTRICT'), nullable=False)
    draft_id: Mapped[str] = mapped_column(ForeignKey('editing_drafts.id', ondelete='RESTRICT'), nullable=False)


class LocalExampleMedia(Base):
    """Retain fourteen scoped original-file identities without free-form JSON reference claims."""
    __tablename__ = 'local_example_media'
    request_id: Mapped[str] = mapped_column(ForeignKey('local_example_ready.request_id', ondelete='RESTRICT'), primary_key=True)
    clip_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(10), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    media_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    __table_args__ = (
        ForeignKeyConstraint(['media_id', 'project_id'], ['production_media.id', 'production_media.project_id'], ondelete='RESTRICT'),
        CheckConstraint("typeof(clip_index)='integer' AND clip_index BETWEEN 1 AND 7"),
        CheckConstraint("kind IN ('video','picture')"),
    )


class LocalExampleTake(Base):
    """Retain the seven original imported takes while allowing later selection and draft edits."""
    __tablename__ = 'local_example_takes'
    request_id: Mapped[str] = mapped_column(ForeignKey('local_example_ready.request_id', ondelete='RESTRICT'), primary_key=True)
    clip_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    take_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    __table_args__ = (
        ForeignKeyConstraint(['take_id', 'project_id'], ['production_takes.id', 'production_takes.project_id'], ondelete='RESTRICT'),
        CheckConstraint("typeof(clip_index)='integer' AND clip_index BETWEEN 1 AND 7"),
    )


def sqlite_trigger(table, name, sql):
    """Attach explicit SQLite guards to normal metadata creation and additive initialization."""
    event.listen(table, 'after_create', DDL(f'CREATE TRIGGER IF NOT EXISTS {name} {sql}').execute_if(dialect='sqlite'))


sqlite_trigger(LocalExampleRequest.__table__, 'local_example_initial_intent', '''
BEFORE INSERT ON local_example_requests WHEN NEW.state!='running' OR NEW.attempts!=1
OR NEW.lease_until<=CAST(strftime('%%s','now') AS INTEGER)
OR NEW.lease_until>CAST(strftime('%%s','now') AS INTEGER)+180
OR EXISTS(SELECT 1 FROM projects WHERE id=NEW.project_id)
OR EXISTS(SELECT 1 FROM chapters WHERE id=NEW.chapter_id)
BEGIN SELECT RAISE(ABORT,'Original example requires a new target and first owned attempt'); END''')
sqlite_trigger(LocalExampleRequest.__table__, 'local_example_identity_immutable', '''
BEFORE UPDATE OF id,project_id,chapter_id,submitted_by,request_key,request_hash,template_version,created_at
ON local_example_requests BEGIN SELECT RAISE(ABORT,'Immutable original example intent'); END''')
sqlite_trigger(LocalExampleRequest.__table__, 'local_example_attempt_fence', '''
BEFORE UPDATE ON local_example_requests WHEN NOT (
 (OLD.state='running' AND NEW.attempts=OLD.attempts AND (
   (NEW.state='running' AND NEW.lease_token IS OLD.lease_token AND NEW.lease_until>=OLD.lease_until
    AND OLD.lease_until>CAST(strftime('%%s','now') AS INTEGER)
    AND NEW.lease_until<=CAST(strftime('%%s','now') AS INTEGER)+180)
   OR NEW.state='failedunknown'
   OR (NEW.state='ready' AND OLD.lease_until>CAST(strftime('%%s','now') AS INTEGER))))
 OR (NEW.state='running' AND NEW.attempts=OLD.attempts+1 AND NEW.lease_token IS NOT OLD.lease_token
   AND (OLD.state='failedunknown' OR (OLD.state='running' AND OLD.lease_until<=CAST(strftime('%%s','now') AS INTEGER)))
   AND NEW.lease_until>CAST(strftime('%%s','now') AS INTEGER)
   AND NEW.lease_until<=CAST(strftime('%%s','now') AS INTEGER)+180))
BEGIN SELECT RAISE(ABORT,'Original example attempt ownership conflict'); END''')
sqlite_trigger(LocalExampleRequest.__table__, 'local_example_no_delete', '''
BEFORE DELETE ON local_example_requests BEGIN SELECT RAISE(ABORT,'Retained original example intent'); END''')
sqlite_trigger(LocalExampleRequest.__table__, 'local_example_no_replace', '''
BEFORE INSERT ON local_example_requests WHEN EXISTS(SELECT 1 FROM local_example_requests old
WHERE old.id=NEW.id OR old.project_id=NEW.project_id OR old.chapter_id=NEW.chapter_id
OR (old.submitted_by=NEW.submitted_by AND old.request_key=NEW.request_key))
BEGIN SELECT RAISE(ABORT,'Original example intent cannot be replaced'); END''')


# These guards establish installation scope, never human retention confirmation,
# generated-model provenance or the current contents of a later edited draft.
sqlite_trigger(LocalExampleReady.__table__, 'local_example_ready_context', '''
BEFORE INSERT ON local_example_ready WHEN NOT EXISTS (
 SELECT 1 FROM local_example_requests r JOIN chapters c ON c.id=NEW.chapter_id
 JOIN script_preparation_snapshots s ON s.id=NEW.script_snapshot_id
 JOIN script_preparation_drafts sd ON sd.id=s.draft_id
 JOIN shot_plans p ON p.id=NEW.plan_id JOIN shot_plan_drafts pd ON pd.id=p.draft_id
 JOIN editing_drafts d ON d.id=NEW.draft_id
 WHERE r.id=NEW.request_id AND r.state='running' AND r.lease_until>CAST(strftime('%%s','now') AS INTEGER)
 AND r.project_id=NEW.project_id AND r.chapter_id=NEW.chapter_id AND c.project_id=r.project_id
 AND s.project_id=r.project_id AND sd.project_id=r.project_id AND sd.chapter_id=r.chapter_id
 AND p.project_id=r.project_id AND pd.project_id=r.project_id AND pd.chapter_id=r.chapter_id
 AND json_extract(p.payload,'$.script_snapshot_id')=s.id AND json_extract(p.payload,'$.binding') IS NULL
 AND d.project_id=r.project_id AND d.chapter_id=r.chapter_id AND json_extract(d.document,'$.plan_id')=p.id)
BEGIN SELECT RAISE(ABORT,'Original example receipt context mismatch'); END''')
sqlite_trigger(LocalExampleMedia.__table__, 'local_example_media_context', '''
BEFORE INSERT ON local_example_media WHEN NOT EXISTS (
 SELECT 1 FROM local_example_ready ready JOIN local_example_requests r ON r.id=ready.request_id
 JOIN production_media m ON m.id=NEW.media_id JOIN files f ON f.id=m.id
 WHERE ready.request_id=NEW.request_id AND r.state='running'
 AND r.lease_until>CAST(strftime('%%s','now') AS INTEGER) AND NEW.project_id=ready.project_id
 AND m.project_id=ready.project_id
 AND m.mime=CASE NEW.kind WHEN 'video' THEN 'video/mp4' ELSE 'image/png' END)
BEGIN SELECT RAISE(ABORT,'Original example media context mismatch'); END''')
sqlite_trigger(LocalExampleTake.__table__, 'local_example_take_context', '''
BEFORE INSERT ON local_example_takes WHEN NOT EXISTS (
 SELECT 1 FROM local_example_ready ready JOIN local_example_requests r ON r.id=ready.request_id
 JOIN production_takes t ON t.id=NEW.take_id JOIN shots s ON s.id=t.shot_id
 JOIN editing_take_origins o ON o.take_id=t.id
 JOIN local_example_media m ON m.request_id=ready.request_id AND m.clip_index=NEW.clip_index AND m.kind='video'
 JOIN shot_plans p ON p.id=ready.plan_id, json_each(p.payload,'$.entries') entry
 WHERE ready.request_id=NEW.request_id AND r.state='running'
 AND r.lease_until>CAST(strftime('%%s','now') AS INTEGER)
 AND NEW.project_id=ready.project_id AND t.project_id=ready.project_id AND s.chapter_id=ready.chapter_id
 AND s."index"=NEW.clip_index AND t.media_id=m.media_id AND t.provenance='user_import'
 AND o.project_id=ready.project_id AND o.plan_id=ready.plan_id AND o.shot_id=s.id
 AND o.origin_kind='user_import' AND o.job_id IS NULL
 AND json_extract(entry.value,'$.shot_id')=s.id AND json_extract(entry.value,'$.entry_hash')=o.entry_hash)
BEGIN SELECT RAISE(ABORT,'Original example take context mismatch'); END''')
sqlite_trigger(LocalExampleRequest.__table__, 'local_example_ready_complete', '''
BEFORE UPDATE ON local_example_requests WHEN NEW.state='ready' AND (
 (SELECT count(*) FROM local_example_ready WHERE request_id=NEW.id)!=1
 OR (SELECT count(*) FROM local_example_media WHERE request_id=NEW.id)!=14
 OR (SELECT count(*) FROM local_example_takes WHERE request_id=NEW.id)!=7)
BEGIN SELECT RAISE(ABORT,'Original example installation is incomplete'); END''')


def retain_receipt(table, conflict):
    """Refuse updates, deletions and INSERT OR REPLACE for typed installation history."""
    name = table.name
    sqlite_trigger(table, name + '_no_update', f"BEFORE UPDATE ON {name} BEGIN SELECT RAISE(ABORT,'Immutable original example receipt'); END")
    sqlite_trigger(table, name + '_no_delete', f"BEFORE DELETE ON {name} BEGIN SELECT RAISE(ABORT,'Retained original example receipt'); END")
    sqlite_trigger(table, name + '_no_replace', f"BEFORE INSERT ON {name} WHEN EXISTS(SELECT 1 FROM {name} old WHERE {conflict}) BEGIN SELECT RAISE(ABORT,'Original example receipt cannot be replaced'); END")


retain_receipt(LocalExampleReady.__table__, 'old.request_id=NEW.request_id')
retain_receipt(LocalExampleMedia.__table__, '(old.request_id=NEW.request_id AND old.clip_index=NEW.clip_index AND old.kind=NEW.kind) OR old.media_id=NEW.media_id')
retain_receipt(LocalExampleTake.__table__, '(old.request_id=NEW.request_id AND old.clip_index=NEW.clip_index) OR old.take_id=NEW.take_id')
