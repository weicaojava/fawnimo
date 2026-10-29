"""Exercise demo creation against real isolated tenant processes and interrupted replies."""
import asyncio
import sqlite3
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from app.controlplane import demo
from app.controlplane.store import IdentityStore
from app.controlplane.workers import WorkspaceWorkers

PASSWORD = "demo-isolated-fixture-password"


def identity(store, name):
    """Create a disposable test identity with a real trusted membership/session."""
    store.provision(name, PASSWORD)
    return store.current(store.login(name, PASSWORD, "fixture-local"))


async def client_for(pool, current):
    """Use only the pool's private loopback worker and server-generated scope headers."""
    worker = await pool.get(current["workspace_id"])
    return httpx.AsyncClient(base_url=f"http://127.0.0.1:{worker.port}", trust_env=False,
        headers={"X-Workspace-Token": worker.token, "X-Workspace-Id": current["workspace_id"],
                 "X-Identity-Id": current["user"]["id"]}, timeout=20)


def ledger(store, key):
    """Read the isolated control ledger to distinguish unknown outcome from successful replay."""
    with store.connect() as db:
        return dict(db.execute("SELECT * FROM demo_requests WHERE request_key=?", (key,)).fetchone())


class NoPool:
    """Invalid identity or request keys must be rejected before a runtime starts."""
    async def get(self, workspace):
        """Trap accidental worker admission in read-only negative tests."""
        raise AssertionError("Invalid request reached a tenant runtime")


@pytest.mark.parametrize("key", ["", "tiny", "../private", "space in key", "x" * 129, None])
@pytest.mark.asyncio
async def test_demo_request_key_is_bounded_and_not_a_path(tmp_path, key):
    """No caller-selected key becomes a filesystem path or a mutable project identifier."""
    store = IdentityStore(tmp_path)
    current = identity(store, "demo.a")
    with pytest.raises(HTTPException) as error:
        await demo.create_demo(store, NoPool(), current, key)
    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_demo_uses_current_database_membership_not_claimed_role(tmp_path):
    """Forged role/scope fields cannot start writes in another member's workspace."""
    store = IdentityStore(tmp_path)
    a, b = identity(store, "demo.a"), identity(store, "demo.b")
    forged = {**a, "workspace_id": b["workspace_id"], "role": "owner"}
    with pytest.raises(HTTPException) as error:
        await demo.create_demo(store, NoPool(), forged, "demo-request-001")
    assert error.value.status_code == 403
    with store.connect() as db:
        db.execute("UPDATE memberships SET role='viewer' WHERE user_id=?", (a["user"]["id"],))
    with pytest.raises(HTTPException) as error:
        await demo.create_demo(store, NoPool(), a, "demo-request-001")
    assert error.value.status_code == 403
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM demo_requests").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_demo_refuses_mismatched_worker_and_preserves_uncertain_receipt(tmp_path):
    """The pool's fixed worker identity must equal the authenticated space before HTTP writes."""
    store = IdentityStore(tmp_path)
    current = identity(store, "demo.a")

    class WrongPool:
        """Represent a broken pool without creating or accessing another real worker."""
        async def get(self, workspace):
            """Return a mismatched identity; service must stop before reading a port/token."""
            return SimpleNamespace(workspace_id="ws_" + "0" * 32)

    with pytest.raises(HTTPException) as error:
        await demo.create_demo(store, WrongPool(), current, "demo-request-001")
    assert error.value.status_code == 503
    row = ledger(store, "demo-request-001")
    assert row["state"] == "failedunknown" and row["completed_steps"] == 0
    assert row["lease_token"] is None


