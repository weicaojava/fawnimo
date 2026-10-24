"""Typed human benchmark preparation; declarations never certify Q0 readiness."""
from typing import Annotated,Literal
from pydantic import BaseModel,ConfigDict,Field,StrictInt

Identity=Annotated[str,Field(pattern=r'^[a-zA-Z0-9_-]{1,32}$')]
Frame=Annotated[StrictInt,Field(ge=0)]


class StrictDocument(BaseModel):
    """Reject unknown imported fields instead of accepting executable configuration."""
    model_config=ConfigDict(extra='forbid')


class Role(StrictDocument):
    """Human-declared stable character identity."""
    id:Identity
    name:str=Field('',max_length=80)


class Dialogue(StrictDocument):
    """A declared utterance with inclusive original-frame endpoints."""
    id:Identity
    role_id:Identity
    text:str=Field('',max_length=2000)
    start_frame:Frame
    end_frame:Frame


class MouthAnchor(StrictDocument):
    """A frame-level speaking or closed mouth declaration."""
    role_id:Identity
    frame_index:Frame
    state:Literal['speaking','closed']


class BenchmarkEvent(StrictDocument):
    """Human-declared interaction/action/camera evidence interval."""
    id:Identity
    kind:Literal['interaction','action','camera_change']
    description:str=Field('',max_length=1000)
    start_frame:Frame
    end_frame:Frame


class RubricNotes(StrictDocument):
    """All eight quality dimensions exist even when draft notes are incomplete."""
    character:str=Field('',max_length=2000)
    scene:str=Field('',max_length=2000)
    composition:str=Field('',max_length=2000)
    lighting:str=Field('',max_length=2000)
    action:str=Field('',max_length=2000)
    continuity:str=Field('',max_length=2000)
    lipsync:str=Field('',max_length=2000)
    editing:str=Field('',max_length=2000)


class BenchmarkDocument(StrictDocument):
    """Incomplete drafts are allowed; semantic claims remain human declarations."""
    schema_version:Literal[1]=1
    title:str=Field('',max_length=120)
    source_note:str=Field('',max_length=1000)
    license_basis:Literal['owned','permission','open_license','unknown']='unknown'
    license_reference:str=Field('',max_length=1000)
    license_confirmed:bool=False
    independent_from_target:bool=False
    continuous_take:bool=False
    reviewer:str=Field('',max_length=120)
    human_reviewed:bool=False
    roles:list[Role]=Field(default_factory=list,max_length=8)
    dialogues:list[Dialogue]=Field(default_factory=list,max_length=200)
    mouth_anchors:list[MouthAnchor]=Field(default_factory=list,max_length=200)
    events:list[BenchmarkEvent]=Field(default_factory=list,max_length=100)
    cut_frame_indices:list[Frame]=Field(default_factory=list,max_length=256)
    rubric_notes:RubricNotes=Field(default_factory=RubricNotes)


class DraftCreate(StrictDocument):
    """Import JSON as inert draft content against an owned completed analysis."""
    analysis_id:str=Field(min_length=1,max_length=64)
    document:BenchmarkDocument
    request_key:str=Field(min_length=1,max_length=128)


class DraftPatch(StrictDocument):
    """Replace a draft only at the version the reviewer actually saw."""
    expected_revision:Annotated[StrictInt,Field(ge=1)]
    document:BenchmarkDocument


class FreezeWrite(StrictDocument):
    """Freeze one reviewed revision without upgrading formal Q0 status."""
    expected_revision:Annotated[StrictInt,Field(ge=1)]


class Issue(StrictDocument):
    """A concrete missing prerequisite for software snapshot preparation."""
    code:str
    message:str


class Readiness(StrictDocument):
    """Human-declared checklist completeness, not model or license verification."""
    can_freeze:bool
    issues:list[Issue]
    semantic_verification:Literal['human_declaration_only']='human_declaration_only'


class SnapshotSummary(StrictDocument):
    """Immutable snapshot identity attached to one draft revision."""
    id:str
    content_hash:str
    draft_revision:int


class BenchmarkRead(StrictDocument):
    """Safe draft projection with no internal filesystem or runtime state."""
    id:str
    analysis_id:str
    revision:int
    document:BenchmarkDocument
    readiness:Readiness
    snapshots:list[SnapshotSummary]


class BenchmarkSnapshot(StrictDocument):
    """A portable immutable human-declared benchmark evidence package."""
    id:str
    content_hash:str
    payload:dict
