"""Durable controller tests with real private SQLite/files and an authored in-memory daemon, not actual image qualification."""
import asyncio
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path

import pytest

import local_runtime_bootstrap
from app.controlplane.store import IdentityStore
from app.controlplane.workers import WorkspaceWorkers
from app.core.contracts.local_runtime_release import LocalRuntimePolicy, LocalRuntimeActivation, LocalRuntimeStop
from app.services.community.local_release_files import read_json
from app.services.community.runtime_release_docker import RuntimeDocker, RuntimeMutationUnknown, RuntimeHelperRefused
from app.services.community.runtime_release_helper import execute
from app.services.community.runtime_release_store import RuntimeReleaseStore, RuntimeReleaseError, operation_id
from app.services.community.runtime_releases import LocalRuntimeManager

IMAGE = 'sha256:' + '1' * 64
NEXT_IMAGE = 'sha256:' + '2' * 64
COMMIT = '3' * 40
NEXT_COMMIT = '4' * 40


class AuthoredRuntime(RuntimeDocker):
    """Model daemon acceptance/loss while executing actual fixed offline helpers against only new test directories."""

    def __init__(self, owner, root):
        """Use independent fixture volumes and explicit fault flags; no local Docker process is contacted."""
        self.owner = owner
        self.root = root
        self.root.mkdir()
        self.resources = {}
        self.image_env = {}
        self.mutations = []
        self.lose_create = None
        self.refuse_probe = False

    def engine(self):
        """Return a stable fixture identity, without claiming a reachable actual engine."""
        return {'endpoint_sha256': '5' * 64, 'engine_id': 'authored-no-engine'}

    def image(self, binding):
        """Accept only explicit fixture image IDs; separate OCI tests perform actual image/label enforcement."""
        assert binding.image_id in {IMAGE, NEXT_IMAGE}
        self.image_env[binding.image_id] = {}

    def inspect(self, value, *, absent=False):
        """Find exact fixture ownership, retaining unknown accepted resources just as a daemon would."""
        self._check_spec(value)
        record = self.resources.get(value['name'])
        if record is None:
            if absent: return None
            raise RuntimeReleaseError('Missing authored fixture resource')
        if value.get('id') and record['Id'] != value['id']: raise RuntimeReleaseError('Changed fixture resource')
        return record

    def create_volume(self, value):
        """Create a fresh fixture named volume as an empty private real directory."""
        self._fresh(value)
        path = self.root / value['name']
        path.mkdir(mode=0o700)
        result = {**value, 'id': value['name']}
        self.resources[value['name']] = {'Id': value['name'], 'Name': value['name'], 'spec': result, 'path': path}
        self.mutations.append(('create', value['name']))
        return result

    def create_network(self, value):
        """Retain an isolated/front fixture network identity, without claiming actual network enforcement."""
        self._fresh(value)
        result = {**value, 'id': hashlib.sha256(value['name'].encode()).hexdigest()}
        self.resources[value['name']] = {'Id': result['id'], 'Name': value['name'], 'spec': result}
        self.mutations.append(('create', value['name']))
        return result

    def create_container(self, value):
        """Persist acceptance before an optional lost response; repeated create is then an explicit fixture error."""
        self._fresh(value)
        result = {**value, 'id': hashlib.sha256(value['name'].encode()).hexdigest()}
        self.resources[value['name']] = {'Id': result['id'], 'Name': value['name'], 'spec': result,
                                        'State': {'Running': False, 'Pid': 0, 'ExitCode': 0}}
        self.mutations.append(('create', value['name']))
        if self.lose_create == value['role']:
            self.lose_create = None
            raise RuntimeMutationUnknown('Authored accepted-create response loss')
        return result

    def connect_front(self, value):
        """Acknowledge only this proxy's own front bridge connection."""
        self.inspect(value)
        self.mutations.append(('connect', value['name']))
        result = {**value, 'front_connected': True}
        self.resources[value['name']]['spec'] = result
        return result

    def start(self, value):
        """Activate a known fixture workload, preserving its original data path and immutable identity."""
        record = self.inspect(value)
        assert not record['State']['Running']
        record['State'] = {'Running': True, 'Pid': 123, 'ExitCode': 0}
        self.mutations.append(('start', value['name']))
        return value

    def stop(self, value):
        """Quiesce only the known fixture workload and keep all private files and resource identities."""
        record = self.inspect(value)
        record['State'] = {'Running': False, 'Pid': 0, 'ExitCode': 0}
        self.mutations.append(('stop', value['name']))
        return value

    def helper(self, value, frame):
        """Run actual snapshot/restore/init code; readiness is deliberately an authored protocol result in these controller tests."""
        record = self.inspect(value)
        self.mutations.append(('helper', value['name']))
        volumes = {entry['destination']: self.resources[entry['volume']['name']]['path'] for entry in value['mounts']}
        try:
            if frame['action'] == 'probe':
                if self.refuse_probe:
                    self.refuse_probe = False
                    raise ValueError('Authored known readiness refusal')
                result = {'private_readiness_verified': True, 'fixture_only': True}
            else:
                result = execute(frame, source=volumes.get('/source'), current=volumes.get('/current'), target=volumes.get('/target'))['result']
        except Exception:
            record['State'] = {'Running': False, 'Pid': 0, 'ExitCode': 1}
            raise RuntimeHelperRefused('Authored stopped helper refusal') from None
        record['State'] = {'Running': False, 'Pid': 0, 'ExitCode': 0}
        record['helper_result'] = result
        return result

    def remove_helper(self, value):
        """Delete only a stopped acknowledged fixture helper, retaining every volume and archive."""
        self.stopped(value)
        del self.resources[value['name']]
        self.mutations.append(('remove', value['name']))
        return {'helper_removed': True}

    def helper_result(self, value, action):
        """Read the authored helper result for reconciliation tests without repeating its action."""
        return self.inspect(value).get('helper_result', {'fixture_only': True, 'action': action})

    def initial_claims(self, app):
        """Read only this known app's real generated fixture claims for private-export behavior checks."""
        self.inspect(app)
        volume = next(item['volume'] for item in app['mounts'] if item['destination'] == '/data')
        return read_json(self.resources[volume['name']]['path'] / 'claims.json')

    def app_ip(self, app):
        """Use a fixed fixture target; actual Docker tests assert the daemon's numeric address."""
        assert self.inspect(app)['State']['Running']
        return '172.23.0.2'

    def fixed_app_address(self, network):
        """Return only the authored network's fixed fixture app address."""
        self.inspect(network)
        return '172.23.0.2'

    def verify_network_members(self, network, containers):
        """Assert controller wiring among its known active fixture resources; this is not packet-level isolation proof."""
        assert all(self.inspect(item)['State']['Running'] for item in containers)
        return True


