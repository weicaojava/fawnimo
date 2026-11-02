"""Exercise operational refusal/cleanup invariants without creating or connecting to any Docker resources."""
import importlib.util
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


if __name__ == '__main__':
    unittest.main()
