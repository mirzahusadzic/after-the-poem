import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import extraction_pipeline as extraction
import session_flow


MODEL = 'deepseek/deepseek-v4.1-flash-1017'
RECEIPT = {
    'status': 'completed',
    'dry_run': False,
    'requested': {'model': MODEL, 'reasoning': 'high'},
    'runtime': {'codex_version': 'synthetic-no-inference', 'router': None},
    'runtime_checks': {'status': 'passed'},
}


class ExtractionPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='after-poem-extraction-')
        self.root = Path(self.temporary.name)
        self.prepared = extraction.prepare_sources(ROOT, 'sol-original')
        self.ident = extraction.identity(self.prepared, 'sol-original-test-extraction')

    def tearDown(self):
        self.temporary.cleanup()

    def validate(self, answer):
        path = self.root / 'answer.json'
        path.write_text(json.dumps(answer), encoding='utf-8')
        return extraction.validate_answer(
            path, self.ident, self.prepared['source_texts'], RECEIPT, receipt_sha256='0' * 64
        )

    def test_evidence_uses_neutral_numbered_sources_and_withholds_scores(self):
        evidence = session_flow.format_evidence(self.prepared['seed_lines'], self.prepared['sources'])
        self.assertTrue(evidence.startswith('EVIDENCE'))
        self.assertIn('=== SOURCE source-0001 · ', evidence)
        self.assertIn('\n1|', evidence)
        for source in self.prepared['sources']:
            self.assertNotIn(source['path'], evidence.split('=== SOURCE')[0])
        self.assertNotIn('"scores"', extraction.EXTRACTION_TASK)
        self.assertIn('do not copy quotation text', extraction.EXTRACTION_TASK)

    def test_dry_run_writes_and_revalidates_a_schema_two_record(self):
        output = self.root / 'extraction'
        result = extraction.run(
            ROOT,
            'sol-original',
            output,
            model=MODEL,
            reasoning='high',
            extraction_id='sol-original-synthetic-extraction',
            dry_run=True,
        )
        self.assertEqual((result['status'], result['attempts']), ('completed', 1))
        record = json.loads((output / 'extraction.json').read_text(encoding='utf-8'))
        self.assertEqual(record['schema_version'], 2)
        self.assertEqual(record['extractor']['gateway'], 'codex-unified-model-router')
        self.assertNotIn('quote', record['items'][0]['citations'][0])
        validated = extraction.validate_output(ROOT, output / 'extraction.json')
        self.assertTrue(validated['synthetic'])
        self.assertEqual(validated['extraction_sha256'], result['extraction_sha256'])

    def test_recover_rebuilds_the_record_without_inference(self):
        output = self.root / 'extraction'
        extraction.run(ROOT, 'sol-original', output, model=MODEL, extraction_id='sol-original-recover', dry_run=True)
        expected = (output / 'extraction.json').read_bytes()
        (output / 'extraction.json').unlink()
        with mock.patch.object(extraction.worker, 'run_turn', side_effect=AssertionError('no inference')):
            result = extraction.recover(ROOT, output)
        self.assertTrue(result['recovered'])
        self.assertEqual((output / 'extraction.json').read_bytes(), expected)

    def test_live_extraction_requires_acceptance_and_budget_before_output(self):
        output = self.root / 'extraction'
        with self.assertRaisesRegex(ValueError, 'accept-run'):
            extraction.run(ROOT, 'sol-original', output, model=MODEL)
        with self.assertRaisesRegex(ValueError, 'budget'):
            extraction.run(ROOT, 'sol-original', output, model=MODEL, accept_run=True)
        self.assertFalse(output.exists())

    def test_host_checks_ranges_and_rejects_invalid_ones(self):
        answer = json.loads(extraction.synthetic_answer(self.prepared))
        citation = self.validate(answer)['items'][0]['citations'][0]
        self.assertEqual(set(citation), {'source_path', 'start', 'end'})
        answer['items'][0]['citations'][0]['end'] = 100000
        with self.assertRaisesRegex(ValueError, 'line range'):
            self.validate(answer)

    def test_trailing_newline_off_by_one_is_normalized_and_recorded(self):
        answer = json.loads(extraction.synthetic_answer(self.prepared))
        citation = answer['items'][0]['citations'][0]
        path = self.ident['source_map'][citation['source_id']]
        line_count = len(self.prepared['source_texts'][path].splitlines())
        citation.update(start=1, end=line_count + 1)
        record = self.validate(answer)
        self.assertTrue(any('trailing-newline' in unknown for unknown in record['unknowns']))
        self.assertEqual(record['items'][0]['citations'][0]['end'], line_count)

    def test_blank_line_citation_is_ignored_when_other_evidence_remains(self):
        answer = json.loads(extraction.synthetic_answer(self.prepared))
        first = answer['items'][0]['citations'][0]
        path = self.ident['source_map'][first['source_id']]
        blank = next(
            index for index, line in enumerate(self.prepared['source_texts'][path].splitlines(), 1) if not line.strip()
        )
        answer['items'][0]['citations'].append(dict(first, start=blank, end=blank))
        record = self.validate(answer)
        self.assertEqual(len(record['items'][0]['citations']), 1)
        self.assertTrue(any('ignored blank-line citation' in unknown for unknown in record['unknowns']))

    def test_duplicate_keys_are_invalid_answers(self):
        path = self.root / 'answer.json'
        path.write_text('{"items": [], "items": [], "unknowns": []}', encoding='utf-8')
        with self.assertRaisesRegex(extraction.InvalidExtractionAnswer, 'Duplicate JSON key'):
            extraction.validate_answer(
                path, self.ident, self.prepared['source_texts'], RECEIPT, receipt_sha256='0' * 64
            )


if __name__ == '__main__':
    unittest.main()
