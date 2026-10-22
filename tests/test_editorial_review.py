import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from evaluation_fixtures import EDITORIAL

sys.path.insert(0, str(ROOT / 'scripts'))
import atlas
import review_pipeline as reviews


class EditorialReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='poem-editorial-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in ['data', 'corpus', 'src', 'assets']:
            shutil.copytree(ROOT / name, self.root / name, ignore=EDITORIAL)
        self.packet_dir = self.root / 'packet'
        self.identity = reviews.export_packet(self.root, 'sol-original', self.packet_dir)
        packet = json.loads((self.packet_dir / 'packet.json').read_bytes())
        self.answer = json.loads(reviews.synthetic_result(packet['rubric']))
        self.answer['summary'] = 'Synthetic editorial validation fixture, not an assessment of this candidate.'
        source = next(source for source in packet['sources'] if source['lines'])
        self.answer['scores']['recognition'].update(
            value=1,
            confidence='low',
            rationale='A validation fixture only.',
            citations=[{'source_id': source['source_id'], 'start': 1, 'end': 1, 'quote': source['lines'][0]}],
        )
        self.answer_path = self.root / 'answer.json'
        self.save_answer()
        self.predecessor = atlas.validate(self.root)['selections']['sol-original']

    def save_answer(self):
        self.answer_path.write_text(json.dumps(self.answer), encoding='utf-8')

    def validate(self, **changes):
        args = dict(
            project=self.root,
            packet_dir=self.packet_dir,
            answer_path=self.answer_path,
            review_id='editorial-fixture',
            reviewer='Fixture reader',
            date='2026-10-21',
            context='Known identity; synthetic validation fixture.',
            supersedes=self.predecessor,
        )
        args.update(changes)
        return reviews.editorial_review(**args)

    def test_editorial_admission_preserves_history_without_worker_attestation(self):
        parent = self.root / 'data/reviews' / (self.predecessor + '.json')
        before = parent.read_bytes()
        with (
            mock.patch('subprocess.Popen', side_effect=AssertionError('No process during editorial review')),
            mock.patch('socket.create_connection', side_effect=AssertionError('No network during editorial review')),
        ):
            review = self.validate()
            atlas.add_review(self.root, review)
        self.assertEqual(parent.read_bytes(), before)
        self.assertEqual(atlas.validate(self.root)['selections']['sol-original'], review['id'])
        self.assertEqual(self.validate(), review)
        self.assertEqual(review['status'], 'provisional')
        self.assertEqual(review['provenance']['method'], 'editorial-source-reading-v1')
        self.assertEqual(review['provenance']['blinding'], 'none')
        self.assertEqual(review['provenance']['isolation']['status'], 'not-performed')
        self.assertFalse(review['provenance']['new_model_calls'])
        self.assertFalse(review['provenance']['preview_evidence_used'])
        self.assertNotIn('runtime_checks', review['provenance'])
        self.assertEqual(review['provenance']['packet_sha256'], self.identity['packet_sha256'])
        self.assertEqual(review['provenance']['answer_sha256'], atlas.sha(self.answer_path.read_bytes()))

    def test_packet_reuse_or_tampering_cannot_change_evidence(self):
        with self.assertRaisesRegex(ValueError, 'already exists'):
            reviews.export_packet(self.root, 'sol-original', self.packet_dir)
        for name in ['packet.json', 'identity.json']:
            path = self.packet_dir / name
            original = path.read_bytes()
            path.chmod(0o600)
            if name == 'packet.json':
                path.write_bytes(original + b' ')
            else:
                changed = copy.deepcopy(self.identity)
                changed['source_map']['source-0001'] = 'invented.txt'
                path.write_text(json.dumps(changed))
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'Packet inputs differ'):
                self.validate()
            path.write_bytes(original)

    def test_invented_quotes_missing_axes_and_false_identity_are_rejected(self):
        self.answer['scores']['recognition']['citations'][0]['quote'] = 'An invented passage.'
        self.save_answer()
        with self.assertRaisesRegex(ValueError, 'quote differs'):
            self.validate()
        del self.answer['scores']['recognition']
        self.save_answer()
        with self.assertRaisesRegex(ValueError, 'every rubric axis'):
            self.validate()
        with self.assertRaisesRegex(ValueError, 'context disclosure'):
            self.validate(context='')
        with self.assertRaisesRegex(ValueError, 'Invalid review ID'):
            self.validate(review_id='../outside')

    def test_editorial_cli_admits_once_and_rejects_conflicting_reuse(self):
        command = [
            sys.executable,
            str(ROOT / 'scripts/review_pipeline.py'),
            'admit-editorial',
            str(self.packet_dir),
            '--answer',
            str(self.answer_path),
            '--review-id',
            'editorial-cli-fixture',
            '--reviewer',
            'Fixture reader',
            '--date',
            '2026-10-21',
            '--context',
            'Known identity; fixture only.',
            '--supersedes',
            self.predecessor,
            '--project',
            str(self.root),
        ]
        for _ in range(2):
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Admitted provisional editorial review', result.stdout)
        self.answer['summary'] = 'A conflicting replacement.'
        self.save_answer()
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Existing review differs', result.stderr)

    def test_selected_pilot_reading_pins_evidence_without_worker_claims(self):
        rid = 'deepseek-flash-max-pilot'
        data = atlas.validate(ROOT)
        review = data['active_reviews'][rid]
        editorial = next(item for item in data['reviews'] if item['id'] == rid + '-editorial-20261021-v1')
        packet, identity = reviews.prepare_packet(ROOT, rid)
        provenance = editorial['provenance']
        self.assertEqual(review['status'], 'provisional')
        self.assertEqual(review['live_cooperation'], 'unassessed')
        self.assertEqual(editorial['corpus_sha256'], identity['corpus_sha256'])
        self.assertEqual(editorial['rubric_sha256'], identity['rubric_sha256'])
        self.assertEqual(provenance['packet_sha256'], atlas.sha(packet))
        self.assertEqual(provenance['prompt_sha256'], identity['prompt_sha256'])
        self.assertEqual(provenance['method'], 'editorial-source-reading-v1')
        self.assertEqual(provenance['blinding'], 'none')
        self.assertEqual(provenance['isolation']['status'], 'not-performed')
        self.assertFalse(provenance['new_model_calls'])
        self.assertFalse(provenance['preview_evidence_used'])
        self.assertIn('earlier sandboxed preview', provenance['context_disclosure'])
        self.assertEqual(provenance['validator_sha256'], atlas.sha(atlas.canonical(provenance['validator_files'])))
        self.assertNotIn('runtime_checks', provenance)
        self.assertNotIn('reported', provenance)
        self.assertTrue(all(score['citations'] and score['rationale'] for score in editorial['scores'].values()))


if __name__ == '__main__':
    unittest.main()
