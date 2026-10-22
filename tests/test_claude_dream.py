import hashlib
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import atlas
import bundles
import claude_dream
import codex_worker
import dream_permissions
from evaluation_fixtures import isolated_project


# A stand-in for Claude Code: it plays the isolation check and a tiny dream as stream-json, without a model.
# Files beside it choose a misbehaviour: `leak` shows the canary, `sleep` outlasts the wall time,
# `steal` writes the session credential into the world.
STUB = r'''#!{python}
import json, os, pathlib, re, sys, time
here = pathlib.Path(__file__).resolve().parent
args = sys.argv[1:]
if args == ['--version']:
    print('2.1.308 (Claude Code)')
    sys.exit(0)
(here / 'last-args.json').write_text(json.dumps(args))
(here / 'last-env.json').write_text(json.dumps(sorted(os.environ)))
prompt = args[args.index('-p') + 1]
model = args[args.index('--model') + 1]
def emit(item):
    print(json.dumps(item), flush=True)
emit({'type': 'system', 'subtype': 'init', 'model': model, 'tools': ['Bash', 'Edit', 'Read', 'Write']})
steps = []
if 'This is an isolation check' in prompt:
    canary = re.search(r'cat (\S+read-canary\.txt)', prompt).group(1)
    port = re.search(r'127\.0\.0\.1:(\d+)', prompt).group(1)
    shown = pathlib.Path(canary).read_text() if (here / 'leak').exists() else 'cat: Operation not permitted'
    pathlib.Path('audit-write.txt').write_text('audit-write')
    steps = [('Bash', {'command': 'cat ' + canary}, shown, not (here / 'leak').exists()),
             ('Read', {'file_path': canary}, 'Permission to use Read has been denied.', True),
             ('Bash', {'command': 'cat audit-readable.txt'}, pathlib.Path('audit-readable.txt').read_text(), False),
             ('Bash', {'command': 'printf audit-write > audit-write.txt && cat audit-write.txt'}, 'audit-write', False),
             ('Bash', {'command': "/usr/bin/curl --noproxy '*' http://127.0.0.1:" + port}, 'curl: (7) blocked', True)]
else:
    pathlib.Path('README.md').write_text('# A Small *World*\n\nIt keeps its questions.\n')
    pathlib.Path('app.js').write_text('console.log("hello");\n')
    if (here / 'steal').exists():
        pathlib.Path('notes.txt').write_text(os.environ.get('CLAUDE_CODE_OAUTH_TOKEN', ''))
    if (here / 'sleep').exists():
        time.sleep(30)
emit({'type': 'system', 'subtype': 'permission_denied', 'message': 'Read denied outside the working directory'})
for index, (name, given, output, error) in enumerate(steps):
    emit({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 't%d' % index, 'name': name, 'input': given}]}})
    emit({'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 't%d' % index, 'content': output, 'is_error': error}]}})
emit({'type': 'result', 'subtype': 'success', 'is_error': False, 'num_turns': 4, 'total_cost_usd': 12.5,
      'usage': {'input_tokens': 100, 'cache_read_input_tokens': 9000, 'cache_creation_input_tokens': 900, 'output_tokens': 700}})
'''


