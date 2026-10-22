import hashlib
import importlib.util
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

sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('bundles', ROOT / 'scripts/bundles.py')
bundles = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundles)

atlas_spec = importlib.util.spec_from_file_location('atlas', ROOT / 'scripts/atlas.py')
atlas = importlib.util.module_from_spec(atlas_spec)
atlas_spec.loader.exec_module(atlas)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def make_bundle(root, run_id='fixture-run'):
    root.mkdir()
    prompt = b'A small poem.\nA continuation begins here.\n'
    (root / 'prompt.txt').write_bytes(prompt)
    candidate = root / 'candidate'
    candidate.mkdir()
    payload = b'# A world\n\nAn inert candidate document.\n'
    (candidate / 'README.md').write_bytes(payload)
    write_json(
        root / 'manifest.json',
        {'schema_version': 1, 'files': [{'path': 'README.md', 'bytes': len(payload), 'sha256': digest(payload)}]},
    )
    write_json(
        root / 'observations.json',
        {
            'schema_version': 1,
            'attribution': 'Fixture observer',
            'summary': 'A retained observation made without executing the candidate.',
            'checks': [{'name': 'manifest', 'outcome': 'checked'}],
        },
    )
    run = {
        'schema_version': 1,
        'run_id': run_id,
        'seed_id': 'fixture-seed',
        'prompt_sha256': digest(prompt),
        'cohort': {'id': 'fixture-cohort', 'title': 'Fixture poems', 'baseline': True},
        'presentation': {'label': 'Fixture model', 'title': 'Fixture world'},
        'outcome': {
            'status': 'completed',
            'started_at': '2026-10-20T10:00:00Z',
            'ended_at': '2026-10-20T10:00:05Z',
            'stop_reason': 'completed',
            'errors': [],
            'partial_output': False,
            'truncation': {'truncated': False, 'reason': None},
        },
        'condition': {
            'requested': {
                'model': 'fixture',
                'provider': 'local-test',
                'router_revision': None,
                'routing_policy': None,
            },
            'reported': {'model': 'fixture', 'provider': 'local-test'},
            'harness': {
                'name': 'fixture-harness',
                'version': '1.0',
                'reasoning_effort': 'medium',
                'tools': [],
                'runtime': {'os': 'test'},
            },
            'isolation': {'mode': 'offline', 'network': 'denied'},
            'budgets': {'wall_time_seconds': 30, 'input_tokens': None, 'output_tokens': None, 'cost': None},
        },
        'usage': {},
        'cost': {'amount': None, 'currency': None, 'provenance': 'not billed'},
    }
    write_json(root / 'run.json', run)
    return root


class BundleValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='after-poem-bundle-')
        self.bundle = make_bundle(Path(self.temp.name) / 'bundle')

    def tearDown(self):
        self.temp.cleanup()

    def test_bundle_identity_and_manifest_validate(self):
        data = bundles.validate_bundle(self.bundle)
        self.assertEqual(len(data['files']), 1)
        self.assertEqual(len(data['bundle_sha256']), 64)

    def test_producer_schema_is_tracked_and_json_valid(self):
        schema = json.loads((ROOT / 'docs/schemas/run-bundle-v1.schema.json').read_text())
        self.assertEqual(schema['$schema'], 'https://json-schema.org/draft/2020-12/schema')
        self.assertEqual(
            set(schema['$defs']), {'run', 'modelRequest', 'modelReport', 'harness', 'manifest', 'observations'}
        )

    def test_candidate_tampering_is_rejected(self):
        (self.bundle / 'candidate/README.md').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'hash differs|byte count differs'):
            bundles.validate_bundle(self.bundle)

    def test_host_paths_and_credentials_are_rejected(self):
        path = self.bundle / 'run.json'
        run = json.loads(path.read_text())
        run['condition']['requested']['api_key'] = 'never'
        write_json(path, run)
        with self.assertRaisesRegex(ValueError, 'Credential-like'):
            bundles.validate_bundle(self.bundle)
        run['condition']['requested'].pop('api_key')
        run['presentation']['title'] = '/Users/example/private/world'
        write_json(path, run)
        with self.assertRaisesRegex(ValueError, 'Host-specific'):
            bundles.validate_bundle(self.bundle)

    def test_empty_candidate_is_valid_for_a_failed_or_partial_run(self):
        (self.bundle / 'candidate/README.md').unlink()
        write_json(self.bundle / 'manifest.json', {'schema_version': 1, 'files': []})
        path = self.bundle / 'run.json'
        run = json.loads(path.read_text())
        run['outcome'].update(
            status='timed_out',
            stop_reason='deadline',
            partial_output=True,
            truncation={'truncated': True, 'reason': 'deadline'},
        )
        write_json(path, run)
        self.assertEqual(bundles.validate_bundle(self.bundle)['files'], {})


class BundleImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='after-poem-import-')
        self.root = Path(self.temp.name)
        for name in ['corpus', 'data', 'src', 'assets']:
            shutil.copytree(ROOT / name, self.root / name, ignore=EDITORIAL)
        for name in ['LICENSE', 'LICENSE-CONTENT', 'LICENSING.md', 'THIRD_PARTY_NOTICES.md']:
            shutil.copyfile(ROOT / name, self.root / name)
        self.bundle = make_bundle(self.root / 'incoming', run_id='fixture-run')

    def tearDown(self):
        self.temp.cleanup()

    def test_import_adds_unreviewed_evidence_and_builds_without_execution(self):
        result = bundles.import_bundle(self.bundle, self.root)
        data = atlas.validate(self.root)
        review = data['active_reviews']['fixture-run']
        self.assertEqual(review['status'], 'unreviewed')
        self.assertTrue(all(score['value'] is None for score in review['scores'].values()))
        self.assertEqual(data['cohorts'][-1]['baseline'], 'fixture-run')
        self.assertEqual(data['cohorts'][-1]['default_run'], 'fixture-run')
        self.assertTrue((self.root / 'data/bundles/fixture-run/run.json').is_file())
        self.assertTrue((self.root / 'data/bundles/fixture-run/observations.json').is_file())
        atlas.build(self.root)
        report = json.loads((self.root / 'dist/report.json').read_text())
        self.assertIn('fixture-run', report['active_reviews'])
        self.assertEqual(report['artefacts']['fixture-run']['kind'], 'none')
        self.assertEqual(result['run_id'], 'fixture-run')

    def test_import_refuses_an_existing_run_id(self):
        bundles.import_bundle(self.bundle, self.root)
        with self.assertRaisesRegex(ValueError, 'already exists'):
            bundles.import_bundle(self.bundle, self.root)

    def test_import_cli_reports_the_admitted_corpus_hash(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / 'scripts/bundles.py'), 'import', str(self.bundle), '--project', str(self.root)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        corpus_hash = digest((self.root / 'corpus/runs/fixture-run.json').read_bytes())
        self.assertIn('Imported fixture-run as unreviewed evidence. Corpus SHA-256 ' + corpus_hash, result.stdout)


if __name__ == '__main__':
    unittest.main()
