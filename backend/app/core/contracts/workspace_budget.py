"""Public workspace accounting views, expressed in integer CNY minor units."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt


class WorkspaceBudgetWrite(BaseModel):
    """Change only a spending ceiling from a known revision; never submit or settle work."""
    model_config = ConfigDict(extra='forbid')
    expected_revision: StrictInt = Field(ge=0)
    limit_minor: StrictInt = Field(ge=0, le=100_000_000)


class WorkspaceBudgetRead(BaseModel):
    """Keep application balances separate from unverified provider bills and runtime permission."""
    managed: bool
    currency: Literal['CNY'] = 'CNY'
    limit_minor: int
    reserved_minor: int
    spent_minor: int
    available_minor: int
    revision: int
    unsettled_jobs: int
    uncertain_jobs: int
    provider_bill_verified: Literal[False] = False
    coverage: Literal['durable_video'] = 'durable_video'


class WorkspaceBudgetEntryRead(BaseModel):
    """Expose ledger identifiers and amounts without remote IDs, prompts, URLs or credentials."""
    job_id: str
    project_id: str
    project_name: str | None
    state: Literal['reserved', 'unknown', 'released', 'settled']
    quote_minor: int
    actual_minor: int | None


class WorkspaceBudgetPage(BaseModel):
    """Bound the response and provide a stable job-ID cursor for subsequent pages."""
    items: list[WorkspaceBudgetEntryRead]
    next_after: str | None
