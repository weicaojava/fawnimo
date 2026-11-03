"""Fixed public Git read/source-identity tests with authored HTTP bytes only, no network, Git credentials or private source data."""
import base64
from copy import deepcopy
import json

import pytest

from app.services.community.change_bundles import PublicCodeSnapshot
from app.services.community.evaluations import canonical
from app.services.community.public_code import (CODE_PROTOCOL, inspect_public_code, git_blob_id, parse_code_observation, verify_public_code)
from app.services.community.store import CommunityStoreConflict
from tests.test_community_evaluations import CONFIG, NOW

PREFIX = '/repos/authored/fawnimo'
HEAD = 'a' * 40
TREE = 'b' * 40
PATH = 'scripts/features.py'
NEW_TEST = 'backend/tests/test_community_candidate_register.py'


@pytest.fixture
def public_fixture():
    """Use a minimal actual registered feature shape and exact Git blob hashes instead of accepting filename/hash claims."""
    registry = {'features': [{'id': 'F-015.01', 'implementation': 'implemented', 'validation': {'software': 'verified'}, 'depends_on': [], 'code': [PATH]}]}
    files = {'capabilities/features.json': canonical(registry).encode('utf8'), PATH: b'VALUE = 1\n'}
    responses = {PREFIX: {'id': 17, 'full_name': 'authored/fawnimo', 'private': False, 'visibility': 'public', 'archived': False, 'disabled': False},
        PREFIX + '/git/ref/heads/main': {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': HEAD}},
        PREFIX + '/git/commits/' + HEAD: {'sha': HEAD, 'tree': {'sha': TREE}},
        PREFIX + '/git/trees/' + TREE + '?recursive=1': {'sha': TREE, 'truncated': False, 'tree': [
            {'path': path, 'type': 'blob', 'mode': '100644', 'sha': git_blob_id(raw), 'size': len(raw)} for path, raw in files.items()]}}
    for raw in files.values():
        responses[PREFIX + '/git/blobs/' + git_blob_id(raw)] = {'sha': git_blob_id(raw), 'encoding': 'base64', 'size': len(raw),
            'content': base64.b64encode(raw).decode('ascii') + '\n'}
    calls = []
    def reader(path):
        """Record exact fixed-host paths, returning real strict JSON bytes without an injectable network origin."""
        calls.append(path)
        return canonical(responses[path]).encode('utf8')
    return responses, files, calls, reader


def wire(observation):
    """Serialize the exact database-free owned-child protocol for parent validation tests."""
    snapshot = observation.snapshot
    return {'protocol': observation.protocol, 'repository_id': snapshot.repository_id, 'repository_name': snapshot.repository_name,
        'commit': snapshot.commit, 'tree': snapshot.tree, 'checked_at_us': observation.checked_at_us,
        'response_sha256': list(observation.response_sha256),
        'files_base64': {path: base64.b64encode(raw).decode('ascii') for path, raw in snapshot.files.items()}}


def observe(fixture):
    """Request only one registered existing source file and one explicitly new candidate-test path."""
    return inspect_public_code(CONFIG, ['F-015.01'], [PATH, NEW_TEST], reader=fixture[3], clock_us=NOW)


def test_fixed_public_reads_bind_numeric_repo_main_commit_tree_and_actual_blob_bytes(public_fixture):
    """Public object URLs embedded in JSON are not followed; all requests use server-owned paths and main is checked twice."""
    responses, files, calls, _reader = public_fixture
    responses[PREFIX]['url'] = 'https://untrusted.invalid/credential'
    result = observe(public_fixture)
    assert result.snapshot == PublicCodeSnapshot(17, 'authored/fawnimo', HEAD, TREE, files)
    assert calls.count(PREFIX + '/git/ref/heads/main') == 2 and len(calls) == 7
    assert all(path.startswith(PREFIX) and 'untrusted' not in path for path in calls)
    restored = parse_code_observation(canonical(wire(result)).encode('utf8'), CONFIG, now=NOW)
    assert restored == result and not NEW_TEST in restored.snapshot.files
    assert result.receipt()['selected_public_files'][PATH] != git_blob_id(files[PATH])


@pytest.mark.parametrize('bad', ['repo_id', 'repo_private', 'ref_type', 'commit_sha', 'tree_sha', 'truncated', 'duplicate_path', 'symlink', 'submodule', 'executable', 'oversize', 'blob_sha', 'blob_bytes', 'base64'])
def test_ineligible_or_inconsistent_public_objects_are_rejected(public_fixture, bad):
    """Neither matching filenames nor public response metadata can substitute another code object or a redirected file mode."""
    responses, files, calls, _reader = public_fixture
    listing = responses[PREFIX + '/git/trees/' + TREE + '?recursive=1']
    target = next(item for item in listing['tree'] if item['path'] == PATH)
    blob = responses[PREFIX + '/git/blobs/' + git_blob_id(files[PATH])]
    if bad == 'repo_id': responses[PREFIX]['id'] = 18
    elif bad == 'repo_private': responses[PREFIX]['private'] = True
    elif bad == 'ref_type': responses[PREFIX + '/git/ref/heads/main']['object']['type'] = 'tag'
    elif bad == 'commit_sha': responses[PREFIX + '/git/commits/' + HEAD]['sha'] = 'c' * 40
    elif bad == 'tree_sha': listing['sha'] = 'c' * 40
    elif bad == 'truncated': listing['truncated'] = True
    elif bad == 'duplicate_path': listing['tree'].append(deepcopy(target))
    elif bad == 'symlink': target['mode'] = '120000'
    elif bad == 'submodule': target['mode'] = '160000'; target['type'] = 'commit'
    elif bad == 'executable': target['mode'] = '100755'
    elif bad == 'oversize': target['size'] = 131073
    elif bad == 'blob_sha': blob['sha'] = 'c' * 40
    elif bad == 'blob_bytes': blob['content'] = base64.b64encode(b'VALUE = 2\n').decode('ascii')
    else: blob['content'] = 'invalid@@@'
    with pytest.raises(CommunityStoreConflict):
        observe(public_fixture)
    assert calls.count(PREFIX + '/git/ref/heads/main') <= 1


def test_main_ref_change_during_collection_rejects_old_observation(public_fixture):
    """Immutable old blob hashes cannot turn an outdated main ref into continuing permission for a new change."""
    _responses, _files, calls, reader = public_fixture
    def changed(path):
        """Return a newer ref only after the code was read, preserving the original immutable object bytes."""
        if path == PREFIX + '/git/ref/heads/main' and calls.count(path) == 1:
            calls.append(path)
            return canonical({'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': 'c' * 40}}).encode('utf8')
        return reader(path)
    with pytest.raises(CommunityStoreConflict):
        inspect_public_code(CONFIG, ['F-015.01'], [PATH, NEW_TEST], reader=changed, clock_us=NOW)


