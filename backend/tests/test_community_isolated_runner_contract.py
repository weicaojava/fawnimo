"""Supervisor input/source boundary checks do not stand in for the required real Linux/OCI UID, namespace and resource qualification."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from copy import deepcopy

import pytest

from app.core.contracts.community_code_checks import checked_source_path
from app.services.community.change_bundles import build_change_bundle,protected_code_path
from app.services.community.public_code import git_blob_id
from tests.test_community_change_bundles import prepared_change,legacy_controller_bundle
from tests.test_community_queue_leases import factory

RUNNER = Path(__file__).resolve().parents[1] / 'community_isolated_runner.py'


@pytest.fixture
def supervisor():
    """Import definitions only; the stdlib-only module must not probe a kernel, spawn a child or touch a workspace during import."""
    spec = importlib.util.spec_from_file_location('authored_supervisor_contract', RUNNER)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


@pytest.fixture
def frame(prepared_change):
    """Use exact inert bundle bytes and selected source manifests to test parsing; this is not a full image/code admission proof."""
    body, prepared, snapshot, registry = prepared_change
    artifact = build_change_bundle(body, prepared, snapshot, registry)
    base = {path: {'mode': '100644', 'sha': git_blob_id(raw)} for path, raw in snapshot.files.items()}
    candidate = dict(base)
    for change in body.changes:
        if change.action == 'delete': candidate.pop(change.path)
        else: candidate[change.path] = {'mode': '100644', 'sha': git_blob_id(change.text.encode('utf8'))}
    return {'profile': 'linux_pytest_check_v1', 'attempt_id': '11111111-1111-4111-8111-111111111111',
        'deadline_us': time.time_ns() // 1000 + 59000000, 'runner_sha256': hashlib.sha256(RUNNER.read_bytes()).hexdigest(),
        'artifact': artifact, 'base_manifest': base, 'candidate_manifest': candidate}


@pytest.mark.parametrize('path', ['backend/app/services/community/queue_planning.py', 'backend/app/prompts/voice.md',
    'backend/tests/test_community_candidate_example.py', 'backend/sql/upgrade.sql', 'backend/run_controlplane.py',
    'backend/uv.lock', 'backend/.python-version', 'capabilities/features.json', 'scripts/features.py',
    'backend/community_isolated_runner.py', 'backend/.env', 'backend/app/private/token.json', 'backend/tests/__pycache__/x.pyc',
    '../host.py', '/app/private.py', 'backend/app/../private.py', 'backend\\private.py', 'backend/app/x.py:stream', 'front/src/page.ts'])
def test_supervisor_and_host_agree_on_complete_source_subset(supervisor, path):
    """Duplicated privileged stdlib filtering must stay aligned with the host's pinned-tree manifest policy."""
    assert supervisor.checked_source_path(path) == checked_source_path(path)


@pytest.mark.parametrize('path',['backend/check_community.py','backend/queue_community.py','backend/assess_community.py',
    'backend/review_community.py','backend/unseen_control_entry.py','backend/app/services/community/queue_planning.py','scripts/features.py'])
def test_supervisor_and_host_agree_on_protected_control_entrypoints(supervisor,path):
    """The privileged execution policy mirrors the host's entry-point guard, including controller names introduced after this protocol."""
    assert supervisor.protected_code_path(path) == protected_code_path(path)
    assert protected_code_path(path) == (path.startswith('backend/') and path.count('/')==1)


def test_valid_input_is_inert_and_cannot_spawn_candidate_code(supervisor, frame, monkeypatch):
    """Parsing an exact original bundle never creates a process or imports proposed test text."""
    def forbidden(*_args, **_kwargs):
        """A validation call has no executable operation, even though proposed test text deliberately raises if evaluated."""
        raise AssertionError('Unexpected candidate process')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    result = supervisor.validate_input(frame)
    assert result == frame['artifact']['bundle'] and not supervisor.STATE['code_executed']


def test_finite_complete_frame_does_not_wait_for_docker_stdin_eof(supervisor):
    """The fixed protocol has a complete LF record gate even when Docker retains stdin after a client disconnect."""
    class StillOpen:
        """A complete record is available but an unbounded read would wait indefinitely for a retained stream."""
        def readline(self,limit):
            """Only the bounded complete-line operation may open the trusted input gate."""
            assert limit == supervisor.MAX_INPUT + 1
            return b'{"complete":"frame"}\n'

        def read(self,*_args):
            """Waiting for EOF is not a completion condition for this Docker protocol."""
            raise AssertionError('Unexpected EOF-dependent input read')
    assert supervisor.read_frame(StillOpen()) == b'{"complete":"frame"}\n'


