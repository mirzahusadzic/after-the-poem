import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import atlas
import bundles
import codex_worker as worker
import pipeline
import review_pipeline as reviews
import sealed_run


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='after-poem-campaign-test-')
        self.root = Path(self.temporary.name)
        self.config = json.loads((ROOT / 'examples/campaign.json').read_text())
        self.config['generators'] = self.config['generators'][:1]
        self.config_file = self.root / 'config.json'
        self.config_file.write_text(json.dumps(self.config))
        self.campaign = self.root / 'campaign'

    def tearDown(self):
        self.temporary.cleanup()

    def prepare(self):
        return pipeline.prepare(self.config_file, self.campaign, dry_run=True)

    def test_exact_prompt_capture_review_import_build_and_resume(self):
        self.prepare()
        result = pipeline.execute(self.campaign)
        self.assertTrue(result['all_succeeded'])
        self.assertEqual([item['status'] for item in result['jobs']], ['completed', 'admitted', 'admitted'])
        output = self.campaign / 'jobs/generation-0001'
        self.assertEqual((output / 'prompt.txt').read_bytes(), sealed_run.PROMPT_PATH.read_bytes())
        self.assertFalse((output / 'scratch/DREAM_RESULT.json').exists())
        bundle = bundles.validate_bundle(output / 'bundle')
        self.assertEqual(bundle['run']['condition']['reported'], {'model': None, 'provider': None})
        self.assertEqual(bundle['run']['condition']['budgets']['wall_time_seconds'], 1800)
        data = atlas.validate(self.campaign / 'report')
        run_id = 'dream-common-v1-deepseek-s1'
        readings = [review for review in data['reviews'] if review['run_id'] == run_id]
        self.assertEqual(len(readings), 3)
        self.assertTrue(all(score['value'] is None for review in readings for score in review['scores'].values()))
        self.assertTrue((self.campaign / 'report/dist/index.html').is_file())
        with mock.patch.object(worker, 'run_job', side_effect=AssertionError('A finished job must never run again')):
            self.assertTrue(pipeline.execute(self.campaign)['all_succeeded'])

    def test_synthetic_campaign_cannot_be_admitted(self):
        self.prepare()
        with self.assertRaisesRegex(ValueError, 'Synthetic'):
            pipeline.admit(self.campaign)

    def test_live_calls_require_acceptance_before_any_job(self):
        self.prepare()
        path = self.campaign / 'campaign.json'
        plan = bundles.load_json(path)
        plan['runtime']['dry_run'] = False
        worker.write_json(path, plan)
        (self.campaign / 'campaign.sha256').write_text(bundles.sha(bundles.canonical(plan)))
        with mock.patch.object(worker, 'run_job') as launch:
            with self.assertRaisesRegex(ValueError, 'accept-run'):
                pipeline.execute(self.campaign)
            launch.assert_not_called()

    def test_changed_inputs_and_protocol_are_rejected(self):
        self.prepare()
        (self.campaign / 'prompt.txt').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'prompt changed'):
            pipeline.execute(self.campaign)

    def test_frozen_campaign_definition_cannot_change_between_jobs(self):
        self.prepare()
        path = self.campaign / 'campaign.json'
        plan = bundles.load_json(path)
        plan['config']['generation_seconds'] = 500
        worker.write_json(path, plan)
        with self.assertRaisesRegex(ValueError, 'campaign changed'):
            pipeline.execute(self.campaign)

    def test_invalid_review_is_retained_and_does_not_block_other_reader(self):
        self.prepare()
        original = worker.run_job

        def launch(*args, **kwargs):
            if kwargs.get('review') and args[1]['id'] == 'astra':
                kwargs['fake_result'] = '{"summary":"invalid", "scores":{}}'
            return original(*args, **kwargs)

        with mock.patch.object(worker, 'run_job', side_effect=launch):
            state = pipeline.execute(self.campaign)
        self.assertEqual([item['status'] for item in state['jobs']], ['completed', 'rejected', 'admitted'])
        self.assertFalse(state['all_succeeded'])
        self.assertTrue((self.campaign / 'jobs/review-0001-01/control/rejection.txt').is_file())
        self.assertTrue((self.campaign / 'jobs/review-0001-01/control/final-message.txt').is_file())

    def test_existing_runs_can_be_reassessed_without_regeneration(self):
        self.config.update(generators=[], existing_runs=['sol-original'])
        self.config_file.write_text(json.dumps(self.config))
        self.prepare()
        state = pipeline.execute(self.campaign)
        self.assertTrue(state['all_succeeded'])
        self.assertEqual(len(state['jobs']), 2)
        self.assertFalse(list((self.campaign / 'jobs').glob('generation-*')))
        self.assertEqual(len(atlas.validate(self.campaign / 'report')['runs']), len(atlas.validate(ROOT)['runs']))

    def test_provider_selection_and_invalid_reasoning_are_rejected(self):
        for field, value in [('provider', 'openai'), ('reasoning', 'implicit-remap')]:
            config = copy.deepcopy(self.config)
            config['generators'][0][field] = value
            with self.assertRaises(ValueError):
                pipeline.validate_config(config)


