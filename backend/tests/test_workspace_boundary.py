"""Exercise real SQLite ownership constraints and the private worker HTTP boundary."""
from contextlib import closing
import hashlib
import os
import sqlite3

import httpx
import pytest

from app.core.workspace_boundary import (
    READINESS_PATH, TenantBoundaryMiddleware, assert_workspace_boundary,
    preflight_workspace_boundary, prepare_workspace_boundary,
)
from run_tenant_worker import configure_worker


SPACE_A = "ws_" + "a" * 32
SPACE_B = "ws_" + "b" * 32
TOKEN = "private-worker-token-" + "a" * 40


def empty_database(path):
    """Create business and version tables like a newly migrated private workspace."""
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE projects(id TEXT PRIMARY KEY,title TEXT NOT NULL)")
        connection.execute("CREATE TABLE production_schema(version INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO production_schema VALUES(1)")
        connection.execute("CREATE TABLE transition_export_slot(id INTEGER PRIMARY KEY,lease_until REAL)")
        connection.execute("INSERT INTO transition_export_slot VALUES(1,0)")
        connection.commit()


def test_new_database_fixed_scope_and_reentrant_migration(tmp_path):
    """Raw SQL, default ownership, and restarts all retain the same saved user work."""
    database = tmp_path / "studio.db"
    empty_database(database)
    preflight_workspace_boundary(database, SPACE_A)
    prepare_workspace_boundary(database, SPACE_A, allow_initialize=True)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("INSERT INTO projects(id,title) VALUES('saved','Original title')")
        connection.commit()
    prepare_workspace_boundary(database, SPACE_A)
    assert_workspace_boundary(database, SPACE_A)
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT title,workspace_id FROM projects").fetchall() == [("Original title", SPACE_A)]


def test_existing_local_data_never_automatically_claimed(tmp_path):
    """An unmarked legacy database is rejected without modifying any of its bytes."""
    database = tmp_path / "studio.db"
    empty_database(database)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("INSERT INTO projects VALUES('private','Protected original')")
        connection.commit()
    digest = hashlib.sha256(database.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="existing business data"):
        preflight_workspace_boundary(database, SPACE_A)
    with pytest.raises(ValueError, match="existing business data"):
        prepare_workspace_boundary(database, SPACE_A, allow_initialize=True)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == digest


def test_database_cannot_be_rebound_or_relabelled(tmp_path):
    """A copied tenant database cannot start under another ID or update its own marker."""
    database = tmp_path / "studio.db"
    empty_database(database)
    prepare_workspace_boundary(database, SPACE_A, allow_initialize=True)
    for operation in (preflight_workspace_boundary, assert_workspace_boundary, prepare_workspace_boundary):
        with pytest.raises(ValueError, match="another identity"):
            operation(database, SPACE_B)
    with closing(sqlite3.connect(database)) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE workspace_identity SET workspace_id=?", (SPACE_B,))
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM workspace_identity")
    assert_workspace_boundary(database, SPACE_A)


@pytest.mark.parametrize("statement,values", [
    ("INSERT INTO projects(id,title,workspace_id) VALUES('bad','Foreign',?)", (SPACE_B,)),
    ("INSERT INTO projects(id,title,workspace_id) VALUES('bad','Null',?)", (None,)),
    ("UPDATE projects SET workspace_id=? WHERE id='saved'", (SPACE_B,)),
    ("UPDATE projects SET workspace_id=? WHERE id='saved'", (None,)),
])
def test_direct_sql_cannot_write_a_foreign_or_null_owner(tmp_path, statement, values):
    """Independent sessions and raw statements must obey the same physical owner."""
    database = tmp_path / "studio.db"
    empty_database(database)
    prepare_workspace_boundary(database, SPACE_A, allow_initialize=True)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("INSERT INTO projects(id,title) VALUES('saved','Keep')")
        connection.commit()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(statement, values)
        assert connection.execute("SELECT id,workspace_id FROM projects").fetchall() == [("saved", SPACE_A)]


def test_unknown_future_table_fails_closed_until_bound(tmp_path):
    """A newly added private table is not implicitly trusted as infrastructure."""
    database = tmp_path / "studio.db"
    empty_database(database)
    prepare_workspace_boundary(database, SPACE_A, allow_initialize=True)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE new_private_jobs(id TEXT PRIMARY KEY)")
        connection.commit()
    with pytest.raises(ValueError, match="owner column"):
        assert_workspace_boundary(database, SPACE_A)
    prepare_workspace_boundary(database, SPACE_A)
    assert_workspace_boundary(database, SPACE_A)


def test_missing_guard_is_detected_on_startup(tmp_path):
    """Readiness must not accept an altered database just because its marker matches."""
    database = tmp_path / "studio.db"
    empty_database(database)
    prepare_workspace_boundary(database, SPACE_A, allow_initialize=True)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute('DROP TRIGGER "workspace_owner_projects_insert"')
        connection.commit()
    with pytest.raises(ValueError, match="guard is missing"):
        assert_workspace_boundary(database, SPACE_A)


