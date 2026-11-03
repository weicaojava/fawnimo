"""Trusted stdlib-only Linux supervisor; proposed Python runs only as a different UID after exact source/kernel checks and an input gate."""
import base64
import errno
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import signal
import stat
import subprocess
import sys
import threading
import time

PROFILE = 'linux_pytest_check_v1'
MAX_INPUT = 12582912
MAX_OUTPUT = 65536
MAX_SOURCE = 67108864
MAX_FILES = 4096
WORK = Path('/tmp/fawnimo-code-work')
IMAGE_ROOT = Path('/app')
STATE = {'profile': PROFILE, 'status': 'rejected', 'reason': 'not_started', 'code_executed': False,
    'independent_review_completed': False, 'ci_qualified': False, 'deployment_authorized': False,
    'external_model_calls': 0, 'paid_authorization': 0}
PROTECTED = ('backend/app/core/', 'backend/app/controlplane/', 'backend/app/api/',
    'backend/app/services/community/change_', 'backend/app/services/community/development_',
    'backend/app/services/community/candidate_', 'backend/app/services/community/isolated_', 'backend/app/services/community/code_check',
    'backend/app/services/community/review_gate', 'backend/app/services/community/public_source', 'backend/app/services/community/public_code',
    'backend/app/services/community/source_check_', 'backend/app/services/community/store.py', 'backend/app/services/community/queue_store.py',
    'backend/app/services/community/queue_schema.py', 'backend/app/services/community/queue_lease', 'backend/app/services/community/rowid_',
    'backend/app/services/community/evaluations.py', 'backend/app/services/community/events.py', 'backend/develop_community.py', 'backend/community_isolated_runner.py')


class Rejected(ValueError):
    """Expose only fixed supervisor failure codes, never candidate source, environment, private paths or tracebacks."""


def canonical(value):
    """Use the same exact semantic JSON hash as the host, with no eval, dynamic import or command parsing."""
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    """Bind source/protocol records independently of any candidate-generated success text."""
    return hashlib.sha256(canonical(value).encode('utf8')).hexdigest()


def unique_fields(items):
    """Reject ambiguous duplicate JSON fields throughout the gated parent input."""
    value = {}
    for key, item in items:
        if key in value: raise Rejected('duplicate_json_field')
        value[key] = item
    return value


def invalid_number(_value):
    """Nonfinite JSON numbers cannot alter limits or hash interpretation."""
    raise Rejected('invalid_json_number')


def read_frame(stream):
    """Read one complete bounded LF JSON frame; Docker may keep container stdin open after its attach client's EOF."""
    raw = stream.readline(MAX_INPUT + 1)
    if not 1 <= len(raw) <= MAX_INPUT or not raw.endswith(b'\n') or raw.endswith(b'\r\n'):
        raise Rejected('input_size_or_framing')
    return raw


def finite_float(value):
    """Keep complete JSON parsing finite even for numeric fields unused by this fixed profile."""
    parsed = float(value)
    if not math.isfinite(parsed): raise Rejected('invalid_json_number')
    return parsed


def checked_source_path(path):
    """Mirror the fixed public-code subset without importing app code into the privileged supervisor."""
    if (not isinstance(path, str) or not path or len(path) > 500 or path.startswith('/') or '\\' in path or ':' in path
            or any(ord(char) < 32 for char in path) or any(part in {'', '.', '..'} for part in path.split('/'))): return False
    parts = PurePosixPath(path).parts
    if (any(part in {'.git','.venv','__pycache__','node_modules','private','local-files','local-media','local-studio','logs','work','outputs'} for part in parts)
            or any(part.startswith('.env') for part in parts) or path.endswith(('.pyc','.pyo'))
            or path == 'backend/community_isolated_runner.py'): return False
    return (path.startswith(('backend/app/', 'backend/tests/', 'backend/sql/'))
        or len(parts) == 2 and parts[0] == 'backend' and parts[1].endswith('.py')
        or path in {'backend/pyproject.toml','backend/uv.lock','backend/README.md','backend/.python-version','scripts/features.py','capabilities/features.json'})


