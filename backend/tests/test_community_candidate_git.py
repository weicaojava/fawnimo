"""Actual temporary Git repositories verify isolated index construction and original local commit recovery without any remote push."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import subprocess
import time

import pytest

from app.services.community.candidate_git import CandidateGitRepository
from app.services.community.change_bundles import build_change_bundle
from app.services.community.public_code import PublicCodeObservation
from app.services.community.store import CommunityStoreConflict
from tests.test_community_change_bundles import prepared_change
from tests.test_community_queue_leases import factory


def git(root, *arguments, data=None):
    """Operate only the test's new repository with authored identities, no hooks, credentials, network or user Git configuration."""
    allowed = {'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATH', 'PATHEXT', 'TEMP', 'TMP', 'TMPDIR', 'LANG', 'LC_ALL', 'TZ'}
    env = {name: value for name, value in os.environ.items() if name.upper() in allowed}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_AUTHOR_NAME='Authored fixture', GIT_AUTHOR_EMAIL='fixture@example.invalid',
        GIT_COMMITTER_NAME='Authored fixture', GIT_COMMITTER_EMAIL='fixture@example.invalid', GIT_TERMINAL_PROMPT='0')
    result = subprocess.run(['git', '-c', 'core.hooksPath=' + os.devnull, '-c', 'commit.gpgSign=false', '-C', str(root), *arguments],
        input=data, capture_output=True, env=env, timeout=15)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    return result.stdout


@pytest.fixture
def candidate_repo(prepared_change, tmp_path):
    """Create a real public base, then deliberately dirty both working tree and index with authored private markers."""
    body, prepared, snapshot, registry = prepared_change
    root = tmp_path / 'public-clone'; root.mkdir()
    git(root, 'init', '--initial-branch=main')
    for path, raw in snapshot.files.items():
        target = root / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)
    git(root, 'add', '--', 'scripts/features.py', 'capabilities/features.json')
    git(root, 'commit', '-m', 'authored public base')
    head = git(root, 'rev-parse', 'HEAD').decode().strip(); tree = git(root, 'rev-parse', 'HEAD^{tree}').decode().strip()
    snapshot = replace(snapshot, commit=head, tree=tree)
    artifact = build_change_bundle(body, prepared, snapshot, registry)
    working = root / 'scripts/features.py'; working.write_bytes(b'# staged-authored-marker-never-in-public-candidate\n')
    git(root, 'add', '--', 'scripts/features.py')
    working.write_bytes(b'# unstaged-authored-marker-retained\n')
    private = root / 'private-authored-note.txt'; private.write_text('Untracked authored data remains local.', 'utf8')
    protected = {'head': head, 'index_sha256': hashlib.sha256((root / '.git/index').read_bytes()).hexdigest(),
        'working': working.read_bytes(), 'private': private.read_bytes()}
    observation = PublicCodeObservation(snapshot, time.time_ns() // 1000, tuple('a' * 64 for _ in range(7)))
    return CandidateGitRepository(repository=root), body, prepared, artifact, observation, protected


def assert_original(repository, protected):
    """Verify real HEAD/index/dirty file/untracked bytes rather than relying on a claim that checkout APIs were unused."""
    root = repository.root
    assert git(root, 'rev-parse', 'HEAD').decode().strip() == protected['head']
    assert hashlib.sha256((root / '.git/index').read_bytes()).hexdigest() == protected['index_sha256']
    assert (root / 'scripts/features.py').read_bytes() == protected['working']
    assert (root / 'private-authored-note.txt').read_bytes() == protected['private']


def test_actual_candidate_git_uses_committed_public_bytes_and_preserves_dirty_checkout(candidate_repo):
    """Only the intended new source bytes enter the candidate; private staged/unstaged/untracked content is excluded and retained."""
    repository, body, prepared, artifact, observation, protected = candidate_repo
    created = repository.create(artifact, prepared, observation)
    expected = body.changes[0].text.encode('utf8')
    assert git(repository.root, 'show', created.commit + ':scripts/features.py') == expected
    assert b'authored-marker' not in expected
    assert git(repository.root, 'rev-list', '--parents', '-n', '1', created.commit).decode().strip().split() == [created.commit, protected['head']]
    receipt = created.receipt()
    assert not receipt['code_executed'] and not receipt['github_push_performed'] and not receipt['pr_created']
    assert not receipt['independent_review_completed'] and not receipt['ci_qualified']
    assert_original(repository, protected)


def test_concurrent_candidate_builders_recover_one_original_ref(candidate_repo):
    """Competing controllers use separate indexes and CAS only the hash-derived reserved branch, retaining one original commit identity."""
    repository, _body, prepared, artifact, observation, protected = candidate_repo
    with ThreadPoolExecutor(max_workers=6) as pool:
        values = list(pool.map(lambda _index: repository.create(artifact, prepared, observation), range(8)))
    assert len({value.commit for value in values}) == 1 and len({value.tree for value in values}) == 1
    assert_original(repository, protected)


def test_original_local_commit_recovery_precedes_new_source_or_preparation_gates(candidate_repo):
    """A lost local commit response recovers the original tree/message/parent/commit even after the new proof expires."""
    repository, _body, prepared, artifact, observation, protected = candidate_repo
    created = repository.create(artifact, prepared, observation)
    reopened = CandidateGitRepository(repository=repository.root)
    assert reopened.create(artifact, None, None) == created
    assert reopened.recover(artifact) == created
    assert_original(repository, protected)


@pytest.mark.parametrize('bad', ['expired', 'base', 'tree', 'bytes'])
def test_new_candidate_requires_exact_fresh_public_objects(candidate_repo, bad):
    """A current prepared task alone cannot authorize a different public base or edited original file bytes."""
    repository, _body, prepared, artifact, observation, protected = candidate_repo
    if bad == 'expired': altered = replace(observation, checked_at_us=time.time_ns() // 1000 - 60000001)
    elif bad == 'base': altered = replace(observation, snapshot=replace(observation.snapshot, commit='c' * 40))
    elif bad == 'tree': altered = replace(observation, snapshot=replace(observation.snapshot, tree='c' * 40))
    else:
        files = dict(observation.snapshot.files); files['scripts/features.py'] += b'# changed public bytes\n'
        altered = replace(observation, snapshot=replace(observation.snapshot, files=files))
    with pytest.raises(CommunityStoreConflict): repository.create(artifact, prepared, altered)
    assert repository.recover(artifact) is None
    assert_original(repository, protected)


def test_reserved_branch_collision_is_never_overwritten(candidate_repo):
    """Another local result at the reserved name requires attention; no force update or checkout can erase it."""
    repository, _body, prepared, artifact, observation, protected = candidate_repo
    git(repository.root, 'update-ref', 'refs/heads/' + artifact['branch'], protected['head'])
    with pytest.raises(CommunityStoreConflict): repository.create(artifact, prepared, observation)
    assert git(repository.root, 'rev-parse', 'refs/heads/' + artifact['branch']).decode().strip() == protected['head']
    assert_original(repository, protected)
