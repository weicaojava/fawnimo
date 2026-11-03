"""Actual CLI inert defaults/safe failures and bounded private tar parsing; no daemon, model or existing account is used."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from types import SimpleNamespace

import pytest

from app.services.community.runtime_release_docker import RuntimeDocker
from app.services.community.runtime_release_store import RuntimeReleaseError


def run_cli(root, *arguments):
    """Invoke the real new command using this interpreter and independent private fixtures with .env discovery disabled."""
    return subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / 'manage_runtime_release.py'), '--root', str(root), *arguments],
                          capture_output=True, timeout=30, env={**os.environ, 'JELLYFISH_SKIP_ENV_FILE': '1', 'PYTHONDONTWRITEBYTECODE': '1'})


def test_actual_cli_initialization_status_and_disabled_activation(tmp_path):
    """Init/status require no engine; a denied activation neither reserves work nor exposes private input values in errors."""
    root = tmp_path / 'operator'
    result = run_cli(root, 'init')
    assert result.returncode == 0 and json.loads(result.stdout)['daemon_contacted'] is False
    status = run_cli(root, 'status')
    assert status.returncode == 0 and json.loads(status.stdout)['enabled'] is False
    body = tmp_path / 'intent.json'
    body.write_text(json.dumps({'request_key': 'actual-cli-disabled', 'slot': 'stable', 'expected_generation': 0,
        'image_id': 'sha256:' + '1' * 64, 'reason': 'private-authored-reason-not-for-stderr'}))
    denied = run_cli(root, 'activate', '--input', str(body))
    assert denied.returncode == 1
    assert b'private-authored-reason-not-for-stderr' not in denied.stderr and b'Traceback' not in denied.stderr
    assert not denied.stdout


def tar(content, *, name='result.json', special=None):
    """Create bounded authored tar replies, including malicious links/path names, without writing extracted files."""
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as archive:
        entry = tarfile.TarInfo(name)
        if special is not None:
            entry.type = special
            entry.linkname = '/existing-private-file'
            archive.addfile(entry)
        else:
            entry.size = len(content)
            archive.addfile(entry, io.BytesIO(content))
    return output.getvalue()


@pytest.mark.parametrize('reply', [tar(b'{}', name='../result.json'), tar(b'{}', special=tarfile.SYMTYPE),
    tar(b'{}', special=tarfile.LNKTYPE), tar(b'{"same":1,"same":2}'), tar(b'x' * 16385), b'not-a-tar'],
    ids=['parent-path', 'symbolic-link', 'hard-link', 'duplicate-fields', 'oversized', 'partial-tar'])
def test_readonly_helper_copy_refuses_links_paths_duplicates_oversize_and_partial_tar(reply, monkeypatch):
    """Docker cp is parsed in memory only; a private result cannot choose a host path, alias or duplicate semantic field."""
    cli = SimpleNamespace(command=lambda *args: SimpleNamespace(stdout=reply))
    docker = RuntimeDocker(cli, 'a' * 64)
    monkeypatch.setattr(docker, 'inspect', lambda *args, **kwargs: {'State': {'Running': False, 'Pid': 0}})
    value = {'id': 'b' * 64, 'kind': 'container', 'role': 'helper-initialize', 'operation': 'c' * 64}
    with pytest.raises(RuntimeReleaseError): docker._read_private_json(value, '/target/result.json', 'result.json', 16384)


@pytest.mark.parametrize('name', ['review_community.py', 'observe_community_pr.py', 'publish_community.py', 'manage_local_release.py', 'reconcile_model_billing.py'])
def test_existing_operator_clis_remain_separate_and_usable(name):
    """The new runtime entry point does not replace or overload the already shipped review/data/billing commands."""
    result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / name), '--help'], capture_output=True,
                            timeout=30, env={**os.environ, 'JELLYFISH_SKIP_ENV_FILE': '1'})
    assert result.returncode == 0 and b'usage:' in result.stdout
