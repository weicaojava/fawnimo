"""Fixed local-daemon boundary: isolated CLI config, pinned images, finite immutable container options and exact ownership before cleanup."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

from app.core.contracts.community_code_checks import LinuxCodeCheckPolicy, CodeCheckCleanup
from app.services.community.store import CommunityStoreConflict

OWNER_LABEL = 'org.fawnimo.community-code-check'
ATTEMPT_LABEL = 'org.fawnimo.community-code-check-attempt'
ALLOWED_IMAGE_ENV = {'PATH','LANG','GPG_KEY','PYTHON_VERSION','PYTHON_SHA256','PYTHONDONTWRITEBYTECODE','PYTHONUNBUFFERED','UV_NO_CACHE','UV_PYTHON_DOWNLOADS'}


class DockerBoundaryError(CommunityStoreConflict):
    """Fail closed without echoing daemon configuration, labels, credentials, candidate output or host paths."""


def docker_environment():
    """Pass OS discovery only, never Git/model/Docker auth, proxy, API override or user-supplied custom headers."""
    allowed = {'SYSTEMROOT','WINDIR','COMSPEC','PATH','PATHEXT','TEMP','TMP','TMPDIR','LANG','LC_ALL','TZ','HOME','USERPROFILE','HOMEDRIVE','HOMEPATH'}
    return {name: value for name, value in os.environ.items() if name.upper() in allowed}


def local_endpoint(*, runner=None):
    """Read the user's selected context locally, refusing remote/overridden engines before any daemon resource mutation."""
    forbidden = ('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH','DOCKER_CONFIG','DOCKER_CUSTOM_HEADERS','DOCKER_API_VERSION')
    if any(os.environ.get(name) for name in forbidden): raise DockerBoundaryError('Select a normal local Docker context without environment overrides')
    try:
        result = (runner or subprocess.run)(['docker','context','inspect'], capture_output=True, env=docker_environment(), timeout=15)
        if result.returncode or len(result.stdout) > 1048576: raise ValueError()
        contexts = json.loads(result.stdout); endpoint = contexts[0]['Endpoints']['docker']['Host']
        if len(contexts) != 1 or not isinstance(endpoint, str) or not (endpoint.startswith('unix:///') or endpoint.startswith('npipe:////./pipe/')):
            raise ValueError()
        if any(char in endpoint for char in ('\n','\r','\x00')): raise ValueError()
        return endpoint
    except (ValueError,TypeError,KeyError,IndexError,OSError,subprocess.SubprocessError):
        raise DockerBoundaryError('A reachable local Linux Docker endpoint is required') from None


@contextmanager
def isolated_docker_cli(*, runner=None, verify_engine=True):
    """Keep personal Docker auth/proxy/header defaults out of container creation by using a fresh empty owned CLI config."""
    endpoint = local_endpoint(runner=runner)
    temporary = Path(tempfile.gettempdir()).resolve()
    directory = Path(tempfile.mkdtemp(prefix='fawnimo-code-docker-')).absolute()
    if directory.resolve().parent != temporary or not directory.name.startswith('fawnimo-code-docker-'):
        raise DockerBoundaryError('Owned Docker configuration directory is outside the expected temporary root')
    config = directory / 'config.json'
    try:
        config.write_bytes(b'{}\n')
        boundary = DockerCodeBoundary(endpoint, directory, runner=runner)
        if verify_engine: boundary.check_engine()
        yield boundary
    finally:
        # Only this exact empty file/directory are removed. No recursive cleanup,
        # personal Docker config, context, image, volume, network or system prune.
        if config.resolve() != config or directory.resolve().parent != temporary:
            raise DockerBoundaryError('Owned Docker configuration cleanup requires attention')
        config.unlink(missing_ok=True)
        directory.rmdir()


def environment_map(entries):
    """Reject duplicate or secret-bearing image/container env keys without printing their values."""
    if not isinstance(entries, list) or len(entries) > 32: raise DockerBoundaryError('Unsupported check image environment')
    values = {}
    for item in entries:
        if not isinstance(item, str) or '=' not in item or len(item) > 8192: raise DockerBoundaryError('Unsupported check image environment')
        name, value = item.split('=', 1)
        if name in values or name not in ALLOWED_IMAGE_ENV | {'PYTHONUTF8'}: raise DockerBoundaryError('Unexpected environment in isolated check image/container')
        values[name] = value
    return values


