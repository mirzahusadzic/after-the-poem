import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import atlas
import evaluation_contract as contracts
import evaluation_run
import rubric_policy
import scoring_pipeline
from evaluation_fixtures import admissible, isolated_project


MODEL = 'deepseek/deepseek-v4.1-flash-1017'


class FullRubricPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='after-poem-rubric-policy-')
        self.root = isolated_project(Path(self.temporary.name))
        self.rubric = json.loads((self.root / 'data/rubric.json').read_text(encoding='utf-8'))
        self.policy_path = self.root / '.local/rubric-policy.json'
        rubric_policy.write(rubric_policy.build(self.rubric), self.policy_path)

    def tearDown(self):
        self.temporary.cleanup()

    def admit(self, name, outcomes=None):
        """Score sol-original in one synthetic session and admit it as package `name`."""
        evaluation = self.root / '.local' / name
        scoring_pipeline.run(
            self.root,
            self.policy_path,
            evaluation,
            run_id='sol-original',
            model=MODEL,
            evaluation_id=name,
            dry_run=True,
            synthetic_outcomes=outcomes,
        )
        admissible(self.root, evaluation, name)
        return evaluation_run.integrate_evaluation(self.root, evaluation, review_id=name + '-review')

    def test_default_policy_has_one_session_and_one_branch_per_axis(self):
        policy = rubric_policy.build(self.rubric)
        nodes, outgoing, _ = contracts.validate_policy(policy)
        judgments = [node for node in nodes.values() if node['kind'] == 'judgment']
        self.assertEqual([node['id'] for node in judgments], [axis['id'] for axis in self.rubric['axes']])
        self.assertEqual({node['repetitions'] for node in judgments}, {1})
        self.assertEqual(len(policy['nodes']), 1 + 8 * 9)
        self.assertTrue(rubric_policy.matches_rubric(policy, self.rubric))
        changed = json.loads(json.dumps(self.rubric))
        changed['axes'][0]['anchors'][0] = 'A different anchor.'
        self.assertFalse(rubric_policy.matches_rubric(policy, changed))

    def test_all_axes_score_into_a_normal_radar_review(self):
        evaluation = self.root / '.local/full'
        result = scoring_pipeline.run(
            self.root,
            self.policy_path,
            evaluation,
            run_id='sol-original',
            model=MODEL,
            evaluation_id='full',
            dry_run=True,
            layout='per-axis',
        )
        self.assertEqual((result['criteria'], result['judgment_samples'], result['turns']), (8, 8, 9))
        review = evaluation_run.review_from_evaluation(
            self.root, evaluation, review_id='full-review', evaluation_run_id='full-run', reviewer='Synthetic fixture'
        )
        self.assertEqual(set(review['scores']), {axis['id'] for axis in self.rubric['axes']})
        self.assertTrue(all(score['value'] == 0 for score in review['scores'].values()))
        self.assertTrue(all(score['confidence'] == 'low' for score in review['scores'].values()))
        self.assertTrue(all(score['citations'][0]['quote'] for score in review['scores'].values()))
        self.assertEqual(review['provenance']['source_run_id'], 'sol-original')

    def test_unresolved_axes_stay_null_without_citations(self):
        outcomes = {axis['id']: ['level-3'] for axis in self.rubric['axes']}
        outcomes['consent'] = ['unresolved']
        package = self.root / self.admit('unresolved-axis', outcomes)['bundle']
        review = json.loads((package / 'review.json').read_text())
        self.assertIsNone(review['scores']['consent']['value'])
        self.assertEqual(review['scores']['consent']['confidence'], 'unassessed')
        self.assertEqual(review['scores']['consent']['citations'], [])
        self.assertEqual(review['scores']['relations']['value'], 3)

    def test_admission_is_one_rebuild_idempotent_and_slim(self):
        first = self.admit('admitted')
        self.assertEqual((first['status'], first['order']), ('integrated', 1))
        package = self.root / first['bundle']
        evaluation = json.loads((package / 'evaluation.json').read_text())
        self.assertNotIn('quote', evaluation['judgments'][0]['citations'][0])
        self.assertLess(sum(path.stat().st_size for path in package.iterdir()), 120000)
        again = evaluation_run.integrate_evaluation(
            self.root, self.root / '.local/admitted', review_id='admitted-review'
        )
        self.assertEqual(again['status'], 'already-integrated')
        presentation = json.loads((self.root / 'dist/presentations/admitted.json').read_text())
        self.assertEqual(presentation['usage']['calls'], 1)

    def test_independent_evaluations_coexist_and_delete_by_folder(self):
        first, second = self.admit('sibling-first'), self.admit('sibling-second')
        self.assertEqual(second['order'], first['order'] + 1)
        data = atlas.validate(self.root)
        self.assertEqual(data['selections']['sibling-first'], 'sibling-first-review')
        self.assertEqual(data['selections']['sibling-second'], 'sibling-second-review')
        shutil.rmtree(self.root / first['bundle'])
        data = atlas.validate(self.root)
        self.assertNotIn('sibling-first', {run['id'] for run in data['runs']})
        self.assertEqual(data['selections']['sibling-second'], 'sibling-second-review')
        shutil.rmtree(self.root / second['bundle'])
        data = atlas.validate(self.root)
        self.assertEqual(data['selections']['sol-original'], 'sol-original-static-20261019-v2')
        self.assertNotIn('sibling-second-review', {review['id'] for review in data['reviews']})

    def test_selecting_a_base_reading_never_records_package_runs(self):
        result = self.admit('selection-guard')
        atlas.select_review(self.root, 'sol-original-static-20261019-v2')
        selection = json.loads((self.root / 'data/review-selection.json').read_text())
        self.assertNotIn('selection-guard', selection['selections'])
        with self.assertRaisesRegex(ValueError, 'select their own review'):
            atlas.select_review(self.root, 'selection-guard-review')
        shutil.rmtree(self.root / result['bundle'])
        atlas.validate(self.root)

    def test_tampered_package_file_is_rejected(self):
        package = self.root / self.admit('tamper')['bundle']
        review = json.loads((package / 'review.json').read_text(encoding='utf-8'))
        review['summary'] += ' Changed after admission.'
        (package / 'review.json').write_text(json.dumps(review, indent=2) + '\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'package file changed'):
            atlas.validate(self.root)

    def test_rehashed_score_tampering_is_rederived_and_rejected(self):
        package = self.root / self.admit('rehashed')['bundle']
        review = json.loads((package / 'review.json').read_text(encoding='utf-8'))
        review['scores']['recognition']['value'] = 4
        (package / 'review.json').write_text(json.dumps(review, indent=2) + '\n', encoding='utf-8')
        manifest = json.loads((package / 'manifest.json').read_text())
        manifest['files']['review.json'] = hashlib.sha256((package / 'review.json').read_bytes()).hexdigest()
        (package / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Review score'):
            atlas.validate(self.root)

    def test_stray_hidden_files_do_not_break_packages(self):
        package = self.root / self.admit('hidden-file')['bundle']
        (package / '.DS_Store').write_bytes(b'finder')
        atlas.validate(self.root)

    def test_conformance_hook_rejects_tampered_policy(self):
        policy = rubric_policy.build(self.rubric)
        rubric_policy.validate_conformance(policy, self.rubric)
        bad = json.loads(json.dumps(policy))
        bad['nodes'][1]['criterion'] = 'Tampered criterion.'
        self.assertFalse(rubric_policy.matches_rubric(bad, self.rubric))
        with self.assertRaisesRegex(ValueError, 'does not encode'):
            rubric_policy.validate_conformance(bad, self.rubric)
        with self.assertRaisesRegex(ValueError, 'does not encode'):
            contracts.validate_rubric_conformance(bad, self.rubric)

    def test_legacy_sol_original_still_validates_byte_for_byte(self):
        import evaluation_contract as contracts

        data = atlas.validate(ROOT)
        rubric = json.loads((ROOT / 'data/rubric.json').read_text())
        for name in ['sol-original-muse-v5-1', 'sol-original-muse-v4-1']:
            package = ROOT / ('data/evaluations/' + name)
            if not package.exists():
                continue
            evaluation = json.loads((package / 'evaluation.json').read_text())
            policy = json.loads((package / 'scoring-policy.json').read_text())
            review = json.loads((package / 'review.json').read_text())
            source_texts = {
                f['path']: f['text'] for f in data['corpora'][evaluation['run_id']]['files'] if f['text'] is not None
            }
            scores, axes = contracts.review_projection(evaluation, policy, rubric, source_texts)
            self.assertEqual(review['scores'], scores, 'The projection must stay byte-for-byte for ' + name)
            self.assertEqual(review['provenance'].get('axis_outcomes'), axes)

if __name__ == '__main__':
    unittest.main()
