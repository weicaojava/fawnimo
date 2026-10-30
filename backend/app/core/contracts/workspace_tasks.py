"""Read-only lightweight task-center summaries, intentionally excluding execution context and credentials."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

WorkspaceTaskKind = Literal['agent_workflow', 'model_connection_test', 'video_understanding', 'asset_image_generation', 'script_candidate']
WorkspaceTaskStatus = Literal['pending', 'running', 'succeeded', 'failed', 'cancelled']


class WorkspaceTaskSummary(BaseModel):
    """Only generic state, real clocks and a server-built safe return target belong in the global panel."""
    model_config = ConfigDict(extra='forbid')
    id: str
    kind: WorkspaceTaskKind
    title: str
    status: WorkspaceTaskStatus
    progress: int | None = Field(default=None, ge=0, le=100)
    created_at_ts: float | None
    started_at_ts: float | None
    finished_at_ts: float | None
    return_path: str
    return_note: str | None = None


class WorkspaceTaskSource(BaseModel):
    """A failed source remains explicit so clients keep its last-known items instead of silently showing zero."""
    kind: WorkspaceTaskKind
    available: bool
    message: str


class WorkspaceTaskFeed(BaseModel):
    """Bound each source and the final feed; this is a recent summary, never an all-history count."""
    items: list[WorkspaceTaskSummary]
    sources: list[WorkspaceTaskSource]
    complete: bool
    limit: int
    per_source_limit: int
    read_at_ts: float

