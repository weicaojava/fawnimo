"""Startup-only WAL preparation for local single-host SQLite databases."""
import asyncio
import logging
from sqlalchemy import text

logger=logging.getLogger(__name__)
_preparation_lock=asyncio.Lock()


async def prepare_sqlite(engine):
    """Serialize journal configuration before workers start, without changing durability.

    Memory databases are explicitly skipped. Existing WAL is inspected but never
    reset. AUTOCOMMIT ensures journal changes do not occur inside an SQL transaction.
    Backups must use SQLite backup or stopped-service consistent copying; live WAL
    databases must not be backed up by copying only the main database file.
    """
    if engine.dialect.name!='sqlite':return {'prepared':False,'reason':'not_sqlite'}
    async with _preparation_lock:
        async with engine.connect() as raw:
            connection=await raw.execution_options(isolation_level='AUTOCOMMIT')
            version=await connection.scalar(text('SELECT sqlite_version()'))
            databases=(await connection.execute(text('PRAGMA database_list'))).all()
            main=next((row for row in databases if row[1]=='main'),None)
            if main is None or not main[2]:
                logger.info('SQLite runtime version=%s storage=memory WAL skipped',version)
                return {'prepared':False,'reason':'memory','sqlite_version':version}
            before=str(await connection.scalar(text('PRAGMA journal_mode'))).lower()
            mode=before
            if before!='wal':mode=str(await connection.scalar(text('PRAGMA journal_mode=WAL'))).lower()
            if mode!='wal':raise RuntimeError('Local SQLite WAL preparation failed')
            synchronous=await connection.scalar(text('PRAGMA synchronous'))
            logger.info('SQLite runtime version=%s journal=wal synchronous=%s changed=%s single_host_local_only',version,synchronous,before!='wal')
            return {'prepared':True,'sqlite_version':version,'journal_mode':mode,'changed':before!='wal','synchronous':synchronous}
