"""Verify the real single-node Compose runtime using exclusively new, labelled local fixtures.

This executable integration check builds no image, publishes nothing and never attaches
existing data. It requires a local Linux Docker engine and a caller-built image. Its
HTTPS certificates, accounts, uploads, cookies and named volumes are disposable fixtures.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import http.client
from http.cookiejar import CookieJar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import struct
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zlib

LABEL = 'org.fawnimo.container-verification'
REPO = Path(__file__).resolve().parents[1]
HOP_HEADERS = {'connection', 'transfer-encoding', 'keep-alive', 'proxy-authenticate',
               'proxy-authorization', 'te', 'trailer', 'upgrade'}
SOUND_CASES = (
    'tests/test_sound_music.py::test_real_looped_music_ducking_samples_and_immutable_history[24000-1]',
    'tests/test_sound_music.py::test_real_looped_music_ducking_samples_and_immutable_history[48000-2]',
    'tests/test_sound_timeline.py::test_silent_picture_requires_separate_explicit_dub_and_old_v1_stays_blocked',
    'tests/test_sound_timeline.py::test_owned_http_uses_strict_documents_and_actual_original_job',
    'tests/test_sound_timeline.py::test_actual_sound_overlay_uses_final_crossfade_clock',
    'tests/test_sound_timeline.py::test_local_sound_children_do_not_inherit_application_credentials',
    'tests/test_demo_media_render.py::test_actual_seven_original_clips_and_frames_decode',
)


def sound_check_program():
    """Run only seven fixed authored-media cases inside the existing owned image; retain no fixture media or raw logs."""
    # The program has no user-supplied commands, paths, environment values or model connection.
    return '''import hashlib, json, os, pathlib, re, subprocess, sys, tempfile, xml.etree.ElementTree as ET
selected = ''' + json.dumps(SOUND_CASES) + '''
stage = 'prepare_private_fixture'
diagnostics = []
counts = None
try:
    with tempfile.TemporaryDirectory(prefix='fawnimo-image-sound-', dir='/tmp') as directory:
        root = pathlib.Path(directory)
        report = root / 'junit.xml'
        environment = dict(os.environ, JELLYFISH_SKIP_ENV_FILE='1', PYTHONDONTWRITEBYTECODE='1',
            PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', FAWNIMO_TEST_FFMPEG='/usr/bin/ffmpeg', TEST_MEDIA_PROBE='/usr/bin/ffmpeg')
        stage = 'run_fixed_cases'
        with (root / 'private-test.log').open('wb') as log:
            result = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                '--basetemp=' + str(root / 'temporary-tests'), '--junitxml=' + str(report), *selected],
                cwd='/app/backend', env=environment, stdout=log, stderr=subprocess.STDOUT, timeout=240)
        stage = 'read_junit'
        if not report.is_file() or report.is_symlink() or report.stat().st_size > 2097152:
            raise RuntimeError('Fixed authored-media report is missing or unbounded')
        raw = report.read_bytes()
        document = ET.fromstring(raw)
        suites = list(document.iter('testsuite'))
        counts = {name: sum(int(suite.get(name, '0')) for suite in suites)
            for name in ('tests', 'failures', 'errors', 'skipped')}
        cases = list(document.iter('testcase'))
        identities = [case.get('classname', '').replace('.', '/') + '.py::' + case.get('name', '') for case in cases]
        for identity, case in zip(identities, cases):
            for outcome in case:
                if outcome.tag not in {'failure', 'error', 'skipped'}:
                    continue
                # Classify known error families without reflecting traceback text, credentials, media or arbitrary paths.
                text = outcome.get('message', '') + ' ' + (outcome.text or '')
                code = next((name for token, name in (
                    ('Read-only file system', 'readonly_fixture_path'), ('PermissionError', 'fixture_permissions'),
                    ('ModuleNotFoundError', 'missing_runtime_dependency'), ('No such file or directory', 'missing_fixture_path'),
                    ('TimeoutExpired', 'fixture_deadline'), ('AssertionError', 'fixture_assertion'),
                    ('ValueError', 'fixture_validation')) if token in text), 'fixture_case_failed')
                locations = re.findall(r'/app/backend/[A-Za-z0-9_./-]{1,160}', text)
                locations = sorted({path for path in locations if '..' not in path.split('/')})[:3]
                diagnostics.append(dict(case=identity if identity in selected else 'unresolved_fixed_case',
                    outcome=outcome.tag, code=code, code_locations=locations))
        stage = 'validate_fixed_cases'
        if result.returncode != 0:
            raise RuntimeError('Fixed authored-media cases did not complete successfully')
        if counts != dict(tests=7, failures=0, errors=0, skipped=0) or len(identities) != len(set(identities)) or set(identities) != set(selected):
            raise RuntimeError('Fixed authored-media case identities/counts changed')
        if any(list(case) for case in cases):
            raise RuntimeError('Skipped/failed or unexpected case details are not successful qualification')
        value = dict(profile='image_sound_software_v1', **counts, cases=sorted(identities),
            junit_sha256=hashlib.sha256(raw).hexdigest(), uid=os.getuid(),
            readonly_code=bool(os.statvfs('/app').f_flag & os.ST_RDONLY), external_model_calls=0,
            model_quality_qualified=False, complete_film_qualified=False, public_deployment_verified=False)
    print(json.dumps(value, separators=(',', ':')))
except Exception as error:
    # A handled negative result is delivered successfully; the host stores it and explicitly refuses qualification.
    print(json.dumps(dict(profile='image_sound_software_v1', status='failed', stage=stage,
        error_type=type(error).__name__, counts=counts, diagnostics=diagnostics[:16])))
'''


def validate_sound_result(value):
    """Reject changed, missing, repeated or skipped cases; a successful process alone cannot qualify media exports."""
    expected = {'profile', 'tests', 'failures', 'errors', 'skipped', 'cases', 'junit_sha256', 'uid',
                'readonly_code', 'external_model_calls', 'model_quality_qualified', 'complete_film_qualified', 'public_deployment_verified'}
    if not isinstance(value, dict) or set(value) != expected or value['profile'] != 'image_sound_software_v1':
        raise RuntimeError('Invalid actual-image sound result')
    counts = {'tests': 7, 'failures': 0, 'errors': 0, 'skipped': 0, 'uid': 10001, 'external_model_calls': 0}
    if any(type(value[name]) is not int or value[name] != count for name, count in counts.items()):
        raise RuntimeError('Actual-image sound cases were not all successful')
    if (not isinstance(value['cases'], list) or any(not isinstance(item, str) for item in value['cases'])
            or sorted(value['cases']) != sorted(SOUND_CASES)):
        raise RuntimeError('Actual-image sound case identity changed')
    if value['readonly_code'] is not True or any(value[name] is not False for name in
            ('model_quality_qualified', 'complete_film_qualified', 'public_deployment_verified')):
        raise RuntimeError('Actual-image sound scope or runtime controls changed')
    if not isinstance(value['junit_sha256'], str) or re.fullmatch(r'[0-9a-f]{64}', value['junit_sha256']) is None:
        raise RuntimeError('Actual-image sound result lacks its bounded original report hash')
    return value


def execute(arguments, *, environment=None, data=None, timeout=180, allowed_failure=False):
    """Execute literal arguments without a shell and never reflect credentials or command output on failure."""
    result = subprocess.run(arguments, input=data, capture_output=True, env=environment, timeout=timeout)
    if result.returncode and not allowed_failure:
        raise RuntimeError('Local verification command failed: ' + arguments[0] + ' ' + arguments[1])
    return result


def check_engine():
    """Refuse remote/overridden engines before any image, container, volume or network mutation."""
    if any(os.environ.get(key) for key in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH')):
        raise ValueError('Use an explicitly selected local Docker context without environment overrides')
    contexts = json.loads(execute(['docker', 'context', 'inspect'], timeout=15).stdout)
    endpoint = contexts[0]['Endpoints']['docker']['Host']
    if not (endpoint.startswith('unix:///') or endpoint.startswith('npipe:////./pipe/')):
        raise ValueError('A local Docker socket or named pipe is required')
    info = json.loads(execute(['docker', '--host', endpoint, 'info', '--format', '{{json .}}'], timeout=20).stdout)
    if info.get('OSType') != 'linux':
        raise ValueError('The workspace image requires a Linux Docker engine')
    return {'server_version': info['ServerVersion'], 'os_type': info['OSType'], 'local_endpoint': True, 'endpoint': endpoint}


class HTTPSRelay:
    """Use a fresh loopback-only TLS origin and a trusted fixture CA without changing system trust."""

    def __init__(self, directory: Path):
        """Bind before starting Docker so the explicit expected origin is known and cannot race."""
        self.target = None
        cert, key = directory / 'certificate.pem', directory / 'key.pem'
        execute(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                 '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1',
                 '-keyout', str(key), '-out', str(cert)], timeout=30)
        relay = self

        class Handler(BaseHTTPRequestHandler):
            """Forward bounded fixture requests to the container's loopback-published gateway only."""

            def log_message(self, *args):
                """Keep request cookies and fixture contents out of logs and reports."""

            def forward(self):
                """Preserve the public origin and response cookie attributes without following redirects."""
                length = int(self.headers.get('Content-Length', '0'))
                if length < 0 or length > 1024 * 1024 or relay.target is None:
                    self.send_error(503)
                    return
                body = self.rfile.read(length)
                connection = http.client.HTTPConnection('127.0.0.1', relay.target, timeout=120)
                try:
                    headers = {key: value for key, value in self.headers.items() if key.lower() not in HOP_HEADERS}
                    connection.request(self.command, self.path, body=body, headers=headers)
                    response = connection.getresponse()
                    content = response.read(8 * 1024 * 1024 + 1)
                    if len(content) > 8 * 1024 * 1024:
                        raise ValueError('Fixture response exceeds verification bound')
                    self.send_response(response.status)
                    for name, value in response.getheaders():
                        if name.lower() not in HOP_HEADERS | {'content-length'}:
                            self.send_header(name, value)
                    self.send_header('Content-Length', str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                finally:
                    connection.close()

            do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = forward

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=cert, keyfile=key)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.origin = 'https://localhost:' + str(self.server.server_port)
        self.context = ssl.create_default_context(cafile=str(cert))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        """Close only this verifier's server; no system certificates or proxy settings were installed."""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class Client:
    """Maintain one real HTTPS cookie jar per authored account; do not disable certificate verification."""

    def __init__(self, relay: HTTPSRelay):
        """Give distinct clients distinct session jars even when origin hostnames match."""
        self.relay, self.identity = relay, None
        self.cookies = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=relay.context), urllib.request.HTTPCookieProcessor(self.cookies))

    def request(self, method, path, body=None, *, headers=None, content_type=None):
        """Send bytes/JSON and return bounded status, headers and body without logging their private values."""
        selected = {'Origin': self.relay.origin}
        if self.identity:
            selected.update({'X-Identity-Id': self.identity['user']['id'], 'X-Workspace-Id': self.identity['workspace_id']})
        if isinstance(body, dict):
            body, content_type = json.dumps(body).encode(), 'application/json'
        if content_type:
            selected['Content-Type'] = content_type
        selected.update(headers or {})
        request = urllib.request.Request(self.relay.origin + path, data=body, headers=selected, method=method)
        try:
            response = self.opener.open(request, timeout=150)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            content = response.read(8 * 1024 * 1024 + 1)
            if len(content) > 8 * 1024 * 1024:
                raise ValueError('Fixture response exceeds verification bound')
            return response.status, response.headers, content

    def data(self, method, path, body=None, **options):
        """Require successful actual HTTP before accepting an envelope as runtime evidence."""
        status, _, content = self.request(method, '/api/v1' + path, body, **options)
        if not 200 <= status < 300:
            raise RuntimeError('Container fixture API rejected ' + method + ' ' + path + ': ' + str(status))
        return json.loads(content)['data']


