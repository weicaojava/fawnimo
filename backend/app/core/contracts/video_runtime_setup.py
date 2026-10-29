"""Public non-secret video setup and explicit project budget contracts."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from app.core.contracts.shot_plan import PlanIssue


class VideoRuntimeConfiguration(BaseModel):
    """A server-owned configuration file; project imports cannot supply credentials or origins."""
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal[1] = 1
    enabled: StrictBool = False
    credential_env: str = Field('JELLYFISH_VIDEO_API_KEY', pattern=r'^[A-Z][A-Z0-9_]{2,127}$')
    service_version: str = Field('workspace-seedance-v1', pattern=r'^[A-Za-z0-9_-]{1,128}$')
    account_version: str = Field('workspace-account-v1', pattern=r'^[A-Za-z0-9_-]{1,128}$')
    quote_minor: StrictInt | None = Field(None, ge=1, le=100_000_000)
    quote_note: str = Field('', max_length=500)
    iteration_limit: StrictInt = Field(10, ge=1, le=1000)


class VideoRuntimeSetupRead(BaseModel):
    """Expose configuration readiness without credential values, paths or upstream error bodies."""
    configured: bool
    enabled: bool
    running: bool
    issue_code: str
    message: str
    model_id: str
    durations: list[int]
    quote_minor: int | None
    quote_note: str
    iteration_limit: int
    credential_present: bool
    output_description: str
    real_model_qualified: Literal[False] = False
    legacy_actions_available: Literal[False] = False


class ProjectBudgetWrite(BaseModel):
    """Save a ceiling only; this operation never submits work, releases holds or settles a bill."""
    model_config = ConfigDict(extra='forbid')
    expected_revision: StrictInt = Field(ge=0)
    limit_minor: StrictInt = Field(ge=0, le=100_000_000)


class ProjectBudgetRead(BaseModel):
    """Distinguish the application ledger from unverified provider billing."""
    project_id: str
    exists: bool
    currency: str
    limit_minor: int
    reserved_minor: int
    spent_minor: int
    available_minor: int
    revision: int
    unsettled_jobs: int
    uncertain_jobs: int
    provider_bill_verified: Literal[False] = False
    coverage: Literal['durable_video'] = 'durable_video'


class ProjectBudgetEntryRead(BaseModel):
    """Expose one project's amount/state without remote identifiers, prompts or credentials."""
    job_id: str
    project_id: str
    state: Literal['reserved', 'unknown', 'released', 'settled']
    quote_minor: int
    actual_minor: int | None


class ProjectBudgetPage(BaseModel):
    """Bound the current project's ledger and paginate by immutable job identity."""
    items: list[ProjectBudgetEntryRead]
    next_after: str | None


class VideoExecutionPreviewEntry(BaseModel):
    """Inspect the actual fixed wire body from an owned immutable compiled entry."""
    shot_id: str
    entry_hash: str
    body: dict | None
    issues: list[PlanIssue]


class VideoExecutionPlanPreview(BaseModel):
    """A read-only executable-profile preview never grants submission or quality qualification."""
    plan_id: str
    content_hash: str
    wire_hash: str | None = None
    can_prepare: bool
    can_submit: Literal[False] = False
    execution_enabled: bool
    model_qualified: Literal[False] = False
    issues: list[PlanIssue]
    entries: list[VideoExecutionPreviewEntry]
