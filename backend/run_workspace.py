"""Start a persistent, migrated local studio with local analysis and file storage."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime, timezone


def prepare_workspace(workspace: Path, source_db: Path | None = None, source_media: Path | None = None) -> Path:
    """Copy a consistent first-run database without ever replacing an existing workspace."""
    workspace = workspace.expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    database = workspace / 'studio.db'
    if database.exists():
        return database
    if source_db is not None:
        source_db = source_db.resolve(strict=True)
        if source_db == database:
            raise ValueError('Source and workspace database must differ')
        pending = workspace / 'initial-backup.db'
        if pending.exists():
            raise ValueError('An incomplete initial backup exists; inspect it before restarting')
        with closing(sqlite3.connect(source_db.as_uri() + '?mode=ro', uri=True)) as source, closing(sqlite3.connect(pending)) as target:
            source.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone() != ('ok',):
                raise ValueError('Source database integrity check failed')
            if target.execute('PRAGMA foreign_key_check').fetchone() is not None:
                raise ValueError('Source database has invalid references')
        if source_media is not None:
            source_media = source_media.resolve(strict=True)
            if source_media == workspace / 'media' or workspace.is_relative_to(source_media):
                raise ValueError('Source media cannot contain the new workspace')
            shutil.copytree(source_media, workspace / 'media', dirs_exist_ok=True)
        pending.replace(database)
        (workspace / 'initial-source.json').write_text(json.dumps({'database': str(source_db), 'media': str(source_media) if source_media else None}, indent=2), 'utf-8')
    return database


def configure(workspace: Path, database: Path, probe: Path | None, video_config: Path | None = None) -> None:
    """Configure local storage and only opt into video execution through an explicit server file."""
    os.environ.update(
        DATABASE_URL='sqlite+aiosqlite:///' + database.as_posix(),
        PRODUCTION_MEDIA_ROOT=str(workspace / 'media'),
        LOCAL_STORAGE_ROOT=str(workspace / 'files'), STORAGE_BACKEND='local',
        REVERSE_ANALYSIS_ENABLED='true', EDITING_EXPORT_ENABLED='true',
        CORS_ORIGINS='http://127.0.0.1:17888,http://localhost:17888,http://127.0.0.1:7788,http://localhost:7788',
    )
    if probe is not None:
        os.environ['PRODUCTION_PROBE_EXECUTABLE'] = str(probe.resolve(strict=True))
    if video_config is not None:
        os.environ['VIDEO_RUNTIME_CONFIG'] = str(video_config.resolve(strict=True))


def backup_workspace(database: Path) -> Path | None:
    """Keep a consistent pre-migration backup for every existing persistent workspace start."""
    if not database.exists():
        return None
    directory = database.parent / 'backups'
    directory.mkdir(exist_ok=True)
    target = directory / ('before-start-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.db')
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as source, closing(sqlite3.connect(target)) as backup:
        source.backup(backup)
        if backup.execute('PRAGMA integrity_check').fetchone() != ('ok',):
            raise ValueError('Workspace backup integrity check failed')
    return target


async def serve(port: int) -> None:
    """Apply supported additive migrations before exposing the persistent workspace API."""
    # Route imports register all current ORM models before metadata initialization.
    import app.api.v1  # noqa: F401
    from app.core.db import init_db, close_db
    from run_local import build_server
    await init_db()
    try:
        await build_server(port).serve()
    finally:
        await close_db()


def main() -> None:
    """Expose one repeatable local entry point without credentials or paid runtime activation."""
    parser = argparse.ArgumentParser(description='Start the persistent Jellyfish local studio')
    parser.add_argument('--workspace', type=Path, default=Path(__file__).resolve().parent / 'local-workspace')
    parser.add_argument('--source-db', type=Path)
    parser.add_argument('--source-media', type=Path)
    parser.add_argument('--probe', type=Path)
    parser.add_argument('--video-config', type=Path, help='Optional trusted video runtime JSON; templates default to disabled')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('Port must be between 1 and 65535')
    workspace = args.workspace.expanduser().resolve()
    database = prepare_workspace(workspace, args.source_db, args.source_media)
    backup_workspace(database)
    found = shutil.which('ffmpeg')
    probe = args.probe or (Path(found) if found else None)
    configure(workspace, database, probe, args.video_config)
    asyncio.run(serve(args.port))


if __name__ == '__main__':
    main()
