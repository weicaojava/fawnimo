"""Public identity contracts shared by the gateway and generated studio client."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    """Bound account and password inputs without permitting privilege or workspace fields."""
    model_config = ConfigDict(extra="forbid")
    account: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12, max_length=256)


class RegisterRequest(LoginRequest):
    """Create a personal workspace with an optional public display name."""
    display_name: str | None = Field(None, min_length=1, max_length=100)


class ProfileRequest(BaseModel):
    """Persist preferences on the identity rather than inside private project databases."""
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(None, min_length=1, max_length=100)
    language: Literal['zh-CN', 'en-US'] | None = None
    theme: Literal['light', 'dark'] | None = None


class WorkspaceSwitch(BaseModel):
    """Select a membership ID, never a disk path or backend address."""
    model_config = ConfigDict(extra="forbid")
    workspace_id: str = Field(pattern=r'^ws_[a-f0-9]{32}$')


class IdentityUser(BaseModel):
    """Safe account fields; password hashes and session tokens are never serialized."""
    id: str
    account: str
    display_name: str
    language: Literal['zh-CN', 'en-US']
    theme: Literal['light', 'dark']


class IdentityWorkspace(BaseModel):
    """Workspace selector entry derived from current trusted membership."""
    id: str
    name: str
    role: Literal['owner', 'editor', 'viewer']


class IdentityRead(BaseModel):
    """An authenticated principal and the exact active scope for stale-tab protection."""
    user: IdentityUser
    workspace_id: str
    role: Literal['owner', 'editor', 'viewer']
    workspaces: list[IdentityWorkspace]


class IdentityStatus(BaseModel):
    """Distinguish the supported legacy local studio from a login-required gateway."""
    mode: Literal['local', 'workspace']
    registration_enabled: bool


class DemoCreate(BaseModel):
    """Recover the same example request after a lost response without generating a new project."""
    model_config = ConfigDict(extra='forbid')
    request_key: str = Field(pattern=r'^[A-Za-z0-9_-]{8,128}$')


class DemoRead(BaseModel):
    """Describe the editable original text example without implying model or film acceptance."""
    project_id: str
    chapter_id: str
    entry: str
    shot_count: int
    editable_prompt_count: int
    replayed: bool
    notice: str