def git_blob(raw):
    """Verify actual image/workspace bytes against their pinned Git blob identities, never just reported filenames."""
    return hashlib.sha1(b'blob ' + str(len(raw)).encode('ascii') + b'\0' + raw).hexdigest()


def protected_code_path(path):
    """Independently protect top-level entries and the current/future local-advice controller family without importing candidate policy."""
    return path.startswith(PROTECTED) or path.startswith('backend/app/services/community/advice') or path.startswith('backend/') and path.count('/') == 1


def process_status(identifier='self'):
    """Read the real kernel's process attributes before any untrusted source import or child spawn."""
    return {line.split(':', 1)[0]: line.split(':', 1)[1].strip() for line in Path('/proc', str(identifier), 'status').read_text().splitlines() if ':' in line}


def kernel_profile():
    """Require cgroupv2 hard limits, no external interface, restricted supervisor capabilities, seccomp/NNP and a readonly image."""
    if sys.platform != 'linux' or not hasattr(os, 'geteuid') or os.geteuid() != 0: raise Rejected('linux_supervisor_required')
    status = process_status()
    if int(status.get('CapEff', '-1'), 16) != 0xe0 or status.get('NoNewPrivs') != '1' or status.get('Seccomp') != '2':
        raise Rejected('supervisor_privilege_profile_mismatch')
    cgroup = Path('/sys/fs/cgroup')
    memory = (cgroup / 'memory.max').read_text().strip(); swap = (cgroup / 'memory.swap.max').read_text().strip()
    pids = (cgroup / 'pids.max').read_text().strip(); cpu = (cgroup / 'cpu.max').read_text().split()
    if memory != '536870912' or swap != '0' or pids != '64' or len(cpu) != 2 or cpu[0] == 'max' or int(cpu[0]) != int(cpu[1]):
        raise Rejected('kernel_resource_limits_mismatch')
    if {path.name for path in Path('/sys/class/net').iterdir()} != {'lo'}: raise Rejected('external_network_interface_present')
    import resource
    if resource.getrlimit(resource.RLIMIT_CORE) != (0, 0) or resource.getrlimit(resource.RLIMIT_NOFILE) != (128, 128):
        raise Rejected('kernel_file_or_core_limits_mismatch')
    if os.statvfs('/tmp').f_frsize * os.statvfs('/tmp').f_blocks != 134217728 or os.statvfs('/dev/shm').f_frsize * os.statvfs('/dev/shm').f_blocks != 1048576:
        raise Rejected('kernel_tmpfs_limits_mismatch')
    probe = IMAGE_ROOT / '.fawnimo-readonly-probe'
    try:
        descriptor = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as error:
        if error.errno != errno.EROFS: raise Rejected('readonly_image_not_proven') from None
    else:
        os.close(descriptor); probe.unlink()
        raise Rejected('image_was_writable')
    return {'kernel_limits_verified': True, 'namespace_isolation_verified': True, 'supervisor_uid': 0, 'supervisor_cap_eff': status['CapEff'],
        'no_new_privileges': True, 'seccomp_filter': True, 'memory_bytes': int(memory), 'swap_bytes': 0, 'pids': int(pids), 'cpu_quota_ratio': 1,
        'interfaces': ['lo'], 'image_readonly': True}


def validate_manifest(value):
    """Only finite complete fixed-subset manifests from the parent are meaningful; paths cannot select host or supervisor files."""
    if not isinstance(value, dict) or not 1 <= len(value) <= MAX_FILES: raise Rejected('invalid_source_manifest')
    for path, entry in value.items():
        if (not checked_source_path(path) or not isinstance(entry, dict) or set(entry) != {'mode','sha'}
                or entry['mode'] != '100644' or not isinstance(entry['sha'], str) or re.fullmatch(r'[0-9a-f]{40}', entry['sha']) is None):
            raise Rejected('invalid_source_manifest_entry')
    return value


