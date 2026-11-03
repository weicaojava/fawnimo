"""Explicit bounded operator policy and original intents for new owned local stable/preview stacks, never public promotion or model authority."""
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,StrictBool,StrictInt,StrictStr,model_validator


class RuntimeImageBinding(BaseModel):
    """An operator explicitly binds a pre-existing immutable image to a public source revision; this is not an inferred CI approval."""
    model_config=ConfigDict(extra='forbid',frozen=True)
    image_id:StrictStr=Field(pattern=r'^sha256:[a-f0-9]{64}$')
    source_commit:StrictStr=Field(pattern=r'^[a-f0-9]{40}$')


class LocalRuntimePolicy(BaseModel):
    """Default-disabled new-resource policy; no ambient tags, Docker credentials, existing services or other project resources are adopted."""
    model_config=ConfigDict(extra='forbid',frozen=True)
    schema_version:StrictInt=Field(default=1,ge=1,le=1)
    profile:Literal['local_runtime_release_v1']='local_runtime_release_v1'
    repository:Literal['weicaojava/fawnimo']='weicaojava/fawnimo'
    enabled:StrictBool=False
    operator_allowed_images:tuple[RuntimeImageBinding,...]=Field(default=(),max_length=8)
    helper_image_id:StrictStr|None=Field(default=None,pattern=r'^sha256:[a-f0-9]{64}$')
    bootstrap_sha256:StrictStr|None=Field(default=None,pattern=r'^[a-f0-9]{64}$')
    stable_port:StrictInt=Field(default=18080,ge=1024,le=65535)
    preview_port:StrictInt=Field(default=18081,ge=1024,le=65535)
    max_operations:StrictInt=Field(default=16,ge=16,le=16)
    max_generations_per_slot:StrictInt=Field(default=8,ge=8,le=8)
    external_fee_limit_fen:StrictInt=Field(default=0,ge=0,le=0)
    automatic_public_promotion:Literal[False]=False

    @model_validator(mode='after')
    def fixed_authority(self):
        """Reject ambiguous images, original application ports and half-enabled policies before creating resources or opening an engine."""
        images=[image.image_id for image in self.operator_allowed_images]
        if len(images)!=len(set(images)):raise ValueError('Each immutable image may have only one explicit source binding')
        if self.stable_port==self.preview_port or {self.stable_port,self.preview_port}&{8000,17888,18000}:
            raise ValueError('Local slots require distinct new ports, separate from the original application')
        if self.enabled and (not images or self.helper_image_id not in images or self.bootstrap_sha256 is None):
            raise ValueError('Enabled local runtime requires explicitly bound images, trusted helper and bootstrap identity')
        return self

    def image(self,identifier):
        """Select only a fixed explicitly allowed immutable image, without resolving tags, pulling or discovering credentials."""
        for image in self.operator_allowed_images:
            if image.image_id==identifier:return image
        raise ValueError('Image is outside the explicit local runtime policy')

    def origin(self,slot):
        """Separate host-only browser cookies between stable and preview without editing host configuration."""
        if slot=='stable':return f'http://127.0.0.1:{self.stable_port}'
        if slot=='preview':return f'http://127.0.0.2:{self.preview_port}'
        raise ValueError('Unknown local runtime slot')


class LocalRuntimeIntent(BaseModel):
    """Bind a finite operation to one logical slot and observed generation; paths, tokens, commands and resource overrides are forbidden."""
    model_config=ConfigDict(extra='forbid',frozen=True)
    request_key:StrictStr=Field(min_length=8,max_length=128,pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]*$')
    slot:Literal['stable','preview']
    expected_generation:StrictInt=Field(ge=0,le=8)
    reason:StrictStr=Field(min_length=1,max_length=1000)


class LocalRuntimeActivation(LocalRuntimeIntent):
    """Choose an already allowed image while copying data only within the same owned logical slot."""
    image_id:StrictStr=Field(pattern=r'^sha256:[a-f0-9]{64}$')


class LocalRuntimeRollback(LocalRuntimeIntent):
    """Choose an immutable same-slot private archive; its original image is rechecked against policy, never supplied as an override."""
    snapshot_id:StrictStr=Field(pattern=r'^[a-f0-9]{64}$')
    acknowledge_retained_later_edits:StrictBool=False

    @model_validator(mode='after')
    def explicit_old_data(self):
        """Require an existing generation and acknowledgment that later edits remain in its stopped original volume, without an implicit merge."""
        if self.expected_generation<1 or not self.acknowledge_retained_later_edits:
            raise ValueError('Rollback requires an exact existing generation and acknowledgment of retained later edits')
        return self


class LocalRuntimeStop(LocalRuntimeIntent):
    """Stop only the observed current generation, preserving data, archives, unknown resources and later activations."""

    @model_validator(mode='after')
    def existing_generation(self):
        """An empty slot cannot be interpreted as an existing deployment to stop."""
        if self.expected_generation<1:raise ValueError('An existing exact slot generation is required')
        return self


class LocalRuntimeQuiescence(BaseModel):
    """Explicitly stop the original interrupted operation's owned workloads after a fresh read-only state observation; retain all data."""
    model_config=ConfigDict(extra='forbid',frozen=True)
    request_key:StrictStr=Field(min_length=8,max_length=128,pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]*$')
    operation_id:StrictStr=Field(pattern=r'^[a-f0-9]{64}$')
    expected_observation_sha256:StrictStr=Field(pattern=r'^[a-f0-9]{64}$')
    acknowledge_stopped_data_retained:StrictBool
    reason:StrictStr=Field(min_length=1,max_length=1000)

    @model_validator(mode='after')
    def explicit_stopped_data(self):
        """Require an actual true boolean, never numeric/string coercion, for the separate explicit stop action."""
        if self.acknowledge_stopped_data_retained is not True:
            raise ValueError('Explicit quiescence must acknowledge stopped services and retained data')
        return self
