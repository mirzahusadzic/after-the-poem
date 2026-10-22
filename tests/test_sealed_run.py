import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('sealed_run', ROOT / 'scripts/sealed_run.py')
sealed_run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sealed_run)


class WorkspaceResultTests(unittest.TestCase):
    def test_dry_run_captures_post_validated_result_without_output_schema(self):
        with tempfile.TemporaryDirectory(prefix='after-poem-sealed-') as temporary:
            output = Path(temporary) / 'run'
            with mock.patch.dict(os.environ, {'SEALED_FAKE_RESULT': 'valid'}, clear=False):
                receipt = sealed_run.run_once(
                    output,
                    'deepseek/deepseek-v4.1-flash-1017',
                    'max',
                    30,
                    dry_run=True,
                )
            self.assertEqual(receipt['status'], 'completed')
            self.assertEqual(receipt['mode'], 'workspace-result')
            self.assertTrue(receipt['dry_run'])
            self.assertFalse(receipt['output_schema_argument'])
            self.assertEqual(receipt['seed_prompt_sha256'], sealed_run.PROMPT_HASH)
            self.assertNotEqual(receipt['delivered_prompt_sha256'], sealed_run.PROMPT_HASH)
            self.assertEqual(receipt['result']['status'], 'candidate')
            self.assertGreater(receipt['candidate']['file_count'], 0)
            self.assertTrue((output / 'result.json').is_file())
            self.assertTrue((output / 'received-result.json').exists() is False)
            prepared = json.loads((output / 'prepared.json').read_text())
            self.assertNotIn('--output-schema', prepared['command'])
            delivered = (output / 'prompt.txt').read_text()
            seed = (output / 'seed-prompt.txt').read_text()
            self.assertTrue(delivered.startswith(seed))
            self.assertIn('$TMPDIR/DREAM_RESULT.json', delivered)
            events, thread_id = sealed_run.parse_events(output / 'events.jsonl')
            self.assertTrue(thread_id.startswith('dry-run-'))
            self.assertEqual(sum(1 for event in events if event.get('type') == 'thread.started'), 1)

    def test_invalid_workspace_result_marks_the_run_incomplete(self):
        with tempfile.TemporaryDirectory(prefix='after-poem-sealed-') as temporary:
            output = Path(temporary) / 'run'
            with mock.patch.dict(os.environ, {'SEALED_FAKE_RESULT': 'invalid'}, clear=False):
                with self.assertRaisesRegex(sealed_run.SealedRunError, 'status is invalid'):
                    sealed_run.run_once(output, 'deepseek/deepseek-v4.1-flash-1017', 'max', 30, dry_run=True)
            receipt = json.loads((output / 'receipt.json').read_text())
            self.assertEqual(receipt['status'], 'incomplete')
            self.assertIn('status is invalid', receipt['error'])
            self.assertGreater(receipt['candidate']['file_count'], 0)
            self.assertTrue((output / 'candidate-snapshot.json').is_file())
            self.assertFalse((output / 'result.json').exists())

    def test_missing_workspace_result_marks_the_run_incomplete(self):
        with tempfile.TemporaryDirectory(prefix='after-poem-sealed-') as temporary:
            output = Path(temporary) / 'run'
            with mock.patch.dict(os.environ, {'SEALED_FAKE_RESULT': 'missing'}, clear=False):
                with self.assertRaisesRegex(sealed_run.SealedRunError, 'result file is missing'):
                    sealed_run.run_once(output, 'deepseek/deepseek-v4.1-flash-1017', 'max', 30, dry_run=True)
            receipt = json.loads((output / 'receipt.json').read_text())
            self.assertEqual(receipt['status'], 'incomplete')
            self.assertGreater(receipt['candidate']['file_count'], 0)

    def test_legacy_live_entry_refuses_to_bypass_codex_router_cli(self):
        with tempfile.TemporaryDirectory(prefix='after-poem-sealed-') as temporary:
            output = Path(temporary) / 'run'
            with self.assertRaisesRegex(sealed_run.SealedRunError, 'scripts/evaluate.py'):
                sealed_run.run_once(output, 'gpt-6-astra', 'max', 30, dry_run=False)
            self.assertFalse(output.exists())

    def test_result_contract_rejects_extra_fields_and_invalid_unknowns(self):
        valid = {'status': 'candidate', 'summary': 'A result.', 'unknowns': []}
        self.assertEqual(sealed_run.validate_result(valid), valid)
        with self.assertRaisesRegex(sealed_run.SealedRunError, 'fields'):
            sealed_run.validate_result(dict(valid, extra=True))
        with self.assertRaisesRegex(sealed_run.SealedRunError, 'unknown'):
            sealed_run.validate_result(dict(valid, unknowns=[3]))


if __name__ == '__main__':
    unittest.main()
