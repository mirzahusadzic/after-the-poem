import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import load_bearing
import scoring_pipeline
from test_scoring_pipeline import MODEL, rubric_policy_file


PREPARED = {
    'sources': [
        {'source_id': 'source-0001', 'path': 'a.md', 'lines': ['one', 'two', 'three', '', 'five']},
        {'source_id': 'source-0002', 'path': 'b.js', 'lines': ['x'] * 12},
    ],
    'source_texts': {'a.md': 'one\ntwo\nthree\n\nfive\n', 'b.js': 'x\n' * 12},
}


class AblationTests(unittest.TestCase):
    """The ablation control blanks declared lines in what the reader sees and nothing else."""

    def test_ablation_blanks_lines_and_keeps_numbering_and_sources(self):
        ablated = scoring_pipeline.ablate_evidence(PREPARED, [{'path': 'a.md', 'start': 2, 'end': 3}])
        self.assertEqual(ablated['sources'][0]['lines'], ['one', '', '', '', 'five'])
        self.assertEqual(ablated['sources'][1]['lines'], PREPARED['sources'][1]['lines'])
        self.assertEqual(ablated['source_texts'], PREPARED['source_texts'])
        self.assertEqual(PREPARED['sources'][0]['lines'][1], 'two')

    def test_ablation_rejects_ranges_outside_the_evidence(self):
        for ranges in [
            [],
            [{'path': 'c.md', 'start': 1, 'end': 1}],
            [{'path': 'a.md', 'start': 0, 'end': 1}],
            [{'path': 'a.md', 'start': 3, 'end': 2}],
            [{'path': 'a.md', 'start': 1, 'end': 6}],
            [{'path': 'a.md', 'start': 1, 'end': 1, 'why': 'x'}],
        ]:
            with self.assertRaises(ValueError):
                scoring_pipeline.ablate_evidence(PREPARED, ranges)

    def test_an_ablated_reading_is_a_control_and_never_admitted(self):
        with tempfile.TemporaryDirectory(prefix='after-poem-ablate-') as temporary:
            root = Path(temporary)
            (root / 'ranges.json').write_text(json.dumps([{'path': 'WORLD.md', 'start': 30, 'end': 39}]))
            scoring_pipeline.run(
                ROOT,
                rubric_policy_file(root),
                root / 'out',
                run_id='sol-original',
                model=MODEL,
                dry_run=True,
                ablated_ranges_path=root / 'ranges.json',
            )
            self.assertEqual(
                json.loads((root / 'out/status.json').read_text())['control'], 'ablated evidence; never admitted'
            )
            prompt = (root / 'out/sessions/01/turns/001/prompt.txt').read_text()
            self.assertIn('28|## Living edge\n29|\n30|\n31|\n', prompt)
            self.assertIn('39|\n=== SOURCE', prompt)


