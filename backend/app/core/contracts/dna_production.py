"""Source-bound, non-generating import of editable DNA into pending production records."""
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field, StrictInt


class DnaProductionWrite(BaseModel):
    """Import the displayed revision once, with explicitly selected owned reference images."""
    model_config = ConfigDict(extra='forbid')
    expected_revision: Annotated[StrictInt, Field(ge=1)]
    request_key: str = Field(min_length=1, max_length=128)
    chapter_title: str = Field(default='', max_length=120)
    asset_media_ids: dict[str, str] = Field(default_factory=dict, max_length=64)
    shot_media_ids: dict[str, str] = Field(default_factory=dict, max_length=200)


class DnaProductionRead(BaseModel):
    """Return stable imported identities, without implying extraction or generation readiness."""
    id: str
    project_id: str
    draft_id: str
    draft_revision: int
    chapter_id: str
    shot_ids: list[str]
    plan_draft_id: str
    asset_bindings: dict[str, dict]
    prompt_template_ids: list[str]
    counts: dict[str, int]
    status: str = 'pending_confirmation'
    message: str = '已创建待确认资产、分镜与镜头计划；尚未人工签核或提交生成。'
