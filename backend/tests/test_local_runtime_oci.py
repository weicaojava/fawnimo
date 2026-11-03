"""Opt-in actual Linux image activation/rollback qualification on only new fixture-owned Docker resources.

No paid model, cloud resource, original application data, image publication or
independent human review is performed by these authored automated tests.
"""
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile

import httpx
import pytest

import local_runtime_bootstrap
from app.core.contracts.local_runtime_release import LocalRuntimePolicy
from app.services.community.code_check_docker import isolated_docker_cli
from app.services.community.runtime_release_docker import RuntimeDocker, RuntimeMutationUnknown
from app.services.community.runtime_release_store import RuntimeReleaseStore, operation_id
from app.services.community.runtime_releases import LocalRuntimeManager

pytestmark = pytest.mark.skipif(not os.environ.get('FAWNIMO_RUNTIME_OCI_IMAGE'), reason='Actual owned runtime qualification is explicitly opt-in')


@pytest.fixture(scope='module')
def qualification():
    """Collect public-safe check names only; fixture accounts, cookies, tokens, source data and raw Docker logs are never artifacts."""
    record = {'profile': 'local_runtime_release_oci_v1', 'checks': [], 'actual_linux_engine': True,
              'original_resources_used': False, 'external_model_calls': 0, 'paid_authorization': 0,
              'public_deployment_verified': False, 'independent_review_completed': False}
    yield record
    destination = os.environ.get('FAWNIMO_RUNTIME_OCI_REPORT')
    if destination: Path(destination).write_text(json.dumps(record, sort_keys=True, indent=2) + '\n', encoding='utf8')


def check(record, name, condition):
    """Keep each actual observation independently visible in a bounded safe report."""
    record['checks'].append({'name': name, 'passed': bool(condition)})
    assert condition, name


def credentials(docker, stack):
    """Read only the current fixture app's generated claims using one bounded regular-file tar entry; never print them."""
    app = stack['app']
    docker.inspect(app)
    raw = docker.cli.command('container', 'cp', app['id'] + ':/data/claims.json', '-').stdout
    assert len(raw) <= 65536
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
        members = archive.getmembers()
        assert len(members) == 1 and members[0].isfile() and members[0].name == 'claims.json' and members[0].size <= 8192
        return json.loads(archive.extractfile(members[0]).read(8193))


class Client:
    """Own one real browser-style cookie jar and trusted identity/workspace headers against the fixed local slot origin."""

    def __init__(self, origin, claims):
        """Authenticate only this freshly generated fixture account without inheriting host proxies."""
        self.origin = origin
        self.client = httpx.Client(base_url=origin, trust_env=False, follow_redirects=False, timeout=30)
        result = self.client.post('/api/v1/identity/login', json={'account': claims['account'], 'password': claims['password']}, headers={'Origin': origin})
        assert result.status_code == 200
        self.identity = result.json()['data']

    def request(self, method, path, body=None, **options):
        """Use the real gateway's public identity contract; no internal workspace token or readiness header is borrowed."""
        headers = {'Origin': self.origin, 'X-Identity-Id': self.identity['user']['id'], 'X-Workspace-Id': self.identity['workspace_id']}
        headers.update(options.pop('headers', {}))
        return self.client.request(method, '/api/v1' + path, json=body, headers=headers, **options)

    def data(self, method, path, body=None):
        """Require a successful actual typed business response while keeping its private body out of assertion messages/artifacts."""
        result = self.request(method, path, body)
        assert result.status_code == 200, 'Authored runtime HTTP step failed'
        return result.json()['data']

    def close(self):
        """Close this test's own network client and discard its private cookies."""
        self.client.close()


