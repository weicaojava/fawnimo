"""Typed, bounded configuration contracts; none grant permission to execute an Agent."""
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.llm import AgentTypeKey


class AgentNode(BaseModel):
    """Describe a local workflow node rather than executable code or arbitrary tools."""

    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=80)
    type: Literal['start', 'llm', 'end']
    prompt: str = Field(default='', max_length=32000)
    temperature: float = Field(default=0.7, ge=0, le=2)
    top_p: float = Field(default=0.9, ge=0, le=1)


def default_workflow() -> list[AgentNode]:
    """Create a fresh, editable configuration graph with an explicit start and finish."""
    return [AgentNode(id='start', label='开始', type='start'), AgentNode(id='llm1', label='LLM 提取', type='llm'), AgentNode(id='end', label='结束', type='end')]


class AgentParameters(BaseModel):
    """Keep the currently exposed extraction controls typed and persistable."""

    model_config = ConfigDict(extra='forbid')
    plot_threshold: float = Field(default=0.5, ge=0, le=1)
    character_limit: int = Field(default=20, ge=1, le=100)


class AgentConfigCreate(BaseModel):
    """Persist a complete Agent draft while rejecting hidden fields and broken graphs."""

    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=255)
    type: AgentTypeKey
    description: str = Field(default='', max_length=8000)
    is_default: bool = False
    is_favorite: bool = False
    prompt: str = Field(default='', max_length=64000)
    temperature: float = Field(default=0.7, ge=0, le=2)
    top_p: float = Field(default=0.9, ge=0, le=1)
    parameters: AgentParameters = Field(default_factory=AgentParameters)
    workflow: list[AgentNode] = Field(default_factory=default_workflow, min_length=2, max_length=32)

    @model_validator(mode='after')
    def validate_graph(self):
        """Reject duplicate identifiers and ambiguous execution order before any DB write."""
        if len({node.id for node in self.workflow}) != len(self.workflow):
            raise ValueError('工作流节点 ID 不可重复')
        if self.workflow[0].type != 'start' or self.workflow[-1].type != 'end':
            raise ValueError('工作流必须以开始节点起始，以结束节点结尾')
        if sum(node.type == 'start' for node in self.workflow) != 1 or sum(node.type == 'end' for node in self.workflow) != 1:
            raise ValueError('工作流只能包含一个开始节点和一个结束节点')
        if len(self.model_dump_json().encode()) > 262144:
            raise ValueError('Agent 配置不得超过 256 KiB')
        return self


class AgentConfigUpdate(BaseModel):
    """Allow partial metadata actions and full edits with an optional stale-version guard."""

    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    expected_version: int | None = Field(default=None, ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    type: AgentTypeKey | None = None
    description: str | None = Field(default=None, max_length=8000)
    is_default: bool | None = None
    is_favorite: bool | None = None
    prompt: str | None = Field(default=None, max_length=64000)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    parameters: AgentParameters | None = None
    workflow: list[AgentNode] | None = Field(default=None, min_length=2, max_length=32)


class AgentConfigRead(AgentConfigCreate):
    """Return complete saved settings and real timestamps, without provider credentials."""

    model_config = ConfigDict(from_attributes=True, extra='forbid')
    id: str
    version: int
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime

    @field_validator('created_at', 'updated_at')
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        """SQLite drops timezone offsets; preserve the database's UTC clock for browsers."""
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class AgentConfigExport(BaseModel):
    """Portable configuration excludes database identity and never contains credentials."""

    model_config = ConfigDict(extra='forbid')
    schema_version: Literal[1] = 1
    config: AgentConfigCreate


class AgentConfigRevisionRead(BaseModel):
    """Expose saved versions for inspection or an explicit restore through normal update."""

    version: int
    saved_at: datetime
    document: AgentConfigCreate

    @field_validator('saved_at')
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        """Display revision times on the same real UTC clock as config metadata."""
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