def png(red, green, blue):
    """Create original tiny PNG bytes using only the standard library and no copied media."""
    def chunk(kind, data):
        """Encode a PNG chunk with its actual CRC, so decoding exercises a real valid upload."""
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    rows = (b'\0' + bytes((red, green, blue)) * 16) * 16
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!IIBBBBB', 16, 16, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b'')


def upload(client: Client, project: str, content: bytes):
    """Submit a real multipart upload with an owned project and generated PNG content."""
    boundary = 'fawnimo-' + secrets.token_hex(12)
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="project_id"\r\n\r\n{project}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="usage_kind"\r\n\r\nupload\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="original.png"\r\n'
            'Content-Type: image/png\r\n\r\n').encode() + content + f'\r\n--{boundary}--\r\n'.encode()
    return client.data('POST', '/studio/files/upload', body, content_type='multipart/form-data; boundary=' + boundary)


class Runtime:
    """Own one new Compose project; clean up exclusively resources carrying its random ownership label."""

    def __init__(self, image: str, prefix: str, directory: Path, relays: dict, *, endpoint: str):
        """Reject project collisions before provisioning and keep all fixture secrets out of reports."""
        self.prefix, self.owner, self.relays = prefix, secrets.token_hex(24), relays
        self.docker_args = ['docker', '--host', endpoint]
        self.containers = {}
        self.environment = {**os.environ, 'STABLE_PORT': '0', 'PREVIEW_PORT': '0',
            'STABLE_PUBLIC_ORIGIN': relays['stable'].origin, 'PREVIEW_PUBLIC_ORIGIN': relays['preview'].origin}
        self.override = directory / 'compose-verification.json'
        self.compose_args = self.docker_args + ['compose', '--project-name', prefix, '-f', str(REPO / 'deploy/workspace/compose.yaml'),
                             '-f', str(self.override), '--profile', 'stable', '--profile', 'preview']
        self.image_id = json.loads(self.command('image', 'inspect', image, timeout=20).stdout)[0]['Id']
        # Names are checked before creation; cleanup additionally requires an unguessable per-invocation label.
        for kind in ('container', 'volume', 'network'):
            if self.list_resources(kind, 'com.docker.compose.project=' + prefix):
                raise ValueError('The verification project already has resources; select a fresh run prefix')
            # Unlabelled/pre-created names must also be refused: Compose otherwise reuses an existing volume.
            listing = ['ps', '--all', '--format', '{{.Names}}'] if kind == 'container' else [kind, 'ls', '--format', '{{.Name}}']
            if self.command(*listing, '--filter', 'name=' + prefix, timeout=20).stdout.strip():
                raise ValueError('The verification prefix already names resources; select a fresh run prefix')
        override = {'services': {side: {'image': image, 'labels': {LABEL: self.owner}} for side in relays},
                    'volumes': {side + '-data': {'labels': {LABEL: self.owner}} for side in relays},
                    'networks': {'default': {'labels': {LABEL: self.owner}}}}
        self.override.write_text(json.dumps(override), 'utf-8')

    def command(self, *arguments, timeout=180):
        """Pin every engine operation to the previously validated local endpoint, including after context changes."""
        return execute(self.docker_args + list(arguments), environment=self.environment, timeout=timeout)

    def compose(self, *arguments, timeout=180):
        """Use the shipped Compose security/runtime settings and only override fixture image/origins/ports/labels."""
        return execute(self.compose_args + list(arguments), environment=self.environment, timeout=timeout)

    def list_resources(self, kind, label):
        """Query literal labels and return exact engine identifiers without enumerating another project's contents."""
        command = ['ps', '-aq'] if kind == 'container' else [kind, 'ls', '-q']
        return self.command(*command, '--filter', 'label=' + label, timeout=20).stdout.decode().split()

    def owned(self, kind, identifier):
        """Revalidate resource ownership immediately before any stop/removal operation."""
        record = json.loads(self.command(kind, 'inspect', identifier, timeout=20).stdout)[0]
        labels = record.get('Config', {}).get('Labels', {}) if kind == 'container' else record.get('Labels', {})
        if labels.get(LABEL) != self.owner or labels.get('com.docker.compose.project') != self.prefix:
            raise RuntimeError('Refusing to mutate a resource without this invocation ownership labels')
        return record

    def provision(self, side):
        """Exercise the real offline provisioning CLI before the gateway starts on each fresh volume."""
        code = '''import json, pathlib, subprocess, sys
profiles = {}
for side in ('a', 'b'):
    path = '/data/private/container-check-' + side + '.json'
    subprocess.run([sys.executable, 'run_controlplane.py', '--root', '/data', '--provision-account',
        'container.' + side, '--credential-file', path], check=True, stdout=subprocess.DEVNULL)
    profiles[side] = json.loads(pathlib.Path(path).read_text('utf-8'))
print(json.dumps(profiles))
'''
        return json.loads(self.compose('run', '--rm', '--no-deps', '--pull', 'never', '-T', '--entrypoint', 'python', side, '-c', code).stdout)

    def start(self, *sides):
        """Start actual services without building/pulling and discover only their loopback dynamic host ports."""
        self.compose('up', '--detach', '--no-build', '--pull', 'never', *sides)
        for side in sides:
            identifier = self.compose('ps', '--all', '--quiet', side).stdout.decode().strip()
            record = self.owned('container', identifier)
            if record['Image'] != self.image_id:
                raise RuntimeError('Started container image differs from the inspected candidate')
            bindings = record['NetworkSettings']['Ports']['8000/tcp']
            if len(bindings) != 1 or bindings[0]['HostIp'] != '127.0.0.1':
                raise RuntimeError('The verification gateway must publish exclusively on loopback')
            self.relays[side].target = int(bindings[0]['HostPort'])
            self.containers[side] = identifier
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                try:
                    status, _, content = Client(self.relays[side]).request('GET', '/health')
                    if status == 200 and json.loads(content) == {'status': 'ok'}:
                        break
                except (OSError, ValueError, http.client.HTTPException, urllib.error.URLError):
                    pass
                time.sleep(1)
            else:
                raise RuntimeError('Container gateway did not become healthy: ' + side)

    def remove_container(self, side):
        """Recreate only an explicitly owned fixture container while preserving its named volume."""
        identifier = self.containers[side]
        self.owned('container', identifier)
        self.command('stop', '--time', '45', identifier, timeout=60)
        self.owned('container', identifier)
        self.command('rm', identifier, timeout=20)

    def verify_sound_exports(self, record=None):
        """Use only this invocation's current unprivileged stable image, with separate temporary media and no model calls."""
        identifier = self.containers['stable']
        observed = self.owned('container', identifier)
        if (observed['Image'] != self.image_id or observed['Config']['User'] != '10001:10001'
                or observed['HostConfig']['ReadonlyRootfs'] is not True or observed['State']['Running'] is not True):
            raise RuntimeError('Actual-image sound check requires the exact running owned read-only image')
        response = self.command('exec', '--user', '10001:10001', identifier, 'python', '-I', '-c', sound_check_program(), timeout=270)
        current = self.owned('container', identifier)
        if current['Image'] != self.image_id or current['State']['Running'] is not True:
            raise RuntimeError('Owned sound image changed during verification')
        if len(response.stdout) > 32768:
            raise RuntimeError('Actual-image sound result exceeds its report budget')
        value = json.loads(response.stdout)
        if record is not None:
            record['sound_exports'] = value
        return validate_sound_result(value)

    def close(self):
        """Remove only resources labelled by this invocation, including a partially started Compose run."""
        failures = []
        for kind in ('container', 'network', 'volume'):
            for identifier in self.list_resources(kind, LABEL + '=' + self.owner):
                try:
                    self.owned(kind, identifier)
                    if kind == 'container':
                        self.command('stop', '--time', '45', identifier, timeout=60)
                        self.owned(kind, identifier)
                    self.command(*(['rm', identifier] if kind == 'container' else [kind, 'rm', identifier]), timeout=30)
                except (RuntimeError, subprocess.SubprocessError):
                    failures.append(kind)
        if failures:
            raise RuntimeError('Verification resource cleanup requires attention: ' + ','.join(failures))
        return not any(self.list_resources(kind, LABEL + '=' + self.owner) for kind in ('container', 'network', 'volume'))