class LoadBearingTests(unittest.TestCase):
    """Plans are declared before trials; shams avoid cited lines; findings follow the categories."""

    def test_ranges_merge_and_shams_avoid_every_citation(self):
        self.assertEqual(
            load_bearing.merged(
                [
                    {'path': 'b.js', 'start': 4, 'end': 6},
                    {'path': 'b.js', 'start': 1, 'end': 3},
                    {'path': 'a.md', 'start': 2, 'end': 2},
                ]
            ),
            [{'path': 'a.md', 'start': 2, 'end': 2}, {'path': 'b.js', 'start': 1, 'end': 6}],
        )
        ablate = [{'path': 'b.js', 'start': 3, 'end': 4}]
        exclude = ablate + [{'path': 'b.js', 'start': 7, 'end': 12}]
        sham = load_bearing.sham_ranges(PREPARED, ablate, exclude, 7)
        self.assertEqual(sham, load_bearing.sham_ranges(PREPARED, ablate, exclude, 7))
        self.assertEqual([item['end'] - item['start'] for item in sham], [1])
        self.assertFalse(any(load_bearing.overlaps(a, b) for a in sham for b in exclude))
        # A file with no room sends the block elsewhere; a block that fits nowhere stops the plan.
        moved = load_bearing.sham_ranges(PREPARED, ablate, [{'path': 'b.js', 'start': 1, 'end': 12}], 7)
        self.assertEqual(moved[0]['path'], 'a.md')
        with self.assertRaisesRegex(ValueError, 'No uncited block'):
            load_bearing.sham_ranges(
                PREPARED, [{'path': 'b.js', 'start': 1, 'end': 9}], [{'path': 'b.js', 'start': 1, 'end': 9}], 7
            )

    def test_a_sham_never_removes_only_blank_lines(self):
        prepared = {'sources': [{'path': 'a.md', 'lines': ['', '', 'x', '']}]}
        for seed in range(20):
            self.assertEqual(
                load_bearing.sham_ranges(
                    prepared, [{'path': 'a.md', 'start': 1, 'end': 1}], [{'path': 'a.md', 'start': 1, 'end': 1}], seed
                ),
                [{'path': 'a.md', 'start': 3, 'end': 3}],
            )

    def test_findings_follow_the_declared_categories(self):
        def category(b, r, p, a, s):
            return load_bearing.finding({'baseline': b, 'repeat': r, 'presentation': p, 'ablation': a, 'sham': s})[0]

        self.assertEqual(category(3, 3, 3, 1, 3), 'carried')
        self.assertEqual(category(3, 3, 2, 1, 3), 'coupled but misaligned')
        self.assertEqual(category(3, 3, 3, 4, 3), 'coupled but misaligned')
        self.assertEqual(category(3, 3, 3, 3, 3), 'cooled')
        self.assertEqual(category(3, 3, 3, 1, 2), 'unresolved')
        self.assertEqual(category(3, 2, 3, 1, 3), 'unresolved')
        self.assertEqual(category(3, 3, None, 1, 3), 'unresolved')
        self.assertEqual(category(None, 3, 3, 1, 3), 'unresolved')

    def test_rules_2_need_unanimous_baseline_and_repeat_sessions(self):
        levels = {'baseline': 3, 'repeat': 3, 'presentation': 3, 'ablation': 1, 'sham': 3}
        samples = {'baseline': ['level-3'] * 3, 'repeat': ['level-4', 'level-3', 'level-3']}
        self.assertEqual(load_bearing.finding(levels, samples, 1)[0], 'carried')
        category, reason = load_bearing.finding(levels, samples, 2)
        self.assertEqual(category, 'unresolved')
        self.assertIn('3,3,3,4,3,3', reason)
        samples['repeat'] = ['level-3'] * 3
        self.assertEqual(load_bearing.finding(levels, samples, 2)[0], 'carried')

    @unittest.skipUnless(
        (ROOT / 'examples/load-bearing/v5-first-cases.json').is_file(), 'Declared plans are kept with the study record'
    )
    def test_the_declared_plan_is_valid_and_pins_its_shams(self):
        plan = json.loads((ROOT / 'examples/load-bearing/v5-first-cases.json').read_text())
        self.assertEqual(plan['expectations'], load_bearing.EXPECTATIONS)
        for case in plan['cases']:
            self.assertFalse(any(load_bearing.overlaps(a, b) for a in case['sham'] for b in case['cited_anywhere']))
            self.assertEqual(load_bearing.line_count(case['sham']), load_bearing.line_count(case['ablate']))
        load_bearing.validate_plan(
            dict(plan, readings=[r for r in plan['readings'] if r['run_id'] != 'control-decoy-gestures']), ROOT
        )
        broken = json.loads(json.dumps(plan))
        broken['cases'][0]['sham'][0] = dict(broken['cases'][0]['ablate'][0])
        with self.assertRaisesRegex(ValueError, 'sham'):
            load_bearing.validate_plan(dict(broken, readings=broken['readings'][:-1]), ROOT)
        with self.assertRaisesRegex(ValueError, 'Expectations'):
            load_bearing.validate_plan(dict(plan, expectations={'ablation': 'same level'}), ROOT)

    def test_a_dry_plan_runs_every_trial_within_a_total_cap(self):
        with tempfile.TemporaryDirectory(prefix='after-poem-load-bearing-') as temporary:
            root = Path(temporary)
            scoring_pipeline.run(
                ROOT, rubric_policy_file(root), root / 'reference', run_id='sol-original', model=MODEL, dry_run=True
            )
            draft = {
                'schema_version': 1,
                'id': 'dry-plan',
                'question': 'Does the level rest on what it cites?',
                'frame': {'model': MODEL, 'reasoning': 'high', 'sessions': 3, 'minimum_agreement': 0.67},
                'masked_terms': 'examples/controls/seed-vocabulary.json',
                'sham_seed': 1,
                'readings': ['sol-original'],
                'cases': [
                    {
                        'id': 'sol-continuation',
                        'run_id': 'sol-original',
                        'axis': 'continuation',
                        'reference': str(root / 'reference/evaluation.json'),
                    }
                ],
            }
            (root / 'draft.json').write_text(json.dumps(draft))
            (root / 'plan.json').write_text(json.dumps(load_bearing.prepare_plan(root / 'draft.json', ROOT)))
            budget = [
                '--dry-run',
                '--max-turns',
                '9',
                '--max-input-tokens',
                '900000',
                '--max-output-tokens',
                '300000',
                '--max-usd',
                '0.07',
                '--price-input',
                '0.10',
                '--price-cached-input',
                '0.002',
                '--price-output',
                '0.20',
            ]
            record = load_bearing.run(root / 'plan.json', ROOT, root / 'trials', budget, total_usd=0.40, parallel=4)
            case = record['cases'][0]
            self.assertEqual(
                sorted(trial for trial, value in case['trials'].items() if value), sorted(load_bearing.TRIALS)
            )
            self.assertEqual(case['trials']['baseline']['samples'], ['level-0'] * 3)
            self.assertEqual(case['category'], 'cooled')
            status = json.loads((root / 'trials/cases/sol-continuation/sham/status.json').read_text())
            self.assertEqual(status['control'], 'ablated evidence; never admitted')
            self.assertEqual(record['readings'][0]['levels']['continuation']['outcome'], 'level-0')
            # An unfinished trial is set aside, kept, and read again in fresh sessions.
            unfinished = root / 'trials/cases/sol-continuation/sham'
            (root / 'trials/cases/sol-continuation/sham.log').write_text('stopped\n')
            unfinished.rename(root / 'held')
            (unfinished / 'sessions/01').mkdir(parents=True)
            load_bearing.run(root / 'plan.json', ROOT, root / 'trials', budget, total_usd=0.40, parallel=1, retry=True)
            self.assertTrue((root / 'trials/cases/sol-continuation/sham.attempt-1/sessions/01').is_dir())
            self.assertEqual((root / 'trials/cases/sol-continuation/sham.attempt-1.log').read_text(), 'stopped\n')
            self.assertTrue((unfinished / 'evaluation.json').exists())
            # A cap that cannot hold one capped trial starts nothing.
            again = load_bearing.run(root / 'plan.json', ROOT, root / 'starved', budget, total_usd=0.05, parallel=1)
            self.assertEqual(again['cases'][0]['category'], 'unresolved')
            self.assertFalse((root / 'starved/readings').exists())


