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

    def test_router_links_use_its_current_repository(self):
        # The router's repository was renamed; records keep its old name as their gateway, links don't.
        stale = [
            str(path.relative_to(ROOT))
            for path in DOCS
            if 'github.com/mirzahusadzic/codex-unified-model-router' in path.read_text(encoding='utf-8')
        ]
        self.assertEqual(stale, [])

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
        self.assertIn('Nine programs', text)
        for name in [
            'evaluate.py',
            'claude_dream.py',
            'pipeline.py',
            'load_bearing.py',
            'gadfly_bridge.py',
            'reading_controls.py',
            'record_consoles.py',
            'atlas.py',
            'bundles.py',
        ]:
            self.assertIn('`' + name + '`', text)
        self.assertIn('### pipeline.py', text)

    def test_docs_describe_split_sessions_as_the_page_draws_them(self):
        # The page plots a split as the sessions' mean in thirds and names the quorum level beside it.
        for name in ('docs/study.md', 'docs/concepts.md', 'docs/release.md'):
            text = (ROOT / name).read_text(encoding='utf-8')
            self.assertIn('3⅓', text, name)
            self.assertNotIn('tick', text, name)

    def test_every_codex_release_a_finding_ran_through_is_in_the_tested_matrix(self):
        # Gadfly round 1 read through a later Codex CLI than the published readings; the docs name both.
        study = (ROOT / 'docs/study.md').read_text(encoding='utf-8')
        named = set(re.findall(r'Codex CLI (\d+\.\d+\.\d+)', study))
        self.assertTrue({'0.163.0', '0.166.0'} <= named, named)
        for name in ('docs/environment.md', 'docs/pipeline.md'):
            text = (ROOT / name).read_text(encoding='utf-8')
            for version in named:
                self.assertIn(version, text, '%s does not name Codex CLI %s' % (name, version))

    def test_the_limits_state_how_close_the_shown_levels_sit(self):
        # Counted from the readings the page shows, so a new or revised reading fails here until the sentence moves.
        presentation = json.loads((ROOT / 'data/presentation.json').read_text(encoding='utf-8'))
        suffix = presentation['reader']['evaluation_suffix']
        levels = {}
        for run in presentation['shown_runs']:
            evaluation = json.loads(
                (ROOT / 'data/evaluations' / (run + suffix) / 'evaluation.json').read_text(encoding='utf-8')
            )
            for outcome in evaluation['outcomes']:
                levels.setdefault(outcome['node_id'].split('-level')[0], []).append(outcome['score'])
        modal = sum(max(values.count(v) for v in set(values)) for values in levels.values())
        total = sum(len(values) for values in levels.values())
        labels = {a['id']: a['label'].lower() for a in json.loads((ROOT / 'data/rubric.json').read_text())['axes']}
        study = (ROOT / 'docs/study.md').read_text(encoding='utf-8')
        sentence = '%d of the %d levels shown' % (modal, total)
        line = next((line for line in study.splitlines() if sentence in line), None)
        self.assertIsNotNone(line, 'the Limits do not say: ' + sentence)
        self.assertEqual(len(presentation['shown_runs']), 9, 'the sentence counts nine worlds')
        self.assertIn('all nine worlds', line)
        for axis, values in levels.items():
            if len(set(values)) == 1:
                self.assertIn(labels[axis], line, axis)
            else:
                self.assertNotIn(labels[axis], line, axis)

    def test_doc_pages_keep_the_house_heading_shape(self):
        # GitHub underlines # and ##: the title is #, each section is ### under a --- rule, subsections go deeper,
        # and some text always sits between two headings.
        pages = sorted(ROOT.glob('*.md')) + sorted((ROOT / 'docs').glob('*.md'))
        self.assertGreater(len(pages), 10)
        for page in pages:
            lines, fence, failures = page.read_text(encoding='utf-8').split('\n'), False, []
            for i, line in enumerate(lines):
                if line.startswith('```'):
                    fence = not fence
                if fence or not line.startswith('#'):
                    continue
                level = len(line) - len(line.lstrip('#'))
                if i == 0:
                    if level != 1:
                        failures.append('the title is not #')
                    continue
                if level < 3:
                    failures.append('line %d: a section heading above ###' % (i + 1))
                if level == 3 and lines[max(i - 2, 0) : i] != ['---', '']:
                    failures.append('line %d: no --- rule above the section' % (i + 1))
                previous = next((lines[j] for j in range(i - 1, -1, -1) if lines[j].strip() not in ('', '---')), '')
                if previous.startswith('#'):
                    failures.append('line %d: two headings with no text between' % (i + 1))
            self.assertEqual(failures, [], page.name)

    def test_review_manifest_is_documented(self):
        records = (ROOT / 'docs/records.md').read_text(encoding='utf-8')
        release = (ROOT / 'docs/release.md').read_text(encoding='utf-8')
        self.assertIn('data/review-manifest.json', records)
        self.assertIn('data/review-manifest.json', release)


if __name__ == '__main__':
    unittest.main()
