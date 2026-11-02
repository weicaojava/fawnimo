"""Closed model-free example inputs and safe output identities; they do not grant film/model qualification."""
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EXAMPLE_TEMPLATE = 'original-light-island-geometry-v1'


def example_identity(workspace: str, actor: str, request_key: str) -> dict:
    """Share deterministic opaque new-target identity across the control and fixed-worker ledgers."""
    LocalExampleInput(request_key=request_key)
    if not isinstance(workspace, str) or not 1 <= len(workspace) <= 128 or not isinstance(actor, str) or not 1 <= len(actor) <= 128:
        raise ValueError('Trusted workspace and actor identities required')
    digest = hashlib.sha256(json.dumps([EXAMPLE_TEMPLATE,workspace,actor,request_key], separators=(',',':'),ensure_ascii=False).encode('utf8')).hexdigest()
    identifier = 'example-'+digest[:32]
    return {'id':identifier,'project_id':identifier,'chapter_id':identifier+'-chapter','submitted_by':actor,
        'request_key':request_key,'request_hash':digest,'template_version':EXAMPLE_TEMPLATE}


class LocalExampleInput(BaseModel):
    """Accept one durable explicit intent; all geometry, clocks, tools and filesystem paths stay server-owned."""
    model_config = ConfigDict(extra='forbid')
    request_key: str = Field(pattern=r'^[A-Za-z0-9_-]{8,128}$')


class LocalExampleRead(BaseModel):
    """Describe a stored original example without exposing paths, credentials or a generated-model provenance."""
    model_config = ConfigDict(extra='forbid')
    project_id: str
    chapter_id: str
    script_snapshot_id: str
    plan_id: str
    draft_id: str
    state: Literal['ready'] = 'ready'
    source_kind: Literal['original_geometry_code'] = 'original_geometry_code'
    clip_count: Literal[7] = 7
    picture_count: Literal[7] = 7
    duration_ms: Literal[28000] = 28000
    source_audio: Literal[False] = False
    external_model_calls: Literal[0] = 0
    model_quality_qualified: Literal[False] = False
    complete_film_qualified: Literal[False] = False
    replayed: bool
    entry: str
    notice: str


class LocalExampleStatus(BaseModel):
    """Expose bounded preparation availability/history without rendering or running a model during GET."""
    model_config = ConfigDict(extra='forbid')
    available: bool
    message: str
    state: Literal['running', 'ready', 'failedunknown'] | None = None
    attempts: int = Field(ge=0, le=3)
    result: LocalExampleRead | None = None