@pytest.fixture
def runtime(tmp_path):
    """Create real private operator state and new fixture volumes with no original checkout, account or daemon access."""
    store = RuntimeReleaseStore(tmp_path / 'operator', initialize=True)
    bootstrap = hashlib.sha256(Path(local_runtime_bootstrap.__file__).read_bytes()).hexdigest()
    policy = LocalRuntimePolicy(enabled=True, helper_image_id=NEXT_IMAGE, bootstrap_sha256=bootstrap,
        operator_allowed_images=({'image_id': IMAGE, 'source_commit': COMMIT}, {'image_id': NEXT_IMAGE, 'source_commit': NEXT_COMMIT}))
    store.set_policy(policy, expected_revision=1, request_key='enable-fixture-only')
    docker = AuthoredRuntime(store.marker['owner'], tmp_path / 'fixture-volumes')
    calls = []

    @contextmanager
    def boundary(owner):
        """Reuse the authored daemon's retained acceptance state across separate operator invocations."""
        assert owner == docker.owner
        calls.append(owner)
        yield docker

    manager = LocalRuntimeManager(store, boundary_factory=boundary, public_probe=lambda origin: {'fixture_only': True})
    return store, docker, manager, calls


def intent(key, generation=0, slot='stable', image=IMAGE):
    """Supply only the strict public intent fields, never fixture paths or arbitrary Docker commands."""
    return LocalRuntimeActivation(request_key=key, slot=slot, expected_generation=generation, image_id=image, reason='authored controller test')


def test_controller_new_slots_are_independent_and_old_keys_do_not_touch_engine(runtime):
    """Stable/preview each get fresh own credentials; old replay stays historical after a new generation and disabling the policy."""
    store, docker, manager, calls = runtime
    first = intent('controller-first-stable')
    assert manager.activate(first)['phase'] == 'committed'
    assert manager.activate(intent('controller-first-preview', slot='preview'))['phase'] == 'committed'
    stable = store.slot('stable')['stack']
    preview = store.slot('preview')['stack']
    a = read_json(docker.resources[stable['data_volume']['name']]['path'] / 'claims.json')
    b = read_json(docker.resources[preview['data_volume']['name']]['path'] / 'claims.json')
    assert {item['user_id'] for item in a.values()}.isdisjoint({item['user_id'] for item in b.values()})
    assert manager.activate(intent('controller-stable-second', 1, image=NEXT_IMAGE))['phase'] == 'committed'
    store.set_policy(LocalRuntimePolicy(), expected_revision=2, request_key='disable-fixture-only')
    before = (len(calls), list(docker.mutations), store.slot('stable'))
    result = manager.activate(first)
    assert result['historical_only'] and not result['fresh_action_authorized']
    assert (len(calls), docker.mutations, store.slot('stable')) == before
    # Explicit exact-generation stop remains available after activation disable.
    assert manager.stop(LocalRuntimeStop(request_key='stop-disabled-slot', slot='stable', expected_generation=2, reason='owned stop'))['phase'] == 'stopped'
    assert store.slot('preview')['state'] == 'running'
    secret = store.private_config(operation_id(first.request_key))['ready_token']
    assert secret not in json.dumps(result)


