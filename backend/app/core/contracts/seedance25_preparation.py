"""Read-only S25A preparation metadata; no credential or activation input exists."""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict
from app.core.contracts.shot_plan import PlanIssue


class Seedance25ProfileRead(BaseModel):
    """Describe this software slice separately from the provider's wider native abilities."""
    model_config = ConfigDict(extra='forbid')
    model_id: str
    profile_hash: str
    preparation_binding_hash: str
    mode: Literal['text_to_video'] = 'text_to_video'
    min_duration: Literal[4] = 4
    max_duration: Literal[30] = 30
    ratio: Literal['9:16'] = '9:16'
    resolution: Literal['480p'] = '480p'
    output_format: Literal['mp4'] = 'mp4'
    generate_audio: Literal[False] = False
    watermark: Literal[True] = True
    execution_enabled: Literal[False] = False
    model_qualified: Literal[False] = False
    quote_minor: None = None
    currency: Literal['CNY'] = 'CNY'
    unsupported_modes: list[str]
    message: str


class Seedance25PreviewEntry(BaseModel):
    """Keep each wire body attached to its original immutable shot-plan entry."""
    model_config = ConfigDict(extra='forbid')
    shot_id: str
    entry_hash: str
    body: dict[str, Any] | None
    issues: list[PlanIssue]


class Seedance25PlanPreview(BaseModel):
    """A deterministic inert preview cannot submit or confer any model qualification."""
    model_config = ConfigDict(extra='forbid')
    plan_id: str
    content_hash: str
    profile_hash: str
    wire_hash: str | None
    can_prepare: bool
    can_submit: Literal[False] = False
    execution_enabled: Literal[False] = False
    model_qualified: Literal[False] = False
    issues: list[PlanIssue]
    entries: list[Seedance25PreviewEntry]
