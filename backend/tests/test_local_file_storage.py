"""Verify real private file bytes, confinement and API persistence without S3 or model calls."""
from __future__ import annotations

import asyncio
import hashlib
import io
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.core import storage
from app.core.db import Base
from app.dependencies import get_db
from app.main import app
from app.models.studio import FileItem, FileType, Project, ProjectStyle, ProjectVisualStyle
from app.models.production import ProductionMedia


@pytest.fixture
def local_files(tmp_path, monkeypatch):
    """Confine each test's backend to its own private temporary directory."""
    root = tmp_path / "files"
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "local_storage_root", str(root))
    monkeypatch.setattr(settings, "local_storage_max_bytes", 1024 * 1024)
    return root


@pytest.mark.asyncio
async def test_local_roundtrip_metadata_and_delete(local_files):
    content = b"actual private object bytes"
    key = "files/owned.txt"
    uploaded = await storage.upload_file(key=key, data=io.BytesIO(content), content_type="text/plain")
    assert uploaded.size == len(content)
    assert await storage.download_file(key=key) == content
    stored = await storage.get_file_info(key=key)
    assert stored.content_type == "text/plain"
    assert stored.etag == hashlib.sha256(content).hexdigest()
    assert [item.key for item in await storage.list_files(prefix="files/")] == [key]
    await storage.delete_file(key=key)
    assert not (local_files / key).exists()
    with pytest.raises(HTTPException) as error:
        await storage.download_file(key=key)
    assert error.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["../escape", "/absolute", "a/../b", "a//b", "C:/escape", "C:\\escape", "a\\b", "a/./b", "CON.txt", "a/NUL", ".storage-metadata/a", "trailing. ", "file:stream", "nul\x00key"])
async def test_local_storage_rejects_ambiguous_paths(local_files, key):
    with pytest.raises(HTTPException) as error:
        await storage.upload_file(key=key, data=b"not published")
    assert error.value.status_code == 400
    assert not local_files.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_oversize_upload_does_not_publish_bytes(local_files, monkeypatch, stream):
    monkeypatch.setattr(settings, "local_storage_max_bytes", 8)
    body = io.BytesIO(b"123456789") if stream else b"123456789"
    with pytest.raises(HTTPException) as error:
        await storage.upload_file(key="files/large.bin", data=body)
    assert error.value.status_code == 413
    assert not (local_files / "files/large.bin").exists()


