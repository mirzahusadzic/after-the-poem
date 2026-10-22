import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('previews', ROOT / 'scripts/previews.py')
previews = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previews)


class BrowserPreviewTests(unittest.TestCase):
    def test_every_html_document_receives_the_host_policy(self):
        data = {
            'corpora': {
                rid: {
                    'files': [
                        {
                            'path': 'index.html',
                            'text': '<HTML><HEAD data-test="1"><title>x</title></HEAD><BODY></BODY></HTML>',
                        },
                        {
                            'path': 'other/page.html',
                            'text': '<!doctype html><html><head><title>y</title></head></html>',
                        },
                    ]
                }
                for rid in ['sol-original', 'new-browser-run']
            },
            'artefacts': {
                rid: {'kind': 'browser', 'preview': 'previews/' + rid + '/index.html'}
                for rid in ['sol-original', 'new-browser-run']
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)
            previews.build_browser_previews(data, out)
            self.assertTrue((out / 'previews/new-browser-run/index.html').is_file())
            for path in out.glob('previews/*/**/*.html'):
                text = path.read_text()
                self.assertIn('Content-Security-Policy', text)
                self.assertIn('preview-host.js', text)
                self.assertIn("connect-src 'none'", text)
            self.assertIn('../preview-host.js', (out / 'previews/sol-original/other/page.html').read_text())

    def test_preview_requires_explicit_registration_and_cannot_replace_host(self):
        corpus = {'files': [{'path': 'index.html', 'text': '<html><head></head></html>'}]}
        data = {'corpora': {'fixture': corpus}, 'artefacts': {'fixture': {'kind': 'none', 'preview': None}}}
        with tempfile.TemporaryDirectory() as temporary:
            previews.build_browser_previews(data, Path(temporary))
            self.assertEqual(list(Path(temporary).iterdir()), [])
        for entry in [
            'previews/other/index.html',
            'previews/fixture/../../index.html',
            'previews/fixture/missing.html',
        ]:
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                previews.browser_preview_sources('fixture', {'preview': entry}, corpus)
        corpus['files'].append({'path': 'preview-host.js', 'text': 'Do not replace the host'})
        with self.assertRaisesRegex(ValueError, 'collides'):
            previews.browser_preview_sources('fixture', {'preview': 'previews/fixture/index.html'}, corpus)
        corpus['files'].pop()

    def test_html_without_a_head_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'head element'):
            previews._inject_browser_host('<html><body>no head</body></html>', 'index.html')

    def test_native_html_copy_receives_the_escape_host(self):
        injected = previews._inject_native_host('<html><head><title>x</title></head><body></body></html>', 'index.html')
        self.assertIn('<script src="__after_poem_preview_host.js"></script>', injected)
        self.assertLess(injected.index('__after_poem_preview_host.js'), injected.index('</head>'))
        self.assertIn('after-the-poem:escape', previews.BROWSER_HOST)

    def test_browser_host_blocks_external_navigation_and_storage(self):
        script = previews.BROWSER_HOST
        self.assertIn("event.preventDefault()", script)
        self.assertIn("location.protocol === 'file:'", script)
        self.assertIn('Object.defineProperty(window, \'localStorage\'', script)

    def test_preview_host_uses_the_browser_window_not_native_fullscreen(self):
        script = (ROOT / 'src/previews.js').read_text(encoding='utf-8')
        self.assertNotIn('requestFullscreen', script)
        self.assertNotIn('exitFullscreen', script)
        self.assertNotIn('fullscreenchange', script)
        self.assertIn('preview-full-window', script)
        self.assertIn("event.key === 'Escape'", script)

    def test_native_profile_limits_writes_and_executables_to_the_world(self):
        world = Path('/tmp/example-world').resolve()
        profile = previews.sandbox_profile(world, 4318)
        self.assertIn('(subpath "' + str(world) + '")', profile)
        self.assertIn('(allow process-exec (subpath "/usr/bin")', profile)
        self.assertNotIn('(allow process-exec process-fork signal', profile)
        self.assertNotIn('/tmp/example-control', profile)
        self.assertIn('localhost:4318', profile)

    def test_process_group_probe_is_scoped_and_nonfatal(self):
        self.assertGreaterEqual(previews.process_group_count(os.getpgid(0)), 1)
        self.assertEqual(previews.process_group_count(999999999), 0)


