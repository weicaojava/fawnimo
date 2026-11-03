"""Strict local release-data inputs, never deployment authority, model credentials or executable candidate instructions."""
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,StrictStr


class LocalReleaseRequest(BaseModel):
    """Keep original keys stable so preparation, backup and recovery cannot silently overwrite later work."""
    model_config=ConfigDict(extra='forbid',frozen=True)
    request_key:StrictStr=Field(min_length=8,max_length=128,pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]*$')


class LocalPreviewInput(LocalReleaseRequest):
    """Declare the public change/revision that the new empty preview data will serve; this does not verify or run an image."""
    bundle_id:StrictStr=Field(pattern=r'^[0-9a-f]{64}$')
    source_commit:StrictStr=Field(pattern=r'^[0-9a-f]{40}$')


class LocalSnapshotInput(LocalReleaseRequest):
    """Label a private whole-gateway offline snapshot with a declared revision; trusted CLI arguments select the source root."""
    source_commit:StrictStr=Field(pattern=r'^[0-9a-f]{40}$')


class LocalRestoreInput(LocalReleaseRequest):
    """Name an existing verified snapshot; restore targets come only from explicit maintainer CLI arguments and must be fresh."""
    snapshot_id:StrictStr=Field(pattern=r'^[0-9a-f]{64}$')


class LocalReleaseFeedbackInput(LocalReleaseRequest):
    """Save local maintainer/test feedback separately from review, CI, deployment and real-user acceptance."""
    release_id:StrictStr=Field(pattern=r'^[0-9a-f]{64}$')
    decision:Literal['comment','accepted_locally','rejected_locally']
    note:StrictStr=Field(min_length=1,max_length=2000)
