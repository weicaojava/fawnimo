"""Explicit ordered project-media choices for a separate Seedance reference profile."""
from dataclasses import dataclass, field
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

REFERENCE_PROTOCOL = 'seedance25_reference_v1'
ReferenceRole = Literal['first_frame', 'last_frame', 'reference_image', 'reference_video']
MEDIA_ID = Annotated[str, Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')]
MEDIA_SHA = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]


class VideoReferenceInput(BaseModel):
    """A caller can choose an owned media identity and hash, never a URL, path, account or credential."""
    model_config = ConfigDict(extra='forbid', frozen=True)
    media_id: MEDIA_ID
    expected_sha256: MEDIA_SHA
    role: ReferenceRole

    @property
    def kind(self) -> str:
        """Derive the transport type from the explicit role instead of accepting conflicting caller fields."""
        return 'video' if self.role == 'reference_video' else 'image'


class VideoReferenceSelection(BaseModel):
    """Keep first-frame, first-and-last-frame and semantic references mutually exclusive."""
    model_config = ConfigDict(extra='forbid', frozen=True)
    schema_version: Literal[1] = 1
    mode: Literal['reference', 'first_frame', 'first_last_frames']
    references: tuple[VideoReferenceInput, ...] = Field(min_length=1, max_length=4)

    @field_validator('schema_version', mode='before')
    @classmethod
    def exact_version(cls, value):
        """Do not treat a JSON boolean or numeric string as an input-contract version."""
        if type(value) is not int or value != 1:
            raise ValueError('参考输入版本无效')
        return value

    @model_validator(mode='after')
    def exact_mode(self):
        """Validate roles, order and bounded counts without silently dropping or reordering any input."""
        roles = [item.role for item in self.references]
        if self.mode == 'first_frame':
            if roles != ['first_frame']:
                raise ValueError('首帧模式必须明确选择一张首帧图片')
        elif self.mode == 'first_last_frames':
            if roles != ['first_frame', 'last_frame']:
                raise ValueError('首尾帧须按首帧、尾帧顺序明确选择两张图片')
            # The provider allows the exact same image to serve as both frames.
            if self.references[0].media_id == self.references[1].media_id and self.references[0].expected_sha256 != self.references[1].expected_sha256:
                raise ValueError('同一张首尾帧图片不能声明不同内容')
        else:
            if any(role not in {'reference_image', 'reference_video'} for role in roles):
                raise ValueError('素材参考与首尾帧不能混用')
            if roles.count('reference_image') > 3 or roles.count('reference_video') > 1:
                raise ValueError('本协议最多选择三张参考图片和一个参考视频')
            if len({item.media_id for item in self.references}) != len(self.references):
                raise ValueError('素材参考不能重复选择同一媒体')
        return self


def snapshot_reference_selection(snapshot: dict) -> VideoReferenceSelection:
    """Require the legacy-shaped reference index to exactly match the new ordered, role/hash-aware selection."""
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('video_reference'), dict):
        raise ValueError('此参考协议必须明确选择输入模式与素材')
    selection = VideoReferenceSelection.model_validate(snapshot['video_reference'])
    expected = [{'media_id': item.media_id, 'kind': item.kind} for item in selection.references]
    if snapshot.get('references') != expected:
        raise ValueError('参考输入列表、顺序与明确模式不一致')
    return selection


@dataclass(frozen=True, slots=True)
class PreparedReference:
    """Server-only proof of one selected input; image bytes and video delivery credentials stay out of representations.

    This is not a public request DTO. Services must create it only after actual
    owned-media decoding and immutable header validation. The wire builder still
    verifies identity, SHA, dimensions and delivery scope before serialization.
    """
    project_id: str
    media_id: str
    sha256: str
    mime: str
    byte_size: int
    width: int
    height: int
    content: bytes | None = field(default=None, repr=False)
    delivery_url: str | None = field(default=None, repr=False)
    duration_ms: int | None = None
    fps: int | None = None
    frame_clock_sha256: str | None = None
    has_audio: bool | None = None

