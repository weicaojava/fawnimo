"""Bounded text editing contracts with optimistic revision checks."""
from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_serializer, model_validator


class ChapterTextRevisionRead(BaseModel):
    """One actual saved text revision and its restore provenance."""
    model_config = ConfigDict(from_attributes=True)
    id: str
    chapter_id: str
    revision: int
    raw_text: str
    condensed_text: str
    content_hash: str
    origin: str
    restored_from_revision: int | None
    created_at: datetime

    @field_serializer('created_at')
    def utc_time(self, value: datetime) -> str:
        """SQLite returns UTC timestamps without tzinfo; mark them explicitly for browser display."""
        return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value).isoformat()


class ChapterTextState(BaseModel):
    """Current editable content and the revision required for a safe subsequent save."""
    chapter_id: str
    current_revision: int
    raw_text: str
    condensed_text: str


class ChapterTextHistoryRead(ChapterTextState):
    """A real paged history; legacy chapters honestly start with no recorded revisions."""
    items: list[ChapterTextRevisionRead]
    page: int
    page_size: int
    total: int


class ChapterTextSave(BaseModel):
    """Save either or both text fields without overwriting a newer editor revision."""
    model_config = ConfigDict(extra='forbid')
    expected_revision: StrictInt = Field(ge=0)
    raw_text: str | None = Field(default=None, max_length=200000)
    condensed_text: str | None = Field(default=None, max_length=200000)

    @model_validator(mode='after')
    def require_text(self):
        """An omitted field stays unchanged; an explicit null never becomes stored text."""
        fields = self.model_fields_set & {'raw_text', 'condensed_text'}
        if not fields or any(getattr(self, key) is None for key in fields):
            raise ValueError('至少提供一个文本字段，且不能为null')
        return self


class ChapterTextRestore(BaseModel):
    """Restore creates a fresh revision and rejects concurrent content changes."""
    model_config = ConfigDict(extra='forbid')
    expected_revision: StrictInt = Field(ge=0)