def image_files():
    """Collect actual regular image code/resources, rejecting links, extras and excessive bytes before writing the disposable workspace."""
    files = {}
    for prefix in ('backend/app', 'backend/tests', 'backend/sql'):
        directory = IMAGE_ROOT / prefix
        if directory.is_symlink(): raise Rejected('source_link_rejected')
        if not directory.exists(): continue
        for root, directories, names in os.walk(directory):
            if any((Path(root) / name).is_symlink() for name in directories): raise Rejected('source_link_rejected')
            for name in names:
                source = Path(root) / name; path = source.relative_to(IMAGE_ROOT).as_posix()
                if checked_source_path(path): files[path] = source
    for source in (IMAGE_ROOT / 'backend').glob('*.py'):
        path = source.relative_to(IMAGE_ROOT).as_posix()
        if checked_source_path(path): files[path] = source
    for path in ('backend/pyproject.toml','backend/uv.lock','backend/README.md','backend/.python-version','scripts/features.py','capabilities/features.json'):
        source = IMAGE_ROOT / path
        if source.exists() or source.is_symlink(): files[path] = source
    if not 1 <= len(files) <= MAX_FILES: raise Rejected('image_source_capacity')
    total = 0
    for source in files.values():
        metadata = source.lstat()
        if not stat.S_ISREG(metadata.st_mode) or source.resolve() != source: raise Rejected('source_link_or_device_rejected')
        if metadata.st_mode & 0o111: raise Rejected('executable_source_mode_rejected')
        total += metadata.st_size
        if total > MAX_SOURCE: raise Rejected('image_source_capacity')
    return files


def validate_input(value):
    """Check the exact server-owned framing, trusted supervisor identity and inert bundle hashes before any code preparation."""
    keys = {'profile','attempt_id','deadline_us','runner_sha256','artifact','base_manifest','candidate_manifest'}
    if not isinstance(value, dict) or set(value) != keys or value['profile'] != PROFILE: raise Rejected('invalid_check_protocol')
    if not isinstance(value['attempt_id'], str) or re.fullmatch(r'[0-9a-f-]{36}', value['attempt_id']) is None: raise Rejected('invalid_attempt_id')
    actual = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if value['runner_sha256'] != actual: raise Rejected('supervisor_source_mismatch')
    artifact = value['artifact']
    if not isinstance(artifact, dict) or set(artifact) != {'bundle_id','content_hash','branch','bundle'}: raise Rejected('invalid_bundle')
    payload = artifact['bundle']
    if (not isinstance(payload, dict) or payload.get('profile') != 'public_python_change_v1' or payload.get('schema_version') != 1
            or digest(payload) != artifact['bundle_id'] or artifact['content_hash'] != artifact['bundle_id']
            or artifact['branch'] != 'community/change-' + artifact['bundle_id'][:24]
            or any(payload.get(flag) is not False for flag in ('execution_authorized','development_complete','independent_review_completed','ci_qualified','public_deployment_verified'))
            or type(payload.get('paid_authorization')) is not int or payload['paid_authorization'] != 0
            or type(payload.get('external_model_calls')) is not int or payload['external_model_calls'] != 0):
        raise Rejected('invalid_inert_bundle_scope')
    body = payload.get('input'); scope = payload.get('scope')
    if (not isinstance(body, dict) or not isinstance(scope, dict) or digest(scope) != payload.get('scope_hash')
            or digest({'domain': 'public_python_change_v1', 'input': body}) != payload.get('intent_hash')
            or body.get('work_id') != scope.get('work_id') or body.get('test_files') != payload.get('candidate_test_files')):
        raise Rejected('original_bundle_scope_changed')
    if type(value['deadline_us']) is not int or not time.time_ns() // 1000 < value['deadline_us'] <= time.time_ns() // 1000 + 60000000:
        raise Rejected('check_deadline_expired_or_invalid')
    validate_manifest(value['base_manifest']); validate_manifest(value['candidate_manifest'])
    return payload


