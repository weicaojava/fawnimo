"""Actual SQLite journal contention, lifecycle and online-backup tests; no models."""
import sqlite3
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.core.sqlite_runtime import prepare_sqlite


@pytest.mark.asyncio
async def test_prepare_is_startup_only_idempotent_and_skips_memory(tmp_path):
    """File journals become WAL once; memory and synchronous FULL remain unchanged."""
    engine=create_async_engine('sqlite+aiosqlite:///'+(tmp_path/'file.db').as_posix())
    memory=create_async_engine('sqlite+aiosqlite:///:memory:')
    try:
        first=await prepare_sqlite(engine);second=await prepare_sqlite(engine)
        assert first['changed'] and not second['changed']
        assert first['journal_mode']=='wal' and first['synchronous']==2
        assert first['sqlite_version']==sqlite3.sqlite_version
        assert (await prepare_sqlite(memory))['reason']=='memory'
        print('Runtime SQLite version:',first['sqlite_version'],'synchronous:',first['synchronous'])
    finally:await engine.dispose();await memory.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('wal',[False,True])
async def test_actual_exclusive_writer_readers_and_second_writer(tmp_path,wal):
    """A writer held past 100ms blocks DELETE readers but WAL readers see committed data."""
    path=tmp_path/'contention.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE values_test(value INTEGER)');db.execute('INSERT INTO values_test VALUES(1)')
    if wal:
        engine=create_async_engine('sqlite+aiosqlite:///'+path.as_posix())
        try:await prepare_sqlite(engine)
        finally:await engine.dispose()
    writer=sqlite3.connect(path,timeout=.1);reader=sqlite3.connect(path,timeout=.1);other_writer=sqlite3.connect(path,timeout=.1)
    try:
        assert writer.execute('PRAGMA journal_mode').fetchone()[0]==('wal' if wal else 'delete')
        writer.execute('BEGIN EXCLUSIVE');writer.execute('UPDATE values_test SET value=2')
        # All of these calls occur while the first writer remains uncommitted.
        if wal:assert reader.execute('SELECT value FROM values_test').fetchone()[0]==1
        else:
            with pytest.raises(sqlite3.OperationalError,match='locked'):reader.execute('SELECT value FROM values_test').fetchone()
        with pytest.raises(sqlite3.OperationalError,match='locked'):other_writer.execute('BEGIN IMMEDIATE')
        writer.commit()
        assert reader.execute('SELECT value FROM values_test').fetchone()[0]==2
    finally:writer.close();reader.close();other_writer.close()


@pytest.mark.asyncio
async def test_online_backup_and_reopen_include_uncheckpointed_wal(tmp_path):
    """Backup captures committed WAL pages while a source connection remains open."""
    path=tmp_path/'live.db';backup=tmp_path/'backup.db'
    engine=create_async_engine('sqlite+aiosqlite:///'+path.as_posix())
    try:await prepare_sqlite(engine)
    finally:await engine.dispose()
    source=sqlite3.connect(path)
    try:
        source.execute('PRAGMA wal_autocheckpoint=0')
        source.execute('CREATE TABLE evidence(value TEXT)');source.execute("INSERT INTO evidence VALUES('committed in WAL')");source.commit()
        assert path.with_name(path.name+'-wal').stat().st_size>0
        with sqlite3.connect(backup) as target:source.backup(target)
        with sqlite3.connect(backup) as restored:
            assert restored.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            assert restored.execute('SELECT value FROM evidence').fetchone()[0]=='committed in WAL'
        with sqlite3.connect(path) as restarted:assert restarted.execute('SELECT value FROM evidence').fetchone()[0]=='committed in WAL'
    finally:source.close()


@pytest.mark.asyncio
async def test_real_lifespan_prepares_wal_before_runtime_start(tmp_path,monkeypatch):
    """Application startup, not a modified worker fixture, establishes the runtime journal."""
    import app.core.db as database
    import app.main as main
    from app.config import settings
    path=tmp_path/'startup.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE production_schema(version INTEGER)');db.execute('INSERT INTO production_schema VALUES(1)')
        assert db.execute('PRAGMA journal_mode').fetchone()[0]=='delete'
    engine=create_async_engine('sqlite+aiosqlite:///'+path.as_posix())
    started=[]
    class Runtime:
        """Observe the actual database mode at the lifecycle runtime-start boundary."""
        async def start(self):
            """Read through an independent live connection before any worker begins."""
            async with engine.connect() as db:started.append(await db.scalar(text('PRAGMA journal_mode')))
        async def stop(self):
            """Keep the lifecycle stop path observable without scheduling work."""
            started.append('stopped')
    monkeypatch.setattr(database,'engine',engine)
    monkeypatch.setattr(settings,'reverse_analysis_enabled',False)
    monkeypatch.setattr(main,'bootstrap_all_registries',lambda:None)
    monkeypatch.setattr(main.app.state,'execution_runtime',Runtime(),raising=False)
    try:
        async with main.lifespan(main.app):assert started==['wal']
        assert started==['wal','stopped']
    finally:await engine.dispose()
