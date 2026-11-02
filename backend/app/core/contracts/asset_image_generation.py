"""Explicit single-image jobs with separately declared reference and return protocols."""
from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AssetImageKind = Literal['actor', 'costume', 'scene', 'prop']
AssetImageProtocolKey = Literal['openai_b64_v1', 'volcengine_seedream_json_v1']
AssetImageResponseFormat = Literal['b64_json', 'url']


class AssetImageReferenceInput(BaseModel):
    """Authorize exact original-project media and an explicit reference purpose, without accepting URLs or paths."""
    model_config = ConfigDict(extra='forbid', frozen=True)
    media_id: str = Field(min_length=1, max_length=64)
    expected_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    purpose: Literal['appearance', 'composition', 'style']


class AssetImageReferenceRead(BaseModel):
    """Expose actual input proof without private storage paths or data URLs."""
    model_config = ConfigDict(from_attributes=True)
    media_id: str
    position: int
    purpose: Literal['appearance', 'composition', 'style']
    sha256: str
    byte_size: int
    width: int
    height: int
    mime: str


class ImageModelChoice(BaseModel):
    """Expose configuration guidance without URLs, model parameters or credentials."""
    id: str
    name: str
    configured: bool
    message: str
    priced: bool = False
    max_call_minor: int | None = None
    price_fingerprint: str | None = None
    protocol: AssetImageProtocolKey | None = None
    supports_references: bool = False
    max_reference_images: int = 0
    allowed_sizes: list[str] = Field(default_factory=list)
    response_formats: list[AssetImageResponseFormat] = Field(default_factory=lambda: ['b64_json'])


class AssetImageAvailability(BaseModel):
    """Distinguish this limited image protocol from legacy Film and video generation."""
    available: bool
    message: str
    default_model_id: str | None
    models: list[ImageModelChoice]
    protocol: AssetImageProtocolKey = 'openai_b64_v1'
    supports_references: bool = False
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
    """Authorize one image with exact optional project references and a declared result format, never routing overrides."""
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    request_key: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1,max_length=64)
    expected_price_fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    model_id: str = Field(min_length=1, max_length=64)
    prompt: str = Field(min_length=1, max_length=8000)
    size: Literal['256x256', '512x512', '1024x1024', '2048x2048', '2848x1600', '1600x2848'] = '1024x1024'
    references: list[AssetImageReferenceInput] = Field(default_factory=list, max_length=3)
    response_format: AssetImageResponseFormat = 'b64_json'
    confirm_model_call: Literal[True]

    @field_validator('confirm_model_call', mode='before')
    @classmethod
    def actual_model_confirmation(cls, value):
        """A numeric or text lookalike is not an explicit JSON boolean model-call confirmation."""
        if value is not True:
            raise ValueError('需要明确确认本次模型调用')
        return value

    @model_validator(mode='after')
    def unique_references(self):
        """Do not authorize an ambiguous duplicate image at multiple positions or purposes."""
        if len({item.media_id for item in self.references}) != len(self.references):
            raise ValueError('参考图片不能重复')
        return self


class AssetImageRetry(BaseModel):
    """Create a distinct attempt after acknowledging uncertain remote charges."""
    model_config = ConfigDict(extra='forbid')
    request_key: str = Field(min_length=1, max_length=128)
    expected_price_fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    acknowledge_duplicate_charge: Literal[True]

    @field_validator('acknowledge_duplicate_charge', mode='before')
    @classmethod
    def actual_retry_confirmation(cls, value):
        """Do not infer acceptance of a possible duplicate charge from an integer or string."""
        if value is not True:
            raise ValueError('需要明确确认重试可能重复计费')
        return value


class AssetImageAdopt(BaseModel):
    """Require a separately confirmed, exact slot snapshot before changing its image."""
    model_config = ConfigDict(extra='forbid')
    request_key: str = Field(min_length=1, max_length=128)
    expected_file_id: str | None = Field(..., max_length=64)
    expected_updated_at: datetime
    confirm_replace: Literal[True]

    @field_validator('confirm_replace', mode='before')
    @classmethod
    def actual_adoption_confirmation(cls, value):
        """Keep replacement a separate actual boolean confirmation, including for reference-image candidates."""
        if value is not True:
            raise ValueError('需要明确确认采用并替换当前图片')
        return value


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
    protocol: AssetImageProtocolKey | None = None
    response_format: AssetImageResponseFormat = 'b64_json'
    references: list[AssetImageReferenceRead] = Field(default_factory=list)

    @field_validator('created_at', 'finished_at', 'adopted_at')
    @classmethod
    def utc_clock(cls, value):
        """Keep generated candidate clocks stable after refresh on local SQLite."""
        return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value