def test_accepted_create_loss_never_repeats_and_explicit_observed_quiescence_releases_slot(runtime):
    """A lost acknowledgment reserves the slot, read-only reconciliation observes acceptance, and a separate exact-state cleanup stops only owned resources."""
    store, docker, manager, calls = runtime
    docker.lose_create = 'app'
    first = intent('controller-create-loss')
    identifier = operation_id(first.request_key)
    with pytest.raises(RuntimeReleaseError): manager.activate(first)
    assert store.slot('stable')['generation'] == 0
    before = list(docker.mutations)
    assert manager.activate(first)['phase'] == 'attention'
    assert docker.mutations == before
    observed = manager.reconcile(identifier)
    assert observed['daemon_mutations_performed'] == 0 and docker.mutations == before
    assert any(item['kind'] == 'container' and item.get('exists') for item in observed['resources'])
    cleanup = {'request_key': 'quiesce-known-original', 'operation_id': identifier,
               'expected_observation_sha256': observed['observation_sha256'], 'acknowledge_stopped_data_retained': True,
               'reason': 'explicit authored stopped cleanup'}
    assert manager.quiesce(cleanup)['phase'] == 'failed_closed'
    assert manager.activate(intent('controller-after-quiesce'))['phase'] == 'committed'
    before = (len(calls), list(docker.mutations), store.slot('stable'))
    assert manager.quiesce(cleanup)['phase'] == 'failed_closed'
    assert (len(calls), docker.mutations, store.slot('stable')) == before


def test_known_new_image_readiness_refusal_recovers_exact_previous_data_and_image(runtime):
    """A known candidate refusal starts no replacement data over current files and leaves the current generation unchanged."""
    store, docker, manager, _ = runtime
    manager.activate(intent('controller-good-original'))
    previous = store.slot('stable')
    docker.refuse_probe = True
    result = manager.activate(intent('controller-refused-image', 1, image=NEXT_IMAGE))
    assert result['phase'] == 'recovered_previous'
    assert store.slot('stable') == previous
    assert docker.inspect(previous['stack']['app'])['State']['Running']
    assert docker.inspect(previous['stack']['proxy'])['State']['Running']
    names = [name for action, name in docker.mutations if action == 'create']
    assert len(names) == len(set(names)), 'Known recovery probes have their own role/name, not a repeated candidate helper'


def test_failed_activation_after_explicit_stop_does_not_restart_stopped_previous(runtime):
    """Candidate recovery preserves an operator's prior explicit stop decision."""
    store, docker, manager, _ = runtime
    manager.activate(intent('controller-stopped-first'))
    manager.stop(LocalRuntimeStop(request_key='controller-stop-first', slot='stable', expected_generation=1, reason='keep stopped'))
    previous = store.slot('stable')
    docker.refuse_probe = True
    assert manager.activate(intent('controller-refused-stopped', 1, image=NEXT_IMAGE))['phase'] == 'failed_closed'
    assert store.slot('stable') == previous
    assert not docker.inspect(previous['stack']['app'])['State']['Running']