def prepare_workspace(value, payload):
    """Verify complete actual base code, apply only inert source replacements, then pin the exact final candidate subset and seal it readonly."""
    files = image_files()
    base = {path: {'mode': '100644', 'sha': git_blob(source.read_bytes())} for path, source in sorted(files.items())}
    if base != value['base_manifest']: raise Rejected('complete_base_image_source_mismatch')
    registry = json.loads((IMAGE_ROOT / 'capabilities/features.json').read_text('utf8'), object_pairs_hook=unique_fields,
        parse_constant=invalid_number, parse_float=finite_float)
    if digest(registry) != payload['scope'].get('feature_catalog_hash'): raise Rejected('public_feature_catalog_mismatch')
    features = {item['id']: item for item in registry['features']}; registered = set()
    for identifier in payload['scope']['feature_ids']:
        if identifier not in features: raise Rejected('unknown_registered_feature')
        registered.update(features[identifier]['code'])
    if sorted(registered) != payload['scope'].get('registered_code_paths'): raise Rejected('registered_edit_scope_mismatch')
    if WORK.exists() or WORK.is_symlink(): raise Rejected('workspace_not_fresh')
    WORK.mkdir(mode=0o755)
    for path, source in files.items():
        target = WORK / path; target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    changes = payload.get('files'); test_files = payload.get('candidate_test_files')
    if not isinstance(changes, list) or not 1 <= len(changes) <= 16 or not isinstance(test_files, list) or not 1 <= len(test_files) <= 8:
        raise Rejected('invalid_candidate_files')
    seen = set()
    for change in changes:
        if not isinstance(change, dict): raise Rejected('invalid_candidate_file')
        path = change.get('path')
        test = isinstance(path, str) and re.fullmatch(r'backend/tests/test_community_candidate_[a-z0-9_]+\.py', path) is not None
        if (not checked_source_path(path) or not path.endswith('.py') or path in seen or protected_code_path(path)
                or not (test or path in registered) or test and (change.get('before_sha256') is not None or change.get('action') != 'write')):
            raise Rejected('candidate_path_rejected')
        seen.add(path); target = WORK / path
        before = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
        if before != change.get('before_sha256'): raise Rejected('original_candidate_file_mismatch')
        if change.get('action') == 'delete':
            if before is None or change.get('text') is not None: raise Rejected('invalid_source_deletion')
            target.unlink()
        elif change.get('action') == 'write' and isinstance(change.get('text'), str):
            raw = change['text'].encode('utf8')
            if len(raw) > 131072 or b'\0' in raw or b'\r' in raw or raw and not raw.endswith(b'\n') or hashlib.sha256(raw).hexdigest() != change.get('after_sha256'):
                raise Rejected('invalid_source_replacement')
            target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)
        else: raise Rejected('invalid_source_action')
    if len(set(test_files)) != len(test_files) or any(not isinstance(path, str) or re.fullmatch(r'backend/tests/test_community_candidate_[a-z0-9_]+\.py', path) is None
            or path not in seen or not (WORK / path).is_file() for path in test_files): raise Rejected('invalid_candidate_test_profile')
    final = {}
    for root, _directories, names in os.walk(WORK):
        for name in names:
            target = Path(root) / name; path = target.relative_to(WORK).as_posix()
            final[path] = {'mode': '100644', 'sha': git_blob(target.read_bytes())}
    if final != value['candidate_manifest']: raise Rejected('complete_candidate_source_mismatch')
    for root, directories, names in os.walk(WORK, topdown=False):
        for name in names: (Path(root) / name).chmod(0o444)
        for name in directories: (Path(root) / name).chmod(0o555)
    WORK.chmod(0o555)
    return [path.removeprefix('backend/') for path in test_files]


