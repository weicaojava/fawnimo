"""New owned local runtime resources on an explicitly checked local Docker engine.

No pull, build, login, host bind, ambient credential, external model request or
unknown cleanup is performed here. Each mutation is journaled by the controller.
"""
import asyncio
import hashlib
import io
import json
import re
import tarfile

from app.services.community.code_check_docker import DockerBoundaryError, environment_map
from app.services.community.runtime_release_store import RuntimeReleaseError, canonical, opaque

OWNER = 'org.fawnimo.local-runtime-owner'
OPERATION = 'org.fawnimo.local-runtime-operation'
ROLE = 'org.fawnimo.local-runtime-role'
REVISION = 'org.opencontainers.image.revision'
LOG = {'Type': 'local', 'Config': {'max-size': '1m', 'max-file': '1', 'compress': 'false'}}


class RuntimeMutationUnknown(RuntimeReleaseError):
    """An original daemon mutation may have been accepted; preserve its reservation and reconcile by reads only."""


class RuntimeHelperRefused(RuntimeReleaseError):
    """A retained stopped helper proves a bounded refusal, allowing controlled recovery of the exact previous stack."""


class RuntimeDocker:
    """Apply a separate runtime profile without weakening the existing candidate-code isolation boundary."""

    def __init__(self, boundary, owner):
        """The trusted factory supplies a local-only isolated CLI; requests cannot select engines, names or resource knobs."""
        self.cli = boundary
        self.owner = opaque(owner)
        self.image_env = {}

    def engine(self):
        """Require Linux/cgroupv2 and Docker 28+ isolated-gateway/localhost publishing behavior before resource creation."""
        self.cli.check_engine()
        info = json.loads(self.cli.command('info', '--format', '{{json .}}').stdout)
        version = info.get('ServerVersion', '')
        if not isinstance(version, str) or re.match(r'^(\d+)\.', version) is None or int(version.split('.')[0]) < 28:
            raise RuntimeReleaseError('Local runtime requires Docker Engine 28 or newer')
        identifier = info.get('ID')
        if not isinstance(identifier, str) or not 1 <= len(identifier) <= 128:
            raise RuntimeReleaseError('Exact local Docker engine identity is unavailable')
        return {'endpoint_sha256': hashlib.sha256(self.cli.endpoint.encode()).hexdigest(), 'engine_id': identifier}

    def image(self, binding):
        """Check the immutable local image, explicit revision label and installed nonroot runtime environment without pulling."""
        self.cli.image(binding.image_id)
        record = json.loads(self.cli.command('image', 'inspect', binding.image_id).stdout)[0]
        config = record['Config']
        if (config.get('User') != '10001:10001' or config.get('WorkingDir') != '/app/backend'
                or config.get('Labels', {}).get(REVISION) != binding.source_commit):
            raise RuntimeReleaseError('Image does not match its explicitly bound source and nonroot runtime')
        self.image_env[binding.image_id] = environment_map(config['Env'])
        return {'image_id': binding.image_id, 'declared_source_commit': binding.source_commit,
                'operator_binding_only': True, 'independent_review_completed': False}

    def spec(self, kind, operation, role, **details):
        """Derive opaque bounded resource names and complete ownership labels exclusively from the private original journal."""
        opaque(operation)
        if kind not in {'container', 'volume', 'network'} or re.fullmatch(r'[a-z][a-z0-9-]{0,23}', role) is None:
            raise RuntimeReleaseError('Unknown owned runtime resource role')
        return {'kind': kind, 'name': f'fawnimo-rt-{self.owner[:12]}-{operation[:24]}-{role}',
                'owner': self.owner, 'operation': operation, 'role': role, **details}

    def _check_spec(self, value):
        """Refuse names, ownership or kinds changed after the original intent before any inspect, stop or removal."""
        if not isinstance(value, dict) or value.get('owner') != self.owner:
            raise RuntimeReleaseError('Resource belongs to another local runtime journal')
        expected = self.spec(value.get('kind'), value.get('operation'), value.get('role'))
        if any(value.get(key) != item for key, item in expected.items()):
            raise RuntimeReleaseError('Original owned runtime resource identity changed')
        return value

    def labels(self, value):
        """Emit only fixed full owner/operation/role labels; shortened names alone never establish authority."""
        value = self._check_spec(value)
        return {OWNER: self.owner, OPERATION: value['operation'], ROLE: value['role']}

    def inspect(self, value, *, absent=False):
        """Find an exact existing name, reject foreign replacements and check complete runtime configuration before mutations."""
        value = self._check_spec(value)
        kind = value['kind']
        filters = ['--filter', 'name=' + ('^/' + value['name'] + '$' if kind == 'container' else '^' + value['name'] + '$')]
        arguments = [kind, 'ls'] + (['--all', '--no-trunc'] if kind == 'container' else ['--no-trunc'] if kind == 'network' else [])
        found = self.cli.command(*arguments, *filters, '--format', '{{.Names}}' if kind == 'container' else '{{.Name}}').stdout.decode('ascii').splitlines()
        if not found:
            if absent: return None
            raise RuntimeReleaseError('Original owned runtime resource is missing')
        if found != [value['name']]: raise RuntimeReleaseError('Runtime name lookup is ambiguous')
        records = json.loads(self.cli.command(kind, 'inspect', value['name']).stdout)
        if len(records) != 1: raise RuntimeReleaseError('Runtime inspect is ambiguous')
        record = records[0]
        actual_labels = record.get('Config', {}).get('Labels') if kind == 'container' else record.get('Labels')
        if (not isinstance(actual_labels, dict) or any(actual_labels.get(key) != content for key, content in self.labels(value).items())
                or record.get('Name') != ('/' + value['name'] if kind == 'container' else value['name'])
                or value.get('id') is not None and (record.get('Id') if kind != 'volume' else record.get('Name')) != value['id']):
            raise RuntimeReleaseError('Original runtime resource was replaced or ownership changed')
        if kind == 'container': self.validate_container(value, record)
        elif kind == 'volume':
            if record.get('Driver') != 'local' or record.get('Options') or record.get('Scope') != 'local':
                raise RuntimeReleaseError('Runtime volume must be an owned plain local named volume')
        else:
            options = record.get('Options') or {}
            expected = {'com.docker.network.bridge.gateway_mode_ipv4': 'isolated'} if value['internal'] else {}
            if (record.get('Driver') != 'bridge' or record.get('Scope') != 'local' or record.get('Internal') is not value['internal']
                    or record.get('EnableIPv6') is not False or options != expected):
                raise RuntimeReleaseError('Runtime network differs from its fixed isolated/private profile')
            if value.get('subnet'):
                entries=record.get('IPAM',{}).get('Config') or []
                if len(entries)!=1 or entries[0].get('Subnet')!=value['subnet']:
                    raise RuntimeReleaseError('Owned explicit network subnet changed')
        return record

    def create_network(self, value):
        """Create a new own isolated app bridge or data-free front bridge; never reuse an ambient network."""
        self._fresh(value)
        args = ['network', 'create', '--driver', 'bridge']
        if value['internal']:
            args += ['--internal', '--opt', 'com.docker.network.bridge.gateway_mode_ipv4=isolated']
        if value.get('subnet'):
            import ipaddress
            subnet=ipaddress.ip_network(value['subnet'])
            if not value['internal'] or subnet.version!=4 or not subnet.is_private or subnet.num_addresses<8 or str(subnet)!=value['subnet']:
                raise RuntimeReleaseError('An exact own engine-allocated private IPv4 subnet is required')
            args += ['--subnet',value['subnet']]
        result = self._create(value, args)
        return result

    def create_volume(self, value):
        """Create an empty owned named volume with no driver options, host mount or adopted existing data."""
        self._fresh(value)
        return self._create(value, ['volume', 'create', '--driver', 'local'])

    def _fresh(self, value):
        """Refuse any existing name; even an owned unknown-acceptance resource may only be reconciled, never blindly created again."""
        if self.inspect(value, absent=True) is not None:
            raise RuntimeReleaseError('Original resource already exists; reconcile without repeating creation')

    def _create(self, value, args):
        """Send one new-resource command; lost/error responses are unknown, even if the daemon might not have accepted them."""
        for name, content in self.labels(value).items(): args += ['--label', name + '=' + content]
        try: self.cli.command(*args, value['name'])
        except DockerBoundaryError: raise RuntimeMutationUnknown('Original runtime resource creation needs read-only reconciliation') from None
        record = self.inspect(value)
        return {**value, 'id': record.get('Id', record['Name'])}

    def container_spec(self, operation, role, image_id, *, network=None, mounts=(), command=(), front=None, origin=None, ipv4_address=None):
        """Build only a fixed app/proxy/helper configuration; no executable, resource limit or arbitrary mount comes from request JSON."""
        if image_id not in self.image_env: raise RuntimeReleaseError('Image must be explicitly checked before container creation')
        if role not in {'app', 'proxy'} and not role.startswith('helper-'):
            raise RuntimeReleaseError('Unknown fixed container role')
        value = self.spec('container', operation, role, image_id=image_id, network=network,
                          mounts=list(mounts), command=list(command), front=front, origin=origin, ipv4_address=ipv4_address)
        return value

    def create_container(self, value):
        """Create a nonroot capability-free finite workload, with only owned named volumes and its own fixed network."""
        self._fresh(value)
        role = value['role']
        memory = 4294967296 if role == 'app' else 536870912
        pids = 256 if role == 'app' else 64
        args = ['container', 'create', '--pull', 'never', '--name', value['name'], '--interactive',
                '--restart', 'no', '--network', value['network'] or 'none', '--dns', '127.0.0.1',
                '--ipc', 'private', '--cgroupns', 'private', '--read-only', '--init', '--no-healthcheck',
                '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=268435456,mode=1777', '--shm-size', '16777216',
                '--memory', str(memory), '--memory-swap', str(memory), '--cpus', '2' if role == 'app' else '1',
                '--pids-limit', str(pids), '--ulimit', 'core=0:0', '--ulimit', 'nofile=256:256',
                '--user', '10001:10001', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
                '--log-driver', 'local', '--log-opt', 'max-size=1m', '--log-opt', 'max-file=1', '--log-opt', 'compress=false',
                '--entrypoint', '/app/backend/.venv/bin/python', '--workdir', '/app/backend',
                '--env', 'PYTHONUTF8=1', '--env', 'JELLYFISH_SKIP_ENV_FILE=1']
        if role == 'proxy':
            from urllib.parse import urlsplit
            origin = urlsplit(value['origin'])
            args += ['--publish', f'{origin.hostname}:{origin.port}:8000/tcp',
                     '--sysctl', 'net.ipv4.ip_forward=0', '--sysctl', 'net.ipv6.conf.all.forwarding=0']
        if value.get('ipv4_address'):
            args += ['--ip', value['ipv4_address']]
        for mount in value['mounts']:
            self.inspect(mount['volume'])
            if mount['destination'] not in {'/data', '/bootstrap', '/source', '/target', '/current'} or type(mount['readonly']) is not bool:
                raise RuntimeReleaseError('Unknown fixed runtime mount')
            args += ['--mount', 'type=volume,src=' + mount['volume']['name'] + ',dst=' + mount['destination'] + (',readonly' if mount['readonly'] else '')]
        for name, content in self.labels(value).items(): args += ['--label', name + '=' + content]
        try: self.cli.command(*args, value['image_id'], *value['command'])
        except DockerBoundaryError: raise RuntimeMutationUnknown('Original container creation needs read-only reconciliation') from None
        record = self.inspect(value)
        return {**value, 'id': record['Id']}

    def validate_container(self, value, record):
        """Verify the daemon actually applied every ownership, privilege, environment, mount, limit and publication constraint."""
        try:
            config, host = record['Config'], record['HostConfig']
            role = value['role']
            actual_env = dict(item.split('=', 1) for item in config['Env'])
            if (len(actual_env) != len(config['Env']) or actual_env != {**self.image_env[value['image_id']], 'PYTHONUTF8': '1', 'JELLYFISH_SKIP_ENV_FILE': '1'}
                    or record['Image'] != value['image_id'] or config['User'] != '10001:10001'
                    or config['WorkingDir'] != '/app/backend' or config['Entrypoint'] != ['/app/backend/.venv/bin/python']
                    or config['Cmd'] != value['command'] or config.get('Tty') is not False or config.get('OpenStdin') is not True):
                raise ValueError()
            if (host.get('ReadonlyRootfs') is not True or host.get('Privileged') is not False or host.get('Init') is not True
                    or {item.removeprefix('CAP_').upper() for item in host.get('CapDrop') or []} != {'ALL'} or host.get('CapAdd')
                    or set(host.get('SecurityOpt') or []) != {'no-new-privileges:true'}
                    or host.get('NetworkMode') != (value['network'] or 'none') or host.get('IpcMode') != 'private' or host.get('CgroupnsMode') != 'private'
                    or host.get('PidMode') not in {'', None} or host.get('UTSMode') not in {'', None}
                    or any(host.get(key) for key in ('Binds', 'VolumesFrom', 'Devices', 'DeviceRequests', 'ExtraHosts', 'Links'))
                    or host.get('PublishAllPorts') is not False or host.get('OomKillDisable') is True
                    or host.get('Memory') != (4294967296 if role == 'app' else 536870912) or host.get('MemorySwap') != host['Memory']
                    or host.get('NanoCpus') != (2000000000 if role == 'app' else 1000000000)
                    or host.get('PidsLimit') != (256 if role == 'app' else 64) or host.get('ShmSize') != 16777216
                    or host.get('Dns') != ['127.0.0.1'] or host.get('RestartPolicy', {}).get('Name') != 'no'
                    or host.get('LogConfig') != LOG or config.get('Healthcheck', {}).get('Test') != ['NONE']): raise ValueError()
            tmp = host.get('Tmpfs') or {}
            if set(tmp) != {'/tmp'} or set(tmp['/tmp'].split(',')) != {'rw', 'noexec', 'nosuid', 'nodev', 'size=268435456', 'mode=1777'}: raise ValueError()
            limits = {item['Name']: (item['Soft'], item['Hard']) for item in host.get('Ulimits') or []}
            if limits != {'core': (0, 0), 'nofile': (256, 256)}: raise ValueError()
            expected_mounts = {(mount['destination'], mount['volume']['name'], not mount['readonly']) for mount in value['mounts']}
            actual_mounts = set()
            for mount in record.get('Mounts', []):
                if mount.get('Type') == 'tmpfs' and mount.get('Destination') == '/tmp': continue
                if mount.get('Type') != 'volume' or mount.get('Driver') != 'local': raise ValueError()
                actual_mounts.add((mount['Destination'], mount['Name'], mount['RW']))
            if actual_mounts != expected_mounts or len(actual_mounts) != len(value['mounts']): raise ValueError()
            if role == 'proxy':
                from urllib.parse import urlsplit
                origin = urlsplit(value['origin'])
                expected_ports = {'8000/tcp': [{'HostIp': origin.hostname, 'HostPort': str(origin.port)}]}
                if host.get('PortBindings') != expected_ports or host.get('Sysctls') != {'net.ipv4.ip_forward': '0', 'net.ipv6.conf.all.forwarding': '0'} or value['mounts']:
                    raise ValueError()
            elif host.get('PortBindings') or host.get('Sysctls'): raise ValueError()
            networks = set(record.get('NetworkSettings', {}).get('Networks') or {})
            expected_networks = {value['network'] or 'none'}
            if value.get('front_connected'): expected_networks.add(value['front'])
            if networks != expected_networks: raise ValueError()
            if value.get('ipv4_address') and record['NetworkSettings']['Networks'][value['network']].get('IPAMConfig', {}).get('IPv4Address') != value['ipv4_address']:
                raise ValueError()
        except (ValueError, TypeError, KeyError, AttributeError):
            raise RuntimeReleaseError('Actual container differs from the fixed local runtime profile') from None

    def connect_front(self, value):
        """Connect only the data-free proxy to its own front bridge; apps never receive a second network or external route."""
        if value['role'] != 'proxy' or value.get('front_connected') or not value.get('front'):
            raise RuntimeReleaseError('Only a new data-free proxy can connect its own front network')
        self.inspect(value)
        try: self.cli.command('network', 'connect', value['front'], value['id'])
        except DockerBoundaryError: raise RuntimeMutationUnknown('Original proxy network attachment needs read-only reconciliation') from None
        value = {**value, 'front_connected': True}
        self.inspect(value)
        return value

    def start(self, value):
        """Start an exact checked app/proxy once; a missing/lost acknowledgment never triggers an automatic second start."""
        record = self.inspect(value)
        if record['State']['Running'] or record['State'].get('Restarting') or record['State'].get('Paused'):
            raise RuntimeReleaseError('Original runtime container is already active or needs attention')
        try: self.cli.command('container', 'start', value['id'])
        except DockerBoundaryError: raise RuntimeMutationUnknown('Original runtime start needs read-only reconciliation') from None
        observed = self.inspect(value)
        if observed['State'].get('Running') is False and observed['State'].get('Pid') == 0:
            raise RuntimeHelperRefused('Selected local workload exited before readiness')
        return value

    def stopped(self, value):
        """Require a fully stopped exact container before any offline data inspection, snapshot, restore or helper removal."""
        record = self.inspect(value)
        state = record['State']
        if state.get('Running') is not False or state.get('Pid') != 0 or state.get('Restarting') or state.get('Paused'):
            raise RuntimeReleaseError('Original runtime container has not stopped')
        return record

    def stop(self, value):
        """Drain/stop only a completely checked original container; data volumes and unrelated resources are retained."""
        record = self.inspect(value)
        if record['State']['Running']:
            try: self.cli.command('container', 'stop', '--time', '10', value['id'])
            except DockerBoundaryError: raise RuntimeMutationUnknown('Original runtime stop needs read-only reconciliation') from None
        self.stopped(value)
        return value

    def remove_helper(self, value):
        """Remove only a known stopped helper after its durable result has been recorded by the host journal."""
        if not value['role'].startswith('helper-'): raise RuntimeReleaseError('Only acknowledged temporary helpers can be removed')
        self.stopped(value)
        try: self.cli.command('container', 'rm', value['id'])
        except DockerBoundaryError: raise RuntimeMutationUnknown('Original helper cleanup needs read-only reconciliation') from None
        if self.inspect(value, absent=True) is not None: raise RuntimeMutationUnknown('Original helper remains after cleanup')
        return {'helper_removed': True}

    def helper(self, value, frame):
        """Attach a bounded frame once, then verify retained result bytes and stopped state; never accept stdout alone as completion."""
        if not value['role'].startswith('helper-'): raise RuntimeReleaseError('Only a trusted helper accepts a private stdin frame')
        self.inspect(value)
        try: raw, exit_code = asyncio.run(self.cli.attach(value['id'], canonical(frame).encode(), timeout=135))
        except Exception: raise RuntimeMutationUnknown('Original helper attachment outcome needs read-only reconciliation') from None
        record = self.stopped(value)
        result = self.helper_result(value, frame['action'])
        if len(raw) > 16384 or json.loads(raw) != result or exit_code != record['State']['ExitCode']:
            raise RuntimeMutationUnknown('Original helper output does not match its durable stopped result')
        if result.get('ok') is not True or exit_code != 0:
            raise RuntimeHelperRefused('Trusted local helper refused; retain its outcome and data for recovery')
        return result['result']

    def helper_result(self, value, action):
        """Read one small regular JSON file from the exact stopped helper via bounded in-memory tar parsing, never host extraction."""
        self.stopped(value)
        location = '/target/result.json'
        try:result = self._read_private_json(value, location, 'result.json', 16384)
        except DockerBoundaryError:
            # Missing/crashed result storage does not invalidate an already
            # verified container identity. Observation must still allow a
            # separately requested exact owned stop, without retrying work.
            raise RuntimeReleaseError('Original helper durable result is unavailable; retain its original outcome') from None
        if (not isinstance(result, dict) or result.get('profile') != 'local_runtime_helper_v1' or result.get('operation_id') != value['operation'] or result.get('action') != action
                or type(result.get('external_model_calls')) is not int or result['external_model_calls'] != 0
                or type(result.get('paid_authorization')) is not int or result['paid_authorization'] != 0 or type(result.get('ok')) is not bool):
            raise RuntimeReleaseError('Original private helper result is incomplete or changed')
        return result

    def _read_private_json(self, value, location, name, limit):
        """Read exactly one fixed regular small JSON entry in memory; neither tar paths nor links are ever extracted on the host."""
        self.inspect(value)
        raw = self.cli.command('container', 'cp', value['id'] + ':' + location, '-').stdout
        if len(raw) > 65536: raise RuntimeReleaseError('Private helper result exceeds its fixed bound')
        try:
            with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
                members = archive.getmembers()
                if len(members) != 1 or not members[0].isfile() or members[0].name != name or not 1 <= members[0].size <= limit:
                    raise ValueError()
                from app.services.community.events import unique_fields, invalid_number
                result = json.loads(archive.extractfile(members[0]).read(limit + 1), object_pairs_hook=unique_fields, parse_constant=invalid_number)
        except (ValueError, KeyError, tarfile.TarError, AttributeError):
            raise RuntimeReleaseError('Original private helper result is incomplete or changed') from None
        return result

    def initial_claims(self, app):
        """Read only the exact owned app's initial private account claims, without a model key, arbitrary filename or stdout serialization."""
        if app['role'] != 'app': raise RuntimeReleaseError('Initial claims require the original owned app')
        value = self._read_private_json(app, '/data/claims.json', 'claims.json', 8192)
        if not isinstance(value, dict) or set(value) != {'a', 'b'}: raise RuntimeReleaseError('Initial local claims are unavailable')
        for item in value.values():
            if (not isinstance(item, dict) or set(item) != {'user_id', 'workspace_id', 'account', 'password'}
                    or any(not isinstance(content, str) or not 1 <= len(content) <= 256 for content in item.values())):
                raise RuntimeReleaseError('Initial local claims are unavailable')
        return value

    def app_ip(self, app):
        """Read only the exact app's isolated IPv4 address; no operator-supplied upstream or arbitrary service is accepted."""
        import ipaddress
        record = self.inspect(app)
        address = record['NetworkSettings']['Networks'][app['network']]['IPAddress']
        value = ipaddress.ip_address(address)
        if value.version != 4 or not value.is_private or value.is_loopback or value.is_link_local or value.is_unspecified:
            raise RuntimeReleaseError('Owned isolated app address is unavailable')
        return address

    def verify_network_members(self, network, containers):
        """Reject foreign bridge members rather than trusting labels while another container can reach an owned app."""
        record = self.inspect(network)
        if set(record.get('Containers') or {}) != {item['id'] for item in containers}:
            raise RuntimeReleaseError('Owned runtime network contains an unknown or missing active member')
        return True

    def fixed_app_address(self, network):
        """Reserve an address only inside this newly owned isolated bridge so previous-stack recovery keeps the proxy target stable."""
        import ipaddress
        if not network.get('subnet'):raise RuntimeReleaseError('Static app addresses require an explicitly configured owned subnet')
        subnet=ipaddress.ip_network(self.allocated_subnet(network))
        return str(subnet.network_address+2)

    def allocated_subnet(self,network):
        """Use only the local engine allocator's actual new own isolated subnet, rather than selecting arbitrary host/LAN ranges."""
        import ipaddress
        record = self.inspect(network)
        entries = record.get('IPAM', {}).get('Config') or []
        if not network['internal'] or len(entries) != 1: raise RuntimeReleaseError('Owned app bridge needs one private IPv4 subnet')
        subnet = ipaddress.ip_network(entries[0]['Subnet'])
        if subnet.version != 4 or not subnet.is_private or subnet.num_addresses < 8:
            raise RuntimeReleaseError('Owned app bridge address range is unsupported')
        return str(subnet)

    def remove_address_reservation(self,value):
        """Remove only this new empty isolated address-reservation bridge before explicitly configuring the final private network."""
        if value.get('kind')!='network' or value.get('role')!='address-reservation' or value.get('internal') is not True:
            raise RuntimeReleaseError('Only an empty original address reservation can be removed')
        record=self.inspect(value)
        if record.get('Containers'):raise RuntimeReleaseError('Address reservation has an active or unknown endpoint')
        try:self.cli.command('network','rm',value['id'])
        except DockerBoundaryError:raise RuntimeMutationUnknown('Original address reservation removal needs read-only reconciliation') from None
        if self.inspect(value,absent=True) is not None:raise RuntimeMutationUnknown('Original address reservation remains after removal')
        return {'empty_address_reservation_removed':True}
