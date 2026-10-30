"""Maintainer-owned public-repository intake contracts, separate from personal video workspaces."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator


class CommunityIntakeConfiguration(BaseModel):
    """Fix one public repository and its explicit proposal label; configuration cannot contain secrets or worker commands."""
    model_config = ConfigDict(extra='forbid', frozen=True)
    schema_version: Literal[1] = 1
    enabled: StrictBool = False
    repository_id: StrictInt | None = Field(default=None, ge=1)
    repository_name: str = Field(default='weicaojava/fawnimo', pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$')
    proposal_label: str = Field(default='enhancement', min_length=1, max_length=64)

    @field_validator('schema_version', mode='before')
    @classmethod
    def exact_version(cls, value):
        """Reject schema coercions rather than treating a boolean as schema one."""
        if type(value) is not int or value != 1:
            raise ValueError('Invalid community intake schema')
        return value

    @model_validator(mode='after')
    def configured_target(self):
        """Enabling requires the repository's immutable numeric identity as well as its visible name."""
        if self.enabled and self.repository_id is None:
            raise ValueError('Enabled intake requires a repository identity')
        if self.proposal_label != self.proposal_label.strip() or any(ord(c) < 32 for c in self.proposal_label):
            raise ValueError('Invalid proposal label')
        return self


class CommunityDeliveryReceipt(BaseModel):
    """A committed event acknowledgement exposes no signature, secret, raw payload or execution entitlement."""
    delivery_id: str
    replayed: bool
    outcome: Literal['recorded', 'duplicate_version', 'stale_version', 'source_conflict', 'ignored']
    request_id: str | None = None
    version_id: str | None = None


class CommunityRequestSummary(BaseModel):
    """Read only public requirement metadata; development, acceptance and deployment remain distinct future states."""
    id: str
    issue_number: int
    source_url: str
    current_version: str | None
    title: str | None
    source_state: str | None
    source_eligible: bool
    requires_refresh: bool
    received_versions: int