CHILD_BOOTSTRAP = '''import os,sys,json
sys.stdin.buffer.read()
status={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in open('/proc/self/status') if ':' in line}
if os.geteuid()!=10001 or os.getegid()!=10001 or os.getgroups()!=[] or int(status['CapEff'],16)!=0 or status['NoNewPrivs']!='1':
    raise SystemExit(125)
sys.path.insert(0,os.getcwd())
import pytest
tests=json.loads(sys.argv[1])
raise SystemExit(pytest.main(['-q','-c','/dev/null','--rootdir',os.getcwd(),'--basetemp','/tmp/fawnimo-check-tmp/pytest',
    '-p','no:cacheprovider','-p','pytest_asyncio.plugin',*tests]))
'''


def emit(status, reason, exit_code, **details):
    """Emit one bounded trusted report without waiting indefinitely for a disconnected Docker logger/attach client."""
    value = {**STATE, **details, 'status': status, 'reason': reason}
    try:
        raw = (canonical(value) + '\n').encode('utf8')
        if len(raw) > 196608: raw = b'{"profile":"linux_pytest_check_v1","status":"failed","reason":"report_limit"}\n'
        os.set_blocking(1, False); deadline = time.monotonic() + .2
        while raw and time.monotonic() < deadline:
            try:
                written = os.write(1, raw); raw = raw[written:]
            except BlockingIOError: time.sleep(.001)
            except OSError: break
    finally:
        # OCI init/namespace teardown ends escaped descendants as well; no
        # candidate-owned pipe can keep interpreter finalization alive.
        os._exit(exit_code)


def absolute_timeout(_number, _frame):
    """Bound even incomplete stdin or a lost controller before any valid request can start candidate code."""
    emit('timed_out', 'supervisor_deadline', 124)


def terminate_requested(_number, _frame):
    """A daemon cancellation ends the supervisor/namespace; the host must still verify exact owned cleanup before releasing capacity."""
    emit('cancelled', 'termination_requested', 143)


