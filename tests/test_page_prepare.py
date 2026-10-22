import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import extraction_pipeline
import page_prepare
import scoring_pipeline


POLICY = ROOT / 'tests/fixtures/evaluation/attribution/scoring-policy.json'
MATERIAL_POLICY = ROOT / 'examples/scoring-policy-material-resistance.json'
MODEL = 'deepseek/deepseek-v4.1-flash-1017'


class PagePreparationTests(unittest.TestCase):
    def make_evaluation(self, root, *, outcomes=None):
        extraction_dir = root / 'extraction'
        extraction_pipeline.run(
            ROOT,
            'sol-original',
            extraction_dir,
            model=MODEL,
            extraction_id='sol-original-page-extraction',
            dry_run=True,
        )
        evaluation_dir = root / 'evaluation'
        scoring_pipeline.run(
            ROOT,
            POLICY,
            evaluation_dir,
            extraction_path=extraction_dir / 'extraction.json',
            model=MODEL,
            evaluation_id='sol-original-page-evaluation',
            dry_run=True,
            synthetic_outcomes=outcomes or ['contradicted', 'mixed', 'supported'],
        )
        return evaluation_dir

    def test_prepares_generic_accessible_page_from_normalized_record(self):
        with tempfile.TemporaryDirectory(prefix='after-poem-page-') as temporary:
            root = Path(temporary)
            evaluation_dir = self.make_evaluation(root)
            page_dir = root / 'page'
            result = page_prepare.prepare_page(ROOT, evaluation_dir, page_dir)
            self.assertEqual(result['status'], 'unresolved')
            self.assertIsNone(result['score'])
            page = (page_dir / 'index.html').read_text(encoding='utf-8')
            self.assertIn('Repeated judgments', page)
            self.assertIn('No score awarded', page)
            self.assertEqual(page.count('<tr><td>'), 3)
            self.assertIn('contradicted', page)
            self.assertIn('mixed', page)
            self.assertIn('supported', page)
            self.assertIn('Every sample remains visible', page)
            self.assertIn('Declared scale: <strong>0, 1</strong>', page)
            self.assertNotIn('<script', page)
            self.assertNotIn('src="http', page)
            presentation = json.loads((page_dir / 'presentation.json').read_text(encoding='utf-8'))
            self.assertEqual(presentation['status'], 'unresolved')
            self.assertIsNone(presentation['score'])
            self.assertEqual(len(presentation['criteria'][0]['samples']), 3)
            self.assertEqual(presentation['criteria'][0]['scale'], [0, 1])

    def test_criterion_scale_comes_from_the_scoring_policy(self):
        with tempfile.TemporaryDirectory(prefix='after-poem-page-scale-') as temporary:
            root = Path(temporary)
            extraction_dir = root / 'extraction'
            extraction_pipeline.run(
                ROOT,
                'sol-original',
                extraction_dir,
                model=MODEL,
                extraction_id='sol-original-page-scale-extraction',
                dry_run=True,
            )
            policy = json.loads(MATERIAL_POLICY.read_text(encoding='utf-8'))
            next(node for node in policy['nodes'] if node['id'] == 'resistance-supported')['result']['score'] = 9
            policy_path = root / 'scoring-policy.json'
            policy_path.write_text(json.dumps(policy), encoding='utf-8')
            evaluation_dir = root / 'evaluation'
            scoring_pipeline.run(
                ROOT,
                policy_path,
                evaluation_dir,
                extraction_path=extraction_dir / 'extraction.json',
                model=MODEL,
                evaluation_id='sol-original-page-scale-evaluation',
                dry_run=True,
                synthetic_outcomes=['supported', 'supported', 'supported'],
            )
            presentation = page_prepare.build_presentation(
                json.loads((evaluation_dir / 'evaluation.json').read_text(encoding='utf-8')),
                json.loads((evaluation_dir / 'extraction.json').read_text(encoding='utf-8')),
                json.loads((evaluation_dir / 'scoring-policy.json').read_text(encoding='utf-8')),
                evaluation_sha256=page_prepare.sha((evaluation_dir / 'evaluation.json').read_bytes()),
                extraction_sha256=page_prepare.sha((evaluation_dir / 'extraction.json').read_bytes()),
                scoring_policy_sha256=page_prepare.sha((evaluation_dir / 'scoring-policy.json').read_bytes()),
                source_texts=page_prepare._source_texts(ROOT, 'sol-original'),
            )
            self.assertEqual(presentation['criteria'][0]['scale'], [0, 1, 9])
            self.assertEqual(presentation['criteria'][0]['outcome']['score'], 9)
            self.assertIsNone(presentation['score'])

    def test_preparation_is_deterministic(self):
        with tempfile.TemporaryDirectory(prefix='after-poem-page-') as temporary:
            root = Path(temporary)
            evaluation_dir = self.make_evaluation(root)
            first = root / 'page-one'
            second = root / 'page-two'
            page_prepare.prepare_page(ROOT, evaluation_dir, first)
            page_prepare.prepare_page(ROOT, evaluation_dir, second)
            for name in ['presentation.json', 'index.html', 'manifest.json']:
                self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())

    def test_page_escapes_model_authored_markup(self):
        with tempfile.TemporaryDirectory(prefix='after-poem-page-') as temporary:
            root = Path(temporary)
            evaluation_dir = self.make_evaluation(root)
            page_dir = root / 'page'
            page_prepare.prepare_page(ROOT, evaluation_dir, page_dir)
            presentation = json.loads((page_dir / 'presentation.json').read_text(encoding='utf-8'))
            presentation['criteria'][0]['samples'][0]['rationale'] = '<script>alert("bad")</script>'
            page = page_prepare.render_html(presentation)
            self.assertNotIn('<script>alert("bad")</script>', page)
            self.assertIn('&lt;script&gt;alert(&quot;bad&quot;)&lt;/script&gt;', page)

    def test_manifest_detects_tampering(self):
        with tempfile.TemporaryDirectory(prefix='after-poem-page-') as temporary:
            root = Path(temporary)
            evaluation_dir = self.make_evaluation(root)
            page_dir = root / 'page'
            page_prepare.prepare_page(ROOT, evaluation_dir, page_dir)
            index = page_dir / 'index.html'
            index.write_text(index.read_text(encoding='utf-8') + '\nchanged\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Presentation file changed'):
                page_prepare.validate_output(ROOT, page_dir)


if __name__ == '__main__':
    unittest.main()
