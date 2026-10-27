"""Bounded public local-export API, with no executable, path or model input fields."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class ExportRequest(BaseModel):
    """Stable local intention key survives a lost HTTP response without duplicate work."""
    model_config = ConfigDict(extra='forbid')
    request_key: str = Field(min_length=1, max_length=128, pattern=r'.*\S.*')


class ExportCancel(BaseModel):
    """Explicit cancellation has no client-controlled state transition or encoder options."""
    model_config = ConfigDict(extra='forbid')


class ExportFileRead(BaseModel):
    """A verified ready artifact can only be downloaded through an owned content route."""
    kind: Literal['video', 'subtitles_srt', 'subtitles_vtt', 'package']
    name: str
    sha256: str
    size: int
    content_url: str


class ExportRead(BaseModel):
    """Common task status plus immutable provenance and safe ready artifact descriptors."""
    id: str
    project_id: str
    chapter_id: str
    publication_id: str
    publication_hash: str
    task_id: str
    state: Literal['queued', 'running', 'ready', 'failed', 'cancelled']
    reason: str
    attempts: int
    files: list[ExportFileRead]
    model_calls: Literal[0] = 0


class ExportCatalog(BaseModel):
    """Local tool availability is independent from model-account readiness."""
    enabled: bool
    reason: str
    exports: list[ExportRead]
