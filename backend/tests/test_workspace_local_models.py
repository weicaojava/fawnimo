"""Default-closed local-model exceptions and fixed-workspace startup selection, using authored files only."""
import json
import os
from dataclasses import asdict
import pytest

from app.services.worker.workspace_local_models import load_workspace_local_models
from run_tenant_worker import configure_worker
from tests.test_model_price_workspace import SPACE_A, SPACE_B, marked, startup


def setup_file(tmp_path, workspace=SPACE_A, **changes):
    """Create a new private fixture with exact protocol ports and no supplier credentials or user resources."""
    path = tmp_path / 'local-models.json'
    path.write_text(json.dumps({'schema_version': 1, 'workspace_id': workspace, 'enabled': True,
        'agent_text_ports': [19001], 'asset_image_ports': [19002], 'video_understanding_ports': [19003],
        'native_video_port': 19004, **changes}), 'utf-8')
    return path


def closed(config):
    """Assert all transport exceptions are absent, not merely that one selected port is disabled."""
    return not config.agent_text_ports and not config.asset_image_ports and not config.video_understanding_ports and config.native_video_port is None


def test_default_disabled_and_standalone_settings_never_grant_local_ports(tmp_path):
    """No file, missing file, disabled config or absent fixed identity leaves every local protocol closed."""
    assert closed(load_workspace_local_models(None, workspace_id=SPACE_A))
    assert closed(load_workspace_local_models(str(tmp_path / 'missing'), workspace_id=SPACE_A))
    path = setup_file(tmp_path, enabled=False)
    result = load_workspace_local_models(str(path), workspace_id=SPACE_A)
    assert closed(result) and result.issue == 'disabled'
    path = setup_file(tmp_path)
    assert closed(load_workspace_local_models(str(path), workspace_id=None))


def test_exact_owner_protocols_and_ports_are_required_without_other_host_exceptions(tmp_path):
    """One workspace's file cannot enable its sibling; only the chosen protocol gets the fixed IPv4 host."""
    path = setup_file(tmp_path)
    result = load_workspace_local_models(str(path), workspace_id=SPACE_A)
    assert result.issue == 'configured' and result.agent_text_ports == frozenset({19001})
    assert result.asset_image_ports == frozenset({19002}) and result.video_understanding_ports == frozenset({19003})
    assert result.native_video_port == 19004
    assert result.host_for('agent_text') == result.host_for('native_video') == '127.0.0.1'
    assert result.host_for('caller_host') is None
    assert closed(load_workspace_local_models(str(path), workspace_id=SPACE_B))
    single = load_workspace_local_models(str(setup_file(tmp_path, agent_text_ports=[])), workspace_id=SPACE_A)
    assert single.host_for('agent_text') is None and single.host_for('asset_image') == '127.0.0.1'


@pytest.mark.parametrize('changes', [
    {'enabled': 1}, {'schema_version': True}, {'schema_version': '1'}, {'schema_version': 2},
    {'host': 'example.invalid'}, {'api_key': 'never-reflect-authored-private-text'},
    {'agent_text_ports': [True]}, {'agent_text_ports': ['19001']}, {'agent_text_ports': [80]},
    {'asset_image_ports': [65536]}, {'video_understanding_ports': [19003, 19003]},
    {'native_video_port': False}, {'native_video_port': '19004'}, {'agent_text_ports': list(range(19000, 19009))},
])
def test_invalid_config_is_inert_without_echoing_private_file_content(tmp_path, changes):
    """Strict bounded fields reject coercions, arbitrary routing, keys and oversized exception lists."""
    path = setup_file(tmp_path, **changes)
    result = load_workspace_local_models(str(path), workspace_id=SPACE_A)
    assert closed(result) and result.issue == 'invalid_configuration'
    assert 'never-reflect' not in str(asdict(result))


@pytest.mark.parametrize('content', [b'{', b'x' * 16385])
def test_malformed_or_oversized_private_file_cannot_enable_ports(tmp_path, content):
    """File parsing failures preserve manual work and do not introduce an unbounded configuration read."""
    path = tmp_path / 'local-models.json'; path.write_bytes(content)
    result = load_workspace_local_models(str(path), workspace_id=SPACE_A)
    assert closed(result) and result.issue == 'invalid_configuration'


@pytest.mark.parametrize('tail', ['"enabled": false, "enabled": true', '"enabled": true, "agent_text_ports": [NaN]'])
def test_ambiguous_or_nonfinite_json_cannot_grant_an_exception(tmp_path, tail):
    """A hand-edited duplicate enable flag and non-JSON numeric literal both leave every protocol closed."""
    path = tmp_path / 'local-models.json'
    path.write_text('{"workspace_id": "' + SPACE_A + '", ' + tail + '}', 'utf-8')
    result = load_workspace_local_models(str(path), workspace_id=SPACE_A)
    assert closed(result) and result.issue == 'invalid_configuration'


@pytest.mark.parametrize('workspace', [SPACE_A, SPACE_B])
def test_fixed_worker_selects_only_its_own_private_local_model_file(tmp_path, monkeypatch, workspace):
    """Neither an inherited setting nor a new startup-message field selects another workspace's policy."""
    for identity in (SPACE_A, SPACE_B):
        private = marked(tmp_path, identity) / 'private'; private.mkdir()
        setup_file(private, identity)
    monkeypatch.setattr(os, 'environ', {**os.environ, 'WORKSPACE_LOCAL_MODELS_CONFIG': 'untrusted-inherited-file'})
    configure_worker(startup(tmp_path, workspace))
    expected = tmp_path / workspace / 'private/local-models.json'
    assert os.environ['WORKSPACE_LOCAL_MODELS_CONFIG'] == str(expected)
    assert load_workspace_local_models(str(expected), workspace_id=workspace).issue == 'configured'
    with pytest.raises(ValueError):
        configure_worker({**startup(tmp_path, workspace), 'local_models_config': str(tmp_path / 'another-file')})


def test_imported_workspace_lock_cannot_be_overridden_by_local_model_file(tmp_path, monkeypatch):
    """An explicit offline import retains its execution lock even when a same-owner fixture config is present."""
    directory = marked(tmp_path, SPACE_A, imported=True); private = directory / 'private'; private.mkdir()
    setup_file(private)
    monkeypatch.setattr(os, 'environ', {**os.environ, 'WORKSPACE_LOCAL_MODELS_CONFIG': 'untrusted-inherited-file'})
    configure_worker(startup(tmp_path, SPACE_A))
    assert os.environ['WORKSPACE_LOCAL_MODELS_CONFIG'] == '' and os.environ['WORKSPACE_RUNTIME_LOCKED'] == 'true'


def test_redirected_local_model_file_is_rejected_before_startup_mutates_environment(tmp_path, monkeypatch):
    """A static file redirect cannot let one worker load a policy stored outside its fixed directory."""
    directory = marked(tmp_path, SPACE_A); private = directory / 'private'; private.mkdir()
    outside = setup_file(tmp_path)
    try:
        (private / 'local-models.json').symlink_to(outside)
    except OSError:
        pytest.skip('Creating file symlinks requires platform permission')
    monkeypatch.setattr(os, 'environ', {**os.environ, 'WORKSPACE_LOCAL_MODELS_CONFIG': 'unchanged-before-rejection'})
    with pytest.raises(ValueError, match='local model setup cannot redirect'):
        configure_worker(startup(tmp_path, SPACE_A))
    assert os.environ['WORKSPACE_LOCAL_MODELS_CONFIG'] == 'unchanged-before-rejection'
    assert closed(load_workspace_local_models(str(private / 'local-models.json'), workspace_id=SPACE_A))
