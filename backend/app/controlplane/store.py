"""Transactional account, membership and revocable-session persistence for one host."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import re
import secrets
import sqlite3
from app.controlplane.trials import initialize_trials,admit_trial,record_trial,current_trial_access,registration_status,workspace_trial_policy
import time
from pathlib import Path

from fastapi import HTTPException

ACCOUNT = re.compile(r"[a-z0-9][a-z0-9_.-]{2,63}\Z")
WORKSPACE = re.compile(r"ws_[a-f0-9]{32}\Z")
SESSION_SECONDS = 12 * 3600


def password_hash(password: str, salt: str | None = None) -> str:
    """Use salted memory-hard hashes; reject oversized inputs before expensive work."""
    if not 12 <= len(password) <= 256:
        raise HTTPException(422, "密码需要 12 至 256 个字符")
    salt = salt or secrets.token_hex(16)
    value = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32)
    return salt + ":" + value.hex()


def verify_password(password: str, saved: str) -> bool:
    """Compare hashes in constant time without returning account existence to callers."""
    try:
        return hmac.compare_digest(password_hash(password, saved.split(":")[0]), saved)
    except (ValueError, HTTPException):
        return False


class IdentityStore:
    """Keep identities separate from workspace files; every request rechecks membership."""

    def __init__(self, root: Path, *,clock=None):
        """Initialize an additive private control database without importing studio settings."""
        self.root = root.resolve()
        self.clock=clock or time.time
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "control.db"
        self.dummy_password = password_hash(secrets.token_urlsafe(32))
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users(
                    id TEXT PRIMARY KEY, account TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL, display_name TEXT NOT NULL,
                    language TEXT NOT NULL DEFAULT 'zh-CN', theme TEXT NOT NULL DEFAULT 'light',
                    disabled INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS workspaces(
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'personal',
                    owner_id TEXT NOT NULL UNIQUE REFERENCES users(id), created_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS memberships(
                    user_id TEXT NOT NULL REFERENCES users(id), workspace_id TEXT NOT NULL REFERENCES workspaces(id),
                    role TEXT NOT NULL CHECK(role IN ('owner','editor','viewer')), PRIMARY KEY(user_id,workspace_id));
                CREATE TABLE IF NOT EXISTS sessions(
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    workspace_id TEXT NOT NULL REFERENCES workspaces(id), expires_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS login_limits(
                    identity TEXT PRIMARY KEY, failures INTEGER NOT NULL, reset_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS request_audit(
                    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id),
                    submitted_by TEXT NOT NULL REFERENCES users(id), method TEXT NOT NULL,
                    path TEXT NOT NULL, body_sha256 TEXT NOT NULL, created_at INTEGER NOT NULL,
                    status INTEGER, completed_at INTEGER);
                CREATE TRIGGER IF NOT EXISTS request_audit_identity_immutable
                    BEFORE UPDATE OF id,workspace_id,submitted_by,method,path,body_sha256,created_at ON request_audit
                    BEGIN SELECT RAISE(ABORT,'Immutable request identity'); END;
                CREATE TRIGGER IF NOT EXISTS request_audit_no_delete BEFORE DELETE ON request_audit
                    BEGIN SELECT RAISE(ABORT,'Immutable request audit'); END;
            """)
            initialize_trials(db)
            from app.controlplane.image_admission import initialize_image_admission_audit
            initialize_image_admission_audit(db)

    @contextmanager
    def connect(self):
        """Use bounded transactions and foreign keys on every control-plane connection."""
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def provision(self, account: str, password: str, display_name: str | None = None, *, trial_policy=None) -> dict:
        """Atomically create one account/personal space and optional trusted new-user trial; existing permanent users are never converted."""
        account = account.strip().lower()
        if not ACCOUNT.fullmatch(account):
            raise HTTPException(422, "账号需要 3 至 64 个小写字母、数字或 ._- 字符")
        name = (display_name or account).strip()
        if not 1 <= len(name) <= 100:
            raise HTTPException(422, "名称需要 1 至 100 个字符")
        encoded = password_hash(password)
        user, workspace, now = "usr_" + secrets.token_hex(16), "ws_" + secrets.token_hex(16), int(self.clock())
        try:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if trial_policy is not None:
                    admit_trial(db,trial_policy)
                db.execute("INSERT INTO users(id,account,password_hash,display_name,created_at) VALUES(?,?,?,?,?)", (user, account, encoded, name, now))
                db.execute("INSERT INTO workspaces(id,name,owner_id,created_at) VALUES(?,?,?,?)", (workspace, name + "的个人空间", user, now))
                db.execute("INSERT INTO memberships VALUES(?,?,?)", (user, workspace, "owner"))
                if trial_policy is not None:
                    record_trial(db,user,trial_policy,now)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "账号已存在") from exc
        return {"user_id": user, "workspace_id": workspace, "account": account}

    def trial_registration_status(self, policy):
        """Expose only finite capacity/duration/project-count settings selected by the trusted gateway."""
        with self.connect() as db:
            return registration_status(db,policy)

    def workspace_trial_policy(self, workspace_id):
        """Resolve fixed worker quota from the actual space owner's immutable record, never the requesting actor or HTTP fields."""
        with self.connect() as db:
            return workspace_trial_policy(db,workspace_id)

    def login(self, account: str, password: str, peer: str) -> str:
        """Rate-limit failed attempts persistently and issue an opaque, hashed-at-rest session."""
        account, now = account.strip().lower(), int(self.clock())
        keys = [hashlib.sha256((prefix + value).encode()).hexdigest() for prefix, value in (("account:", account), ("peer:", peer))]
        with self.connect() as db:
            for key in keys:
                limited = db.execute("SELECT * FROM login_limits WHERE identity=?", (key,)).fetchone()
                if limited and limited["reset_at"] > now and limited["failures"] >= 10:
                    raise HTTPException(429, "登录尝试过多，请稍后重试")
            row = db.execute("SELECT * FROM users WHERE account=?", (account,)).fetchone()
        valid = verify_password(password, row["password_hash"] if row else self.dummy_password)
        if not valid or row is None or row["disabled"]:
            with self.connect() as db:
                for key in keys:
                    db.execute("""INSERT INTO login_limits VALUES(?,1,?) ON CONFLICT(identity) DO UPDATE SET
                        failures=CASE WHEN reset_at<=? THEN 1 ELSE failures+1 END,
                        reset_at=CASE WHEN reset_at<=? THEN excluded.reset_at ELSE reset_at END""", (key, now + 600, now, now))
            raise HTTPException(401, "账号或密码不正确")
        token = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # Creation and membership are provisioned together; login never adopts legacy data.
            workspace = db.execute("SELECT w.id FROM workspaces w JOIN memberships m ON m.workspace_id=w.id WHERE w.owner_id=? AND m.user_id=?", (row["id"], row["id"])).fetchone()
            if workspace is None:
                raise HTTPException(403, "个人空间成员关系已撤销，请联系维护者")
            db.execute("DELETE FROM login_limits WHERE identity=?", (keys[0],))
            db.execute("DELETE FROM login_limits WHERE reset_at<=?", (now,))
            db.execute("DELETE FROM sessions WHERE expires_at<=?", (now,))
            db.execute("INSERT INTO sessions VALUES(?,?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), row["id"], workspace["id"], now + SESSION_SECONDS))
        return token

    def current(self, token: str | None) -> dict:
        """Authenticate an unexpired session and its currently active membership on every use."""
        if not token or len(token) > 256:
            raise HTTPException(401, "请先登录")
        with self.connect() as db:
            row = db.execute("""SELECT u.id,u.account,u.display_name,u.language,u.theme,s.workspace_id,m.role
                FROM sessions s JOIN users u ON u.id=s.user_id
                JOIN memberships m ON m.user_id=u.id AND m.workspace_id=s.workspace_id
                WHERE s.token_hash=? AND s.expires_at>? AND u.disabled=0""", (hashlib.sha256(token.encode()).hexdigest(), int(self.clock()))).fetchone()
            if row is None:
                raise HTTPException(401, "登录已失效，请重新登录")
            workspaces = db.execute("SELECT w.id,w.name,m.role FROM workspaces w JOIN memberships m ON m.workspace_id=w.id WHERE m.user_id=? ORDER BY w.created_at,w.id", (row["id"],)).fetchall()
            trial=current_trial_access(db,row['id'],row['workspace_id'],int(self.clock()))
        result={"user": {key: row[key] for key in ("id", "account", "display_name", "language", "theme")}, "workspace_id": row["workspace_id"], "role": row["role"], "workspaces": [dict(value) for value in workspaces]}
        if trial is not None: result['trial']=trial
        return result

    def switch(self, token: str, workspace_id: str) -> dict:
        """Accept only a membership-owned ID; paths and worker addresses are never client inputs."""
        current = self.current(token)
        if workspace_id not in {value["id"] for value in current["workspaces"]}:
            raise HTTPException(404, "工作空间不存在")
        with self.connect() as db:
            changed = db.execute("""UPDATE sessions SET workspace_id=? WHERE token_hash=? AND EXISTS
                (SELECT 1 FROM memberships WHERE user_id=? AND workspace_id=?)""", (workspace_id, hashlib.sha256(token.encode()).hexdigest(), current["user"]["id"], workspace_id)).rowcount
            if changed != 1:
                raise HTTPException(403, "工作空间成员关系已撤销")
        return self.current(token)

    def logout(self, token: str | None) -> None:
        """Revoke the durable session so copied cookies immediately lose access."""
        if token:
            with self.connect() as db:
                db.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))

    def profile(self, token: str, updates: dict) -> dict:
        """Store display preferences on the user, independently of the active workspace."""
        current = self.current(token)
        allowed = {"display_name", "language", "theme"}
        if set(updates) - allowed:
            raise HTTPException(422, "个人设置字段无效")
        if "language" in updates and updates["language"] not in {"zh-CN", "en-US"}:
            raise HTTPException(422, "语言无效")
        if "theme" in updates and updates["theme"] not in {"light", "dark"}:
            raise HTTPException(422, "主题无效")
        if "display_name" in updates and not 1 <= len(str(updates["display_name"]).strip()) <= 100:
            raise HTTPException(422, "名称需要 1 至 100 个字符")
        if updates:
            with self.connect() as db:
                db.execute("UPDATE users SET " + ",".join(key + "=?" for key in updates) + " WHERE id=?", (*updates.values(), current["user"]["id"]))
        return self.current(token)

    def audit_start(self, identity: dict, method: str, path: str, body: bytes, *, image_admission=None) -> str:
        """Record trusted actor/scope and a request digest, never plaintext prompts or credentials."""
        identifier = secrets.token_hex(16)
        with self.connect() as db:
            db.execute("INSERT INTO request_audit(id,workspace_id,submitted_by,method,path,body_sha256,created_at) VALUES(?,?,?,?,?,?,?)", (identifier, identity["workspace_id"], identity["user"]["id"], method, path, hashlib.sha256(body).hexdigest(), int(time.time())))
            if image_admission is not None:
                from app.controlplane.image_admission import record_image_admission
                record_image_admission(db,identifier,identity,image_admission)
        return identifier

    def audit_finish(self, identifier: str, status: int) -> None:
        """Complete an audit receipt once, preserving uncertain outcomes after interrupted requests."""
        with self.connect() as db:
            db.execute("UPDATE request_audit SET status=?,completed_at=? WHERE id=? AND completed_at IS NULL", (status, int(time.time()), identifier))
