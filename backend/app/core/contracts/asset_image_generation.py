"""Explicit single-image base64 text-to-image jobs; no reference or URL-result protocol is implied."""
from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

AssetImageKind = Literal['actor', 'costume', 'scene', 'prop']


class ImageModelChoice(BaseModel):
    """Expose configuration guidance without URLs, model parameters or credentials."""
    id: str
    name: str
    configured: bool
    message: str
    priced: bool = False
    max_call_minor: int | None = None
    price_fingerprint: str | None = None


class AssetImageAvailability(BaseModel):
    """Distinguish this limited image protocol from legacy Film and video generation."""
    available: bool
    message: str
    default_model_id: str | None
    models: list[ImageModelChoice]
    protocol: Literal['openai_b64_v1'] = 'openai_b64_v1'
    supports_references: Literal[False] = False
    real_model_qualified: Literal[False] = False


class AssetImageSlotRead(BaseModel):
    """A compare-and-swap token for one actual asset-owned image slot."""
    id: int
    file_id: str | None
    updated_at: datetime
    view_angle: str
    quality_level: str

    @field_validator('updated_at')
    @classmethod
    def utc_clock(cls, value):
        """Make SQLite's UTC clock explicit for an exact later adoption comparison."""
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class AssetImageSubmit(BaseModel):
    """Authorize one image, rejecting references and hidden provider routing overrides."""
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    request_key: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1,max_length=64)
    expected_price_fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    model_id: str = Field(min_length=1, max_length=64)
    prompt: str = Field(min_length=1, max_length=8000)
    size: Literal['256x256', '512x512', '1024x1024'] = '1024x1024'
    confirm_model_call: Literal[True]


class AssetImageRetry(BaseModel):
    """Create a distinct attempt after acknowledging uncertain remote charges."""
    model_config = ConfigDict(extra='forbid')
    request_key: str = Field(min_length=1, max_length=128)
    expected_price_fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    acknowledge_duplicate_charge: Literal[True]


class AssetImageAdopt(BaseModel):
    """Require a separately confirmed, exact slot snapshot before changing its image."""
    model_config = ConfigDict(extra='forbid')
    request_key: str = Field(min_length=1, max_length=128)
    expected_file_id: str | None = Field(..., max_length=64)
    expected_updated_at: datetime
    confirm_replace: Literal[True]


class AssetImageJobRead(BaseModel):
    """Return candidate identity and verified bytes metadata, never local paths or credentials."""
    model_config = ConfigDict(from_attributes=True)
    id: str
    asset_kind: AssetImageKind
    asset_id: str
    slot_id: int
    model_id: str
    model_name: str
    prompt: str
    size: str
    status: Literal['queued', 'running', 'candidate', 'adopted', 'failed', 'cancelled']
    error: str
    attempt: int
    retry_of: str | None
    sha256: str | None
    byte_size: int | None
    width: int | None
    height: int | None
    mime: str | None
    file_id: str | None
    created_at: datetime
    finished_at: datetime | None
    adopted_at: datetime | None
    content_url: str | None = None
    provenance: Literal['generated_asset_candidate_unqualified'] = 'generated_asset_candidate_unqualified'
    project_id: str | None = None
    authorized_max_minor: str | None = None

    @field_validator('created_at', 'finished_at', 'adopted_at')
    @classmethod
    def utc_clock(cls, value):
        """Keep generated candidate clocks stable after refresh on local SQLite."""
        return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value
