"""Security hardening tests for opt-in previews (loopback only, no execution)."""
import http.client
import importlib.util
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))


def _load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


previews = _load('previews', 'scripts/previews.py')
dream_permissions = _load('dream_permissions', 'scripts/dream_permissions.py')

HOST = '127.0.0.1:4317'


class StatusSignalTests(unittest.TestCase):
    def payload(self, headers):
        manager = previews.PreviewManager(Path('/tmp'), False)
        allowed = previews._preview_status_allowed(headers, HOST)
        return allowed, previews._preview_status_payload(manager, allowed)

    def test_same_origin_fetch_is_allowed(self):
        allowed, (code, body) = self.payload({'Sec-Fetch-Site': 'same-origin'})
        self.assertTrue(allowed)
        self.assertEqual(code, 200)
        self.assertIn('token', body)

    def test_origin_fallback_keeps_same_origin_fetch_working(self):
        for headers in (
            {'Origin': 'http://' + HOST},
            {'Referer': 'http://' + HOST + '/report/index.html'},
        ):
            with self.subTest(headers=headers):
                allowed, (code, body) = self.payload(headers)
                self.assertTrue(allowed)
                self.assertEqual(code, 200)
                self.assertIn('token', body)

    def test_bare_and_foreign_requests_get_no_token(self):
        cases = (
            {},
            {'Sec-Fetch-Site': 'none'},
            {'Sec-Fetch-Site': 'cross-site'},
            {'Sec-Fetch-Site': 'same-site'},
            {'Origin': 'http://evil.invalid'},
            {'Referer': 'http://evil.invalid/'},
            {'Sec-Fetch-Site': 'same-origin', 'Origin': 'http://evil.invalid'},
        )
        for headers in cases:
            with self.subTest(headers=headers):
                allowed, (code, body) = self.payload(headers)
                self.assertFalse(allowed)
                self.assertEqual(code, 403)
                self.assertNotIn('token', body)
                self.assertIn('error', body)



