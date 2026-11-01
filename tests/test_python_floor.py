"""The Python floor is Gadfly's, the one package is pinned, and every way in uses the pinned Python."""

import ast
import importlib.metadata
import json
import re
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import python_floor  # noqa: E402


def read(path):
    return (ROOT / path).read_text(encoding='utf-8')


class PythonFloorTests(unittest.TestCase):
    def test_the_floor_is_midwifes(self):
        project = tomllib.loads(read('pyproject.toml'))['project']
        floor = '>=%d.%d' % python_floor.FLOOR
        self.assertEqual(project['requires-python'], floor)
        self.assertEqual(importlib.metadata.metadata('midwife')['Requires-Python'], floor)
        self.assertEqual(read('.python-version').strip(), '%d.%d' % python_floor.FLOOR)
        self.assertGreaterEqual(sys.version_info[:2], python_floor.FLOOR)

    def test_one_package_pinned_from_pypi_by_hash(self):
        project = tomllib.loads(read('pyproject.toml'))['project']
        self.assertEqual(len(project['dependencies']), 1)
        name, version = project['dependencies'][0].split('==')
        self.assertEqual(name, 'midwife')
        lock = tomllib.loads(read('uv.lock'))
        [locked] = [package for package in lock['package'] if package['name'] == 'midwife']
        self.assertEqual(locked['version'], version)
        self.assertEqual(locked['source'], {'registry': 'https://pypi.org/simple'})
        self.assertTrue(all(wheel['hash'].startswith('sha256:') for wheel in locked['wheels']))
        self.assertEqual(importlib.metadata.version('midwife'), version)

    def test_an_older_python_stops_with_a_reason(self):
        with self.assertRaisesRegex(SystemExit, r'needs Python 3\.13 or later; this is 3\.9'):
            python_floor.check((3, 9, 6))
        python_floor.check(python_floor.FLOOR)
        source = read('scripts/fsutil.py')
        first = next(node for node in ast.parse(source).body if isinstance(node, (ast.Import, ast.ImportFrom)))
        self.assertEqual([alias.name for alias in first.names], ['python_floor'])

    def test_npm_scripts_run_python_through_uv(self):
        scripts = json.loads(read('package.json'))['scripts']
        for name, command in scripts.items():
            self.assertNotRegex(command, r'(^|&& |-- )python3? ', name + ' runs an unpinned python')
        self.assertTrue(scripts['test'].startswith('uv run python '))

    def test_node_scripts_that_start_python_run_under_uv(self):
        # A smoke test that starts `python3` itself gets the venv's Python only when node runs under `uv run`,
        # directly or through a script uv started; otherwise it finds whatever python3 the machine has.
        scripts = json.loads(read('package.json'))['scripts']
        starts_python = {
            path.name for path in (ROOT / 'tests').glob('*.mjs') if re.search(r"['\"]python3?['\"]", path.read_text())
        }
        self.assertTrue(starts_python)
        for name, command in scripts.items():
            # Split at the top level only: an `&&` inside a quoted `sh -c` runs under whatever started the shell.
            steps = re.split(r'&&(?=(?:[^"]*"[^"]*")*[^"]*$)', command)
            for step in steps:
                for script in re.findall(r'node tests/([\w-]+\.mjs)', step):
                    if script in starts_python:
                        self.assertTrue(step.strip().startswith('uv run '), '%s: %s' % (name, step.strip()))

    def test_workflows_use_the_floor_through_a_locked_uv(self):
        for path in ('.github/workflows/ci.yml', '.github/workflows/pages.yml'):
            text = read(path)
            self.assertNotIn('python3 ', text, path)
            self.assertEqual(set(re.findall(r"python-version: '([^']+)'", text)), {'%d.%d' % python_floor.FLOOR})
            self.assertIn("UV_LOCKED: '1'", text, path)
            jobs = re.split(r'\n  (?=[A-Za-z][\w-]*:\n)', text.split('\njobs:\n', 1)[1])
            running = [job for job in jobs if 'npm ' in job or 'uv run' in job]
            self.assertTrue(running, path)
            for job in running:
                self.assertIn('astral-sh/setup-uv', job, path + ': ' + job.split(':', 1)[0])

    def test_only_the_gadfly_bridge_imports_midwife(self):
        importers = []
        for path in sorted((ROOT / 'scripts').glob('*.py')):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                names = (
                    [alias.name for alias in node.names] if isinstance(node, ast.Import)
                    else [node.module or ''] if isinstance(node, ast.ImportFrom) else []
                )
                if any(name.split('.')[0] == 'midwife' for name in names):
                    importers.append(path.name)
                    break
        self.assertEqual(importers, ['gadfly_bridge.py'])

    def test_docs_state_the_floor(self):
        for path in ('README.md', 'CONTRIBUTING.md', 'AGENTS.md', 'docs/environment.md', 'docs/pipeline.md'):
            text = read(path)
            self.assertNotRegex(text, r'Python 3\.9|3\.9\+', path)
            self.assertRegex(text, r'3\.13', path)


if __name__ == '__main__':
    unittest.main()