def test_rollback_real_old_file_retains_later_data_and_current_sessions(runtime):
    """Real whole-root restore rewinds only ordinary data in a new volume and carries the latest logout/profile state forward."""
    store, docker, manager, _ = runtime
    manager.activate(intent('controller-rollback-first'))
    first = store.slot('stable')['stack']
    first_data = docker.resources[first['data_volume']['name']]['path']
    claims = read_json(first_data / 'claims.json')
    owner = claims['a']

    async def initialize_worker():
        """Use an actual owned native worker to prepare the supported private studio schema, then join it before snapshot."""
        probe = os.environ.get('TEST_MEDIA_PROBE')
        pool = WorkspaceWorkers(first_data / 'studio' / 'workspaces', probe=Path(probe) if probe else None, capacity=2)
        try: await pool.get(owner['workspace_id'])
        finally: await pool.close()

    asyncio.run(initialize_worker())
    ordinary = Path('studio') / 'workspaces' / owner['workspace_id'] / 'files' / 'authored.txt'
    (first_data / ordinary).parent.mkdir(parents=True, exist_ok=True)
    (first_data / ordinary).write_text('old-picture-bytes')
    second_intent = intent('controller-rollback-second', 1, image=NEXT_IMAGE)
    manager.activate(second_intent)
    second = store.slot('stable')['stack']
    second_data = docker.resources[second['data_volume']['name']]['path']
    (second_data / ordinary).write_text('later-picture-bytes')
    identity = IdentityStore(second_data / 'studio')
    token = identity.login(owner['account'], owner['password'], 'authored-peer')
    identity.profile(token, {'display_name': 'Current operator profile'})
    identity.logout(token)
    rollback = {'request_key': 'controller-actual-rollback', 'slot': 'stable', 'expected_generation': 2,
                'snapshot_id': operation_id(second_intent.request_key), 'acknowledge_retained_later_edits': True,
                'reason': 'explicit authored old-data restore'}
    assert manager.rollback(rollback)['phase'] == 'committed'
    third = store.slot('stable')['stack']
    third_data = docker.resources[third['data_volume']['name']]['path']
    assert third['generation'] == 3 and third['image_id'] == IMAGE
    assert (third_data / ordinary).read_text() == 'old-picture-bytes'
    assert (second_data / ordinary).read_text() == 'later-picture-bytes'
    assert third_data != second_data != first_data
    with pytest.raises(Exception): IdentityStore(third_data / 'studio').current(token)
    before = list(docker.mutations)
    assert manager.rollback(rollback)['phase'] == 'committed'
    assert docker.mutations == before


def test_quiescence_changed_observation_refuses_without_daemon_mutation(runtime):
    """A stale read receipt cannot authorize cleanup after resources changed."""
    store, docker, manager, _ = runtime
    docker.lose_create = 'app'
    first = intent('controller-stale-observation')
    with pytest.raises(RuntimeReleaseError): manager.activate(first)
    before = list(docker.mutations)
    with pytest.raises(RuntimeReleaseError): manager.quiesce({'request_key': 'quiesce-stale-original',
        'operation_id': operation_id(first.request_key), 'expected_observation_sha256': '0' * 64,
        'acknowledge_stopped_data_retained': True, 'reason': 'authored stale observation'})
    assert docker.mutations == before


def test_initial_claims_export_is_private_no_overwrite_and_exact_current_generation(runtime, tmp_path):
    """Usable account delivery writes only a new private file, without printing passwords or resetting the actual identity store."""
    store, docker, manager, _ = runtime
    manager.activate(intent('controller-private-claims'))
    private = tmp_path / 'private-export'
    private.mkdir(mode=0o700)
    target = private / 'claims.json'
    result = manager.export_initial_claims('stable', 1, target)
    value = read_json(target)
    assert result['private_initial_claims_written'] and not result['passwords_printed'] and not result['accounts_reset']
    assert all(item['password'] not in json.dumps(result) for item in value.values())
    if os.name != 'nt': assert target.stat().st_mode & 0o077 == 0
    before = target.read_bytes()
    with pytest.raises(ValueError): manager.export_initial_claims('stable', 1, target)
    with pytest.raises(ValueError): manager.export_initial_claims('stable', 0, private / 'stale.json')
    with pytest.raises(ValueError): manager.export_initial_claims('stable', 1, store.root / 'public-mixed.json')
    assert target.read_bytes() == before and not (private / 'stale.json').exists()


@pytest.mark.parametrize('existing',[False,True],ids=['empty-slot','running-slot'])
def test_failed_readonly_image_preflight_releases_slot_without_touching_current_generation(runtime,monkeypatch,existing):
    """A known read-only preflight refusal cannot trap policy repair or stop/reset a current deployment; original replay remains inert."""
    store,docker,manager,calls=runtime
    if existing:manager.activate(intent('preflight-existing-original'))
    previous=store.slot('stable');before=list(docker.mutations)
    def refused(binding):
        """Refuse the fixed image check before any daemon write, not a lost mutation acknowledgment."""
        raise RuntimeReleaseError('Authored readonly image mismatch')
    monkeypatch.setattr(docker,'image',refused)
    body=intent('preflight-refused-original',generation=previous['generation'])
    with pytest.raises(RuntimeReleaseError) as observed:manager.activate(body)
    assert store.get(operation_id(body.request_key))['phase']=='failed_closed'
    assert observed.value.diagnostic['last_requested_step']=='read_only_preflight'
    assert store.slot('stable')==previous and docker.mutations==before
    store.set_policy(LocalRuntimePolicy(),expected_revision=2,request_key='repair-preflight-policy')
    count=len(calls)
    assert manager.activate(body)['phase']=='failed_closed' and len(calls)==count
