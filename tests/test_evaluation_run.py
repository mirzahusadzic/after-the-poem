import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import codex_worker as worker
import evaluation_run
import session_flow
from evaluation_fixtures import admissible, isolated_project


POLICY = ROOT / 'examples/scoring-policy-material-resistance.json'


class EndToEndEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='after-poem-end-to-end-')
        self.root = Path(self.temporary.name)
        isolated_project(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def arguments(self, **overrides):
        return dict(
            dict(
                model='deepseek/deepseek-v4.1-flash-1017',
                reasoning='max',
                evaluation_id='sol-original-end-to-end-evaluation',
                review_id='sol-original-end-to-end-review',
                dry_run=True,
            ),
            **overrides,
        )

    def test_dry_run_is_complete_reusable_and_never_integrated(self):
        output = self.root / '.local/evaluation-run'
        first = evaluation_run.run(self.root, 'sol-original', POLICY, output, **self.arguments())
        self.assertEqual((first['status'], first['integration_status']), ('completed', 'skipped-synthetic'))
        self.assertFalse(first['evaluation_reused'])
        self.assertEqual(first['evaluation']['sessions'], 3)
        self.assertFalse((self.root / 'data/evaluations').exists())
        with mock.patch.object(worker, 'run_turn', side_effect=AssertionError('completed work is reused')):
            second = evaluation_run.run(self.root, 'sol-original', POLICY, output, **self.arguments())
        self.assertTrue(second['evaluation_reused'])
        self.assertEqual(second['integration_status'], 'skipped-synthetic')

    def test_a_synthetic_output_is_never_reused_as_live(self):
        output = self.root / '.local/mode'
        evaluation_run.run(self.root, 'sol-original', POLICY, output, **self.arguments())
        with self.assertRaisesRegex(ValueError, 'already holds a synthetic evaluation'):
            evaluation_run.run(
                self.root, 'sol-original', POLICY, output, **self.arguments(dry_run=False, accept_run=True)
            )

    def test_live_run_requires_acceptance_before_creating_output(self):
        output = self.root / '.local/live-evaluation-run'
        with self.assertRaisesRegex(ValueError, 'accept-run'):
            evaluation_run.run(self.root, 'sol-original', POLICY, output, **self.arguments(dry_run=False))
        self.assertFalse(output.exists())

    def test_interrupted_run_resumes_without_repeating_completed_turns(self):
        output = self.root / '.local/partial-evaluation-run'
        original = worker.run_turn
        launched = []

        def interrupt_third(*args, **kwargs):
            launched.append(args[1])
            if len(launched) == 3:
                raise KeyboardInterrupt
            return original(*args, **kwargs)

        with mock.patch.object(worker, 'run_turn', side_effect=interrupt_third):
            with self.assertRaises(KeyboardInterrupt):
                evaluation_run.run(self.root, 'sol-original', POLICY, output, **self.arguments(layout='per-axis'))
        preserved = output / 'evaluation/sessions/01/turns/001/receipt.json'
        preserved_bytes = preserved.read_bytes()
        resumed_turns = []
        with mock.patch.object(
            worker,
            'run_turn',
            side_effect=lambda *args, **kwargs: resumed_turns.append(args[1]) or original(*args, **kwargs),
        ):
            result = evaluation_run.run(self.root, 'sol-original', POLICY, output, **self.arguments(layout='per-axis'))
        self.assertEqual(result['evaluation']['judgment_samples'], 3)
        self.assertEqual(preserved.read_bytes(), preserved_bytes)
        self.assertEqual(len(resumed_turns), 2)

    def test_profile_runs_the_same_pipeline_with_minimal_configuration(self):
        profile = json.loads((ROOT / 'examples/evaluation-profile-deepseek.json').read_text(encoding='utf-8'))
        profile.update(
            evaluation_id='sol-original-profile-evaluation',
            review_id='sol-original-profile-review',
            output='.local/profile-run',
        )
        path = self.root / 'profile.json'
        path.write_text(json.dumps(profile), encoding='utf-8')
        loaded = evaluation_run.load_profile(self.root, path)
        self.assertEqual(
            (loaded['run_id'], loaded['repetitions'], loaded['repairs'], loaded['layout']),
            ('sol-original', 1, 2, 'compact'),
        )
        result = evaluation_run.run_profile(self.root, path, dry_run=True, accept_run=False)
        self.assertEqual(result['integration_status'], 'skipped-synthetic')
        self.assertTrue(result['output'].endswith('profile-run.dry-run'))
        self.assertFalse((self.root / '.local/profile-run').exists())
        self.assertEqual((result['evaluation']['criteria'], result['evaluation']['turns']), (8, 1))

    def test_profile_rejects_unknown_fields_and_old_schema(self):
        profile = json.loads((ROOT / 'examples/evaluation-profile-deepseek.json').read_text(encoding='utf-8'))
        path = self.root / 'profile.json'
        path.write_text(json.dumps(dict(profile, judgment_workers=4)), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'unknown fields'):
            evaluation_run.load_profile(self.root, path)
        path.write_text(json.dumps(dict(profile, schema_version=1)), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'expected 2'):
            evaluation_run.load_profile(self.root, path)

    def test_admitted_package_is_a_separate_removable_run(self):
        output = self.root / '.local/admitted'
        evaluation_run.run(
            self.root,
            'sol-original',
            None,
            output,
            **self.arguments(evaluation_id='sol-original-session-a', review_id='sol-original-session-a-review'),
        )
        with self.assertRaisesRegex(ValueError, 'Synthetic evaluations cannot be admitted'):
            evaluation_run.integrate_evaluation(
                self.root, output / 'evaluation', review_id='sol-original-session-a-review'
            )
        admissible(self.root, output / 'evaluation', 'sol-original-session-a')
        admitted = evaluation_run.integrate_evaluation(
            self.root, output / 'evaluation', review_id='sol-original-session-a-review'
        )
        self.assertEqual((admitted['status'], admitted['order']), ('integrated', 1))
        package = self.root / 'data/evaluations/sol-original-session-a'
        files = {path.name for path in package.iterdir()}
        self.assertEqual(
            files,
            {
                'run.json',
                'evaluation.json',
                'extraction.json',
                'scoring-policy.json',
                'status.json',
                'review.json',
                'selection.json',
                'manifest.json',
                'carriage.json',
            },
        )
        carriage = json.loads((package / 'carriage.json').read_text())
        self.assertEqual(carriage['evaluation_id'], 'sol-original-session-a')
        self.assertIn(
            'sol-original-session-a',
            evaluation_run.atlas.validate(self.root)
            and json.loads((self.root / 'dist/report.json').read_text())['carriage'],
        )
        review = json.loads((package / 'review.json').read_text())
        self.assertEqual(review['provenance']['method'], 'session-rubric-dag-v3')
        self.assertTrue(
            all(score['citations'] and score['citations'][0]['quote'] for score in review['scores'].values())
        )
        data = evaluation_run.atlas.validate(self.root)
        self.assertIn('sol-original-session-a', {run['id'] for run in data['runs']})
        selection = json.loads((self.root / 'data/review-selection.json').read_text())
        self.assertNotIn('sol-original-session-a', selection['selections'])
        again = evaluation_run.integrate_evaluation(
            self.root, output / 'evaluation', review_id='sol-original-session-a-review'
        )
        self.assertEqual(again['status'], 'already-integrated')
        shutil.rmtree(package)
        self.assertNotIn(
            'sol-original-session-a', {run['id'] for run in evaluation_run.atlas.validate(self.root)['runs']}
        )

    def test_control_readings_are_never_admitted(self):
        output = self.root / '.local/control'
        scoring = evaluation_run.scoring_pipeline
        policy = output / 'policy.json'
        output.mkdir(parents=True)
        policy.write_text(
            json.dumps(evaluation_run.rubric_policy.build(json.loads((self.root / 'data/rubric.json').read_text())))
        )
        scoring.run(
            self.root,
            policy,
            output / 'evaluation',
            run_id='sol-original',
            model='deepseek/deepseek-v4.1-flash-1017',
            evaluation_id='sol-original-control',
            dry_run=True,
            control_seed_path=ROOT / 'examples/controls/owl-and-the-pussy-cat.txt',
        )
        admissible(self.root, output / 'evaluation', 'sol-original-control')
        with self.assertRaisesRegex(ValueError, 'never admitted'):
            evaluation_run.integrate_evaluation(
                self.root, output / 'evaluation', review_id='sol-original-control-review'
            )

    def test_session_turns_use_one_instruction_profile_and_budget_ledger(self):
        output = self.root / '.local/ledger'
        evaluation_run.run(self.root, 'sol-original', POLICY, output, **self.arguments())
        receipts = session_flow.ledger(output / 'evaluation')
        self.assertEqual(len(receipts), 3)
        self.assertEqual({receipt['requested']['model'] for receipt in receipts}, {'deepseek/deepseek-v4.1-flash-1017'})

    def test_admitted_review_keeps_the_alternative_readings(self):
        output = self.root / '.local/alternatives-record'
        # A rubric-generated policy, so the review matches the 8-axis rubric.
        evaluation_run.run(self.root, 'sol-original', None, output, **self.arguments())
        admissible(self.root, output / 'evaluation', 'sol-original-alternatives')
        review = evaluation_run.review_from_evaluation(
            self.root, output / 'evaluation', review_id='r-alternatives', evaluation_run_id='run-alternatives'
        )
        self.assertIsInstance(review['provenance']['alternatives'], list)
        self.assertNotIn('representative_policy', review['provenance'])

    def test_candidate_identity_is_the_extraction_identity(self):
        output = self.root / '.local/candidate-derived'
        evaluation_run.run(self.root, 'sol-original', None, output, **self.arguments())
        evaluation = json.loads((output / 'evaluation/evaluation.json').read_text())
        extraction = json.loads((output / 'evaluation/extraction.json').read_text())
        self.assertEqual(evaluation['identities']['candidate_sha256'], extraction['identities']['candidate_sha256'])

if __name__ == '__main__':
    unittest.main()
