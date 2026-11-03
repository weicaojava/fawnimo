"""Explicit default-disabled local stable/preview activation, stopped-root archives and fresh-volume rollback.

Only new resources owned by this journal can be used. A known pre-publication
candidate refusal can recover the previous exact stack; unknown daemon outcomes
retain the original reservation and permit observation only.
"""
from contextlib import contextmanager
import secrets
import time

from app.core.contracts.local_runtime_release import LocalRuntimeActivation, LocalRuntimeRollback, LocalRuntimeStop, LocalRuntimeQuiescence
from app.services.community.code_check_docker import isolated_docker_cli
from app.services.community.runtime_release_docker import RuntimeDocker, RuntimeMutationUnknown, RuntimeHelperRefused
from app.services.community.runtime_release_store import RuntimeReleaseError, operation_id, canonical, release_flags
from app.services.community.runtime_release_safety import digest


@contextmanager
def runtime_boundary(owner):
    """Create a short-lived empty Docker client configuration for one checked local engine; never start the daemon."""
    with isolated_docker_cli() as boundary:
        with boundary.budget(600): yield RuntimeDocker(boundary, owner)


def public_readiness(origin):
    """Verify the published loopback gateway and internal-route closure using fixed read-only requests without inherited proxy settings."""
    import httpx
    from local_runtime_bootstrap import READY_PATH
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=3) as client:
        deadline = time.monotonic() + 15
        for _ in range(50):
            try:
                health = client.get(origin + '/health')
                private = client.get(origin + READY_PATH, headers={'x-fawnimo-private-runtime-ready': 'not-an-app-secret'})
                if health.status_code == 200 and len(health.content) <= 4096 and health.json() == {'status': 'ok'} and private.status_code == 404:
                    return {'local_loopback_ready': True, 'private_readiness_route_blocked': True}
            except (httpx.HTTPError, ValueError): pass
            if time.monotonic() >= deadline: break
            time.sleep(.2)
    raise RuntimeReleaseError('Published local loopback gateway could not be verified')