def verify(runtime: Runtime, record: dict):
    """Exercise shipped container controls, HTTPS identity/data boundaries and persistent volume reconstruction."""
    def check(name, condition):
        """Record meaningful observed assertions individually and stop immediately on an unmet invariant."""
        record['checks'].append({'name': name, 'passed': bool(condition)})
        if not condition:
            raise AssertionError(name)

    profiles = {side: runtime.provision(side) for side in ('stable', 'preview')}
    check('offline_cli_provisions_two_accounts_in_each_separate_volume', all(set(value) == {'a', 'b'} for value in profiles.values()))
    runtime.start('stable', 'preview')
    mounted = {}
    for side in ('stable', 'preview'):
        inspected = runtime.owned('container', runtime.containers[side])
        host = inspected['HostConfig']
        check(side + '_runtime_controls', inspected['Config']['User'] == '10001:10001' and host['ReadonlyRootfs']
              and host['CapDrop'] == ['ALL'] and 'no-new-privileges:true' in host['SecurityOpt']
              and host['PidsLimit'] == 256 and host['NanoCpus'] == 2_000_000_000 and host['Memory'] == 4 * 1024 ** 3
              and host['Init'] and {'rw', 'noexec', 'nosuid', 'size=268435456'} <= set(host['Tmpfs']['/tmp'].split(',')))
        check(side + '_restart_health_and_bounded_logs', host['RestartPolicy']['Name'] == 'unless-stopped'
              and inspected['Config']['Healthcheck']['Test'][0] == 'CMD'
              and host['LogConfig']['Type'] == 'json-file'
              and host['LogConfig']['Config'] == {'max-file': '3', 'max-size': '10m'})
        mounts = inspected['Mounts']
        persistent = [mount for mount in mounts if mount['Type'] != 'tmpfs']
        check(side + '_only_private_named_data_volume', len(persistent) == 1 and persistent[0]['Type'] == 'volume'
              and persistent[0]['Destination'] == '/data' and persistent[0]['RW']
              and all(mount['Destination'] == '/tmp' for mount in mounts if mount['Type'] == 'tmpfs'))
        mounted[side] = persistent[0]['Name']
        code = '''import json, os, pathlib, subprocess
readonly = False
try:
    pathlib.Path('/app/container-write-must-fail').write_text('forbidden')
except OSError:
    readonly = True
pathlib.Path('/tmp/container-write-check').write_text('temporary')
print(json.dumps({'uid': os.getuid(), 'readonly': readonly and bool(os.statvfs('/app').f_flag & os.ST_RDONLY),
    'ffmpeg': subprocess.run(['/usr/bin/ffmpeg','-version'],capture_output=True).returncode == 0}))
'''
        observed = json.loads(runtime.command('exec', runtime.containers[side], 'python', '-c', code).stdout)
        check(side + '_actual_unprivileged_readonly_code_tmp_and_ffmpeg', observed == {'uid': 10001, 'readonly': True, 'ffmpeg': True})
    check('stable_and_preview_data_volumes_differ', mounted['stable'] != mounted['preview'])
    verify_http_workflows(runtime, profiles, check)
    record['sound_exports'] = runtime.verify_sound_exports(record)
    record['runtime_verified'] = True


