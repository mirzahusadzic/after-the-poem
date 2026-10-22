import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import atlas


class WithdrawalTests(unittest.TestCase):
    """A withdrawn run keeps its bundle and readings, says why it left, and cannot be shown on the release page."""

    @classmethod
    def setUpClass(cls):
        cls.data = atlas.validate(ROOT)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='after-poem-withdrawals-')
        self.root = Path(self.temporary.name)
        (self.root / 'data').mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, name, value):
        (self.root / 'data' / name).write_text(json.dumps(value), encoding='utf-8')

    def test_a_withdrawn_world_keeps_its_records(self):
        for run_id, withdrawal in self.data['withdrawals'].items():
            self.assertIn(run_id, {run['id'] for run in self.data['runs']})
            self.assertIn(run_id, self.data['corpora'])
            self.assertTrue(withdrawal['reason'].strip())

    def test_a_withdrawal_needs_a_known_run_a_date_and_a_reason(self):
        runs = {run['id']: run for run in self.data['runs']}
        self.assertEqual(atlas.validate_withdrawals(self.root, runs), {})
        known = next(run['id'] for run in self.data['runs'] if not run.get('source_run_id'))
        entry = {'run_id': known, 'date': '2026-10-22', 'reason': 'Misnamed dreamer.'}
        self.write('withdrawals.json', {'schema_version': 1, 'withdrawals': [entry]})
        self.assertEqual(atlas.validate_withdrawals(self.root, runs)[known]['date'], '2026-10-22')
        for broken in [
            dict(entry, run_id='no-such-run'),
            dict(entry, date='22 October'),
            dict(entry, reason=' '),
            dict(entry, why='extra'),
        ]:
            self.write('withdrawals.json', {'schema_version': 1, 'withdrawals': [broken]})
            with self.assertRaises(ValueError):
                atlas.validate_withdrawals(self.root, runs)
        self.write('withdrawals.json', {'schema_version': 1, 'withdrawals': [entry, entry]})
        with self.assertRaisesRegex(ValueError, 'withdrawn once'):
            atlas.validate_withdrawals(self.root, runs)

    def test_the_release_page_cannot_list_a_withdrawn_run(self):
        presentation = json.loads((ROOT / 'data/presentation.json').read_text(encoding='utf-8'))
        shown = presentation['shown_runs'][-1]
        self.write('presentation.json', presentation)
        data = dict(self.data, withdrawals={shown: {'date': '2026-10-22', 'reason': 'For this test.'}})
        with self.assertRaisesRegex(ValueError, 'withdrawn'):
            atlas.project_reader(data, self.root)


class RecordingTests(unittest.TestCase):
    """A console recording for the static site names its world's exact corpus and carries no host paths."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='after-poem-recordings-')
        self.root = Path(self.temporary.name)
        (self.root / 'data/recordings').mkdir(parents=True)
        data = atlas.validate(ROOT)
        self.runs = {run['id']: run for run in data['runs']}
        self.artefacts = data['artefacts']
        self.good = json.loads((ROOT / 'data/recordings/astra-garden.json').read_text(encoding='utf-8'))

    def tearDown(self):
        self.temporary.cleanup()

    def check(self, record, name='astra-garden'):
        (self.root / 'data/recordings' / (name + '.json')).write_text(json.dumps(record), encoding='utf-8')
        return atlas.validate_recordings(self.root, self.runs, self.artefacts)

    def test_the_study_records_its_shown_consoles(self):
        recordings = atlas.validate(ROOT)['recordings']
        self.assertIn('dream-claude-fable51-20261022', recordings)
        self.assertNotIn('/private/', json.dumps(recordings))

    def test_a_recording_must_match_its_world_and_carry_no_host_path(self):
        self.assertIn('astra-garden', self.check(self.good))
        for broken in [
            dict(self.good, corpus_sha256='0' * 64),
            dict(self.good, recorded_on='today'),
            dict(self.good, entries=[dict(self.good['entries'][0], output='at /Users/someone/world')]),
            dict(self.good, entries=[]),
            dict(self.good, extra=True),
        ]:
            with self.assertRaises(ValueError):
                self.check(broken)
        with self.assertRaises(ValueError):
            self.check(dict(self.good, run_id='sol-original'), 'sol-original')


if __name__ == '__main__':
    unittest.main()
