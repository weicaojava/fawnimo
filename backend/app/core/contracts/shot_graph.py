"""Atomic chapter structure commands; none grants a source clock or generation qualification."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class GraphDTO(BaseModel):
    """Reject hidden structural intent instead of silently accepting unknown fields."""
    model_config = ConfigDict(extra='forbid')


class ShotGraphWrite(GraphDTO):
    """Compare the saved complete graph and its revision before applying one idempotent command."""
    project_id: str = Field(min_length=1, max_length=64)
    chapter_id: str = Field(min_length=1, max_length=64)
    request_key: str = Field(min_length=1, max_length=128)
    expected_revision: StrictInt = Field(ge=0)
    expected_fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    action: Literal['reorder', 'insert', 'merge']
    ordered_shot_ids: list[str] = Field(default_factory=list, max_length=500)
    source_shot_ids: list[str] = Field(default_factory=list, max_length=50)
    after_shot_id: str | None = Field(default=None, max_length=64)
    title: str | None = Field(default=None, min_length=1, max_length=255)

    @model_validator(mode='after')
    def command_shape(self):
        """Each command has exactly one shape; identifiers cannot appear twice."""
        if not self.request_key.strip() or (self.title is not None and not self.title.strip()):
            raise ValueError('请求标识和标题不能只有空白')
        for group in (self.ordered_shot_ids, self.source_shot_ids):
            if any(not value.strip() or len(value) > 64 for value in group) or len(set(group)) != len(group):
                raise ValueError('镜头标识不能为空、重复或超过64字')
        if self.action == 'reorder':
            if not self.ordered_shot_ids or self.source_shot_ids or self.after_shot_id is not None or self.title is not None:
                raise ValueError('重排须仅提供完整镜头顺序')
        elif self.action == 'insert':
            if self.ordered_shot_ids or self.source_shot_ids:
                raise ValueError('插入不能同时指定重排或合并')
        elif len(self.source_shot_ids) < 2 or self.ordered_shot_ids or self.after_shot_id is not None:
            raise ValueError('合并须仅指定至少两个相邻镜头')
        return self


class ShotGraphItem(GraphDTO):
    """A saved identity and ordering, not an adopted-output summary."""
    id: str
    index: int
    title: str
    status: str


class ShotGraphReceiptSummary(GraphDTO):
    """A durable backup pointer for structural recovery and request replay."""
    id: str
    action: str
    revision: int
    result_shot_id: str | None
    source_shot_ids: list[str]


class ShotGraphRead(GraphDTO):
    """The full chapter order and exact compare-and-swap token."""
    project_id: str
    chapter_id: str
    revision: int
    fingerprint: str
    shots: list[ShotGraphItem]
    receipts: list[ShotGraphReceiptSummary]


class ShotGraphResult(GraphDTO):
    """A committed command receipt alongside current state; replay never reapplies an old edit."""
    receipt: ShotGraphReceiptSummary
    replayed: bool
    graph: ShotGraphRead


class ShotGraphReceiptRead(GraphDTO):
    """Downloadable original graph data; restoring it does not restore adopted qualification."""
    project_id: str
    chapter_id: str
    receipt: ShotGraphReceiptSummary
    before: dict
    after_fingerprint: str
    recovery_note: str
