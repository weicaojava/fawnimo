"""Chapter CRUD（从 projects.py 拆分）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.utils import apply_keyword_filter, apply_order, paginate
from app.dependencies import get_db
from app.models.studio import Chapter, Project, Shot
from app.schemas.common import ApiResponse, PaginatedData, created_response, empty_response, paginated_response, success_response
from app.services.common import (
    create_and_refresh,
    delete_if_exists,
    entity_already_exists,
    entity_not_found,
    ensure_not_exists,
    flush_and_refresh,
    get_or_404,
    patch_model,
    require_entity,
)
from app.schemas.studio.projects import ChapterCreate, ChapterRead, ChapterUpdate
from app.schemas.studio.chapter_text_history import ChapterTextHistoryRead, ChapterTextRestore, ChapterTextSave, ChapterTextState
from app.services.studio import chapter_text_history

router = APIRouter()

CHAPTER_ORDER_FIELDS = {"index", "title", "created_at", "updated_at", "storyboard_count", "status"}


@router.get(
    "",
    response_model=ApiResponse[PaginatedData[ChapterRead]],
    summary="章节列表（分页）",
)
async def list_chapters(
    db: AsyncSession = Depends(get_db, scope='function'),
    project_id: str | None = Query(None, description="按项目过滤"),
    q: str | None = Query(None, description="关键字，过滤 title/summary"),
    order: str | None = Query(None, description="排序字段"),
    is_desc: bool = Query(False, description="是否倒序"),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
) -> ApiResponse[PaginatedData[ChapterRead]]:
    stmt = select(Chapter)
    if project_id:
        stmt = stmt.where(Chapter.project_id == project_id)
    stmt = apply_keyword_filter(stmt, q=q, fields=[Chapter.title, Chapter.summary])
    stmt = apply_order(
        stmt,
        model=Chapter,
        order=order,
        is_desc=is_desc,
        allow_fields=CHAPTER_ORDER_FIELDS,
        default="index",
    )
    items, total = await paginate(db, stmt=stmt, page=page, page_size=page_size)

    chapter_ids = [c.id for c in items]
    shot_count_by_chapter: dict[str, int] = {}
    if chapter_ids:
        count_stmt = (
            select(Shot.chapter_id, func.count(Shot.id))
            .where(Shot.chapter_id.in_(chapter_ids))
            .group_by(Shot.chapter_id)
        )
        res = await db.execute(count_stmt)
        shot_count_by_chapter = {str(ch_id): int(cnt) for ch_id, cnt in res.all()}

    return paginated_response(
        [
            ChapterRead.model_validate(x).model_copy(update={"shot_count": shot_count_by_chapter.get(x.id, 0)})
            for x in items
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post(
    "",
    response_model=ApiResponse[ChapterRead],
    status_code=status.HTTP_201_CREATED,
    summary="创建章节",
)
async def create_chapter(
    body: ChapterCreate,
    db: AsyncSession = Depends(get_db, scope='function'),
) -> ApiResponse[ChapterRead]:
    await ensure_not_exists(
        db,
        Chapter,
        body.id,
        detail=entity_already_exists("Chapter"),
    )
    await require_entity(
        db,
        Project,
        body.project_id,
        detail=entity_not_found("Project"),
        status_code=400,
    )
    obj = await create_and_refresh(db, Chapter(**body.model_dump()))
    await chapter_text_history.record_initial(db, obj)
    return created_response(ChapterRead.model_validate(obj))


@router.get(
    "/{chapter_id}",
    response_model=ApiResponse[ChapterRead],
    summary="获取章节",
)
async def get_chapter(
    chapter_id: str,
    db: AsyncSession = Depends(get_db, scope='function'),
) -> ApiResponse[ChapterRead]:
    obj = await get_or_404(db, Chapter, chapter_id, detail=entity_not_found("Chapter"))
    count_stmt = select(func.count(Shot.id)).where(Shot.chapter_id == chapter_id)
    res = await db.execute(count_stmt)
    shot_count = int(res.scalar() or 0)
    return success_response(ChapterRead.model_validate(obj).model_copy(update={"shot_count": shot_count}))


@router.patch(
    "/{chapter_id}",
    response_model=ApiResponse[ChapterRead],
    summary="更新章节",
)
async def update_chapter(
    chapter_id: str,
    body: ChapterUpdate,
    db: AsyncSession = Depends(get_db, scope='function'),
) -> ApiResponse[ChapterRead]:
    obj = await chapter_text_history.chapter_for_edit(db, chapter_id)
    update = body.model_dump(exclude_unset=True)
    if "project_id" in update:
        await require_entity(
            db,
            Project,
            update["project_id"],
            detail=entity_not_found("Project"),
            status_code=400,
        )
    text_update = {key: update.pop(key) for key in ('raw_text', 'condensed_text') if key in update}
    if text_update:
        await chapter_text_history.save_fields(db, obj, text_update, origin='edit')
    patch_model(obj, update)
    await flush_and_refresh(db, obj)
    return success_response(ChapterRead.model_validate(obj))


@router.get('/{chapter_id}/text-history', response_model=ApiResponse[ChapterTextHistoryRead], summary='读取章节真实文本历史')
async def list_chapter_text_history(chapter_id: str, db: AsyncSession = Depends(get_db, scope='function'), page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=30)):
    """Return only persisted revisions; current legacy text is not presented as invented history."""
    return success_response(await chapter_text_history.list_history(db, chapter_id, page, page_size))


@router.post('/{chapter_id}/text-history', response_model=ApiResponse[ChapterTextState], summary='保存章节文本新修订')
async def save_chapter_text_revision(chapter_id: str, body: ChapterTextSave, db: AsyncSession = Depends(get_db, scope='function')):
    """Save at the visible revision, preserving both history and unsaved conflict recovery."""
    return success_response(await chapter_text_history.save_text(db, chapter_id, body))


@router.post('/{chapter_id}/text-history/{revision}/restore', response_model=ApiResponse[ChapterTextState], summary='恢复章节历史为新修订')
async def restore_chapter_text_revision(chapter_id: str, revision: int, body: ChapterTextRestore, db: AsyncSession = Depends(get_db, scope='function')):
    """Restore a revision from this chapter without deleting subsequent history."""
    return success_response(await chapter_text_history.restore_text(db, chapter_id, revision, body))


@router.delete(
    "/{chapter_id}",
    response_model=ApiResponse[None],
    summary="删除章节",
)
async def delete_chapter(
    chapter_id: str,
    db: AsyncSession = Depends(get_db, scope='function'),
) -> ApiResponse[None]:
    await delete_if_exists(db, Chapter, chapter_id)
    return empty_response()
