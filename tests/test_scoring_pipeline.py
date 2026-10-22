import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import codex_worker as worker
import extraction_pipeline
import rubric_policy
import scoring_pipeline
import session_flow


POLICY = ROOT / 'tests/fixtures/evaluation/attribution/scoring-policy.json'
MODEL = 'deepseek/deepseek-v4.1-flash-1017'


def rubric_policy_file(root, repetitions=1):
    path = root / 'rubric-policy.json'
    path.write_text(
        json.dumps(rubric_policy.build(json.loads((ROOT / 'data/rubric.json').read_text()), repetitions=repetitions)),
        encoding='utf-8',
    )
    return path


class ScoringPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='after-poem-scoring-')
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def make_extraction(self):
        output = self.root / 'extraction'
        extraction_pipeline.run(
            ROOT, 'sol-original', output, model=MODEL, extraction_id='sol-original-score-test', dry_run=True
        )
        return output / 'extraction.json'

    def score(self, outcomes, name='evaluation', **options):
        return scoring_pipeline.run(
            ROOT,
            POLICY,
            self.root / name,
            extraction_path=self.make_extraction(),
            model=MODEL,
            evaluation_id='sol-original-' + name,
            dry_run=True,
            synthetic_outcomes=outcomes,
            **options,
        )

    def test_split_sessions_end_unresolved_without_a_score(self):
        result = self.score(['contradicted', 'mixed', 'supported'])
        self.assertEqual(result['outcomes'], {'preserve-attribution': 'split'})
        self.assertEqual((result['sessions'], result['turns']), (3, 3))
        record = json.loads((self.root / 'evaluation/evaluation.json').read_text(encoding='utf-8'))
        self.assertEqual(record['schema_version'], 2)
        self.assertEqual([item['outcome'] for item in record['judgments']], ['contradicted', 'mixed', 'supported'])
        self.assertEqual(record['aggregations'][0]['agreement'], 0.333333)
        self.assertIsNone(record['outcomes'][0]['score'])
        self.assertNotIn('quote', record['judgments'][0]['citations'][0])
        self.assertEqual(scoring_pipeline.validate_output(ROOT, self.root / 'evaluation')['judgment_samples'], 3)

    def test_unanimous_and_two_of_three_reach_the_scored_terminal(self):
        self.assertEqual(self.score(['supported'] * 3, 'unanimous')['scored_axes'], 1)
        result = self.score(['supported', 'supported', 'contradicted'], 'quorum')
        self.assertEqual(result['outcomes'], {'preserve-attribution': 'supported'})
        record = json.loads((self.root / 'quorum/evaluation.json').read_text(encoding='utf-8'))
        self.assertEqual(record['aggregations'][0]['agreement'], 0.666667)

    def test_unclear_sessions_go_to_human_review(self):
        result = self.score(['unclear'] * 3)
        self.assertEqual((result['scored_axes'], result['unresolved_axes']), (0, 1))

    def test_synthetic_outcomes_must_match_the_declared_repetitions(self):
        with self.assertRaisesRegex(ValueError, 'repetitions'):
            self.score(['supported'])

    def test_live_scoring_requires_acceptance_and_budget_before_output(self):
        extraction = self.make_extraction()
        with self.assertRaisesRegex(ValueError, 'accept-run'):
            scoring_pipeline.run(ROOT, POLICY, self.root / 'live', extraction_path=extraction, model=MODEL)
        with self.assertRaisesRegex(ValueError, 'budget'):
            scoring_pipeline.run(
                ROOT, POLICY, self.root / 'live', extraction_path=extraction, model=MODEL, accept_run=True
            )
        self.assertFalse((self.root / 'live').exists())

    def test_synthetic_extraction_cannot_feed_live_scoring(self):
        with self.assertRaisesRegex(ValueError, 'synthetic extraction'):
            scoring_pipeline.run(
                ROOT,
                POLICY,
                self.root / 'live',
                extraction_path=self.make_extraction(),
                model=MODEL,
                accept_run=True,
                budget={'max_turns': 9, 'max_input_tokens': 10**7, 'max_output_tokens': 10**6},
            )

    def test_one_session_reads_evidence_once_and_resumes_its_thread(self):
        calls = []
        original = worker.run_turn

        def recording(session, turn, job, prompt, *args, **kwargs):
            calls.append({'prompt': prompt, 'thread': kwargs['thread_id'], 'name': kwargs['instruction_name']})
            return original(session, turn, job, prompt, *args, **kwargs)

        with mock.patch.object(worker, 'run_turn', side_effect=recording):
            result = scoring_pipeline.run(
                ROOT,
                rubric_policy_file(self.root),
                self.root / 'session',
                run_id='sol-original',
                model=MODEL,
                dry_run=True,
                layout='per-axis',
            )
        self.assertEqual((result['sessions'], result['turns']), (1, 9))
        self.assertTrue(calls[0]['prompt'].startswith(b'EVIDENCE'))
        self.assertIn(b'TASK extraction', calls[0]['prompt'])
        self.assertIsNone(calls[0]['thread'])
        thread = json.loads((self.root / 'session/sessions/01/turns/001/receipt.json').read_text())['thread_id']
        for call in calls[1:]:
            self.assertTrue(call['prompt'].startswith(b'TASK '))
            self.assertEqual(call['thread'], thread)
        self.assertEqual({call['name'] for call in calls}, {'session'})
        extraction = json.loads((self.root / 'session/extraction.json').read_text())
        self.assertEqual(extraction['schema_version'], 2)
        self.assertEqual(result['estimate']['turns'], 9)

    def test_compact_layout_reads_and_judges_every_axis_in_one_turn(self):
        result = scoring_pipeline.run(
            ROOT, rubric_policy_file(self.root), self.root / 'compact', run_id='sol-original', model=MODEL, dry_run=True
        )
        self.assertEqual((result['sessions'], result['turns'], result['criteria']), (1, 1, 8))
        prompt = (self.root / 'compact/sessions/01/turns/001/prompt.txt').read_text()
        self.assertTrue(prompt.startswith('EVIDENCE'))
        self.assertIn('TASK reading · 8 criteria', prompt)
        self.assertIn('"inventory"', prompt)
        for axis in json.loads((ROOT / 'data/rubric.json').read_text())['axes']:
            self.assertIn('## ' + axis['id'] + ' · ', prompt)
        record = json.loads((self.root / 'compact/evaluation.json').read_text())
        self.assertEqual(len({judgment['answer_sha256'] for judgment in record['judgments']}), 1)
        self.assertEqual(json.loads((self.root / 'compact/extraction.json').read_text())['schema_version'], 2)
        self.assertEqual(scoring_pipeline.validate_output(ROOT, self.root / 'compact')['turns'], 1)

    def test_compact_answer_missing_an_axis_is_repaired_as_a_whole(self):
        original = worker.run_turn
        broken = {'done': False}

        def drop_axis(session, turn, job, prompt, *args, **kwargs):
            if not broken['done']:
                broken['done'] = True
                answer = json.loads(kwargs['fake_result'])
                answer['judgments'].pop('consent')
                kwargs['fake_result'] = json.dumps(answer)
            return original(session, turn, job, prompt, *args, **kwargs)

        with mock.patch.object(worker, 'run_turn', side_effect=drop_axis):
            result = scoring_pipeline.run(
                ROOT,
                rubric_policy_file(self.root),
                self.root / 'compact-repair',
                run_id='sol-original',
                model=MODEL,
                dry_run=True,
            )
        self.assertEqual(result['turns'], 2)
        self.assertIn(
            b'must name every criterion', (self.root / 'compact-repair/sessions/01/turns/002/prompt.txt').read_bytes()
        )

    def test_wall_time_budget_stops_before_the_next_turn(self):
        original = worker.run_turn

        def slow(*args, **kwargs):
            receipt = original(*args, **kwargs)
            receipt['elapsed_seconds'] = 400
            (Path(args[1]) / 'receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
            return receipt

        extraction = self.make_extraction()
        with mock.patch.object(worker, 'run_turn', side_effect=slow):
            with self.assertRaisesRegex(session_flow.SessionStopped, 'seconds of turn time'):
                scoring_pipeline.run(
                    ROOT,
                    POLICY,
                    self.root / 'slow',
                    extraction_path=extraction,
                    model=MODEL,
                    dry_run=True,
                    synthetic_outcomes=['supported'] * 3,
                    budget={'max_turns': 9, 'max_input_tokens': 10**8, 'max_output_tokens': 10**6, 'max_seconds': 300},
                )
        self.assertEqual(len(session_flow.ledger(self.root / 'slow')), 1)

    def test_invalid_answer_is_preserved_and_repaired_in_the_same_session(self):
        original = worker.run_turn
        broken = {'done': False}

        def flaky(session, turn, job, prompt, *args, **kwargs):
            if not broken['done'] and b'TASK preserve-attribution' in prompt:
                broken['done'] = True
                kwargs['fake_result'] = '{"outcome": "supported"}'
            return original(session, turn, job, prompt, *args, **kwargs)

        extraction = self.make_extraction()
        with mock.patch.object(worker, 'run_turn', side_effect=flaky):
            result = scoring_pipeline.run(
                ROOT,
                POLICY,
                self.root / 'repair',
                extraction_path=extraction,
                model=MODEL,
                dry_run=True,
                synthetic_outcomes=['supported'] * 3,
                layout='per-axis',
            )
        self.assertEqual((result['turns'], result['usage']['calls']), (4, 4))
        turns = self.root / 'repair/sessions/01/turns'
        self.assertEqual((turns / '001/final-message.txt').read_text(), '{"outcome": "supported"}')
        meta = json.loads((turns / '002/turn.json').read_text())
        self.assertEqual((meta['kind'], meta['attempt']), ('repair', 2))
        self.assertIn(b'failed host validation', (turns / '002/prompt.txt').read_bytes())
        record = json.loads((self.root / 'repair/evaluation.json').read_text())
        self.assertEqual(record['judgments'][0]['attempt'], 2)
        self.assertEqual(scoring_pipeline.validate_output(ROOT, self.root / 'repair')['turns'], 4)

    def test_repair_limit_stops_and_a_raised_limit_resumes(self):
        original = worker.run_turn

        def always_invalid(session, turn, job, prompt, *args, **kwargs):
            if Path(session).name == '01':
                kwargs['fake_result'] = 'not json'
            return original(session, turn, job, prompt, *args, **kwargs)

        extraction = self.make_extraction()
        options = dict(extraction_path=extraction, model=MODEL, dry_run=True, synthetic_outcomes=['supported'] * 3)
        with mock.patch.object(worker, 'run_turn', side_effect=always_invalid):
            with self.assertRaisesRegex(session_flow.SessionStopped, 'failed 2 time'):
                scoring_pipeline.run(ROOT, POLICY, self.root / 'limit', repairs=1, **options)
        self.assertEqual(len(list((self.root / 'limit/sessions/01/turns').iterdir())), 2)
        result = scoring_pipeline.run(ROOT, POLICY, self.root / 'limit', repairs=2, **options)
        self.assertEqual(result['turns'], 5)
        self.assertEqual(len(list((self.root / 'limit/sessions/01/turns').iterdir())), 3)

    def test_cancelled_turn_stops_and_resume_retries_without_repeating_answers(self):
        original = worker.run_turn
        state = {'cancelled': False}

        def cancel_second(session, turn, job, prompt, *args, **kwargs):
            if Path(session).name == '02' and not state['cancelled']:
                state['cancelled'] = True
                raise KeyboardInterrupt
            return original(session, turn, job, prompt, *args, **kwargs)

        extraction = self.make_extraction()
        options = dict(extraction_path=extraction, model=MODEL, dry_run=True, synthetic_outcomes=['supported'] * 3)
        with mock.patch.object(worker, 'run_turn', side_effect=cancel_second):
            with self.assertRaises(KeyboardInterrupt):
                scoring_pipeline.run(ROOT, POLICY, self.root / 'cancel', **options)
        launched = []
        with mock.patch.object(
            worker,
            'run_turn',
            side_effect=lambda *args, **kwargs: launched.append(args[0]) or original(*args, **kwargs),
        ):
            result = scoring_pipeline.run(ROOT, POLICY, self.root / 'cancel', **options)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual({Path(path).name for path in launched}, {'02', '03'})

    def test_budget_counts_repairs_and_stops_before_the_next_turn(self):
        original = worker.run_turn
        broken = {'done': False}

        def flaky(session, turn, job, prompt, *args, **kwargs):
            if not broken['done']:
                broken['done'] = True
                kwargs['fake_result'] = 'not json'
            return original(session, turn, job, prompt, *args, **kwargs)

        extraction = self.make_extraction()
        with mock.patch.object(worker, 'run_turn', side_effect=flaky):
            with self.assertRaisesRegex(session_flow.SessionStopped, 'Budget reached'):
                scoring_pipeline.run(
                    ROOT,
                    POLICY,
                    self.root / 'budget',
                    extraction_path=extraction,
                    model=MODEL,
                    dry_run=True,
                    synthetic_outcomes=['supported'] * 3,
                    budget={'max_turns': 3, 'max_input_tokens': 10**8, 'max_output_tokens': 10**6},
                )
        self.assertEqual(len(session_flow.ledger(self.root / 'budget')), 3)

    def test_budget_below_the_planned_minimum_is_refused_before_launch(self):
        with self.assertRaisesRegex(ValueError, 'planned'):
            scoring_pipeline.run(
                ROOT,
                rubric_policy_file(self.root),
                self.root / 'small',
                run_id='sol-original',
                model=MODEL,
                dry_run=True,
                budget={'max_turns': 9, 'max_input_tokens': 1000, 'max_output_tokens': 1000},
            )
        self.assertEqual(session_flow.ledger(self.root / 'small'), [])

    def test_resumed_thread_usage_is_cumulative_and_counted_once(self):
        # Codex reports thread totals on resumed turns (observed in the first live probe).
        receipts = [
            {
                'status': 'completed',
                'thread_id': 'a',
                'usage': {'input_tokens': 34679, 'cached_input_tokens': 0, 'output_tokens': 18515},
            },
            {
                'status': 'completed',
                'thread_id': 'a',
                'usage': {'input_tokens': 88449, 'cached_input_tokens': 53120, 'output_tokens': 25668},
            },
            {
                'status': 'completed',
                'thread_id': 'b',
                'usage': {'input_tokens': 35998, 'cached_input_tokens': 0, 'output_tokens': 21218},
            },
            {'status': 'cancelled', 'usage': {}},
        ]
        totals = session_flow.usage_totals(receipts)
        self.assertEqual(
            (totals['input_tokens'], totals['cached_input_tokens'], totals['output_tokens']),
            (88449 + 35998, 53120, 25668 + 21218),
        )
        self.assertEqual((totals['calls'], totals['unmetered_calls']), (4, 1))

    def test_compact_v4_records_seed_reading_and_carriage(self):
        result = scoring_pipeline.run(
            ROOT, rubric_policy_file(self.root), self.root / 'v4', run_id='sol-original', model=MODEL, dry_run=True
        )
        self.assertEqual(result['turns'], 1)
        prompt = (self.root / 'v4/sessions/01/turns/001/prompt.txt').read_text()
        self.assertIn('"seed_reading"', prompt)
        self.assertIn('naming a theme is not carrying', prompt)
        carriage = json.loads((self.root / 'v4/carriage.json').read_text())
        self.assertEqual(carriage['evaluation_id'], 'evaluation-sol-original')
        reading = carriage['readings'][0]
        self.assertEqual({entry['status'] for entry in reading['carriage']}, {'carried', 'lost'})
        self.assertEqual(scoring_pipeline.validate_output(ROOT, self.root / 'v4')['turns'], 1)

    def test_carriage_must_account_for_every_seed_element(self):
        import evaluation_contract as contracts

        reading = {
            'elements': [
                {'id': name, 'kind': 'role', 'statement': 's', 'seed_span': [1, 1], 'ambiguity': None}
                for name in ['a', 'b', 'c']
            ],
            'alternatives': [],
        }
        ids = contracts.validate_seed_reading(reading)
        lost = [{'element': name, 'status': 'lost', 'note': 'n', 'citations': []} for name in ['a', 'b']]
        with self.assertRaisesRegex(ValueError, 'missing c'):
            contracts.validate_carriage(lost, ids)
        with self.assertRaisesRegex(ValueError, 'name no seed element'):
            contracts.validate_carriage(
                lost + [{'element': 'c', 'status': 'introduced', 'note': 'n', 'citations': []}], ids
            )
        with self.assertRaisesRegex(ValueError, 'Invalid carriage citations'):
            contracts.validate_carriage(
                lost + [{'element': 'c', 'status': 'carried', 'note': 'n', 'citations': []}], ids
            )

    def test_control_seed_reading_is_recorded_but_never_admitted(self):
        control = ROOT / 'examples/controls/owl-and-the-pussy-cat.txt'
        result = scoring_pipeline.run(
            ROOT,
            rubric_policy_file(self.root),
            self.root / 'control',
            run_id='sol-original',
            model=MODEL,
            dry_run=True,
            control_seed_path=control,
        )
        self.assertIn('control', result)
        prompt = (self.root / 'control/sessions/01/turns/001/prompt.txt').read_text()
        seed_block = prompt.split('=== SEED')[1].split('=== SOURCE')[0]
        self.assertIn('Pussy-cat', seed_block)
        self.assertNotIn('Glory Portal', seed_block)
        plan = json.loads((self.root / 'control/plan.json').read_text())
        self.assertIn('control_seed_sha256', plan)
        self.assertIn('control', scoring_pipeline.validate_output(ROOT, self.root / 'control'))

    def test_redundant_null_shape_is_normalized_and_recorded(self):
        answer, repairs = scoring_pipeline.normalize_answer_shape(
            {
                'outcome': 'supported',
                'confidence': 'low',
                'verdict': 'v',
                'seed_span': [1, 1],
                'citations': [],
                'counter_citations': [],
                'rationale': 'r',
                'seed_span_note': None,
            }
        )
        self.assertNotIn('seed_span_note', answer)
        self.assertEqual(repairs, ['removed redundant null field seed_span_note'])
        with self.assertRaisesRegex(ValueError, 'non-null'):
            scoring_pipeline.normalize_answer_shape(dict(answer, rationale_note='x'))

    def test_verdict_rejects_level_narration_and_multiple_sentences(self):
        with self.assertRaisesRegex(ValueError, 'narrate'):
            scoring_pipeline.validate_verdict('This is level 3 because of evidence.')
        with self.assertRaisesRegex(ValueError, 'single sentence'):
            scoring_pipeline.validate_verdict('First claim. Second claim.')

    def test_preserved_sessions_recover_without_inference(self):
        self.score(['supported', 'mixed', 'supported'])
        recovered = self.root / 'recovered'
        shutil.copytree(self.root / 'evaluation', recovered)
        (recovered / 'evaluation.json').unlink()
        (recovered / 'status.json').unlink()
        with mock.patch.object(worker, 'run_turn', side_effect=AssertionError('no inference during recovery')):
            result = scoring_pipeline.recover(ROOT, recovered, evaluation_id='recovered-evaluation')
        self.assertTrue(result['recovered'])
        self.assertEqual(result['outcomes'], {'preserve-attribution': 'supported'})

    def test_validation_detects_preserved_answer_tampering(self):
        self.score(['supported'] * 3)
        answer = self.root / 'evaluation/sessions/02/turns/001/final-message.txt'
        answer.write_text(answer.read_text().replace('"low"', '"high"'), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'answer changed'):
            scoring_pipeline.validate_output(ROOT, self.root / 'evaluation')


if __name__ == '__main__':
    unittest.main()