@pytest.mark.parametrize('path', ['backend/unregistered.py', 'backend/app/core/owned_process.py', 'backend/app/services/community/public_code.py'])
def test_unregistered_or_protected_files_are_not_collected(public_fixture, path):
    """Client replacement paths cannot widen public source collection into a controller change or arbitrary file request."""
    with pytest.raises(CommunityStoreConflict):
        inspect_public_code(CONFIG, ['F-015.01'], [path], reader=public_fixture[3], clock_us=NOW)
    assert not any(path in call for call in public_fixture[2])


@pytest.mark.parametrize('path',['backend/check_community.py','backend/queue_community.py','backend/assess_community.py',
    'backend/review_community.py','backend/unseen_control_entry.py','backend/app/services/community/advice.py',
    'backend/app/services/community/advice_store.py','backend/app/services/community/advice_child.py',
    'backend/app/services/community/advice_future_worker.py'])
def test_root_controller_rejected_before_any_public_read(path):
    """A future registered controller name cannot widen source collection; policy rejection happens before any network request."""
    def forbidden(_path):
        """No fixed-host read is needed to decide that a top-level execution entry point is protected."""
        raise AssertionError('Unexpected source read for a protected entry point')
    with pytest.raises(CommunityStoreConflict): inspect_public_code(CONFIG,['F-015.01'],[path],reader=forbidden,clock_us=NOW)


@pytest.mark.parametrize('bad', ['stale', 'future', 'bool_clock', 'wrong_repo', 'host', 'bad_hash', 'bad_files', 'protocol'])
def test_parent_refuses_stale_or_changed_child_protocol(public_fixture, bad):
    """A helper response must match the exact configured public source and finite observation window, with no extra authority fields."""
    value = wire(observe(public_fixture))
    if bad == 'stale': value['checked_at_us'] = NOW - 60000001
    elif bad == 'future': value['checked_at_us'] = NOW + 1
    elif bad == 'bool_clock': value['checked_at_us'] = True
    elif bad == 'wrong_repo': value['repository_id'] = 18
    elif bad == 'host': value['host'] = 'untrusted.invalid'
    elif bad == 'bad_hash': value['response_sha256'][0] = 'not-a-hash'
    elif bad == 'bad_files': value['files_base64'][PATH] = 'invalid@@'
    else: value['protocol'] = 'untrusted-protocol'
    with pytest.raises(CommunityStoreConflict):
        parse_code_observation(canonical(value).encode('utf8'), CONFIG, now=NOW)


@pytest.mark.asyncio
async def test_owned_code_helper_uses_fixed_argv_and_os_only_environment(public_fixture, monkeypatch):
    """The shipped source collector never receives model, studio, Git or proxy credentials from the parent environment."""
    result = observe(public_fixture)
    async def owned(command, input_data, **options):
        """Inspect the actual production helper invocation without making a real network request."""
        assert command[-1].endswith('public_code_child.py') and len(command) == 2
        supplied = json.loads(input_data)
        assert supplied['paths'] == [PATH, NEW_TEST] and supplied['feature_ids'] == ['F-015.01']
        assert options['timeout'] == 45 and options['max_stdout_bytes'] == 8388608
        assert not any(name in options['environment'] for name in ('OPENAI_API_KEY', 'GITHUB_TOKEN', 'DATABASE_URL', 'HTTPS_PROXY'))
        # The parent uses its actual wall clock; this fixture's observation is
        # refreshed without changing immutable Git/file identities.
        import time
        value = wire(result); value['checked_at_us'] = time.time_ns() // 1000
        return canonical(value).encode('utf8'), 0
    monkeypatch.setenv('GITHUB_TOKEN', 'authored-secret-never-propagated')
    monkeypatch.setenv('OPENAI_API_KEY', 'authored-secret-never-propagated')
    monkeypatch.setenv('DATABASE_URL', 'authored-private-root-never-propagated')
    monkeypatch.setenv('HTTPS_PROXY', 'authored-private-proxy-never-propagated')
    monkeypatch.setattr('app.core.owned_process.run_owned_process', owned)
    observed = await verify_public_code(CONFIG, ['F-015.01'], [PATH, NEW_TEST])
    assert observed.snapshot == result.snapshot
