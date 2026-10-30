"""Bounded script proposals and explicit human decisions; no imported execution authority."""
from typing import Annotated,Literal
from pydantic import BaseModel,ConfigDict,Field,StrictBool,StrictInt,model_validator
from app.core.contracts.agent_execution import ExecutionModelChoice

ID=Annotated[str,Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')]
Operation=Literal['optimize','simplify','divide','extract_shot']
Text=Annotated[str,Field(min_length=1,max_length=16000)]
Name=Annotated[str,Field(min_length=1,max_length=120)]


class StrictInput(BaseModel):
    """Reject hidden IDs, file references, providers and unknown generated properties."""
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)


class ScriptCandidateCreate(StrictInput):
    """Confirm one original-project text call against an exact saved source revision."""
    request_key:Annotated[str,Field(min_length=1,max_length=128)]
    operation:Operation
    source_field:Literal['raw_text','condensed_text']='raw_text'
    shot_id:ID|None=None
    expected_source_revision:Annotated[StrictInt,Field(ge=0)]
    expected_source_hash:Annotated[str,Field(pattern=r'^[a-f0-9]{64}$')]
    model_id:ID
    expected_price_fingerprint:Annotated[str,Field(pattern=r'^[a-f0-9]{64}$')]
    confirm_model_call:StrictBool

    @model_validator(mode='after')
    def exact_operation_scope(self):
        """One-shot extraction cannot borrow a chapter-wide source or omit its real shot."""
        if self.confirm_model_call is not True:raise ValueError('请明确确认本项目模型费用')
        if (self.operation=='extract_shot')!=(self.shot_id is not None):raise ValueError('单镜提取须且仅须提供镜头ID')
        if self.operation in ('optimize','simplify','extract_shot') and self.source_field!='raw_text':raise ValueError('本操作只使用其保存的原始文本')
        return self


class ScriptCandidateDecision(StrictInput):
    """A decision names the exact saved proposal and current source instead of new model text."""
    expected_source_revision:Annotated[StrictInt,Field(ge=0)]
    expected_source_hash:Annotated[str,Field(pattern=r'^[a-f0-9]{64}$')]
    expected_output_hash:Annotated[str,Field(pattern=r'^[a-f0-9]{64}$')]


class ScriptTextProposal(StrictInput):
    """A model proposes text without directly replacing chapter text or its history."""
    text:Text
    summary:Annotated[str,Field(max_length=2000)]=''


class ScriptShotProposal(StrictInput):
    """Shot identities and order are server-created only after explicit adoption."""
    title:Annotated[str,Field(min_length=1,max_length=160)]
    script_excerpt:Annotated[str,Field(min_length=1,max_length=8000)]


class ScriptDivisionProposal(StrictInput):
    """A finite proposed division cannot delete or overwrite existing shots."""
    shots:list[ScriptShotProposal]=Field(min_length=1,max_length=64)
    summary:Annotated[str,Field(max_length=2000)]=''


class ScriptEntityProposal(StrictInput):
    """Name-based candidate content cannot supply database, file or template IDs."""
    name:Name
    description:Annotated[str,Field(max_length=4000)]=''


class ScriptDialogueProposal(StrictInput):
    """Bounded original spoken text with optional named speakers for later confirmation."""
    text:Annotated[str,Field(min_length=1,max_length=4000)]
    speaker_name:Name|None=None
    target_name:Name|None=None
    line_mode:Literal['DIALOGUE','VOICE_OVER','OFF_SCREEN','PHONE']='DIALOGUE'


class ScriptExtractionProposal(StrictInput):
    """Extract only names, descriptions and dialogue; it cannot link actual foreign assets."""
    characters:list[ScriptEntityProposal]=Field(default_factory=list,max_length=32)
    scenes:list[ScriptEntityProposal]=Field(default_factory=list,max_length=1)
    props:list[ScriptEntityProposal]=Field(default_factory=list,max_length=32)
    costumes:list[ScriptEntityProposal]=Field(default_factory=list,max_length=32)
    dialogues:list[ScriptDialogueProposal]=Field(default_factory=list,max_length=64)
    summary:Annotated[str,Field(max_length=2000)]=''

    @model_validator(mode='after')
    def named_entities_are_unambiguous(self):
        """No duplicate names or undeclared speaker names are silently merged."""
        for group in (self.characters,self.scenes,self.props,self.costumes):
            if len({item.name for item in group})!=len(group):raise ValueError('同类候选名称不能重复')
        names={item.name for item in self.characters}
        if any(name is not None and name not in names for row in self.dialogues for name in (row.speaker_name,row.target_name)):
            raise ValueError('对白人物必须在本次角色候选内声明')
        return self


class ScriptCandidateRead(BaseModel):
    """Expose a refreshable proposal and its actual original-project charge without private evidence."""
    id:str
    project_id:str
    chapter_id:str
    shot_id:str|None
    operation:Operation
    source_field:Literal['raw_text','condensed_text']
    source_revision:int
    source_hash:str
    source_text:str
    status:str
    decision:Literal['pending','adopted','dismissed']
    model_name:str
    authorized_max_minor:str
    result:ScriptTextProposal|ScriptDivisionProposal|ScriptExtractionProposal|None
    output_hash:str|None
    source_current:bool
    can_adopt:bool
    message:str


class ScriptCandidateAvailability(BaseModel):
    """A source-bound fee preview does not reserve funds or issue any model request."""
    available:bool
    message:str
    source_revision:int
    source_hash:str
    input_characters:int
    project_name:str
    chapter_title:str
    selected_model_id:str|None=None
    selected_price_fingerprint:str|None=None
    authorized_max_minor:str|None=None
    models:list[ExecutionModelChoice]
