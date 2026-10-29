"""Verify browser media range requests through the actual file API and error envelope."""
from types import SimpleNamespace
import pytest
from app.services.studio import files


@pytest.mark.parametrize('requested,status,expected,content_range', [
    (None, 200, b'0123456789', None), ('bytes=0-3', 206, b'0123', 'bytes 0-3/10'),
    ('bytes=5-', 206, b'56789', 'bytes 5-9/10'), ('bytes=-2', 206, b'89', 'bytes 8-9/10'),
    ('bytes=8-99', 206, b'89', 'bytes 8-9/10'),
])
def test_download_preserves_actual_requested_bytes(client, monkeypatch, requested, status, expected, content_range):
    """Seeking must receive the requested real byte interval with an accurate total length."""
    async def read(*_args, **_kwargs):
        """Provide a deterministic object while exercising the real HTTP streaming implementation."""
        return SimpleNamespace(storage_key='example.mp4'), b'0123456789'
    monkeypatch.setattr(files, 'read_file_bytes', read)
    response = client.get('/api/v1/studio/files/range-test/download', headers={'Range': requested} if requested else {})
    assert response.status_code == status and response.content == expected
    assert response.headers['content-length'] == str(len(expected))
    assert response.headers['accept-ranges'] == 'bytes'
    assert response.headers.get('content-range') == content_range


@pytest.mark.parametrize('requested', ['bytes=99-', 'bytes=3-2', 'bytes=-0', 'bytes=0-1,4-5', 'bytes=-'])
def test_invalid_range_returns_safe_error_and_size(client, monkeypatch, requested):
    """Invalid or unsupported multipart requests fail clearly instead of serving wrong media."""
    async def read(*_args, **_kwargs):
        """Use bounded test bytes without touching a real workspace file."""
        return SimpleNamespace(storage_key='example.mp4'), b'0123456789'
    monkeypatch.setattr(files, 'read_file_bytes', read)
    response = client.get('/api/v1/studio/files/range-test/download', headers={'Range': requested})
    assert response.status_code == 416 and response.headers['content-range'] == 'bytes */10'
    assert response.json()['code'] == 416
