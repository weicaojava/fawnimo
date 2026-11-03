"""Exercise operational refusal/cleanup invariants without creating or connecting to any Docker resources."""
import importlib.util
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'verify_workspace_container.py'
SPEC = importlib.util.spec_from_file_location('container_verifier', SOURCE)
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)


class EngineRefusalTests(unittest.TestCase):
    """Ensure network context validation fails before any engine mutation can become possible."""

    def test_each_environment_override_refuses_before_command(self):
        """All Docker endpoint override variables are rejected, including ones unrelated to the selected context."""
        for variable in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'):
            with self.subTest(variable=variable), patch.dict(VERIFIER.os.environ, {variable: 'fixture'}, clear=True), patch.object(VERIFIER, 'execute') as run:
                with self.assertRaises(ValueError):
                    VERIFIER.check_engine()
                run.assert_not_called()

    def test_remote_contexts_stop_after_readonly_context_inspection(self):
        """SSH, TCP and nonlocal Unix endpoints never reach even docker info, much less a write operation."""
        for endpoint in ('ssh://fixture.invalid', 'tcp://127.0.0.1:2375', 'unix://fixture.invalid/socket'):
            result = SimpleNamespace(stdout=json.dumps([{'Endpoints': {'docker': {'Host': endpoint}}}]).encode())
            with self.subTest(endpoint=endpoint), patch.dict(VERIFIER.os.environ, {}, clear=True), patch.object(VERIFIER, 'execute', return_value=result) as run:
                with self.assertRaises(ValueError):
                    VERIFIER.check_engine()
                self.assertEqual(run.call_args_list[0].args[0], ['docker', 'context', 'inspect'])
                self.assertEqual(run.call_count, 1)

    def test_windows_container_engine_is_rejected(self):
        """An available local endpoint is insufficient if it serves the wrong container OS."""
        responses = [SimpleNamespace(stdout=json.dumps([{'Endpoints': {'docker': {'Host': 'npipe:////./pipe/dockerDesktopWindowsEngine'}}}]).encode()),
                     SimpleNamespace(stdout=b'{"OSType":"windows"}')]
        with patch.dict(VERIFIER.os.environ, {}, clear=True), patch.object(VERIFIER, 'execute', side_effect=responses) as run:
            with self.assertRaises(ValueError):
                VERIFIER.check_engine()
            self.assertEqual(run.call_count, 2)

    def test_info_is_pinned_to_the_validated_local_endpoint(self):
        """Changing the CLI's current context after inspection cannot redirect the next engine read."""
        endpoint = 'unix:///var/run/docker.sock'
        responses = [SimpleNamespace(stdout=json.dumps([{'Endpoints': {'docker': {'Host': endpoint}}}]).encode()),
                     SimpleNamespace(stdout=b'{"OSType":"linux","ServerVersion":"fixture"}')]
        with patch.dict(VERIFIER.os.environ, {}, clear=True), patch.object(VERIFIER, 'execute', side_effect=responses) as run:
            self.assertEqual(VERIFIER.check_engine()['endpoint'], endpoint)
            self.assertEqual(run.call_args.args[0][:4], ['docker', '--host', endpoint, 'info'])


