"""Trusted trial lifecycle and safe public status; monetary authorization remains with each original project."""
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,StrictBool,StrictInt


class TrialRegistrationPolicy(BaseModel):
    """Operator-owned finite trial settings, never accepted from a public registration request."""
    model_config=ConfigDict(extra='forbid',frozen=True)
    capacity:StrictInt=Field(default=4,ge=1,le=128)
    duration_seconds:StrictInt=Field(default=604800,ge=60,le=2592000)
    project_limit:StrictInt=Field(default=2,ge=1,le=10)


class TrialAccessRead(BaseModel):
    """Expose only the effective trusted expiry and active-space project count, without credentials or other users' identities."""
    profile:Literal['trial_v1']='trial_v1'
    expires_at:StrictInt=Field(ge=0)
    read_only:StrictBool
    project_limit:StrictInt|None=Field(default=None,ge=1,le=10)


class TrialRegistrationRead(BaseModel):
    """Explain finite registration availability, trial duration and project count; these are resource counts, not a shared money budget."""
    capacity:StrictInt=Field(ge=1,le=128)
    remaining:StrictInt=Field(ge=0,le=128)
    duration_seconds:StrictInt=Field(ge=60,le=2592000)
    project_limit:StrictInt=Field(ge=1,le=10)


class WorkspaceTrialPolicy(BaseModel):
    """Private gateway-to-owned-worker policy, fixed at account creation and not taken from HTTP headers/body/environment."""
    model_config=ConfigDict(extra='forbid',frozen=True)
    profile:Literal['trial_v1']='trial_v1'
    expires_at:StrictInt=Field(gt=0)
    project_limit:StrictInt=Field(ge=1,le=10)