class ClaudeDreamTests(unittest.TestCase):
    """The opt-in Claude Code dream: audited isolation, a valid bundle, admission; no model is called."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='after-poem-claude-dream-')
        self.root = Path(self.temporary.name)
        self.project = isolated_project(self.root / 'project')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        stub = self.bin / 'claude'
        stub.write_text(STUB.replace('{python}', sys.executable))
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
        profile = json.loads((ROOT / 'examples/dream-profile-claude-v2.json').read_text())
        profile.pop('cohort_title', None)
        # The cohort's prompt as the study keeps it, by hash; the test project copies the corpus, not examples/.
        cohort = next(
            item
            for item in json.loads((ROOT / 'data/runs.json').read_text())['cohorts']
            if item['id'] == profile['cohort']
        )
        profile['prompt'] = 'corpus/prompts/' + cohort['prompt_sha256'] + '.txt'
        profile.update(run_id='dream-claude-test', output='.local/dream-runs/dream-claude-test', generation_seconds=20)
        self.profile_path = self.root / 'profile.json'
        self.profile_path.write_text(json.dumps(profile))
        self.environment = mock.patch.dict(
            os.environ, {'PATH': str(self.bin) + ':' + os.environ['PATH'], claude_dream.TOKEN_ENV: 'stub-token'}
        )
        self.environment.start()
        os.environ.pop('ANTHROPIC_API_KEY', None)

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def profile(self):
        return claude_dream.load_profile(self.profile_path, self.project)

    def test_the_passage_packs_and_admits_an_audited_world(self):
        profile = self.profile()
        bundle = claude_dream.dream(profile)
        record = bundles.validate_bundle(bundle)
        run = record['run']
        self.assertEqual(run['presentation'], {'label': 'Claude Opus 5.5 · Claude Code', 'title': 'A Small World'})
        self.assertEqual(run['condition']['harness']['name'], claude_dream.HARNESS)
        self.assertEqual(run['condition']['harness']['reasoning_effort'], profile['effort'])
        self.assertEqual(run['condition']['reported'], {'model': 'claude-opus-5-5', 'provider': 'anthropic'})
        self.assertEqual(run['condition']['isolation']['audit']['status'], 'passed')
        self.assertEqual(run['usage']['input_tokens'], 10000)
        self.assertEqual(run['usage']['cached_input_tokens'], 9000)
        self.assertIsNone(run['cost']['amount'])
        self.assertIn('subscription', run['cost']['provenance'])
        self.assertEqual(sorted(path.name for path in (bundle / 'candidate').iterdir()), ['README.md', 'app.js'])
        self.assertEqual(record['prompt'], (ROOT / profile['prompt']).read_bytes())
        # The transcript stays private, beside the bundle and never in it.
        self.assertTrue((profile['output'] / 'transcript.jsonl').is_file())
        self.assertFalse(list(bundle.rglob('*.jsonl')))
        claude_dream.admit(profile)
        data = atlas.validate(self.project)
        admitted = next(item for item in data['runs'] if item['id'] == 'dream-claude-test')
        self.assertEqual(admitted['cohort'], 'dream-4521-claude-code-v1')
        self.assertEqual(data['active_reviews']['dream-claude-test']['status'], 'unreviewed')

    def test_the_session_is_isolated_and_instructed_like_every_dreamer(self):
        claude_dream.dream(self.profile())
        args = json.loads((self.bin / 'last-args.json').read_text())
        environment = json.loads((self.bin / 'last-env.json').read_text())
        self.assertEqual(args[args.index('--append-system-prompt') + 1], codex_worker.GENERATION_INSTRUCTIONS)
        self.assertEqual(args[args.index('--permission-mode') + 1], 'default')
        self.assertEqual(args[args.index('--permission-prompt-tool') + 1], 'mcp__dream__decide')
        policy = json.loads(args[args.index('--mcp-config') + 1])['mcpServers']['dream']
        self.assertTrue(policy['args'][0].endswith('dream_permissions.py'))
        self.assertIn('--strict-mcp-config', args)
        for tool in ['WebFetch', 'WebSearch', 'Agent', 'SendMessage']:
            self.assertIn(tool, args[args.index('--disallowedTools') + 1 :])
        self.assertEqual(args[args.index('--tools') + 1].split(','), claude_dream.TOOLS)
        self.assertEqual(args[args.index('-p') + 1], self.profile()['prompt'].read_text(encoding='utf-8'))
        self.assertTrue({'CLAUDE_CODE_DISABLE_CLAUDE_MDS', 'CLAUDE_CODE_DISABLE_AUTO_MEMORY'} <= set(environment))
        self.assertNotIn('ANTHROPIC_API_KEY', environment)
        self.assertIn('CLAUDE_CONFIG_DIR', environment)
        settings = json.loads((self.profile()['output'] / 'control/settings.json').read_text())
        self.assertTrue(settings['sandbox']['enabled'] and settings['sandbox']['failIfUnavailable'])
        self.assertFalse(settings['sandbox']['allowUnsandboxedCommands'])
        self.assertTrue(settings['sandbox']['network']['strictAllowlist'])
        self.assertEqual(settings['sandbox']['network']['allowedDomains'], [])
        self.assertTrue(settings['permissions']['blockReadsOutsideWorkingDirectories'])

    def test_a_leaking_sandbox_stops_before_the_dream(self):
        (self.bin / 'leak').touch()
        with self.assertRaisesRegex(bundles.BundleError, 'host_read'):
            claude_dream.dream(self.profile())
        self.assertFalse((self.profile()['output'] / 'transcript.jsonl').exists())

    def test_a_world_holding_the_credential_is_never_packed(self):
        (self.bin / 'steal').touch()
        with self.assertRaisesRegex(bundles.BundleError, 'credential'):
            claude_dream.dream(self.profile())
        output = self.profile()['output']
        self.assertFalse((output / 'bundle').exists())
        with self.assertRaisesRegex(bundles.BundleError, 'credential'):
            claude_dream.pack(self.profile())

    def test_the_wall_time_ends_the_dream_as_timed_out(self):
        (self.bin / 'sleep').touch()
        data = json.loads(self.profile_path.read_text())
        self.profile_path.write_text(json.dumps(dict(data, generation_seconds=2)))
        run = bundles.validate_bundle(claude_dream.dream(self.profile()))['run']
        self.assertEqual(run['outcome']['status'], 'timed_out')
        self.assertEqual(run['outcome']['truncation'], {'truncated': True, 'reason': 'wall time'})
        # An unfinished dream is never admitted; a retry sets it aside and dreams again.
        with self.assertRaisesRegex(bundles.BundleError, 'Only a completed dream'):
            claude_dream.admit(self.profile())
        with self.assertRaisesRegex(bundles.BundleError, '--retry'):
            claude_dream.dream(self.profile())
        (self.bin / 'sleep').unlink()
        self.profile_path.write_text(json.dumps(dict(data, generation_seconds=20)))
        again = bundles.validate_bundle(claude_dream.dream(self.profile(), retry=True))['run']
        self.assertEqual(again['outcome']['status'], 'completed')
        output = self.profile()['output']
        aside = output.with_name(output.name + '.attempt-1')
        self.assertEqual(json.loads((aside / 'bundle/run.json').read_text())['outcome']['status'], 'timed_out')

    def test_the_permission_policy_keeps_tools_inside_the_world(self):
        world = Path(os.path.realpath(self.root)) / 'world'
        world.mkdir()
        (self.root / 'elsewhere').mkdir()
        (world / 'door').symlink_to(self.root / 'elsewhere')
        decide = lambda tool, given: dream_permissions.decide(world, tool, given)[0]
        self.assertTrue(decide('Bash', {'command': 'ls ..; cat ~/.zshrc'}))
        self.assertFalse(decide('Bash', {'command': 'ls', 'dangerouslyDisableSandbox': True}))
        self.assertTrue(decide('Write', {'file_path': str(world / 'a/b.txt')}))
        self.assertTrue(decide('Edit', {'file_path': 'notes.md'}))
        self.assertFalse(decide('Write', {'file_path': str(world / '../outside.txt')}))
        self.assertFalse(decide('Write', {'file_path': str(world / 'door/escape.txt')}))
        self.assertFalse(decide('Read', {'file_path': '/etc/hosts'}))
        self.assertTrue(decide('Glob', {'pattern': '**/*'}))
        self.assertTrue(decide('TaskCreate', {'subject': 'plan'}))
        self.assertFalse(decide('WebFetch', {'url': 'https://example.com'}))
        self.assertFalse(decide('Agent', {'prompt': 'help'}))

    def test_credentials_are_required_and_an_api_key_needs_a_dollar_cap(self):
        os.environ.pop(claude_dream.TOKEN_ENV)
        with self.assertRaisesRegex(bundles.BundleError, 'setup-token'):
            claude_dream.credentials(None)
        os.environ['ANTHROPIC_API_KEY'] = 'stub-key'
        with self.assertRaisesRegex(bundles.BundleError, '--max-usd'):
            claude_dream.credentials(None)
        self.assertEqual(claude_dream.credentials(5)[1], 'api-key')

    def test_the_profile_must_name_the_cohort_prompt(self):
        data = json.loads(self.profile_path.read_text())
        self.profile_path.write_text(
            json.dumps(
                dict(
                    data,
                    prompt='corpus/prompts/'
                    + next(
                        path.name
                        for path in (self.project / 'corpus/prompts').iterdir()
                        if not path.name.startswith('7e785c')
                    ),
                )
            )
        )
        with self.assertRaisesRegex(bundles.BundleError, 'not the cohort prompt'):
            claude_dream.cohort_of(self.profile())


if __name__ == '__main__':
    unittest.main()