class ResourceOwnershipTests(unittest.TestCase):
    """Destructive cleanup must require both a random invocation label and the exact Compose project."""

    def runtime(self):
        """Create only an in-memory lifecycle owner, without invoking Docker or the constructor."""
        runtime = VERIFIER.Runtime.__new__(VERIFIER.Runtime)
        runtime.prefix, runtime.owner = 'fawnimo-check-fixture-001', 'owned-fixture-marker'
        runtime.docker_args, runtime.environment = ['docker', '--host', 'unix:///fixture.sock'], {}
        return runtime

    def test_unlabelled_existing_names_refuse_before_compose_creation(self):
        """Pre-created containers, volumes and networks without project labels cannot be reused or written."""
        for selected_kind in ('container', 'volume', 'network'):
            def response(arguments, **options):
                """Return a colliding name only, as a real unlabelled engine resource would appear."""
                if arguments[3:5] == ['image', 'inspect']:
                    return SimpleNamespace(stdout=b'[{"Id":"sha256:fixture"}]')
                kind = 'container' if arguments[3] == 'ps' else arguments[3]
                collision = kind == selected_kind and '--filter' in arguments and any(value.startswith('name=') for value in arguments)
                return SimpleNamespace(stdout=b'fawnimo-check-fixture-001_stable-data\n' if collision else b'')

            with self.subTest(kind=selected_kind), tempfile.TemporaryDirectory() as temporary, patch.object(VERIFIER, 'execute', side_effect=response) as run:
                directory = Path(temporary)
                relay = SimpleNamespace(origin='https://localhost:1')
                with self.assertRaises(ValueError):
                    VERIFIER.Runtime('fixture:image', 'fawnimo-check-fixture-001', directory,
                        {'stable': relay, 'preview': relay}, endpoint='unix:///fixture.sock')
                self.assertFalse((directory / 'compose-verification.json').exists())
                self.assertTrue(all('compose' not in call.args[0] for call in run.call_args_list))

    def test_container_stop_refuses_missing_or_wrong_owner_or_project(self):
        """Even a discovered identifier cannot authorize stop/removal if its ownership changed."""
        for labels in ({}, {VERIFIER.LABEL: 'another-marker', 'com.docker.compose.project': 'fawnimo-check-fixture-001'},
                       {VERIFIER.LABEL: 'owned-fixture-marker', 'com.docker.compose.project': 'other-project'}):
            result = SimpleNamespace(stdout=json.dumps([{'Config': {'Labels': labels}}]).encode())
            runtime = self.runtime()
            runtime.containers = {'stable': 'container-fixture-id'}
            with self.subTest(labels=labels), patch.object(VERIFIER, 'execute', return_value=result) as run:
                with self.assertRaises(RuntimeError):
                    runtime.remove_container('stable')
                self.assertEqual(run.call_count, 1)
                self.assertEqual(run.call_args.args[0], ['docker', '--host', 'unix:///fixture.sock', 'container', 'inspect', 'container-fixture-id'])

    def test_cleanup_refuses_foreign_volume_despite_matching_discovery(self):
        """A stale/forged list result is rechecked before deletion; no global cleanup operation exists."""
        runtime = self.runtime()
        result = SimpleNamespace(stdout=b'[{"Labels": {}}]')
        with patch.object(runtime, 'list_resources', side_effect=[[], [], ['foreign-volume']]), patch.object(VERIFIER, 'execute', return_value=result) as run:
            with self.assertRaises(RuntimeError):
                runtime.close()
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0], ['docker', '--host', 'unix:///fixture.sock', 'volume', 'inspect', 'foreign-volume'])

    def test_rechecks_container_before_removal_after_stop(self):
        """A changed owner between stop and removal cannot authorize a later rm operation."""
        runtime = self.runtime()
        runtime.containers = {'stable': 'fixture-id'}
        with patch.object(runtime, 'owned', side_effect=[{}, RuntimeError('ownership changed')]), patch.object(VERIFIER, 'execute') as run:
            with self.assertRaises(RuntimeError):
                runtime.remove_container('stable')
            run.assert_called_once_with(['docker', '--host', 'unix:///fixture.sock', 'stop', '--time', '45', 'fixture-id'], environment={}, timeout=60)