class PurseTests(unittest.TestCase):
    """A trial that does not fit yet waits for running trials; only an idle purse without room skips."""

    def test_a_trial_waits_for_running_trials_before_it_is_skipped(self):
        import threading

        purse = load_bearing.Purse(0.25, 0.07, {}, [])
        self.assertTrue(purse.reserve())
        self.assertTrue(purse.reserve())
        started = []
        waiter = threading.Thread(target=lambda: started.append(purse.reserve()))
        waiter.start()
        waiter.join(0.2)
        self.assertTrue(waiter.is_alive())
        purse.settle(0.02)
        waiter.join(2)
        self.assertEqual(started, [True])
        purse.settle(0.02)
        purse.settle(0.02)
        self.assertAlmostEqual(purse.spent, 0.06)
        self.assertTrue(purse.reserve())
        purse.settle(0.15)
        self.assertFalse(purse.reserve())


class ReuseTests(unittest.TestCase):
    """A later plan reuses retained trials its change does not touch, pinned by hash, and pays only for new ones."""

    def test_a_second_plan_reuses_retained_trials_and_runs_only_new_ones(self):
        budget = [
            '--dry-run',
            '--max-turns',
            '9',
            '--max-input-tokens',
            '900000',
            '--max-output-tokens',
            '300000',
            '--max-usd',
            '0.07',
            '--price-input',
            '0.10',
            '--price-cached-input',
            '0.002',
            '--price-output',
            '0.20',
        ]
        with tempfile.TemporaryDirectory(prefix='after-poem-reuse-') as temporary:
            root = Path(temporary)
            for name in ['scripts', 'examples']:
                (root / name).symlink_to(ROOT / name)
            with mock.patch.object(load_bearing, 'ROOT', root):
                scoring_pipeline.run(
                    ROOT, rubric_policy_file(root), root / 'reference', run_id='sol-original', model=MODEL, dry_run=True
                )
                common = {
                    'schema_version': 1,
                    'question': 'Does the level rest on what it cites?',
                    'frame': {'model': MODEL, 'reasoning': 'high', 'sessions': 3, 'minimum_agreement': 0.67},
                    'masked_terms': 'examples/controls/seed-vocabulary.json',
                    'sham_seed': 1,
                }
                first = dict(
                    common,
                    id='first',
                    readings=['sol-original'],
                    cases=[
                        {
                            'id': 'one',
                            'run_id': 'sol-original',
                            'axis': 'continuation',
                            'reference': str(root / 'reference/evaluation.json'),
                        }
                    ],
                )
                (root / 'first.json').write_text(json.dumps(first))
                (root / 'first-plan.json').write_text(json.dumps(load_bearing.prepare_plan(root / 'first.json', ROOT)))
                load_bearing.run(root / 'first-plan.json', ROOT, root / 'lb/p1', budget, total_usd=0.5, parallel=5)
                second = dict(
                    common,
                    id='second',
                    rules=2,
                    reuse_root='lb',
                    readings=[{'run_id': 'sol-original', 'reuse': 'p1/readings/sol-original'}],
                    cases=[
                        {
                            'id': 'two',
                            'run_id': 'sol-original',
                            'axis': 'continuation',
                            'anchors': ['p1/readings/sol-original', 'p1/cases/one/ablation'],
                            'reuse': {'repeat': 'p1/cases/one/repeat', 'presentation': 'p1/cases/one/presentation'},
                        }
                    ],
                )
                (root / 'second.json').write_text(json.dumps(second))
                plan = load_bearing.prepare_plan(root / 'second.json', ROOT)
                self.assertEqual(plan['rules'], 2)
                self.assertEqual(
                    [item['from'] for item in plan['cases'][0]['reference']],
                    ['p1/readings/sol-original', 'p1/cases/one/ablation'],
                )
                (root / 'second-plan.json').write_text(json.dumps(plan))
                record = load_bearing.run(
                    root / 'second-plan.json', ROOT, root / 'lb/p2', budget, total_usd=0.5, parallel=2
                )
                trials = record['cases'][0]['trials']
                self.assertEqual(trials['baseline']['reused_from'], 'p1/readings/sol-original')
                self.assertEqual(trials['repeat']['reused_from'], 'p1/cases/one/repeat')
                self.assertNotIn('reused_from', trials['ablation'])
                self.assertEqual(record['rules'], 2)
                self.assertEqual(record['cases'][0]['category'], 'cooled')
                # Only the new trials ran here; the copies carry no log of their own.
                self.assertTrue((root / 'lb/p2/cases/two/ablation.log').exists())
                self.assertFalse((root / 'lb/p2/readings/sol-original.log').exists())
                # A retained trial that changes after the plan is declared is refused.
                tampered = root / 'lb/p1/cases/one/presentation/evaluation.json'
                tampered.chmod(0o600)
                tampered.write_text(tampered.read_text() + ' ')
                with self.assertRaisesRegex(ValueError, 'changed since'):
                    load_bearing.run(root / 'second-plan.json', ROOT, root / 'lb/p3', budget, total_usd=0.5)


if __name__ == '__main__':
    unittest.main()
