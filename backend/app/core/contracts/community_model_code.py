"""Bounded local-model Python proposals from an exact prepared public requirement; model output is inert source text."""
import re
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,StrictInt,StrictBool,field_validator,model_validator
from app.core.contracts.community_advice import CommunityAdviceConfiguration
from app.core.contracts.community_changes import code_path


class CommunityCodeModelConfiguration(CommunityAdviceConfiguration):
    """Select a separate explicit local coding model/digest and finite call quota without any paid or remote fallback."""
    profile: Literal['maintainer_local_ollama_code_v1'] = 'maintainer_local_ollama_code_v1'
    max_requests: StrictInt = Field(default=0,ge=0,le=128)


class CommunityCodeModelRequest(BaseModel):
    """Pin one original prepared work and a small explicit set of source/new-test paths; commands and URLs are never accepted."""
    model_config=ConfigDict(extra='forbid',frozen=True)
    request_key: str = Field(min_length=8,max_length=96,pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]*$')
    work_id: str = Field(min_length=1,max_length=64)
    paths: list[str] = Field(min_length=2,max_length=4)
    test_files: list[str] = Field(min_length=1,max_length=2)

    @model_validator(mode='after')
    def explicit_small_scope(self):
        """Code and tests must be distinct, canonical Python targets with at least one actual source path and explicit new candidate tests."""
        if len(set(self.paths)) != len(self.paths) or len(set(self.test_files)) != len(self.test_files):
            raise ValueError('Repeated local code proposal path')
        for path in self.paths:
            code_path(path)
            if not path.endswith('.py'):
                raise ValueError('Local code proposal accepts Python text only')
        if not set(self.test_files) < set(self.paths) or any(re.fullmatch(r'backend/tests/test_community_candidate_[a-z0-9_]+\.py',path) is None for path in self.test_files):
            raise ValueError('Explicit original source and new candidate tests are required')
        return self


class CommunityModelFileText(BaseModel):
    """One complete proposed LF Python replacement, never a script executed by the model controller."""
    model_config=ConfigDict(extra='forbid',frozen=True)
    path: str
    text: str

    @field_validator('path')
    @classmethod
    def canonical_python_path(cls,value):
        """Canonical path syntax is checked before source authority is separately compared with the original selection."""
        code_path(value)
        if not value.endswith('.py'):
            raise ValueError('Local code proposal accepts Python text only')
        return value

    @field_validator('text')
    @classmethod
    def complete_bounded_text(cls,value):
        """Refuse oversized, partial or ambiguously normalized model source rather than silently repairing its output."""
        if not value or len(value.encode('utf8'))>32768 or '\x00' in value or '\r' in value or not value.endswith('\n'):
            raise ValueError('Local model must return complete bounded LF source text')
        return value


class CommunityModelCodeProposal(BaseModel):
    """A strict untrusted title and exact replacement set; origin, work, old hashes and test identity remain server-owned."""
    model_config=ConfigDict(extra='forbid',frozen=True)
    title: str = Field(min_length=1,max_length=120)
    files: list[CommunityModelFileText] = Field(min_length=2,max_length=4)

    @model_validator(mode='after')
    def finite_inert_proposal(self):
        """Duplicate files, invisible titles and output beyond the small local profile never become a change bundle."""
        if not self.title.strip() or any(ord(char)<32 for char in self.title):
            raise ValueError('Invalid local code proposal title')
        if len({item.path for item in self.files}) != len(self.files) or sum(len(item.text.encode('utf8')) for item in self.files)>49152:
            raise ValueError('Repeated or oversized local code proposal files')
        return self


class CommunityCodeModelResult(BaseModel):
    """An immutable local inference outcome; generated means complete inert text and never successful coding or execution."""
    model_config=ConfigDict(extra='forbid',frozen=True)
    state: Literal['generated','not_submitted','uncertain']
    submitted: StrictBool
    proposal: CommunityModelCodeProposal | None = None
    usage: dict[str,StrictInt] | None = None
    model_digest: str | None = Field(default=None,pattern=r'^[a-f0-9]{64}$')
    response_sha256: str | None = Field(default=None,pattern=r'^[a-f0-9]{64}$')
    error: Literal['local_code_model_not_completed','local_code_model_outcome_not_confirmed'] | None = None
    external_fee_fen: StrictInt = Field(default=0,ge=0,le=0)
    execution_authorized: Literal[False] = False
    model_quality_qualified: Literal[False] = False
    independent_review_completed: Literal[False] = False

    @model_validator(mode='after')
    def complete_original_outcome(self):
        """Complete text requires the original model/usage evidence; an ambiguous result cannot masquerade as generated code."""
        evidence=(self.proposal,self.usage,self.model_digest,self.response_sha256)
        if self.state=='generated':
            if not self.submitted or self.error is not None or any(value is None for value in evidence):
                raise ValueError('Generated source requires complete original evidence')
            if set(self.usage)!={'prompt_eval_count','eval_count'} or not 0<=self.usage['prompt_eval_count']<=1000000 or not 0<=self.usage['eval_count']<=4096:
                raise ValueError('Invalid local coding-model usage')
        elif self.error is None or any(value is not None for value in evidence) or (self.state=='uncertain')!=self.submitted:
            raise ValueError('Failed inference cannot carry generated source')
        return self