@pytest.fixture
def actual_runtime(tmp_path, qualification):
    """Use the already-running CI engine, prebuilt explicit images and one new private operator journal; no daemon/service is started."""
    images = {name: os.environ['FAWNIMO_RUNTIME_OCI_' + name] for name in ('IMAGE', 'PREVIOUS_IMAGE', 'FAULT_IMAGE')}
    current = os.environ['FAWNIMO_RUNTIME_OCI_COMMIT']
    previous = os.environ['FAWNIMO_RUNTIME_OCI_PREVIOUS_COMMIT']
    # This deliberately broken derived image is an authored negative fixture,
    # not a claim that its fixture revision is a public Git source revision.
    fault = 'f' * 40
    store = RuntimeReleaseStore(tmp_path / 'private-operator', initialize=True)
    policy = LocalRuntimePolicy(enabled=True, helper_image_id=images['IMAGE'],
        bootstrap_sha256=hashlib.sha256(Path(local_runtime_bootstrap.__file__).read_bytes()).hexdigest(),
        operator_allowed_images=({'image_id': images['IMAGE'], 'source_commit': current},
                                 {'image_id': images['PREVIOUS_IMAGE'], 'source_commit': previous},
                                 {'image_id': images['FAULT_IMAGE'], 'source_commit': fault}))
    store.set_policy(policy, expected_revision=1, request_key='enable-owned-ci-runtime')
    clients = []
    with isolated_docker_cli() as cli:
        docker = RuntimeDocker(cli, store.marker['owner'])

        @contextmanager
        def boundary(owner):
            """Reuse only this fixture's checked local endpoint and isolated empty Docker configuration."""
            assert owner == docker.owner
            with cli.budget(600): yield docker

        manager = LocalRuntimeManager(store, boundary_factory=boundary)
        try: yield store, docker, manager, images, clients
        finally:
            for client in clients: client.close()
            # Cleanup is deliberately test-only. The shipped controller keeps
            # old data/archive volumes and offers no automatic eviction/prune.
            known = {}
            with store.connect() as db: operations = [row[0] for row in db.execute('SELECT id FROM operations ORDER BY sequence')]
            cleanup_ok = True
            try:
                with cli.budget(180):
                    for identifier in operations:
                        observed, verified = manager._observe(docker, identifier)
                        if any(item.get('identity_needs_attention') for item in observed): raise ValueError('Owned fixture identity requires attention')
                        for value in verified: known[value['name']] = value
                    for value in known.values():
                        if value['kind'] == 'container':
                            docker.stop(value)
                            docker.stopped(value)
                            cli.command('container', 'rm', value['id'])
                            assert docker.inspect(value, absent=True) is None
                    for kind in ('network', 'volume'):
                        for value in known.values():
                            if value['kind'] == kind:
                                docker.inspect(value)
                                cli.command(kind, 'rm', value['id'])
                                assert docker.inspect(value, absent=True) is None
                    for kind in ('container', 'network', 'volume'):
                        args = [kind, 'ls'] + (['--all'] if kind == 'container' else [])
                        assert not cli.command(*args, '--filter', 'label=org.fawnimo.local-runtime-owner=' + docker.owner, '--format', '{{.ID}}' if kind == 'container' else '{{.Name}}').stdout.strip()
            except Exception:
                cleanup_ok = False
                raise
            finally:
                qualification['checks'].append({'name': 'actual_owned_cleanup_' + tmp_path.name, 'passed': cleanup_ok})


def activate(manager, images, key, *, slot='stable', generation=0, image='PREVIOUS_IMAGE'):
    """Issue only the strict public original activation contract against explicitly built local immutable images."""
    return manager.activate({'request_key': key, 'slot': slot, 'expected_generation': generation, 'image_id': images[image], 'reason': 'actual authored local runtime qualification'})


