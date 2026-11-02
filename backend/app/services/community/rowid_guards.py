"""Supplement natural-key guards with explicit SQLite rowid collision protection, independent of connection pragmas."""
import re


def rowid_guard_definitions(tables):
    """Block replacement of an existing physical receipt row even when a caller supplies different logical keys."""
    result={}
    for table in tables:
        if re.fullmatch(r'community_[a-z_]+',table) is None:raise ValueError('Invalid trusted community table name')
        name=table+'_no_rowid_replace'
        result[name]=f"CREATE TRIGGER {name} BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE rowid=NEW.rowid) BEGIN SELECT RAISE(ABORT,'Community rowid replacement rejected'); END"
    return result
