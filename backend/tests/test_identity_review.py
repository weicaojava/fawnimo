"""Independent normal regressions for gateway principals and transport cleanup.

All transport interactions use local in-memory fixtures; no application worker,
private data directory, model endpoint, or external network is accessed.
"""
import asyncio
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.controlplane import gateway


PASSWORD = "independent-fixture-password-2026"


class FixturePool:
    """Return a fixed fake worker to inspect gateway admission and trusted headers."""

    def __init__(self):
        """Keep requests observable without starting any process or network service."""
        self.selected = []

    async def get(self, workspace_id):
        """Record the trusted scope selected by the identity store."""
        self.selected.append(workspace_id)
        return SimpleNamespace(port=19001, token="server-private-fixture-token")

    async def close(self):
        """There are no process resources in this in-memory fixture."""


class FixtureResponse:
    """Represent a response whose socket cleanup must happen on every completion path."""

    status_code = 201
    headers = {"content-type": "application/json"}

    def __init__(self):
        """Expose whether the gateway releases the upstream response."""
        self.closed = False

    async def aiter_raw(self):
        """Return ordinary response bytes without creating a socket."""
        yield b'{"ok":true}'

    async def aclose(self):
        """Observe cleanup independent of the gateway's implementation details."""
        self.closed = True


class FixtureClient:
    """Model the HTTP client's cancellation, success, and close contracts in memory."""

    def __init__(self, *, cancel=False):
        """Keep cleanup observations separate from response content."""
        self.cancel, self.closed = cancel, False
        self.response = FixtureResponse()
        self.request = None

    def build_request(self, method, target, **kwargs):
        """Retain the gateway's destination and header choices for ordinary requests."""
        self.request = SimpleNamespace(method=method, target=target, **kwargs)
        return self.request

    async def send(self, request, **kwargs):
        """Simulate a browser disconnect while request headers are still pending."""
        if self.cancel:
            raise asyncio.CancelledError()
        return self.response

    async def aclose(self):
        """Observe release even when no response stream was returned to the browser."""
        self.closed = True


def request_for(app, token, identity, *, method="GET", extra_headers=None):
    """Build an authenticated same-origin ASGI request with trusted current-tab context."""
    headers = {
        "host": "studio.local", "cookie": "studio_session=" + token,
        "x-workspace-id": identity["workspace_id"], "x-identity-id": identity["user"]["id"],
        **(extra_headers or {}),
    }

    async def receive():
        """Supply one finite ordinary JSON body to the gateway's bounded reader."""
        return {"type": "http.request", "body": b"{}", "more_body": False}

    return Request({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                    "method": method, "scheme": "http", "path": "/api/v1/studio/projects",
                    "raw_path": b"/api/v1/studio/projects", "query_string": b"",
                    "root_path": "", "headers": [(key.encode(), value.encode()) for key, value in headers.items()],
                    "client": ("127.0.0.1", 12345), "server": ("studio.local", 80), "app": app}, receive)


def setup_identity(tmp_path):
    """Return a private control store, fake pool, and current authenticated principal."""
    pool = FixturePool()
    app = gateway.create_gateway(tmp_path, workers=pool, secure_cookies=False)
    store = app.state.identity_store
    store.provision("review.user", PASSWORD)
    token = store.login("review.user", PASSWORD, "local")
    endpoint = next(route.endpoint for route in app.routes if route.path == "/api/v1/{path:path}")
    return app, pool, store, token, store.current(token), endpoint


@pytest.mark.asyncio
async def test_cancel_before_response_releases_transport(tmp_path, monkeypatch):
    """A browser disconnect during upstream send cannot retain an open HTTP client."""
    app, pool, store, token, identity, endpoint = setup_identity(tmp_path)
    client = FixtureClient(cancel=True)
    monkeypatch.setattr(gateway.httpx, "AsyncClient", lambda **kwargs: client)
    with pytest.raises(asyncio.CancelledError):
        await endpoint("studio/projects", request_for(app, token, identity))
    assert client.closed


