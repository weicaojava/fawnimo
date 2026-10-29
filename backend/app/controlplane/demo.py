"""Create an original, editable seven-shot demo through the tenant's real HTTP API.

Reset means creating a new example with a new request key. Existing examples and
user edits are preserved; replay never repairs or overwrites a completed example.
No media, model, credentials, shared studio database or original user clip is used.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import time

import httpx
from fastapi import HTTPException

DEMO_VERSION = "original-light-island-v1"
REQUEST_KEY = re.compile(r"[A-Za-z0-9_-]{8,128}\Z")
LEASE_SECONDS = 60
MAX_REQUESTS_PER_WORKSPACE = 20


def _document(project_id: str) -> list[dict]:
    """Build only self-authored text and deterministic resource IDs for the current template."""
    chapter = project_id + "-chapter"
    story = [
        ("雾中的小岛", "透明小岛静置于深蓝桌面，岛中央的空心方块仍未亮起。", "ELS", "STATIC"),
        ("第一缕光", "一颗金色光点从画面左侧进入，在方块的左边停下。", "MS", "PAN"),
        ("轻轻触碰", "光点触碰方块，方块的第一条边线亮起。", "CU", "STATIC"),
        ("沿边行走", "金色光点沿方块外缘缓慢绕行，留下连续柔和的光线。", "MCU", "TRACK"),
        ("小岛回应", "方块亮起后，三枚小三角片依次发出淡蓝微光。", "MS", "DOLLY_OUT"),
        ("光的汇合", "金色光点回到方块上方，与三枚三角片的微光汇合。", "LS", "STATIC"),
        ("点亮小岛", "全岛保持温暖微光，镜头缓慢拉远，画面安静收束。", "ELS", "DOLLY_OUT"),
    ]
    provenance = "纯自作几何光影练习文本；未使用用户原片或第三方素材；没有模型调用或已生成成片。"
    description = provenance + "示例可自由编辑；重新建立示例会新建工程，保留所有旧示例和编辑。"
    project = {"id": project_id, "name": "体验示例 · 点亮小岛", "description": description,
               "style": "动漫3D", "visual_style": "动漫", "seed": 20261029,
               "unify_style": True, "default_video_ratio": "9:16"}
    raw = "《点亮小岛》\n" + provenance + "\n\n" + "\n".join(f"{i}. {title}：{text}" for i, (title, text, _, _) in enumerate(story, 1))
    steps = [
        {"collection": "projects", "body": project},
        {"collection": "chapters", "body": {"id": chapter, "project_id": project_id, "index": 1,
            "title": "第一章 · 光的旅程", "summary": provenance, "raw_text": raw,
            "condensed_text": "金色光点点亮几何小岛。", "storyboard_count": 7, "status": "draft"}},
    ]
    for index, (title, text, camera, movement) in enumerate(story, 1):
        shot = f"{project_id}-shot-{index}"
        steps.append({"collection": "shots", "body": {"id": shot, "chapter_id": chapter,
            "index": index, "title": title, "status": "pending", "skip_extraction": False,
            "script_excerpt": text}})
        prefix = f"原创几何光影练习，竖屏9:16，透明小岛、空心方块与金色光点，镜头{index}：{text}"
        steps.append({"collection": "shot-details", "body": {"id": shot, "camera_shot": camera,
            "angle": "EYE_LEVEL", "movement": movement, "duration": 4,
            "atmosphere": "安静、克制、暖光与冷蓝背景", "mood_tags": ["安静", "希望"],
            "action_beats": [text], "has_bgm": False,
            "first_frame_prompt": prefix + "。首帧：展示动作发生前的稳定构图。",
            "last_frame_prompt": prefix + "。尾帧：展示动作完成后的稳定构图。",
            "key_frame_prompt": prefix + "。关键帧：保留方块、光点和小岛的空间关系。"}})
    return steps


def _schema(store) -> None:
    """Add a control-only idempotency ledger without touching any tenant database."""
    with store.connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS demo_requests(
                workspace_id TEXT NOT NULL REFERENCES workspaces(id),
                submitted_by TEXT NOT NULL REFERENCES users(id), request_key TEXT NOT NULL,
                request_hash TEXT NOT NULL, template_version TEXT NOT NULL, project_id TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL CHECK(state IN ('pending','succeeded','failedunknown')),
                completed_steps INTEGER NOT NULL DEFAULT 0, lease_token TEXT,
                lease_until INTEGER, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
                PRIMARY KEY(workspace_id,submitted_by,request_key));
            CREATE TRIGGER IF NOT EXISTS demo_request_identity_immutable
                BEFORE UPDATE OF workspace_id,submitted_by,request_key,request_hash,template_version,project_id,created_at ON demo_requests
                BEGIN SELECT RAISE(ABORT,'Immutable demo identity'); END;
            CREATE TRIGGER IF NOT EXISTS demo_request_no_delete BEFORE DELETE ON demo_requests
                BEGIN SELECT RAISE(ABORT,'Immutable demo request'); END;
        """)


