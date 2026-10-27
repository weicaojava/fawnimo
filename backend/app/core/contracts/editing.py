"""Strict account-free editing contracts; declarations never grant model qualification."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator


class EditingDTO(BaseModel):
    """Reject extra fields at every public document boundary."""
    model_config = ConfigDict(extra='forbid')


class SelectionWrite(EditingDTO):
    """Describe one explicit CAS adoption or historical selection intent."""
    request_key: str = Field(min_length=1,max_length=128)
    shot_id: str = Field(min_length=1,max_length=64)
    expected_revision: StrictInt = Field(ge=0)
    job_id: str | None = Field(default=None,max_length=64)
    take_id: str | None = Field(default=None,max_length=64)
    media_id: str | None = Field(default=None,max_length=64)
    plan_id: str | None = Field(default=None,max_length=64)
    accept_historical: StrictBool = False
    change_note: str = Field(default='',max_length=1000)

    @model_validator(mode='after')
    def one_source(self):
        """A source is chosen once; provenance cannot be supplied by a client."""
        if sum(value is not None for value in (self.job_id,self.take_id,self.media_id)) != 1:raise ValueError('Choose exactly one job, take or media')
        if not self.request_key.strip():raise ValueError('Request key cannot be blank')
        if self.media_id and (not self.plan_id or not self.change_note.strip()):raise ValueError('Imported media needs a plan and explanation')
        return self


class EditClip(EditingDTO):
    """An ordered video interval; endpoints use source video-zero milliseconds."""
    shot_id: str = Field(min_length=1,max_length=64)
    take_id: str = Field(min_length=1,max_length=64)
    source_in_ms: StrictInt = Field(ge=0,le=300000)
    source_out_ms: StrictInt = Field(ge=0,le=300000)
    source_audio: StrictBool = True

    @model_validator(mode='after')
    def interval(self):
        """Only exact 100ms intervals can map to three output frames."""
        if self.source_out_ms<=self.source_in_ms or self.source_in_ms%100 or self.source_out_ms%100:raise ValueError('Trim needs increasing 100ms endpoints')
        return self


class EditAnchor(EditingDTO):
    """One human declaration in original source milliseconds, not timeline time."""
    kind: Literal['beat','dialogue']
    item_id: str = Field(min_length=1,max_length=64)
    take_id: str = Field(min_length=1,max_length=64)
    start_ms: StrictInt = Field(ge=0,le=300000)
    end_ms: StrictInt = Field(ge=0,le=300000)
    confirmed: StrictBool = False

    @model_validator(mode='after')
    def interval(self):
        """Anchors can use 1ms precision but cannot be empty or reversed."""
        if self.end_ms<=self.start_ms:raise ValueError('Anchor end must follow start')
        return self


class EditDocument(EditingDTO):
    """A whole chapter's explicitly selected takes and retention declarations."""
    schema_version: Literal[1] = 1
    plan_id: str | None = Field(default=None,max_length=64)
    title: str = Field(default='',max_length=120)
    clips: list[EditClip] = Field(default_factory=list,max_length=100)
    anchors: list[EditAnchor] = Field(default_factory=list,max_length=1000)


class EditDraftCreate(EditingDTO):
    """Register one editable chapter assembly."""
    chapter_id: str = Field(min_length=1,max_length=64)
    document: EditDocument


class EditDraftPatch(EditingDTO):
    """Protect all editable fields with an expected revision."""
    expected_revision: StrictInt = Field(ge=1)
    document: EditDocument


class EditFreezeWrite(EditingDTO):
    """Acknowledge duration, trim or historical upstream differences explicitly."""
    expected_revision: StrictInt = Field(ge=1)
    acknowledge_changes: StrictBool = False
    change_note: str = Field(default='',max_length=1000)


class EditIssue(EditingDTO):
    """Actionable readiness feedback without internal paths or exception traces."""
    code: str
    message: str
    shot_id: str | None = None
    item_id: str | None = None


class EditReadiness(EditingDTO):
    """Software content readiness; never a semantic or model quality result."""
    can_freeze: bool
    issues: list[EditIssue]
    human_declarations_only: Literal[True] = True


class EditPublicationSummary(EditingDTO):
    """Immutable revision available for historical restore and export."""
    id: str
    content_hash: str
    draft_revision: int


class EditDraftRead(EditingDTO):
    """A complete controlled form and current checklist."""
    id: str
    project_id: str
    chapter_id: str
    revision: int
    document: EditDocument
    readiness: EditReadiness
    publications: list[EditPublicationSummary]


class EditPublicationRead(EditingDTO):
    """Safe publication view; payload omits private storage credentials."""
    id: str
    project_id: str
    chapter_id: str
    draft_id: str
    draft_revision: int
    content_hash: str
    payload: dict


class EditSelectionDifference(EditingDTO):
    """A later selection does not mutate a published assembly."""
    shot_id: str
    publication_take_id: str
    current_take_id: str | None


class EditPreflightRead(EditingDTO):
    """Check source availability independently of present selection choices."""
    usable: bool
    issues: list[EditIssue]
    current_selection_differences: list[EditSelectionDifference]
    human_declarations_only: Literal[True] = True


class SelectionRead(EditingDTO):
    """Return the original event on replay without reselecting its take."""
    event_id: str
    shot_id: str
    take_id: str
    revision: int
    origin_kind: str
    replayed: bool = False


class EditTakeOption(EditingDTO):
    """Historical choice with explicit eligibility for a selected plan."""
    id: str
    media_id: str
    name: str
    origin_kind: str
    plan_id: str | None
    entry_hash: str | None
    eligible_for_plan: bool
    issues: list[EditIssue]
    duration_ms: int | None


class EditSelectionOption(EditingDTO):
    """Actual current pointer and the new service's CAS revision."""
    shot_id: str
    revision: int
    take_id: str | None


class SelectionHistoryRead(EditingDTO):
    """Selection audit and immutable takes, including legacy history."""
    shot_id: str
    current_take_id: str | None
    revision: int
    events: list[SelectionRead]
    takes: list[EditTakeOption]


class EditPlanOption(EditingDTO):
    """Owned chapter plan available as a stable coverage denominator."""
    id: str
    content_hash: str
    title: str


class EditClipOption(EditingDTO):
    """One actual planned shot and its historical take choices."""
    shot_id: str
    title: str
    index: int
    duration_ms: int
    takes: list[EditTakeOption]


class EditItemOption(EditingDTO):
    """Original immutable story item and allowable mapped shots."""
    kind: Literal['beat','dialogue']
    item_id: str
    required: bool
    text: str
    speaker_id: str | None
    shot_ids: list[str]


class EditMediaOption(EditingDTO):
    """Owned MP4 eligible for explicitly acknowledged manual import."""
    id: str
    name: str
    sha256: str
    size: int
    mime: str


class EditCandidateOption(EditingDTO):
    """Actual planned execution candidate; ready is not quality qualification."""
    job_id: str
    shot_id: str
    plan_id: str
    state: str
    media_id: str | None


class EditCatalog(EditingDTO):
    """Bounded real chapter choices; limits fail rather than truncate."""
    project_id: str
    chapter_id: str
    plans: list[EditPlanOption]
    plan_id: str | None
    clips: list[EditClipOption]
    items: list[EditItemOption]
    selections: list[EditSelectionOption]
    media: list[EditMediaOption]
    candidates: list[EditCandidateOption]
