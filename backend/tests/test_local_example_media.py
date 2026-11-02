"""Verify actual original media, atomic publication races and cancellation using disposable private files."""
import asyncio
import hashlib
from pathlib import Path
import threading

import pytest

from app.config import settings
from app.services.studio.local_example_media import file_operation, prepare_media, publish_file


@pytest.mark.asyncio
async def test_actual_original_media_preparation_returns_complete_final_proofs(tmp_path, monkeypatch):
    """Rendering, parent hashes, full decoding and exclusive publication produce14 owned final resources."""
    monkeypatch.setattr(settings, 'production_media_root', str(tmp_path))
    resources, proofs, videos = await prepare_media('example-'+'a'*32, 'b'*64)
    assert len(resources) == len(proofs) == 14 and len(videos) == 7
    assert len({value['relative_path'] for value in resources}) == 14
    assert {(value['kind'],value['clip_index']) for value in resources} == {(kind,index) for kind in ('picture','video') for index in range(1,8)}
    for item in resources:
        path = tmp_path / item['relative_path']
        assert path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == item['sha256']
        assert proofs[item['id']]['metadata'] == {key:item[key] for key in ('id','sha256','size','mime','relative_path')}
        if item['kind'] == 'video':
            assert videos[item['id']]['video']['coverage_us'] == [[0,4000000]]
            assert videos[item['id']]['audio'] is None
    assert len(list(tmp_path.glob('original-nonce-*'))) == 1


def test_incomplete_source_never_publishes_and_existing_conflict_is_preserved(tmp_path, monkeypatch):
    """A crashed nonce cannot expose a final pathname or repair another attempt's mismatching bytes."""
    monkeypatch.setattr(settings, 'production_media_root', str(tmp_path))
    source = tmp_path / 'nonce.mp4'
    source.write_bytes(b'complete fixture bytes')
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    relative = 'original-examples/reserved/final.mp4'
    with pytest.raises(ValueError):
        publish_file(source, relative, source.stat().st_size+1, checksum)
    final = tmp_path / relative
    assert not final.exists()
    final.write_bytes(b'previous conflicting bytes')
    with pytest.raises(ValueError):
        publish_file(source, relative, source.stat().st_size, checksum)
    assert final.read_bytes() == b'previous conflicting bytes'


@pytest.mark.asyncio
async def test_two_actual_exclusive_publishers_preserve_one_complete_final_file(tmp_path, monkeypatch):
    """Two private same-filesystem nonces can converge only on verified identical final bytes."""
    monkeypatch.setattr(settings, 'production_media_root', str(tmp_path))
    blob = b'original complete content' * 1024
    sources = [tmp_path / name for name in ('nonce-a','nonce-b')]
    for source in sources:
        source.write_bytes(blob)
    checksum = hashlib.sha256(blob).hexdigest()
    paths = await asyncio.gather(*(file_operation(publish_file, source, 'original-examples/reserved/final.mp4', len(blob), checksum) for source in sources))
    assert paths[0] == paths[1] and paths[0].read_bytes() == blob
    assert all(source.read_bytes() == blob for source in sources)


@pytest.mark.asyncio
async def test_repeated_cancellation_joins_real_file_thread_before_return(tmp_path):
    """A still-running private writer cannot outlive the caller and race a replacement attempt."""
    entered, release = threading.Event(), threading.Event()
    path = tmp_path / 'owned-nonce'

    def write_after_gate():
        """Keep a real write pending so the repeated-cancellation assertion observes actual unfinished work."""
        entered.set()
        assert release.wait(8)
        path.write_bytes(b'finished')

    task = asyncio.create_task(file_operation(write_after_gate))
    assert await asyncio.to_thread(entered.wait, 5)
    try:
        for _ in range(4):
            task.cancel()
            await asyncio.sleep(0)
        assert not task.done() and not path.exists()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert path.read_bytes() == b'finished'
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
