"""Explicit local-maintainer model advice; model text never grants review, queue, coding or publication authority."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator


class CommunityAdviceConfiguration(BaseModel):
    """Bind one separately configured loopback local model, installed digest and finite request quota with zero external fee."""
    model_config = ConfigDict(extra='forbid', frozen=True)
    profile: Literal['maintainer_local_ollama_advice_v1'] = 'maintainer_local_ollama_advice_v1'
    enabled: StrictBool = False
    port: StrictInt = Field(default=11434, ge=1024, le=65535)
    model: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:/-]*$')
    model_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    max_requests: StrictInt = Field(default=0, ge=0, le=1000)
    external_fee_fen: StrictInt = Field(default=0, ge=0, le=0)

    @model_validator(mode='after')
    def explicit_local_quota(self):
        """Never select a cloud-tagged model or turn a disabled/zero-quota profile into an implicit generation allowance."""
        if 'cloud' in self.model.casefold() or '//' in self.model or '..' in self.model or self.model.count(':') > 1:
            raise ValueError('Only an explicitly configured local model tag is supported')
        if self.enabled and self.max_requests == 0:
            raise ValueError('Enabled local advice requires an explicit finite request quota')
        return self


class CommunityAdviceRequest(BaseModel):
    """One original requirement version and replay key; server configuration and source context are not model instructions."""
    model_config = ConfigDict(extra='forbid', frozen=True)
    request_key: str = Field(min_length=8, max_length=128, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]*$')
    version_id: str = Field(min_length=1, max_length=64)


class CommunityModelAdvice(BaseModel):
    """An untrusted advisory proposal, requiring explicit maintainer review before any existing decision or queue write."""
    model_config = ConfigDict(extra='forbid', frozen=True)
    suggested_decision: Literal['reasonable','needs_clarification','not_actionable','duplicate']
    reason: str = Field(min_length=1,max_length=4000)
    feature_ids: list[str] = Field(default_factory=list,max_length=16)
    acceptance: list[str] = Field(default_factory=list,max_length=16)
    questions: list[str] = Field(default_factory=list,max_length=8)
    duplicate_version_id: str | None = Field(default=None,min_length=1,max_length=64)

    @field_validator('reason')
    @classmethod
    def meaningful_reason(cls, value):
        """Store bounded ordinary UTF8 text without executing or interpreting model-written commands."""
        value.encode('utf8')
        if not value.strip() or '\x00' in value:
            raise ValueError('Invalid model advice reason')
        return value

    @field_validator('acceptance','questions')
    @classmethod
    def bounded_statements(cls, values):
        """Require concise individual advisory criteria or clarification questions."""
        for value in values:
            value.encode('utf8')
            if not 1 <= len(value.strip()) <= 1000 or '\x00' in value:
                raise ValueError('Invalid model advice statement')
        return values

    @model_validator(mode='after')
    def advisory_context(self):
        """Keep feature references, duplicate choices and missing-information questions explicit and internally consistent."""
        import re
        if len(set(self.feature_ids)) != len(self.feature_ids) or any(re.fullmatch(r'F-\d{3}\.\d{2}',value) is None for value in self.feature_ids):
            raise ValueError('Invalid model feature references')
        if self.suggested_decision == 'reasonable' and (not self.feature_ids or not self.acceptance):
            raise ValueError('Reasonable advice requires feature references and acceptance criteria')
        if bool(self.questions) != (self.suggested_decision == 'needs_clarification'):
            raise ValueError('Clarification advice needs explicit questions')
        if (self.duplicate_version_id is not None) != (self.suggested_decision == 'duplicate'):
            raise ValueError('Duplicate advice needs an explicit candidate version')
        if len(self.model_dump_json().encode('utf8')) > 16384:
            raise ValueError('Model advice exceeds its bounded output profile')
        return self


class CommunityAdviceReceipt(BaseModel):
    """A validated immutable local outcome; ready advice still grants no application or independent-review authority."""
    model_config = ConfigDict(extra='forbid', frozen=True)
    state: Literal['ready','not_submitted','uncertain']
    submitted: StrictBool
    advice: CommunityModelAdvice | None = None
    usage: dict[str,StrictInt] | None = None
    model_digest: str | None = Field(default=None,pattern=r'^[a-f0-9]{64}$')
    response_sha256: str | None = Field(default=None,pattern=r'^[a-f0-9]{64}$')
    error: Literal['local_advice_not_completed','local_advice_outcome_not_confirmed'] | None = None
    execution_authorized: Literal[False] = False
    external_fee_fen: StrictInt = Field(default=0,ge=0,le=0)
    model_quality_qualified: Literal[False] = False
    independent_review_completed: Literal[False] = False
    public_feedback_sent: Literal[False] = False
    source_current_at_completion: StrictBool
    requires_fresh_source_for_maintainer_decision: Literal[True] = True

    @model_validator(mode='after')
    def original_outcome(self):
        """Ready needs complete bounded evidence; failed/uncertain outcomes cannot carry a ready model decision."""
        evidence = (self.advice,self.usage,self.model_digest,self.response_sha256)
        if self.state == 'ready':
            if not self.submitted or self.error is not None or any(value is None for value in evidence):
                raise ValueError('Ready advice requires the original complete evidence')
            if set(self.usage) != {'prompt_eval_count','eval_count'} or any(not 0 <= value <= 1000000 for value in self.usage.values()):
                raise ValueError('Invalid original advice usage')
        elif self.error is None or any(value is not None for value in evidence) or (self.state == 'uncertain') != self.submitted:
            raise ValueError('Failed advice cannot become a ready model suggestion')
        return self
