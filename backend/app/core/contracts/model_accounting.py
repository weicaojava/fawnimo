"""Trusted server quote envelopes and typed call units; these are not client-supplied prices."""
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_serializer, model_validator

ModelCallKind = Literal['agent_text','model_connection_test','asset_image','video_vision','video_asr','native_video']


class ModelPriceEnvelope(BaseModel):
    """Pin a positive per-call upper bound to one operator-selected model/account revision."""

    model_config = ConfigDict(extra='forbid',str_strip_whitespace=True,frozen=True)
    model_id: str = Field(min_length=1,max_length=64)
    model_name: str | None = Field(default=None,min_length=1,max_length=255)
    provider_id: str = Field(min_length=1,max_length=64)
    provider_connection_version: StrictInt = Field(ge=0,le=9223372036854775806)
    account_version: str = Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')
    currency: Literal['CNY'] = 'CNY'
    max_call_minor: StrictInt = Field(ge=1,le=100_000_000)
    source_note: str = Field(min_length=1,max_length=1000)

    @model_serializer(mode='wrap')
    def legacy_compatible_dump(self,handler):
        """Preserve exact historical quote fingerprints/manifests; unbound legacy quotes cannot authorize new calls."""
        data=handler(self)
        if self.model_name is None:
            data.pop('model_name',None)
        return data

    @property
    def fingerprint(self) -> str:
        """Identify the complete inert pricing policy without any credential or key digest."""
        value=json.dumps(self.model_dump(),ensure_ascii=False,sort_keys=True,separators=(',',':'))
        return hashlib.sha256(value.encode()).hexdigest()


class QuotedModelCall(BaseModel):
    """Describe one pre-reserved node/request within an immutable bounded execution batch."""

    model_config = ConfigDict(extra='forbid',str_strip_whitespace=True,frozen=True)
    unit_key: str = Field(min_length=1,max_length=128)
    kind: ModelCallKind
    binding_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    quote: ModelPriceEnvelope

    @field_validator('unit_key')
    @classmethod
    def inert_unit_key(cls,value):
        """Allow saved node identifiers, never a filesystem or URL routing instruction."""
        if any(ord(character)<32 for character in value):
            raise ValueError('Call unit identifier contains control characters')
        return value


class ModelPriceRule(BaseModel):
    """Opt specific call protocols into a server-owned conservative price envelope."""

    model_config = ConfigDict(extra='forbid',frozen=True)
    quote: ModelPriceEnvelope
    kinds: list[ModelCallKind] = Field(min_length=1,max_length=6)

    @model_validator(mode='after')
    def distinct_kinds(self):
        """A protocol/model pair cannot obtain ambiguous competing quotes."""
        if len(self.kinds)!=len(set(self.kinds)):
            raise ValueError('Duplicate model call kinds')
        return self


class ModelPriceConfiguration(BaseModel):
    """Disabled by default; a key or a project budget cannot imply priced execution permission."""

    model_config = ConfigDict(extra='forbid',frozen=True)
    schema_version: Literal[1] = 1
    enabled: StrictBool = False
    rules: list[ModelPriceRule] = Field(default_factory=list,max_length=128)

    @model_validator(mode='after')
    def no_ambiguous_rules(self):
        """Require exactly one quote for each explicitly enabled protocol/model pair."""
        identities=[(kind,rule.quote.model_id) for rule in self.rules for kind in rule.kinds]
        if len(identities)!=len(set(identities)) or (self.enabled and not self.rules):
            raise ValueError('Missing or ambiguous model price rules')
        return self


class BillingStatementContext(BaseModel):
    """Operator-selected historical account identity; imports never infer it from current credentials."""

    model_config = ConfigDict(extra='forbid',str_strip_whitespace=True,frozen=True)
    provider_id: str = Field(min_length=1,max_length=64)
    provider_connection_version: StrictInt = Field(ge=0,le=9223372036854775806)
    account_version: str = Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')
    statement_key: str = Field(min_length=1,max_length=128)


class NormalizedBillingLine(BaseModel):
    """One explicit original-request total in integer CNY minor units, including genuine zero charges."""

    model_config = ConfigDict(extra='forbid',str_strip_whitespace=True,frozen=True)
    row_reference: str = Field(pattern=r'^[A-Za-z0-9_.:-]{1,128}$')
    provider_request_id: str = Field(pattern=r'^[A-Za-z0-9_.:-]{1,128}$')
    status: Literal['charged','not_charged']
    currency: Literal['CNY'] = 'CNY'
    amount_minor: StrictInt = Field(ge=0,le=2**62-1)

    @model_validator(mode='after')
    def explicit_no_charge(self):
        """A failure/absence cannot be rewritten as a refund; only an explicit zero row can release funds."""
        if self.status=='not_charged' and self.amount_minor!=0:
            raise ValueError('A no-charge line must explicitly record zero')
        return self