@unittest.skipUnless(sys.platform == 'darwin' and Path('/usr/bin/sandbox-exec').is_file(), 'macOS sandbox required')
class NativePreviewBoundaryTests(unittest.TestCase):
    def run_profile(self, profile, args, cwd=None):
        return subprocess.run(
            ['/usr/bin/sandbox-exec', '-f', str(profile)] + args,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=10,
            preexec_fn=previews.child_limits,
        )

    def test_control_files_are_outside_the_candidate_world(self):
        with tempfile.TemporaryDirectory(prefix='after-poem-sandbox-test-') as temporary:
            root = Path(temporary)
            world, control = root / 'world', root / 'control'
            world.mkdir()
            control.mkdir()
            profile = control / 'profile.sb'
            profile.write_text(previews.sandbox_profile(world))
            (world / 'inside.txt').write_text('candidate world')
            (control / 'secret.txt').write_text('host control secret')
            command = (
                '/bin/cat inside.txt; /bin/cat ' + str(control / 'secret.txt') + ' 2>&1; /usr/bin/printf completed'
            )
            result = self.run_profile(profile, ['/bin/sh', '-c', command], cwd=world)
            if 'sandbox_apply' in result.stderr and 'Operation not permitted' in result.stderr:
                self.skipTest('The outer test sandbox does not permit nested sandbox-exec.')
            self.assertIn('candidate world', result.stdout)
            self.assertIn('completed', result.stdout)
            self.assertNotIn('host control secret', result.stdout)

    def test_python_console_runs_only_its_own_commands(self):
        bench = 'dream-claude-opus55-v2-20261022'
        self.assertEqual(previews.CONSOLES[bench][:2], ['/usr/bin/python3', '-I'])
        self.assertEqual(
            previews.command_arguments(bench, 'bin/tend survey worlds/lamp.world'), ['survey', 'worlds/lamp.world']
        )
        self.assertEqual(
            previews.command_arguments(bench, 'probe worlds/jugs.world "big == 4"'),
            ['probe', 'worlds/jugs.world', 'big == 4'],
        )
        for refused in ['receipts --ledger /tmp/x', 'new x --dir=/tmp', 'python3 -c "print(1)"', 'survey; rm -rf .']:
            with self.assertRaises(ValueError, msg=refused):
                previews.command_arguments(bench, refused)

    def test_only_a_named_tree_is_added_to_what_may_execute(self):
        world = Path('/tmp/example-world').resolve()
        self.assertNotIn('CommandLineTools', previews.sandbox_profile(world))
        profile = previews.sandbox_profile(world, executables=['/Library/Developer/CommandLineTools'])
        self.assertIn('(allow process-exec file-read* (subpath "/Library/Developer/CommandLineTools"))', profile)
        self.assertNotIn('network', profile)

    def test_candidate_tree_cannot_supply_an_executable(self):
        with tempfile.TemporaryDirectory(prefix='after-poem-sandbox-test-') as temporary:
            root = Path(temporary)
            world, control = root / 'world', root / 'control'
            world.mkdir()
            control.mkdir()
            profile = control / 'profile.sb'
            profile.write_text(previews.sandbox_profile(world))
            executable = world / 'candidate-command'
            executable.write_text('#!/bin/sh\nprintf escaped\n')
            executable.chmod(0o755)
            result = self.run_profile(profile, [str(executable)], cwd=world)
            if 'sandbox_apply' in result.stderr and 'Operation not permitted' in result.stderr:
                self.skipTest('The outer test sandbox does not permit nested sandbox-exec.')
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('escaped', result.stdout)


if __name__ == '__main__':
    unittest.main()
