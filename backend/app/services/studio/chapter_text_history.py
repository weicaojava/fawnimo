"""Serialize chapter text writes and retain recoverable snapshots in the same transaction."""
import hashlib
import json
import uuid
from fastapi import HTTPException
from sqlalchemy import func, select, text
from app.models.chapter_text_history import ChapterTextRevision
from app.models.studio import Chapter


async def chapter_for_edit(db, chapter_id: str):
    """Acquire the local writer lock before reading the revision that a save will replace."""
    if db.bind.dialect.name == 'sqlite' and not db.in_transaction():
        await db.execute(text('BEGIN IMMEDIATE'))
    row = await db.scalar(select(Chapter).where(Chapter.id == chapter_id).with_for_update())
    if row is None:
        raise HTTPException(404, '章节不存在')
    return row


async def latest_revision(db, chapter_id: str) -> int:
    """Read the authoritative history counter while the caller holds its read/write scope."""
    return int(await db.scalar(select(func.max(ChapterTextRevision.revision)).where(ChapterTextRevision.chapter_id == chapter_id)) or 0)


def state(chapter, revision: int) -> dict:
    """Expose both current fields so clients do not reconstruct text from an old history row."""
    return {'chapter_id': chapter.id, 'current_revision': revision, 'raw_text': chapter.raw_text or '', 'condensed_text': chapter.condensed_text or ''}


async def append_revision(db, chapter, revision: int, origin: str, restored_from=None):
    """Publish exactly the current chapter text as a new immutable-in-API history entry."""
    payload = {'raw_text': chapter.raw_text or '', 'condensed_text': chapter.condensed_text or ''}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    row = ChapterTextRevision(id=str(uuid.uuid4()), chapter_id=chapter.id, revision=revision,
        raw_text=payload['raw_text'], condensed_text=payload['condensed_text'], content_hash=hashlib.sha256(encoded).hexdigest(), origin=origin, restored_from_revision=restored_from)
    db.add(row)
    await db.flush()
    return row


async def record_initial(db, chapter) -> None:
    """Record a new chapter once; pre-feature legacy chapters remain unmodified by reads."""
    if not await latest_revision(db, chapter.id):
        await append_revision(db, chapter, 1, 'initial')


async def save_fields(db, chapter, update: dict, *, expected_revision=None, origin='edit') -> dict:
    """Snapshot the legacy baseline and changed text atomically, without duplicate no-op edits."""
    revision = await latest_revision(db, chapter.id)
    if expected_revision is not None and revision != expected_revision:
        raise HTTPException(409, '章节文本已有新修订，请先刷新；当前编辑内容未被覆盖')
    if any(update.get(key) is None for key in update):
        raise HTTPException(422, '章节文本不能为null')
    if not revision:
        await append_revision(db, chapter, 1, 'initial')
        revision = 1
    changed = any(getattr(chapter, key) != value for key, value in update.items())
    if changed:
        for key, value in update.items():
            setattr(chapter, key, value)
        revision += 1
        await append_revision(db, chapter, revision, origin)
    return state(chapter, revision)


async def save_text(db, chapter_id: str, body) -> dict:
    """Save only the supplied fields at the displayed CAS revision."""
    chapter = await chapter_for_edit(db, chapter_id)
    updates = body.model_dump(exclude_unset=True, exclude={'expected_revision'})
    return await save_fields(db, chapter, updates, expected_revision=body.expected_revision)


async def list_history(db, chapter_id: str, page: int, page_size: int) -> dict:
    """Read current text and history from one consistent SQLite snapshot without creating records."""
    if db.bind.dialect.name == 'sqlite' and not db.in_transaction():
        await db.execute(text('BEGIN'))
    chapter = await db.get(Chapter, chapter_id)
    if chapter is None:
        raise HTTPException(404, '章节不存在')
    revision = await latest_revision(db, chapter_id)
    total = int(await db.scalar(select(func.count()).select_from(ChapterTextRevision).where(ChapterTextRevision.chapter_id == chapter_id)) or 0)
    rows = (await db.scalars(select(ChapterTextRevision).where(ChapterTextRevision.chapter_id == chapter_id)
        .order_by(ChapterTextRevision.revision.desc()).offset((page - 1) * page_size).limit(page_size))).all()
    return {**state(chapter, revision), 'items': rows, 'page': page, 'page_size': page_size, 'total': total}


async def restore_text(db, chapter_id: str, revision: int, body) -> dict:
    """Copy one owned historical snapshot into a new revision; never rewrite old history."""
    chapter = await chapter_for_edit(db, chapter_id)
    current = await latest_revision(db, chapter_id)
    if current != body.expected_revision:
        raise HTTPException(409, '章节文本已有新修订，请刷新后恢复；当前文本未被覆盖')
    target = await db.scalar(select(ChapterTextRevision).where(ChapterTextRevision.chapter_id == chapter_id, ChapterTextRevision.revision == revision))
    if target is None:
        raise HTTPException(404, '本章节的历史修订不存在')
    chapter.raw_text = target.raw_text
    chapter.condensed_text = target.condensed_text
    await append_revision(db, chapter, current + 1, 'restore', revision)
    return state(chapter, current + 1)
