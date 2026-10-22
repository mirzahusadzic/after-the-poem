import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import atlas
import bundles
import pipeline
import reconcile_generation as reconcile
from bundles import sha
from evaluation_fixtures import isolated_project


TRANSPORT = b'[model_providers.unified]\nname = "Codex Unified Router"\n'
STANZA = '\n[projects."' + str(ROOT) + '"]\ntrust_level = "trusted"\n'


class ReconcileGenerationTests(unittest.TestCase):
    """A dry-run generation is reshaped into the exact failure the early dream runs recorded."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='after-poem-reconcile-')
        self.root = Path(self.temporary.name)
        config = json.loads((ROOT / 'examples/campaign.json').read_text())
        config.update(generators=config['generators'][:1], reviewers=[], display_reviewer=None)
        (self.root / 'config.json').write_text(json.dumps(config))
        self.campaign = self.root / 'campaign'
        pipeline.prepare(self.root / 'config.json', self.campaign, dry_run=True)
        pipeline.execute(self.campaign)
        self.job = self.campaign / 'jobs/generation-0001'
        (self.campaign / 'transport.config.toml').write_bytes(TRANSPORT)
        home = self.job / 'control/codex-home'
        home.mkdir(parents=True)
        (home / 'config.toml').write_bytes(TRANSPORT + STANZA.encode())
        (self.job / 'control/integrity-failure.txt').write_text('Worker transport changed', encoding='utf-8')
        receipt = json.loads((self.job / 'receipt.json').read_text())
        receipt['runtime'] = dict(receipt['runtime'], transport_config_sha256=sha(TRANSPORT))
        receipt.update(
            status='failed',
            error='runtime-integrity-failed',
            exit_code=0,
            stopped_reason=None,
            isolation={'status': 'passed'},
            runtime_checks={
                'status': 'failed',
                'phases': [
                    {'phase': 'before-audit', 'status': 'passed'},
                    {'phase': 'before-launch', 'status': 'passed'},
                    {'phase': 'after-process', 'status': 'failed'},
                ],
            },
        )
        self.write_receipt(receipt)
        self.cohort = {'id': 'dream-4521-v1', 'title': 'The Programmer and the Glory Portal'}

    def tearDown(self):
        self.temporary.cleanup()

    def write_receipt(self, receipt):
        (self.job / 'receipt.json').write_text(json.dumps(receipt), encoding='utf-8')

    def test_clean_finish_is_reconciled_without_touching_the_original(self):
        original = sha((self.job / 'bundle/run.json').read_bytes())
        receipt = (self.job / 'receipt.json').read_bytes()
        bundle = reconcile.reconciled_bundle(self.job, cohort=self.cohort, label='Model · dream 1', title='A world')
        self.assertEqual(sha((self.job / 'bundle/run.json').read_bytes()), original)
        self.assertEqual((self.job / 'receipt.json').read_bytes(), receipt)
        run = bundles.validate_bundle(bundle)['run']
        self.assertEqual((run['outcome']['status'], run['cohort']['id']), ('completed', 'dream-4521-v1'))
        self.assertEqual(run['reconciliation']['original_receipt']['error'], 'runtime-integrity-failed')
        self.assertEqual(run['reconciliation']['rule'], reconcile.RULE)

    def test_reconciled_run_joins_the_cohort_of_its_prompt(self):
        project = isolated_project(self.root / 'project')
        result = reconcile.admit(project, self.job, cohort_id='dream-4521-v1', label='Model · dream 1', title='A world')
        data = atlas.validate(project)
        run = next(item for item in data['runs'] if item['id'] == result['run_id'])
        self.assertEqual((run['cohort'], run['condition']), ('dream-4521-v1', 'completed'))
        self.assertEqual(data['active_reviews'][run['id']]['status'], 'unreviewed')
        retained = json.loads((project / 'data/bundles' / run['id'] / 'run.json').read_text())
        self.assertIn('reconciliation', retained)
        again = reconcile.admit(project, self.job, cohort_id='dream-4521-v1', label='Model · dream 1', title='A world')
        self.assertEqual(again['status'], 'already-admitted')

    def test_any_other_config_change_is_rejected(self):
        (self.job / 'control/codex-home/config.toml').write_bytes(TRANSPORT + STANZA.encode() + b'extra = true\n')
        with self.assertRaisesRegex(ValueError, 'Worker transport changed'):
            reconcile.verify(self.job)

    def test_changed_world_is_rejected(self):
        next(path for path in (self.job / 'world').rglob('*') if path.is_file()).write_text('changed', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Preserved world differs'):
            reconcile.verify(self.job)

    def test_other_failures_are_not_reconcilable(self):
        receipt = json.loads((self.job / 'receipt.json').read_text())
        receipt['runtime_checks']['phases'][1]['status'] = 'failed'
        self.write_receipt(receipt)
        with self.assertRaisesRegex(ValueError, 'Only the after-process check'):
            reconcile.verify(self.job)
        self.write_receipt(dict(receipt, status='cancelled', error='operator-interrupted'))
        with self.assertRaisesRegex(ValueError, 'Only jobs rejected'):
            reconcile.verify(self.job)


if __name__ == '__main__':
    unittest.main()
