"""Finite private whole-root snapshots with SQLite backup and content verification; never public artifacts or in-place rollback."""
from contextlib import closing,ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from app.controlplane.maintenance import ProcessLease
from app.controlplane.migration import table_receipts,flush_directory

MAX_FILES=50000
MAX_BYTES=8589934592
MAX_FILE_BYTES=2147483648
MAX_SECONDS=120
MAX_MANIFEST=8388608
PROFILE='local_release_data_v1'


class LocalReleaseError(ValueError):
    """Use fixed errors without reflecting private filenames, database rows, model configuration or credentials."""


def plain(path,*,exists=True):
    """Reject relative aliases, symlinks/junctions and redirected ancestors before touching a private artifact or destination."""
    value=Path(path).absolute()
    if value.resolve()!=value or exists and not value.exists(): raise LocalReleaseError('Canonical existing local path required')
    return value


def separate(*paths):
    """All source/archive/restore/control paths must be disjoint; copying into a parent/child or the original is never rollback."""
    values=[plain(path,exists=False) for path in paths]
    if any(left.is_relative_to(right) or right.is_relative_to(left) for index,left in enumerate(values) for right in values[index+1:]):
        raise LocalReleaseError('Independent local paths required')


def deadline_check(deadline):
    """Every bounded file/SQLite pass shares an operation deadline, independent of caller data or wall-clock adjustments."""
    if time.monotonic()>deadline: raise LocalReleaseError('Local snapshot time budget reached')


