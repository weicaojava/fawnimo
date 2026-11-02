"""A separately configured read-only exact-PR gate; source, candidate output or cached decisions cannot grant review/merge authority."""
from dataclasses import dataclass
from typing import Annotated,Literal

from pydantic import BaseModel,ConfigDict,Field,StrictBool,StrictInt,StrictStr,field_validator,model_validator

PositiveID=Annotated[StrictInt,Field(gt=0,le=9223372036854775807)]
WORKFLOW_REQUIREMENTS={
    '.github/workflows/verify-fawnimo.yml':{
        'register-and-backend':('Check feature references and generated views','Verify isolated identities, workspaces and examples'),
        'frontend':('Check types and build the same-origin studio',)},
    '.github/workflows/verify-workspace-container.yml':{
        'workspace-container':('Verify Compose runtime, private accounts and persistent reconstruction',
            'Verify actual isolated candidate checks and owned cleanup without publishing')}}
WorkflowPath=Literal['.github/workflows/verify-fawnimo.yml','.github/workflows/verify-workspace-container.yml']


class TrustedWorkflowRule(BaseModel):
    """A maintainer pins the exact public workflow blob; required jobs/steps are fixed by server code, never proposed source or request JSON."""
    model_config=ConfigDict(extra='forbid',frozen=True)
    path:WorkflowPath
    blob_sha:StrictStr=Field(pattern=r'^[0-9a-f]{40}$')


class CommunityReviewGatePolicy(BaseModel):
    """Explicit trusted local policy, disabled by default; activating it requires concrete independent identities and both pinned workflows."""
    model_config=ConfigDict(extra='forbid',frozen=True)
    schema_version:StrictInt=Field(default=1,ge=1,le=1)
    profile:Literal['github_exact_pr_review_v1']='github_exact_pr_review_v1'
    repository_id:PositiveID
    repository_name:StrictStr=Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$',max_length=201)
    enabled:StrictBool=False
    independent_reviewer_ids:tuple[PositiveID,...]=Field(default=(),max_length=8)
    developer_actor_ids:tuple[PositiveID,...]=Field(default=(),max_length=16)
    trusted_workflows:tuple[TrustedWorkflowRule,...]=Field(default=(),max_length=2)
    trusted_check_image_ids:tuple[Annotated[StrictStr,Field(pattern=r'^sha256:[0-9a-f]{64}$')],...]=Field(default=(),max_length=4)
    trusted_runner_sha256:StrictStr|None=Field(default=None,pattern=r'^[0-9a-f]{64}$')
    required_approvals:StrictInt=Field(default=1,ge=1,le=1)
    max_reviews:StrictInt=Field(default=300,ge=300,le=300)
    max_workflow_runs:StrictInt=Field(default=200,ge=200,le=200)
    max_jobs:StrictInt=Field(default=100,ge=100,le=100)
    observation_seconds:StrictInt=Field(default=60,ge=60,le=60)
    max_saved_observations:StrictInt=Field(default=384,ge=384,le=384)
    max_saved_bytes:StrictInt=Field(default=16777216,ge=16777216,le=16777216)
    max_receipt_bytes:StrictInt=Field(default=65536,ge=65536,le=65536)
    external_fee_limit_fen:StrictInt=Field(default=0,ge=0,le=0)

    @field_validator('repository_name')
    @classmethod
    def canonical_repository(cls,value):
        """Repository identity comparisons and policy hashes use one canonical case without parsing URLs or selecting another host."""
        return value.casefold()

    @model_validator(mode='after')
    def independent_known_policy(self):
        """No repeated identities, developer self-approval, unknown workflow or half-enabled policy can silently widen promotion eligibility."""
        reviewers=set(self.independent_reviewer_ids); developers=set(self.developer_actor_ids)
        paths=[rule.path for rule in self.trusted_workflows]
        if len(reviewers)!=len(self.independent_reviewer_ids) or len(developers)!=len(self.developer_actor_ids) or reviewers&developers:
            raise ValueError('Reviewer/developer identities must be distinct and unambiguous')
        if len(paths)!=len(set(paths)): raise ValueError('Repeated trusted workflow')
        if len(self.trusted_check_image_ids)!=len(set(self.trusted_check_image_ids)): raise ValueError('Repeated trusted image')
        if self.enabled and (not reviewers or set(paths)!=set(WORKFLOW_REQUIREMENTS) or not self.trusted_check_image_ids or self.trusted_runner_sha256 is None):
            raise ValueError('Enabled gate requires independent identities, both pinned workflows, trusted images and supervisor identity')
        return self


class CommunityPRGateInput(BaseModel):
    """Name an existing exact check/candidate and real PR; reviewer identities, workflow results and approvals never come from this input."""
    model_config=ConfigDict(extra='forbid',frozen=True)
    request_key:StrictStr=Field(min_length=8,max_length=128,pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]*$')
    bundle_id:StrictStr=Field(pattern=r'^[0-9a-f]{64}$')
    check_attempt_id:StrictStr=Field(pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
    pr_number:StrictInt=Field(ge=1,le=2147483647)


@dataclass(frozen=True)
class ExactPublicPRObservation:
    """Internal bounded fixed-host collector facts, never parsed from an issue/candidate JSON as proof of reviewer identity or actual CI."""
    repository_id:int
    repository_name:str
    checked_at_us:int
    main_commit:str
    pull_request:dict
    tested_merge:dict
    reviews:tuple[dict,...]
    workflows:tuple[dict,...]
    response_sha256:tuple[str,...]
    protocol:str='github-public-pr-rest-2022-11-28-v1'