class ReviewTests(unittest.TestCase):
    def test_packet_withholds_identity_prior_scores_and_runtime_observations(self):
        prompt, identity = reviews.prepare_packet(ROOT, 'sol-original')
        packet = json.loads(prompt)
        self.assertEqual(
            set(packet), {'policy', 'rubric', 'seed_lines', 'sources', 'line_numbering', 'response_schema'}
        )
        self.assertTrue(
            all(set(source) == {'source_id', 'format', 'lines', 'omission'} for source in packet['sources'])
        )
        self.assertEqual(identity['run_id'], 'sol-original')
        self.assertNotIn('run_id', packet)
        self.assertNotIn('scores', packet)
        self.assertNotIn('observations', packet)

    def test_valid_scored_reading_and_fabricated_quote(self):
        prompt, identity = reviews.prepare_packet(ROOT, 'sol-original')
        packet = json.loads(prompt)
        source = next(source for source in packet['sources'] if source['lines'])
        answer = json.loads(reviews.synthetic_result(packet['rubric']))
        axis = packet['rubric']['axes'][0]['id']
        answer['scores'][axis].update(
            value=1,
            confidence='low',
            rationale='Synthetic validation fixture, not an actual assessment.',
            citations=[{'source_id': source['source_id'], 'start': 1, 'end': 1, 'quote': source['lines'][0]}],
        )
        receipt = {
            'status': 'completed',
            'ended_at': '2026-10-20T12:00:00Z',
            'requested': {'model': 'fixture'},
            'reported': {'model': None, 'provider': None},
            'runtime': {},
            'usage': {},
            'isolation': {},
            'runtime_checks': {'status': 'passed'},
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'answer.json'
            path.write_text(json.dumps(answer))
            review = reviews.validate_answer(ROOT, path, identity, 'test-evidence-review', 'Synthetic fixture', receipt)
            self.assertEqual(review['scores'][axis]['value'], 1)
            self.assertEqual(
                review['scores'][axis]['citations'][0]['path'], identity['source_map'][source['source_id']]
            )
            for changed in [
                dict(receipt, status='failed'),
                dict(receipt, runtime_checks={'status': 'failed'}),
                dict(receipt, runtime_checks={}),
            ]:
                with self.assertRaises(ValueError):
                    reviews.validate_answer(ROOT, path, identity, 'test-bad-runtime', 'Synthetic fixture', changed)
            answer['scores'][axis]['citations'][0]['quote'] = 'Invented quote'
            path.write_text(json.dumps(answer))
            with self.assertRaisesRegex(ValueError, 'quote differs'):
                reviews.validate_answer(ROOT, path, identity, 'test-bad-review', 'Synthetic fixture', receipt)


class WorkerTests(unittest.TestCase):
    def read_router_health(self, value):
        with mock.patch.object(worker, 'build_opener') as opener:
            opener.return_value.open.return_value = io.BytesIO(json.dumps(value).encode())
            return worker.router_health('http://127.0.0.1:18766/v1')

    def test_router_capabilities_pin_only_the_required_policy_and_revision(self):
        health = {
            'clientAuth': True,
            'openrouter': True,
            'revision': 'a' * 64,
            'capabilities': {'routingPolicies': ['future-policy', worker.POLICY], 'revisionPrecondition': True},
        }
        self.assertEqual(self.read_router_health(health), {'policy': worker.POLICY, 'revision': 'a' * 64})

    def test_router_without_openrouter_can_still_serve_native_models(self):
        health = {
            'clientAuth': True,
            'openrouter': False,
            'revision': 'a' * 64,
            'capabilities': {'routingPolicies': [worker.POLICY], 'revisionPrecondition': True},
        }
        with mock.patch.object(worker, 'build_opener') as opener:
            opener.return_value.open.return_value = io.BytesIO(json.dumps(health).encode())
            self.assertEqual(
                worker.router_health('http://127.0.0.1:18766/v1', require_openrouter=False),
                {'policy': worker.POLICY, 'revision': 'a' * 64},
            )

    def test_router_missing_capabilities_invalid_revision_or_unconfigured_access_are_rejected(self):
        health = {
            'clientAuth': True,
            'openrouter': True,
            'revision': 'a' * 64,
            'capabilities': {'routingPolicies': [worker.POLICY], 'revisionPrecondition': True},
        }
        for change in [
            {'capabilities': {}},
            {'capabilities': None},
            {'capabilities': {'routingPolicies': [worker.POLICY]}},
            {'capabilities': {'routingPolicies': worker.POLICY}},
            {'capabilities': {'routingPolicies': ['unknown']}},
            {'revision': 'x' * 64},
            {'revision': None},
            {'clientAuth': False},
            {'openrouter': False},
        ]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.read_router_health(dict(health, **change))

    def test_router_is_required_even_for_a_native_model_catalog_entry(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch.object(worker.shutil, 'which', return_value=str(Path(temporary) / 'codex')),
            mock.patch.object(worker.subprocess, 'run', return_value=mock.Mock(stdout='codex-cli fixture\n')),
            mock.patch.object(worker, 'router_health', side_effect=ValueError('router unavailable')),
        ):
            output = Path(temporary) / 'campaign'
            output.mkdir()
            (Path(temporary) / 'codex').write_text('fixture executable', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'router unavailable'):
                worker.freeze_runtime(
                    output, [{'model': 'gpt-6-astra', 'reasoning': 'max'}], False, 'http://127.0.0.1:18766/v1'
                )

    def test_unknown_models_fail_closed_before_catalog_or_router_inference(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'campaign'
            output.mkdir()
            with self.assertRaisesRegex(ValueError, 'explicit capability descriptors'):
                worker.freeze_runtime(
                    output, [{'model': 'new/provider-model', 'reasoning': 'high'}], True, 'http://127.0.0.1:18766/v1'
                )
            self.assertFalse((output / 'catalog.json').exists())

    def test_capability_descriptors_not_slug_prefixes_control_transport(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / 'campaign'
            output.mkdir()
            registry = root / 'capabilities.json'
            registry.write_text(
                json.dumps(
                    {
                        'schema_version': 1,
                        'models': {
                            'deepseek/fixture': {
                                'transport': 'native',
                                'requires_openai_auth': False,
                                'structured_output': 'native',
                            }
                        },
                    }
                ),
                encoding='utf-8',
            )
            runtime = worker.freeze_runtime(
                output,
                [{'model': 'deepseek/fixture', 'reasoning': 'high'}],
                True,
                'http://127.0.0.1:18766/v1',
                capability_path=registry,
            )
            self.assertEqual(runtime['model_capabilities']['deepseek/fixture']['transport'], 'native')
            self.assertEqual(runtime['capability_registry_sha256'], bundles.sha(registry.read_bytes()))

    def test_review_disables_execution_and_uses_plain_final_message(self):
        args = worker.arguments(
            {'model': 'deepseek/example', 'reasoning': 'max'},
            Path('/world'),
            Path('/scratch'),
            Path('/control'),
            Path('/catalog.json'),
            'http://127.0.0.1:18766/v1',
            review=True,
            router_revision='a' * 64,
        )
        self.assertNotIn('--ignore-user-config', args)
        self.assertNotIn('--profile', args)
        self.assertIn('shell_tool', args)
        self.assertIn('--output-last-message', args)
        self.assertNotIn('--output-schema', args)
        self.assertIn('model_provider="unified"', args)
        self.assertIn('model_providers.unified.base_url="http://127.0.0.1:18766/v1"', args)
        self.assertTrue(any('preserve-model-v1' in item for item in args))
        self.assertTrue(any('X-Codex-Router-Revision="' + 'a' * 64 + '"' in item for item in args))
        self.assertTrue(any('"."="read"' in item for item in args))

    def test_transport_configuration_discards_ambient_settings_and_rejects_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = root / 'profile.toml'
            profile.write_text(
                'developer_instructions = "private ambient instructions"\n'
                'http_headers = { "X-Codex-Router-Token" = "' + 'a' * 64 + '" }\n'
            )
            transport = worker.transport_config(profile, requires_openai_auth=False)
            self.assertNotIn(b'ambient', transport)
            self.assertNotIn(b'developer_instructions', transport)
            path = root / 'transport.config.toml'
            path.write_bytes(transport)
            runtime = {'transport_config_sha256': bundles.sha(transport)}
            control = root / 'control'
            control.mkdir()
            job = {'model': 'deepseek/fixture', 'reasoning': 'max'}
            environment = worker.job_environment(job, root, control, root / 'catalog.json', runtime, root / 'world')
            self.assertEqual(Path(environment['CODEX_HOME']), control / 'codex-home')
            self.assertEqual((control / 'codex-home/config.toml').read_bytes(), transport)
            path.write_bytes(transport + b'\n')
            with self.assertRaisesRegex(ValueError, 'transport configuration changed'):
                worker.job_environment(job, root, control, root / 'catalog.json', runtime, root / 'world')

    def test_zero_process_exit_does_not_hide_failed_turn(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'events.jsonl'
            path.write_text(
                '\n'.join(
                    json.dumps(item)
                    for item in [
                        {'type': 'thread.started', 'thread_id': 'fixture'},
                        {'type': 'turn.failed'},
                    ]
                )
            )
            with self.assertRaisesRegex(ValueError, 'successful turn'):
                worker.event_outcome(path)

    def test_failed_job_keeps_candidate_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'job'
            job = {'model': 'fixture', 'reasoning': 'max', 'seconds': 5}

            def fail(command, world, environment, prompt, events, stderr, timeout):
                (world / 'README.md').write_text('Partial work')
                return {'exit_code': 1, 'stopped_reason': None}

            with mock.patch.object(sealed_run, 'capture_process', side_effect=fail):
                result = worker.run_job(output, job, b'Unchanged seed', {}, Path('unused'), '', dry_run=True)
            self.assertEqual(result['status'], 'failed')
            self.assertIsNotNone(result['candidate_sha256'])
            self.assertEqual(bundles.load_json(output / 'candidate-snapshot.json')['file_count'], 1)

    def test_reviewer_tool_activity_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'events.jsonl'
            path.write_text(
                '\n'.join(
                    json.dumps(item)
                    for item in [
                        {'type': 'thread.started', 'thread_id': 'fixture'},
                        {'type': 'item.completed', 'item': {'type': 'command_execution'}},
                        {'type': 'turn.completed', 'usage': {}},
                    ]
                )
            )
            with self.assertRaisesRegex(ValueError, 'source-only'):
                worker.event_outcome(path, forbid_tools=True)

    def test_large_stdin_cannot_prevent_wall_timeout(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = sealed_run.capture_process(
                [sys.executable, '-c', 'import time; time.sleep(20)'],
                root,
                {},
                b'x' * (1024 * 1024),
                root / 'events',
                root / 'stderr',
                0.1,
            )
            self.assertEqual(result['stopped_reason'], 'wall_time_limit')
            self.assertLess(result['elapsed_seconds'], 5)


if __name__ == '__main__':
    unittest.main()