def _member(db, workspace: str, actor: str) -> None:
    """Recheck the real member role rather than trusting a caller-supplied role field."""
    row = db.execute("""SELECT m.role FROM memberships m JOIN users u ON u.id=m.user_id
        WHERE m.workspace_id=? AND m.user_id=? AND u.disabled=0""", (workspace, actor)).fetchone()
    if row is None or row["role"] not in {"owner", "editor"}:
        raise HTTPException(403, "当前身份没有创建示例的权限")


def _claim(store, workspace: str, actor: str, key: str, digest: str, project_id: str) -> dict:
    """Atomically bind one request to one project, allowing only fenced same-key recovery."""
    now, token = int(time.time()), secrets.token_hex(24)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        _member(db, workspace, actor)
        row = db.execute("SELECT * FROM demo_requests WHERE workspace_id=? AND submitted_by=? AND request_key=?", (workspace, actor, key)).fetchone()
        if row is not None:
            if row["request_hash"] != digest or row["template_version"] != DEMO_VERSION or row["project_id"] != project_id:
                raise HTTPException(409, "原示例请求的版本或内容不一致，请保留原标识核对，不能覆盖旧工程")
            if row["state"] == "succeeded":
                return dict(row)
            if row["lease_token"] and (row["lease_until"] or 0) > now:
                raise HTTPException(409, "此示例请求正在处理，请稍后使用同一请求标识重试")
            changed = db.execute("""UPDATE demo_requests SET state='pending',lease_token=?,lease_until=?,updated_at=?
                WHERE workspace_id=? AND submitted_by=? AND request_key=? AND state IN ('pending','failedunknown')
                AND (lease_token IS NULL OR lease_until<=?)""", (token, now + LEASE_SECONDS, now, workspace, actor, key, now)).rowcount
            if changed != 1:
                raise HTTPException(409, "示例请求已由其他执行器接续，请核对原请求")
        else:
            count = db.execute("SELECT count(*) FROM demo_requests WHERE workspace_id=?", (workspace,)).fetchone()[0]
            if count >= MAX_REQUESTS_PER_WORKSPACE:
                raise HTTPException(429, "此空间已达到20次示例建立上限；旧工程和原请求仍保留，请继续编辑已有示例")
            db.execute("""INSERT INTO demo_requests(workspace_id,submitted_by,request_key,request_hash,template_version,
                project_id,state,lease_token,lease_until,created_at,updated_at) VALUES(?,?,?,?,?,?,'pending',?,?,?,?)""",
                (workspace, actor, key, digest, DEMO_VERSION, project_id, token, now + LEASE_SECONDS, now, now))
        return dict(db.execute("SELECT * FROM demo_requests WHERE workspace_id=? AND submitted_by=? AND request_key=?", (workspace, actor, key)).fetchone())


def _progress(store, row: dict, completed: int, *, succeeded: bool = False) -> None:
    """Fence every confirmed step and final success by a live lease and current membership."""
    now = int(time.time())
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        _member(db, row["workspace_id"], row["submitted_by"])
        changed = db.execute("""UPDATE demo_requests SET completed_steps=?,updated_at=?,state=?,lease_until=?,lease_token=?
            WHERE workspace_id=? AND submitted_by=? AND request_key=? AND state='pending'
            AND lease_token=? AND lease_until>?""", (completed, now, "succeeded" if succeeded else "pending",
            None if succeeded else now + LEASE_SECONDS, None if succeeded else row["lease_token"],
            row["workspace_id"], row["submitted_by"], row["request_key"], row["lease_token"], now)).rowcount
        if changed != 1:
            raise HTTPException(409, "示例执行租约已失效，请用原请求标识核对")


def _uncertain(store, row: dict) -> None:
    """Preserve partial data and release only this attempt; never erase unknown outcomes."""
    with store.connect() as db:
        db.execute("""UPDATE demo_requests SET state='failedunknown',lease_token=NULL,lease_until=NULL,updated_at=?
            WHERE workspace_id=? AND submitted_by=? AND request_key=? AND state='pending' AND lease_token=?""",
            (int(time.time()), row["workspace_id"], row["submitted_by"], row["request_key"], row["lease_token"]))


