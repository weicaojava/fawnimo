"""Exercise actual original geometry output and refusal paths using only disposable local files and FFmpeg."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from PIL import Image
import pytest

from app.core.owned_process import run_owned_process
from app.core.media_environment import media_environment
from app.services.studio import demo_media_child as renderer


def parameters(tmp_path):
    """Make private fixture paths with no gateway, database, network or user-media access."""
    output = tmp_path / 'new-output'
    output.mkdir()
    return {'schema_version': 1, 'deadline': 120, 'output': str(output)}


@pytest.mark.parametrize('change', [
    {'schema_version': True}, {'deadline': True}, {'deadline': 120.0}, {'deadline': 3600},
    {'width': 8192}, {'output': '../user-files'}, {'ffmpeg': 'https://fixture.invalid/ffmpeg'},
])
def test_unbounded_or_caller_selected_profiles_are_rejected(tmp_path, change):
    """Public-looking options cannot increase the fixed helper resource profile or select network inputs."""
    value = {**parameters(tmp_path), **change}
    with pytest.raises(ValueError):
        renderer.configuration(json.dumps(value).encode())


def test_duplicate_parameters_and_oversized_stdin_are_rejected(tmp_path):
    """An alternate last value cannot replace the originally validated file path or watchdog."""
    with pytest.raises(ValueError):
        renderer.configuration(b'{"schema_version":1,"schema_version":1}')
    with pytest.raises(ValueError):
        renderer.configuration(b' ' * 16385)


def test_existing_output_is_preserved_without_starting_a_child(tmp_path, monkeypatch):
    """A concurrent creator cannot overwrite the first already-written picture, even after path validation."""
    value = parameters(tmp_path)
    output = renderer.configuration(json.dumps(value).encode())
    old = output / 'scene-01.png'
    old.write_bytes(b'owned-previous-output')

    def reject_process(*args, **kwargs):
        """No encoder may start after an exclusive output collision."""
        raise AssertionError('A previous output must be preserved before encoder creation')

    monkeypatch.setattr(renderer.av, 'open', reject_process)
    with pytest.raises(FileExistsError):
        renderer.render(output)
    assert old.read_bytes() == b'owned-previous-output'
    with pytest.raises(ValueError):
        renderer.configuration(json.dumps(value).encode())


@pytest.mark.asyncio
async def test_actual_seven_original_clips_and_frames_decode(tmp_path):
    """The owned helper produces seven distinct four-second silent MP4s and original PNGs with matching bytes."""
    selected = os.environ.get('FAWNIMO_TEST_FFMPEG') or os.environ.get('TEST_MEDIA_PROBE')
    if not selected or not Path(selected).is_file():
        pytest.skip('A trusted local FFmpeg executable is required')
    output = tmp_path / 'rendered'
    output.mkdir()
    helper = Path(renderer.__file__)
    stdout, returncode = await run_owned_process([sys.executable, str(helper)],
        json.dumps({'schema_version': 1, 'deadline': 120, 'output': str(output)}).encode(), timeout=125, environment=media_environment())
    result = json.loads(stdout)
    assert returncode == 0, result
    assert result['template_version'] == renderer.TEMPLATE and len(result['clips']) == 7
    assert result['external_model_calls'] == 0 and result['provenance'] == 'original_geometry_code'
    assert result['license'] == 'Apache-2.0' and result['model_quality_qualified'] is False
    assert len(list(output.iterdir())) == 14
    assert len({clip['video']['sha256'] for clip in result['clips']}) == 7
    for clip in result['clips']:
        assert (clip['video']['width'], clip['video']['height'], clip['video']['frames'], clip['video']['duration_ms']) == (360, 640, 48, 4000)
        assert clip['video']['audio'] is False
        for kind in ('video', 'picture'):
            path = output / clip[kind]['name']
            assert path.parent == output and path.stat().st_size == clip[kind]['size']
            assert hashlib.sha256(path.read_bytes()).hexdigest() == clip[kind]['sha256']
        with Image.open(output / clip['picture']['name']) as picture:
            assert picture.format == 'PNG' and picture.size == (360, 640)
            picture.load()
        decoded = subprocess.run([selected, '-nostdin', '-hide_banner', '-v', 'error', '-protocol_whitelist', 'file,pipe',
            '-i', str(output / clip['video']['name']), '-f', 'null', '-'], capture_output=True, timeout=15,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        assert decoded.returncode == 0 and not decoded.stderr


@pytest.mark.asyncio
async def test_actual_owned_helper_has_no_model_or_configuration_environment(tmp_path, monkeypatch):
    """A real gated subprocess cannot observe even newly named credentials, proxies or workspace configuration paths."""
    for key in ('FUTURE_MODEL_CREDENTIAL', 'MODEL_PRICE_CONFIG', 'WORKSPACE_LOCAL_MODELS_CONFIG', 'VIDEO_RUNTIME_CONFIG', 'HTTP_PROXY', 'DATABASE_URL'):
        monkeypatch.setenv(key, 'fixture-must-not-reach-child')
    child = tmp_path / 'inspect_environment.py'
    child.write_text('import json,os,sys\nsys.stdin.buffer.read()\nprint(json.dumps(dict(os.environ)))\n', 'utf-8')
    stdout, code = await run_owned_process([sys.executable, str(child)], b'owned-input-gate', timeout=10, environment=media_environment())
    assert code == 0 and b'fixture-must-not-reach-child' not in stdout
    observed = json.loads(stdout)
    assert observed['JELLYFISH_SKIP_ENV_FILE'] == '1' and observed['PYTHONNOUSERSITE'] == '1'
