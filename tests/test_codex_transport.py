"""Opt-in installed-Codex checks against a loopback synthetic Responses server."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import codex_worker as worker
import sealed_run


@unittest.skipUnless(
    os.environ.get('POEM_CODEX_SMOKE') == '1' and shutil.which('codex'), 'Opt-in installed Codex smoke test'
)
class CodexTransportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='poem-codex-smoke-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.binary = str(Path(shutil.which('codex')).resolve(strict=True))
        self.world, self.scratch, self.control, self.home = [
            self.root / name for name in ['world', 'scratch', 'control', 'source-home']
        ]
        for path in [self.world, self.scratch, self.control, self.home]:
            path.mkdir()
        self.environment = sealed_run.child_environment(self.scratch)
        self.environment.update(HOME=str(self.root), CODEX_HOME=str(self.home), NO_PROXY='*', no_proxy='*')
        self.requests, self.headers = [], []
        self.answer = '{"summary":"Synthetic response; no model inference.","scores":{}}'
        item = {
            'id': 'msg_fixture',
            'type': 'message',
            'role': 'assistant',
            'status': 'completed',
            'phase': 'final_answer',
            'content': [{'type': 'output_text', 'text': self.answer, 'annotations': []}],
        }
        response = {
            'id': 'resp_fixture',
            'object': 'response',
            'created_at': 1,
            'model': 'gpt-6-astra',
            'status': 'completed',
            'output': [item],
            'usage': {
                'input_tokens': 1,
                'output_tokens': 1,
                'total_tokens': 2,
                'input_tokens_details': {'cached_tokens': 0},
            },
        }
        events = [
            ('response.created', {'response': dict(response, status='in_progress', output=[])}),
            ('response.output_item.added', {'output_index': 0, 'item': dict(item, status='in_progress', content=[])}),
            (
                'response.content_part.added',
                {
                    'item_id': item['id'],
                    'output_index': 0,
                    'content_index': 0,
                    'part': {'type': 'output_text', 'text': '', 'annotations': []},
                },
            ),
            (
                'response.output_text.delta',
                {'item_id': item['id'], 'output_index': 0, 'content_index': 0, 'delta': self.answer},
            ),
            (
                'response.output_text.done',
                {'item_id': item['id'], 'output_index': 0, 'content_index': 0, 'text': self.answer},
            ),
            (
                'response.content_part.done',
                {'item_id': item['id'], 'output_index': 0, 'content_index': 0, 'part': item['content'][0]},
            ),
            ('response.output_item.done', {'output_index': 0, 'item': item}),
            ('response.completed', {'response': response}),
        ]
        payload = ''.join(
            'event: ' + name + '\ndata: ' + json.dumps(dict(data, type=name, sequence_number=i)) + '\n\n'
            for i, (name, data) in enumerate(events)
        ).encode()
        requests, headers = self.requests, self.headers

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                headers.append({key.lower(): value for key, value in self.headers.items()})
                body = payload.replace(b'gpt-6-astra', requests[-1]['model'].encode())
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                body = json.dumps(
                    {
                        'clientAuth': True,
                        'openrouter': True,
                        'revision': 'a' * 64,
                        'capabilities': {'routingPolicies': [worker.POLICY], 'revisionPrecondition': True},
                    }
                ).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.url = 'http://127.0.0.1:' + str(server.server_port) + '/v1'
        self.catalog = self.root / 'catalog.json'
        data = json.loads(
            subprocess.run(
                [self.binary, 'debug', 'models', '--bundled'],
                env=self.environment,
                capture_output=True,
                check=True,
                timeout=20,
            ).stdout
        )
        template = next(model for model in data['models'] if model['slug'] == 'gpt-6-astra')
        data['models'].append(dict(template, slug='deepseek/transport-fixture'))
        self.catalog.write_text(json.dumps(data))

    def capture(self, args, prompt, name, environment=None, review=True):
        events, stderr = self.root / (name + '.jsonl'), self.root / (name + '.stderr')
        result = sealed_run.capture_process(
            [self.binary] + args, self.world, environment or self.environment, prompt, events, stderr, 30
        )
        self.assertEqual(result['exit_code'], 0, stderr.read_text())
        records = [json.loads(line) for line in events.read_text().splitlines()]
        self.assertEqual([record['item'] for record in records if record.get('item', {}).get('type') == 'error'], [])
        worker.event_outcome(events, forbid_tools=review)
        self.assertEqual((self.control / 'final-message.txt').read_text().strip(), self.answer)

    def assert_source_only(self, request):
        tools = {tool.get('name', tool.get('type')) for tool in request.get('tools', [])}
        self.assertFalse(tools & {'exec_command', 'shell', 'shell_command', 'apply_patch', 'web_search', 'mcp'})
        self.assertNotEqual(request.get('text', {}).get('format', {}).get('type'), 'json_schema')

    def test_native_exact_seed_source_only_review_and_real_sandbox_canaries(self):
        for review in [False, True]:
            self.assertEqual(
                worker.audit_isolation(
                    self.world, self.scratch, self.control, review, self.environment, binary=self.binary
                )['status'],
                'passed',
            )
            instructions = worker.REVIEW_INSTRUCTIONS if review else worker.GENERATION_INSTRUCTIONS
            (self.control / 'instructions.txt').write_text(instructions)
            args = worker.arguments(
                {'model': 'gpt-6-astra', 'reasoning': 'max'},
                self.world,
                self.scratch,
                self.control,
                self.catalog,
                '',
                review=review,
                router_revision='a' * 64,
            )[:-1]
            for config in [
                'model_provider="synthetic"',
                'model_providers.synthetic.name="Synthetic local fixture"',
                'model_providers.synthetic.base_url=' + json.dumps(self.url),
                'model_providers.synthetic.wire_api="responses"',
                'model_providers.synthetic.requires_openai_auth=false',
                'model_providers.synthetic.supports_websockets=false',
            ]:
                args += ['--config', config]
            seed = b'Return the synthetic fixture answer.' if review else sealed_run.PROMPT_PATH.read_bytes()
            self.capture(args + ['-'], seed, 'review' if review else 'generation', review=review)
            request = self.requests[-1]
            self.assertEqual(request['model'], 'gpt-6-astra')
            self.assertIn(instructions.strip(), json.dumps(request).replace('\\n', '\n'))
            user_texts = [
                content.get('text')
                for entry in request.get('input', [])
                if entry.get('role') == 'user'
                for content in entry.get('content', [])
                if isinstance(content, dict)
            ]
            self.assertIn(seed.decode(), user_texts)
            if review:
                self.assert_source_only(request)
        self.assertEqual(len(self.requests), 2)

    def write_profile(self, ambient=''):
        profile = self.home / 'unified-openrouter.config.toml'
        profile.write_text(
            '\n'.join(
                [
                    ambient,
                    'model = "deepseek/transport-fixture"',
                    'model_provider = "unified"',
                    'model_catalog_json = ' + json.dumps(str(self.catalog)),
                    '[model_providers.unified]',
                    'name = "Synthetic router transport"',
                    'base_url = ' + json.dumps(self.url),
                    'wire_api = "responses"',
                    'requires_openai_auth = false',
                    'supports_websockets = false',
                    'http_headers = { "X-Codex-Router-Token" = "' + 'b' * 64 + '", '
                    '"X-Codex-Routing-Policy" = "preserve-model-v1" }',
                ]
            )
            + '\n'
        )
        return profile

    def assert_router_transport(self):
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0]['model'], 'deepseek/transport-fixture')
        self.assertEqual(self.headers[0]['x-codex-routing-policy'], worker.POLICY)
        self.assertEqual(self.headers[0]['x-codex-router-token'], 'b' * 64)
        self.assertNotIn('authorization', self.headers[0])
        self.assert_source_only(self.requests[0])

    def test_documented_profile_loads_sidecar_and_inherits_base_settings_without_openai_auth(self):
        (self.home / 'config.toml').write_text(
            'model = "gpt-6-astra"\n' 'developer_instructions = "BASE_AMBIENT_INSTRUCTIONS"\n'
        )
        self.write_profile()
        args = [
            'exec',
            '--profile',
            'unified-openrouter',
            '--ephemeral',
            '--skip-git-repo-check',
            '--ignore-rules',
            '--strict-config',
            '--json',
            '--color',
            'never',
            '--cd',
            str(self.world),
            '--output-last-message',
            str(self.control / 'final-message.txt'),
        ]
        for config in [
            'approval_policy="never"',
            'default_permissions=' + json.dumps(worker.PROFILE),
            worker.permission_config(self.world, self.scratch, review=True),
            'suppress_unstable_features_warning=true',
            'mcp_servers={}',
            'web_search="disabled"',
            'project_doc_max_bytes=0',
        ]:
            args += ['--config', config]
        for feature in worker.DISABLED + ['shell_tool', 'unified_exec']:
            args += ['--disable', feature]
        args += ['--enable', 'skip_host_skill_discovery', '-']
        self.capture(args, b'Synthetic documented profile check.', 'profile')
        self.assert_router_transport()
        self.assertIn('BASE_AMBIENT_INSTRUCTIONS', json.dumps(self.requests[0]))
        self.assertNotIn('x-codex-router-revision', self.headers[0])

    def test_worker_reconstructs_private_home_and_excludes_source_home_ambient_settings(self):
        (self.home / 'config.toml').write_text('invalid base config; must not be loaded')
        transport = worker.transport_config(
            self.write_profile('developer_instructions = "PRIVATE_AMBIENT_INSTRUCTIONS"'), requires_openai_auth=False
        )
        (self.root / 'transport.config.toml').write_bytes(transport)
        version = subprocess.run(
            [self.binary, '--version'], env=self.environment, capture_output=True, text=True, check=True, timeout=10
        ).stdout.strip()
        runtime = {
            'codex_version': version,
            'codex_binary_sha256': worker.sha(Path(self.binary).read_bytes()),
            'catalog_sha256': worker.sha(self.catalog.read_bytes()),
            'transport_config_sha256': worker.sha(transport),
            'review_instructions_sha256': worker.sha(worker.REVIEW_INSTRUCTIONS.encode()),
            'router': worker.router_health(self.url),
            'model_capabilities': {
                'deepseek/transport-fixture': {
                    'transport': 'openrouter',
                    'requires_openai_auth': False,
                    'structured_output': 'local-validation',
                }
            },
        }
        output = self.root / 'worker-job'
        with mock.patch.dict(os.environ, self.environment, clear=True):
            receipt = worker.run_job(
                output,
                {'model': 'deepseek/transport-fixture', 'reasoning': 'max', 'seconds': 30},
                b'Synthetic isolated worker check.',
                runtime,
                self.catalog,
                self.url,
                review=True,
            )
        failure = output / 'control/failure.txt'
        self.assertEqual(receipt['status'], 'completed', failure.read_text() if failure.exists() else receipt)
        self.assertEqual(receipt['runtime_checks']['status'], 'passed')
        self.assertEqual(receipt['isolation']['status'], 'passed')
        self.assert_router_transport()
        self.assertEqual(self.headers[0]['x-codex-router-revision'], 'a' * 64)
        self.assertIn(worker.REVIEW_INSTRUCTIONS.strip(), json.dumps(self.requests[0]).replace('\\n', '\n'))
        self.assertNotIn('PRIVATE_AMBIENT_INSTRUCTIONS', json.dumps(self.requests[0]))
        self.assertNotEqual(output / 'control/codex-home', self.home)


if __name__ == '__main__':
    unittest.main()