def test_actual_new_slots_upgrade_rollback_failure_security_and_finances(actual_runtime, qualification):
    """Exercise old/new real images, actual native workers, A/B data, current sessions, stopped archives and known failure recovery."""
    store, docker, manager, images, clients = actual_runtime
    check(qualification, 'old_image_first_activation', activate(manager, images, 'actual-runtime-stable-first')['phase'] == 'committed')
    check(qualification, 'new_image_independent_preview', activate(manager, images, 'actual-runtime-preview-first', slot='preview', image='IMAGE')['phase'] == 'committed')
    stable = store.slot('stable')['stack']
    preview = store.slot('preview')['stack']
    first_claims = credentials(docker, stable)
    private_export = store.root.parent / 'private-initial-claims'
    private_export.mkdir(mode=0o700)
    delivery = manager.export_initial_claims('stable', 1, private_export / 'initial.json')
    exported = json.loads((private_export / 'initial.json').read_bytes())
    check(qualification, 'actual_initial_claims_private_delivery', delivery['private_initial_claims_written'] and not delivery['passwords_printed'] and exported == first_claims)
    preview_claims = credentials(docker, preview)
    a = Client(stable['origin'], first_claims['a']); clients.append(a)
    b = Client(stable['origin'], first_claims['b']); clients.append(b)
    p = Client(preview['origin'], preview_claims['a']); clients.append(p)
    for client, name, ceiling in ((a, 'stable-a-old', 1000), (b, 'stable-b-old', 2000), (p, 'preview-a-only', 3000)):
        client.data('POST', '/studio/projects', {'id': 'same-project', 'name': name, 'style': '真人都市', 'visual_style': '现实'})
        client.data('PUT', '/video-runtime/projects/same-project/budget', {'expected_revision': 0, 'limit_minor': ceiling})
    check(qualification, 'actual_three_scoped_same_project_ids', a.data('GET', '/studio/projects/same-project')['name'] == 'stable-a-old'
          and b.data('GET', '/studio/projects/same-project')['name'] == 'stable-b-old' and p.data('GET', '/studio/projects/same-project')['name'] == 'preview-a-only')
    forged = a.request('GET', '/studio/projects', headers={'X-Workspace-Id': b.identity['workspace_id']})
    check(qualification, 'actual_forged_workspace_denied', forged.status_code == 409)
    old_cookie = a.client.cookies.get('studio_session')
    check(qualification, 'actual_stable_cookie_not_preview', p.request('GET', '/identity/me', headers={'Cookie': 'studio_session=' + old_cookie}).status_code == 401)
    for path in ('/agent-execution/agents/other/runs', '/execution/projects/same-project/submissions', '/video-understanding/projects/same-project/jobs'):
        check(qualification, 'actual_paid_gate_closed_' + path.split('/')[1], a.request('POST', path, {}).status_code == 503)
    second_key = 'actual-runtime-stable-upgrade'
    check(qualification, 'actual_image_upgrade_new_volume', activate(manager, images, second_key, generation=1, image='IMAGE')['phase'] == 'committed')
    second = store.slot('stable')['stack']
    check(qualification, 'actual_new_image_and_old_data_retained', second['image_id'] == images['IMAGE'] and second['data_volume']['id'] != stable['data_volume']['id']
          and docker.stopped(stable['app'])['State']['Pid'] == 0 and docker.inspect(stable['data_volume']) is not None)
    check(qualification, 'actual_sessions_projects_and_budgets_survive', a.data('GET', '/studio/projects/same-project')['name'] == 'stable-a-old'
          and b.data('GET', '/video-runtime/projects/same-project/budget')['limit_minor'] == 2000)
    a.data('PATCH', '/studio/projects/same-project', {'name': 'later-picture-edit'})
    current_a = Client(second['origin'], first_claims['a']); clients.append(current_a)
    current_a.data('PATCH', '/identity/me', {'display_name': 'Current local security profile'})
    a.data('POST', '/identity/logout', {})
    rollback = {'request_key': 'actual-runtime-old-data-rollback', 'slot': 'stable', 'expected_generation': 2,
        'snapshot_id': operation_id(second_key), 'acknowledge_retained_later_edits': True, 'reason': 'actual explicitly retained later-data test'}
    check(qualification, 'actual_rollback_fresh_volume', manager.rollback(rollback)['phase'] == 'committed')
    third = store.slot('stable')['stack']
    check(qualification, 'actual_old_picture_current_security', current_a.data('GET', '/studio/projects/same-project')['name'] == 'stable-a-old'
          and current_a.data('GET', '/identity/me')['user']['display_name'] == 'Current local security profile'
          and current_a.request('GET', '/identity/me', headers={'Cookie': 'studio_session=' + old_cookie}).status_code == 401
          and b.data('GET', '/studio/projects/same-project')['name'] == 'stable-b-old')
    check(qualification, 'actual_later_volume_stopped_and_retained', docker.stopped(second['app'])['State']['Pid'] == 0
          and docker.inspect(second['data_volume']) is not None and third['data_volume']['id'] != second['data_volume']['id'])
    check(qualification, 'actual_preview_survives_stable_switches', p.data('GET', '/studio/projects/same-project')['name'] == 'preview-a-only')
    bad = activate(manager, images, 'actual-runtime-known-fault', generation=3, image='FAULT_IMAGE')
    check(qualification, 'actual_broken_fixture_recovers_previous', bad['phase'] == 'recovered_previous' and store.slot('stable')['generation'] == 3
          and current_a.data('GET', '/studio/projects/same-project')['name'] == 'stable-a-old')
    budget = current_a.data('GET', '/video-runtime/projects/same-project/budget')
    current_a.data('PUT', '/video-runtime/projects/same-project/budget', {'expected_revision': budget['revision'], 'limit_minor': 1500})
    rejected = manager.rollback(rollback | {'request_key': 'actual-runtime-finance-rewind-refused', 'expected_generation': 3})
    check(qualification, 'actual_budget_rewind_refused', rejected['phase'] == 'recovered_previous' and store.slot('stable')['generation'] == 3
          and current_a.data('GET', '/video-runtime/projects/same-project/budget')['limit_minor'] == 1500)
    check(qualification, 'actual_no_external_app_route', docker.inspect(third['app'])['HostConfig']['PortBindings'] in ({}, None)
          and set(docker.inspect(third['app'])['NetworkSettings']['Networks']) == {third['app_network']['name']}
          and docker.inspect(third['app_network'])['Options'] == {'com.docker.network.bridge.gateway_mode_ipv4': 'isolated'})
    front_address = docker.inspect(third['proxy'])['NetworkSettings']['Networks'][third['front_network']['name']]['IPAddress']
    network_probe = (
        "import json,socket;from pathlib import Path;"
        "routes=[line.split() for line in Path('/proc/net/route').read_text().splitlines()[1:]];"
        "assert not any(row[1]=='00000000' for row in routes);"
        "connection=socket.socket();connection.settimeout(.5);"
        "result=connection.connect_ex(('" + front_address + "',8000));connection.close();"
        "assert result!=0;print(json.dumps({'default_route':False,'own_front_endpoint_reachable':False}))"
    )
    packet = docker.cli.command('container', 'exec', third['app']['id'], '/app/backend/.venv/bin/python', '-c', network_probe)
    forwarding = docker.cli.command('container', 'exec', third['proxy']['id'], '/app/backend/.venv/bin/python', '-c',
                                    "from pathlib import Path;assert Path('/proc/sys/net/ipv4/ip_forward').read_text().strip()=='0';print('forwarding-disabled')")
    check(qualification, 'actual_packet_route_and_proxy_forwarding_closed', json.loads(packet.stdout) == {'default_route': False, 'own_front_endpoint_reachable': False}
          and forwarding.stdout.strip() == b'forwarding-disabled')


