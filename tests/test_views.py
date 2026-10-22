import importlib.util
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from markdown_view import render_markdown, source_link
from evidence_text import extract_readable
from previews import command_arguments


class Tags(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.tags = []
        self.ids = []
        self.links = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        if 'href' in attrs:
            self.links.append(attrs['href'])


class MarkdownTests(unittest.TestCase):
    def test_markdown_is_readable_and_keeps_every_line_addressable(self):
        text = '# A world\n\nA **shared** question with `code`.\n\n- First\n  continues\n- Second\n\n```sh\necho example\n```\n\n| Role | Choice |\n| --- | --- |\n| Artist | Question |'
        rendered = render_markdown(text, line_ids=True)
        tags = Tags(rendered)
        for tag in ['h1', 'strong', 'code', 'ul', 'li', 'pre', 'table', 'th', 'td']:
            self.assertIn(tag, tags.tags)
        line_ids = [i for i in tags.ids if i.startswith('L')]
        self.assertEqual(line_ids, ['L' + str(i) for i in range(1, len(text.splitlines()) + 1)])

    def test_archived_html_and_unsafe_links_stay_inert(self):
        resolve = lambda target: source_link(target, 'README.md', {'README.md', 'WORLD.md'})
        text = (
            '<script>alert(1)</script>\n\n[bad](javascript:alert) ![image](https://remote/image.png) [world](WORLD.md)'
        )
        tags = Tags(render_markdown(text, resolve_link=resolve))
        self.assertNotIn('script', tags.tags)
        self.assertNotIn('img', tags.tags)
        self.assertEqual(tags.links, ['WORLD.md.html'])

    def test_document_links_cannot_leave_the_known_source_set(self):
        for target in ['../../outside', 'file:///secret', '//remote/file', 'https://remote/file', '%2fetc/passwd']:
            self.assertIsNone(source_link(target, 'README.md', {'README.md'}))
        self.assertEqual(
            source_link('../README.md#world', 'memory/origin.md', {'README.md'}, '../'), '../README.md.html#world'
        )

    def test_readable_source_text_drops_code_syntax(self):
        javascript = (
            'const slot = next();\nshowToast("The world made room.");\nreturn template.replace("{text}", shortText);'
        )
        self.assertEqual(extract_readable('app.js', javascript), 'The world made room.')
        self.assertIsNone(extract_readable('styles.css', '.node { color: var(--teal); }'))
        self.assertIsNone(extract_readable('app.js', 'const thought = { id: makeId("thought"), text: text };'))

    def test_shell_sql_and_extensionless_scripts_keep_only_prose(self):
        shell = (
            'echo "Probe a larger harvest."\nassert "$(json_field \'$.completed_steps\')" committed days\n'
            'q="$(query \'SELECT kind FROM head;\')"\n# Give the exhausted soil a day of rest.'
        )
        self.assertEqual(
            extract_readable('tests/check.sh', shell), 'Give the exhausted soil a day of rest.\nProbe a larger harvest.'
        )
        sql = (
            'policy_sql() { # Keys and integer values were checked before SQL generation; prose is quoted.\n'
            ' printf "INSERT INTO input SELECT %s FROM head;"\n}'
        )
        self.assertEqual(
            extract_readable('sql/schema.sql', sql),
            'Keys and integer values were checked before SQL generation; prose is quoted.',
        )
        script = '#!/bin/zsh\nset -eu\n# Tendora keeps a living world in a file.\nROOT=${0:A:h}'
        self.assertEqual(extract_readable('tendora', script), 'Tendora keeps a living world in a file.')
        self.assertIsNone(extract_readable('tendora', '#!/bin/zsh\nprintf "%-10s %s" "${q_state[$id]}" "$id"'))

    def test_manifests_and_template_literals_read_as_text(self):
        manifest = '{"name": "The Hand Remains", "description": "A local-first workbench for questions.", "display": "standalone"}'
        self.assertEqual(
            extract_readable('manifest.webmanifest', manifest),
            'The Hand Remains\nA local-first workbench for questions.',
        )
        template = 'const t = `While no one was here, the world practiced {cycles}.`;'
        self.assertEqual(extract_readable('app.js', template), 'While no one was here, the world practiced {cycles}.')

    def test_readable_source_text_extracts_visible_prose(self):
        markup = '<div aria-label="Open the map"><span>Note to the next mind</span><script>alert("code")</script></div>'
        self.assertEqual(extract_readable('index.html', markup), 'Open the map\nNote to the next mind')
        markdown = '# Continuity\n\nPreserve the **living** memory.\n\n```sh\nrm -rf world\n```'
        self.assertEqual(extract_readable('README.md', markdown), 'Continuity\nPreserve the living memory.')


class ConsoleBoundaryTests(unittest.TestCase):
    def test_artefact_commands_are_arguments_not_a_shell_program(self):
        text = 'ask example "$(touch /tmp/never-run); a literal question"'
        args = command_arguments('astra-max', text)
        self.assertEqual(args, ['ask', 'example', '$(touch /tmp/never-run); a literal question'])

    def test_other_programs_and_unbounded_runs_are_rejected(self):
        for text in ['sh -c echo', 'source /tmp/other.zsh', 'inspect; echo nope', 'run 99999', 'commit ../../file']:
            with self.assertRaises(ValueError):
                command_arguments('astra-garden', text)

    def test_documented_entry_prefixes_work(self):
        self.assertEqual(command_arguments('astra-garden', '. ./tendora.zsh inspect'), ['inspect'])
        self.assertEqual(command_arguments('astra-max', 'zsh ./tendora.zsh status'), ['status'])


if __name__ == '__main__':
    unittest.main()