@pytest.mark.parametrize('raw',[b'',b'{}',b'{}\r\n'],ids=['empty','incomplete','crlf'])
def test_partial_or_noncanonical_frame_cannot_open_gate(supervisor,raw):
    """A disconnected writer or noncanonical frame never starts candidate work, independently of JSON validity."""
    with pytest.raises(supervisor.Rejected): supervisor.read_frame(io.BytesIO(raw))


def test_oversized_single_frame_is_bounded_before_json_or_code(supervisor):
    """The pipe reader stops at the fixed cap even if a peer never terminates its JSON record."""
    with pytest.raises(supervisor.Rejected): supervisor.read_frame(io.BytesIO(b'x'*(supervisor.MAX_INPUT+1)+b'\n'))


def test_trusted_supervisor_rejects_legacy_controller_change_before_code(supervisor,legacy_controller_bundle,tmp_path,monkeypatch):
    """An inert artifact accepted by the older host guard cannot bypass the privileged runner's independent current entry-point policy."""
    body,_prepared,snapshot,_registry,artifact=legacy_controller_bundle
    root=tmp_path/'image'; work=tmp_path/'sealed-work'
    for path,raw in snapshot.files.items():
        target=root/path; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(raw)
    base={path:{'mode':'100644','sha':git_blob_id(raw)} for path,raw in snapshot.files.items()}; candidate=dict(base)
    for change in body.changes: candidate[change.path]={'mode':'100644','sha':git_blob_id(change.text.encode('utf8'))}
    value={'profile':'linux_pytest_check_v1','attempt_id':'11111111-1111-4111-8111-111111111111',
        'deadline_us':time.time_ns()//1000+59000000,'runner_sha256':hashlib.sha256(RUNNER.read_bytes()).hexdigest(),
        'artifact':artifact,'base_manifest':base,'candidate_manifest':candidate}
    monkeypatch.setattr(supervisor,'IMAGE_ROOT',root); monkeypatch.setattr(supervisor,'WORK',work)
    payload=supervisor.validate_input(value)
    with pytest.raises(supervisor.Rejected,match='candidate_path_rejected'): supervisor.prepare_workspace(value,payload)
    assert not supervisor.STATE['code_executed'] and not (work/'backend/check_community.py').exists()


@pytest.mark.parametrize('bad', ['profile', 'runner_hash', 'extra_command', 'expired', 'bool_deadline', 'bundle_hash', 'execution', 'manifest_escape', 'manifest_link'])
def test_invalid_parent_framing_rejects_before_code_or_file_preparation(supervisor, frame, bad):
    """Untrusted metadata cannot select another supervisor/protocol, extend time or make a traversal/link manifest usable."""
    value = deepcopy(frame)
    if bad == 'profile': value['profile'] = 'untrusted'
    elif bad == 'runner_hash': value['runner_sha256'] = '0' * 64
    elif bad == 'extra_command': value['command'] = ['arbitrary']
    elif bad == 'expired': value['deadline_us'] = time.time_ns() // 1000 - 1
    elif bad == 'bool_deadline': value['deadline_us'] = True
    elif bad == 'bundle_hash': value['artifact']['content_hash'] = '0' * 64
    elif bad == 'execution': value['artifact']['bundle']['execution_authorized'] = True
    elif bad == 'manifest_escape': value['base_manifest']['../host.py'] = {'mode': '100644', 'sha': '0' * 40}
    else: next(iter(value['base_manifest'].values()))['mode'] = '120000'
    with pytest.raises(supervisor.Rejected): supervisor.validate_input(value)
    assert not supervisor.STATE['code_executed']


def test_standalone_supervisor_rejects_outside_its_explicit_protocol_without_loading_app():
    """A real separate process cannot accidentally use the maintenance machine as an unconfined executor."""
    allowed = {'SYSTEMROOT','WINDIR','COMSPEC','PATH','PATHEXT','TEMP','TMP','TMPDIR','LANG','LC_ALL','TZ'}
    env = {name: value for name, value in os.environ.items() if name.upper() in allowed}
    env.update(PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run([sys.executable, '-I', '-B', '-S', str(RUNNER)], input=b'{}', capture_output=True, env=env, timeout=5)
    assert result.returncode == 125
    report = json.loads(result.stdout)
    assert report['profile'] == 'linux_pytest_check_v1' and report['status'] == 'rejected' and not report['code_executed']
