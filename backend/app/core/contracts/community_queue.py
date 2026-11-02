"""Strict maintainer queue estimates and finite server policy, separate from project budgets or execution permission."""
import re
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,StrictInt,field_validator,model_validator


class CommunityQueuePolicy(BaseModel):
    """A dedicated queue's hard operational limits; the current profile authorizes zero external model expense."""
    model_config=ConfigDict(extra='forbid',frozen=True)
    schema_version:StrictInt=Field(default=1,ge=1,le=1)
    max_concurrent:StrictInt=Field(default=1,ge=1,le=4)
    max_attempts:StrictInt=Field(default=3,ge=1,le=3)
    lease_seconds:StrictInt=Field(default=30,ge=5,le=60)
    attempt_timeout_seconds:StrictInt=Field(default=300,ge=10,le=900)
    max_plan_effort_minutes:StrictInt=Field(default=120,ge=1,le=480)
    max_active_effort_minutes:StrictInt=Field(default=60,ge=1,le=120)
    external_fee_limit_fen:StrictInt=Field(default=0,ge=0,le=0)

    @model_validator(mode='after')
    def lease_within_deadline(self):
        """A lease cannot promise ownership beyond the entire bounded attempt's lifetime."""
        if self.lease_seconds>self.attempt_timeout_seconds:raise ValueError('Lease exceeds attempt deadline')
        return self


class CommunityQueueProposal(BaseModel):
    """A maintainer-supplied value/effort estimate binds one exact reasonable evaluation, with explicit predecessor keys."""
    model_config=ConfigDict(extra='forbid',frozen=True)
    key:str=Field(min_length=1,max_length=64,pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]*$')
    evaluation_id:str=Field(min_length=1,max_length=64)
    value_score:StrictInt=Field(ge=0,le=100)
    value_reason:str=Field(min_length=1,max_length=1000)
    effort_minutes:StrictInt=Field(ge=1,le=480)
    estimated_external_fee_fen:StrictInt=Field(default=0,ge=0,le=1000000)
    after:list[str]=Field(default_factory=list,max_length=32)

    @field_validator('value_reason')
    @classmethod
    def meaningful_reason(cls,value):
        """Value is a local maintainer judgement; require concrete text instead of an invented automatic priority."""
        value.encode('utf8')
        if not value.strip() or '\x00' in value:raise ValueError('Missing value reason')
        return value

    @field_validator('after')
    @classmethod
    def explicit_predecessors(cls,value):
        """Reject ambiguous dependency identities and duplicates before graph construction."""
        if len(set(value))!=len(value) or any(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}',key) is None for key in value):
            raise ValueError('Invalid predecessor keys')
        return value


class CommunityQueuePlanInput(BaseModel):
    """A versioned roadmap request contains bounded proposals only, never commands, credentials or deployment options."""
    model_config=ConfigDict(extra='forbid',frozen=True)
    request_key:str=Field(min_length=8,max_length=128,pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]*$')
    title:str=Field(min_length=1,max_length=120)
    proposals:list[CommunityQueueProposal]=Field(min_length=1,max_length=32)

    @model_validator(mode='after')
    def unique_entries(self):
        """Keep one explicit identity per proposal/evaluation and cap the complete persisted original intent."""
        self.title.encode('utf8')
        if not self.title.strip() or any(ord(c)<32 for c in self.title):raise ValueError('Invalid plan title')
        if len({entry.key for entry in self.proposals})!=len(self.proposals):raise ValueError('Repeated proposal key')
        if len({entry.evaluation_id for entry in self.proposals})!=len(self.proposals):raise ValueError('Repeated evaluation')
        if len(self.model_dump_json().encode('utf8'))>65536:raise ValueError('Queue intent exceeds64KiB')
        return self


class QueueOperation(BaseModel):
    """A stable original key recovers one exact operation, never a new target or retry."""
    model_config = ConfigDict(extra='forbid', frozen=True)
    request_key: str = Field(min_length=8, max_length=128, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]*$')


class QueueMaterializeInput(QueueOperation):
    """Prepare only selected entries of one exact roadmap selection; this cannot authorize a code executor."""
    plan_id: str = Field(min_length=1, max_length=64)
    expected_revision: StrictInt = Field(ge=1)


class QueueSelectionInput(QueueOperation):
    """Select or deactivate one exact plan with a compare-and-swap revision, preserving original replay semantics."""
    plan_id: str | None = Field(max_length=64)
    expected_revision: StrictInt = Field(ge=0)


class QueueWorkInput(QueueOperation):
    """Every work operation names an existing preparation record instead of an implicit next task."""
    work_id: str = Field(min_length=1, max_length=64)


class QueueClaimInput(QueueWorkInput):
    """A local owner label identifies the preparation reservation, not an authenticated GitHub identity."""
    owner: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]*$')


class QueueLeaseInput(QueueClaimInput):
    """A server-generated private token fences exactly one bounded preparation attempt."""
    attempt_id: str = Field(min_length=1, max_length=64)
    lease_token: str = Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')


class QueueFinishInput(QueueLeaseInput):
    """Prepared means the reservation's preparation ended, never implementation, review or deployment success."""
    outcome: Literal['prepared', 'failed']
    note: str = Field(min_length=1, max_length=1000)

    @field_validator('note')
    @classmethod
    def meaningful_note(cls, value):
        """Keep bounded human observations as inert text without shell or model interpretation."""
        value.encode('utf8')
        if not value.strip() or '\x00' in value:
            raise ValueError('Missing preparation result note')
        return value


class QueueCancelInput(QueueWorkInput):
    """Local cancellation releases only a preparation reservation with no authorized external side effects."""
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator('reason')
    @classmethod
    def meaningful_reason(cls, value):
        """Require a human cancellation reason while treating it only as saved data."""
        value.encode('utf8')
        if not value.strip() or '\x00' in value:
            raise ValueError('Missing cancellation reason')
        return value
