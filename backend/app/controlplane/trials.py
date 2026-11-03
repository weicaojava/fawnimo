"""Additive immutable trial records, atomic capacity and safe current-user/workspace lifecycle views."""
import asyncio
from contextlib import closing
from pathlib import Path
import sqlite3
from fastapi import HTTPException
from starlette.responses import JSONResponse
from app.core.contracts.identity_trials import TrialRegistrationPolicy,TrialAccessRead,TrialRegistrationRead,WorkspaceTrialPolicy

TRIAL_SCHEMA='''
CREATE TABLE IF NOT EXISTS identity_trial_accounts(
 user_id TEXT PRIMARY KEY NOT NULL REFERENCES users(id),
 profile TEXT NOT NULL CHECK(profile='trial_v1'),
 created_at INTEGER NOT NULL CHECK(typeof(created_at)='integer' AND created_at>=0),
 expires_at INTEGER NOT NULL CHECK(typeof(expires_at)='integer' AND expires_at>created_at),
 project_limit INTEGER NOT NULL CHECK(typeof(project_limit)='integer' AND project_limit BETWEEN 1 AND 10));
CREATE TRIGGER IF NOT EXISTS identity_trial_no_update BEFORE UPDATE ON identity_trial_accounts BEGIN SELECT RAISE(ABORT,'Immutable trial lifecycle'); END;
CREATE TRIGGER IF NOT EXISTS identity_trial_no_delete BEFORE DELETE ON identity_trial_accounts BEGIN SELECT RAISE(ABORT,'Immutable trial lifecycle'); END;
CREATE TRIGGER IF NOT EXISTS identity_trial_no_replace BEFORE INSERT ON identity_trial_accounts
 WHEN EXISTS(SELECT 1 FROM identity_trial_accounts WHERE user_id=NEW.user_id OR rowid=NEW.rowid)
 BEGIN SELECT RAISE(ABORT,'Trial lifecycle replacement rejected'); END;
CREATE TRIGGER IF NOT EXISTS identity_trial_capacity BEFORE INSERT ON identity_trial_accounts
 WHEN(SELECT count(*) FROM identity_trial_accounts)>=128 BEGIN SELECT RAISE(ABORT,'Trial lifecycle capacity reached'); END;
'''


def initialize_trials(db):
    """Add the separate optional table without updating, expiring or reassigning any existing user/workspace data."""
    db.executescript(TRIAL_SCHEMA)


def admit_trial(db,policy):
    """Run inside the same writer as account/workspace creation, so competing registrants cannot exceed the last remaining slot."""
    if not isinstance(policy,TrialRegistrationPolicy): raise ValueError('Trusted operator trial policy required')
    if db.execute('SELECT count(*) FROM identity_trial_accounts').fetchone()[0]>=policy.capacity:
        raise HTTPException(409,'本轮体验名额已满，请使用已有账号登录或联系维护者')


def record_trial(db,user_id,policy,now):
    """Bind immutable expiration and project count to the newly created user; no registration field can extend or remove these limits."""
    db.execute('INSERT INTO identity_trial_accounts VALUES(?,?,?,?,?)',(user_id,'trial_v1',now,now+policy.duration_seconds,policy.project_limit))


def registration_status(db,policy):
    """Return only finite public availability, with no list of accounts or private data roots."""
    count=db.execute('SELECT count(*) FROM identity_trial_accounts').fetchone()[0]
    return TrialRegistrationRead(capacity=policy.capacity,remaining=max(0,policy.capacity-count),duration_seconds=policy.duration_seconds,project_limit=policy.project_limit).model_dump()


def workspace_trial_policy(db,workspace_id):
    """The trusted owner of the fixed workspace determines its immutable worker quota, independently of the current actor's headers."""
    row=db.execute('SELECT t.expires_at,t.project_limit FROM identity_trial_accounts t JOIN workspaces w ON w.owner_id=t.user_id WHERE w.id=?',(workspace_id,)).fetchone()
    return None if row is None else WorkspaceTrialPolicy(expires_at=row[0],project_limit=row[1]).model_dump()