def test_actual_accepted_create_loss_reconcile_quiesce_and_old_replay(actual_runtime, qualification, monkeypatch):
    """Inject loss only after the actual daemon acknowledged creation, proving reconciliation/explicit cleanup do not repeat activation."""
    store, docker, manager, images, clients = actual_runtime
    real_create = docker.create_container
    lost = []

    def lose_app(value):
        """Keep the real accepted owned CID while hiding its first response from the controller."""
        result = real_create(value)
        if value['role'] == 'app' and not lost:
            lost.append(result['id'])
            raise RuntimeMutationUnknown('Authored response loss after actual daemon acceptance')
        return result

    monkeypatch.setattr(docker, 'create_container', lose_app)
    key = 'actual-runtime-create-response-loss'
    with pytest.raises(ValueError): activate(manager, images, key, image='IMAGE')
    observed = manager.reconcile(operation_id(key))
    check(qualification, 'actual_accepted_create_observed_without_retry', len(lost) == 1 and observed['daemon_mutations_performed'] == 0
          and any(item['kind'] == 'container' and item.get('exists') for item in observed['resources']))
    cleanup = {'request_key': 'actual-runtime-explicit-quiesce', 'operation_id': operation_id(key),
               'expected_observation_sha256': observed['observation_sha256'], 'acknowledge_stopped_data_retained': True,
               'reason': 'explicit authored actual owned cleanup'}
    check(qualification, 'actual_observed_quiescence', manager.quiesce(cleanup)['phase'] == 'failed_closed')
    monkeypatch.setattr(docker, 'create_container', real_create)
    check(qualification, 'actual_new_request_after_quiescence', activate(manager, images, 'actual-runtime-after-explicit-quiesce', image='IMAGE')['phase'] == 'committed')
    generation = store.slot('stable')

    @contextmanager
    def forbidden_boundary(owner):
        """Any engine consultation during old-key replay would be a test failure before entering a daemon command."""
        raise AssertionError('Old replay must not contact Docker')
        yield

    historical = LocalRuntimeManager(store, boundary_factory=forbidden_boundary)
    check(qualification, 'actual_old_activation_key_is_history_only', activate(historical, images, key, image='IMAGE')['phase'] == 'failed_closed')
    check(qualification, 'actual_old_cleanup_key_preserves_new_generation', historical.quiesce(cleanup)['phase'] == 'failed_closed' and store.slot('stable') == generation)
