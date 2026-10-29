"""Version-two transition assembly based on immutable v1 timelines, with no model qualification."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator
from app.core.contracts.editing_export import ExportRead, ExportCatalog, ExportRequest, ExportCancel


class TransitionDTO(BaseModel):
    """Keep all v2 requests strict and separate from v1 clip/source definitions."""
    model_config = ConfigDict(extra='forbid')


class TransitionEdge(TransitionDTO):
    """One actual adjacent boundary; crossfade changes output time, never original source time."""
    left_shot_id: str = Field(min_length=1, max_length=64)
    right_shot_id: str = Field(min_length=1, max_length=64)
    kind: Literal['hard_cut', 'crossfade'] = 'hard_cut'
    duration_ms: StrictInt = Field(default=0, ge=0, le=1000)

    @model_validator(mode='after')
    def duration(self):
        """Crossfade samples must land on the 100ms/three-frame grid."""
        if self.kind == 'hard_cut' and self.duration_ms != 0:
            raise ValueError('硬切时长必须为0')
        if self.kind == 'crossfade' and (self.duration_ms < 100 or self.duration_ms % 100):
            raise ValueError('交叉淡化须为100至1000毫秒、100毫秒整数步')
        return self


class TransitionDocument(TransitionDTO):
    """Only boundary choices are editable; source media, trim and adoptions stay fixed in the base."""
    schema_version: Literal[2] = 2
    title: str = Field(default='', max_length=120)
    edges: list[TransitionEdge] = Field(default_factory=list, max_length=99)


class TransitionCreate(TransitionDTO):
    """Register a v2 variant for an actual owned immutable v1 publication."""
    base_publication_id: str = Field(min_length=1, max_length=64)
    document: TransitionDocument


class TransitionPatch(TransitionDTO):
    """Persist a complete boundary form under compare-and-swap revision control."""
    expected_revision: StrictInt = Field(ge=1)
    document: TransitionDocument


class TransitionFreeze(TransitionDTO):
    """Explicitly acknowledge shorter output before locking actual crossfade intent."""
    expected_revision: StrictInt = Field(ge=1)
    accept_overlap_change: StrictBool = False


class TransitionIssue(TransitionDTO):
    """Precise preparation failure, with source identity where applicable."""
    code: str
    message: str
    shot_id: str | None = None
    item_id: str | None = None


class TransitionPublicationSummary(TransitionDTO):
    """Independent immutable v2 revision; it does not replace the original v1 publication."""
    id: str
    draft_revision: int
    content_hash: str
    duration_ms: int


class TransitionDraftRead(TransitionDTO):
    """Controlled v2 form and overlap/anchor checklist; availability is not a model assessment."""
    id: str
    project_id: str
    chapter_id: str
    base_publication_id: str
    base_publication_hash: str
    revision: int
    document: TransitionDocument
    base_duration_ms: int
    overlap_ms: int
    duration_ms: int
    can_freeze: bool
    issues: list[TransitionIssue]
    publications: list[TransitionPublicationSummary]


class TransitionPublicationRead(TransitionDTO):
    """Safe immutable v2 plan view; actual source paths remain private."""
    id: str
    project_id: str
    chapter_id: str
    draft_revision: int
    content_hash: str
    payload: dict
    model_calls: Literal[0] = 0
