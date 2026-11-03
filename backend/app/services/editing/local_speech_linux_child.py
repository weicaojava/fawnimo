"""Fixed headless Linux system speech helper; stdin carries original dialogue, never shell options or credentials."""
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import wave

# Load only this repository-owned stdlib contract, independent of editable installs or model/application settings.
_CONTRACT = Path(__file__).resolve().parent.parent.parent/'core/contracts/local_speech_protocols.py'
_SPEC = importlib.util.spec_from_file_location('owned_local_speech_protocols', _CONTRACT)
_PROTOCOLS = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_PROTOCOLS)
PROFILE = _PROTOCOLS.LINUX_PROFILE
ESPEAK = Path('/usr/bin/espeak-ng')
FFMPEG = Path('/usr/bin/ffmpeg')
MAX_INPUT = 32768
MAX_WAVE = 32 * 1024 * 1024
LANGUAGES = frozenset({'cmn', 'en-us'})


class SpeechCommandError(RuntimeError):
    """Keep a fixed failing stage and return code, without exposing dialogue or raw subprocess errors."""
    def __init__(self, stage, returncode, stdout_bytes=0, stderr_bytes=0):
        """Attach only application-owned diagnostic fields for the original bounded command."""
        super().__init__('Installed Linux speech command did not complete')
        self.stage, self.returncode = stage, returncode
        self.stdout_bytes, self.stderr_bytes = stdout_bytes, stderr_bytes


def configuration(raw):
    """Accept only the fixed catalog/render frame, with strict resource limits and no filename/model overrides."""
    def unique(pairs):
        """Duplicate fields cannot replace a previously checked voice, text or output directory."""
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('Duplicate local speech field')
            value[key] = item
        return value

    def invalid(_value):
        """Nonfinite JSON is never a local engine option."""
        raise ValueError('Invalid numeric local speech field')

    if not isinstance(raw, bytes) or len(raw) > MAX_INPUT:
        raise ValueError('Local speech frame exceeds its limit')
    value = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)
    if not isinstance(value, dict) or not isinstance(value.get('mode'), str) or value['mode'] not in {'catalog', 'render'}:
        raise ValueError('Unknown local speech operation')
    if value['mode'] == 'catalog':
        if set(value) != {'mode'}:
            raise ValueError('Catalog has no configurable paths or commands')
        return value
    if set(value) != {'mode', 'voice', 'text', 'rate', 'folder'}:
        raise ValueError('Invalid local speech render fields')
    if type(value['rate']) is not int or not -2 <= value['rate'] <= 2:
        raise ValueError('Invalid local speech rate')
    text = value['text']
    if not isinstance(text, str) or not 1 <= len(text) <= 1000 or not text.strip() or any(ord(char) < 32 and char not in '\n\t\r' for char in text):
        raise ValueError('Invalid original dialogue')
    text.encode('utf8')
    voice = value['voice']
    if (not isinstance(voice, dict) or set(voice) != {'id', 'name', 'culture'}
            or not isinstance(voice.get('culture'), str) or voice['culture'] not in LANGUAGES):
        raise ValueError('Unsupported installed voice')
    _PROTOCOLS.voice_identity(PROFILE, {key: voice[key] for key in ('name', 'culture')}, '0' * 64)
    if not isinstance(voice['id'], str) or len(voice['id']) != 71 or not voice['id'].startswith('espeak_') or any(char not in '0123456789abcdef' for char in voice['id'][7:]):
        raise ValueError('Invalid original voice identity')
    if not isinstance(value['folder'], str) or len(value['folder']) > 4096:
        raise ValueError('Invalid private output directory')
    folder = Path(value['folder'])
    if not folder.is_absolute() or folder.is_symlink() or not folder.is_dir() or folder.resolve() != folder:
        raise ValueError('Expected a real absolute private attempt directory')
    return value


def limit_resources():
    """Bound this helper and its inherited child resources without changing another process or user-wide PID limits."""
    import resource
    os.umask(0o077)
    for kind, limit in ((resource.RLIMIT_CORE, 0), (resource.RLIMIT_CPU, 60), (resource.RLIMIT_FSIZE, MAX_WAVE),
                        (resource.RLIMIT_AS, 1024 * 1024 * 1024), (resource.RLIMIT_NOFILE, 64)):
        soft, hard = resource.getrlimit(kind)
        selected = limit if hard == resource.RLIM_INFINITY else min(limit, hard)
        resource.setrlimit(kind, (selected, selected))


def trusted_binary(path):
    """Use only the fixed root-owned installed ELF; no model response or caller can select an executable."""
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o6022 or not info.st_mode & 0o111 or not 4 <= info.st_size <= 16 * 1024 * 1024:
        raise ValueError('Installed system binary is unavailable')
    with path.open('rb') as stream:
        if stream.read(4) != b'\x7fELF':
            raise ValueError('Expected an installed Linux executable')
        stream.seek(0)
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def command(binary, arguments, folder, *, content=None, descriptors=(), timeout=10, stage='system_command'):
    """Keep subprocess input/output bounded and private, with literal arguments and an environment containing no application secrets."""
    environment = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'HOME': str(folder), 'TMPDIR': str(folder),
        'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1', 'NUMEXPR_NUM_THREADS': '1'}
    with tempfile.TemporaryFile(dir=folder) as output, tempfile.TemporaryFile(dir=folder) as errors:
        result = subprocess.run([str(binary), *arguments], input=content, stdout=output, stderr=errors,
            cwd=folder, env=environment, pass_fds=descriptors, timeout=timeout)
        if result.returncode or output.tell() > 65536 or errors.tell() > 65536:
            raise SpeechCommandError(stage, result.returncode, output.tell(), errors.tell())
        output.seek(0)
        return output.read(65537)


