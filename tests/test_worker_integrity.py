import contextlib
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import codex_worker as worker
import sealed_run


class RouterBoundaryTests(unittest.TestCase):
    def test_health_redirects_are_rejected_before_contacting_the_target(self):
        contacted = []
        health = {
            'clientAuth': True,
            'openrouter': True,
            'revision': 'a' * 64,
            'capabilities': {'routingPolicies': [worker.POLICY], 'revisionPrecondition': True},
        }

        class Target(BaseHTTPRequestHandler):
            def do_GET(self):
                contacted.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(health).encode())

            def log_message(self, *_):
                pass

        with contextlib.ExitStack() as stack:
            target = ThreadingHTTPServer(('127.0.0.1', 0), Target)

            class Redirect(Target):
                def do_GET(self):
                    self.send_response(int(self.path.split('/')[1]))
                    self.send_header('Location', 'http://127.0.0.1:' + str(target.server_port) + '/target')
                    self.end_headers()

            redirect = ThreadingHTTPServer(('127.0.0.1', 0), Redirect)
            for server in [target, redirect]:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                stack.callback(thread.join, 2)
                stack.callback(server.server_close)
                stack.callback(server.shutdown)
            for code in [301, 302, 303, 307, 308]:
                with self.subTest(code=code):

                    class RewriteRedirect(Redirect):
                        def do_GET(self):
                            self.path = '/' + str(code)
                            super().do_GET()

                    redirect.RequestHandlerClass = RewriteRedirect
                    with self.assertRaisesRegex(ValueError, 'redirect'):
                        worker.router_health('http://127.0.0.1:' + str(redirect.server_port))
            self.assertEqual(contacted, [])

    def test_invalid_health_origins_are_rejected_before_network_access(self):
        for url in [
            'https://127.0.0.1/v1',
            'http://localhost/v1',
            'http://example.invalid/v1',
            'http://127.0.0.1:65536/v1',
            'http://user@127.0.0.1/v1',
            'http://127.0.0.1/elsewhere',
        ]:
            with self.subTest(url=url), mock.patch.object(worker, 'build_opener') as opener:
                with self.assertRaises(ValueError):
                    worker.router_health(url)
                opener.assert_not_called()

    def test_private_worker_transport_allows_only_the_exact_project_trust_stanza(self):
        transport = b'[model_providers.unified]\nname = "fixture"\n'
        world = Path('/tmp/worker-world')
        worker.validate_worker_transport(transport, transport, world)
        trusted = transport + ('\n[projects."' + str(world.resolve()) + '"]\ntrust_level = "trusted"\n').encode()
        worker.validate_worker_transport(trusted, transport, world)
        repository_trust = transport + ('\n[projects."' + str(worker.ROOT) + '"]\ntrust_level = "trusted"\n').encode()
        worker.validate_worker_transport(repository_trust, transport, world)
        with self.assertRaisesRegex(ValueError, 'Worker transport changed'):
            worker.validate_worker_transport(trusted + b'extra = true\n', transport, world)


