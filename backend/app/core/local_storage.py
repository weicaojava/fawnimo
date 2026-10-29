"""Bounded, path-confined private object storage for the supported local workspace."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import tempfile
from pathlib import Path
from typing import Any, BinaryIO

from fastapi import HTTPException

from app.config import settings

_RESERVED = ".storage-metadata"
_DEVICE = re.compile(r"(?i)^(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\.|$)")


def storage_path(key: str) -> Path:
    """Resolve server logical keys inside one root, rejecting Windows/POSIX aliases."""
    if not isinstance(key, str) or not key or len(key) > 1024 or "\\" in key or ":" in key or "\x00" in key:
        raise HTTPException(400, "文件存储路径无效")
    parts = key.split("/")
    if any(part in {"", ".", "..", _RESERVED} or part.endswith((" ", ".")) or _DEVICE.match(part) for part in parts):
        raise HTTPException(400, "文件存储路径无效")
    root = Path(settings.local_storage_root).resolve()
    target = root.joinpath(*parts).resolve()
    if not target.is_relative_to(root) or target == root:
        raise HTTPException(400, "文件存储路径越界")
    return target


def _metadata_path(key: str) -> Path:
    """Keep internal metadata outside the logical key namespace."""
    root = Path(settings.local_storage_root).resolve()
    target = root / _RESERVED / (hashlib.sha256(key.encode("utf-8")).hexdigest() + ".json")
    if not target.resolve().is_relative_to(root):
        raise HTTPException(400, "文件存储路径越界")
    return target


def _bounded_bytes(data: bytes | BinaryIO) -> bytes:
    """Bound both byte bodies and streams before publishing any object."""
    limit = settings.local_storage_max_bytes
    if isinstance(data, (bytes, bytearray)):
        if len(data) > limit:
            raise HTTPException(413, "文件超过本地存储上限")
        return bytes(data)
    chunks: list[bytes] = []
    total = 0
    while chunk := data.read(min(1024 * 1024, limit + 1 - total)):
        if not isinstance(chunk, bytes):
            raise HTTPException(400, "上传内容必须是二进制文件")
        total += len(chunk)
        if total > limit:
            raise HTTPException(413, "文件超过本地存储上限")
        chunks.append(chunk)
    return b"".join(chunks)


def _atomic_write(target: Path, content: bytes) -> None:
    """Publish a complete private file without exposing a partial upload."""
    target.parent.mkdir(parents=True, exist_ok=True)
    root = Path(settings.local_storage_root).resolve()
    if not target.resolve().is_relative_to(root):
        raise HTTPException(400, "文件存储路径越界")
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".upload-", delete=False) as stream:
            temporary = stream.name
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def upload(key: str, data: bytes | BinaryIO, content_type: str | None) -> dict[str, Any]:
    """Store bounded bytes and metadata; HTTP URLs are assigned by the file-ID service."""
    target = storage_path(key)
    content = _bounded_bytes(data)
    if content_type and (len(content_type) > 256 or "\r" in content_type or "\n" in content_type):
        raise HTTPException(400, "文件类型无效")
    metadata = {"size": len(content), "content_type": content_type or mimetypes.guess_type(key)[0] or "application/octet-stream", "etag": hashlib.sha256(content).hexdigest()}
    _atomic_write(target, content)
    _atomic_write(_metadata_path(key), json.dumps(metadata).encode("utf-8"))
    return {"key": key, "url": "", **metadata}


def download(key: str) -> bytes:
    """Return existing bounded content or a clear missing/oversize error."""
    target = storage_path(key)
    if not target.is_file():
        raise HTTPException(404, "文件不存在或已移除")
    try:
        with target.open("rb") as stream:
            return _bounded_bytes(stream)
    except FileNotFoundError as exc:
        raise HTTPException(404, "文件不存在或已移除") from exc


def info(key: str) -> dict[str, Any]:
    """Calculate metadata from the actual stored bytes, retaining the supplied MIME type."""
    content = download(key)
    content_type = mimetypes.guess_type(key)[0] or "application/octet-stream"
    metadata = _metadata_path(key)
    if metadata.is_file():
        try:
            saved = json.loads(metadata.read_text("utf-8"))
            content_type = str(saved.get("content_type") or content_type)
        except (OSError, ValueError, TypeError):
            pass
    return {"key": key, "url": "", "size": len(content), "content_type": content_type, "etag": hashlib.sha256(content).hexdigest()}


def delete(key: str) -> None:
    """Delete only the requested existing local object and its private metadata."""
    target = storage_path(key)
    if not target.is_file():
        raise HTTPException(404, "文件不存在或已移除")
    try:
        target.unlink()
    except FileNotFoundError as exc:
        raise HTTPException(404, "文件不存在或已移除") from exc
    _metadata_path(key).unlink(missing_ok=True)


def list_objects(prefix: str = "") -> list[dict[str, Any]]:
    """List at most 1000 existing logical objects, excluding private metadata and staging files."""
    if prefix:
        storage_path(prefix.rstrip("/"))
    root = Path(settings.local_storage_root).resolve()
    if not root.is_dir():
        return []
    result = []
    for target in sorted(root.rglob("*")):
        relative = target.relative_to(root)
        if not target.is_file() or _RESERVED in relative.parts or target.name.startswith(".upload-"):
            continue
        key = relative.as_posix()
        if key.startswith(prefix):
            result.append(info(key))
            if len(result) >= 1000:
                break
    return result
