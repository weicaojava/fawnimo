"""Fixed-database tenant ownership guards for the single-host identity gateway.

The identity gateway chooses a server-owned worker; this module never chooses a
tenant from a request header. One worker, database, and file root have one fixed
workspace identity. This is application isolation, not an OS security sandbox.
"""
from __future__ import annotations

from contextlib import closing
import hmac
import json
from pathlib import Path
import re
import sqlite3


WORKSPACE_ID_PATTERN = re.compile(r"ws_[0-9a-f]{32}\Z")
MARKER_TABLE = "workspace_identity"
INTERNAL_TOKEN_HEADER = b"x-workspace-token"
READINESS_PATH = "/__workspace/ready"

# These tables contain schema versions or service-local concurrency leases only.
# New tables are private business data unless explicitly reviewed into this list.
INFRASTRUCTURE_TABLES = frozenset({
    MARKER_TABLE, "production_schema", "execution_schema", "reverse_schema",
    "benchmark_schema", "script_preparation_schema", "shot_plan_schema",
    "editing_schema", "edit_export_schema", "video_dna_schema",
    "video_understanding_schema", "execution_slots", "edit_export_slot",
    "transition_export_slot",
})


def validate_workspace_id(workspace_id: str) -> str:
    """Accept only opaque server-generated IDs, never filesystem path components."""
    if not isinstance(workspace_id, str) or not WORKSPACE_ID_PATTERN.fullmatch(workspace_id):
        raise ValueError("Invalid fixed workspace identity")
    return workspace_id


def _identifier(name: str) -> str:
    """Quote inspected SQLite identifiers without interpreting their text as SQL."""
    return '"' + name.replace('"', '""') + '"'


def _tables(connection: sqlite3.Connection) -> list[str]:
    """Enumerate private tables while excluding explicit version/lease infrastructure."""
    return [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ) if row[0] not in INFRASTRUCTURE_TABLES and not row[0].startswith("sqlite_")]


def _read_marker(connection: sqlite3.Connection, workspace_id: str) -> bool:
    """Refuse an existing marker that belongs to any other tenant or schema version."""
    found = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (MARKER_TABLE,)
    ).fetchone()
    if not found:
        return False
    try:
        rows = connection.execute(
            "SELECT singleton,workspace_id,version FROM workspace_identity"
        ).fetchall()
    except sqlite3.DatabaseError as error:
        raise ValueError("Invalid workspace database marker") from error
    if rows != [(1, workspace_id, 1)]:
        raise ValueError("Workspace database belongs to another identity or version")
    return True


def _require_empty_unclaimed(connection: sqlite3.Connection) -> None:
    """Do not silently give any existing business records to a new login or worker."""
    for table in _tables(connection):
        if connection.execute(f"SELECT 1 FROM {_identifier(table)} LIMIT 1").fetchone():
            raise ValueError("Unclaimed database contains existing business data; explicit offline migration required")


def preflight_workspace_boundary(database: Path, workspace_id: str) -> bool:
    """Check ownership before ORM migrations can touch an existing database.

    Empty or partially initialized new databases may be completed. Existing local
    user data without a marker must use a separate explicit migration procedure.
    Return whether a matching marker already exists, for file-root checks.
    """
    validate_workspace_id(workspace_id)
    database = Path(database).resolve()
    if not database.exists():
        return False
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        marked = _read_marker(connection, workspace_id)
        if not marked:
            _require_empty_unclaimed(connection)
        return marked


def _normalize_sql(value: str) -> str:
    """Compare our guard definitions across SQLite's harmless formatting changes."""
    return " ".join(value.strip().rstrip(";").split())


def _guard_sql(workspace_id: str, table: str) -> dict[str, str]:
    """Generate immutable marker and fixed-owner write guards with validated literals."""
    quoted = _identifier(table)
    if table == MARKER_TABLE:
        return {
            f"workspace_identity_no_{action.lower()}":
                f"CREATE TRIGGER {_identifier('workspace_identity_no_' + action.lower())} "
                f"BEFORE {action} ON {quoted} BEGIN SELECT RAISE(ABORT,'Workspace identity is immutable'); END"
            for action in ("UPDATE", "DELETE")
        }
    return {
        f"workspace_owner_{table}_{action.lower()}":
            f"CREATE TRIGGER {_identifier('workspace_owner_' + table + '_' + action.lower())} "
            f"BEFORE {action} ON {quoted} WHEN NEW.workspace_id IS NOT '{workspace_id}' "
            "BEGIN SELECT RAISE(ABORT,'Cross-workspace write rejected'); END"
        for action in ("INSERT", "UPDATE")
    }


def _check_trigger(connection: sqlite3.Connection, name: str, sql: str, *, create: bool) -> None:
    """Reject missing or changed guards rather than silently trusting a reused name."""
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (name,)
    ).fetchone()
    if row is None and create:
        connection.execute(sql)
        return
    if row is None or _normalize_sql(row[0]) != _normalize_sql(sql):
        raise ValueError("Workspace database ownership guard is missing or changed")


