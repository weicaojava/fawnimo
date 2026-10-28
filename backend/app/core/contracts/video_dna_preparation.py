"""Read-only DNA preparation and dependency comparison never admit generation tasks."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from app.core.contracts.video_dna import DnaDTO, ID, DnaIssue

LAYERS = Literal['GLOBAL_STYLE_PROMPT', 'CHARACTER_LOCK_PROMPT', 'SCENE_LOCK_PROMPT', 'SHOT_IMAGE_PROMPT', 'SHOT_VIDEO_PROMPT', 'NEGATIVE_CONSTRAINTS', 'AUDIO_AND_EDITING_PROMPTS']


class DnaPreparedClaim(BaseModel):
    """A compiled statement keeps the human status, uncertainty and exact source locators."""
    path: str
    label: str
    status: Literal['unknown', 'observed', 'inferred']
    zh: str | None
    en: str | None
    reason: str
    confidence: int | None
    evidence: list[dict]


class DnaPreparedLayer(BaseModel):
    """One of seven ordered bilingual recipe layers with its original claim identities."""
    key: LAYERS
    zh: str | None
    en: str | None
    claims: list[DnaPreparedClaim]


class DnaPreparedUnknown(BaseModel):
    """An unknown remains visible rather than being silently replaced by guessed text."""
    path: str
    reason: str


class DnaPreparedShot(BaseModel):
    """Frame-based reconstruction preparation; the terminal duration is deliberately unknown."""
    shot_id: str
    start_frame: int
    end_frame: int
    start_relative_us: int
    end_relative_us: int | None
    character_ids: list[str]
    scene_id: str | None
    prop_ids: list[str]
    layers: list[DnaPreparedLayer]
    unknowns: list[DnaPreparedUnknown]
    issues: list[DnaIssue]


class DnaPreparationPayload(BaseModel):
    """Deterministic semantic body excludes random snapshot identity and all runtime configuration."""
    schema_version: Literal[1] = 1
    compiler_version: Literal['dna-preparation-v1'] = 'dna-preparation-v1'
    dna_content_hash: str
    source: dict
    asset_versions: list[dict]
    entries: list[DnaPreparedShot]
    execution_enabled: Literal[False] = False
    model_qualified: Literal[False] = False
    original_author_prompt: Literal[False] = False
    prompt_provenance: Literal['human_reconstruction_hypothesis'] = 'human_reconstruction_hypothesis'
    human_declarations_only: Literal[True] = True


class DnaPreparationRead(BaseModel):
    """Snapshot identity is an envelope; importing equal DNA preserves the semantic preparation hash."""
    snapshot_id: str
    dna_content_hash: str
    compiler_version: str
    content_hash: str
    payload: DnaPreparationPayload


class DnaCompareRequest(DnaDTO):
    """Only owned immutable snapshot identities may be compared, without a client execution payload."""
    base_snapshot_id: ID
    other_snapshot_id: ID


class DnaImpactReason(BaseModel):
    """A precise semantic field path explains why one old or new shot is affected."""
    shot_id: str
    path: str
    reason: str


class DnaCompareRead(BaseModel):
    """Comparison is an advisory dependency report, never an automatic rewrite or regeneration."""
    base_snapshot_id: str
    other_snapshot_id: str
    changed_paths: list[str]
    affected_shot_ids: list[str]
    dependency_index: dict[str, list[str]]
    reasons: list[DnaImpactReason]
    execution_enabled: Literal[False] = False
