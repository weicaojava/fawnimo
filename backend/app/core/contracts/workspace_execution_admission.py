"""Explicit operator capabilities for fixed-workspace execution; all monetary authority remains in original per-project charge policies."""
import re
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,StrictBool,StrictInt,StrictStr,field_validator
from app.core.contracts.workspace_image_admission import WorkspaceID

ExecutionOperation=Literal['agent_execution','script_preparation','video_understanding','native_video']


class WorkspaceExecutionAdmissionConfiguration(BaseModel):
    """Keep every capability disabled by default; exact spaces and named operation families cannot supply URLs, credentials or a workspace money ceiling."""
    model_config=ConfigDict(extra='forbid',frozen=True)
    profile:Literal['workspace_execution_admission_v1']='workspace_execution_admission_v1'
    schema_version:StrictInt=Field(default=1,ge=1,le=1)
    enabled:StrictBool=False
    workspace_ids:tuple[WorkspaceID,...]=Field(default=(),max_length=16)
    operations:tuple[ExecutionOperation,...]=Field(default=(),max_length=4)

    @field_validator('workspace_ids','operations')
    @classmethod
    def unique_items(cls,value):
        """Refuse conflicting/repeated declarations rather than silently rewriting the operator's policy."""
        if len(set(value))!=len(value):raise ValueError('Duplicate execution-admission entry')
        return value


class WorkspaceExecutionAdmissionGrant(BaseModel):
    """Bind one private fixed worker to the exact configured operation list and non-secret policy digest."""
    model_config=ConfigDict(extra='forbid',frozen=True)
    profile:Literal['gateway_execution_grant_v1']='gateway_execution_grant_v1'
    workspace_id:WorkspaceID
    policy_sha256:StrictStr=Field(pattern=r'^[a-f0-9]{64}$')
    operations:tuple[ExecutionOperation,...]=Field(min_length=1,max_length=4)

    @field_validator('operations')
    @classmethod
    def unique_operations(cls,value):
        """No repeated capability can broaden the immutable private startup grant."""
        if len(set(value))!=len(value):raise ValueError('Duplicate execution operation')
        return value

    @classmethod
    def from_private_message(cls,value,workspace_id):
        """Check the exact worker owner before any application import, database setup or endpoint can use this private message."""
        checked=cls.model_validate(value)
        if checked.workspace_id!=workspace_id:raise ValueError('Execution admission belongs to another workspace')
        return checked


def execution_submission_operation(method,path):
    """Classify only exact explicit POST shapes; images, callbacks, legacy tasks, arbitrary endpoints and other methods receive no authority."""
    if method!='POST' or not isinstance(path,str):return None
    routes=(
        ('agent_execution',r'/api/v1/agent-execution/(?:agents/[^/]+/runs|models/[^/]+/connection-tests|runs/[^/]+/retry)/?'),
        ('script_preparation',r'/api/v1/script-preparation/projects/[^/]+/chapters/[^/]+/candidates/?'),
        ('video_understanding',r'/api/v1/video-understanding/projects/[^/]+/jobs(?:/[^/]+/retry)?/?'),
        ('native_video',r'/api/v1/(?:execution/projects/[^/]+/submissions|shot-plans/projects/[^/]+/plans/[^/]+/shots/[^/]+/submissions)/?'),
    )
    return next((operation for operation,pattern in routes if re.fullmatch(pattern,path)),None)
