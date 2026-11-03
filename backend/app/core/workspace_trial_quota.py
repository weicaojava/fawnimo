"""Atomic project-count enforcement for one trusted SQLite worker; all creation paths, including demos, share the same trigger."""
from contextlib import closing
import sqlite3
from app.core.contracts.identity_trials import WorkspaceTrialPolicy
from app.core.workspace_boundary import assert_workspace_boundary

TABLE='''CREATE TABLE workspace_trial_quota(singleton INTEGER PRIMARY KEY CHECK(singleton=1),workspace_id TEXT NOT NULL,
 profile TEXT NOT NULL CHECK(profile='trial_v1'),expires_at INTEGER NOT NULL CHECK(expires_at>0),project_limit INTEGER NOT NULL CHECK(project_limit BETWEEN 1 AND 10))'''
GUARDS={
 'workspace_trial_quota_no_update':"CREATE TRIGGER workspace_trial_quota_no_update BEFORE UPDATE ON workspace_trial_quota BEGIN SELECT RAISE(ABORT,'Immutable worker trial policy'); END",
 'workspace_trial_quota_no_delete':"CREATE TRIGGER workspace_trial_quota_no_delete BEFORE DELETE ON workspace_trial_quota BEGIN SELECT RAISE(ABORT,'Immutable worker trial policy'); END",
 'workspace_trial_quota_no_replace':"CREATE TRIGGER workspace_trial_quota_no_replace BEFORE INSERT ON workspace_trial_quota WHEN EXISTS(SELECT 1 FROM workspace_trial_quota WHERE singleton=NEW.singleton OR rowid=NEW.rowid) BEGIN SELECT RAISE(ABORT,'Worker trial policy replacement rejected'); END",
 'workspace_trial_project_limit':"CREATE TRIGGER workspace_trial_project_limit BEFORE INSERT ON projects WHEN (SELECT count(*) FROM projects)>=(SELECT project_limit FROM workspace_trial_quota WHERE singleton=1) BEGIN SELECT RAISE(ABORT,'FAWNIMO_TRIAL_PROJECT_LIMIT'); END"}


def prepare_trial_project_quota(database,workspace_id,policy):
    """Install a trusted fixed quota only after ownership/schema preparation; absent policy cannot disable an existing guarded trial worker."""
    policy=None if policy is None else WorkspaceTrialPolicy.model_validate(policy)
    assert_workspace_boundary(database,workspace_id)
    with closing(sqlite3.connect(database,timeout=1.5)) as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            present=db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='workspace_trial_quota'").fetchone()
            if policy is None:
                if present is not None: raise ValueError('Existing trial policy cannot be silently disabled')
                db.rollback(); return
            if present is None:
                db.execute(TABLE)
                db.execute('INSERT INTO workspace_trial_quota VALUES(1,?,?,?,?)',(workspace_id,policy.profile,policy.expires_at,policy.project_limit))
                for sql in GUARDS.values(): db.execute(sql)
            else:
                if ' '.join(present[0].split())!=' '.join(TABLE.split()): raise ValueError('Worker trial schema changed')
                for name,expected in GUARDS.items():
                    found=db.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",(name,)).fetchone()
                    if found is None or ' '.join(found[0].split())!=' '.join(expected.split()): raise ValueError('Worker trial guard changed')
            if db.execute('SELECT * FROM workspace_trial_quota').fetchall()!=[(1,workspace_id,policy.profile,policy.expires_at,policy.project_limit)]:
                raise ValueError('Worker trial quota belongs to another fixed identity or policy')
            db.commit()
        except BaseException: db.rollback(); raise
