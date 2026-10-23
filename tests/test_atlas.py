import copy
import csv
import importlib.util
import json
import shutil
import tempfile
import unittest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from evaluation_fixtures import EDITORIAL

sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('atlas', ROOT / 'scripts/atlas.py')
atlas = importlib.util.module_from_spec(spec)
spec.loader.exec_module(atlas)


class EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = atlas.validate(ROOT)

    def setUp(self):
        self.review = copy.deepcopy(self.data['active_reviews']['sol-original'])

    def validate_review(self):
        run = next(r for r in self.data['runs'] if r['id'] == 'sol-original')
        return atlas.validate_review(
            self.review,
            {r['id']: r for r in self.data['runs']},
            self.data['corpora'],
            self.data['rubric'],
            self.data['rubric_sha256'],
            self.data['cohort_prompts'][run['cohort']],
        )

    def test_retained_corpus_keeps_the_historical_five_and_accepts_new_cohorts(self):
        self.assertGreaterEqual(len(self.data['runs']), 6)
        historical = next(c for c in self.data['cohorts'] if c['baseline'] == 'sol-original')
        five = ['sol-original', 'astra-garden', 'sol56-max', 'sol61-max', 'astra-max']
        self.assertEqual(historical['runs'][:5], five)
        scores = [score['value'] for rid in five for score in self.data['active_reviews'][rid]['scores'].values()]
        self.assertEqual(sum(value is not None for value in scores), 40)
        # Later imports join the cohort unreviewed or reviewed; unassessed axes stay null, never zero.
        for rid in historical['runs'][5:]:
            review = self.data['active_reviews'][rid]
            if review['status'] == 'unreviewed':
                self.assertTrue(all(score['value'] is None for score in review['scores'].values()))
        self.assertGreaterEqual(len(self.data['cohorts']), 2)
        self.assertEqual(len({r['cohort'] for r in self.data['runs']}), len(self.data['cohorts']))
        self.assertEqual(self.data['baseline'], 'sol-original')

    def test_retained_pilot_assessment_preserves_admission_and_its_own_cohort(self):
        rid = 'deepseek-flash-max-pilot'
        run = next(r for r in self.data['runs'] if r['id'] == rid)
        cohort = next(c for c in self.data['cohorts'] if c['id'] == run['cohort'])
        # The retained pilot leads its cohort; admitted evaluation packages coexist as their own runs.
        self.assertEqual(cohort['runs'][0], rid)
        packages = {item['id'] for item in self.data['runs'] if item.get('condition') == 'evaluation'}
        self.assertEqual(
            set(cohort['runs'][1:]),
            {
                item
                for item in packages
                if next(r for r in self.data['runs'] if r['id'] == item)['source_run_id'] == rid
            },
        )
        self.assertNotEqual(cohort['prompt_sha256'], self.data['cohorts'][0]['prompt_sha256'])
        review = self.data['active_reviews'][rid]
        self.assertEqual(review['status'], 'provisional')
        reviews_by_id = {item['id']: item for item in self.data['reviews']}
        lineage, cursor = [], review
        while cursor:
            lineage.append(cursor)
            cursor = reviews_by_id.get(cursor.get('supersedes'))
        self.assertIn(rid + '-unreviewed-v1', {item['id'] for item in lineage})
        self.assertIn(rid + '-editorial-20261021-v1', {item['id'] for item in lineage})
        predecessor = reviews_by_id[rid + '-unreviewed-v1']
        self.assertEqual(predecessor['status'], 'unreviewed')
        self.assertIsNone(predecessor['supersedes'])
        self.assertEqual(
            atlas.sha((ROOT / 'data/reviews' / (predecessor['id'] + '.json')).read_bytes()),
            'c8eff55f6ecd4a766a0bf6a1d3bd2e07d25604ff330f9b03a39f688567bbb0bc',
        )
        for score in predecessor['scores'].values():
            self.assertIsNone(score['value'])
            self.assertEqual(score['confidence'], 'unassessed')
            self.assertEqual(score['citations'], [])
        self.assertEqual(run['condition'], 'partial')
        self.assertEqual(run['cli_version'], 'unrecorded')
        self.assertIn('unverified', run['model_identity_basis'])

    def test_retained_pilot_bytes_and_retrospective_provenance_are_pinned(self):
        rid = 'deepseek-flash-max-pilot'
        folder = ROOT / 'data/bundles' / rid
        run = atlas.load(folder / 'run.json')
        manifest = atlas.load(folder / 'manifest.json')
        corpus = self.data['corpora'][rid]
        self.assertEqual(len(manifest['files']), 13)
        self.assertEqual(
            [{key: source[key] for key in ['path', 'bytes', 'sha256']} for source in corpus['files']], manifest['files']
        )
        for source in corpus['files']:
            payload = source['text'].encode('utf-8')
            self.assertEqual(atlas.sha(payload), source['sha256'])
            self.assertEqual(len(payload), source['bytes'])
        prompt = (ROOT / 'corpus/prompts' / (run['prompt_sha256'] + '.txt')).read_bytes()
        delivery = run['prompt_delivery']
        seed = prompt[: delivery['seed_bytes']]
        suffix = prompt[delivery['seed_bytes'] :]
        self.assertEqual(len(suffix), delivery['suffix_bytes'])
        self.assertEqual(atlas.sha(seed), delivery['seed_sha256'])
        self.assertEqual(atlas.sha(suffix), delivery['suffix_sha256'])
        self.assertEqual(
            seed, (ROOT / 'corpus/prompts' / (self.data['cohorts'][0]['prompt_sha256'] + '.txt')).read_bytes()
        )
        self.assertEqual(run['retention']['kind'], 'retrospective-workspace-capture')
        self.assertIsNone(run['retention']['original_snapshot_sha256'])
        self.assertEqual(run['outcome']['legacy_status'], 'incomplete')
        self.assertEqual(run['outcome']['stop_reason'], 'legacy-handoff-missing')
        self.assertEqual(run['outcome']['process_exit_code'], 0)
        self.assertEqual(run['condition']['reported'], {'model': None, 'provider': None})
        self.assertTrue(all(value is None for value in run['condition']['budgets'].values()))
        identity = {'prompt_sha256': atlas.sha(prompt)}
        identity.update(
            {
                name + '_sha256': atlas.sha((folder / (name + '.json')).read_bytes())
                for name in ['run', 'manifest', 'observations']
            }
        )
        self.assertEqual(atlas.sha(atlas.canonical(identity)), corpus['recorded_snapshot_sha256'])

    def test_unassessed_remains_null(self):
        s = self.review['scores']['consent']
        s.update(value=None, confidence='unassessed', citations=[], rationale='No evidence assessed.')
        self.validate_review()
        self.assertIsNone(s['value'])

    def test_unknown_is_not_a_zero(self):
        self.review['scores']['consent'].update(value=None, confidence='medium')
        with self.assertRaisesRegex(ValueError, 'unassessed confidence'):
            self.validate_review()

    def test_scores_need_real_evidence(self):
        self.review['scores']['touch']['citations'] = []
        with self.assertRaisesRegex(ValueError, 'need evidence'):
            self.validate_review()

    def test_invented_quotes_are_rejected(self):
        self.review['scores']['touch']['citations'][0]['quote'] += '\nThis was never written.'
        with self.assertRaisesRegex(ValueError, 'quote differs'):
            self.validate_review()

    def test_citations_cannot_claim_unread_lines(self):
        self.review['scores']['touch']['citations'][0]['end'] = 100000
        with self.assertRaisesRegex(ValueError, 'line range'):
            self.validate_review()

    def test_score_bounds_and_types(self):
        for invalid in [-1, 5, 2.5, True, '3']:
            self.review['scores']['recognition']['value'] = invalid
            with self.assertRaisesRegex(ValueError, 'declared rubric scale'):
                self.validate_review()

    def test_review_validation_uses_the_declared_scale(self):
        rubric = copy.deepcopy(self.data['rubric'])
        rubric['scale'] = [0, 2, 4, 6]
        rubric['axes'] = copy.deepcopy(rubric['axes'][:2])
        for axis in rubric['axes']:
            axis['anchors'] = [axis['anchors'][index] for index in [0, 2, 4, 0]]
        review = copy.deepcopy(self.review)
        review['rubric_version'] = rubric['version']
        review['rubric_sha256'] = atlas.sha(atlas.canonical(rubric))
        review['scores'] = {axis['id']: copy.deepcopy(review['scores'][axis['id']]) for axis in rubric['axes']}
        for score in review['scores'].values():
            score['value'] = 2
        review['scores'][rubric['axes'][0]['id']]['value'] = 6
        runtime = copy.deepcopy(self.data)
        run = next(r for r in runtime['runs'] if r['id'] == 'sol-original')
        prompt = runtime['cohort_prompts'][run['cohort']]
        atlas.validate_review(
            review,
            {r['id']: r for r in runtime['runs']},
            runtime['corpora'],
            rubric,
            atlas.sha(atlas.canonical(rubric)),
            prompt,
        )
        review['scores'][rubric['axes'][0]['id']]['value'] = 5
        with self.assertRaisesRegex(ValueError, 'declared rubric scale'):
            atlas.validate_review(
                review,
                {r['id']: r for r in runtime['runs']},
                runtime['corpora'],
                rubric,
                atlas.sha(atlas.canonical(rubric)),
                prompt,
            )

    def test_rubric_and_candidate_drift_invalidate_reviews(self):
        for field in ['rubric_sha256', 'corpus_sha256']:
            original = self.review[field]
            self.review[field] = '0' * 64
            with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                self.validate_review()
            self.review[field] = original

    def test_unreviewed_records_cannot_award_scores(self):
        self.review['status'] = 'unreviewed'
        with self.assertRaisesRegex(ValueError, 'cannot award'):
            self.validate_review()
        self.review['scores'] = {
            axis: {
                'value': None,
                'confidence': 'unassessed',
                'rationale': 'Not assessed.',
                'citations': [],
                'seed_span': [1, 1],
            }
            for axis in self.review['scores']
        }
        self.validate_review()

    def test_independent_root_reviews_can_coexist_without_replacing_selection(self):
        independent = copy.deepcopy(self.data['active_reviews']['sol-original'])
        independent.update(id='independent-reading-1', reviewer='Second reader', supersedes=None)
        result = atlas.validate(ROOT, extra_review=independent)
        self.assertEqual(
            result['active_reviews']['sol-original']['id'], self.data['active_reviews']['sol-original']['id']
        )
        self.assertTrue(any(review['id'] == independent['id'] for review in result['reviews']))

    def test_selected_reading_must_be_a_leaf(self):
        selections = dict(self.data['selections'])
        selections['sol-original'] = 'sol-original-static-20261019-v1'
        with self.assertRaisesRegex(ValueError, 'current leaf'):
            atlas.validate(ROOT, selection_override=selections)

    def test_source_html_cannot_execute_the_candidate(self):
        source = '<script>alert("candidate")</script>\n</pre><img src=x onerror=alert(1)>'
        page = atlas.source_page('unsafe <title>', source, atlas.sha(source.encode()))
        self.assertNotIn('<script>', page)
        self.assertNotIn('<img', page)
        self.assertIn('&lt;script&gt;', page)
        self.assertIn('id="L2"', page)

    def test_cross_document_source_links_open_in_new_tabs(self):
        page = atlas.source_page(
            'Links',
            '[Other](OTHER.md) and [section](#L1)\n',
            atlas.sha(b'x'),
            markdown=True,
            resolve_link=lambda target: '#L1' if target == '#L1' else 'OTHER.md.html' if target == 'OTHER.md' else None,
        )
        self.assertIn('<a href="OTHER.md.html" target="_blank" rel="noopener">', page)
        self.assertIn('<a href="#L1">', page)

    def test_source_paths_stay_relative(self):
        for path in ['/etc/passwd', '../outside', 'foo/../../outside', 'foo\\bar', 'a/./b', 'a//b', 'bad\nname']:
            self.assertFalse(atlas.safe_path(path))


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='after-poem-test-')
        self.root = Path(self.tmp.name)
        for name in ['corpus', 'data', 'src', 'assets']:
            shutil.copytree(ROOT / name, self.root / name, ignore=EDITORIAL)
        for name in ['LICENSE', 'LICENSE-CONTENT', 'LICENSING.md', 'THIRD_PARTY_NOTICES.md']:
            shutil.copyfile(ROOT / name, self.root / name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_tampering_is_detected_before_a_report_is_built(self):
        path = self.root / 'corpus/runs/sol-original.json'
        path.write_text(path.read_text() + ' ')
        with self.assertRaisesRegex(ValueError, 'Corpus hash mismatch'):
            atlas.build(self.root)
        self.assertFalse((self.root / 'dist').exists())

    def test_build_target_marks_the_static_site(self):
        atlas.build(self.root)
        self.assertEqual(json.loads((self.root / 'dist/report.json').read_text())['site'], {'target': 'home'})
        atlas.build(self.root, target='pages')
        self.assertEqual(json.loads((self.root / 'dist/report.json').read_text())['site'], {'target': 'pages'})
        with self.assertRaisesRegex(ValueError, 'Unknown build target'):
            atlas.build(self.root, target='remote')

    def test_build_is_reproducible_and_keeps_sources_inert(self):
        atlas.build(self.root)
        first = (self.root / 'dist/build-manifest.json').read_bytes()
        atlas.build(self.root)
        self.assertEqual(first, (self.root / 'dist/build-manifest.json').read_bytes())
        self.assertTrue((self.root / 'dist/.nojekyll').exists())
        self.assertFalse(list((self.root / 'dist/sources').rglob('*.js')))
        report = json.loads((self.root / 'dist/report.json').read_text())
        self.assertNotIn('text', report['sources']['sol-original'][0])
        self.assertNotIn('excerpt_html', report)
        rid = 'deepseek-flash-max-pilot'
        cohort = next(c for c in report['cohorts'] if c['baseline'] == rid)
        self.assertEqual(
            atlas.sha((self.root / 'dist/prompts' / (cohort['id'] + '.txt')).read_bytes()), cohort['prompt_sha256']
        )
        preview = self.root / 'dist' / report['artefacts'][rid]['preview']
        self.assertIn('Content-Security-Policy', preview.read_text())
        self.assertIn('preview-host.js', preview.read_text())
        image = report['artefacts'][rid]['images'][0]
        self.assertEqual(image['path'], 'assets/artefacts/deepseek-flash-max-pilot.png')
        self.assertEqual(image['sha256'], atlas.sha((self.root / image['path']).read_bytes()))
        self.assertEqual(set(report['presentations']), {run['id'] for run in report['runs']})
        presentation = report['presentations'][rid]
        self.assertEqual(presentation['status'], 'profile')
        self.assertEqual(len(presentation['criteria']), 8)
        self.assertTrue((self.root / 'dist/presentations' / (rid + '.json')).is_file())
        self.assertNotIn(rid + '/app.js:527-537', report['evidence_text'])
        self.assertNotIn(rid + '/styles.css:1416-1427', report['evidence_text'])
        world_reply = report['evidence_text'][rid + '/app.js:802-827']
        self.assertIn('A question with no owner was seen crossing the northern meridian.', world_reply)
        for syntax in ['function ', 'const ', 'return ', '=>']:
            self.assertNotIn(syntax, world_reply)
        with (self.root / 'dist/scores.csv').open(newline='') as stream:
            row = next(row for row in csv.DictReader(stream) if row['run'] == rid)
        selected_id = report['review_selections'][rid]
        authored_path = self.root / 'data/reviews' / (selected_id + '.json')
        if authored_path.is_file():
            authored = atlas.load(authored_path)
        else:
            package_review = next(self.root.glob('data/evaluations/*/review.json'))
            authored = atlas.load(package_review)
        self.assertEqual(report['active_reviews'][rid], authored)
        self.assertEqual(row['review'], authored['id'])
        self.assertEqual(row['status'], authored['status'])
        self.assertEqual(row['rubric'], authored['rubric_version'])
        for axis in report['rubric']['axes']:
            value = authored['scores'][axis['id']]['value']
            self.assertEqual(row[axis['id']], '' if value is None else str(value))
        lineage, cursor = [], authored
        while cursor:
            lineage.append(cursor)
            cursor = next((review for review in report['reviews'] if review['id'] == cursor.get('supersedes')), None)
        self.assertTrue(any(all(score['value'] is None for score in review['scores'].values()) for review in lineage))

    def test_a_correction_preserves_its_predecessor_and_becomes_selected(self):
        current = atlas.validate(self.root)['active_reviews']['sol-original']
        count = len(atlas.validate(self.root)['reviews'])
        successor = copy.deepcopy(current)
        successor.update(id='human-review-1', supersedes=current['id'], reviewer='Test reviewer')
        successor['scores']['touch']['value'] = 4
        atlas.add_review(self.root, successor)
        data = atlas.validate(self.root)
        self.assertEqual(data['active_reviews']['sol-original']['id'], 'human-review-1')
        self.assertEqual(len(data['reviews']), count + 1)

    def test_ordinary_corrections_still_allow_only_one_successor(self):
        current = atlas.validate(self.root)['active_reviews']['sol-original']
        first = copy.deepcopy(current)
        first.update(id='human-review-first', supersedes=current['id'], reviewer='First correction')
        atlas.add_review(self.root, first)
        second = copy.deepcopy(current)
        second.update(id='human-review-second', supersedes=current['id'], reviewer='Second correction')
        with self.assertRaisesRegex(ValueError, 'one correction successor'):
            atlas.add_review(self.root, second)

    def test_independent_reviews_do_not_become_silent_replacements(self):
        current = atlas.validate(self.root)['active_reviews']['sol-original']
        other = copy.deepcopy(current)
        other.update(id='unselected-review', reviewer='Second reader', supersedes=None)
        selected = atlas.add_review(self.root, other)
        self.assertEqual(selected, current['id'])
        self.assertEqual(atlas.validate(self.root)['active_reviews']['sol-original']['id'], current['id'])
        atlas.select_review(self.root, other['id'])
        self.assertEqual(atlas.validate(self.root)['active_reviews']['sol-original']['id'], other['id'])

    def test_baseline_identity_is_explicit(self):
        path = self.root / 'data/runs.json'
        data = json.loads(path.read_text())
        data['cohorts'][0]['baseline'] = 'missing-run'
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'baseline'):
            atlas.validate(self.root)

    def test_build_does_not_normalise_or_recalculate_authored_scores(self):
        # Committed review bytes are pinned, so a changed reading arrives as a new
        # review through add-review; the build must still publish it verbatim.
        current = atlas.validate(self.root)['active_reviews']['sol-original']
        successor = copy.deepcopy(current)
        successor.update(id='human-score-passthrough', supersedes=current['id'], reviewer='Test reviewer')
        successor['scores']['touch']['value'] = 1
        atlas.add_review(self.root, successor)
        atlas.build(self.root)
        report = json.loads((self.root / 'dist/report.json').read_text())
        self.assertEqual(report['active_reviews']['sol-original']['scores']['touch']['value'], 1)


if __name__ == '__main__':
    unittest.main()
