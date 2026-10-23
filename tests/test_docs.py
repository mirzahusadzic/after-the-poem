"""Documentation contracts: links resolve, commands exist and concepts stay aligned."""

import json
import re
import unittest
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
DOCS = [
    ROOT / 'README.md',
    ROOT / 'CONTRIBUTING.md',
    ROOT / 'SECURITY.md',
    ROOT / 'LICENSING.md',
    ROOT / 'LICENSES-DATA.md',
    ROOT / 'THIRD_PARTY_NOTICES.md',
]
DOCS += sorted((ROOT / 'docs').glob('*.md'))


def without_fenced_code(text):
    return re.sub(r'```.*?```', '', text, flags=re.DOTALL)


def github_slug(heading):
    value = heading.strip().lower().replace('`', '')
    value = re.sub(r'[^\w\s-]', '', value, flags=re.UNICODE)
    return re.sub(r'\s+', '-', value).strip('-')


def anchors(path):
    seen, result = {}, set()
    for line in path.read_text(encoding='utf-8').splitlines():
        match = re.match(r'^(#{1,6})\s+(.+?)\s*$', line)
        if not match:
            continue
        slug = github_slug(match.group(2))
        seen[slug] = seen.get(slug, 0) + 1
        result.add(slug if seen[slug] == 1 else slug + '-' + str(seen[slug] - 1))
    return result


class DocumentationTests(unittest.TestCase):
    def test_internal_markdown_links_resolve(self):
        failures = []
        for path in DOCS:
            text = without_fenced_code(path.read_text(encoding='utf-8'))
            for target in re.findall(r'(?<!!)\[[^\]]+\]\(([^)]+)\)', text):
                target = target.strip().split('"', 1)[0].strip('<>')
                if re.match(r'^[a-z][a-z0-9+.-]*:', target, re.IGNORECASE) or target.startswith('/'):
                    continue
                relative, _, fragment = target.partition('#')
                destination = path if not relative else (path.parent / unquote(relative)).resolve()
                if not destination.exists():
                    failures.append('%s -> %s' % (path.relative_to(ROOT), target))
                    continue
                if fragment and destination.suffix == '.md' and fragment not in anchors(destination):
                    failures.append('%s -> %s (missing anchor)' % (path.relative_to(ROOT), target))
        self.assertEqual(failures, [])

    def test_documented_npm_scripts_exist(self):
        package = json.loads((ROOT / 'package.json').read_text(encoding='utf-8'))
        scripts = set(package['scripts'])
        failures = []
        for path in DOCS:
            for name in re.findall(r'\bnpm run ([a-z][a-z0-9:-]*)', path.read_text(encoding='utf-8')):
                if name not in scripts:
                    failures.append('%s -> npm run %s' % (path.relative_to(ROOT), name))
        self.assertEqual(failures, [])

    def test_commands_reference_includes_every_runnable_program(self):
        text = (ROOT / 'docs/commands.md').read_text(encoding='utf-8')
        self.assertIn('Eight programs', text)
        for name in [
            'evaluate.py',
            'claude_dream.py',
            'pipeline.py',
            'load_bearing.py',
            'reading_controls.py',
            'record_consoles.py',
            'atlas.py',
            'bundles.py',
        ]:
            self.assertIn('`' + name + '`', text)
        self.assertIn('## pipeline.py', text)

    def test_docs_describe_split_sessions_as_the_page_draws_them(self):
        # The page plots a split as the sessions' mean in thirds and names the quorum level beside it.
        for name in ('docs/study.md', 'docs/concepts.md', 'docs/release.md'):
            text = (ROOT / name).read_text(encoding='utf-8')
            self.assertIn('3⅓', text, name)
            self.assertNotIn('tick', text, name)

    def test_review_manifest_is_documented(self):
        records = (ROOT / 'docs/records.md').read_text(encoding='utf-8')
        release = (ROOT / 'docs/release.md').read_text(encoding='utf-8')
        self.assertIn('data/review-manifest.json', records)
        self.assertIn('data/review-manifest.json', release)


if __name__ == '__main__':
    unittest.main()