async def _send(client: httpx.AsyncClient, method: str, path: str, body: dict | None = None) -> httpx.Response:
    """Send one bounded internal request without HTTP retries or redirects."""
    return await client.request(method, path, json=body) if body is not None else await client.request(method, path)


def _matches(response: httpx.Response, expected: dict) -> bool:
    """Compare the actual persisted fields; ignore server-computed statistics and timestamps."""
    try:
        actual = response.json()["data"]
        return isinstance(actual, dict) and all(actual.get(key) == value for key, value in expected.items())
    except (ValueError, KeyError, TypeError):
        return False


async def _ensure(client: httpx.AsyncClient, step: dict, *, confirmed: bool) -> None:
    """Read before create, resolving lost POST replies by deterministic ID without overwrites."""
    body = step["body"]
    collection = "/api/v1/studio/" + step["collection"]
    detail = collection + "/" + body["id"]
    response = await _send(client, "GET", detail)
    if response.status_code == 404 and not confirmed:
        created = await _send(client, "POST", collection, body)
        if created.status_code not in {200, 201, 409}:
            raise HTTPException(502, "示例保存未能确认，请保留原请求标识重试核对")
        response = await _send(client, "GET", detail)
    if response.status_code != 200 or not _matches(response, body):
        raise HTTPException(409, "原示例的内容已被修改、删除或尚不能确认；已保留所有内容，不会覆盖，请核对原请求")


def _result(row: dict, *, replayed: bool) -> dict:
    """Return safe navigation and explicit example provenance instead of model-quality claims."""
    project = row["project_id"]
    return {"project_id": project, "chapter_id": project + "-chapter", "workspace_id": row["workspace_id"],
            "template_version": row["template_version"], "request_key": row["request_key"], "state": "succeeded",
            "entry": f"/projects/{project}?tab=chapters", "shot_count": 7, "editable_prompt_count": 21,
            "replayed": replayed, "previous_examples_preserved": True, "external_model_calls": 0,
            "provenance": "original_text_only", "model_quality_qualified": False,
            "complete_film_qualified": False,
            "notice": "纯自作文字示例，7个待确认镜头与21条可编辑提示词；没有生成媒体。新建示例保留旧工程与编辑。"}


async def create_demo(store, pool, identity: dict, request_key: str) -> dict:
    """Create or resume the authenticated space's demo; a new key creates a fresh example.

    Identity must come from the gateway's current session. The service independently
    rechecks membership and never accepts workspace, actor, paths or template content
    from the HTTP body. After a lost reply, callers must reuse the original key.
    """
    if not isinstance(request_key, str) or not REQUEST_KEY.fullmatch(request_key):
        raise HTTPException(422, "示例请求标识需为8至128位字母、数字、下划线或短横线")
    try:
        workspace, actor = identity["workspace_id"], identity["user"]["id"]
    except (KeyError, TypeError):
        raise HTTPException(401, "请先登录") from None
    # Scope is part of the deterministic ID even if two accounts reuse one request key.
    identity_hash = hashlib.sha256(json.dumps([workspace, actor, request_key], separators=(",", ":")).encode()).hexdigest()[:32]
    project = "demo-" + identity_hash
    steps = _document(project)
    digest = hashlib.sha256(json.dumps({"version": DEMO_VERSION, "steps": steps}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    await asyncio.to_thread(_schema, store)
    row = await asyncio.to_thread(_claim, store, workspace, actor, request_key, digest, project)
    if row["state"] == "succeeded":
        return _result(row, replayed=True)
    try:
        worker = await pool.get(workspace)
        if worker.workspace_id != workspace:
            raise HTTPException(503, "工作空间执行器身份不匹配，示例未继续写入")
        headers = {"X-Workspace-Token": worker.token, "X-Workspace-Id": workspace, "X-Identity-Id": actor}
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{worker.port}", headers=headers,
                                     timeout=httpx.Timeout(20, connect=3), trust_env=False, follow_redirects=False) as client:
            for index, step in enumerate(steps):
                await asyncio.to_thread(_progress, store, row, row["completed_steps"])
                await _ensure(client, step, confirmed=index < row["completed_steps"])
                completed = max(row["completed_steps"], index + 1)
                await asyncio.to_thread(_progress, store, row, completed)
                row["completed_steps"] = completed
        await asyncio.to_thread(_progress, store, row, len(steps), succeeded=True)
        return _result(row, replayed=False)
    except BaseException as error:
        await asyncio.shield(asyncio.to_thread(_uncertain, store, row))
        if isinstance(error, (HTTPException, asyncio.CancelledError)):
            raise
        raise HTTPException(502, "示例响应中断，已保留进度；请使用原请求标识核对，不要重复新建") from None
