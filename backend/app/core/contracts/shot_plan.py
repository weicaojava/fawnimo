"""Strict inert shot-plan preparation and server-trusted submission contracts."""
from typing import Annotated,Literal
from pydantic import BaseModel,ConfigDict,Field,StrictInt
from app.core.contracts.script_preparation import ScriptDocument

ID=Annotated[str,Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')]


class StrictInput(BaseModel):
    """Reject imported execution configuration, claimed hashes and qualification flags."""
    model_config=ConfigDict(extra='forbid')


class PlanShotInput(StrictInput):
    """Select explicit immutable versions and owned media for one actual shot."""
    shot_id:ID
    version_ids:list[ID]=Field(default_factory=list,max_length=32)
    reference_media_ids:list[ID]=Field(default_factory=list,max_length=32)
    prompt_addition:str=Field('',max_length=4000)


class PlanDocument(StrictInput):
    """Incomplete chapter preparation can be saved without a model account."""
    schema_version:Literal[1]=1
    script_snapshot_id:ID|None=None
    model_id:str=Field('',max_length=128)
    shots:list[PlanShotInput]=Field(default_factory=list,max_length=200)


class PlanCreate(StrictInput):
    """Create a single chapter-owned plan draft."""
    chapter_id:ID
    document:PlanDocument


class PlanPatch(StrictInput):
    """Save a replacement document at the revision shown to the user."""
    expected_revision:Annotated[StrictInt,Field(ge=1)]
    document:PlanDocument


class PlanCompile(StrictInput):
    """Freeze one draft revision without charging or submitting a task."""
    expected_revision:Annotated[StrictInt,Field(ge=1)]


class PlannedSubmission(StrictInput):
    """Only a recovery identity is client supplied; plan inputs come from the server."""
    request_key:str=Field(min_length=1,max_length=128)


class AssetRegistration(StrictInput):
    """Register a version for an existing identity without copying that identity."""
    kind:Literal['character','scene']
    entity_id:ID


class AssetRegistrationRead(StrictInput):
    """Return stable asset and initial/latest version identities."""
    asset_id:str
    version_id:str


class PlanIssue(StrictInput):
    """A concrete preparation/runtime deficiency, never a quality-pass assertion."""
    code:str
    message:str
    shot_id:ID|None=None


class PlanReadiness(StrictInput):
    """Separate content completeness from unavailable runtime prerequisites."""
    can_compile:bool
    issues:list[PlanIssue]
    runtime_issues:list[PlanIssue]


class PlanSummary(StrictInput):
    """Immutable historical compilation identity."""
    id:str
    draft_revision:int
    content_hash:str
    model_bound:bool


class PlanRead(StrictInput):
    """Safe editable document and its prior compiled versions."""
    id:str
    chapter_id:str
    revision:int
    document:PlanDocument
    readiness:PlanReadiness
    plans:list[PlanSummary]


class CompiledPlanRead(StrictInput):
    """Portable immutable prepared input, not provider qualification."""
    id:str
    draft_revision:int
    content_hash:str
    payload:dict


class PlanPreflight(StrictInput):
    """Current preparation and advisory runtime checks; submission rechecks atomically."""
    usable:bool
    can_submit:bool
    issues:list[PlanIssue]
    runtime_issues:list[PlanIssue]


class PlanScriptOption(StrictInput):
    """Latest chapter publication and its real current usability."""
    id:str
    content_hash:str
    draft_revision:int
    title:str
    document:ScriptDocument
    usable:bool
    issues:list[PlanIssue]


class PlanShotOption(StrictInput):
    """Actual chapter shot identity and extraction status."""
    id:str
    index:int
    title:str
    status:str


class PlanVersionOption(StrictInput):
    """Explicit project version identity with semantic entity association."""
    asset_id:str
    version_id:str
    kind:str
    name:str
    entity_id:str|None
    subject_id:str|None
    revision:int
    media_count:int


class PlanMediaOption(StrictInput):
    """Owned local reference identity; no private path or external URL."""
    id:str
    name:str
    mime:str
    size:int
    sha256:str


class PlanModelOption(StrictInput):
    """Public trusted policy summary, explicitly not a real-model qualification."""
    model_id:str
    durations:list[int]
    reference_kinds:list[str]
    max_references:int
    quote_minor:int|None
    currency:str
    iteration_limit:int
    binding_hash:str


class PlanCatalog(StrictInput):
    """Complete bounded chapter/project preparation choices."""
    script:PlanScriptOption|None
    shots:list[PlanShotOption]
    versions:list[PlanVersionOption]
    media:list[PlanMediaOption]
    models:list[PlanModelOption]


class PlannedJobRead(StrictInput):
    """Candidate provenance only; output is not automatically adopted."""
    job_id:str
    task_id:str
    plan_id:str
    shot_id:str
    entry_hash:str
    state:str
    reason:str
    media_id:str|None
    media_available:bool


class PlannedSubmissionRead(StrictInput):
    """Durable job recovery identity returned by atomic admission."""
    job_id:str