class LocalRuntimeManager:
    """Coordinate original intents with one owned runtime boundary; injectable factories are trusted fixtures, never CLI/request fields."""

    def __init__(self, store, *, boundary_factory=runtime_boundary, public_probe=public_readiness):
        """Keep journal, daemon transport and bounded read-only readiness checks separate from application APIs."""
        self.store = store
        self.boundary_factory = boundary_factory
        self.public_probe = public_probe

    def _step(self, identifier, step, resource, action):
        """Flush the exact single-mutation intent before calling Docker, then append only its acknowledged bounded outcome."""
        self.store.event(identifier, 'mutation_requested', {'step': step, 'resource': resource})
        try: result = action()
        except RuntimeHelperRefused:
            self.store.event(identifier, 'helper_refused', {'step': step, 'resource': resource, 'known_stopped_refusal_verified': True})
            raise
        self.store.event(identifier, 'mutation_completed', {'step': step, 'resource': resource, 'result': result})
        return result

    def _volume(self, docker, identifier, role):
        """Create a new own named volume, recording its identity before the daemon call."""
        value = docker.spec('volume', identifier, role)
        return self._step(identifier, 'create_' + role, value, lambda: docker.create_volume(value))

    def _network(self, docker, identifier, role, internal):
        """Create a private own network; apps only join isolated internal bridges."""
        details={'internal':internal}
        if internal:
            reservation=docker.spec('network',identifier,'address-reservation',internal=True)
            reservation=self._step(identifier,'reserve_private_subnet',reservation,lambda:docker.create_network(reservation))
            subnet=docker.allocated_subnet(reservation)
            self._step(identifier,'release_empty_subnet_reservation',reservation,lambda:docker.remove_address_reservation(reservation))
            details['subnet']=subnet
        value = docker.spec('network', identifier, role, **details)
        return self._step(identifier, 'create_' + role, value, lambda: docker.create_network(value))

    def _container(self, docker, identifier, spec):
        """Create one acknowledged fixed app/proxy/helper and retain its immutable CID in the private journal."""
        return self._step(identifier, 'create_' + spec['role'], spec, lambda: docker.create_container(spec))

    def _helper(self, docker, identifier, action, policy, config, *, source=None, current=None, target=None, network=None, target_ip=None, recovery=False, archive_manifest_sha256=None):
        """Run one fixed trusted helper once, retaining a stopped private result before removing only that helper."""
        if action=='probe':
            # A stopped container loses tmpfs content. Keep the one small
            # readiness result in a separate own volume, never on business data.
            if target is not None:raise RuntimeReleaseError('Probe result destination is generated by the controller')
            target=self._volume(docker,identifier,'recovery-probe-result' if recovery else 'probe-result')
        mounts = []
        # Snapshot/restore fences intentionally write only cooperative lock
        # files on owned stopped sources; business SQL and source bytes are
        # read-only. Probe needs no native fence and mounts data read-only.
        for volume, destination in ((source, '/source'), (current, '/current'), (target, '/target')):
            if volume: mounts.append({'volume': volume, 'destination': destination, 'readonly': action == 'probe' and destination!='/target'})
        spec = docker.container_spec(identifier, 'helper-' + ('recovery-' if recovery else '') + action, policy.helper_image_id, network=network,
                                     mounts=mounts, command=['-B', '/app/backend/local_runtime_helper.py'])
        spec['helper_action'] = action
        helper = self._container(docker, identifier, spec)
        frame = {'profile': policy.profile, 'action': action, 'operation_id': identifier, 'config': config,
                 'bootstrap_sha256': policy.bootstrap_sha256, 'target_ip': target_ip, 'archive_manifest_sha256': archive_manifest_sha256}
        try:
            result = self._step(identifier, 'run_' + action, helper, lambda: docker.helper(helper, frame))
        except RuntimeHelperRefused:
            self._step(identifier, 'remove_' + action, helper, lambda: docker.remove_helper(helper))
            raise
        self._step(identifier, 'remove_' + action, helper, lambda: docker.remove_helper(helper))
        return result

    def _stop_stack(self, docker, identifier, previous):
        """Close public ingress first, then drain the exact owned app before an offline inspection or copy."""
        for role in ('proxy', 'app'):
            value = previous[role]
            self._step(identifier, 'stop_previous_' + role, value, lambda value=value: docker.stop(value))
        docker.stopped(previous['app'])
        docker.stopped(previous['proxy'])

    def _check_images(self, docker, policy):
        """Recheck only explicitly bound immutable local images, including retained historical images needed for exact cleanup."""
        for binding in policy.operator_allowed_images: docker.image(binding)

    def _recover_previous(self, docker, identifier, previous, candidate, *, resume_previous=True):
        """After a known refusal, stop only acknowledged candidate workloads and restart the exact unchanged previous data/image stack."""
        if candidate:
            for role in ('proxy', 'app'):
                if candidate.get(role):
                    value = candidate[role]
                    self._step(identifier, 'stop_refused_' + role, value, lambda value=value: docker.stop(value))
        if not previous or not resume_previous:
            self.store.event(identifier, 'failed_closed', {'new_candidate_stopped': True, 'existing_data_overwritten': False})
            return
        for role in ('app', 'proxy'):
            value = previous[role]
            if not docker.inspect(value)['State']['Running']:
                self._step(identifier, 'recover_previous_' + role, value, lambda value=value: docker.start(value))
        policy = self.store.policy_at(previous['policy_revision'])
        config = self.store.private_config(previous['operation_id'])
        self._helper(docker, identifier, 'probe', policy, config, source=previous['data_volume'],
                     network=previous['app_network']['name'], target_ip=docker.app_ip(previous['app']), recovery=True)
        self.public_probe(previous['origin'])
        self.store.event(identifier, 'recovered_previous', {'generation': previous['generation'], 'new_candidate_stopped': True,
                                                           'previous_data_unchanged': True, 'public_deployment_verified': False})

    def _operate(self, kind, body):
        """Replay original keys before engine/policy checks; fresh operations keep a durable reservation over every mutation and crash."""
        identifier = operation_id(body.request_key)
        with self.store.exclusive():
            fresh, original = self.store.reserve(kind, body)
            if not fresh: return self.store.get(identifier)
            policy = self.store.policy_at(original['policy_revision'])
            current = self.store.slot(body.slot)
            previous = current['stack']
            candidate = {}
            try:
                with self.boundary_factory(self.store.marker['owner']) as docker:
                    self.store.bind_engine(docker.engine())
                    self._check_images(docker, policy)
                    if previous: self._check_images(docker, self.store.policy_at(previous['policy_revision']))
                    self.store.event(identifier, 'engine_checked', {'local_engine_pinned': True})
                    if kind == 'stop':
                        self._stop_stack(docker, identifier, previous)
                        self.store.finish(identifier)
                        return self.store.get(identifier)
                    config = self.store.private_config(identifier, {'profile': policy.profile, 'slot': body.slot,
                                                       'origin': policy.origin(body.slot), 'ready_token': secrets.token_hex(32)})
                    archive = None
                    image = body.image_id if kind == 'activate' else self.store.archive(body.snapshot_id, body.slot)['image_id']
                    binding = policy.image(image)
                    if previous:
                        self._stop_stack(docker, identifier, previous)
                        archive_volume = self._volume(docker, identifier, 'archive')
                        try:
                            archived = self._helper(docker, identifier, 'snapshot', policy, config,
                                                    source=previous['data_volume'], target=archive_volume)
                        except RuntimeHelperRefused:
                            self._recover_previous(docker, identifier, previous, candidate, resume_previous=current['state'] == 'running')
                            return self.store.get(identifier)
                        self.store.save_archive(identifier, body.slot, previous['generation'],
                                                {'volume': archive_volume, 'image_id': previous['image_id'],
                                                 'source_commit': previous['source_commit'], 'proof': archived})
                        self.store.event(identifier, 'archive_sealed', {'snapshot_id': identifier, 'manifest_sha256': archived['manifest_sha256']})
                        archive = self.store.archive(body.snapshot_id if kind == 'rollback' else identifier, body.slot)
                    app_network = self._network(docker, identifier, 'private', True)
                    front_network = self._network(docker, identifier, 'front', False)
                    data_volume = self._volume(docker, identifier, 'data')
                    code_volume = self._volume(docker, identifier, 'bootstrap')
                    candidate.update({'data_volume': data_volume, 'app_network': app_network, 'front_network': front_network})
                    try:
                        self._helper(docker, identifier, 'bootstrap', policy, config, target=code_volume)
                        if archive:
                            # Archive identity/manifest bytes were sealed by the
                            # original same-slot operation, not provided by JSON.
                            self._helper(docker, identifier, 'restore', policy, config, source=archive['volume'],
                                         current=previous['data_volume'], target=data_volume,
                                         archive_manifest_sha256=archive['proof']['manifest_sha256'])
                        else: self._helper(docker, identifier, 'initialize', policy, config, target=data_volume)
                        app_spec = docker.container_spec(identifier, 'app', image, network=app_network['name'],
                            mounts=[{'volume': data_volume, 'destination': '/data', 'readonly': False},
                                    {'volume': code_volume, 'destination': '/bootstrap', 'readonly': True}],
                            command=['-B', '/bootstrap/runtime.py', 'app'], ipv4_address=docker.fixed_app_address(app_network))
                        candidate['app'] = self._container(docker, identifier, app_spec)
                        self._step(identifier, 'start_app', candidate['app'], lambda: docker.start(candidate['app']))
                        address = docker.app_ip(candidate['app'])
                        self._helper(docker, identifier, 'probe', policy, config, source=data_volume,
                                     network=app_network['name'], target_ip=address)
                        proxy_config = {'profile': policy.profile, 'slot': body.slot, 'origin': config['origin'], 'target_ip': address}
                        proxy_spec = docker.container_spec(identifier, 'proxy', policy.helper_image_id,
                            network=app_network['name'], front=front_network['name'], origin=config['origin'],
                            command=['-B', '/app/backend/local_runtime_bootstrap.py', 'proxy', canonical(proxy_config)])
                        candidate['proxy'] = self._container(docker, identifier, proxy_spec)
                        candidate['proxy'] = self._step(identifier, 'connect_front', candidate['proxy'], lambda: docker.connect_front(candidate['proxy']))
                        self._step(identifier, 'start_proxy', candidate['proxy'], lambda: docker.start(candidate['proxy']))
                        docker.verify_network_members(app_network, [candidate['app'], candidate['proxy']])
                        docker.verify_network_members(front_network, [candidate['proxy']])
                        self.public_probe(config['origin'])
                    except RuntimeHelperRefused:
                        self._recover_previous(docker, identifier, previous, candidate, resume_previous=current['state'] == 'running')
                        return self.store.get(identifier)
                    self.store.event(identifier, 'local_readiness_verified', {'private_app_ready': True, 'fixed_proxy_ready': True,
                                                                            'external_app_network': False, 'existing_data_overwritten': False})
                    stack = {'operation_id': identifier, 'slot': body.slot, 'generation': body.expected_generation + 1,
                             'policy_revision': original['policy_revision'], 'image_id': image, 'source_commit': binding.source_commit,
                             'origin': config['origin'], 'app': candidate['app'], 'proxy': candidate['proxy'],
                             'data_volume': data_volume, 'bootstrap_volume': code_volume,
                             'app_network': app_network, 'front_network': front_network,
                             'snapshot_id': archive['id'] if archive else None, 'local_runtime_verified': True,
                             'public_deployment_verified': False, 'independent_review_completed': False}
                    self.store.finish(identifier, stack)
            except Exception as error:
                # Neither unknown daemon acceptance nor a lost host response is
                # a reason to create/start/copy again. All known/new resources
                # and original newer data remain available for observation.
                history = self.store.get(identifier)
                mutations = [event for event in history['events'] if event['phase'] == 'mutation_requested']
                if history['phase'] not in {'committed', 'stopped', 'recovered_previous', 'failed_closed'}:
                    if not mutations:
                        # Engine/image/config preflight consists only of reads.
                        # An explicit refusal here cannot have created a daemon
                        # resource and must not lock out a corrected policy.
                        self.store.event(identifier, 'failed_closed', {'preflight_failed_before_daemon_mutations': True,
                                                                      'current_generation_unchanged': True})
                    else:
                        self.store.event(identifier, 'attention', {'original_outcome_reserved': True,
                                                                 'unknown_daemon_acceptance': isinstance(error, RuntimeMutationUnknown)})
                failure = RuntimeReleaseError('Local runtime operation needs read-only reconciliation; preserve its original journal and resources')
                # Fixed installed reasons and stage names support private/CI
                # diagnosis; CLI history never echoes exception/input values.
                failure.diagnostic = {'cause_type': type(error).__name__,
                    'fixed_reason': str(error) if isinstance(error, RuntimeReleaseError) else None,
                    'last_requested_step': mutations[-1]['details']['step'] if mutations else 'read_only_preflight',
                    'mutations_requested': len(mutations), 'phase': self.store.get(identifier)['phase']}
                raise failure from None
            return self.store.get(identifier)

    def activate(self, body):
        """Activate an explicitly allowed image using a new same-slot volume; first activation creates fresh independent A/B accounts."""
        return self._operate('activate', LocalRuntimeActivation.model_validate(body))

    def rollback(self, body):
        """Restore one immutable same-slot archive to a new volume, retaining later data and current financial/security history."""
        return self._operate('rollback', LocalRuntimeRollback.model_validate(body))

    def stop(self, body):
        """Stop only the exact observed current generation; replaying an old stop cannot affect a newer activation."""
        return self._operate('stop', LocalRuntimeStop.model_validate(body))

    def export_initial_claims(self, slot, expected_generation, destination):
        """Export current initial login claims only to one new private file outside source/Git/journal; never print passwords or reset accounts."""
        import os
        from pathlib import Path
        from app.services.community.local_release_files import plain, separate
        from app.controlplane.migration import flush_directory
        target = plain(destination, exists=False)
        parent = plain(target.parent)
        separate(self.store.root, target)
        source = Path(__file__).resolve().parents[4]
        separate(source, target)
        if target.exists() or not parent.is_dir() or os.name != 'nt' and parent.stat().st_mode & 0o077:
            raise RuntimeReleaseError('Initial claims require a new file in an existing private directory')
        markers = [item / '.git' for item in (parent, *parent.parents)]
        if any(marker.is_file() or marker.is_symlink() or any((marker / name).exists() for name in ('HEAD', 'config', 'objects')) for marker in markers):
            raise RuntimeReleaseError('Initial claims must stay outside Git checkouts')
        with self.store.exclusive():
            current = self.store.slot(slot)
            if type(expected_generation) is not int or current['generation'] != expected_generation or current['stack'] is None:
                raise RuntimeReleaseError('Exact current local generation is required for initial claims')
            stack = current['stack']
            with self.boundary_factory(self.store.marker['owner']) as docker:
                self.store.bind_engine(docker.engine())
                self._check_images(docker, self.store.policy_at(stack['policy_revision']))
                claims = docker.initial_claims(stack['app'])
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write((canonical(claims) + '\n').encode()); stream.flush(); os.fsync(stream.fileno())
            flush_directory(parent)
        return {'private_initial_claims_written': True, 'passwords_printed': False, 'accounts_reset': False, 'generation': expected_generation, **release_flags()}

    def _observe(self, docker, identifier):
        """Inspect original names plus the current stack, allowing only the two exact outcomes of a lost proxy attachment acknowledgment."""
        history = self.store.get(identifier)
        original = self.store.original(identifier)
        self.store.bind_engine(docker.engine())
        self._check_images(docker, self.store.policy_at(original['policy_revision']))
        current = self.store.slot(original['input']['slot'])['stack']
        resources = {}
        if current:
            self._check_images(docker, self.store.policy_at(current['policy_revision']))
            for key in ('app', 'proxy', 'data_volume', 'bootstrap_volume', 'app_network', 'front_network'):
                value = current[key]
                resources[value['name']] = value
        attach_requested = set()
        for event in history['events']:
            detail = event['details']
            if event['phase'] in {'mutation_requested', 'mutation_completed', 'helper_refused'}:
                value = detail['result'] if event['phase'] == 'mutation_completed' and isinstance(detail.get('result'), dict) and 'kind' in detail['result'] else detail['resource']
                resources[value['name']] = value
                if detail['step'] == 'connect_front': attach_requested.add(value['name'])
        observations = []
        verified = []
        for value in resources.values():
            try:
                try: record = docker.inspect(value, absent=True)
                except RuntimeReleaseError:
                    if value['name'] not in attach_requested or value['role'] != 'proxy': raise
                    value = {**value, 'front_connected': not value.get('front_connected', False)}
                    record = docker.inspect(value, absent=True)
                observed = {'name': value['name'], 'kind': value['kind'], 'exists': record is not None}
                if record is not None:
                    # An accepted create can lose its response before the CID
                    # reaches the journal. Bind the independently verified
                    # actual ID before any later explicit stop or cleanup.
                    value={**value,'id':record['Id'] if value['kind']!='volume' else record['Name']}
                    observed['id']=value['id']
                    observed['identity_sha256'] = digest(value)
                    verified.append(value)
                if record is not None and value['kind'] == 'container':
                    state = record['State']
                    observed.update({'running': state['Running'], 'pid_zero': state['Pid'] == 0, 'exit_code': state.get('ExitCode')})
                    if value['role'].startswith('helper-') and not state['Running']:
                        try: observed['helper_result'] = docker.helper_result(value, value.get('helper_action', value['role'].removeprefix('helper-')))
                        except RuntimeReleaseError: observed['helper_result_verified'] = False
            except RuntimeReleaseError:
                observed = {'name': value['name'], 'kind': value['kind'], 'identity_needs_attention': True}
            observations.append(observed)
        return observations, verified

    def reconcile(self, identifier):
        """Observe exact original resources and retained helper outcomes with no daemon create/start/stop/copy retry."""
        with self.store.exclusive(), self.boundary_factory(self.store.marker['owner']) as docker:
            history = self.store.get(identifier)
            observations, _ = self._observe(docker, identifier)
            return {'id': identifier, 'phase': history['phase'], 'resources': observations,
                    'observation_sha256': digest(observations), 'daemon_mutations_performed': 0, **release_flags()}

    def quiesce(self, body):
        """Explicitly stop exact interrupted/current owned workloads after a fresh observation CAS, retaining every data volume and archive."""
        body = LocalRuntimeQuiescence.model_validate(body)
        with self.store.exclusive():
            if not self.store.begin_quiescence(body): return self.store.get(body.operation_id)
            try:
                with self.boundary_factory(self.store.marker['owner']) as docker:
                    observations, verified = self._observe(docker, body.operation_id)
                    if digest(observations) != body.expected_observation_sha256 or any(item.get('identity_needs_attention') for item in observations):
                        raise RuntimeReleaseError('Observed original runtime resources changed before explicit quiescence')
                    containers = [item for item in verified if item['kind'] == 'container']
                    containers.sort(key=lambda item: 0 if item['role'] == 'proxy' else 1)
                    for value in containers:
                        self._step(body.operation_id, 'quiesce_workload', value, lambda value=value: docker.stop(value))
                    for value in containers: docker.stopped(value)
                    self.store.finish_closed(body.operation_id, body.expected_observation_sha256)
            except Exception:
                self.store.event(body.operation_id, 'attention', {'quiescence_outcome_reserved': True, 'no_automatic_retry': True})
                raise RuntimeReleaseError('Explicit quiescence needs another read-only reconciliation; no action was automatically repeated') from None
            return self.store.get(body.operation_id)
