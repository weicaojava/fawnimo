"""Zero external fee, explicitly selected installed-system speech contracts."""
from typing import Literal
from pydantic import Field, StrictInt, model_validator
from app.core.contracts.editing import EditingDTO
from app.core.contracts.sound_timeline import SoundCandidateRead

LocalSpeechProfile = Literal['windows_sapi_pcm_v1', 'linux_espeak_pcm_v1']


class LocalSpeechVoice(EditingDTO):
    """An actual enabled system voice, identified by a stable name/culture digest."""
    id: str
    name: str
    culture: str


class LocalSpeechCatalog(EditingDTO):
    """An unavailable offline engine never silently falls back to a paid service."""
    enabled: bool
    reason: str
    profile: LocalSpeechProfile = 'windows_sapi_pcm_v1'
    external_fee_fen: Literal[0] = 0
    voices: list[LocalSpeechVoice]


class LocalSpeechRequest(EditingDTO):
    """Generate the exact original frozen dialogue using one explicitly selected voice."""
    request_key: str = Field(min_length=1, max_length=128, pattern=r'.*\S.*')
    plan_id: str = Field(min_length=1, max_length=64)
    dialogue_id: str = Field(min_length=1, max_length=64)
    voice_id: str = Field(pattern=r'^(?:sapi_|espeak_)[a-f0-9]{64}$')
    rate: StrictInt = Field(default=0, ge=-2, le=2)
    profile: LocalSpeechProfile

    @model_validator(mode='after')
    def original_voice_profile(self):
        """An explicit voice cannot silently switch between the installed Windows and Linux engines."""
        prefix = 'sapi_' if self.profile == 'windows_sapi_pcm_v1' else 'espeak_'
        if not self.voice_id.startswith(prefix):
            raise ValueError('Selected voice does not match its explicit local speech profile')
        return self


class LocalSpeechRead(EditingDTO):
    """An original durable local job; completed output remains a candidate until separately adopted."""
    id: str
    project_id: str
    chapter_id: str
    task_id: str
    state: Literal['queued', 'running', 'ready', 'failed', 'cancelled']
    reason: str
    attempts: int
    plan_id: str
    dialogue_id: str
    text: str
    speaker_id: str | None
    voice: LocalSpeechVoice
    rate: int
    profile: LocalSpeechProfile
    external_fee_fen: Literal[0] = 0
    candidate: SoundCandidateRead | None = None


class LocalSpeechHistory(EditingDTO):
    """Current voice availability and bounded actual chapter history."""
    catalog: LocalSpeechCatalog
    jobs: list[LocalSpeechRead]
