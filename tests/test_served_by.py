"""Which providers served a Codex run's calls: asked of the router after the run, recorded by name only."""

import contextlib
import copy
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import bundles  # noqa: E402
import codex_worker as worker  # noqa: E402

TOKEN = 'c' * 64


def session_log(home, calls, name='rollout-fixture.jsonl'):
    log = Path(home) / 'sessions/2026/11/03' / name
    log.parent.mkdir(parents=True, exist_ok=True)
    records = [{'type': 'session_meta', 'payload': {'id': 'fixture'}}]
    records += [{'type': 'token_usage_record', 'payload': {'response_id': call}} for call in calls]
    log.write_text('\n'.join(json.dumps(record) for record in records) + '\n', encoding='utf-8')


class Clock:
    """A clock that moves only when the lookup sleeps."""

    def __init__(self):
        self.now, self.slept = 0.0, []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


class ServedByTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='after-poem-served-by-')
        self.root = Path(self.temporary.name)
        self.transport = self.root / 'transport.config.toml'
        self.transport.write_text('http_headers = { "X-Codex-Router-Token" = "' + TOKEN + '" }\n', encoding='utf-8')

    def tearDown(self):
        self.temporary.cleanup()

    def served(self, homes, ask):
        clock = Clock()
        result = worker.served_by(
            homes, 'http://127.0.0.1:18766/v1', self.transport, ask=ask, sleep=clock.sleep, clock=clock
        )
        return result, clock

    def test_each_call_is_counted_once_across_sessions_and_a_cut_line_is_skipped(self):
        first, second = self.root / 'one', self.root / 'two'
        session_log(first, ['gen-a1', 'gen-b2'])
        session_log(first, ['gen-b2'], name='rollout-resumed.jsonl')  # a resumed thread's log repeats its calls
        session_log(second, ['gen-c3', 'resp_native'])
        with (second / 'sessions/2026/11/03/rollout-fixture.jsonl').open('a') as log:
            log.write('{"type": "token_usage_rec')
        self.assertEqual(worker.call_ids([first, second]), ['gen-a1', 'gen-b2', 'gen-c3', 'resp_native'])
        self.assertEqual(worker.call_ids([self.root / 'never-ran']), [])

    def test_a_pending_call_is_asked_again_until_it_resolves_or_the_wait_ends(self):
        session_log(self.root, ['gen-a1', 'gen-b2', 'gen-c3', 'resp_native'])
        asked = []
        later = {'gen-b2': 2}

        def ask(url, token, call):
            asked.append(call)
            self.assertEqual(token, TOKEN)
            if call == 'gen-a1':
                return 'resolved', 'Fireworks'
            if call == 'gen-b2':
                later['gen-b2'] -= 1
                return ('resolved', 'DeepInfra') if later['gen-b2'] < 0 else ('pending', None)
            return 'pending', None  # gen-c3 never resolves

        result, clock = self.served([self.root], ask)
        self.assertEqual(
            result,
            [
                {'provider': None, 'calls': 2},
                {'provider': 'DeepInfra', 'calls': 1},
                {'provider': 'Fireworks', 'calls': 1},
            ],
        )
        self.assertNotIn('resp_native', asked)  # only OpenRouter generations can be looked up
        self.assertEqual(asked.count('gen-a1'), 1)
        self.assertEqual(asked.count('gen-b2'), 3)
        self.assertTrue(all(gap == worker.LOOKUP_GAP_SECONDS for gap in clock.slept))
        self.assertLessEqual(clock.now, worker.LOOKUP_WAIT_SECONDS)

    def test_an_unreachable_router_leaves_the_rest_unnamed_and_never_fails_the_run(self):
        session_log(self.root, ['gen-a1', 'gen-b2'])

        def ask(url, token, call):
            if call == 'gen-b2':
                raise URLError('connection refused')
            return 'resolved', 'Fireworks'

        result, _ = self.served([self.root], ask)
        self.assertEqual(result, [{'provider': 'Fireworks', 'calls': 1}, {'provider': None, 'calls': 1}])
        self.transport.write_text('no token here', encoding='utf-8')
        result, _ = self.served([self.root], ask)
        self.assertEqual(result, [{'provider': None, 'calls': 2}])

    def test_a_run_without_openrouter_calls_never_asks_or_reads_the_token(self):
        session_log(self.root, ['resp_native'])
        self.transport.unlink()
        result, clock = self.served([self.root], lambda *args: self.fail('asked'))
        self.assertEqual(result, [{'provider': None, 'calls': 1}])
        self.assertEqual(clock.slept, [])

    def test_the_router_is_asked_on_loopback_with_its_token_and_its_answer_is_checked(self):
        answers = {
            'gen-a1': (200, {'id': 'gen-a1', 'status': 'resolved', 'provider': 'DeepInfra'}),
            'gen-b2': (200, {'id': 'gen-b2', 'status': 'pending', 'provider': 'unknown'}),
            'gen-c3': (404, {'error': 'unknown generation'}),
            'gen-d4': (200, {'id': 'gen-d4', 'status': 'resolved', 'provider': 'Deep\nInfra'}),
            'gen-e5': (200, ['not', 'an', 'object']),
            'gen-f6': (200, {'id': 'gen-f6', 'status': 'unknown', 'provider': 'unknown'}),
        }
        seen = []

        class Router(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append((self.path, self.headers.get('X-Codex-Router-Token')))
                code, body = answers[self.path.rsplit('/', 1)[1]]
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_):
                pass

        with contextlib.ExitStack() as stack:
            server = ThreadingHTTPServer(('127.0.0.1', 0), Router)
            stack.callback(server.server_close)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            stack.callback(server.shutdown)
            url = 'http://127.0.0.1:%d/v1' % server.server_address[1]
            told = {call: worker.generation_provider(url, TOKEN, call) for call in answers}
            with self.assertRaises(ValueError):
                worker.generation_provider(url, TOKEN, '../health')
        self.assertEqual(
            told,
            {
                'gen-a1': ('resolved', 'DeepInfra'),
                'gen-b2': ('pending', None),
                'gen-c3': ('unknown', None),
                'gen-d4': ('unknown', None),
                'gen-e5': ('unknown', None),
                'gen-f6': ('unknown', None),
            },
        )
        self.assertEqual({path.rsplit('/', 1)[0] for path, _ in seen}, {'/v1/generations'})
        self.assertEqual({token for _, token in seen}, {TOKEN})


