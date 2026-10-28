"""Strict human Video DNA; these contracts cannot enable model execution."""
from typing import Annotated,Literal
from pydantic import BaseModel,ConfigDict,Field,StrictBool,StrictInt,model_validator,field_validator

SAFE=9007199254740991
ID=Annotated[str,Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')]
Integer=Annotated[StrictInt,Field(ge=-SAFE,le=SAFE)]


def safe_json(value):
    """Reject floats and unsafe integer leaves without coercing original source clocks."""
    if isinstance(value,BaseModel):value=value.model_dump()
    if value is None or type(value) in (str,bool):return
    if type(value) is int:
        if abs(value)>SAFE:raise ValueError('Integer exceeds portable JSON safe range')
        return
    if isinstance(value,dict):
        for key,item in value.items():
            if not isinstance(key,str) or not key.isascii():raise ValueError('Schema keys must be ASCII')
            safe_json(item)
        return
    if isinstance(value,list):
        for item in value:safe_json(item)
        return
    raise ValueError('Only safe integer JSON is supported')


class DnaDTO(BaseModel):
    """Forbid executable extras and protect every nested portable JSON value."""
    model_config=ConfigDict(extra='forbid')

    @model_validator(mode='before')
    @classmethod
    def safe_tree(cls,value):
        """Preserve primitive identity rather than silently converting float/bool clocks."""
        safe_json(value)
        if isinstance(value,dict) and 'schema_version' in value and (type(value['schema_version']) is not int or value['schema_version']!=1):raise ValueError('Only integer schema version 1 is supported')
        return value


class DnaEvidence(DnaDTO):
    """Original decoded-frame half-open evidence interval."""
    start_frame: Annotated[Integer,Field(ge=0,le=7200)]
    end_frame: Annotated[Integer,Field(ge=1,le=7200)]

    @model_validator(mode='after')
    def increasing(self):
        """Evidence cannot be empty or reversed."""
        if self.start_frame>=self.end_frame:raise ValueError('Evidence must have increasing endpoints')
        return self


class DnaClaim(DnaDTO):
    """An explicit unknown, human observation or reconstruction hypothesis."""
    zh: str|None=Field(default=None,max_length=4000)
    en: str|None=Field(default=None,max_length=4000)
    status: Literal['unknown','observed','inferred']='unknown'
    reason: str=Field(default='',max_length=4000)
    confidence: Annotated[Integer,Field(ge=0,le=100)]|None=None
    evidence: list[DnaEvidence]=Field(default_factory=list,max_length=32)


FIELD_KEYS={'character':('face','hair','body','costume','makeup','accessories','voice'),'scene':('layout','architecture','props','lighting','background','time_and_space'),'prop':('shape','material','state')}


class DnaAsset(DnaDTO):
    """A local analysis identity optionally mapped by a human to one owned production version."""
    id: ID
    kind: Literal['character','scene','prop']
    name_zh: str=Field(default='',max_length=4000)
    name_en: str=Field(default='',max_length=4000)
    fields: dict[str,DnaClaim]=Field(default_factory=dict)
    lock_prompt: DnaClaim=Field(default_factory=DnaClaim)
    production_version_id: ID|None=None
    binding_note: str=Field(default='',max_length=4000)

    @model_validator(mode='after')
    def fixed_fields(self):
        """Fill omitted schema fields as unknown; never discard unrecognized keys."""
        keys=FIELD_KEYS[self.kind]
        if set(self.fields)-set(keys):raise ValueError('Unknown asset field')
        self.fields={key:self.fields.get(key,DnaClaim()) for key in keys}
        return self


class DnaDialogue(DnaDTO):
    """Human source transcription declaration, never an ASR qualification."""
    id: ID
    speaker_id: ID|None=None
    claim: DnaClaim=Field(default_factory=DnaClaim)


class DnaEvent(DnaDTO):
    """A local action hypothesis with explicit participating character references."""
    id: ID
    character_ids: list[ID]=Field(default_factory=list,max_length=64)
    claim: DnaClaim=Field(default_factory=DnaClaim)


class DnaShot(DnaDTO):
    """A half-open original frame interval and its editable reconstruction claims."""
    id: ID
    start_frame: Annotated[Integer,Field(ge=0,le=7199)]
    end_frame: Annotated[Integer,Field(ge=1,le=7200)]
    character_ids: list[ID]=Field(default_factory=list,max_length=64)
    scene_id: ID|None=None
    prop_ids: list[ID]=Field(default_factory=list,max_length=64)
    composition: DnaClaim=Field(default_factory=DnaClaim)
    camera: DnaClaim=Field(default_factory=DnaClaim)
    lighting: DnaClaim=Field(default_factory=DnaClaim)
    action: DnaClaim=Field(default_factory=DnaClaim)
    continuity: DnaClaim=Field(default_factory=DnaClaim)
    audio: DnaClaim=Field(default_factory=DnaClaim)
    image_prompt: DnaClaim=Field(default_factory=DnaClaim)
    video_prompt: DnaClaim=Field(default_factory=DnaClaim)
    dialogues: list[DnaDialogue]=Field(default_factory=list,max_length=64)
    events: list[DnaEvent]=Field(default_factory=list,max_length=64)

    @model_validator(mode='after')
    def increasing(self):
        """Shots cannot have reversed or empty frame intervals."""
        if self.start_frame>=self.end_frame:raise ValueError('Shot must have increasing frame endpoints')
        return self


class DnaDocument(DnaDTO):
    """An editable bilingual bible and whole-source shot set, without execution settings."""
    schema_version: Literal[1]=1
    title: str=Field(default='',max_length=4000)
    reviewer: str=Field(default='',max_length=4000)
    human_reviewed: StrictBool=False
    global_style: DnaClaim=Field(default_factory=DnaClaim)
    negative_constraints: DnaClaim=Field(default_factory=DnaClaim)
    audio_editing: DnaClaim=Field(default_factory=DnaClaim)
    assets: list[DnaAsset]=Field(default_factory=list,max_length=64)
    shots: list[DnaShot]=Field(default_factory=list,max_length=256)


class DnaFrame(DnaDTO):
    """Preserved R1 original rational frame clock, never reconstructed from nominal fps."""
    index: Annotated[Integer,Field(ge=0)]
    pts: Integer
    time_base_num: Annotated[Integer,Field(gt=0)]
    time_base_den: Annotated[Integer,Field(gt=0)]
    relative_us: Annotated[Integer,Field(ge=0)]


class DnaSource(DnaDTO):
    """Server-owned frozen source; frame N is a sentinel with unknown terminal time."""
    analysis_id: ID
    analysis_revision: Annotated[Integer,Field(ge=1)]
    media_id: ID
    sha256: str
    manifest_hash: str
    frame_clock_hash: str
    frames: list[DnaFrame]=Field(min_length=1,max_length=7200)
    confirmed_cut_indices: list[Annotated[Integer,Field(ge=0)]]=Field(min_length=1,max_length=256)
    audio_present: StrictBool
    terminal_time_known: Literal[False]=False


class DnaCreate(DnaDTO):
    """Register a source-bound draft or ask the server to generate its exact cut template."""
    analysis_id: ID
    expected_analysis_revision: Annotated[Integer,Field(ge=1)]
    request_key: str=Field(min_length=1,max_length=128)
    document: DnaDocument|None=None


class DnaPatch(DnaDTO):
    """Save the entire controlled document under optimistic revision protection."""
    expected_revision: Annotated[Integer,Field(ge=1)]
    document: DnaDocument


class DnaFreeze(DnaDTO):
    """Freeze one complete human-declaration revision, not a quality result."""
    expected_revision: Annotated[Integer,Field(ge=1)]


class DnaImport(DnaDTO):
    """Import inert v1 JSON into an explicitly selected owned analysis."""
    analysis_id: ID
    expected_analysis_revision: Annotated[Integer,Field(ge=1)]
    request_key: str=Field(min_length=1,max_length=128)
    content: dict


class DnaIssue(DnaDTO):
    """A concrete field/source issue with a stable local locator."""
    code: str
    message: str
    path: str|None=None
    shot_id: str|None=None


class DnaReadiness(DnaDTO):
    """Structural freeze readiness does not assert correctness of the claims."""
    can_freeze: bool
    issues: list[DnaIssue]
    source_current: bool
    human_declarations_only: Literal[True]=True


class DnaSnapshotSummary(DnaDTO):
    """Immutable saved revision available without changing its original source."""
    id: str
    content_hash: str
    draft_revision: int


class DnaDraft(DnaDTO):
    """Complete source and document fields for a controlled editor."""
    id: str
    project_id: str
    analysis_id: str
    revision: int
    source: DnaSource
    document: DnaDocument
    readiness: DnaReadiness
    snapshots: list[DnaSnapshotSummary]


class DnaVersionMedia(DnaDTO):
    """Only logical immutable byte evidence leaves the private media service."""
    media_id: str
    sha256: str
    size: Integer
    mime: str


class DnaAssetVersion(DnaDTO):
    """Exact owned production snapshot metadata; arbitrary M2 payload is excluded."""
    version_id: str
    asset_id: str
    kind: Literal['character','scene','prop']
    content_hash: str
    media: list[DnaVersionMedia]


class DnaSnapshotPayload(DnaDTO):
    """Portable integer-only immutable DNA without disk paths, credentials or execution state."""
    schema_version: Literal[1]=1
    source: DnaSource
    document: DnaDocument
    asset_versions: list[DnaAssetVersion]
    human_declarations_only: Literal[True]=True


class DnaSnapshot(DnaDTO):
    """A content-addressed human hypothesis, not an original-author prompt."""
    id: str
    content_hash: str
    payload: DnaSnapshotPayload


class DnaPreflight(DnaDTO):
    """Availability and current-source hints are separate from historical content identity."""
    usable: bool
    source_available: bool
    asset_versions_available: bool
    source_current: bool
    issues: list[DnaIssue]
    human_declarations_only: Literal[True]=True


class DnaAnalysisOption(DnaDTO):
    """A bounded real R1 analysis and its non-authoritative create checklist."""
    id: str
    media_id: str
    name: str
    state: str
    revision: int
    source_hash: str
    frame_count: int
    confirmed_cut_indices: list[int]
    can_create: bool
    issues: list[DnaIssue]


class DnaDraftOption(DnaDTO):
    """Select a saved draft without dropping it when another analysis is inspected."""
    id: str
    analysis_id: str
    title: str
    revision: int


class DnaVersionOption(DnaDTO):
    """Public project-owned production version option without arbitrary source payload."""
    version_id: str
    asset_id: str
    kind: Literal['character','scene','prop']
    name: str
    revision: int
    content_hash: str


class DnaCatalog(DnaDTO):
    """Actual bounded options; oversized projects fail rather than silently truncate."""
    analyses: list[DnaAnalysisOption]
    drafts: list[DnaDraftOption]
    versions: list[DnaVersionOption]
