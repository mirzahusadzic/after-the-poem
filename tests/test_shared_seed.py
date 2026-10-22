import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import atlas
import page_prepare


CODEX = 'You are Codex inhabiting a Tendora dream.\nThe same poem.\n'
CLAUDE = 'You are Claude Code inhabiting a Tendora dream.\nThe same poem.\n'


class SharedSeedTests(unittest.TestCase):
    """A cohort joins another's workbench only when its prompt changes nothing but the harness named in line 1."""

    def check(self, child_prompt, parent='a', cohorts=None):
        cohorts = cohorts or {'a': {'id': 'a'}, 'b': {'id': 'b', 'seed_of': parent}}
        atlas.validate_shared_seed('b', cohorts['b'], cohorts, {'a': CODEX, 'b': child_prompt})

    def test_only_the_harness_name_may_differ(self):
        self.check(CLAUDE)
        self.assertEqual(
            atlas.harness_line_difference(CLAUDE.split('\n')[0], CODEX.split('\n')[0]), ['Claude Code', 'Codex']
        )
        for broken in [
            CLAUDE.replace('The same poem.', 'Another poem.'),
            CODEX,
            'Ignore the poem; build a tool.\nThe same poem.\n',
        ]:
            with self.assertRaises(ValueError):
                self.check(broken)

    def test_a_shared_seed_names_a_registered_cohort_without_nesting(self):
        with self.assertRaises(ValueError):
            self.check(CLAUDE, parent='missing')
        with self.assertRaises(ValueError):
            self.check(CLAUDE, cohorts={'a': {'id': 'a', 'seed_of': 'b'}, 'b': {'id': 'b', 'seed_of': 'a'}})

    def test_the_study_joins_the_claude_code_prompt_to_the_first_cohort(self):
        data = atlas.validate(ROOT)
        cohort = next(item for item in data['cohorts'] if item['id'] == 'dream-4521-claude-code-v1')
        self.assertEqual(cohort['seed_of'], 'dream-4521-v1')

    def test_the_page_receives_agreement_spread_and_the_case_for_other_levels(self):
        package = ROOT / 'data/evaluations/dream-claude-opus55-v2-20261022-muse-v5-1'
        rubric = atlas.validate(ROOT)['rubric']
        details = atlas.reading_details(package, rubric)
        self.assertEqual((details['judgment']['agreeing'], details['judgment']['sessions']), (2, 3))
        self.assertEqual(sorted(set(details['judgment']['levels'])), [3, 4])
        self.assertEqual(details['relations']['agreeing'], 3)
        self.assertIn(details['judgment']['reader_confidence'], ['low', 'medium', 'high'])
        self.assertTrue(details['judgment']['other_levels'])
        self.assertNotIn('Other levels:', details['judgment']['other_levels'])

    def test_who_can_do_what_counts_a_gesture_held_in_most_sessions(self):
        summary = atlas.standing_summary(ROOT / 'data/evaluations/sol56-max-muse-v5-1')
        self.assertEqual(summary['sessions'], 3)
        kinds = {item['kind']: item['gestures'] for item in summary['participants']}
        self.assertEqual(kinds['intelligence'], [])
        self.assertIn('question', kinds['person'])
        self.assertEqual(list(kinds), [kind for kind in atlas.STANDING_ORDER if kind in kinds])


class WithheldPathTests(unittest.TestCase):
    """A public quote withholds a host path a world wrote into itself; the preserved source keeps it."""

    def test_host_paths_in_quotes_are_withheld_and_punctuation_stays(self):
        line = '{"body":{"text":"World materialized at /private/tmp/after-the-poem-dream-x/world."}}'
        self.assertEqual(
            page_prepare.HOST_PATH_TEXT.sub(page_prepare.WITHHELD_PATH, line),
            '{"body":{"text":"World materialized at [a path on the dreaming machine]."}}',
        )
        for kept in ['docs/tmp/x', 'a/home/b', 'https://example.org/tmp/x']:
            self.assertEqual(page_prepare.HOST_PATH_TEXT.sub(page_prepare.WITHHELD_PATH, kept), kept)


if __name__ == '__main__':
    unittest.main()