def run_candidate(test_files, deadline_us):
    """Drop identity before opening a child gate, bound combined output/time, and never import candidate code in the privileged parent."""
    # Leave time for the bounded root report before the outer controller's
    # original hard deadline, even after a slow create/source preparation.
    remaining = min(45, (deadline_us - time.time_ns() // 1000) / 1000000 - 1)
    if not 0 < remaining <= 45: raise Rejected('candidate_deadline_spent')
    for path in (Path('/tmp/fawnimo-check-tmp'), Path('/tmp/fawnimo-check-home')):
        if path.exists() or path.is_symlink(): raise Rejected('candidate_temp_not_fresh')
        path.mkdir(mode=0o1777); path.chmod(0o1777)
    env = {'PATH': '/app/backend/.venv/bin:/usr/local/bin:/usr/bin:/bin', 'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1',
        'PYTHONUNBUFFERED': '1', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'JELLYFISH_SKIP_ENV_FILE': '1',
        'HOME': '/tmp/fawnimo-check-home', 'TMPDIR': '/tmp/fawnimo-check-tmp',
        'DATABASE_URL': 'sqlite+aiosqlite:////tmp/fawnimo-check-tmp/unstarted-studio.db'}
    child = subprocess.Popen(['/app/backend/.venv/bin/python', '-I', '-B', '-c', CHILD_BOOTSTRAP, canonical(test_files)],
        cwd=WORK / 'backend', env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        bufsize=0, start_new_session=True, user=10001, group=10001, extra_groups=[])
    captured = bytearray(); counts = [0]; overflow = threading.Event(); mutex = threading.Lock()

    def read_output(stream):
        """Read candidate pipes into a finite shared prefix; stdout cannot become the supervisor's result channel."""
        try:
            while True:
                part = stream.read(16384)
                if not part: break
                with mutex:
                    counts[0] = min(MAX_OUTPUT + 1, counts[0] + len(part))
                    captured.extend(part[:max(0, MAX_OUTPUT - len(captured))])
                    if counts[0] > MAX_OUTPUT: overflow.set()
        except OSError:
            overflow.set()

    readers = [threading.Thread(target=read_output, args=(stream,), daemon=True) for stream in (child.stdout, child.stderr)]
    for reader in readers: reader.start()
    # The trusted child is still behind its empty-stdin gate. Check actual
    # kernel identity, rather than reporting success solely from Popen options.
    identity = process_status(child.pid)
    if (identity.get('Uid', '').split() != ['10001'] * 4 or identity.get('Gid', '').split() != ['10001'] * 4
            or identity.get('Groups') != '' or int(identity.get('CapEff', '-1'), 16) != 0 or identity.get('NoNewPrivs') != '1'):
        try: os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        child.wait(timeout=2)
        raise Rejected('candidate_uid_or_capability_drop_not_proven')
    STATE['code_executed'] = True
    child.stdin.close()  # EOF gate opens only after root has both bounded readers.
    started = time.monotonic(); reason = 'candidate_tests_finished'; timed_out = False
    try:
        while child.poll() is None:
            if overflow.is_set(): reason = 'candidate_output_limit'; break
            if time.monotonic() - started >= remaining: reason = 'candidate_timeout'; timed_out = True; break
            time.sleep(.02)
        if child.poll() is None:
            try: os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError: pass
        child.wait(timeout=2)
        for reader in readers: reader.join(timeout=.25)
        if any(reader.is_alive() for reader in readers): reason = 'descendants_or_output_not_drained'
        if overflow.is_set(): reason = 'candidate_output_limit'
        output = bytes(captured)
        outcome = 'timed_out' if timed_out else 'passed' if child.returncode == 0 and reason == 'candidate_tests_finished' else 'failed'
        return {'outcome': outcome, 'process_exit_code': child.returncode, 'output_sha256': hashlib.sha256(output).hexdigest(),
            'output_bytes': counts[0], 'diagnostic_output_base64': base64.b64encode(output).decode('ascii'),
            'uid_separation_verified': True, 'candidate_uid': 10001, 'candidate_gid': 10001,
            'candidate_cap_eff': '0000000000000000', 'reason': reason, 'candidate_seconds': round(time.monotonic() - started, 3)}
    finally:
        if child.poll() is None:
            try: os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            child.wait(timeout=2)


def main():
    """Install an independent60-second watchdog before reading stdin; check exact code/kernel state, then run only the fixed low-UID pytest profile."""
    if sys.platform != 'linux': emit('rejected', 'linux_supervisor_required', 125)
    signal.signal(signal.SIGALRM, absolute_timeout); signal.setitimer(signal.ITIMER_REAL, 60)
    signal.signal(signal.SIGTERM, terminate_requested)
    try:
        raw = read_frame(sys.stdin.buffer)
        value = json.loads(raw.decode('utf8'), object_pairs_hook=unique_fields, parse_constant=invalid_number, parse_float=finite_float)
        payload = validate_input(value)
        STATE.update(attempt_id=value['attempt_id'], bundle_id=value['artifact']['bundle_id'], scope_hash=payload['scope_hash'],
            runner_sha256=value['runner_sha256'], base_manifest_hash=digest(value['base_manifest']), candidate_manifest_hash=digest(value['candidate_manifest']))
        STATE.update(kernel_profile())
        tests = prepare_workspace(value, payload)
        result = run_candidate(tests, value['deadline_us'])
        status = result.pop('outcome'); reason = result.pop('reason')
        emit(status, reason, 0 if status == 'passed' else 124 if status == 'timed_out' else 1, **result)
    except Rejected as error:
        emit('rejected', str(error), 125)
    except BaseException:
        emit('rejected', 'invalid_profile_or_runtime_failure', 125)


if __name__ == '__main__':
    main()