@pytest.mark.asyncio
async def test_real_demo_creation_reset_replay_isolation_and_lost_response(tmp_path, monkeypatch):
    """Real HTTP and SQLite prove editable content, retained edits and no duplicate project after interruption."""
    store = IdentityStore(tmp_path / "identity")
    a, b = identity(store, "demo.a"), identity(store, "demo.b")
    pool = WorkspaceWorkers(tmp_path / "workspaces", capacity=2)
    try:
        first = await demo.create_demo(store, pool, a, "demo-request-001")
        assert first["shot_count"] == 7 and first["editable_prompt_count"] == 21
        assert first["provenance"] == "original_text_only" and first["external_model_calls"] == 0
        assert first["model_quality_qualified"] is False and first["complete_film_qualified"] is False
        row = ledger(store, "demo-request-001")
        assert row["state"] == "succeeded" and row["completed_steps"] == 16 and row["lease_token"] is None
        assert row["submitted_by"] == a["user"]["id"] and row["workspace_id"] == a["workspace_id"]
        async with await client_for(pool, a) as client:
            chapter = (await client.get("/api/v1/studio/chapters/" + first["chapter_id"])).json()["data"]
            assert chapter["shot_count"] == 7
            assert "未使用用户原片" in chapter["raw_text"]
            response = await client.get("/api/v1/studio/shots", params={"chapter_id": first["chapter_id"], "page_size": 100})
            shots = response.json()["data"]["items"]
            assert len(shots) == 7 and all(shot["status"] == "pending" for shot in shots)
            for shot in shots:
                detail = (await client.get("/api/v1/studio/shot-details/" + shot["id"])).json()["data"]
                assert all(detail[field] for field in ("first_frame_prompt", "last_frame_prompt", "key_frame_prompt"))
            path = "/api/v1/studio/projects/" + first["project_id"]
            assert (await client.patch(path, json={"name": "我的示例编辑要保留"})).status_code == 200
            detail_path = "/api/v1/studio/shot-details/" + shots[0]["id"]
            assert (await client.patch(detail_path, json={"key_frame_prompt": "我的手工提示词"})).status_code == 200
            replay = await demo.create_demo(store, pool, a, "demo-request-001")
            assert replay["project_id"] == first["project_id"] and replay["replayed"] is True
            assert (await client.get(path)).json()["data"]["name"] == "我的示例编辑要保留"
            assert (await client.get(detail_path)).json()["data"]["key_frame_prompt"] == "我的手工提示词"
            reset = await demo.create_demo(store, pool, a, "demo-request-002")
            assert reset["project_id"] != first["project_id"] and reset["previous_examples_preserved"]
            assert (await client.get(path)).json()["data"]["name"] == "我的示例编辑要保留"
            assert (await client.get("/api/v1/studio/projects")).json()["data"]["pagination"]["total"] == 2

            original_send, interrupted = demo._send, False

            async def lose_first_project_reply(client, method, route, body=None):
                """Lose a response after the real server committed; do not replace the real API."""
                nonlocal interrupted
                response = await original_send(client, method, route, body)
                if not interrupted and method == "POST" and route.endswith("/projects"):
                    interrupted = True
                    raise httpx.ReadTimeout("synthetic lost reply after actual commit")
                return response

            monkeypatch.setattr(demo, "_send", lose_first_project_reply)
            with pytest.raises(HTTPException) as error:
                await demo.create_demo(store, pool, a, "demo-request-003")
            assert error.value.status_code == 502 and interrupted
            uncertain = ledger(store, "demo-request-003")
            assert uncertain["state"] == "failedunknown" and uncertain["completed_steps"] == 0
            assert (await client.get("/api/v1/studio/projects/" + uncertain["project_id"])).status_code == 200
            monkeypatch.setattr(demo, "_send", original_send)
            resumed = await demo.create_demo(store, pool, a, "demo-request-003")
            assert resumed["project_id"] == uncertain["project_id"]
            assert (await client.get("/api/v1/studio/projects")).json()["data"]["pagination"]["total"] == 3
            assert ledger(store, "demo-request-003")["state"] == "succeeded"

            monkeypatch.setattr(demo, "DEMO_VERSION", "changed-template-version")
            with pytest.raises(HTTPException) as error:
                await demo.create_demo(store, pool, a, "demo-request-003")
            assert error.value.status_code == 409
            monkeypatch.setattr(demo, "DEMO_VERSION", uncertain["template_version"])

            concurrent = await asyncio.gather(demo.create_demo(store, pool, a, "demo-request-concurrent"),
                                             demo.create_demo(store, pool, a, "demo-request-concurrent"), return_exceptions=True)
            assert sum(isinstance(result, dict) for result in concurrent) == 1
            rejected = next(result for result in concurrent if isinstance(result, HTTPException))
            assert rejected.status_code == 409
            assert (await client.get("/api/v1/studio/projects")).json()["data"]["pagination"]["total"] == 4

        second = await demo.create_demo(store, pool, b, "demo-request-001")
        assert second["project_id"] != first["project_id"]
        async with await client_for(pool, b) as client:
            assert (await client.get("/api/v1/studio/projects/" + first["project_id"])).status_code == 404
            assert (await client.get("/api/v1/studio/projects")).json()["data"]["pagination"]["total"] == 1
        monkeypatch.setattr(demo, "MAX_REQUESTS_PER_WORKSPACE", 1)
        assert (await demo.create_demo(store, pool, b, "demo-request-001"))["replayed"] is True
        with pytest.raises(HTTPException) as error:
            await demo.create_demo(store, pool, b, "new-request-over-cap")
        assert error.value.status_code == 429
        with store.connect() as db:
            with pytest.raises(sqlite3.IntegrityError):
                db.execute("UPDATE demo_requests SET project_id='forged-project'")
        for current in (a, b):
            database = pool.root / current["workspace_id"] / "studio.db"
            with sqlite3.connect(database) as db:
                for table in ("agent_execution_runs", "asset_image_generation_jobs", "video_understanding_jobs", "execution_jobs"):
                    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                        assert db.execute('SELECT count(*) FROM "' + table + '"').fetchone()[0] == 0
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_partial_demo_edits_and_confirmed_deletions_are_never_repaired(tmp_path, monkeypatch):
    """Recovery stops on user changes and never recreates a previously confirmed deleted project."""
    store = IdentityStore(tmp_path / "identity")
    current = identity(store, "demo.a")
    pool = WorkspaceWorkers(tmp_path / "workspaces", capacity=1)
    original_send = demo._send
    try:
        lose_collection = "projects"
        interrupted = False

        async def interrupted_after_commit(client, method, route, body=None):
            """Inject a real response loss after a selected API write has already committed."""
            nonlocal interrupted
            response = await original_send(client, method, route, body)
            if not interrupted and method == "POST" and route.endswith("/" + lose_collection):
                interrupted = True
                raise httpx.ReadTimeout("synthetic reply interruption")
            return response

        monkeypatch.setattr(demo, "_send", interrupted_after_commit)
        with pytest.raises(HTTPException) as error:
            await demo.create_demo(store, pool, current, "partial-edit-request")
        assert error.value.status_code == 502
        edited = ledger(store, "partial-edit-request")
        async with await client_for(pool, current) as client:
            path = "/api/v1/studio/projects/" + edited["project_id"]
            assert (await client.patch(path, json={"name": "部分示例的用户编辑"})).status_code == 200
            monkeypatch.setattr(demo, "_send", original_send)
            with pytest.raises(HTTPException) as error:
                await demo.create_demo(store, pool, current, "partial-edit-request")
            assert error.value.status_code == 409
            assert (await client.get(path)).json()["data"]["name"] == "部分示例的用户编辑"
            assert (await client.get("/api/v1/studio/projects")).json()["data"]["pagination"]["total"] == 1

            lose_collection, interrupted = "chapters", False
            monkeypatch.setattr(demo, "_send", interrupted_after_commit)
            with pytest.raises(HTTPException) as error:
                await demo.create_demo(store, pool, current, "partial-delete-request")
            assert error.value.status_code == 502
            deleted = ledger(store, "partial-delete-request")
            assert deleted["completed_steps"] == 1
            deleted_path = "/api/v1/studio/projects/" + deleted["project_id"]
            assert (await client.delete(deleted_path)).status_code == 200
            monkeypatch.setattr(demo, "_send", original_send)
            with pytest.raises(HTTPException) as error:
                await demo.create_demo(store, pool, current, "partial-delete-request")
            assert error.value.status_code == 409
            assert (await client.get(deleted_path)).status_code == 404
            assert ledger(store, "partial-delete-request")["state"] == "failedunknown"
    finally:
        await pool.close()