def verify_http_workflows(runtime: Runtime, profiles: dict, check):
    """Exercise identity/data flows independently of runtime inspection; only the caller can qualify containers."""
    clients = {(stage, side): Client(runtime.relays[stage]) for stage in ('stable', 'preview') for side in ('a', 'b')}
    for (stage, side), client in clients.items():
        check(stage + '_' + side + '_anonymous_api_denied', client.request('GET', '/api/v1/studio/projects')[0] == 401)
        login = profiles[stage][side]
        status, headers, body = client.request('POST', '/api/v1/identity/login', {'account': login['account'], 'password': login['password']})
        check(stage + '_' + side + '_https_login_secure_cookie', status == 200 and all(value in headers.get('Set-Cookie', '').lower()
              for value in ('httponly', 'secure', 'samesite=strict')))
        client.identity = json.loads(body)['data']
        check(stage + '_' + side + '_real_cookie_session_and_empty_projects', client.data('GET', '/identity/me')['workspace_id'] == login['workspace_id']
              and client.data('GET', '/studio/projects')['pagination']['total'] == 0)
    a, b, preview = clients['stable', 'a'], clients['stable', 'b'], clients['preview', 'a']
    html = a.request('GET', '/projects')
    check('built_spa_and_same_origin_runtime_config', html[0] == 200 and b'<div id="root">' in html[2]
          and b"BACKEND_URL: ''" in a.request('GET', '/env.js')[2])
    for client, side in ((a, 'a'), (b, 'b')):
        client.data('POST', '/studio/projects', {'id': 'same-project', 'name': 'container-' + side, 'style': '真人都市', 'visual_style': '现实'})
        client.data('PUT', '/video-runtime/projects/same-project/budget', {'expected_revision': 0, 'limit_minor': 1000 if side == 'a' else 2000})
    a.data('POST', '/studio/projects', {'id': 'only-a-project', 'name': 'only-a', 'style': '真人都市', 'visual_style': '现实'})
    check('same_project_ids_are_scoped_to_independent_accounts', a.data('GET', '/studio/projects/same-project')['name'] == 'container-a'
          and b.data('GET', '/studio/projects/same-project')['name'] == 'container-b')
    check('foreign_project_and_forged_workspace_denied', b.request('GET', '/api/v1/studio/projects/only-a-project')[0] == 404
          and a.request('GET', '/api/v1/studio/projects', headers={'X-Workspace-Id': b.identity['workspace_id']})[0] == 409)
    check('project_budget_is_per_project_in_each_account', a.data('GET', '/video-runtime/projects/same-project/budget')['limit_minor'] == 1000
          and b.data('GET', '/video-runtime/projects/same-project/budget')['limit_minor'] == 2000
          and a.request('PUT', '/api/v1/video-runtime/workspace-budget', {})[0] == 410)
    check('cross_origin_write_denied', a.request('POST', '/api/v1/studio/projects', {}, headers={'Origin': 'https://foreign.invalid'})[0] == 403)
    content = png(170, 30, 80)
    file = upload(a, 'same-project', content)
    route = '/api/v1/studio/files/' + file['id'] + '/download'
    status, headers, downloaded = a.request('GET', route)
    ranged = a.request('GET', route, headers={'Range': 'bytes=2-9'})
    check('uploaded_original_png_download_and_byte_range', status == 200 and downloaded == content and ranged[0] == 206
          and ranged[2] == content[2:10] and headers.get('Cache-Control') == 'no-store')
    check('foreign_media_denied', b.request('GET', route)[0] == 404)
    cookie = next(cookie.value for cookie in a.cookies if cookie.name == 'studio_session')
    foreign = Client(runtime.relays['preview'])
    check('stable_cookie_is_not_a_preview_session', foreign.request('GET', '/api/v1/identity/me', headers={'Cookie': 'studio_session=' + cookie})[0] == 401)
    check('preview_data_is_independent', preview.data('GET', '/studio/projects')['pagination']['total'] == 0)
    preview.data('POST', '/studio/projects', {'id': 'preview-only', 'name': 'independent-preview', 'style': '真人都市', 'visual_style': '现实'})
    demo = a.data('POST', '/identity/demo', {'request_key': 'container-demo-0001'})
    check('editable_original_demo_created', demo['shot_count'] == 7 and demo['editable_prompt_count'] == 21
          and '未使用用户原片' in a.data('GET', '/studio/chapters/' + demo['chapter_id'])['raw_text'])
    a.data('PATCH', '/studio/projects/' + demo['project_id'], {'name': 'preserved-demo-edit'})
    replay = a.data('POST', '/identity/demo', {'request_key': 'container-demo-0001'})
    reset = a.data('POST', '/identity/demo', {'request_key': 'container-demo-0002'})
    check('demo_replay_and_reset_preserve_original_edit', replay['project_id'] == demo['project_id'] and replay['replayed']
          and reset['project_id'] != demo['project_id'] and a.data('GET', '/studio/projects/' + demo['project_id'])['name'] == 'preserved-demo-edit')
    paid = ['/agent-execution/agents/a/runs', '/agent-execution/models/m/connection-tests',
            '/script-preparation/projects/p/chapters/c/candidates', '/asset-image-generation/assets/actor/a/slots/s/jobs',
            '/video-understanding/projects/p/jobs', '/execution/projects/p/submissions']
    check('all_six_paid_admissions_stay_closed', all(a.request('POST', '/api/v1' + path, {})[0] == 503 for path in paid))
    previous = runtime.containers['stable']
    runtime.remove_container('stable')
    check('preview_remains_available_while_stable_is_stopped', preview.data('GET', '/studio/projects/preview-only')['name'] == 'independent-preview')
    runtime.start('stable')
    check('stable_container_actually_recreated', runtime.containers['stable'] != previous)
    check('original_sessions_and_projects_survive_recreation', a.data('GET', '/identity/me')['workspace_id'] == profiles['stable']['a']['workspace_id']
          and a.data('GET', '/studio/projects/same-project')['name'] == 'container-a' and b.data('GET', '/studio/projects/same-project')['name'] == 'container-b')
    check('uploaded_bytes_and_budgets_survive_recreation', a.request('GET', route)[2] == content
          and a.data('GET', '/video-runtime/projects/same-project/budget')['limit_minor'] == 1000
          and b.data('GET', '/video-runtime/projects/same-project/budget')['limit_minor'] == 2000)
    check('demo_receipt_and_edit_survive_recreation', a.data('POST', '/identity/demo', {'request_key': 'container-demo-0001'})['project_id'] == demo['project_id']
          and a.data('GET', '/studio/projects/' + demo['project_id'])['name'] == 'preserved-demo-edit')
    check('foreign_media_remains_denied_after_recreation', b.request('GET', route)[0] == 404)