@pytest.mark.asyncio
async def test_symlink_escape_is_rejected(local_files, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    local_files.mkdir()
    try:
        (local_files / "alias").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("This Windows account cannot create symbolic links")
    with pytest.raises(HTTPException) as error:
        await storage.upload_file(key="alias/escape.bin", data=b"private")
    assert error.value.status_code == 400
    assert not (outside / "escape.bin").exists()


@pytest.mark.asyncio
async def test_auto_retains_configured_s3(monkeypatch):
    calls = []

    class FakeS3:
        """An in-process protocol witness; never contacts a cloud account."""
        def put_object(self, **kwargs):
            calls.append(kwargs)
            return {"ETag": "protocol-etag"}

    monkeypatch.setattr(settings, "storage_backend", "auto")
    monkeypatch.setattr(settings, "s3_bucket_name", "test-bucket")
    monkeypatch.setattr(settings, "s3_base_path", "")
    monkeypatch.setattr(storage, "_build_s3_client", lambda: FakeS3())
    result = await storage.upload_file(key="files/a.bin", data=b"abc")
    assert result.etag == "protocol-etag"
    assert calls == [{"Bucket": "test-bucket", "Key": "files/a.bin", "Body": b"abc"}]


@pytest.fixture
def isolated_api(tmp_path, monkeypatch, local_files):
    """Exercise actual routes against an independent database and binary storage root."""
    database = tmp_path / "api.db"
    media_root = tmp_path / "production-media"
    media_root.mkdir()
    monkeypatch.setattr(settings, "production_media_root", str(media_root))
    monkeypatch.setattr(settings, "production_probe_executable", str(Path(__file__).resolve().parents[4] / "ffmpeg.exe"))
    engine = create_async_engine("sqlite+aiosqlite:///" + database.as_posix(), poolclass=NullPool)

    @event.listens_for(engine.sync_engine, "connect")
    def foreign_keys(connection, _record):
        """Enable the same reference protection as the supported SQLite runtime."""
        connection.execute("PRAGMA foreign_keys=ON")

    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def prepare():
        """Initialize only this test's tables, leaving every running workspace untouched."""
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def override_db():
        """Commit real accepted writes and roll back rejected API requests."""
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    asyncio.run(prepare())
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = override_db
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client, factory, local_files, media_root
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        asyncio.run(engine.dispose())


def test_real_image_upload_preview_info_and_delete_without_s3(isolated_api):
    client, _factory, root, _media_root = isolated_api
    body = io.BytesIO()
    Image.new("RGB", (32, 24), (25, 100, 180)).save(body, format="PNG")
    content = body.getvalue()
    uploaded = client.post("/api/v1/studio/files/upload", files={"file": ("真实图片.png", content, "image/png")})
    assert uploaded.status_code == 201, uploaded.text
    data = uploaded.json()["data"]
    assert data["thumbnail"] == f"/api/v1/studio/files/{data['id']}/download"
    downloaded = client.get(data["thumbnail"])
    assert downloaded.status_code == 200
    assert downloaded.content == content
    assert downloaded.headers["content-type"].startswith("image/png")
    info = client.get(f"/api/v1/studio/files/{data['id']}/storage-info")
    assert info.status_code == 200
    assert info.json()["data"]["size"] == len(content)
    assert info.json()["data"]["etag"] == hashlib.sha256(content).hexdigest()
    assert client.delete(f"/api/v1/studio/files/{data['id']}").status_code == 200
    assert client.get(data["thumbnail"]).status_code == 404
    assert not list((root / "files").glob("*.png"))


def test_production_media_download_hash_and_delete_protection(isolated_api):
    client, factory, _root, media_root = isolated_api
    content = b"bounded actual production bytes"
    relative = "project/actual.mp4"
    target = media_root / relative
    target.parent.mkdir()
    target.write_bytes(content)
    identity = "audit-immutable-file"

    async def seed():
        """Build owned production metadata in this test database only."""
        async with factory() as session:
            session.add(Project(id="local-file-project", name="Local file project", style=ProjectStyle.real_people_city, visual_style=ProjectVisualStyle.live_action))
            session.add(FileItem(id=identity, type=FileType.video, name="actual.mp4", thumbnail="", tags=[], storage_key="local-production/" + relative))
            await session.flush()
            session.add(ProductionMedia(id=identity, project_id="local-file-project", name="actual.mp4", mime="video/mp4", sha256=hashlib.sha256(content).hexdigest(), size=len(content), relative_path=relative))
            await session.commit()

    asyncio.run(seed())
    route = f"/api/v1/studio/files/{identity}"
    downloaded = client.get(route + "/download")
    assert downloaded.status_code == 200
    assert downloaded.content == content
    assert client.get(route + "/storage-info").json()["data"]["size"] == len(content)
    assert client.delete(route).status_code == 409
    assert target.read_bytes() == content
    target.write_bytes(b"changed")
    assert client.get(route + "/download").status_code == 409
    target.unlink()
    assert client.get(route + "/download").status_code == 404


def test_rejected_scope_upload_rolls_back_owned_bytes(isolated_api):
    client, _factory, root, _media_root = isolated_api
    body = io.BytesIO()
    Image.new("RGB", (16, 16), (12, 40, 80)).save(body, format="PNG")
    response = client.post(
        "/api/v1/studio/files/upload",
        files={"file": ("image.png", body.getvalue(), "image/png")},
        data={"project_id": "missing-project", "usage_kind": "upload"},
    )
    assert response.status_code == 409
    assert not list((root / "files").glob("*.png"))
    assert not list((root / ".storage-metadata").glob("*.json"))
    assert client.get("/api/v1/studio/files").json()["data"]["pagination"]["total"] == 0


def test_missing_local_bytes_do_not_report_delete_success(isolated_api):
    client, factory, root, _media_root = isolated_api
    identity = "missing-local-bytes"

    async def seed_missing():
        """Keep a metadata-only orphan in this test database to exercise honest error reporting."""
        async with factory() as session:
            session.add(FileItem(id=identity, type=FileType.image, name="missing.png", thumbnail="", tags=[], storage_key="files/missing.png"))
            await session.commit()

    asyncio.run(seed_missing())
    route = f"/api/v1/studio/files/{identity}"
    assert client.get(route + "/download").status_code == 404
    assert client.get(route + "/storage-info").status_code == 404
    assert client.delete(route).status_code == 404
    assert client.get(route).status_code == 200
    assert not root.exists()
