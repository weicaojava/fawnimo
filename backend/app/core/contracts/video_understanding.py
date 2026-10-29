"""Explicit model connections, durable understanding attempts and source-bound candidates."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator
from app.core.contracts.video_dna import DnaDocument


class UnderstandingDTO(BaseModel):
    """Keep configuration and control inputs bounded and reject unrecognized execution fields."""
    model_config = ConfigDict(extra='forbid')


class UnderstandingConnection(UnderstandingDTO):
    """A dedicated multimodal connection; credentials refer only to new server environment entries."""
    enabled: StrictBool = False
    base_url: str = Field(default='', max_length=1024)
    vision_model: str = Field(default='', max_length=255)
    transcribe_audio: StrictBool = True
    audio_model: str = Field(default='', max_length=255)
    audio_base_url: str = Field(default='', max_length=1024)
    credential_env: str = Field(default='', max_length=96, pattern=r'^(?:DNA_UNDERSTANDING_[A-Z0-9_]{1,64})?$')
    audio_credential_env: str = Field(default='', max_length=96, pattern=r'^(?:DNA_UNDERSTANDING_[A-Z0-9_]{1,64})?$')
    max_frames: StrictInt = Field(default=24, ge=3, le=96)
    timeout_seconds: StrictInt = Field(default=120, ge=5, le=300)
    max_output_tokens: StrictInt = Field(default=12000, ge=256, le=32768)

    @model_validator(mode='after')
    def complete_enabled(self):
        """Saving a disabled partial setup is safe; enabling requires explicit model names."""
        if self.enabled and (not self.base_url.strip() or not self.vision_model.strip() or (self.transcribe_audio and not self.audio_model.strip())):
            raise ValueError('启用前请填写服务地址、视觉模型及启用转写时的音频模型')
        return self


class UnderstandingConnectionWrite(UnderstandingConnection):
    """Save the visible dedicated configuration without overwriting another editor's changes."""
    expected_revision: StrictInt = Field(ge=0)


class UnderstandingConnectionRead(UnderstandingDTO):
    """Expose usable setup state without returning secret values or reading existing provider keys."""
    project_id: str
    revision: int
    connection: UnderstandingConnection
    configured: bool
    available: bool
    credential_present: bool
    worker_enabled: bool
    remote_allowed: bool
    message: str
    next_steps: list[str]


class UnderstandingCreate(UnderstandingDTO):
    """Bind a single submission to the exact displayed source and connection revisions."""
    analysis_id: str = Field(min_length=1, max_length=64)
    expected_analysis_revision: StrictInt = Field(ge=1)
    expected_connection_revision: StrictInt = Field(ge=1)
    request_key: str = Field(min_length=1, max_length=128)


class UnderstandingAttemptControl(UnderstandingDTO):
    """An old window cannot cancel or retry a different, newer network attempt."""
    expected_attempt: StrictInt = Field(ge=1)


class UnderstandingDraftCreate(UnderstandingDTO):
    """Only a successful candidate may create a new draft against matching human-confirmed cuts."""
    expected_analysis_revision: StrictInt = Field(ge=1)


class UnderstandingTranscript(UnderstandingDTO):
    """ASR hypotheses retain both original audio-relative times and their original-frame mapping."""
    id: str
    text: str
    audio_start_us: int
    audio_end_us: int
    video_start_us: int
    video_end_us: int
    start_frame: int | None
    end_frame: int | None


class UnderstandingAttemptRead(UnderstandingDTO):
    """Keep exact input receipts and safe failure categories separate from model-quality claims."""
    attempt: int
    state: str
    phase: str
    started_at: float | None
    finished_at: float | None
    reason: str
    input_receipt: dict | None
    response_hash: str | None


class UnderstandingJobRead(UnderstandingDTO):
    """A persistent candidate remains unreviewed and inert until a human opens a new DNA draft."""
    id: str
    project_id: str
    analysis_id: str
    state: Literal['queued', 'running', 'succeeded', 'failed', 'cancelled']
    phase: str
    attempt: int
    progress: int
    cancel_requested: bool
    reason: str
    source: dict
    connection: UnderstandingConnection
    document: DnaDocument | None
    transcripts: list[UnderstandingTranscript]
    warnings: list[str]
    draft_id: str | None
    attempts: list[UnderstandingAttemptRead]
    created_at: float
    updated_at: float


class UnderstandingDraftRead(UnderstandingDTO):
    """Return only the new or previously linked owned draft identity; do not imply a frozen snapshot."""
    draft_id: str