@pytest.mark.asyncio
async def test_receipt_completion_failure_releases_upstream(tmp_path, monkeypatch):
    """A control-database completion error cannot leak an already received media connection."""
    app, pool, store, token, identity, endpoint = setup_identity(tmp_path)
    client = FixtureClient()
    monkeypatch.setattr(gateway.httpx, "AsyncClient", lambda **kwargs: client)

    def failed_receipt(*args):
        """Simulate a local disk error after a request outcome has become uncertain."""
        raise OSError("fixture disk unavailable")

    monkeypatch.setattr(store, "audit_finish", failed_receipt)
    with pytest.raises(OSError):
        await endpoint("studio/projects", request_for(app, token, identity, method="POST"))
    assert client.closed and client.response.closed
    with store.connect() as db:
        assert db.execute("SELECT status FROM request_audit").fetchone()[0] is None


@pytest.mark.asyncio
async def test_forwarded_principal_and_receipt_come_from_current_identity(tmp_path, monkeypatch):
    """Browser token/receipt headers are never reused as worker credentials or actor evidence."""
    app, pool, store, token, identity, endpoint = setup_identity(tmp_path)
    client = FixtureClient()
    monkeypatch.setattr(gateway.httpx, "AsyncClient", lambda **kwargs: client)
    request = request_for(app, token, identity, method="POST", extra_headers={
        "x-workspace-token": "browser-value", "x-submission-receipt": "browser-value",
    })
    response = await endpoint("studio/projects", request)
    content = b"".join([chunk async for chunk in response.body_iterator])
    assert content == b'{"ok":true}' and client.closed and client.response.closed
    assert pool.selected == [identity["workspace_id"]]
    headers = client.request.headers
    assert headers["X-Workspace-Token"] == "server-private-fixture-token"
    assert headers["X-Workspace-Id"] == identity["workspace_id"]
    assert headers["X-Identity-Id"] == identity["user"]["id"]
    assert headers["X-Submission-Receipt"] != "browser-value"
    with store.connect() as db:
        row = db.execute("SELECT * FROM request_audit").fetchone()
        assert row["workspace_id"] == identity["workspace_id"] and row["submitted_by"] == identity["user"]["id"]
        assert row["status"] == 201


@pytest.mark.asyncio
async def test_viewer_write_is_rejected_before_worker_selection(tmp_path):
    """A membership role change is checked on the next request even with an old cookie."""
    app, pool, store, token, identity, endpoint = setup_identity(tmp_path)
    with store.connect() as db:
        db.execute("UPDATE memberships SET role='viewer' WHERE user_id=?", (identity["user"]["id"],))
    with pytest.raises(HTTPException) as error:
        await endpoint("studio/projects", request_for(app, token, identity, method="POST"))
    assert error.value.status_code == 403 and pool.selected == []


@pytest.mark.asyncio
async def test_native_media_scope_remains_bound_to_the_original_tab(tmp_path, monkeypatch):
    """Same-origin image/video requests cannot silently follow a replacement browser login."""
    app, pool, store, token_a, identity_a, endpoint = setup_identity(tmp_path)
    store.provision("review.second", PASSWORD)
    token_b = store.login("review.second", PASSWORD, "local")
    identity_b = store.current(token_b)
    client = FixtureClient()
    monkeypatch.setattr(gateway.httpx, "AsyncClient", lambda **kwargs: client)

    def native_request(token, identity, scope_values):
        """Native media tags send cookies and URL scope, but cannot add API headers."""
        request = request_for(app, token, identity, extra_headers={"range": "bytes=0-4"})
        request.scope["headers"] = [(name, value) for name, value in request.scope["headers"]
                                    if name not in {b"x-workspace-id", b"x-identity-id"}]
        request.scope["query_string"] = urlencode(scope_values).encode()
        return request

    old_scope = {"__studio_user": identity_a["user"]["id"], "__studio_workspace": identity_a["workspace_id"]}
    with pytest.raises(HTTPException) as error:
        await endpoint("studio/files/shared-example-id/download", native_request(token_b, identity_b, old_scope))
    assert error.value.status_code == 409 and pool.selected == []
    with pytest.raises(HTTPException) as error:
        await endpoint("studio/files/shared-example-id/download", native_request(token_b, identity_b, {}))
    assert error.value.status_code == 409 and pool.selected == []
    current_scope = {"__studio_user": identity_b["user"]["id"], "__studio_workspace": identity_b["workspace_id"]}
    response = await endpoint("studio/files/shared-example-id/download", native_request(token_b, identity_b, current_scope))
    content = b"".join([chunk async for chunk in response.body_iterator])
    assert content == b'{"ok":true}' and pool.selected == [identity_b["workspace_id"]]
    assert client.request.headers["range"] == "bytes=0-4"
    assert client.closed and client.response.closed