class SoundQualificationTests(unittest.TestCase):
    """Image sound qualification must reject incomplete evidence and lost resource ownership, independently of test exit status."""

    def result(self):
        """Provide authored input solely to exercise the receipt guards, never as actual-image qualification."""
        return {'profile': 'image_sound_software_v1', 'tests': 7, 'failures': 0, 'errors': 0, 'skipped': 0,
                'cases': sorted(VERIFIER.SOUND_CASES), 'junit_sha256': '1' * 64, 'uid': 10001,
                'readonly_code': True, 'external_model_calls': 0, 'model_quality_qualified': False,
                'complete_film_qualified': False, 'public_deployment_verified': False}

    def runtime(self):
        """Represent only a previously owned fixture identity, without starting or reaching a daemon."""
        runtime = VERIFIER.Runtime.__new__(VERIFIER.Runtime)
        runtime.containers = {'stable': 'exact-owned-container'}
        runtime.image_id = 'sha256:fixture'
        return runtime

    def observed(self):
        """Describe the minimum already-owned image controls required before this fixed trusted check."""
        return {'Image': 'sha256:fixture', 'Config': {'User': '10001:10001'},
                'HostConfig': {'ReadonlyRootfs': True}, 'State': {'Running': True}}

    def test_exact_complete_software_result_preserves_external_limits(self):
        """All seven fixed cases can pass without granting model, public or complete-film qualification."""
        value = self.result()
        self.assertEqual(VERIFIER.validate_sound_result(value), value)

    def test_skipped_missing_duplicate_or_falsely_scoped_cases_cannot_pass(self):
        """A green process or seven reported rows cannot replace the exact successful cases and runtime controls."""
        changes = [{'skipped': 1}, {'failures': 1}, {'errors': 1}, {'tests': True}, {'external_model_calls': False},
                   {'uid': 0}, {'readonly_code': False}, {'complete_film_qualified': True},
                   {'model_quality_qualified': 0}, {'public_deployment_verified': True}, {'junit_sha256': 'not-a-hash'},
                   {'cases': list(VERIFIER.SOUND_CASES[:-1])}, {'cases': [VERIFIER.SOUND_CASES[0]] * 7},
                   {'cases': [None] * 7}, {'untrusted_extra': 'cannot silently extend the profile'}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                VERIFIER.validate_sound_result({**self.result(), **change})

    def test_wrong_image_user_or_stopped_container_refuses_before_exec(self):
        """A stale/writable/replaced service is not a place to execute trusted test code or create temporary files."""
        for field, name, value in [('Image', None, 'another-image'), ('Config', 'User', '0'),
                                   ('HostConfig', 'ReadonlyRootfs', False), ('State', 'Running', False)]:
            observed = copy.deepcopy(self.observed())
            if name is None:
                observed[field] = value
            else:
                observed[field][name] = value
            runtime = self.runtime()
            with self.subTest(field=field), patch.object(runtime, 'owned', return_value=observed), patch.object(runtime, 'command') as command:
                with self.assertRaises(RuntimeError):
                    runtime.verify_sound_exports()
                command.assert_not_called()

    def test_changed_ownership_after_exec_cannot_qualify_the_result(self):
        """A result from an original process cannot approve a resource whose ownership check was subsequently lost."""
        runtime = self.runtime()
        response = SimpleNamespace(stdout=json.dumps(self.result()).encode())
        with patch.object(runtime, 'owned', side_effect=[self.observed(), RuntimeError('changed owner')]), patch.object(runtime, 'command', return_value=response) as command:
            with self.assertRaises(RuntimeError):
                runtime.verify_sound_exports()
            self.assertEqual(command.call_count, 1)
            self.assertEqual(command.call_args.args[:4], ('exec', '--user', '10001:10001', 'exact-owned-container'))

    def test_delivered_negative_receipt_is_retained_and_still_fails(self):
        """Successful diagnostic delivery cannot make a failed/skipped media test green or lose its safe failure details."""
        runtime = self.runtime()
        negative = {'profile': 'image_sound_software_v1', 'status': 'failed', 'stage': 'validate_fixed_cases',
                    'error_type': 'RuntimeError', 'counts': {'tests': 7, 'failures': 1, 'errors': 0, 'skipped': 0},
                    'diagnostics': [{'case': VERIFIER.SOUND_CASES[0], 'outcome': 'failure', 'code': 'fixture_assertion', 'code_locations': []}]}
        record = {}
        with patch.object(runtime, 'owned', return_value=self.observed()), patch.object(runtime, 'command', return_value=SimpleNamespace(stdout=json.dumps(negative).encode())):
            with self.assertRaises(RuntimeError):
                runtime.verify_sound_exports(record)
        self.assertEqual(record['sound_exports'], negative)
        self.assertNotIn('runtime_verified', record)

    def test_fixture_failure_summary_removes_credentials_and_keeps_useful_error(self):
        """Negative fixture diagnostics must remain useful without publishing bearer credentials, key fields or URL payloads."""
        raw = "CalledProcessError: encoder failed api_key='private-key-value' password=private-password secret='private secret with spaces' Bearer private-bearer https://fixture.invalid/download?secret=private-query " + 'Z' * 64
        summary = VERIFIER.sound_failure_summary(raw)
        self.assertIn('CalledProcessError: encoder failed', summary)
        for secret in ('private-key-value', 'private-password', 'private secret with spaces', 'private-bearer', 'private-query', 'Z' * 64):
            self.assertNotIn(secret, summary)
        self.assertLessEqual(len(summary), 384)


if __name__ == '__main__':
    unittest.main()
