"""文件服务：封装文件上传、下载、列表、详情、更新与删除。"""

from __future__ import annotations

import os
import re
import uuid
import asyncio
import hashlib
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.utils import apply_keyword_filter, apply_order, paginate
from app.core import storage
from app.models.studio import FileItem, FileType
from app.models.production import ProductionMedia
from app.schemas.common import ApiResponse, PaginatedData, paginated_response
from app.schemas.studio import FileDetailRead, FileRead, FileUpdate, FileUsageRead, FileUsageWrite
from app.services.common import create_and_refresh, entity_not_found, flush_and_refresh, get_or_404, patch_model
from app.services.studio.file_usages import upsert_file_usage

FILE_ORDER_FIELDS = {"name", "created_at", "updated_at"}


def _detect_file_type(filename: str) -> FileType:
    _, ext = os.path.splitext(filename.lower())
    if ext in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return FileType.image
    if ext in {".mp4", ".mov", ".mkv", ".avi", ".webm"}:
        return FileType.video
    raise HTTPException(status_code=400, detail=f"不支持的文件类型: {ext or '未知后缀'}")


def _build_display_name(filename: str, name: str | None) -> str:
    if name:
        return name
    base, _ = os.path.splitext(filename)
    return base or filename


def _resolve_download_media_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    media_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
    }
    if ext in media_types:
        return media_types[ext]
    if ext in {".mkv", ".avi", ".webm"}:
        return f"video/{ext.lstrip('.')}"
    return "application/octet-stream"


async def list_files_paginated(
    db: AsyncSession,
    *,
    q: str | None,
    order: str | None,
    is_desc: bool,
    page: int,
    page_size: int,
) -> ApiResponse[PaginatedData[FileRead]]:
    """分页查询文件。"""
    stmt = select(FileItem)
    stmt = apply_keyword_filter(stmt, q=q, fields=[FileItem.name])
    stmt = apply_order(
        stmt,
        model=FileItem,
        order=order,
        is_desc=is_desc,
        allow_fields=FILE_ORDER_FIELDS,
        default="created_at",
    )
    items, total = await paginate(db, stmt=stmt, page=page, page_size=page_size)
    return paginated_response(
        [FileRead.model_validate(x) for x in items],
        page=page,
        page_size=page_size,
        total=total,
    )


async def get_file_detail(
    db: AsyncSession,
    *,
    file_id: str,
) -> FileDetailRead:
    """获取文件详情。"""
    stmt = select(FileItem).options(selectinload(FileItem.usages)).where(FileItem.id == file_id)
    res = await db.execute(stmt)
    obj = res.scalars().first()
    if obj is None:
        raise HTTPException(status_code=404, detail=entity_not_found("File"))
    usages = [FileUsageRead.model_validate(u) for u in (obj.usages or [])]
    base = FileRead.model_validate(obj)
    return FileDetailRead(**base.model_dump(), usages=usages)


async def update_file_meta(
    db: AsyncSession,
    *,
    file_id: str,
    body: FileUpdate,
) -> FileItem:
    """更新文件元信息，并按需写入 usage。"""
    obj = await get_or_404(db, FileItem, file_id, detail=entity_not_found("File"))
    data = body.model_dump(exclude_unset=True)
    usage_payload = data.pop("usage", None)
    patch_model(obj, data)
    if usage_payload is not None:
        u = FileUsageWrite.model_validate(usage_payload)
        await upsert_file_usage(
            db,
            file_id=file_id,
            project_id=u.project_id,
            chapter_id=u.chapter_id,
            shot_id=u.shot_id,
            usage_kind=u.usage_kind,
            source_ref=u.source_ref,
        )
    return await flush_and_refresh(db, obj)