def write_json(path,value):
    """Create and fsync one new record; interruption leaves recoverable evidence and never overwrites an earlier file."""
    raw=(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode('utf8')
    if len(raw)>MAX_MANIFEST: raise LocalReleaseError('Local record size limit reached')
    with plain(path,exists=False).open('xb') as stream: stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    flush_directory(Path(path).parent)


def read_json(path):
    """Bound private manifests and reject duplicate/nonfinite fields rather than trusting changed or partial journal text."""
    from app.services.community.events import unique_fields,invalid_number,finite_float
    with plain(path).open('rb') as stream: raw=stream.read(MAX_MANIFEST+1)
    if len(raw)>MAX_MANIFEST: raise LocalReleaseError('Local record size limit reached')
    return json.loads(raw.decode('utf8'),object_pairs_hook=unique_fields,parse_constant=invalid_number,parse_float=finite_float)


def file_hash(path,deadline):
    """Hash regular private bytes in finite chunks, without interpreting models, source code or media."""
    value=plain(path); information=value.stat()
    if not stat.S_ISREG(information.st_mode) or information.st_nlink!=1 or information.st_size>MAX_FILE_BYTES:
        raise LocalReleaseError('Unsupported linked, special or oversized private file')
    digest=hashlib.sha256(); count=0
    with value.open('rb') as stream:
        while content:=stream.read(1048576):
            deadline_check(deadline); count+=len(content)
            if count>MAX_FILE_BYTES: raise LocalReleaseError('Private file grew beyond limit')
            digest.update(content)
    if value.stat().st_size!=count: raise LocalReleaseError('Private file changed while reading')
    return {'sha256':digest.hexdigest(),'bytes':count}


def inventory(root,deadline):
    """Enumerate all private regular files; skip only known cooperative lock directories and SQLite transient companions."""
    root=plain(root); result={}; total=0; pending=[root]; directories=0
    while pending:
        directory=pending.pop(); deadline_check(deadline); directories+=1
        if directories>MAX_FILES: raise LocalReleaseError('Private directory limit reached')
        for item in sorted(directory.iterdir()):
            value=plain(item); relative=value.relative_to(root).as_posix()
            if relative=='.maintenance.lock' or relative=='workspaces/.leases' or relative.startswith('workspaces/.leases/'): continue
            if value.is_dir():
                pending.append(value)
                if len(pending)>MAX_FILES: raise LocalReleaseError('Private directory limit reached')
                continue
            if any(value.name.endswith(suffix) and value.with_name(value.name[:-len(suffix)]).is_file()
                and sqlite_file(value.with_name(value.name[:-len(suffix)])) for suffix in ('-wal','-shm','-journal')): continue
            result[relative]=file_hash(value,deadline); total+=result[relative]['bytes']
            if len(result)>MAX_FILES or total>MAX_BYTES: raise LocalReleaseError('Private snapshot capacity reached')
    return result


def database_receipt(path,deadline):
    """Check exact SQLite business content with bounded row values, table count and progress; private rows never enter a report."""
    with closing(sqlite3.connect(plain(path).as_uri()+'?mode=ro',uri=True,timeout=1.5)) as db:
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,8388608)
        db.set_progress_handler(lambda:1 if time.monotonic()>deadline else 0,1000)
        if db.execute('PRAGMA quick_check').fetchall()!=[('ok',)] or db.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise LocalReleaseError('SQLite snapshot integrity check failed')
        if db.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]>256:
            raise LocalReleaseError('SQLite table limit reached')
        names={row[0]:None for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")}
        schema=db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        return {'tables':table_receipts(db,expected=names),
            'schema_sha256':hashlib.sha256(json.dumps(schema,ensure_ascii=False,separators=(',',':')).encode()).hexdigest(),
            'user_version':db.execute('PRAGMA user_version').fetchone()[0],'application_id':db.execute('PRAGMA application_id').fetchone()[0]}


def flush_copy(directory,deadline):
    """Flush only owned destination files/directories before publishing completion; never open original source data for writing."""
    directory=plain(directory)
    for root,_children,files in os.walk(directory,topdown=False):
        deadline_check(deadline); root=plain(root)
        for name in files:
            deadline_check(deadline)
            with plain(root/name).open('r+b') as stream: os.fsync(stream.fileno())
        flush_directory(root)
    flush_directory(directory.parent)


def sqlite_file(path):
    """Identify a database by its fixed header, not by importing application modules or trusting a suffix."""
    with plain(path).open('rb') as stream: return stream.read(16)==b'SQLite format 3\x00'


def copy_snapshot(source,payload,deadline):
    """Under offline source/worker leases, back up every SQLite database and verify all other bytes before sealing the private archive."""
    source=plain(source); payload=plain(payload,exists=False)
    separate(source,payload)
    if not (source/'control.db').is_file(): raise LocalReleaseError('An existing identity gateway root is required')
    with ExitStack() as stack:
        stack.enter_context(ProcessLease(source/'.maintenance.lock'))
        with closing(sqlite3.connect((source/'control.db').as_uri()+'?mode=ro',uri=True,timeout=1.5)) as control:
            workspaces=[row[0] for row in control.execute('SELECT id FROM workspaces ORDER BY id')]
        if len(workspaces)>1024 or any(not isinstance(value,str) or re.fullmatch(r'ws_[0-9a-f]{32}',value) is None for value in workspaces):
            raise LocalReleaseError('Invalid gateway workspace inventory')
        spaces=source/'workspaces'
        if spaces.exists() and any(item.name!='.leases' and (not re.fullmatch(r'ws_[0-9a-f]{32}',item.name) or item.name not in workspaces) for item in spaces.iterdir()):
            raise LocalReleaseError('Unclaimed or incomplete workspace data requires maintenance')
        for identifier in workspaces: stack.enter_context(ProcessLease(spaces/'.leases'/(identifier+'.lock')))
        before=inventory(source,deadline); databases={}; payload.mkdir(mode=0o700); written_total=0
        for relative in before:
            original=source/relative; target=payload/relative; target.parent.mkdir(parents=True,exist_ok=True); deadline_check(deadline)
            if sqlite_file(original):
                original_rows=database_receipt(original,deadline)
                def backup_progress(*_args):
                    """Stop growth while SQLite copies pages, before a changing WAL can exceed the remaining private archive budget."""
                    deadline_check(deadline)
                    size=target.stat().st_size if target.exists() else 0
                    if size>MAX_FILE_BYTES or size+written_total>MAX_BYTES: raise LocalReleaseError('SQLite copy capacity reached')
                with closing(sqlite3.connect(original.as_uri()+'?mode=ro',uri=True,timeout=1.5)) as reader,closing(sqlite3.connect(target)) as writer:
                    reader.backup(writer,pages=128,progress=backup_progress,sleep=0.01)
                if database_receipt(target,deadline)!=original_rows or database_receipt(original,deadline)!=original_rows:
                    raise LocalReleaseError('Database changed during offline snapshot')
                databases[relative]=original_rows
            else:
                with original.open('rb') as reader,target.open('xb') as writer:
                    copied=0
                    while chunk:=reader.read(1048576):
                        deadline_check(deadline); copied+=len(chunk)
                        if copied>before[relative]['bytes'] or copied>MAX_FILE_BYTES or copied+written_total>MAX_BYTES:
                            raise LocalReleaseError('Private file grew during bounded copy')
                        writer.write(chunk)
                    writer.flush(); os.fsync(writer.fileno())
                if file_hash(target,deadline)!=before[relative]: raise LocalReleaseError('Private file changed during copy')
            written_total+=target.stat().st_size
            if written_total>MAX_BYTES: raise LocalReleaseError('Private archive capacity reached')
        # A WAL checkpoint can rewrite SQLite pages without changing business
        # content. Compare rows/schema/sequence/version, not physical DB bytes;
        # all non-database files still require the original complete hashes.
        after=inventory(source,deadline)
        if (set(before)!=set(after) or any(before[name]!=after[name] for name in before if name not in databases)
                or any(database_receipt(source/name,deadline)!=expected for name,expected in databases.items())):
            raise LocalReleaseError('Source inventory or database content changed during offline snapshot')
        files=inventory(payload,deadline)
        flush_copy(payload,deadline)
        return {'profile':PROFILE,'files':files,'databases':databases,'workspace_count':len(workspaces),'private_archive':True,
            'services_started':False,'existing_data_overwritten':False,'deployment_verified':False,'external_model_calls':0,'paid_authorization':0}


def verify_snapshot(payload,manifest,deadline):
    """Verify every archived file and database before restoring; extra/missing/changed content is never silently accepted."""
    if manifest.get('profile')!=PROFILE or inventory(payload,deadline)!=manifest.get('files'):
        raise LocalReleaseError('Snapshot file inventory differs from sealed manifest')
    for relative,expected in manifest['databases'].items():
        if relative not in manifest['files'] or database_receipt(plain(payload)/relative,deadline)!=expected:
            raise LocalReleaseError('Snapshot database content differs from sealed manifest')


def restore_snapshot(payload,manifest,destination,deadline):
    """Copy a verified private archive to one brand-new independent target; current data and later edits are never replaced."""
    payload=plain(payload); destination=plain(destination,exists=False); separate(payload,destination)
    if destination.exists() or not destination.parent.is_dir(): raise LocalReleaseError('Restore requires a fresh target under an existing parent')
    verify_snapshot(payload,manifest,deadline); destination.mkdir(mode=0o700)
    for relative in manifest['files']:
        deadline_check(deadline); target=destination/relative; target.parent.mkdir(parents=True,exist_ok=True)
        with (payload/relative).open('rb') as reader,target.open('xb') as writer:
            copied=0
            while chunk:=reader.read(1048576):
                deadline_check(deadline); copied+=len(chunk)
                if copied>manifest['files'][relative]['bytes']: raise LocalReleaseError('Archive file grew while restoring')
                writer.write(chunk)
            writer.flush(); os.fsync(writer.fileno())
    verify_snapshot(destination,manifest,deadline); flush_copy(destination,deadline)
    return {'restored_to_new_path':True,'file_count':len(manifest['files']),'database_count':len(manifest['databases']),
        'workspace_count':manifest['workspace_count'],'services_started':False,'existing_data_overwritten':False,'deployment_verified':False,'external_model_calls':0,'paid_authorization':0}