class RuntimeIntegrityTests(unittest.TestCase):
    def exercise(self, changed=None, phase='audit', *, review=False, interrupt=False):
        with tempfile.TemporaryDirectory() as temporary, contextlib.ExitStack() as stack:
            root = Path(temporary).resolve()
            binary = root / 'codex-real'
            binary.write_bytes(b'Synthetic executable identity; never executed.')
            alias = root / 'codex'
            alias.symlink_to(binary)
            catalog = root / 'catalog.json'
            catalog.write_bytes(b'{"models":[]}')
            transport = root / 'transport.config.toml'
            transport.write_bytes(b'Synthetic private transport')
            output = root / 'job'
            runtime = {
                'codex_version': 'fixture',
                'codex_binary_sha256': worker.sha(binary.read_bytes()),
                'catalog_sha256': worker.sha(catalog.read_bytes()),
                'transport_config_sha256': worker.sha(transport.read_bytes()),
                'router': {'policy': worker.POLICY, 'revision': 'a' * 64},
                'model_capabilities': {
                    'router/fixture': {
                        'transport': 'openrouter',
                        'requires_openai_auth': False,
                        'structured_output': 'local-validation',
                    }
                },
                'capability_registry_sha256': 'b' * 64,
                'generation_instructions_sha256': worker.sha(worker.GENERATION_INSTRUCTIONS.encode()),
                'review_instructions_sha256': worker.sha(worker.REVIEW_INSTRUCTIONS.encode()),
            }
            health = dict(runtime['router'])
            launched, audited = [], []

            def mutate():
                if changed == 'router':
                    health['revision'] = 'b' * 64
                elif changed:
                    paths = {
                        'binary': binary,
                        'catalog': catalog,
                        'transport': transport,
                        'worker_catalog': output / 'control/catalog.json',
                        'worker_transport': output / 'control/codex-home/config.toml',
                        'instructions': output / 'control/instructions.txt',
                    }
                    path = paths[changed]
                    if path.exists():
                        path.chmod(0o600)
                    path.write_bytes(b'Changed during worker execution')

            def audit(*args, **kwargs):
                audited.append(kwargs.get('binary'))
                if phase == 'audit':
                    mutate()
                return {'status': 'passed'}

            def capture(command, world, environment, prompt, events, stderr, timeout, **kwargs):
                if phase == 'launch':
                    mutate()
                if kwargs.get('before_launch'):
                    kwargs['before_launch']()
                launched.append(command)
                (world / 'README.md').write_text('Retained synthetic work')
                events.write_text(
                    '\n'.join(
                        json.dumps(item)
                        for item in [
                            {'type': 'thread.started', 'thread_id': 'fixture'},
                            {'type': 'turn.completed', 'usage': {}},
                        ]
                    )
                )
                stderr.write_text('')
                if phase == 'process':
                    mutate()
                if interrupt:
                    raise KeyboardInterrupt
                return {'exit_code': 0, 'stopped_reason': None}

            stack.enter_context(mock.patch.object(worker.shutil, 'which', return_value=str(alias)))
            stack.enter_context(
                mock.patch.object(worker.subprocess, 'run', return_value=SimpleNamespace(stdout='fixture'))
            )
            stack.enter_context(
                mock.patch.object(worker, 'router_health', side_effect=lambda _url, **_kwargs: dict(health))
            )
            stack.enter_context(mock.patch.object(worker, 'audit_isolation', side_effect=audit))
            stack.enter_context(mock.patch.object(sealed_run, 'capture_process', side_effect=capture))
            receipt = worker.run_job(
                output,
                {'model': 'router/fixture', 'reasoning': 'max', 'seconds': 5},
                b'Synthetic seed',
                runtime,
                catalog,
                'http://127.0.0.1:18766/v1',
                review=review,
            )
            return receipt, launched, audited, str(binary)

    def test_runtime_changes_during_audit_or_at_launch_prevent_inference(self):
        for phase in ['audit', 'launch']:
            for changed in [
                'binary',
                'catalog',
                'transport',
                'worker_catalog',
                'worker_transport',
                'instructions',
                'router',
            ]:
                with self.subTest(phase=phase, changed=changed):
                    receipt, launched, _, _ = self.exercise(changed, phase)
                    self.assertEqual(receipt['status'], 'failed')
                    self.assertEqual(launched, [])

    def test_runtime_changes_during_execution_invalidate_the_receipt_but_keep_work(self):
        for changed in [
            'binary',
            'catalog',
            'transport',
            'worker_catalog',
            'worker_transport',
            'instructions',
            'router',
        ]:
            with self.subTest(changed=changed):
                receipt, launched, _, _ = self.exercise(changed, 'process')
                self.assertEqual(len(launched), 1)
                self.assertEqual(receipt['status'], 'failed')
                self.assertEqual(receipt['runtime_checks']['status'], 'failed')
                self.assertIn('candidate_sha256', receipt)

    def test_audit_and_worker_use_the_verified_absolute_executable_and_revision(self):
        receipt, launched, audited, binary = self.exercise()
        self.assertEqual(receipt['status'], 'completed')
        self.assertEqual(audited, [binary])
        self.assertEqual(launched[0][0], binary)
        self.assertIn('model_providers.unified.http_headers.X-Codex-Router-Revision="' + 'a' * 64 + '"', launched[0])
        self.assertEqual(receipt['runtime_checks']['status'], 'passed')
        self.assertEqual(
            [item['phase'] for item in receipt['runtime_checks']['phases']],
            ['before-audit', 'before-launch', 'after-process'],
        )
        self.assertTrue(any('/control/catalog.json' in item for item in launched[0]))

    def test_generation_and_reviewer_jobs_reject_runtime_drift(self):
        for review in [False, True]:
            with self.subTest(review=review):
                receipt, launched, _, binary = self.exercise('worker_catalog', 'process', review=review)
                self.assertEqual(launched[0][0], binary)
                self.assertEqual(receipt['status'], 'failed')
                self.assertEqual(receipt['runtime_checks']['status'], 'failed')

    def test_integrity_failure_does_not_hide_operator_cancellation(self):
        receipt, launched, _, _ = self.exercise('binary', 'process', interrupt=True)
        self.assertEqual(len(launched), 1)
        self.assertEqual(receipt['status'], 'cancelled')
        self.assertEqual(receipt['runtime_checks']['status'], 'failed')
        self.assertIn('candidate_sha256', receipt)

    def test_capture_preflight_failure_cannot_launch_a_process(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(sealed_run.subprocess, 'Popen') as launch:
            root = Path(temporary)

            def changed():
                self.assertTrue((root / 'events').is_file())
                raise ValueError('changed before Popen')

            with self.assertRaisesRegex(ValueError, 'changed before Popen'):
                sealed_run.capture_process(
                    ['unused'], root, {}, b'fixture', root / 'events', root / 'stderr', 1, before_launch=changed
                )
            launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