class LiveServerTests(unittest.TestCase):
    """The real report server on an ephemeral loopback port, previews disabled: nothing ever starts."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='after-poem-preview-server-')
        out = Path(cls.temporary.name)
        (out / 'index.html').write_text('<!doctype html><title>report</title>', encoding='utf-8')
        (out / 'sources').mkdir()
        (out / 'sources/page.html').write_text('<p>source</p>', encoding='utf-8')
        cls.server, cls.manager = previews.make_server(out, out, 0, False)
        cls.server.RequestHandlerClass.log_message = lambda *args: None
        cls.port = cls.server.server_address[1]
        cls.host = '127.0.0.1:%d' % cls.port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temporary.cleanup()

    def request(self, method, path, headers=None, body=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        try:
            connection.request(method, path, body=body, headers=dict({'Host': self.host}, **(headers or {})))
            response = connection.getresponse()
            return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
        finally:
            connection.close()

    def test_status_hands_the_token_only_to_the_report_itself(self):
        report = 'http://' + self.host
        for headers in ({'Sec-Fetch-Site': 'same-origin'}, {'Referer': report + '/index.html'}, {'Origin': report}):
            with self.subTest(headers=headers):
                status, _, body = self.request('GET', '/_preview/status', headers)
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body)['token'], self.manager.token)
        for headers in (
            {},
            {'Sec-Fetch-Site': 'none'},
            {'Sec-Fetch-Site': 'cross-site'},
            {'Referer': 'http://evil.invalid/'},
            {'Host': 'evil.invalid', 'Sec-Fetch-Site': 'same-origin'},
        ):
            with self.subTest(headers=headers):
                status, _, body = self.request('GET', '/_preview/status', headers)
                self.assertEqual(status, 403)
                self.assertNotIn(self.manager.token.encode(), body)

    def test_actions_need_the_report_origin_and_its_token(self):
        def start(token, headers):
            payload = json.dumps({'token': token, 'run_id': 'astra-garden'}).encode()
            return self.request(
                'POST', '/_preview/start', dict({'Content-Type': 'application/json'}, **headers), payload
            )[0]

        origin = {'Origin': 'http://' + self.host}
        self.assertEqual(start(self.manager.token, {}), 403)
        self.assertEqual(start(self.manager.token, {'Origin': 'http://evil.invalid'}), 403)
        self.assertEqual(start('wrong-token', origin), 403)
        # The right origin and token reach the manager, which refuses: previews are disabled here.
        self.assertEqual(start(self.manager.token, origin), 400)
        self.assertEqual(self.manager.sessions, {})

    def test_files_are_served_with_hardening_headers_and_folders_are_never_listed(self):
        status, headers, _ = self.request('GET', '/sources/page.html')
        self.assertEqual(status, 200)
        self.assertEqual(headers['x-content-type-options'], 'nosniff')
        self.assertEqual(headers['referrer-policy'], 'no-referrer')
        self.assertEqual(self.request('GET', '/sources/')[0], 404)


class StaticServerHardeningTests(unittest.TestCase):
    def test_capture_server_sends_hardening_headers_without_listings(self):
        source = (ROOT / 'scripts/capture_page.mjs').read_text()
        self.assertIn("'X-Content-Type-Options': 'nosniff'", source)
        self.assertIn("'Referrer-Policy': 'no-referrer'", source)
        self.assertIn("form-action 'none'", source)
        # Files only: a directory never renders a listing.
        self.assertIn('readFile(path)', source)


class ConsoleSanitizationTests(unittest.TestCase):
    def test_osc8_hyperlink_text_survives_without_the_url(self):
        evil = 'see \x1b]8;;https://evil.invalid/\x07this\x1b]8;;\x07 end'
        clean = previews._sanitize_console_output(evil)
        self.assertIn('this', clean)
        self.assertNotIn('evil.invalid', clean)
        self.assertNotIn('\x1b]', clean)

    def test_other_osc_strings_are_stripped(self):
        clean = previews._sanitize_console_output('a\x1b]0;title\x07b\x1b]99;data\x07c')
        self.assertEqual(clean, 'abc')

    def test_dangling_osc_without_terminator_is_dropped(self):
        clean = previews._sanitize_console_output('ok\x1b]8;;https://evil.invalid/')
        self.assertEqual(clean, 'ok')

    def test_sgr_colour_survives_while_other_csi_goes(self):
        coloured = '\x1b[38;5;14mworld › \x1b[0m'
        clean = previews._sanitize_console_output(coloured + '\x1b[2K\x1b[1A\x1b[?25l')
        self.assertIn(coloured, clean)
        self.assertNotIn('[2K', clean)
        self.assertNotIn('[1A', clean)

    def test_newline_tab_and_carriage_return_survive(self):
        clean = previews._sanitize_console_output('a\tb\nc\rd')
        self.assertEqual(clean, 'a\tb\nc\rd')

    def test_lone_escapes_and_controls_go(self):
        clean = previews._sanitize_console_output('a\x1bXb\x00c\x07d\x7fe')
        self.assertEqual(clean, 'aXbcde')


class FormInterceptionTests(unittest.TestCase):
    def test_every_preview_page_carries_an_enforced_form_action_limit(self):
        # The injected host's own interception is exercised in a browser by tests/ui-smoke.mjs; the limit
        # the browser enforces is pinned here for both kinds of world.
        self.assertIn("form-action 'none'", previews.BROWSER_POLICY)
        self.assertEqual(previews.NATIVE_POLICY, "form-action 'self'")

    def test_capture_frame_blocks_external_submits(self):
        source = (ROOT / 'scripts/capture_page.mjs').read_text()
        self.assertIn('addInitScript', source)
        self.assertIn("addEventListener(\n    'submit'", source)
        self.assertIn('.origin !== location.origin', source)
        self.assertIn('stopImmediatePropagation()', source)

    def test_external_form_fixture_keeps_the_injected_host(self):
        form = (
            '<html><head><title>t</title></head><body>'
            '<form action="https://evil.invalid/collect" method="post">'
            '<input type="text" name="x"></form></body></html>'
        )
        corpus = {'files': [{'path': 'index.html', 'text': form}]}
        data = {
            'corpora': {'fixture': corpus},
            'artefacts': {'fixture': {'kind': 'browser', 'preview': 'previews/fixture/index.html'}},
        }
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)
            previews.build_browser_previews(data, out)
            rendered = (out / 'previews/fixture/index.html').read_text()
            self.assertIn('preview-host.js', rendered)
            self.assertIn("form-action 'none'", rendered)
            host = (out / 'previews/fixture/preview-host.js').read_text()
            self.assertIn("addEventListener('submit'", host)


class SandboxAttributeTests(unittest.TestCase):
    # The report frontend (src/previews.js) owns the iframe elements; this
    # module owns the host policy they pair with, so the exact values are
    # pinned here:
    # - browser artefacts run opaque (no allow-same-origin) and cannot reach
    #   the report origin; downloads stay permitted.
    # - server artefacts run on their own local origin and need
    #   allow-same-origin for their own resources, fenced off from the report
    #   origin by the origin check before the frame source is set.
    BROWSER_SANDBOX = 'allow-scripts allow-forms allow-downloads'
    SERVER_SANDBOX = 'allow-scripts allow-forms allow-same-origin allow-downloads'

    def test_exact_sandbox_attributes(self):
        frontend = (ROOT / 'src/previews.js').read_text()
        browser = re.findall(r"setAttribute\('sandbox', '([^']+)'\)", frontend)
        self.assertIn(self.BROWSER_SANDBOX, browser)
        self.assertIn(self.SERVER_SANDBOX, browser)


class DreamSymlinkTests(unittest.TestCase):
    def test_symlink_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            world = (root / 'world').resolve()
            world.mkdir()
            secret = root / 'secret.txt'
            secret.write_text('outside')
            link = world / 'link.txt'
            try:
                os.symlink(secret, link)
            except OSError as error:
                self.skipTest('symlinks unavailable: %s' % error)
            self.assertFalse(dream_permissions.inside(world, str(link)))
            self.assertFalse(dream_permissions.inside(world, 'link.txt'))
            allowed, _ = dream_permissions.decide(world, 'Read', {'file_path': str(link)})
            self.assertFalse(allowed)
            allowed, _ = dream_permissions.decide(world, 'Read', {'file_path': 'link.txt'})
            self.assertFalse(allowed)


if __name__ == '__main__':
    unittest.main()
