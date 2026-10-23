import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from evaluation_fixtures import EDITORIAL


class EvaluationCliTests(unittest.TestCase):
    def run_cli(self, *arguments):
        result = subprocess.run(
            [sys.executable, str(ROOT / 'scripts/evaluate.py'), *arguments], cwd=ROOT, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_offline_cli_plans_runs_resumes_status_and_validates(self):
        with tempfile.TemporaryDirectory(prefix='after-poem-cli-') as temporary:
            root = Path(temporary)
            config = json.loads((ROOT / 'examples/campaign.json').read_text(encoding='utf-8'))
            config.update(
                id='cli-dry-run-v1',
                samples=1,
                generators=config['generators'][:1],
                reviewers=config['reviewers'][:1],
                display_reviewer='astra',
            )
            config_path = root / 'campaign.config.json'
            config_path.write_text(json.dumps(config), encoding='utf-8')
            campaign = root / 'campaign'

            planned = self.run_cli('plan', str(config_path), '--output', str(campaign), '--dry-run')
            self.assertEqual(planned['gateway'], 'codex-unified-model-router')
            self.assertTrue(planned['runtime']['dry_run'])
            self.assertIsNone(planned['runtime']['router'])

            first = self.run_cli('run', str(campaign))
            self.assertTrue(first['all_succeeded'])
            resumed = self.run_cli('resume', str(campaign))
            self.assertTrue(resumed['all_succeeded'])
            self.assertEqual([item['job'] for item in first['jobs']], [item['job'] for item in resumed['jobs']])

            status = self.run_cli('status', str(campaign))
            self.assertTrue(status['complete'])
            self.assertEqual(status['gateway'], 'codex-unified-model-router')
            validated = self.run_cli('validate', str(campaign))
            self.assertEqual(validated['bundles_checked'], 1)
            self.assertEqual(validated['reviews_checked'], 1)

    def test_doctor_offline_needs_no_credentials_router_or_inference(self):
        with tempfile.TemporaryDirectory(prefix='after-poem-doctor-') as temporary:
            root = Path(temporary)
            config = json.loads((ROOT / 'examples/campaign.json').read_text(encoding='utf-8'))
            config.update(
                id='doctor-dry-run-v1',
                samples=1,
                generators=config['generators'][:1],
                reviewers=config['reviewers'][:1],
                display_reviewer='astra',
            )
            config_path = root / 'campaign.config.json'
            config_path.write_text(json.dumps(config), encoding='utf-8')
            report = self.run_cli('doctor', str(config_path), '--dry-run')
            self.assertEqual(report['status'], 'valid')
            self.assertEqual(report['gateway'], 'codex-unified-model-router')
            self.assertTrue(report['dry_run'])
            self.assertEqual(report['model_capabilities']['gpt-6-astra']['transport'], 'native')
            self.assertEqual(
                report['model_capabilities']['deepseek/deepseek-v4.1-flash-1017']['transport'], 'openrouter'
            )

    def test_doctor_accepts_the_current_deepseek_only_live_preset(self):
        report = self.run_cli('doctor', str(ROOT / 'examples/campaign-deepseek-live.json'), '--dry-run')
        self.assertEqual(report['status'], 'valid')
        self.assertEqual(report['models'], ['deepseek/deepseek-v4.1-flash-1017'])
        self.assertEqual(report['model_capabilities']['deepseek/deepseek-v4.1-flash-1017']['transport'], 'openrouter')

    def test_contract_validation_is_available_from_the_public_cli(self):
        result = self.run_cli('validate-contract', str(ROOT / 'tests/fixtures/evaluation/attribution'))
        self.assertEqual(result['judgment_samples'], 3)
        self.assertEqual(result['aggregations'], 1)
        self.assertEqual(result['outcomes'], 1)

    def test_full_rubric_policy_generation_is_available_from_the_public_cli(self):
        with tempfile.TemporaryDirectory(prefix='after-poem-cli-policy-') as temporary:
            output = Path(temporary) / 'policy.json'
            result = self.run_cli('generate-policy', '--output', str(output), '--repetitions', '2')
            self.assertEqual(result['judgments'], 8)
            self.assertEqual(result['nodes'], 73)
            self.assertTrue(output.is_file())

    def test_extraction_dry_run_and_validation_are_available_from_the_public_cli(self):
        with tempfile.TemporaryDirectory(prefix='after-poem-cli-extraction-') as temporary:
            output = Path(temporary) / 'extraction'
            extracted = self.run_cli(
                'extract',
                'sol-original',
                '--output',
                str(output),
                '--dry-run',
                '--model',
                'deepseek/deepseek-v4.1-flash-1017',
                '--extraction-id',
                'sol-original-cli-extraction',
            )
            self.assertEqual(extracted['status'], 'completed')
            self.assertEqual(extracted['gateway'], 'codex-unified-model-router')
            validated = self.run_cli('validate-extraction', str(output / 'extraction.json'))
            self.assertEqual(validated['extraction_id'], 'sol-original-cli-extraction')

    def test_scoring_dry_run_and_validation_are_available_from_the_public_cli(self):
        with tempfile.TemporaryDirectory(prefix='after-poem-cli-scoring-') as temporary:
            root = Path(temporary)
            extraction_dir = root / 'extraction'
            self.run_cli(
                'extract',
                'sol-original',
                '--output',
                str(extraction_dir),
                '--dry-run',
                '--model',
                'deepseek/deepseek-v4.1-flash-1017',
                '--extraction-id',
                'sol-original-cli-score-extraction',
            )
            evaluation_dir = root / 'evaluation'
            result = self.run_cli(
                'score',
                '--extraction',
                str(extraction_dir / 'extraction.json'),
                '--policy',
                str(ROOT / 'tests/fixtures/evaluation/attribution/scoring-policy.json'),
                '--output',
                str(evaluation_dir),
                '--evaluation-id',
                'sol-original-cli-evaluation',
                '--model',
                'deepseek/deepseek-v4.1-flash-1017',
                '--synthetic-outcomes',
                'contradicted,mixed,supported',
                '--dry-run',
            )
            self.assertEqual(result['outcomes'], {'preserve-attribution': 'split'})
            self.assertEqual((result['sessions'], result['unresolved_axes']), (3, 1))
            validated = self.run_cli('validate-evaluation', str(evaluation_dir))
            self.assertEqual(validated['judgment_samples'], 3)
            (evaluation_dir / 'evaluation.json').unlink()
            (evaluation_dir / 'status.json').unlink()
            recovered = self.run_cli(
                'recover-score', str(evaluation_dir), '--evaluation-id', 'sol-original-cli-recovered-evaluation'
            )
            self.assertTrue(recovered['recovered'])
            self.assertEqual(recovered['judgment_samples'], 3)
            self.run_cli('validate-evaluation', str(evaluation_dir))
            page_dir = root / 'page'
            prepared = self.run_cli('prepare-page', '--evaluation', str(evaluation_dir), '--output', str(page_dir))
            self.assertEqual(prepared['status'], 'unresolved')
            self.assertIsNone(prepared['score'])
            validated_page = self.run_cli('validate-presentation', str(page_dir))
            self.assertEqual(validated_page['samples'], 3)

    def test_integrate_evaluation_cli_requires_review_identity(self):
        with tempfile.TemporaryDirectory(prefix='after-poem-cli-integration-') as temporary:
            root = Path(temporary)
            for name in ['corpus', 'data', 'src', 'assets']:
                shutil.copytree(ROOT / name, root / name, ignore=EDITORIAL)
            shutil.rmtree(root / 'data/evaluations', ignore_errors=True)
            for name in ['LICENSE', 'LICENSE-CONTENT', 'LICENSING.md', 'THIRD_PARTY_NOTICES.md']:
                shutil.copyfile(ROOT / name, root / name)
            extraction_dir = root / '.local/extraction'
            self.run_cli(
                'extract',
                'sol-original',
                '--project',
                str(root),
                '--output',
                str(extraction_dir),
                '--model',
                'deepseek/deepseek-v4.1-flash-1017',
                '--extraction-id',
                'sol-original-cli-integration-extraction',
                '--dry-run',
            )
            evaluation_dir = root / '.local/evaluation'
            self.run_cli(
                'score',
                '--project',
                str(root),
                '--extraction',
                str(extraction_dir / 'extraction.json'),
                '--policy',
                str(ROOT / 'examples/scoring-policy-material-resistance.json'),
                '--output',
                str(evaluation_dir),
                '--evaluation-id',
                'sol-original-cli-integration-evaluation',
                '--model',
                'deepseek/deepseek-v4.1-flash-1017',
                '--synthetic-outcomes',
                'supported,supported,supported',
                '--dry-run',
            )
            rejected = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / 'scripts/evaluate.py'),
                    'integrate-evaluation',
                    '--project',
                    str(root),
                    '--evaluation',
                    str(evaluation_dir),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn('--review-id', rejected.stderr)

    def test_run_evaluation_cli_dry_run_reuses_completed_stages(self):
        with tempfile.TemporaryDirectory(prefix='after-poem-cli-end-to-end-') as temporary:
            root = Path(temporary)
            for name in ['corpus', 'data', 'src', 'assets']:
                shutil.copytree(ROOT / name, root / name, ignore=EDITORIAL)
            shutil.rmtree(root / 'data/evaluations', ignore_errors=True)
            for name in ['LICENSE', 'LICENSE-CONTENT', 'LICENSING.md', 'THIRD_PARTY_NOTICES.md']:
                shutil.copyfile(ROOT / name, root / name)
            output = root / '.local/evaluation-run'
            command = [
                'run-evaluation',
                'sol-original',
                '--project',
                str(root),
                '--policy',
                str(ROOT / 'examples/scoring-policy-material-resistance.json'),
                '--output',
                str(output),
                '--model',
                'deepseek/deepseek-v4.1-flash-1017',
                '--reasoning',
                'max',
                '--evaluation-id',
                'sol-original-cli-end-to-end-evaluation',
                '--review-id',
                'sol-original-cli-end-to-end-review',
                '--dry-run',
            ]
            first = self.run_cli(*command)
            self.assertEqual(first['integration_status'], 'skipped-synthetic')
            self.assertFalse(first['evaluation_reused'])
            second = self.run_cli(*command)
            self.assertTrue(second['evaluation_reused'])
            self.assertFalse((root / 'data/evaluations').exists())

    def test_profile_batch_runs_in_parallel_and_never_touches_live_outputs(self):
        with tempfile.TemporaryDirectory(prefix='after-poem-cli-batch-') as temporary:
            root = Path(temporary)
            for name in ['corpus', 'data', 'src', 'assets']:
                shutil.copytree(ROOT / name, root / name, ignore=EDITORIAL)
            shutil.rmtree(root / 'data/evaluations', ignore_errors=True)
            for name in ['LICENSE', 'LICENSE-CONTENT', 'LICENSING.md', 'THIRD_PARTY_NOTICES.md']:
                shutil.copyfile(ROOT / name, root / name)
            profiles = [
                str(ROOT / 'examples/rescore-deepseek/sol-original.json'),
                str(ROOT / 'examples/rescore-muse/sol-original.json'),
            ]
            result = self.run_cli('run-profiles', *profiles, '--project', str(root), '--parallel', '2', '--dry-run')
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(
                set(result['runs']), {'rescore-deepseek/sol-original.json', 'rescore-muse/sol-original.json'}
            )
            outputs = sorted(path.name for path in (root / '.local/evaluation-runs').iterdir())
            expected = sorted(
                Path(json.loads(Path(profile).read_text())['output']).name + '.dry-run' for profile in profiles
            )
            self.assertEqual(outputs, expected)
            self.assertFalse((root / 'data/evaluations').exists())

    def test_live_commands_refuse_to_start_without_a_budget(self):
        with tempfile.TemporaryDirectory(prefix='after-poem-cli-budget-') as temporary:
            output = Path(temporary) / 'extraction'
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / 'scripts/evaluate.py'),
                    'extract',
                    'sol-original',
                    '--output',
                    str(output),
                    '--model',
                    'deepseek/deepseek-v4.1-flash-1017',
                    '--accept-run',
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn('budget', result.stderr)
            self.assertFalse(output.exists())

    def test_partial_budget_override_fails_early_with_an_all_or_none_message(self):
        with tempfile.TemporaryDirectory(prefix='after-poem-cli-partial-budget-') as temporary:
            output = Path(temporary) / 'extraction'
            for extra in (
                ['--max-turns', '3'],
                ['--max-turns', '3', '--max-input-tokens', '1000'],
                ['--max-turns', '3', '--max-input-tokens', '1000', '--max-output-tokens', '80000', '--price-input', '1.0'],
            ):
                result = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / 'scripts/evaluate.py'),
                        'extract',
                        'sol-original',
                        '--output',
                        str(output),
                        '--model',
                        'deepseek/deepseek-v4.1-flash-1017',
                        '--extraction-id',
                        'sol-original-cli-partial-budget',
                        '--dry-run',
                        *extra,
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 1, extra)
                self.assertIn('Partial budget', result.stderr)
                self.assertIn('all-or-none', result.stderr)
                self.assertFalse(output.exists())
            complete = self.run_cli(
                'extract',
                'sol-original',
                '--output',
                str(output),
                '--model',
                'deepseek/deepseek-v4.1-flash-1017',
                '--extraction-id',
                'sol-original-cli-full-budget',
                '--dry-run',
                '--max-turns',
                '3',
                '--max-input-tokens',
                '300000',
                '--max-output-tokens',
                '80000',
            )
            self.assertEqual(complete['status'], 'completed')

    def test_run_profile_repairs_and_layout_overrides_reach_the_scoreflow(self):
        with tempfile.TemporaryDirectory(prefix='after-poem-cli-profile-overrides-') as temporary:
            root = Path(temporary)
            for name in ['corpus', 'data', 'src', 'assets']:
                shutil.copytree(ROOT / name, root / name, ignore=EDITORIAL)
            shutil.rmtree(root / 'data/evaluations', ignore_errors=True)
            for name in ['LICENSE', 'LICENSE-CONTENT', 'LICENSING.md', 'THIRD_PARTY_NOTICES.md']:
                shutil.copyfile(ROOT / name, root / name)
            profile = json.loads((ROOT / 'examples/rescore-deepseek/sol-original.json').read_text(encoding='utf-8'))
            profile.update(output='.local/evaluation-runs/cli-override-check')
            profile_path = root / 'profile.json'
            profile_path.write_text(json.dumps(profile), encoding='utf-8')
            result = self.run_cli(
                'run-profile',
                str(profile_path),
                '--project',
                str(root),
                '--dry-run',
                '--repairs',
                '0',
                '--layout',
                'per-axis',
                '--max-turns',
                '60',
                '--max-input-tokens',
                '20000000',
                '--max-output-tokens',
                '2000000',
            )
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['integration_status'], 'skipped-synthetic')
            stored = json.loads((Path(result['output']) / 'evaluation/plan.json').read_text(encoding='utf-8'))
            self.assertEqual(stored['layout'], 'per-axis')
            profile.update(output='.local/evaluation-runs/cli-override-rejected')
            profile_path.write_text(json.dumps(profile), encoding='utf-8')
            rejected = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / 'scripts/evaluate.py'),
                    'run-profile',
                    str(profile_path),
                    '--project',
                    str(root),
                    '--dry-run',
                    '--repairs',
                    '99',
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(rejected.returncode, 1)
            self.assertIn('repairs', rejected.stderr)

    def test_run_dream_profile_cli_dry_run_creates_a_new_campaign_run(self):
        with tempfile.TemporaryDirectory(prefix='after-poem-cli-dream-') as temporary:
            root = Path(temporary)
            for name in ['corpus', 'data', 'src', 'assets']:
                shutil.copytree(ROOT / name, root / name, ignore=EDITORIAL)
            shutil.rmtree(root / 'data/evaluations', ignore_errors=True)
            for name in ['LICENSE', 'LICENSE-CONTENT', 'LICENSING.md', 'THIRD_PARTY_NOTICES.md']:
                shutil.copyfile(ROOT / name, root / name)
            profile = json.loads((ROOT / 'examples/dream-profile-deepseek.json').read_text(encoding='utf-8'))
            profile.update(
                campaign_id='cli-dream-20261021',
                output='.local/cli-dream',
                evaluation_id='cli-dream-evaluation',
                review_id='cli-dream-review',
            )
            profile_path = root / 'dream-profile.json'
            profile_path.write_text(json.dumps(profile), encoding='utf-8')
            result = self.run_cli('run-dream-profile', str(profile_path), '--project', str(root), '--dry-run')
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['integration_status'], 'skipped-synthetic')
            self.assertEqual(result['run_id'], 'cli-dream-20261021-deepseek-s1')
            self.assertTrue((Path(result['campaign']) / 'report/dist/report.json').is_file())


if __name__ == '__main__':
    unittest.main()