def current_trial_access(db,user_id,workspace_id,now):
    """Apply the earliest actor/space-owner trial expiry; collaboration cannot bypass either principal's read-only boundary."""
    own=db.execute('SELECT expires_at FROM identity_trial_accounts WHERE user_id=?',(user_id,)).fetchone()
    active=workspace_trial_policy(db,workspace_id)
    expiries=([own[0]] if own else [])+([active['expires_at']] if active else [])
    if not expiries: return None
    expiry=min(expiries)
    return TrialAccessRead(expires_at=expiry,read_only=now>=expiry,project_limit=active['project_limit'] if active else None).model_dump()


def require_trial_write(identity):
    """Keep expired project data readable/downloadable while refusing new business submissions, including demos and existing sessions."""
    if (identity.get('trial') or {}).get('read_only') is True:
        raise HTTPException(403,'体验已到期，当前可查看和下载资料，项目编辑已暂停；请联系维护者')


def preflight_trial_example(store,pool,identity,request_key):
    """Give new demo requests a clear full-project response; original request recovery precedes this diagnostic count and SQL remains authoritative."""
    limit=(identity.get('trial') or {}).get('project_limit')
    if limit is None: return
    with store.connect() as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='demo_requests'").fetchone():
            if db.execute('SELECT 1 FROM demo_requests WHERE workspace_id=? AND submitted_by=? AND request_key=?',
                (identity['workspace_id'],identity['user']['id'],request_key)).fetchone(): return
    root=Path(pool.root).absolute(); database=root/identity['workspace_id']/'studio.db'
    if database.resolve()!=database: raise HTTPException(503,'体验空间资料需要维护者核对')
    if not database.is_file(): return
    from app.core.workspace_boundary import preflight_workspace_boundary
    if not preflight_workspace_boundary(database,identity['workspace_id']): return
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=1.5)) as db:
        if db.execute('SELECT count(*) FROM projects').fetchone()[0]>=limit:
            raise HTTPException(409,'体验项目数量已用完，请先删除不需要的项目后再创建示例')


class IdentityBodyLimit:
    """Bound public identity writes before JSON parsing/password work, without buffering ordinary uploads or echoing private inputs."""
    def __init__(self,app):
        """Identity inputs are small fixed contracts; this does not set a storage or monetary quota for any project."""
        self.app=app

    async def __call__(self,scope,receive,send):
        """Accept at most16KiB/1024 body frames/15seconds for an identity write and forward one exact preserved body to FastAPI."""
        if scope['type']!='http' or not scope.get('path','').startswith('/api/v1/identity/') or scope.get('method') not in {'POST','PATCH','PUT'}:
            return await self.app(scope,receive,send)
        content=bytearray(); more=True
        try:
            async with asyncio.timeout(15):
                for _index in range(1024):
                    message=await receive()
                    if message['type']=='http.disconnect': return
                    chunk=message.get('body',b'')
                    if len(content)+len(chunk)>16384: raise ValueError()
                    content.extend(chunk)
                    more=message.get('more_body',False)
                    if not more: break
                if more: raise ValueError()
        except (ValueError,TimeoutError) as error:
            status=408 if isinstance(error,TimeoutError) else 413
            response=JSONResponse(status_code=status,content={'code':status,'message':'账号请求过大或接收超时，请缩短输入后重试','data':None},headers={'Cache-Control':'no-store'})
            return await response(scope,receive,send)
        sent=False
        async def replay():
            """Replay the already bounded body once; later disconnect reads retain the original ASGI connection semantics."""
            nonlocal sent
            if not sent:
                sent=True; return {'type':'http.request','body':bytes(content),'more_body':False}
            return await receive()
        return await self.app(scope,replay,send)