def catalog(folder):
    """Enumerate only installed basic Mandarin/US-English voices, binding selection to the actual binary and version."""
    checksum = trusted_binary(ESPEAK)
    raw_version = command(ESPEAK, ['--version'], folder, stage='engine_version')
    lines = raw_version.decode('utf8').splitlines()
    if not lines or not 1 <= len(lines[0]) <= 512:
        raise ValueError('Invalid installed engine version')
    fingerprint = hashlib.sha256(json.dumps({'profile': PROFILE, 'binary_sha256': checksum,
        'version': lines[0]}, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    content = command(ESPEAK, ['--voices'], folder, stage='installed_voices').decode('utf8')
    voices, seen = [], set()
    for line in content.splitlines():
        columns = line.split()
        if len(columns) < 5 or columns[1] not in LANGUAGES:
            continue
        culture = columns[1]
        if culture in seen or columns[4].startswith('mb/'):
            raise ValueError('Ambiguous installed basic voice')
        row = {'name': columns[3].replace('_', ' '), 'culture': culture}
        _PROTOCOLS.voice_identity(PROFILE, row, fingerprint)
        voices.append(row)
        seen.add(culture)
    return {'voices': sorted(voices, key=lambda row: row['culture']), 'engine_fingerprint': fingerprint}


def file_descriptor(stack, directory, name):
    """Create only a new fixed attempt file and retain its descriptor across trusted subprocesses."""
    descriptor = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    stack.callback(os.close, descriptor)
    return descriptor


def render(value):
    """Synthesize the exact original dialogue and normalize a fresh owned descriptor to PCM16/48kHz mono."""
    folder = Path(value['folder'])
    installed = catalog(folder)
    candidates = [{'id': _PROTOCOLS.voice_identity(PROFILE, voice, installed['engine_fingerprint']), **voice}
        for voice in installed['voices']]
    if value['voice'] not in candidates:
        raise ValueError('Original installed voice changed or was removed')
    trusted_binary(FFMPEG)
    with ExitStack() as stack:
        directory = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, directory)
        owner = os.fstat(directory)
        if owner.st_uid != os.getuid():
            raise ValueError('Private attempt owner changed')
        raw_fd = file_descriptor(stack, directory, 'espeak-source.wav')
        output_fd = file_descriptor(stack, directory, 'voice.wav')
        speed = 175 + 25 * value['rate']
        command(ESPEAK, ['-b', '1', '-v', value['voice']['culture'], '-s', str(speed),
            '--stdin', '-w', '/proc/self/fd/'+str(raw_fd)], folder,
            content=value['text'].encode('utf8'), descriptors=(raw_fd,), timeout=35, stage='synthesize_original_dialogue')
        os.fsync(raw_fd)
        if not 44 <= os.fstat(raw_fd).st_size <= MAX_WAVE:
            raise ValueError('Installed speech output exceeds its byte limit')
        os.lseek(raw_fd, 0, os.SEEK_SET)
        with os.fdopen(os.dup(raw_fd), 'rb') as stream, wave.open(stream, 'rb') as source:
            if (source.getcomptype() != 'NONE' or source.getsampwidth() != 2 or source.getnchannels() != 1
                    or not 8000 <= source.getframerate() <= 96000 or not 0 < source.getnframes() <= source.getframerate() * 300):
                raise ValueError('Installed speech output is outside the original bounded PCM profile')
        os.lseek(raw_fd, 0, os.SEEK_SET)
        command(FFMPEG, ['-nostdin', '-hide_banner', '-v', 'error', '-protocol_whitelist', 'file,pipe',
            '-threads', '1', '-filter_threads', '1', '-filter_complex_threads', '1', '-i', '/proc/self/fd/'+str(raw_fd),
            '-map', '0:a:0', '-vn', '-ac', '1', '-ar', '48000', '-c:a', 'pcm_s16le', '-f', 'wav',
            '-y', '/proc/self/fd/'+str(output_fd)], folder, descriptors=(raw_fd, output_fd), timeout=20, stage='normalize_pcm')
        os.fsync(output_fd)
        if not 44 <= os.fstat(output_fd).st_size <= MAX_WAVE:
            raise ValueError('Normalized speech output exceeds its byte limit')
        current = folder.stat()
        if (current.st_dev, current.st_ino) != (owner.st_dev, owner.st_ino):
            raise ValueError('Private attempt directory changed')
    return {'completed': True}


def main():
    """Serve exactly one bounded local operation; report only fixed failure metadata and never user dialogue."""
    stage = 'configuration'
    try:
        if not sys.platform.startswith('linux'):
            raise ValueError('Linux local speech profile requires Linux')
        limit_resources()
        value = configuration(sys.stdin.buffer.read(MAX_INPUT + 1))
        stage = value['mode']
        if value['mode'] == 'catalog':
            with tempfile.TemporaryDirectory(prefix='fawnimo-speech-catalog-') as folder:
                result = catalog(Path(folder))
        else:
            result = render(value)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as error:
        record = {'error': 'installed_linux_speech_unavailable', 'stage': getattr(error, 'stage', stage), 'error_type': type(error).__name__}
        if isinstance(error, SpeechCommandError):
            record['returncode'] = error.returncode
            record['stdout_bytes'] = error.stdout_bytes
            record['stderr_bytes'] = error.stderr_bytes
        print(json.dumps(record))
        return 1


if __name__ == '__main__':
    sys.exit(main())
