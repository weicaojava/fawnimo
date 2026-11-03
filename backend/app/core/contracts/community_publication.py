"""Maintainer-owned candidate publication policy and exact original input, with no reviewer/merge/deploy authority."""
from typing import Annotated,Literal
from pydantic import BaseModel,ConfigDict,Field,StrictBool,StrictInt,StrictStr,field_validator,model_validator
from app.core.contracts.community_review_gates import TrustedWorkflowRule,WORKFLOW_REQUIREMENTS


class CommunityPublicationPolicy(BaseModel):
    """Keep remote writes closed until a maintainer pins the repository, checked runtime and both immutable workflows."""
    model_config=ConfigDict(extra='forbid',frozen=True)
    profile:Literal['github_candidate_publication_v1']='github_candidate_publication_v1'
    repository_id:StrictInt=Field(gt=0,le=9223372036854775807)
    repository_name:StrictStr=Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$',max_length=201)
    enabled:StrictBool=False
    trusted_workflows:tuple[TrustedWorkflowRule,...]=Field(default=(),max_length=2)
    trusted_check_image_ids:tuple[Annotated[StrictStr,Field(pattern=r'^sha256:[0-9a-f]{64}$')],...]=Field(default=(),max_length=4)
    trusted_runner_sha256:StrictStr|None=Field(default=None,pattern=r'^[0-9a-f]{64}$')
    max_publications:StrictInt=Field(default=64,ge=64,le=64)
    max_events:StrictInt=Field(default=512,ge=512,le=512)
    max_saved_bytes:StrictInt=Field(default=16777216,ge=16777216,le=16777216)
    external_fee_limit_fen:StrictInt=Field(default=0,ge=0,le=0)

    @field_validator('repository_name')
    @classmethod
    def canonical_name(cls,value):
        """Pin a canonical GitHub repository name, never an arbitrary endpoint or credential destination."""
        return value.casefold()

    @model_validator(mode='after')
    def complete_enabled_policy(self):
        """Remote publication cannot run with missing/duplicate trusted workflows or a guessed image/supervisor identity."""
        paths=[item.path for item in self.trusted_workflows]
        if len(set(paths))!=len(paths) or len(set(self.trusted_check_image_ids))!=len(self.trusted_check_image_ids):
            raise ValueError('Repeated trusted workflow or image')
        if self.enabled and (set(paths)!=set(WORKFLOW_REQUIREMENTS) or not self.trusted_check_image_ids or self.trusted_runner_sha256 is None):
            raise ValueError('Enabled publication requires both fixed workflows, checked images and supervisor')
        return self


class CommunityPublicationInput(BaseModel):
    """Only name original public artifacts; no token, URL, branch override, executable, review or successful-result field is accepted."""
    model_config=ConfigDict(extra='forbid',frozen=True)
    request_key:StrictStr=Field(min_length=8,max_length=128,pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]*$')
    bundle_id:StrictStr=Field(pattern=r'^[0-9a-f]{64}$')
    check_attempt_id:StrictStr=Field(pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')


def publication_flags():
    """Publishing a draft does not prove independent review, execution of public CI, acceptance, merge or deployment."""
    return {'independent_review_completed':False,'ci_qualified':False,'merge_authorized':False,'deployment_authorized':False,
        'external_model_calls':0,'paid_authorization':0,'full_feature_qualified':False}
