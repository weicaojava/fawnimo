"""Check archive limits, binary integrity and safe entry names independently of storage engines."""
from io import BytesIO
from types import SimpleNamespace
import zipfile

from fastapi import HTTPException
import pytest

from app.services.studio import file_exports


@pytest.mark.asyncio
async def test_bundle_preserves_binary_and_sanitizes_names(monkeypatch):
    """Never reinterpret media as text or let user filenames become ZIP paths."""
    data = bytes(range(256))

    async def read(_db, *, file_id):
        """Stand in for the separately tested verified-storage reader."""
        return SimpleNamespace(id=file_id, name='../folder\\file.mp4'), data

    monkeypatch.setattr(file_exports, 'read_file_bytes', read)
    result = await file_exports.export_file_bundle(None, ['one', 'two', 'one'])
    with zipfile.ZipFile(BytesIO(result)) as archive:
        assert len(archive.namelist()) == 2
        assert all('/' not in name and '\\' not in name for name in archive.namelist())
        assert archive.read(archive.namelist()[0]) == data
        assert archive.read(archive.namelist()[1]) == data


@pytest.mark.asyncio
async def test_export_limits_and_missing_media_never_return_partial_archive(monkeypatch):
    """A request fails completely if any file is missing or the actual bytes exceed limits."""
    async def read(_db, *, file_id):
        """Model a storage verification rejection before the archive is published."""
        if file_id == 'missing':
            raise HTTPException(404, 'Missing media')
        return SimpleNamespace(id=file_id, name='file.png'), b'12345'

    monkeypatch.setattr(file_exports, 'read_file_bytes', read)
    monkeypatch.setattr(file_exports, 'MAX_EXPORT_BYTES', 8)
    with pytest.raises(HTTPException) as failure:
        await file_exports.export_file_bundle(None, ['one', 'two'])
    assert failure.value.status_code == 413
    with pytest.raises(HTTPException) as failure:
        await file_exports.export_file_bundle(None, ['one', 'missing'])
    assert failure.value.status_code == 404
    for bad in [[], [str(number) for number in range(21)]]:
        with pytest.raises(HTTPException) as failure:
            await file_exports.export_file_bundle(None, bad)
        assert failure.value.status_code == 422
