"""Trusted operator image-admission configuration and private fixed-worker grant; no project budget, credential or URL is supplied here."""
import re
from typing import Annotated,Literal
from pydantic import BaseModel,ConfigDict,Field,StrictBool,StrictInt,StrictStr,field_validator

WorkspaceID=Annotated[StrictStr,Field(pattern=r'^ws_[a-f0-9]{32}$')]


class WorkspaceImageAdmissionConfiguration(BaseModel):
    """Keep new image submissions closed unless an operator names exact spaces; all money limits remain in the original project budgets."""
    model_config=ConfigDict(extra='forbid',frozen=True)
    profile:Literal['workspace_asset_image_admission_v1']='workspace_asset_image_admission_v1'
    schema_version:StrictInt=Field(default=1,ge=1,le=1)
    enabled:StrictBool=False
    workspace_ids:tuple[WorkspaceID,...]=Field(default=(),max_length=16)

    @field_validator('workspace_ids')
    @classmethod
    def unique_workspaces(cls,value):
        """Reject duplicate scope declarations instead of silently rewriting the operator's policy."""
        if len(set(value))!=len(value):raise ValueError('Duplicate image-admission workspace')
        return value


class WorkspaceImageAdmissionGrant(BaseModel):
    """Bind one private worker and request header to the exact trusted operator policy; this grants no other model, callback or deployment action."""
    model_config=ConfigDict(extra='forbid',frozen=True)
    profile:Literal['gateway_asset_image_grant_v1']='gateway_asset_image_grant_v1'
    workspace_id:WorkspaceID
    policy_sha256:StrictStr=Field(pattern=r'^[a-f0-9]{64}$')

    @classmethod
    def from_private_message(cls,value,workspace_id):
        """A startup payload cannot relabel another workspace's grant or supply additional transport/fee fields."""
        checked=cls.model_validate(value)
        if checked.workspace_id!=workspace_id:raise ValueError('Image admission belongs to another workspace')
        return checked


def image_submission_path(method,path):
    """Allow only explicit POST asset-slot jobs/retries; no callback, legacy task, arbitrary route or other billable model is covered."""
    return method=='POST' and isinstance(path,str) and re.fullmatch(
        r'/api/v1/asset-image-generation/(?:assets/(?:actor|costume|scene|prop)/[^/]+/slots/[0-9]+/jobs|jobs/[^/]+/retry)/?',path) is not None