class ReadingTests(unittest.TestCase):
    """A reading asks once, after its last session, over every session's Codex home."""

    def test_every_session_of_a_reading_is_counted(self):
        import scoring_pipeline
        from unittest import mock

        with tempfile.TemporaryDirectory(prefix='after-poem-reading-providers-') as temporary:
            output = Path(temporary)
            (output / 'transport.config.toml').write_text('"X-Codex-Router-Token" = "' + TOKEN + '"\n')
            for session, calls in [(1, ['gen-a1', 'gen-a2']), (2, ['gen-b1']), (3, ['gen-c1'])]:
                session_log(scoring_pipeline._session_dir(output, session) / 'control/codex-home', calls)
            answers = {'gen-a1': 'Fireworks', 'gen-a2': 'Fireworks', 'gen-b1': 'DeepInfra', 'gen-c1': 'Fireworks'}
            ask = mock.patch.object(scoring_pipeline.worker, 'generation_provider')  # the module it calls, if reloaded
            with ask as patched:
                patched.side_effect = lambda u, t, c: ('resolved', answers[c])
                providers = scoring_pipeline.reading_providers(output, 3, 'http://127.0.0.1:18766/v1')
        self.assertEqual(providers, [{'provider': 'Fireworks', 'calls': 3}, {'provider': 'DeepInfra', 'calls': 1}])


class RunRecordTests(unittest.TestCase):
    """run.json may name the providers that served a run; its one reported provider must agree with them."""

    RUN = ROOT / 'data/bundles/dream-deepseek-20261021-deepseek-s1/run.json'

    def check(self, reported):
        record = copy.deepcopy(bundles.load_json(self.RUN))
        record['condition']['reported'] = reported
        return bundles.validate_run_record(record, record['prompt_sha256'])

    def test_named_providers_are_kept_by_name_with_their_calls(self):
        self.check(worker.reported([{'provider': 'DeepInfra', 'calls': 40}]))
        self.check(worker.reported([{'provider': 'DeepInfra', 'calls': 40}, {'provider': None, 'calls': 2}]))
        self.check(worker.reported([]))
        self.check({'model': None, 'provider': None})  # a run from before the router could say

    def test_a_reported_provider_that_the_list_does_not_bear_out_is_refused(self):
        listed = [{'provider': 'DeepInfra', 'calls': 40}, {'provider': 'Fireworks', 'calls': 2}]
        for reported in [
            {'model': None, 'provider': 'DeepInfra', 'providers': listed},
            {'model': None, 'provider': None, 'providers': [{'provider': 'DeepInfra', 'calls': 40}]},
            {'model': None, 'provider': None, 'providers': [{'provider': 'DeepInfra', 'calls': 0}]},
            {'model': None, 'provider': None, 'providers': [{'provider': '', 'calls': 1}]},
            {'model': None, 'provider': None, 'providers': [{'provider': 'A', 'calls': 1, 'id': 'gen-a1'}]},
            {'model': None, 'provider': None, 'providers': listed + [{'provider': 'Fireworks', 'calls': 1}]},
            {'model': None, 'provider': None, 'providers': 'DeepInfra'},
        ]:
            with self.subTest(reported=reported), self.assertRaises(ValueError):
                self.check(reported)


if __name__ == '__main__':
    unittest.main()