class DockerCodeBoundary:
    """Operate only literal builtins at a pinned local endpoint; trusted runner injection is fixture code, never request JSON."""

    def __init__(self, endpoint, configuration, *, runner=None):
        """The public factory supplies a validated local endpoint and a fresh empty config; embedding arguments remain trusted host configuration."""
        self.endpoint = endpoint; self.configuration = Path(configuration); self.runner = runner or subprocess.run
        self.policy = LinuxCodeCheckPolicy(); self.image_env = {}; self.deadline = None; self._last_error = None
        if not isinstance(endpoint, str) or not (endpoint.startswith('unix:///') or endpoint.startswith('npipe:////./pipe/')):
            raise DockerBoundaryError('Remote Docker endpoints are not supported')
        if self.configuration.resolve() != self.configuration.absolute() or (self.configuration / 'config.json').read_bytes() != b'{}\n':
            raise DockerBoundaryError('Isolated Docker CLI requires its own empty configuration')

    def argv(self, *arguments):
        """Pin both endpoint and empty config for every daemon command, preventing proxy/auth inheritance from the selected user context."""
        return ['docker','--config',str(self.configuration),'--host',self.endpoint,*arguments]

    def command(self, *arguments, allowed_failure=False):
        """Bound local metadata/mutations and hide raw daemon errors; callers retain unknown mutation outcomes for explicit reconciliation."""
        try:
            remaining = self.policy.docker_command_seconds if self.deadline is None else min(self.policy.docker_command_seconds, self.deadline - time.monotonic())
            if remaining <= 0: raise DockerBoundaryError('Owned Docker operation deadline reached; original outcome remains reserved')
            result = self.runner(self.argv(*arguments), capture_output=True, env=docker_environment(), timeout=remaining)
        except (OSError, subprocess.SubprocessError):
            raise DockerBoundaryError('Owned Docker operation was interrupted; reconcile the original check before retrying') from None
        if len(result.stdout) > 1048576 or len(result.stderr) > 65536: raise DockerBoundaryError('Docker response exceeds check bounds')
        if result.returncode:
            # Only trusted local diagnostics may inspect this private bounded
            # value. Exceptions/CLI/public check history never serialize it.
            self._last_error = {'operation': tuple(arguments[:2]), 'exit_code': result.returncode, 'stderr': result.stderr[:4096]}
            if not allowed_failure: raise DockerBoundaryError('Owned Docker operation failed; original outcome may need reconciliation')
        return result

    @contextmanager
    def budget(self, seconds):
        """Bound a group of metadata/cleanup calls together; uncertain resources retain their journal reservation when time is spent."""
        previous = self.deadline
        self.deadline = min(previous, time.monotonic() + seconds) if previous is not None else time.monotonic() + seconds
        try: yield
        finally: self.deadline = previous

    async def attach(self, identifier, frame, *, timeout):
        """Attach stdin/stdout once to an acknowledged CID; killing this CLI is never presented as daemon-container cleanup."""
        from app.core.owned_process import run_owned_process
        if re.fullmatch(r'[0-9a-f]{64}', identifier) is None or not isinstance(frame, bytes) or len(frame) > 12582912:
            raise DockerBoundaryError('Exact owned container and bounded supervisor frame required')
        return await run_owned_process(self.argv('container','start','--attach','--interactive',identifier), frame,
            timeout=timeout, max_stdout_bytes=196608, environment=docker_environment())

    def check_engine(self):
        """Require real Linux/cgroupv2 enforcement support rather than recording resource flags that the daemon cannot apply."""
        try:
            info = json.loads(self.command('info','--format','{{json .}}').stdout)
            if info.get('OSType') != 'linux' or str(info.get('CgroupVersion')) != '2': raise ValueError()
            if any(info.get(name) is not True for name in ('MemoryLimit','SwapLimit','PidsLimit','CpuCfsPeriod','CpuCfsQuota')): raise ValueError()
        except (ValueError,TypeError,AttributeError):
            raise DockerBoundaryError('Linux cgroupv2 memory/swap/PID/CPU enforcement is required') from None
        return {'local_endpoint': True, 'linux_engine': True, 'cgroup_version': '2'}

    def image(self, identifier):
        """Use an existing immutable local image only, rejecting baked env secrets and automatic volumes; never pull or log in."""
        if not isinstance(identifier, str) or re.fullmatch(r'sha256:[0-9a-f]{64}', identifier) is None: raise DockerBoundaryError('Immutable local image ID required')
        try:
            entries = json.loads(self.command('image','inspect',identifier).stdout)
            if len(entries) != 1 or entries[0]['Id'] != identifier or entries[0]['Config'].get('Volumes'): raise ValueError()
            self.image_env = environment_map(entries[0]['Config']['Env'])
        except (ValueError,TypeError,KeyError,IndexError):
            raise DockerBoundaryError('Check image has unsupported identity, volumes or environment') from None
        return {'image_id': identifier, 'image_checked': True}

    def ownership(self, attempt):
        """Only exact journal-generated names/private tokens/images may enter mutation arguments or cleanup filters."""
        if (not isinstance(attempt, dict) or not isinstance(attempt.get('container_name'), str)
                or re.fullmatch(r'fawnimo-code-[0-9a-f]{32}', attempt['container_name']) is None
                or not isinstance(attempt.get('owner_token'), str) or re.fullmatch(r'[0-9a-f]{64}', attempt['owner_token']) is None
                or not isinstance(attempt.get('image_id'), str) or re.fullmatch(r'sha256:[0-9a-f]{64}', attempt['image_id']) is None
                or not isinstance(attempt.get('id'), str) or re.fullmatch(r'[0-9a-f-]{36}', attempt['id']) is None):
            raise DockerBoundaryError('Exact original owned check identity required')
        return attempt

    def create(self, attempt):
        """Create one fixed resource-limited private namespace without starting it; the journal must record create_requested first."""
        value = self.ownership(attempt); policy = self.policy
        args = ['container','create','--pull','never','--interactive','--name',value['container_name'],
            '--label',OWNER_LABEL+'='+value['owner_token'],'--label',ATTEMPT_LABEL+'='+value['id'],
            '--restart','no','--network','none','--ipc','private','--cgroupns','private','--read-only',
            '--tmpfs',f'/tmp:rw,noexec,nosuid,nodev,size={policy.tmpfs_bytes},mode=1777',
            '--shm-size',str(policy.shm_bytes),'--memory',str(policy.memory_bytes),'--memory-swap',str(policy.memory_bytes),
            '--cpus','1','--pids-limit',str(policy.pids),'--ulimit','core=0:0','--ulimit',f'nofile={policy.max_open_files}:{policy.max_open_files}',
            '--user','0:0','--cap-drop','ALL','--cap-add','KILL','--cap-add','SETUID','--cap-add','SETGID',
            '--security-opt','no-new-privileges:true','--init','--no-healthcheck','--log-driver','local','--log-opt','max-size=1m','--log-opt','max-file=1',
            '--entrypoint','/usr/local/bin/python','--workdir','/tmp','--env','PYTHONUTF8=1',value['image_id'],
            '-I','-B','-S','/app/backend/community_isolated_runner.py']
        identifier = self.command(*args).stdout.decode('ascii').strip()
        if re.fullmatch(r'[0-9a-f]{64}', identifier) is None: raise DockerBoundaryError('Container creation outcome is unknown; do not create another attempt')
        return identifier

    def find_owned(self, attempt, *, expected_id=None):
        """Discover only this unpredictable owner label, and reject renamed/replaced/multiple/foreign resources before any deletion."""
        value = self.ownership(attempt)
        lines = self.command('container','ls','--all','--no-trunc','--filter','label='+OWNER_LABEL+'='+value['owner_token'],'--format','{{.ID}}').stdout.decode('ascii').splitlines()
        if not lines: return None
        if len(lines) != 1 or re.fullmatch(r'[0-9a-f]{64}', lines[0]) is None or expected_id is not None and lines[0] != expected_id:
            raise DockerBoundaryError('Original check resource identity needs attention')
        try:
            entries = json.loads(self.command('container','inspect',lines[0]).stdout); record = entries[0]
            if (len(entries) != 1 or record['Id'] != lines[0] or record['Image'] != value['image_id']
                    or record['Name'] != '/' + value['container_name'] or record['Config']['Labels'].get(OWNER_LABEL) != value['owner_token']
                    or record['Config']['Labels'].get(ATTEMPT_LABEL) != value['id']): raise ValueError()
        except (ValueError,TypeError,KeyError,IndexError):
            raise DockerBoundaryError('Cleanup refuses a resource outside original ownership') from None
        return record

    def validate_created(self, attempt, record):
        """Check actual daemon configuration before starting a candidate; a different mount/env/privilege policy is a failure."""
        policy = self.policy; value = self.ownership(attempt)
        try:
            config = record['Config']; host = record['HostConfig']
            caps = lambda entries: {item.removeprefix('CAP_').upper() for item in (entries or [])}
            if (record['Image'] != value['image_id'] or config['User'] != '0:0' or config['WorkingDir'] != '/tmp'
                    or config['Entrypoint'] != ['/usr/local/bin/python'] or config['Cmd'] != ['-I','-B','-S','/app/backend/community_isolated_runner.py']
                    or config.get('Tty') is not False or config.get('OpenStdin') is not True
                    or environment_map(config['Env']) != {**self.image_env, 'PYTHONUTF8': '1'}): raise ValueError()
            if (host.get('ReadonlyRootfs') is not True or host.get('Privileged') is not False or host.get('Init') is not True
                    or caps(host.get('CapDrop')) != {'ALL'} or caps(host.get('CapAdd')) != {'KILL','SETUID','SETGID'}
                    or set(host.get('SecurityOpt') or []) != {'no-new-privileges:true'}
                    or host.get('NetworkMode') != 'none' or host.get('IpcMode') != 'private' or host.get('CgroupnsMode') != 'private'
                    or host.get('PidMode') not in {'',None} or host.get('UTSMode') not in {'',None}
                    or any(host.get(name) for name in ('Binds','Mounts','VolumesFrom','Devices','DeviceRequests','PortBindings','ExtraHosts','Links','Sysctls'))
                    or host.get('PublishAllPorts') is not False or host.get('OomKillDisable') is True
                    or host.get('Memory') != policy.memory_bytes or host.get('MemorySwap') != policy.memory_bytes
                    or host.get('NanoCpus') != policy.nano_cpus or host.get('PidsLimit') != policy.pids or host.get('ShmSize') != policy.shm_bytes): raise ValueError()
            expected_tmp = {'rw','noexec','nosuid','nodev','size='+str(policy.tmpfs_bytes),'mode=1777'}
            tmpfs = host.get('Tmpfs') or {}
            if set(tmpfs) != {'/tmp'} or set(tmpfs['/tmp'].split(',')) != expected_tmp: raise ValueError()
            if any(item.get('Type') != 'tmpfs' or item.get('Destination') != '/tmp' for item in record.get('Mounts', [])): raise ValueError()
            limits = {item['Name']: (item['Soft'],item['Hard']) for item in (host.get('Ulimits') or [])}
            if limits.get('core') != (0,0) or limits.get('nofile') != (128,128): raise ValueError()
            if host.get('RestartPolicy', {}).get('Name') != 'no' or config.get('Healthcheck',{}).get('Test') != ['NONE']: raise ValueError()
            if host.get('LogConfig') != {'Type':'local','Config':{'max-size':'1m','max-file':'1'}}: raise ValueError()
        except (ValueError,TypeError,KeyError,AttributeError):
            raise DockerBoundaryError('Actual container does not match the fixed isolated check policy') from None
        return {'daemon_configuration_verified': True}

    def remove_owned(self, attempt, *, expected_id=None):
        """Remove only the exact verified original container and prove owner-label absence; unknown creation acceptance is resolved by the journal separately."""
        value = self.ownership(attempt); record = self.find_owned(value, expected_id=expected_id)
        identifier = record['Id'] if record is not None else expected_id
        if record is not None: self.command('container','rm','--force',record['Id'])
        if self.find_owned(value, expected_id=identifier) is not None: raise DockerBoundaryError('Owned container cleanup remains unverified')
        if identifier is not None:
            remaining = self.command('container','ls','--all','--no-trunc','--filter','id='+identifier,'--format','{{.ID}}').stdout.strip()
            if remaining: raise DockerBoundaryError('Original container ID remains after cleanup')
        return CodeCheckCleanup(value['id'], value['container_name'], value['image_id'], value['owner_token'], identifier, True, True)
