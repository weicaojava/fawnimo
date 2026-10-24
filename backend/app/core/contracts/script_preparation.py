"""Strict inert manual production-script contracts, not model generation output."""
from typing import Annotated,Literal
from pydantic import BaseModel,ConfigDict,Field,StrictBool,StrictInt

ID=Annotated[str,Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')]


class StrictInput(BaseModel):
    """Reject unrecognized execution configuration in imported content."""
    model_config=ConfigDict(extra='forbid')


class ScriptBeat(StrictInput):
    """Stable original requirement identity and narrative phase."""
    id:ID
    text:str=Field('',max_length=4000)
    phase:Literal['conflict','development','resolution']
    required:StrictBool=True


class ScriptDialogue(StrictInput):
    """Stable utterance requirement with an explicitly declared project character."""
    id:ID
    speaker_id:ID
    text:str=Field('',max_length=4000)
    required:StrictBool=True


class ShotBinding(StrictInput):
    """Human preparation mapping; never replaces actual generated-shot verification."""
    shot_id:ID
    beat_ids:list[ID]=Field(default_factory=list,max_length=100)
    dialogue_ids:list[ID]=Field(default_factory=list,max_length=200)
    character_ids:list[ID]=Field(default_factory=list,max_length=16)
    scene_id:ID|None=None
    duration_ms:Annotated[StrictInt,Field(ge=1,le=120000)]


class ScriptDocument(StrictInput):
    """Editable incomplete script using integer durations and stable references."""
    schema_version:Literal[1]=1
    title:str=Field('',max_length=160)
    script_text:str=Field('',max_length=64000)
    target_duration_ms:Annotated[StrictInt,Field(ge=1000,le=300000)]=30000
    character_ids:list[ID]=Field(default_factory=list,max_length=16)
    scene_ids:list[ID]=Field(default_factory=list,max_length=16)
    beats:list[ScriptBeat]=Field(default_factory=list,max_length=100)
    dialogues:list[ScriptDialogue]=Field(default_factory=list,max_length=200)
    bindings:list[ShotBinding]=Field(default_factory=list,max_length=200)


class ScriptCreate(StrictInput):
    """Create one chapter-owned preparation draft, not an implicit overwrite."""
    chapter_id:ID
    document:ScriptDocument


class ScriptPatch(StrictInput):
    """Replace a draft under optimistic revision control."""
    expected_revision:Annotated[StrictInt,Field(ge=1)]
    document:ScriptDocument


class ScriptPublish(StrictInput):
    """Acknowledge altered original requirements before a new immutable publication."""
    expected_revision:Annotated[StrictInt,Field(ge=1)]
    acknowledge_requirement_changes:StrictBool=False
    requirement_change_note:str=Field('',max_length=1000)


class ScriptIssue(StrictInput):
    """Concrete software preparation deficiency, not a model failure."""
    code:str
    message:str


class ScriptCoverage(StrictInput):
    """Coverage denominator comes from original declared required IDs."""
    required_beats:int
    covered_beats:int
    required_dialogues:int
    covered_dialogues:int


class ScriptReadiness(StrictInput):
    """Checklist completeness and current usability without real generation claims."""
    can_publish:bool
    issues:list[ScriptIssue]
    coverage:ScriptCoverage
    requirement_changes:list[str]
    latest_snapshot_usable:bool


class ScriptSnapshotSummary(StrictInput):
    """An immutable publication identity and its draft revision."""
    id:str
    draft_revision:int
    content_hash:str


class ScriptRead(StrictInput):
    """Safe preparation draft and historical snapshot summaries."""
    id:str
    chapter_id:str
    revision:int
    document:ScriptDocument
    readiness:ScriptReadiness
    snapshots:list[ScriptSnapshotSummary]


class ScriptSnapshot(StrictInput):
    """Exported manual preparation evidence with canonical content hash."""
    id:str
    content_hash:str
    payload:dict


class ScriptPreflight(StrictInput):
    """Reject stale preparation without claiming provider or video readiness."""
    usable:bool
    issues:list[ScriptIssue]


class ScriptEntityOption(StrictInput):
    """A current owned entity identity displayed by the manual preparation UI."""
    id:str
    name:str


class ScriptCatalog(StrictInput):
    """Bounded complete project choices; excess is rejected rather than hidden."""
    characters:list[ScriptEntityOption]
    scenes:list[ScriptEntityOption]
