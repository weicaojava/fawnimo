"""Independent sound-v3 documents; client declarations never supply decoded media facts."""
from typing import Literal
from pydantic import Field, StrictBool, StrictInt, model_validator
from app.core.contracts.editing import EditingDTO, EditDocument, EditIssue
from app.core.contracts.editing_v2 import TransitionEdge
from app.core.contracts.editing_export import ExportRead, ExportFileRead


class SoundCandidateImport(EditingDTO):
    """Register an actual project WAV as a candidate, without adopting it anywhere."""
    request_key: str = Field(min_length=1, max_length=128)
    chapter_id: str = Field(min_length=1, max_length=64)
    media_id: str = Field(min_length=1, max_length=64)
    expected_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    kind: Literal['voice', 'effect', 'music']
    name: str = Field(min_length=1, max_length=120)


class SoundAudioFacts(EditingDTO):
    """Actual fully decoded PCM16 facts on a contiguous source sample clock."""
    sample_rate: Literal[24000, 48000]
    channels: Literal[1, 2]
    sample_frames: StrictInt = Field(ge=240, le=14400000)
    duration_us: StrictInt = Field(ge=10000, le=300000000)
    coverage_us: list[list[StrictInt]] = Field(min_length=1, max_length=1)
    peak_pcm16: StrictInt = Field(ge=0, le=32768)
    rms_pcm16: StrictInt = Field(ge=0, le=32768)
    envelope_pcm16: list[StrictInt] = Field(min_length=1, max_length=256)


class SoundCandidateRead(EditingDTO):
    """Verified candidate facts; selection still requires a separate timeline edit."""
    id: str
    project_id: str
    chapter_id: str
    media_id: str
    sha256: str
    kind: Literal['voice', 'effect', 'music']
    name: str
    origin: Literal['user_import', 'local_speech']
    audio: SoundAudioFacts
    model_quality_qualified: Literal[False] = False


class SoundMusicOptions(EditingDTO):
    """Explicit music-only loop target and confirmed-dialogue attenuation; no automatic extension of a picture."""
    loop_until_ms: StrictInt | None = Field(default=None, ge=10, le=300000)
    voice_duck_db: StrictInt | None = Field(default=None, ge=-30, le=-3)

    @model_validator(mode='after')
    def explicit_music(self):
        """Require at least one selected operation and the same strict bounded clock as the renderer."""
        from app.core.contracts.sound_mix import checked_music_options
        checked_music_options(self.model_dump())
        return self


class SoundTrack(EditingDTO):
    """An explicit audio placement on the final output clock, never an automatic retime."""
    id: str = Field(min_length=1, max_length=64)
    candidate_id: str = Field(min_length=1, max_length=64)
    source_in_ms: StrictInt = Field(ge=0, le=300000)
    source_out_ms: StrictInt = Field(ge=0, le=300000)
    timeline_start_ms: StrictInt = Field(ge=0, le=300000)
    gain_db: StrictInt = Field(default=0, ge=-60, le=6)
    muted: StrictBool = False
    dialogue_id: str | None = Field(default=None, min_length=1, max_length=64)
    content_confirmed: StrictBool = False
    music: SoundMusicOptions | None = None

    @model_validator(mode='after')
    def interval(self):
        """Ten milliseconds maps exactly to samples at both admitted WAV sample rates."""
        if self.source_out_ms <= self.source_in_ms or any(value % 10 for value in (self.source_in_ms, self.source_out_ms, self.timeline_start_ms)):
            raise ValueError('音轨裁剪和位置必须是递增的10毫秒整数步')
        if self.music is not None and self.music.loop_until_ms is not None and self.music.loop_until_ms <= self.timeline_start_ms+self.source_out_ms-self.source_in_ms:
            raise ValueError('音乐循环终点必须晚于原裁剪区间的输出终点')
        return self


class SoundDocument(EditingDTO):
    """Own the complete picture and sound form independently of mutable v1/v2 drafts."""
    schema_version: Literal[3] = 3
    title: str = Field(default='', max_length=120)
    picture: EditDocument
    edges: list[TransitionEdge] = Field(default_factory=list, max_length=99)
    tracks: list[SoundTrack] = Field(default_factory=list, max_length=64)


class SoundCreate(EditingDTO):
    """Create one independently editable sound document for a real chapter."""
    chapter_id: str = Field(min_length=1, max_length=64)
    document: SoundDocument


class SoundPatch(EditingDTO):
    """Save every sound field atomically under an expected saved revision."""
    expected_revision: StrictInt = Field(ge=1)
    document: SoundDocument


class SoundFreeze(EditingDTO):
    """Acknowledge picture, overlap and historical upstream changes before locking."""
    expected_revision: StrictInt = Field(ge=1)
    acknowledge_changes: StrictBool = False
    change_note: str = Field(default='', max_length=1000)


class SoundPublicationSummary(EditingDTO):
    """One immutable historical sound revision, available after restart."""
    id: str
    content_hash: str
    draft_revision: int
    duration_ms: int


class SoundDraftRead(EditingDTO):
    """Current form and actual picture/audio readiness, without private file paths."""
    id: str
    project_id: str
    chapter_id: str
    revision: int
    document: SoundDocument
    duration_ms: int
    can_freeze: bool
    issues: list[EditIssue]
    changes: list[str]
    publications: list[SoundPublicationSummary]


class SoundPublicationRead(EditingDTO):
    """Portable display view; its bytes are distinct from the private evidence hash."""
    id: str
    project_id: str
    chapter_id: str
    draft_revision: int
    content_hash: str
    payload: dict
    model_calls: Literal[0] = 0


class SoundExportFile(ExportFileRead):
    """The independently downloadable PCM mix accompanies the usual video and subtitle files."""
    kind: Literal['video', 'mixdown', 'subtitles_srt', 'subtitles_vtt', 'package']


class SoundExportRead(ExportRead):
    """A durable sound-v3 export, including the actual standalone audio mix."""
    files: list[SoundExportFile]


class SoundExportCatalog(EditingDTO):
    """Bounded sound export history and current local renderer availability."""
    enabled: bool
    reason: str
    exports: list[SoundExportRead]
