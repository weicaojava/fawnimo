"""Bounded Agent workflow execution and explicit text-model connection-test contracts."""
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

RunStatus = Literal['queued', 'running', 'succeeded', 'failed', 'cancelled']
NodeStatus = Literal['pending', 'running', 'succeeded', 'failed', 'cancelled', 'skipped']


class ExecutionModelChoice(BaseModel):
    """Expose selectable model identities without provider URLs, parameters or credentials."""
    id: str
    name: str
    provider_name: str
    configured: bool
    message: str
    priced: bool = False
    max_call_minor: int | None = None
    price_fingerprint: str | None = None


class AgentExecutionAvailability(BaseModel):
    """Separate configured text execution from image/video generation qualification."""
    available: bool
    message: str
    default_model_id: str | None = None
    models: list[ExecutionModelChoice] = Field(default_factory=list)
    max_llm_nodes: int = 8
    call_kind: Literal['agent_text','model_connection_test'] = 'agent_text'


class AgentRunCreate(BaseModel):
    """Require an explicit bounded model invocation using a saved Agent revision."""
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    request_key: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1,max_length=64)
    expected_price_fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    input_text: str = Field(min_length=1, max_length=32000)
    model_id: str | None = Field(default=None, min_length=1, max_length=64)
    expected_version: int = Field(ge=1)
    max_tokens: int = Field(default=512, ge=8, le=2048)
    confirm_model_call: Literal[True]


class AgentRunRetry(BaseModel):
    """Create a new full attempt only after acknowledging uncertain prior provider effects."""
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    request_key: str = Field(min_length=1, max_length=128)
    expected_price_fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    acknowledge_duplicate_charge: Literal[True]


class ModelConnectionTestCreate(BaseModel):
    """Authorize exactly one fixed eight-token text request, never a hidden generation test."""
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    request_key: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1,max_length=64)
    expected_price_fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    confirm_model_call: Literal[True]


class AgentNodeRunRead(BaseModel):
    """Keep concrete node inputs, outputs and failures next to the workflow, not task-center summaries."""
    model_config = ConfigDict(from_attributes=True)
    node_id: str
    position: int
    label: str
    type: str
    status: NodeStatus
    input_text: str
    output_text: str
    prompt: str
    parameters: dict
    usage: dict
    error: str
    started_at: datetime | None
    finished_at: datetime | None

    @field_validator('started_at', 'finished_at')
    @classmethod
    def utc_clock(cls, value):
        """SQLite stores UTC without an offset; return an unambiguous browser timestamp."""
        return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


class AgentRunRead(BaseModel):
    """Return durable state and safe binding metadata, excluding stored config internals and leases."""
    model_config = ConfigDict(from_attributes=True)
    id: str
    kind: Literal['workflow', 'connection_test','script_candidate']
    agent_id: str | None
    agent_name: str
    agent_version: int
    model_id: str
    model_name: str
    status: RunStatus
    input_text: str
    output_text: str
    error: str
    max_tokens: int
    attempt: int
    retry_of: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    nodes: list[AgentNodeRunRead] = Field(default_factory=list)
    project_id: str | None = None
    authorized_max_minor: str | None = None

    @field_validator('created_at', 'started_at', 'finished_at')
    @classmethod
    def utc_clock(cls, value):
        """Normalize all persistent run clocks to UTC for refresh and history views."""
        return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value