def _check_columns(connection: sqlite3.Connection, workspace_id: str, table: str) -> None:
    """Require a fixed non-null default and reject any records carrying another owner."""
    columns = {row[1]: row for row in connection.execute(f"PRAGMA table_info({_identifier(table)})")}
    column = columns.get("workspace_id")
    if column is None or column[3] != 1 or str(column[4]).strip() not in {
        f"'{workspace_id}'", f'"{workspace_id}"', f"('{workspace_id}')",
    }:
        raise ValueError("Workspace database requires a fixed, non-null owner column")
    if connection.execute(
        f"SELECT 1 FROM {_identifier(table)} WHERE workspace_id IS NOT ? LIMIT 1", (workspace_id,)
    ).fetchone():
        raise ValueError("Workspace database contains foreign ownership records")


def prepare_workspace_boundary(database: Path, workspace_id: str, *, allow_initialize: bool = False) -> None:
    """Add fixed ownership after supported ORM migrations, atomically and idempotently.

    Initializing an unmarked database is limited to empty business tables. This
    function is deliberately never called by the existing local launcher.
    """
    validate_workspace_id(workspace_id)
    database = Path(database).resolve(strict=True)
    with closing(sqlite3.connect(database.as_uri() + "?mode=rw", uri=True)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        try:
            connection.execute("BEGIN IMMEDIATE")
            if not _read_marker(connection, workspace_id):
                if not allow_initialize:
                    raise ValueError("Workspace database is not bound to an identity")
                _require_empty_unclaimed(connection)
                connection.execute(
                    "CREATE TABLE workspace_identity (singleton INTEGER PRIMARY KEY CHECK(singleton=1),"
                    f"workspace_id TEXT NOT NULL UNIQUE CHECK(workspace_id='{workspace_id}'),"
                    "version INTEGER NOT NULL CHECK(version=1))"
                )
                connection.execute("INSERT INTO workspace_identity VALUES(1,?,1)", (workspace_id,))
            for table in _tables(connection):
                columns = {row[1] for row in connection.execute(f"PRAGMA table_info({_identifier(table)})")}
                if "workspace_id" not in columns:
                    connection.execute(
                        f"ALTER TABLE {_identifier(table)} ADD COLUMN workspace_id TEXT NOT NULL "
                        f"DEFAULT '{workspace_id}' CHECK(workspace_id='{workspace_id}')"
                    )
                _check_columns(connection, workspace_id, table)
                for name, sql in _guard_sql(workspace_id, table).items():
                    _check_trigger(connection, name, sql, create=True)
            for name, sql in _guard_sql(workspace_id, MARKER_TABLE).items():
                _check_trigger(connection, name, sql, create=True)
            if connection.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("Workspace database has inconsistent references")
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def assert_workspace_boundary(database: Path, workspace_id: str) -> None:
    """Verify the fixed marker, every business owner column, and all write guards."""
    validate_workspace_id(workspace_id)
    database = Path(database).resolve(strict=True)
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        if not _read_marker(connection, workspace_id):
            raise ValueError("Workspace database is not bound to an identity")
        for table in _tables(connection):
            _check_columns(connection, workspace_id, table)
            for name, sql in _guard_sql(workspace_id, table).items():
                _check_trigger(connection, name, sql, create=False)
        for name, sql in _guard_sql(workspace_id, MARKER_TABLE).items():
            _check_trigger(connection, name, sql, create=False)


class TenantBoundaryMiddleware:
    """Require the gateway's private worker token before any application endpoint.

    The gateway must drop browser-supplied internal headers and supply its own.
    A workspace ID supplied by a caller never changes this worker's fixed scope.
    """

    def __init__(self, app, *, workspace_id: str, internal_token: str) -> None:
        """Fail startup on missing fixed identity or insufficient internal secret."""
        self.app = app
        self.workspace_id = validate_workspace_id(workspace_id)
        if not isinstance(internal_token, str) or len(internal_token) < 32 or len(internal_token) > 512 or not internal_token.isascii():
            raise ValueError("Invalid private workspace worker token")
        self.internal_token = internal_token.encode("ascii")

    async def __call__(self, scope, receive, send) -> None:
        """Guard HTTP and WebSocket traffic while allowing ASGI lifespan startup."""
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        tokens = [value for name, value in scope.get("headers", []) if name.lower() == INTERNAL_TOKEN_HEADER]
        accepted = len(tokens) == 1 and hmac.compare_digest(tokens[0], self.internal_token)
        if not accepted:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await self._respond(send, 403, {"code": 403, "message": "Private workspace worker", "data": None})
            return
        if scope["type"] == "http" and scope.get("path") == READINESS_PATH:
            if scope.get("method") != "GET":
                await self._respond(send, 405, {"code": 405, "message": "Method not allowed", "data": None})
            else:
                await self._respond(send, 200, {"workspace_id": self.workspace_id, "status": "ready"})
            return
        await self.app(scope, receive, send)

    @staticmethod
    async def _respond(send, status: int, content: dict) -> None:
        """Return a private, non-cacheable response without reflecting tokens or headers."""
        body = json.dumps(content, separators=(",", ":")).encode("utf-8")
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"),
        ]})
        await send({"type": "http.response.body", "body": body})
