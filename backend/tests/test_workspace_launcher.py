"""Protect saved local work across first-time setup, later restarts and migrations."""
import hashlib
import sqlite3
from contextlib import closing
import pytest
from run_workspace import backup_workspace, prepare_workspace


def original_database(path):
    """Create a small real source database whose bytes must not be modified by setup."""
    with closing(sqlite3.connect(path)) as connection:
        connection.execute('CREATE TABLE saved_work(id INTEGER PRIMARY KEY, text TEXT)')
        connection.execute("INSERT INTO saved_work VALUES(1,'original')")
        connection.commit()


def test_first_run_copies_consistently_and_restart_keeps_new_user_work(tmp_path):
    """A restart cannot roll the user back to the older preview copy."""
    source = tmp_path / 'source.db'
    original_database(source)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    media = tmp_path / 'source-media'
    media.mkdir()
    (media / 'evidence.bin').write_bytes(b'original evidence')
    workspace = tmp_path / 'persistent'
    database = prepare_workspace(workspace, source, media)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("UPDATE saved_work SET text='user edit' WHERE id=1")
        connection.commit()
    assert prepare_workspace(workspace, source, media) == database
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute('SELECT text FROM saved_work').fetchone() == ('user edit',)
    assert (workspace / 'media/evidence.bin').read_bytes() == b'original evidence'
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest


def test_backup_is_readable_after_connections_close_on_windows(tmp_path):
    """Completed backups can be reopened independently without leaked SQLite file handles."""
    source = tmp_path / 'studio.db'
    original_database(source)
    backup = backup_workspace(source)
    assert backup and backup != source
    with closing(sqlite3.connect(backup)) as connection:
        assert connection.execute('SELECT text FROM saved_work').fetchone() == ('original',)
    moved = backup.with_suffix('.verified.db')
    backup.rename(moved)
    assert moved.exists() and source.exists()


def test_unknown_partial_setup_is_not_silently_overwritten(tmp_path):
    """An earlier incomplete copy needs inspection instead of a destructive retry."""
    source = tmp_path / 'source.db'
    original_database(source)
    workspace = tmp_path / 'persistent'
    workspace.mkdir()
    pending = workspace / 'initial-backup.db'
    pending.write_bytes(b'preserve for recovery')
    with pytest.raises(ValueError, match='incomplete initial backup'):
        prepare_workspace(workspace, source)
    assert pending.read_bytes() == b'preserve for recovery' and not (workspace / 'studio.db').exists()