async def upload_file(
    db: AsyncSession,
    *,
    file: UploadFile,
    name: str | None = None,
    project_id: str | None = None,
    chapter_id: str | None = None,
    shot_id: str | None = None,
    usage_kind: str | None = None,
    source_ref: str | None = None,
) -> FileItem:
    """上传文件到对象存储，并创建 FileItem 记录。"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="上传文件缺少文件名")

    file_type = _detect_file_type(file.filename)
    display_name = _build_display_name(file.filename, name)
    from app.services.studio.production_media import inspect_media, read_bounded, probe_media
    import asyncio
    content = await read_bounded(file)
    inspect_media(file.filename, content)
    await asyncio.to_thread(probe_media, file.filename, content)
    key = f"files/{uuid.uuid4()}{Path(file.filename).suffix.lower()}"
    info = await storage.upload_file(
        key=key,
        data=content,
        content_type=file.content_type,
        extra_args={"ACL": "public-read"},
    )

    file_id = str(uuid.uuid4())
    try:
        file_item = await create_and_refresh(
            db,
            FileItem(
                id=file_id,
                type=file_type,
                name=display_name,
                thumbnail=info.url or f"/api/v1/studio/files/{file_id}/download",
                tags=[],
                storage_key=key,
            ),
        )
        if project_id and usage_kind:
            await upsert_file_usage(
                db,
                file_id=file_item.id,
                project_id=project_id,
                chapter_id=chapter_id,
                shot_id=shot_id,
                usage_kind=usage_kind,
                source_ref=source_ref,
            )
        return file_item
    except Exception:
        # The generated key belongs solely to this failed upload, never an existing object.
        try:
            await storage.delete_file(key=key)
        except Exception:
            pass
        raise


async def read_file_bytes(db: AsyncSession, *, file_id: str) -> tuple[FileItem, bytes]:
    """Read local/S3 files and hash-check immutable production media through one entry point."""
    file_item = await get_or_404(db, FileItem, file_id, detail=entity_not_found("File"))
    if file_item.storage_key.startswith("local-asset-image/"):
        from app.services.asset_image_generation import read_adopted_file
        return file_item, await read_adopted_file(db, file_item)
    if file_item.storage_key.startswith("local-production/"):
        from app.services.studio.production_media import media_path
        from app.config import settings
        media = await db.get(ProductionMedia, file_id)
        if media is None or file_item.storage_key != "local-production/" + media.relative_path:
            raise HTTPException(409, "生产媒体记录与文件不匹配")
        target = media_path(media.relative_path)

        def read_verified() -> bytes:
            """Bound disk reads before checking the immutable size and content hash."""
            if not target.is_file():
                raise HTTPException(404, "文件不存在或已移除")
            try:
                with target.open("rb") as stream:
                    content = stream.read(settings.production_upload_limit + 1)
            except FileNotFoundError as exc:
                raise HTTPException(404, "文件不存在或已移除") from exc
            if len(content) > settings.production_upload_limit:
                raise HTTPException(413, "文件超过本地媒体上限")
            if len(content) != media.size or hashlib.sha256(content).hexdigest() != media.sha256:
                raise HTTPException(409, "媒体校验失败，请重新导入原文件")
            return content

        return file_item, await asyncio.to_thread(read_verified)
    return file_item, await storage.download_file(key=file_item.storage_key)


async def build_download_response(
    db: AsyncSession,
    *,
    file_id: str,
    range_header: str | None = None,
) -> StreamingResponse:
    """Serve real bounded bytes with HTTP ranges so media previews can seek and capture frames."""
    file_item, content = await read_file_bytes(db, file_id=file_id)

    filename = Path(file_item.storage_key).name or "download"
    media_type = _resolve_download_media_type(filename)
    content_disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    size = len(content)
    headers = {"Content-Disposition": content_disposition, "Accept-Ranges": "bytes"}
    status_code = 200
    if range_header:
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', range_header) if len(range_header) <= 128 else None
        if not match or not size or not any(match.groups()):
            raise HTTPException(416, '不支持的文件范围', headers={'Content-Range': f'bytes */{size}'})
        first, last = match.groups()
        if first:
            start, end = int(first), min(int(last), size - 1) if last else size - 1
        else:
            suffix = int(last)
            start, end = max(0, size - suffix), size - 1
            if not suffix:
                raise HTTPException(416, '文件范围为空', headers={'Content-Range': f'bytes */{size}'})
        if start >= size or start > end:
            raise HTTPException(416, '文件范围超出内容', headers={'Content-Range': f'bytes */{size}'})
        headers['Content-Range'] = f'bytes {start}-{end}/{size}'
        content = content[start:end + 1]
        status_code = 206
    headers['Content-Length'] = str(len(content))
    return StreamingResponse(
        iter([content]),
        media_type=media_type,
        status_code=status_code,
        headers=headers,
    )


async def get_storage_info(
    db: AsyncSession,
    *,
    file_id: str,
) -> dict[str, Any]:
    """读取对象存储信息。"""
    file_item = await get_or_404(db, FileItem, file_id, detail=entity_not_found("File"))
    if file_item.storage_key.startswith("local-asset-image/"):
        from app.services.asset_image_generation import read_adopted_file
        content = await read_adopted_file(db, file_item)
        return {"key": file_item.storage_key, "url": f"/api/v1/studio/files/{file_id}/download", "size": len(content), "content_type": _resolve_download_media_type(file_item.storage_key), "etag": hashlib.sha256(content).hexdigest()}
    if file_item.storage_key.startswith("local-production/"):
        _, content = await read_file_bytes(db, file_id=file_id)
        media = await db.get(ProductionMedia, file_id)
        assert media is not None
        return {"key": file_item.storage_key, "url": f"/api/v1/studio/files/{file_id}/download", "size": len(content), "content_type": media.mime, "etag": media.sha256}
    info = await storage.get_file_info(key=file_item.storage_key)
    return {
        "key": info.key,
        "url": info.url or f"/api/v1/studio/files/{file_id}/download",
        "size": info.size,
        "content_type": info.content_type,
        "etag": info.etag,
    }


async def delete_file(
    db: AsyncSession,
    *,
    file_id: str,
) -> None:
    """删除文件记录与对象存储中的内容；若记录不存在则静默返回。"""
    file_item = await db.get(FileItem, file_id)
    if file_item is None:
        return

    if file_item.storage_key.startswith(("local-production/", "local-asset-image/")) or await db.get(ProductionMedia, file_id) is not None:
        raise HTTPException(409, "生产媒体已固定，请从工程中管理引用；不能从文件列表删除")
    key = file_item.storage_key
    await db.delete(file_item)
    await db.flush()
    # Validate DB references before touching bytes; storage failures roll back the request.
    await storage.delete_file(key=key)
