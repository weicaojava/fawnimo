"""Actual cross-process OS locks prevent public object write contention and release on owner death without deleting lock files."""
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from app.services.community.candidate_lock import candidate_object_lock
from app.services.community.candidate_git import GIT_DEADLINE
from app.services.community.store import CommunityStoreConflict
from tests.test_community_candidate_git import candidate_repo, assert_original
from tests.test_community_change_bundles import prepared_change
from tests.test_community_queue_leases import factory


def test_real_other_process_fences_writers_and_kernel_releases_after_owner_death(tmp_path):
    """A second process cannot enter the protected writer; killing/joining the owner releases the same persistent-file kernel lock."""
    objects = tmp_path / 'objects'; objects.mkdir()
    script = '''import sys,time
from app.services.community.candidate_lock import candidate_object_lock
with candidate_object_lock(sys.argv[1],time.monotonic()+5):
    print('owned',flush=True)
    sys.stdin.buffer.read(1)
'''
    allowed = {'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATH', 'PATHEXT', 'TEMP', 'TMP', 'TMPDIR', 'LANG', 'LC_ALL', 'TZ'}
    env = {name: value for name, value in os.environ.items() if name.upper() in allowed}
    env.update(PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', JELLYFISH_SKIP_ENV_FILE='1')
    child = subprocess.Popen([sys.executable, '-u', '-c', script, str(objects)], cwd=Path(__file__).resolve().parents[1],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    try:
        assert child.stdout.readline().strip() == b'owned'
        with pytest.raises(CommunityStoreConflict):
            with candidate_object_lock(objects, time.monotonic() + .05):
                raise AssertionError('Another process acquired the live object writer')
        child.kill(); child.wait(timeout=5)
        # Windows root-process exit need not make a separately opened ordinary
        # read immediately succeed. Wait for actual kernel ownership; do not
        # delete a marker or infer release merely from the dead PID.
        with candidate_object_lock(objects, time.monotonic() + 2):
            assert (objects / 'info/fawnimo-community-writes.lock').is_file()
        marker = (objects / 'info/fawnimo-community-writes.lock').read_bytes()
        with candidate_object_lock(objects, time.monotonic() + .5):
            assert (objects / 'info/fawnimo-community-writes.lock').is_file()
        assert (objects / 'info/fawnimo-community-writes.lock').read_bytes() == marker
    finally:
        if child.poll() is None:
            child.kill(); child.wait(timeout=5)
        for stream in (child.stdin, child.stdout, child.stderr): stream.close()


def test_foreign_marker_is_retained_and_never_repaired_or_deleted(tmp_path):
    """An unknown metadata file stops candidate writes instead of being assumed stale or overwritten."""
    objects = tmp_path / 'objects'; info = objects / 'info'; info.mkdir(parents=True)
    path = info / 'fawnimo-community-writes.lock'; raw = b'authored foreign metadata is retained\n'; path.write_bytes(raw)
    with pytest.raises(CommunityStoreConflict):
        with candidate_object_lock(objects, time.monotonic() + .5):
            raise AssertionError('Foreign marker was accepted')
    assert path.read_bytes() == raw


def test_expired_operation_does_not_initialize_a_partial_mutex_or_change_git(candidate_repo):
    """A deadline already spent must stop before opening/creating shared metadata, while preserving the original clone and branch state."""
    repository, _body, prepared, artifact, observation, protected = candidate_repo
    path = repository.objects / 'info/fawnimo-community-writes.lock'
    assert not path.exists()
    token = GIT_DEADLINE.set(time.monotonic() - 1)
    try:
        with pytest.raises(CommunityStoreConflict): repository.create(artifact, prepared, observation)
    finally:
        GIT_DEADLINE.reset(token)
    assert not path.exists()
    assert repository.recover(artifact) is None
    assert_original(repository, protected)


def test_same_thread_nested_ownership_is_reentrant_and_exception_releases_it(tmp_path):
    """Create calls recovery under the same owner; it must neither deadlock nor leak kernel ownership on failure."""
    objects = tmp_path / 'objects'; objects.mkdir()
    with pytest.raises(RuntimeError):
        with candidate_object_lock(objects, time.monotonic() + .5):
            with candidate_object_lock(objects, time.monotonic() + .5):
                raise RuntimeError('Authored interrupted operation')
    with candidate_object_lock(objects, time.monotonic() + .5):
        assert (objects / 'info/fawnimo-community-writes.lock').is_file()