def main():
    """Report observed container integration only; do not grant hosting, model quality or browser acceptance."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--run-prefix', required=True)
    parser.add_argument('--report', type=Path, required=True)
    arguments = parser.parse_args()
    if not re.fullmatch(r'fawnimo-check-[a-z0-9][a-z0-9-]{5,45}', arguments.run_prefix):
        parser.error('Use a fresh fawnimo-check- prefix with 6..46 lowercase letters/digits/hyphens')
    if not re.fullmatch(r'[a-z0-9][a-z0-9./_:@-]{1,200}', arguments.image) or arguments.image.startswith('-'):
        parser.error('A literal local candidate image name or digest is required')
    if arguments.report.exists():
        parser.error('Select a new report file; existing evidence is never overwritten')
    record = {'schema_version': 1, 'at_utc': datetime.now(timezone.utc).isoformat(), 'run_prefix': arguments.run_prefix,
              'checks': [], 'runtime_verified': False, 'cleanup_verified': False, 'external_model_calls': 0,
              'public_deployment_verified': False, 'browser_acceptance_verified': False,
              'limits': ['Authored accounts/media and private loopback HTTPS only; no public hosting, browser UI, real models, full film or backup restore qualification.']}
    runtime = None
    try:
        engine = check_engine()
        record['engine'] = {key: value for key, value in engine.items() if key != 'endpoint'}
        with tempfile.TemporaryDirectory(prefix='fawnimo-container-check-') as temporary, ExitStack() as stack:
            directory = Path(temporary)
            relays = {}
            for side in ('stable', 'preview'):
                own_directory = directory / side
                own_directory.mkdir()
                relays[side] = HTTPSRelay(own_directory)
                stack.callback(relays[side].close)
            runtime = Runtime(arguments.image, arguments.run_prefix, directory, relays, endpoint=engine['endpoint'])
            record['image_id'] = runtime.image_id
            try:
                verify(runtime, record)
            finally:
                record['cleanup_verified'] = runtime.close()
        record['status'] = 'passed' if record['runtime_verified'] and record['cleanup_verified'] else 'failed'
    except Exception as error:
        record.update(status='failed', error_type=type(error).__name__, error=str(error)[:300])
    arguments.report.parent.mkdir(parents=True, exist_ok=True)
    arguments.report.write_text(json.dumps(record, indent=2) + '\n', 'utf-8')
    print(json.dumps({'status': record['status'], 'checks_passed': sum(item['passed'] for item in record['checks']),
                      'runtime_verified': record['runtime_verified'], 'cleanup_verified': record['cleanup_verified']}))
    if record['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
