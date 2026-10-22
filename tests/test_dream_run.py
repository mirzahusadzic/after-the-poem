import json
import shutil
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from evaluation_fixtures import EDITORIAL

sys.path.insert(0, str(ROOT / 'scripts'))
import atlas
import dream_run


class DreamRunTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='after-poem-dream-run-')
        self.root = Path(self.temporary.name)
        for name in ['corpus', 'data', 'src', 'assets']:
            shutil.copytree(ROOT / name, self.root / name, ignore=EDITORIAL)
        for name in ['LICENSE', 'LICENSE-CONTENT', 'LICENSING.md', 'THIRD_PARTY_NOTICES.md']:
            shutil.copyfile(ROOT / name, self.root / name)
        shutil.rmtree(self.root / 'data/evaluations', ignore_errors=True)

    def tearDown(self):
        self.temporary.cleanup()

    def test_dream_profile_generates_and_imports_a_named_run(self):
        profile = json.loads((ROOT / 'examples/dream-profile-deepseek.json').read_text(encoding='utf-8'))
        profile.update(
            campaign_id='dream-test-20261021',
            output='.local/dream-test',
            evaluation_id='dream-test-evaluation',
            review_id='dream-test-review',
        )
        path = self.root / 'dream-profile.json'
        path.write_text(json.dumps(profile), encoding='utf-8')
        result = dream_run.run(self.root, path, dry_run=True, accept_run=False)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['run_id'], 'dream-test-20261021-deepseek-s1')
        self.assertEqual(result['integration_status'], 'skipped-synthetic')
        data = atlas.validate(Path(result['campaign']) / 'report')
        self.assertIn(result['run_id'], {run['id'] for run in data['runs']})
        self.assertIn(result['run_id'] + '-unreviewed-v1', {review['id'] for review in data['reviews']})
        resumed = dream_run.run(self.root, path, dry_run=True, accept_run=False)
        self.assertEqual(resumed['run_id'], result['run_id'])

    def test_live_dream_run_requires_acceptance_before_output(self):
        profile = json.loads((ROOT / 'examples/dream-profile-deepseek.json').read_text(encoding='utf-8'))
        profile.update(campaign_id='dream-live-test', output='.local/dream-live-test')
        path = self.root / 'dream-profile.json'
        path.write_text(json.dumps(profile), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'accept-run'):
            dream_run.run(self.root, path)
        self.assertFalse((self.root / '.local/dream-live-test').exists())

    def test_runner_and_scorer_are_independent_profile_fields(self):
        profile = json.loads((ROOT / 'examples/dream-profile-deepseek.json').read_text(encoding='utf-8'))
        profile.update(generator_model='runner/model', evaluator_model='scorer/model')
        path = self.root / 'dream-profile.json'
        path.write_text(json.dumps(profile), encoding='utf-8')
        loaded = dream_run.load_profile(self.root, path)
        self.assertEqual(loaded['generator_model'], 'runner/model')
        self.assertEqual(loaded['evaluator_model'], 'scorer/model')
        config = dream_run.campaign_config(loaded)
        self.assertEqual(config['generators'][0]['model'], 'runner/model')
        self.assertEqual(config['reviewers'], [])

    def test_runner_and_scorer_can_be_overridden_by_explicit_flags(self):
        profile = json.loads((ROOT / 'examples/dream-profile-deepseek.json').read_text(encoding='utf-8'))
        path = self.root / 'dream-profile.json'
        path.write_text(json.dumps(profile), encoding='utf-8')
        loaded = dream_run.load_profile(
            self.root,
            path,
            runner_model='openrouter/google/gemini-4-argon',
            runner_reasoning='high',
            scorer_model='deepseek/deepseek-v4.1-flash-1017',
            scorer_reasoning='max',
        )
        self.assertEqual(loaded['generator_model'], 'openrouter/google/gemini-4-argon')
        self.assertEqual(loaded['generator_reasoning'], 'high')
        self.assertEqual(loaded['evaluator_model'], 'deepseek/deepseek-v4.1-flash-1017')
        self.assertEqual(loaded['evaluator_reasoning'], 'max')

    def test_preflight_checks_both_models_through_the_same_frozen_router_runtime(self):
        profile = json.loads((ROOT / 'examples/dream-profile-deepseek.json').read_text(encoding='utf-8'))
        path = self.root / 'dream-profile.json'
        path.write_text(json.dumps(profile), encoding='utf-8')
        frozen = {
            'codex_version': 'fixture',
            'router': {'policy': 'preserve-model-v1'},
            'model_capabilities': {'runner/model': {}, 'scorer/model': {}},
            'capability_registry_sha256': 'a' * 64,
        }
        with mock.patch.object(dream_run.worker, 'freeze_runtime', return_value=frozen) as freeze:
            result = dream_run.preflight(
                self.root,
                path,
                runner_model='runner/model',
                runner_reasoning='high',
                scorer_model='scorer/model',
                scorer_reasoning='max',
            )
        jobs = freeze.call_args.args[1]
        self.assertEqual(
            [(job['model'], job['reasoning']) for job in jobs], [('runner/model', 'high'), ('scorer/model', 'max')]
        )
        self.assertEqual(result['gateway'], 'codex-unified-model-router')

    def test_live_flow_executes_generation_before_import_and_evaluation(self):
        profile = json.loads((ROOT / 'examples/dream-profile-deepseek.json').read_text(encoding='utf-8'))
        profile.update(
            campaign_id='dream-order-test',
            output='.local/dream-order-test',
            evaluation_id='dream-order-evaluation',
            review_id='dream-order-review',
        )
        path = self.root / 'dream-profile.json'
        path.write_text(json.dumps(profile), encoding='utf-8')
        events = []

        def ordered_validate(project):
            return {'runs': [{'id': 'dream-order-test-deepseek-s1'}]}

        def execute(campaign, accept_run=False):
            events.append(('execute', accept_run))
            return {'all_succeeded': True}

        def admit(campaign, project):
            events.append(('admit', project))

        def evaluate(*args, **kwargs):
            events.append(('evaluate', kwargs['model'], kwargs['reasoning']))
            return {
                'status': 'completed',
                'integration_status': 'integrated',
                'ui_url': 'index.html#run=dream-order-test-deepseek-s1',
            }

        def capture(project, run_id):
            events.append(('capture', run_id))
            return {'status': 'captured'}

        def prepare(config_path, campaign_dir, project, **options):
            campaign_dir.mkdir(parents=True)
            return {'config': json.loads(config_path.read_text(encoding='utf-8'))}

        with (
            mock.patch.object(dream_run.atlas, 'validate', side_effect=ordered_validate),
            mock.patch.object(dream_run.pipeline, 'prepare', side_effect=prepare),
            mock.patch.object(dream_run.pipeline, 'execute', side_effect=execute),
            mock.patch.object(dream_run.pipeline, 'admit', side_effect=admit),
            mock.patch.object(dream_run.artefact_capture, 'ensure', side_effect=capture),
            mock.patch.object(dream_run.evaluation_run, 'run', side_effect=evaluate),
        ):
            result = dream_run.run(self.root, path, accept_run=True)
        self.assertEqual([event[0] for event in events], ['execute', 'admit', 'capture', 'evaluate'])
        self.assertEqual(events[0][1], True)
        self.assertEqual(events[3][1:], (profile['evaluator_model'], profile['evaluator_reasoning']))
        self.assertEqual(result['run_id'], 'dream-order-test-deepseek-s1')

    def test_transient_upstream_rejection_is_archived_and_retried_in_fresh_campaign(self):
        profile = json.loads((ROOT / 'examples/dream-profile-deepseek.json').read_text(encoding='utf-8'))
        profile.update(
            campaign_id='dream-retry-test',
            output='.local/dream-retry-test',
            evaluation_id='dream-retry-evaluation',
            review_id='dream-retry-review',
        )
        path = self.root / 'dream-profile.json'
        path.write_text(json.dumps(profile), encoding='utf-8')
        campaigns = []

        def prepare(config_path, campaign_dir, project, **options):
            campaign_dir.mkdir(parents=True)
            campaigns.append(campaign_dir)
            return {'config': json.loads(config_path.read_text(encoding='utf-8'))}

        def execute(campaign, accept_run=False):
            if len(campaigns) == 1:
                job = campaign / 'jobs/generation-0001'
                job.mkdir(parents=True)
                (job / 'receipt.json').write_text(
                    json.dumps(
                        {
                            'status': 'failed',
                            'error': 'BundleError',
                            'runtime_checks': {'status': 'passed'},
                            'isolation': {'status': 'passed'},
                        }
                    ),
                    encoding='utf-8',
                )
                return {
                    'all_succeeded': False,
                    'complete': True,
                    'jobs': [{'job': 'generation-0001', 'status': 'failed'}],
                }
            return {
                'all_succeeded': True,
                'complete': True,
                'jobs': [{'job': 'generation-0001', 'status': 'completed'}],
            }

        with (
            mock.patch.object(dream_run.pipeline, 'prepare', side_effect=prepare),
            mock.patch.object(dream_run.pipeline, 'execute', side_effect=execute),
            mock.patch.object(dream_run.pipeline, 'admit'),
            mock.patch.object(
                dream_run.atlas, 'validate', return_value={'runs': [{'id': 'dream-retry-test-deepseek-s1'}]}
            ),
            mock.patch.object(dream_run.artefact_capture, 'ensure', return_value={'status': 'captured'}),
            mock.patch.object(
                dream_run.evaluation_run,
                'run',
                return_value={
                    'status': 'completed',
                    'integration_status': 'integrated',
                    'ui_url': 'index.html#run=dream-retry-test-deepseek-s1',
                },
            ),
        ):
            result = dream_run.run(self.root, path, accept_run=True, generation_attempts=3)
        self.assertEqual(len(campaigns), 2)
        self.assertEqual(result['generation_attempts_used'], 2)
        archived = Path(result['archived_generation_attempts'][0])
        self.assertTrue(archived.is_dir())
        actual_archive = self.root / '.local/dream-retry-test/generation-attempts/attempt-001'
        self.assertEqual(archived.resolve(), actual_archive.resolve())
        record = json.loads((actual_archive / 'generation-archive.json').read_text(encoding='utf-8'))
        self.assertEqual(record['receipt_errors']['generation-0001']['error'], 'BundleError')

    def test_integrity_failure_is_not_retried(self):
        profile = json.loads((ROOT / 'examples/dream-profile-deepseek.json').read_text(encoding='utf-8'))
        profile.update(
            campaign_id='dream-integrity-test',
            output='.local/dream-integrity-test',
            evaluation_id='dream-integrity-evaluation',
            review_id='dream-integrity-review',
        )
        path = self.root / 'dream-profile.json'
        path.write_text(json.dumps(profile), encoding='utf-8')

        def prepare(config_path, campaign_dir, project, **options):
            campaign_dir.mkdir(parents=True)
            return {'config': {}}

        def execute(campaign, accept_run=False):
            job = campaign / 'jobs/generation-0001'
            job.mkdir(parents=True)
            (job / 'receipt.json').write_text(
                json.dumps(
                    {
                        'status': 'failed',
                        'error': 'runtime-integrity-failed',
                        'runtime_checks': {'status': 'failed'},
                        'isolation': {'status': 'passed'},
                    }
                ),
                encoding='utf-8',
            )
            return {'all_succeeded': False, 'jobs': [{'job': 'generation-0001', 'status': 'failed'}]}

        with (
            mock.patch.object(dream_run.pipeline, 'prepare', side_effect=prepare),
            mock.patch.object(dream_run.pipeline, 'execute', side_effect=execute) as run,
        ):
            with self.assertRaisesRegex(ValueError, 'non-retryable'):
                dream_run.run(self.root, path, accept_run=True, generation_attempts=3)
        self.assertEqual(run.call_count, 1)


if __name__ == '__main__':
    unittest.main()