@pytest.mark.parametrize("suffix", ["../other", "ws_" + "a" * 31, SPACE_A + "/other", "WS_" + "a" * 32])
def test_invalid_workspace_id_cannot_form_a_storage_path(tmp_path, suffix):
    """Workspace identifiers are opaque IDs rather than client-selectable path text."""
    with pytest.raises(ValueError, match="identity"):
        preflight_workspace_boundary(tmp_path / "missing.db", suffix)


def test_private_worker_starts_without_inherited_credentials_or_env_file(tmp_path, monkeypatch):
    """A hypothetical new credential name is stripped together with known provider settings."""
    monkeypatch.setattr(os, "environ", {
        "PATH": "safe-tools", "SYSTEMROOT": "C:/Windows", "VIDEO_RUNTIME_CONFIG": "shared.json",
        "OPENAI_API_KEY": "must-not-inherit", "UNKNOWN_FUTURE_CLOUD_SECRET": "must-not-inherit",
        "DATABASE_URL": "shared-private-database", "STORAGE_BACKEND": "s3", "S3_BUCKET_NAME": "shared",
    })
    database, identity, port = configure_worker({
        "root": str(tmp_path), "workspace_id": SPACE_A, "port": 19452, "internal_token": TOKEN,
    })
    assert identity == SPACE_A and port == 19452 and database == tmp_path / SPACE_A / "studio.db"
    assert os.environ["JELLYFISH_SKIP_ENV_FILE"] == "1"
    assert os.environ["WORKSPACE_ID"] == SPACE_A and os.environ["STORAGE_BACKEND"] == "local"
    assert os.environ["VIDEO_UNDERSTANDING_ALLOW_REMOTE"] == "false"
    assert os.environ["CELERY_BROKER_URL"] == "memory://"
    assert all(key not in os.environ for key in (
        "OPENAI_API_KEY", "UNKNOWN_FUTURE_CLOUD_SECRET", "VIDEO_RUNTIME_CONFIG", "S3_BUCKET_NAME",
    ))
    assert os.environ["DATABASE_URL"].endswith(database.as_posix())


def test_worker_refuses_unknown_paths_and_unclaimed_directory(tmp_path):
    """A preexisting media directory cannot silently become a newly registered workspace."""
    directory = tmp_path / SPACE_A
    directory.mkdir()
    (directory / "private-video.mp4").write_bytes(b"preserved")
    message = {"root": str(tmp_path), "workspace_id": SPACE_A, "port": 19452, "internal_token": TOKEN}
    with pytest.raises(ValueError, match="must be empty"):
        configure_worker(message)
    assert (directory / "private-video.mp4").read_bytes() == b"preserved"
    with pytest.raises(ValueError, match="startup fields"):
        configure_worker({**message, "database": "other.db"})


def test_empty_unmarked_database_does_not_claim_preexisting_media(tmp_path):
    """An empty database file cannot disguise existing private files as a new workspace."""
    directory = tmp_path / SPACE_A
    directory.mkdir()
    empty_database(directory / "studio.db")
    media = directory / "media"
    media.mkdir()
    (media / "private.mp4").write_bytes(b"preserve")
    with pytest.raises(ValueError, match="must be empty"):
        configure_worker({"root": str(tmp_path), "workspace_id": SPACE_A, "port": 19452, "internal_token": TOKEN})
    assert (media / "private.mp4").read_bytes() == b"preserve"


@pytest.mark.asyncio
async def test_private_http_guard_and_fixed_readiness_identity():
    """Direct access, forged scope headers, and duplicate private tokens do not bypass the gateway."""
    reached = []

    async def application(scope, receive, send):
        """Observe whether authentication allowed an ordinary application endpoint."""
        reached.append(scope["path"])
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = TenantBoundaryMiddleware(application, workspace_id=SPACE_A, internal_token=TOKEN)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://private") as client:
        for path in ("/health", "/openapi.json", "/api/v1/studio/projects", READINESS_PATH):
            response = await client.get(path, headers={"X-Workspace-ID": SPACE_A})
            assert response.status_code == 403
        duplicate = await client.get(READINESS_PATH, headers=[("X-Workspace-Token", TOKEN), ("X-Workspace-Token", TOKEN)])
        assert duplicate.status_code == 403
        response = await client.get(READINESS_PATH, headers={"X-Workspace-Token": TOKEN, "X-Workspace-ID": SPACE_B})
        assert response.status_code == 200 and response.json() == {"workspace_id": SPACE_A, "status": "ready"}
        assert response.headers["cache-control"] == "no-store"
        response = await client.get("/api/v1/studio/projects", headers={"X-Workspace-Token": TOKEN})
        assert response.status_code == 204
    assert reached == ["/api/v1/studio/projects"]
