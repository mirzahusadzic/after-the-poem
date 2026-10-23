"""Admission hardening for central atlas validation and the atomic dist build.

Uses no models, no network and no candidate execution: reviews are static
records, builds render inert source views, and every tamper case works on a
throwaway copy of the study.
"""

import copy
import importlib.util
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

spec = importlib.util.spec_from_file_location('atlas', ROOT / 'scripts/atlas.py')
atlas = importlib.util.module_from_spec(spec)
spec.loader.exec_module(atlas)

import previews  # noqa: E402  (patched for failure injection)


def make_project(root):
    for name in ['corpus', 'data', 'src', 'assets']:
        shutil.copytree(ROOT / name, root / name)
    for name in ['LICENSE', 'LICENSE-CONTENT', 'LICENSING.md', 'THIRD_PARTY_NOTICES.md']:
        shutil.copyfile(ROOT / name, root / name)
    return root


class ReviewManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = atlas.validate(ROOT)

    def test_committed_manifest_matches_the_repo(self):
        self.assertEqual(atlas.review_manifest(ROOT), atlas.load_review_manifest(ROOT))

    def test_every_committed_review_is_explicitly_pinned(self):
        manifest = atlas.load_review_manifest(ROOT)
        self.assertEqual(set(manifest), set(atlas.review_manifest(ROOT)))

    def test_selection_identity_names_an_existing_review_for_its_run(self):
        for rid, review_id in self.data['selections'].items():
            review = next(r for r in self.data['reviews'] if r['id'] == review_id)
            self.assertEqual(review['run_id'], rid)

    def test_host_path_in_an_admitted_review_is_rejected(self):
        review = copy.deepcopy(self.data['active_reviews']['sol-original'])
        review['id'] = 'tamper-host-path'
        review['summary'] += ' Seen at /private/tmp/dream/world.'
        with self.assertRaisesRegex(ValueError, 'Host-specific path'):
            atlas.validate(ROOT, extra_review=review)

    def test_a_quote_keeps_the_path_its_world_wrote(self):
        # A quote must equal the retained source, so the guard skips it; the authored text stays guarded.
        review = copy.deepcopy(self.data['active_reviews']['dream-claude-fable51-20261022-muse-v5-1'])
        review.update(id='quoted-world-path', reviewer='Second reader', supersedes=None)
        corpus = json.loads((ROOT / 'corpus/runs/dream-claude-fable51-20261022.json').read_text(encoding='utf-8'))
        line = next(f for f in corpus['files'] if f['path'] == 'ledger/journal.jsonl')['text'].splitlines()[0]
        self.assertIn('/private/tmp/', line)
        review['scores']['recognition']['citations'] = [
            {'path': 'ledger/journal.jsonl', 'start': 1, 'end': 1, 'quote': line}
        ]
        result = atlas.validate(ROOT, extra_review=review)
        self.assertTrue(any(r['id'] == review['id'] for r in result['reviews']))
        review['scores']['recognition']['rationale'] += ' Seen at /private/tmp/dream/world.'
        with self.assertRaisesRegex(ValueError, 'Host-specific path'):
            atlas.validate(ROOT, extra_review=review)

    def test_credential_like_key_in_an_admitted_review_is_rejected(self):
        review = copy.deepcopy(self.data['active_reviews']['sol-original'])
        review['id'] = 'tamper-credential-key'
        review['api_key'] = 'not-a-real-secret'
        with self.assertRaisesRegex(ValueError, 'Credential-like metadata key'):
            atlas.validate(ROOT, extra_review=review)

    def test_clean_independent_reading_still_admits(self):
        review = copy.deepcopy(self.data['active_reviews']['sol-original'])
        review.update(id='independent-reading-admission', reviewer='Second reader', supersedes=None)
        result = atlas.validate(ROOT, extra_review=review)
        self.assertTrue(any(r['id'] == review['id'] for r in result['reviews']))
        self.assertEqual(result['active_reviews']['sol-original']['id'], 'sol-original-static-20261019-v2')


class ReviewPinTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='after-poem-admission-')
        self.root = make_project(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_committed_review_bytes_cannot_drift_silently(self):
        path = self.root / 'data/reviews/sol-original-static-20261019-v2.json'
        path.write_text(path.read_text(encoding='utf-8') + ' ')
        with self.assertRaisesRegex(ValueError, 'Committed review changed'):
            atlas.validate(self.root)

    def test_review_file_identity_must_match_its_contents(self):
        source = self.root / 'data/reviews/sol-original-static-20261019-v2.json'
        (self.root / 'data/reviews/renamed.json').write_bytes(source.read_bytes())
        with self.assertRaisesRegex(ValueError, 'Review file identity differs'):
            atlas.validate(self.root)

    def test_admitting_a_review_updates_its_manifest_pin(self):
        current = atlas.validate(self.root)['active_reviews']['sol-original']
        review = copy.deepcopy(current)
        review.update(id='manifest-pinned-successor', supersedes=current['id'], reviewer='Test reviewer')
        atlas.add_review(self.root, review)
        manifest = atlas.load_review_manifest(self.root)
        self.assertEqual(
            manifest[review['id']], atlas.sha((self.root / 'data/reviews' / (review['id'] + '.json')).read_bytes())
        )
        atlas.validate(self.root)

    def successor(self, review_id):
        current = atlas.validate(self.root)['active_reviews']['sol-original']
        review = copy.deepcopy(current)
        review.update(id=review_id, supersedes=current['id'], reviewer='Test reviewer')
        return review

    @staticmethod
    def stored_bytes(review):
        return json.dumps(review, ensure_ascii=False, indent=2).encode('utf-8') + b'\n'

    def test_admission_stopped_after_storing_the_review_is_finished_on_retry(self):
        review = self.successor('stored-before-pin')
        (self.root / 'data/reviews' / (review['id'] + '.json')).write_bytes(self.stored_bytes(review))
        with self.assertRaisesRegex(ValueError, 'Committed review is not pinned'):
            atlas.validate(self.root)
        self.assertEqual(atlas.add_review(self.root, review), review['id'])
        self.assertEqual(atlas.load_review_manifest(self.root)[review['id']], atlas.sha(self.stored_bytes(review)))
        self.assertEqual(atlas.validate(self.root)['active_reviews']['sol-original']['id'], review['id'])

    def test_admission_stopped_after_pinning_the_review_is_finished_on_retry(self):
        review = self.successor('pinned-before-store')
        manifest = self.root / 'data/review-manifest.json'
        record = json.loads(manifest.read_text(encoding='utf-8'))
        record['files'][review['id']] = atlas.sha(self.stored_bytes(review))
        manifest.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'names a missing review'):
            atlas.validate(self.root)
        atlas.add_review(self.root, review)
        self.assertEqual(
            (self.root / 'data/reviews' / (review['id'] + '.json')).read_bytes(), self.stored_bytes(review)
        )
        self.assertEqual(atlas.validate(self.root)['active_reviews']['sol-original']['id'], review['id'])

    def test_a_retry_with_reordered_keys_finishes_the_stored_copy(self):
        review = self.successor('reordered-retry')
        stored = self.stored_bytes(review)
        (self.root / 'data/reviews' / (review['id'] + '.json')).write_bytes(stored)
        reordered = dict(reversed(list(review.items())))
        self.assertNotEqual(self.stored_bytes(reordered), stored)
        atlas.add_review(self.root, reordered)
        self.assertEqual((self.root / 'data/reviews' / (review['id'] + '.json')).read_bytes(), stored)
        self.assertEqual(atlas.load_review_manifest(self.root)[review['id']], atlas.sha(stored))
        atlas.validate(self.root)

    def test_admitting_the_same_review_again_changes_nothing(self):
        review = self.successor('admitted-twice')
        atlas.add_review(self.root, review)
        before = {
            path.name: path.read_bytes()
            for path in [self.root / 'data/review-manifest.json', self.root / 'data/review-selection.json']
        }
        self.assertEqual(atlas.add_review(self.root, review), review['id'])
        for name, payload in before.items():
            self.assertEqual((self.root / 'data' / name).read_bytes(), payload)

    def test_different_bytes_under_a_stored_review_id_are_refused(self):
        review = self.successor('stored-then-changed')
        atlas.add_review(self.root, review)
        review['summary'] += ' Changed afterwards.'
        with self.assertRaisesRegex(ValueError, 'Another review is already stored'):
            atlas.add_review(self.root, review)

    def test_an_interrupt_between_writes_restores_the_study(self):
        review = self.successor('interrupted-admission')
        before = {
            name: (self.root / 'data' / name).read_bytes() for name in ['review-manifest.json', 'review-selection.json']
        }
        real = atlas.fsutil.write_bytes_atomic

        interrupted = []

        def interrupt_the_pin(path, data, **options):
            # The review file is already stored when its pin is interrupted, once.
            if Path(path).name == 'review-manifest.json' and not interrupted:
                interrupted.append(path)
                self.assertTrue((self.root / 'data/reviews' / (review['id'] + '.json')).exists())
                raise KeyboardInterrupt
            return real(path, data, **options)

        with mock.patch.object(atlas.fsutil, 'write_bytes_atomic', side_effect=interrupt_the_pin):
            with self.assertRaises(KeyboardInterrupt):
                atlas.add_review(self.root, review)
        self.assertEqual(len(interrupted), 1)
        for name, payload in before.items():
            self.assertEqual((self.root / 'data' / name).read_bytes(), payload)
        self.assertFalse((self.root / 'data/reviews' / (review['id'] + '.json')).exists())
        atlas.validate(self.root)

    def test_fresh_unreviewed_imports_validate_without_a_pin(self):
        current = atlas.validate(self.root)['active_reviews']['sol-original']
        review = copy.deepcopy(current)
        review.update(
            id='fresh-import-unreviewed',
            status='unreviewed',
            reviewer='Import fixture',
            summary='Fresh import; not assessed.',
            supersedes=None,
            scores={
                axis: {
                    'value': None,
                    'confidence': 'unassessed',
                    'rationale': 'Not assessed.',
                    'citations': [],
                    'seed_span': [1, 1],
                }
                for axis in review['scores']
            },
        )
        (self.root / 'data/reviews' / (review['id'] + '.json')).write_text(
            json.dumps(review, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
        )
        data = atlas.validate(self.root)
        self.assertTrue(any(r['id'] == review['id'] for r in data['reviews']))

    def test_unpinned_scored_reviews_are_rejected(self):
        current = atlas.validate(self.root)['active_reviews']['sol-original']
        review = copy.deepcopy(current)
        review.update(id='unpinned-provisional', supersedes=None)
        (self.root / 'data/reviews' / (review['id'] + '.json')).write_text(
            json.dumps(review, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
        )
        with self.assertRaisesRegex(ValueError, 'Committed review is not pinned'):
            atlas.validate(self.root)

    def test_selection_must_name_a_review_for_its_run(self):
        path = self.root / 'data/review-selection.json'
        record = json.loads(path.read_text(encoding='utf-8'))
        record['selections']['sol-original'] = 'no-such-review'
        path.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Selected reading is missing'):
            atlas.validate(self.root)

    def test_review_provenance_hashes_are_recomputed(self):
        package = self.root / 'data/evaluations/sol-original-muse-v5-1'
        review_path = package / 'review.json'
        review = json.loads(review_path.read_text(encoding='utf-8'))
        review['provenance']['evaluation_sha256'] = '0' * 64
        review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        manifest_path = package / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest['files']['review.json'] = atlas.sha(review_path.read_bytes())
        manifest_path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Evaluation review provenance differs'):
            atlas.validate(self.root)

    def test_representative_only_drift_is_rejected(self):
        package = self.root / 'data/evaluations/sol-original-muse-v5-1'
        review_path = package / 'review.json'
        review = json.loads(review_path.read_text(encoding='utf-8'))
        review['scores']['recognition']['rationale'] = 'Drifted representative wording for testing.'
        review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        manifest_path = package / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest['files']['review.json'] = atlas.sha(review_path.read_bytes())
        manifest_path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Review scores differ'):
            atlas.validate(self.root)

    def test_changed_levels_are_not_drift(self):
        package = self.root / 'data/evaluations/sol-original-muse-v5-1'
        review_path = package / 'review.json'
        review = json.loads(review_path.read_text(encoding='utf-8'))
        review['scores']['recognition']['value'] = 1
        review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        manifest_path = package / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest['files']['review.json'] = atlas.sha(review_path.read_bytes())
        manifest_path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Review scores differ'):
            atlas.validate(self.root)


class AlternativesTests(unittest.TestCase):
    def test_carriage_alternatives_dedupe_in_reading_order(self):
        carriage = {
            'readings': [
                {'seed_reading': {'alternatives': ['second reading', 'first reading']}},
                {'seed_reading': {'alternatives': ['first reading', 'third reading']}},
                {'seed_reading': {'alternatives': []}},
                {},
            ]
        }
        self.assertEqual(
            atlas.carriage_alternatives(carriage), ['second reading', 'first reading', 'third reading']
        )
        self.assertEqual(atlas.carriage_alternatives({}), [])
        self.assertEqual(atlas.carriage_alternatives(None), [])

    def test_carriage_alternatives_keep_only_strings_and_withhold_host_paths(self):
        carriage = {'readings': [{'seed_reading': {'alternatives': [None, 7, 'kept', '  ']}}]}
        self.assertEqual(atlas.carriage_alternatives(carriage), ['kept'])
        hosted = {'readings': [{'seed_reading': {'alternatives': ['dreamed in /private/tmp/x/world']}}]}
        alternatives = atlas.carriage_alternatives(hosted)
        self.assertEqual(len(alternatives), 1)
        self.assertNotIn('/private/tmp/x/world', alternatives[0])
        self.assertIn('dreaming machine', alternatives[0])


class SourceLicenseTests(unittest.TestCase):
    def test_code_pages_lead_with_the_code_license(self):
        page = atlas.source_page('app.js', 'const x = 1;\n', atlas.sha(b'x'), path='app.js')
        code = page.index('Code · MIT')
        content = page.index('Content · CC BY 4.0')
        self.assertLess(code, content)
        self.assertIn('licenses/MIT.txt', page)

    def test_prose_pages_lead_with_the_content_license(self):
        for kwargs in [{'path': 'WORLD.md'}, {}]:
            page = atlas.source_page('WORLD.md', 'A world.\n', atlas.sha(b'x'), **kwargs)
            content = page.index('Content · CC BY 4.0')
            code = page.index('Code · MIT')
            self.assertLess(content, code)


class AtomicBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='after-poem-build-')
        self.root = make_project(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_publishes_alternatives_and_a_verifiable_manifest(self):
        out = atlas.build(self.root)
        report = json.loads((out / 'report.json').read_text(encoding='utf-8'))
        self.assertIn('sol-original', report['alternatives'])
        self.assertTrue(
            any('elder comforts Sol' in alternative for alternative in report['alternatives']['sol-original'])
        )
        for alternatives in report['alternatives'].values():
            self.assertEqual(len(alternatives), len(set(alternatives)))
            for alternative in alternatives:
                self.assertIsInstance(alternative, str)
        manifest = atlas.verify_build_manifest(out)
        self.assertNotIn('build-manifest.json', manifest)
        # The published root is readable by everyone, not mkdtemp's owner-only mode.
        self.assertEqual(out.stat().st_mode & 0o777, 0o755)
        from page_prepare import HOST_PATH_TEXT

        for value in report['evidence_text'].values():
            self.assertIsNone(HOST_PATH_TEXT.search(value or ''))

    def test_failed_build_preserves_the_previous_dist(self):
        out = atlas.build(self.root)
        before = (out / 'build-manifest.json').read_bytes()
        report_before = (out / 'report.json').read_text(encoding='utf-8')
        with mock.patch.object(previews, 'build_browser_previews', side_effect=RuntimeError('injected')):
            with self.assertRaisesRegex(RuntimeError, 'injected'):
                atlas.build(self.root)
        self.assertEqual((out / 'build-manifest.json').read_bytes(), before)
        self.assertEqual((out / 'report.json').read_text(encoding='utf-8'), report_before)
        self.assertEqual(sorted(self.root.glob('dist-*')), [])
        self.assertFalse((self.root / 'dist.previous').exists())
        # The next build recovers on the same project.
        out = atlas.build(self.root)
        self.assertEqual(
            json.loads((out / 'report.json').read_text(encoding='utf-8'))['alternatives'],
            json.loads(report_before)['alternatives'],
        )

    def test_build_manifest_detects_a_changed_file(self):
        out = atlas.build(self.root)
        (out / 'scores.csv').write_text('tampered\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Build manifest differs'):
            atlas.verify_build_manifest(out)


if __name__ == '__main__':
    unittest.main()
