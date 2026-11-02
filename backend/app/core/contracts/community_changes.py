"""Strict inert source replacements for an explicit public-code change, never commands, credentials or execution authority."""
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def code_path(value):
    """Require unambiguous lowercase POSIX paths that also avoid Windows aliases and device names."""
    if (not isinstance(value, str) or not 1 <= len(value) <= 200 or re.fullmatch(r'[a-z0-9_./-]+', value) is None
            or value.startswith('/') or any(part in {'', '.', '..'} or part.endswith('.')
                or re.fullmatch(r'(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', part) for part in value.split('/'))):
        raise ValueError('Invalid canonical public code path')
    return value


class CommunityFileChange(BaseModel):
    """Replace or remove a regular UTF8 Python source file, with an exact old hash or explicit new-file intent."""
    model_config = ConfigDict(extra='forbid', frozen=True)
    path: str
    before_sha256: str | None = Field(pattern=r'^[0-9a-f]{64}$')
    action: Literal['write', 'delete']
    text: str | None = None

    @field_validator('path')
    @classmethod
    def canonical_path(cls, value):
        """Validate before any filesystem or Git operation can consider the supplied path."""
        return code_path(value)

    @model_validator(mode='after')
    def bounded_text(self):
        """Keep the current profile text-only and deterministic, without binary patches or ambiguous line endings."""
        if not self.path.endswith('.py'):
            raise ValueError('Current change profile accepts Python files only')
        if self.action == 'delete':
            if self.text is not None or self.before_sha256 is None:
                raise ValueError('Deletion requires an exact existing file and no replacement')
        elif self.text is None or len(self.text.encode('utf8')) > 131072 or '\x00' in self.text or '\r' in self.text or (self.text and not self.text.endswith('\n')):
            raise ValueError('Replacement requires bounded LF UTF8 text with a final newline')
        return self


class CommunityChangeInput(BaseModel):
    """Bind one bounded proposed change and its candidate tests to an original preparation record."""
    model_config = ConfigDict(extra='forbid', frozen=True)
    request_key: str = Field(min_length=8, max_length=128, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]*$')
    work_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=120)
    changes: list[CommunityFileChange] = Field(min_length=1, max_length=16)
    test_files: list[str] = Field(min_length=1, max_length=8)

    @model_validator(mode='after')
    def finite_original_intent(self):
        """Forbid repeated targets/tests, invisible titles and oversized original JSON before source preparation."""
        self.title.encode('utf8')
        if not self.title.strip() or any(ord(char) < 32 for char in self.title):
            raise ValueError('Invalid change title')
        if len({change.path for change in self.changes}) != len(self.changes) or len(set(self.test_files)) != len(self.test_files):
            raise ValueError('Repeated change or test path')
        for path in self.test_files:
            code_path(path)
        if sum(len((change.text or '').encode('utf8')) for change in self.changes) > 1048576 or len(self.model_dump_json().encode('utf8')) > 2097152:
            raise ValueError('Complete change input exceeds bounded profile')
        return self
