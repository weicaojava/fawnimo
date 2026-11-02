"""Local maintainer decisions are immutable source-bound records, never worker commands or deployment authority."""
from typing import Literal
import re
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CommunityEvaluationInput(BaseModel):
    """Separate reasonableness from priority and retain clarification/appeal reasons against an exact issue version."""
    model_config = ConfigDict(extra='forbid', frozen=True)
    request_key: str = Field(min_length=8, max_length=128, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]*$')
    version_id: str = Field(min_length=1, max_length=64)
    decision: Literal['reasonable', 'needs_clarification', 'not_actionable', 'duplicate']
    reason: str = Field(min_length=1, max_length=4000)
    feature_ids: list[str] = Field(default_factory=list, max_length=16)
    acceptance: list[str] = Field(default_factory=list, max_length=16)
    questions: list[str] = Field(default_factory=list, max_length=8)
    duplicate_version_id: str | None = Field(default=None, min_length=1, max_length=64)
    appeal_of: str | None = Field(default=None, min_length=1, max_length=64)
    appeal_reason: str | None = Field(default=None, min_length=1, max_length=4000)
    advisory_note: str | None = Field(default=None, min_length=1, max_length=4000)

    @field_validator('reason', 'appeal_reason', 'advisory_note')
    @classmethod
    def meaningful_text(cls, value):
        """Retain data as text, rejecting empty or non-UTF8 input without interpreting instructions."""
        if value is not None:
            value.encode('utf8')
            if not value.strip() or '\x00' in value: raise ValueError('Evaluation text is empty or invalid')
        return value

    @field_validator('acceptance', 'questions')
    @classmethod
    def bounded_lines(cls, value):
        """Require useful individual review/clarification statements within a fixed total input size."""
        for line in value:
            line.encode('utf8')
            if not 1 <= len(line.strip()) <= 1000 or '\x00' in line: raise ValueError('Invalid evaluation statement')
        return value

    @model_validator(mode='after')
    def explicit_decision_context(self):
        """No advice automatically becomes an accepted decision; duplicates, questions and reconsiderations stay explicit."""
        if len(set(self.feature_ids)) != len(self.feature_ids): raise ValueError('Repeated feature identity')
        if any(re.fullmatch(r'F-\d{3}\.\d{2}', item) is None for item in self.feature_ids): raise ValueError('Invalid feature identity')
        if self.decision == 'reasonable' and (not self.feature_ids or not self.acceptance): raise ValueError('Reasonable requests need features and acceptance criteria')
        if bool(self.questions) != (self.decision == 'needs_clarification'): raise ValueError('Clarification requires explicit questions')
        if (self.duplicate_version_id is not None) != (self.decision == 'duplicate'): raise ValueError('Duplicate decisions need an explicit other version')
        if (self.appeal_of is None) != (self.appeal_reason is None): raise ValueError('Reconsideration requires the original evaluation and reason')
        if len(self.model_dump_json().encode('utf8')) > 32768: raise ValueError('Evaluation exceeds32KiB')
        return self
