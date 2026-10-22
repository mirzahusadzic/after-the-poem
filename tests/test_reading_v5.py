import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import atlas
import evaluation_contract as contracts
import extraction_pipeline
import reading_controls
import scoring_pipeline
from test_scoring_pipeline import MODEL, rubric_policy_file


class ReadingV5Tests(unittest.TestCase):
    """The version 5 reading: the declared reading, the stance, the standing map and a lens per criterion."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='after-poem-reading-v5-')
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def judgments(self):
        policy = json.loads(rubric_policy_file(self.root).read_text())
        return [branch['judgment'] for branch in scoring_pipeline.score_branches(policy)[2]]

    def test_the_reader_is_given_the_declared_reading_verbatim(self):
        prompt = scoring_pipeline.compact_prompt(self.judgments(), inventory=True, version=5)
        for line in scoring_pipeline.DECLARED_READING:
            self.assertIn(line, prompt)
        self.assertIn('"standing"', prompt)
        self.assertIn('"stance"', prompt)
        self.assertIn('Lens: ... Reading: ... Other levels: ...', prompt)
        self.assertIn('it is not an answer key', prompt)
        # Earlier readings replay their own prompts unchanged.
        self.assertNotIn(
            'THE READING THIS STUDY MEASURES',
            scoring_pipeline.compact_prompt(self.judgments(), inventory=True, version=4),
        )

    def test_a_v5_session_records_its_reading_stance_and_standing(self):
        self.assertEqual(extraction_pipeline.PROMPT_VERSION, 5)
        scoring_pipeline.run(
            ROOT, rubric_policy_file(self.root), self.root / 'v5', run_id='sol-original', model=MODEL, dry_run=True
        )
        record = json.loads((self.root / 'v5/evaluation.json').read_text())
        reading = record['gateway']['reading_prompt']
        self.assertEqual(reading['version'], 5)
        prompt = (self.root / 'v5/sessions/01/turns/001/prompt.txt').read_text()
        self.assertIn(scoring_pipeline.DECLARED_READING[0], prompt)
        carriage = json.loads((self.root / 'v5/carriage.json').read_text())
        self.assertEqual(carriage['schema_version'], 2)
        self.assertTrue(
            any(element['kind'] == 'stance' for element in carriage['readings'][0]['seed_reading']['elements'])
        )
        self.assertEqual(carriage['readings'][0]['standing'][0]['kind'], 'other')
        self.assertTrue(all(judgment['rationale'].startswith('Lens:') for judgment in record['judgments']))
        scoring_pipeline.validate_output(ROOT, self.root / 'v5')

    def test_per_axis_sessions_keep_the_version_4_prompts(self):
        plan = scoring_pipeline.Plan(
            ROOT,
            json.loads(rubric_policy_file(self.root).read_text()),
            run_id='sol-original',
            dry_run=True,
            layout='per-axis',
        )
        self.assertEqual(plan.prompt_version, 4)
        with self.assertRaisesRegex(ValueError, 'compact layout'):
            scoring_pipeline.Plan(
                ROOT,
                json.loads(rubric_policy_file(self.root).read_text()),
                run_id='sol-original',
                dry_run=True,
                layout='per-axis',
                prompt_version=5,
            )

    def test_rationale_parts_stance_and_standing_are_required(self):
        prepared = extraction_pipeline.prepare_sources(ROOT, 'sol-original')
        judgment = self.judgments()[0]
        answer = scoring_pipeline.synthetic_judgment(judgment['outcomes'][0], prepared, 5)
        scoring_pipeline.judgment_from_answer(
            answer, judgment=judgment, prepared=prepared, answer_sha256='0' * 64, receipt_sha256='0' * 64, version=5
        )
        for broken in ['Reading: only.', 'Other levels: a. Lens: b. Reading: c.']:
            with self.assertRaisesRegex(ValueError, 'Lens: ... Reading: ... Other levels'):
                scoring_pipeline.judgment_from_answer(
                    dict(answer, rationale=broken),
                    judgment=judgment,
                    prepared=prepared,
                    answer_sha256='0' * 64,
                    receipt_sha256='0' * 64,
                    version=5,
                )
        carriage = scoring_pipeline.synthetic_carriage(prepared, 5)
        scoring_pipeline.carriage_from_answer(carriage, prepared=prepared, answer_sha256='0' * 64, version=5)
        without_stance = json.loads(json.dumps(carriage))
        for element in without_stance['seed_reading']['elements']:
            element['kind'] = 'role' if element['kind'] == 'stance' else element['kind']
        with self.assertRaisesRegex(ValueError, 'stance'):
            scoring_pipeline.carriage_from_answer(without_stance, prepared=prepared, answer_sha256='0' * 64, version=5)
        with self.assertRaisesRegex(ValueError, 'Standing kind'):
            scoring_pipeline.carriage_from_answer(
                dict(carriage, standing=[dict(carriage['standing'][0], kind='oracle')]),
                prepared=prepared,
                answer_sha256='0' * 64,
                version=5,
            )
        with self.assertRaisesRegex(ValueError, 'gestures'):
            scoring_pipeline.carriage_from_answer(
                dict(carriage, standing=[dict(carriage['standing'][0], gestures=['obey'])]),
                prepared=prepared,
                answer_sha256='0' * 64,
                version=5,
            )

    def test_masked_evidence_hides_the_seeds_words_but_keeps_the_lines(self):
        prepared = {
            'sources': [{'source_id': 'source-0001', 'lines': ['A Question became a probe.', 'touchstone, touch']}],
            'source_texts': {'a.md': 'A Question became a probe.\ntouchstone, touch\n'},
        }
        masked = scoring_pipeline.mask_evidence(prepared, ['question', 'probe', 'touch'])
        self.assertEqual(masked['sources'][0]['lines'], ['A term-01 became a term-02.', 'touchstone, term-03'])
        self.assertEqual(masked['source_texts'], prepared['source_texts'])
        terms = json.loads((ROOT / 'examples/controls/seed-vocabulary.json').read_text())
        self.assertEqual(len({term.lower() for term in terms}), len(terms))
        with self.assertRaises(ValueError):
            scoring_pipeline.mask_evidence(prepared, ['touch', 'Touch'])

    def test_a_masked_reading_is_a_control_and_never_admitted(self):
        result = scoring_pipeline.run(
            ROOT,
            rubric_policy_file(self.root),
            self.root / 'masked',
            run_id='sol-original',
            model=MODEL,
            dry_run=True,
            masked_terms_path=ROOT / 'examples/controls/seed-vocabulary.json',
        )
        status = json.loads((self.root / 'masked/status.json').read_text())
        self.assertEqual(status['control'], 'masked evidence; never admitted')
        prompt = (self.root / 'masked/sessions/01/turns/001/prompt.txt').read_text()
        evidence = prompt.split('=== SOURCE', 1)[1]
        self.assertNotRegex(evidence.split('=== END OF EVIDENCE')[0], r'(?i)\bportal\b')
        self.assertTrue(result)

    def test_the_reading_identity_is_validated_in_the_gateway(self):
        gateway = {
            'name': contracts.GATEWAY,
            'model': MODEL,
            'reasoning': 'xhigh',
            'codex_version': 'codex-cli 0.163.0',
            'router_revision': '0' * 64,
            'pipeline_sha256': '0' * 64,
        }
        contracts.gateway_record(
            dict(gateway, reading_prompt={'version': 5, 'sha256': 'a' * 64}), 'gateway', name_field='name'
        )
        with self.assertRaises(ValueError):
            contracts.gateway_record(
                dict(gateway, reading_prompt={'version': 4, 'sha256': 'a' * 64}), 'gateway', name_field='name'
            )

    def test_the_scratch_study_holds_the_decoy_as_an_authored_control(self):
        out = reading_controls.prepare(self.root / 'scratch')
        data = atlas.validate(out)
        decoy = next(run for run in data['runs'] if run['id'] == reading_controls.DECOY_RUN)
        self.assertEqual((decoy['cohort'], decoy['model']), ('control-decoy-v1', 'none (authored control)'))
        self.assertNotIn(reading_controls.DECOY_RUN, {run['id'] for run in atlas.validate(ROOT)['runs']})


if __name__ == '__main__':
    unittest.main()
