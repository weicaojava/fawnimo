"""Run one gateway-owned, loopback-only workspace after its process ownership gate.

Only the maintenance gateway invokes this entry point. Private data locations,
workspace identity, and internal authentication never come from HTTP clients.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys

from app.core.workspace_boundary import (
    assert_workspace_boundary, preflight_workspace_boundary,
    prepare_workspace_boundary, validate_workspace_id,
)


SAFE_ENVIRONMENT_KEYS = frozenset({
    "SYSTEMROOT", "WINDIR", "COMSPEC", "SYSTEMDRIVE", "OS", "PATH", "PATHEXT",
    "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL", "TZ", "PYTHONIOENCODING",
    "PYTHONUTF8", "PYTHONDONTWRITEBYTECODE",
})


def configure_worker(message: dict) -> tuple[Path, str, int]:
    """Confine paths and replace inherited configuration before app settings import."""
    if not isinstance(message, dict) or set(message) - {"root", "workspace_id", "port", "internal_token", "probe"}:
        raise ValueError("Invalid private workspace startup fields")
    workspace_id = validate_workspace_id(message.get("workspace_id"))
    port, token = message.get("port"), message.get("internal_token")
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("Invalid private workspace port")
    if not isinstance(token, str) or not 32 <= len(token) <= 512 or not token.isascii():
        raise ValueError("Invalid private workspace token")
    root_value = message.get("root")
    if not isinstance(root_value, str) or not Path(root_value).is_absolute():
        raise ValueError("Workspace root must be an absolute server-owned path")
    root = Path(root_value).resolve()
    root.mkdir(parents=True, exist_ok=True)
    directory = root / workspace_id
    if directory.resolve() != directory:
        raise ValueError("Workspace directory cannot redirect to another path")
    directory.mkdir(exist_ok=True)
    database = directory / "studio.db"
    for target in (database, directory / "media", directory / "files"):
        if target.resolve() != target:
            raise ValueError("Workspace storage path cannot redirect to another path")
    marked = preflight_workspace_boundary(database, workspace_id)
    if not marked:
        for item in directory.iterdir():
            # Only empty storage directories and SQLite's own startup artifacts
            # may survive a partially initialized, as-yet unclaimed worker.
            if item.name in {"studio.db", "studio.db-wal", "studio.db-shm", "studio.db-journal"} and item.is_file():
                continue
            if item.name in {"media", "files"} and item.is_dir() and not any(item.iterdir()):
                continue
            raise ValueError("Unclaimed workspace directory must be empty")
    probe_value = message.get("probe")
    probe = ""
    if probe_value is not None:
        if not isinstance(probe_value, str) or not Path(probe_value).is_absolute():
            raise ValueError("Media executable must be a fixed absolute server path")
        probe_path = Path(probe_value).resolve(strict=True)
        if not probe_path.is_file():
            raise ValueError("Media executable must be a file")
        probe = str(probe_path)
    # The second allowlist protects direct launcher use as well as gateway spawn.
    # It prevents future inherited credential names from bypassing a denylist.
    kept = {key: value for key, value in os.environ.items() if key.upper() in SAFE_ENVIRONMENT_KEYS}
    os.environ.clear()
    os.environ.update(kept)
    os.environ.update({
        "JELLYFISH_SKIP_ENV_FILE": "1", "WORKSPACE_ID": workspace_id,
        "WORKSPACE_INTERNAL_TOKEN": token,
        "DATABASE_URL": "sqlite+aiosqlite:///" + database.as_posix(),
        "LOCAL_STORAGE_ROOT": str(directory / "files"), "STORAGE_BACKEND": "local",
        "PRODUCTION_MEDIA_ROOT": str(directory / "media"),
        "PRODUCTION_PROBE_EXECUTABLE": probe,
        "VIDEO_UNDERSTANDING_ALLOW_REMOTE": "false",
        "VIDEO_UNDERSTANDING_ENABLED": "true",
        "REVERSE_ANALYSIS_ENABLED": "true", "EDITING_EXPORT_ENABLED": "true",
        "DEBUG": "false", "CORS_ORIGINS": "",
        # No shared Celery endpoint is usable by a private workspace worker.
        "CELERY_BROKER_URL": "memory://", "REDIS_HOST": "127.0.0.1",
    })
    (directory / "media").mkdir(exist_ok=True)
    (directory / "files").mkdir(exist_ok=True)
    return database, workspace_id, port


async def serve_worker(database: Path, workspace_id: str, port: int) -> None:
    """Migrate only the already checked tenant database before starting its workers."""
    # Delayed imports are essential: Settings otherwise reads backend/.env.
    import app.api.v1  # noqa: F401
    from app.core.db import close_db, init_db
    from run_local import build_server
    try:
        await init_db()
        from app.core.db import engine
        from app.services.worker.workspace_budget_schema import migrate_workspace_budget
        prepare_workspace_boundary(database, workspace_id, allow_initialize=True)
        await migrate_workspace_budget(engine)
        assert_workspace_boundary(database, workspace_id)
        await build_server(port).serve()
    finally:
        await close_db()


def main() -> None:
    """Wait for stdin EOF before imports, database writes, or descendant creation."""
    payload = sys.stdin.buffer.read(16385)
    if not payload or len(payload) > 16384:
        raise ValueError("Private workspace startup message missing or oversized")
    # The parent assigns its Job Object or process group before closing stdin.
    message = json.loads(payload)
    database, workspace_id, port = configure_worker(message)
    asyncio.run(serve_worker(database, workspace_id, port))


if __name__ == "__main__":
    main()
