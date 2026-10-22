import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import bundles
import retain_pilot


class PilotRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='poem-retain-pilot-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source, self.output = self.root / 'pilot', self.root / 'bundle'
        self.source.mkdir()
        (self.source / 'world').mkdir()
        (self.source / 'world/README.md').write_bytes(b'# Fixture\r\nPreserve exact bytes.\r\n')
        (self.source / 'world/never-run.py').write_text('raise RuntimeError("Never execute candidates")\n')
        for name in ['events.jsonl', 'stderr.log', 'private-message.txt']:
            (self.source / name).write_text('PRIVATE_CONVERSATION_SENTINEL')
        seed, suffix = b'The original seed.\n', b'\nCompletion handoff fixture.\n'
        for name, data in [('seed-prompt.txt', seed), ('delivery-suffix.txt', suffix), ('prompt.txt', seed + suffix)]:
            (self.source / name).write_bytes(data)
        shared = {
            'mode': 'workspace-result',
            'dry_run': False,
            'model': 'deepseek/fixture',
            'reasoning_effort': 'max',
            'output_schema_argument': False,
            'seed_prompt_sha256': bundles.sha(seed),
            'delivery_suffix_sha256': bundles.sha(suffix),
            'delivered_prompt_sha256': bundles.sha(seed + suffix),
        }
        self.prepared = dict(
            shared,
            schema_version=1,
            command=['codex', 'exec', '--cd', '/Users/private/world', '--config', 'PRIVATE_CONFIG_SENTINEL'],
        )
        self.receipt = dict(
            shared,
            status='incomplete',
            error='Workspace result file is missing',
            exit_code=0,
            stopped_reason=None,
            started_at='2026-10-20T00:00:00Z',
            finished_at='2026-10-20T00:01:00Z',
        )
        self.save_records()

    def save_records(self):
        for name, value in [('prepared.json', self.prepared), ('receipt.json', self.receipt)]:
            (self.source / name).write_text(json.dumps(value))

    def retain(self, output=None):
        return retain_pilot.retain(
            self.source,
            output or self.output,
            run_id='pilot-fixture',
            cohort_id='pilot-cohort',
            cohort_title='Legacy pilot',
            label='Fixture',
            title='Fixture world',
            provider='openrouter',
        )

    def test_retention_preserves_bytes_without_running_or_copying_private_material(self):
        before = {path.relative_to(self.source): path.read_bytes() for path in self.source.rglob('*') if path.is_file()}
        with mock.patch('subprocess.Popen', side_effect=AssertionError('No subprocesses during retention')):
            result = self.retain()
        for path, content in before.items():
            self.assertEqual((self.source / path).read_bytes(), content)
        self.assertEqual((self.output / 'candidate/README.md').read_bytes(), before[Path('world/README.md')])
        self.assertEqual((self.output / 'prompt.txt').read_bytes(), before[Path('prompt.txt')])
        run = result['run']
        self.assertEqual(run['outcome']['status'], 'partial')
        self.assertEqual(run['outcome']['legacy_status'], 'incomplete')
        self.assertEqual(run['condition']['reported'], {'model': None, 'provider': None})
        self.assertIsNone(run['retention']['original_snapshot_sha256'])
        self.assertEqual(run['condition']['harness']['version'], 'unrecorded')
        public = b''.join(path.read_bytes() for path in self.output.rglob('*') if path.is_file())
        for secret in [b'PRIVATE_CONVERSATION_SENTINEL', b'PRIVATE_CONFIG_SENTINEL', b'/Users/private']:
            self.assertNotIn(secret, public)

    def test_input_hash_mismatch_refuses_retention(self):
        (self.source / 'seed-prompt.txt').write_bytes(b'Changed seed')
        with self.assertRaisesRegex(ValueError, 'prompt'):
            self.retain()
        self.assertFalse(self.output.exists())

    def test_original_snapshot_and_other_failures_are_not_reclassified(self):
        (self.source / 'candidate-snapshot.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'snapshot'):
            self.retain()
        (self.source / 'candidate-snapshot.json').unlink()
        self.receipt['error'] = 'A different failure'
        self.save_records()
        with self.assertRaisesRegex(ValueError, 'missing completion handoff'):
            self.retain()
        self.assertFalse(self.output.exists())

    def test_symlinks_and_existing_or_nested_outputs_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'outside'):
            self.retain(self.source / 'new-bundle')
        (self.source / 'world/alias').symlink_to(self.source / 'world/README.md')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.retain()
        (self.source / 'world/alias').unlink()
        self.retain()
        with self.assertRaisesRegex(ValueError, 'new output'):
            self.retain()


if __name__ == '__main__':
    unittest.main()
