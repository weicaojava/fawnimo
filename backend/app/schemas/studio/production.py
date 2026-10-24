"""Strict project production inputs; imported metadata is data, never executable configuration."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class VersionWrite(BaseModel):
    """Publish a bounded snapshot; all executable/provider options belong elsewhere."""
    model_config = ConfigDict(extra='forbid')
    description: str = Field('', max_length=16000)
    media_ids: list[str] = Field(default_factory=list, max_length=24)
    expected_revision: int = Field(0, ge=0)


class AssetWrite(VersionWrite):
    """Create an asset attached to the same existing project's legacy library."""
    kind: Literal['character','appearance','scene','prop','voice']
    name: str = Field(min_length=1, max_length=120)
    subject_id: str | None = None


class ArchiveWrite(BaseModel):
    """Explicit reversible archive operation, separate from immutable history."""
    archived: bool


class TakeWrite(BaseModel):
    """Adopt an imported media candidate; this endpoint cannot claim model generation."""
    model_config = ConfigDict(extra='forbid')
    shot_id: str
    media_id: str
    version_ids: list[str] = Field(min_length=1, max_length=24)
