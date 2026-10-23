import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import evaluation_contract as contracts


FIXTURE = ROOT / 'tests/fixtures/evaluation/attribution'


class EvaluationContractTests(unittest.TestCase):
    def setUp(self):
        self.extraction = contracts.load_json(FIXTURE / 'extraction.json')
        self.policy = contracts.load_json(FIXTURE / 'scoring-policy.json')
        self.evaluation = contracts.load_json(FIXTURE / 'evaluation.json')
        self.sources = {
            path.name: path.read_text(encoding='utf-8') for path in [FIXTURE / 'source.md', FIXTURE / 'candidate.md']
        }
        self.extraction_bytes = (FIXTURE / 'extraction.json').read_bytes()
        self.policy_bytes = (FIXTURE / 'scoring-policy.json').read_bytes()

    def validate(self, *, evaluation=None, extraction=None, policy=None, source_texts=None):
        return contracts.validate_evaluation(
            evaluation or self.evaluation,
            extraction=extraction or self.extraction,
            extraction_sha256=contracts.sha(self.extraction_bytes),
            policy=policy or self.policy,
            scoring_policy_sha256=contracts.sha(self.policy_bytes),
            source_texts=self.sources if source_texts is None else source_texts,
        )

    def test_attribution_fixture_preserves_disagreement_as_unresolved(self):
        result = contracts.validate_directory(FIXTURE)
        self.assertEqual(result['judgment_samples'], 3)
        self.assertEqual(result['outcomes'], 1)
        self.assertEqual(self.evaluation['aggregations'][0]['outcome'], 'split')
        self.assertEqual(self.evaluation['outcomes'][0]['status'], 'unresolved')
        self.assertIsNone(self.evaluation['outcomes'][0]['score'])
        self.assertEqual(
            self.evaluation['aggregations'][0]['distinct_outcomes'], ['contradicted', 'mixed', 'supported']
        )

    def test_public_schema_documents_are_tracked(self):
        expected = {
            'extraction-record-v1.schema.json': 'urn:after-the-poem:extraction-record-v1',
            'scoring-policy-v1.schema.json': 'urn:after-the-poem:scoring-policy-v1',
            'extraction-record-v2.schema.json': 'urn:after-the-poem:extraction-record-v2',
            'evaluation-record-v1.schema.json': 'urn:after-the-poem:evaluation-record-v1',
            'evaluation-record-v2.schema.json': 'urn:after-the-poem:evaluation-record-v2',
            'presentation-record-v1.schema.json': 'urn:after-the-poem:presentation-record-v1',
            'model-capabilities-v1.schema.json': 'urn:after-the-poem:model-capabilities-v1',
            'evaluation-profile-v2.schema.json': 'urn:after-the-poem:evaluation-profile-v2',
            'dream-profile-v2.schema.json': 'urn:after-the-poem:dream-profile-v2',
        }
        for name, identity in expected.items():
            with self.subTest(name=name):
                schema = json.loads((ROOT / 'docs/schemas' / name).read_text(encoding='utf-8'))
                self.assertEqual(schema['$schema'], 'https://json-schema.org/draft/2020-12/schema')
                self.assertEqual(schema['$id'], identity)
                self.assertFalse(schema['additionalProperties'])

    def test_example_profiles_match_their_published_schemas(self):
        for example, schema in [
            ('evaluation-profile-deepseek.json', 'evaluation-profile-v2.schema.json'),
            ('dream-profile-deepseek.json', 'dream-profile-v2.schema.json'),
        ]:
            with self.subTest(example=example):
                value = json.loads((ROOT / 'examples' / example).read_text(encoding='utf-8'))
                document = json.loads((ROOT / 'docs/schemas' / schema).read_text(encoding='utf-8'))
                self.assertLessEqual(set(document['required']), set(value))
                self.assertLessEqual(set(value), set(document['properties']))
                budget = value.get('budget') or value.get('evaluation_budget')
                self.assertLessEqual(
                    set(budget),
                    set(
                        document['properties'].get('budget', document['properties'].get('evaluation_budget'))[
                            'properties'
                        ]
                    ),
                )

    def test_invented_quote_and_wrong_source_are_rejected(self):
        changed = copy.deepcopy(self.evaluation)
        changed['judgments'][0]['citations'][0]['quote'] = 'This sentence was never retained.'
        with self.assertRaisesRegex(ValueError, 'quote differs'):
            self.validate(evaluation=changed)
        changed = copy.deepcopy(self.evaluation)
        changed['judgments'][0]['citations'][0]['source_path'] = 'other.md'
        with self.assertRaisesRegex(ValueError, 'unknown source'):
            self.validate(evaluation=changed)

    def test_extraction_items_need_exact_evidence(self):
        changed = copy.deepcopy(self.extraction)
        changed['items'][0]['citations'] = []
        with self.assertRaisesRegex(ValueError, 'item.citations'):
            self.validate(extraction=changed)
        changed = copy.deepcopy(self.extraction)
        changed['items'][0]['citations'][0]['quote'] = 'Career-wide work.'
        with self.assertRaisesRegex(ValueError, 'quote differs'):
            self.validate(extraction=changed)

    def test_stale_extraction_identity_is_rejected(self):
        changed = copy.deepcopy(self.evaluation)
        changed['identities']['extraction_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'different extraction'):
            self.validate(evaluation=changed)

    def test_policy_cycle_and_incomplete_branch_are_rejected(self):
        cyclic = copy.deepcopy(self.policy)
        next(edge for edge in cyclic['edges'] if edge['from'] == 'attribution-consensus' and edge['when'] == 'split')[
            'to'
        ] = 'preserve-attribution'
        with self.assertRaisesRegex(ValueError, 'Cycle'):
            contracts.validate_policy(cyclic)
        incomplete = copy.deepcopy(self.policy)
        incomplete['edges'] = [
            edge
            for edge in incomplete['edges']
            if not (edge['from'] == 'preserve-attribution' and edge['when'] == 'unclear')
        ]
        with self.assertRaisesRegex(ValueError, 'every configured outcome'):
            contracts.validate_policy(incomplete)

    def test_incomplete_repetition_and_non_null_unresolved_score_are_rejected(self):
        changed = copy.deepcopy(self.evaluation)
        changed['judgments'].pop()
        with self.assertRaisesRegex(ValueError, 'repetitions are incomplete'):
            self.validate(evaluation=changed)
        changed = copy.deepcopy(self.evaluation)
        changed['outcomes'][0]['score'] = 0
        with self.assertRaisesRegex(ValueError, 'score differs'):
            self.validate(evaluation=changed)
        invalid_policy = copy.deepcopy(self.policy)
        next(node for node in invalid_policy['nodes'] if node['id'] == 'attribution-split')['result']['score'] = 0
        with self.assertRaisesRegex(ValueError, 'must use null score'):
            contracts.validate_policy(invalid_policy)

    def test_aggregation_must_match_repeated_samples(self):
        changed = copy.deepcopy(self.evaluation)
        changed['aggregations'][0]['agreement'] = 1.0
        with self.assertRaisesRegex(ValueError, 'Aggregation differs from its judgments'):
            self.validate(evaluation=changed)
        changed = copy.deepcopy(self.evaluation)
        changed['aggregations'][0]['distinct_outcomes'] = ['contradicted', 'mixed']
        with self.assertRaisesRegex(ValueError, 'Aggregation differs from its judgments'):
            self.validate(evaluation=changed)

    def test_stored_outcome_must_be_the_recomputed_winner(self):
        # A minority outcome with an unchanged agreement value is still rejected.
        changed = copy.deepcopy(self.evaluation)
        changed['aggregations'][0]['outcome'] = 'mixed'
        with self.assertRaisesRegex(ValueError, 'Aggregation differs from its judgments'):
            self.validate(evaluation=changed)

    def test_two_decimal_quorum_rounds_half_up(self):
        node = {'id': 'a', 'strategy': 'quorum', 'minimum_agreement': 0.63, 'outcomes': ['x', 'y', 'split']}
        self.assertEqual(contracts.aggregate_samples(['x'] * 5 + ['y'] * 3, node)['outcome'], 'x')
        node['minimum_agreement'] = 0.67
        self.assertEqual(contracts.aggregate_samples(['x', 'x', 'y'], node)['outcome'], 'x')
        node['strategy'] = 'majority'
        self.assertEqual(contracts.aggregate_samples(['x', 'y'], node)['outcome'], 'split')

    def test_directory_validation_rejects_source_hash_drift(self):
        with tempfile.TemporaryDirectory(prefix='after-poem-contract-') as temporary:
            root = Path(temporary)
            for name in ['extraction.json', 'scoring-policy.json', 'evaluation.json', 'source.md', 'candidate.md']:
                (root / name).write_bytes((FIXTURE / name).read_bytes())
            source = root / 'source.md'
            source.write_text(source.read_text(encoding='utf-8') + '\nchanged\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Source hash differs'):
                contracts.validate_directory(root)




class DataIntegrityHookTests(unittest.TestCase):
    """Cover each changed invariant and its tamper case; schema stays backward compatible."""

    def test_rubric_conformance_hook_accepts_match_and_rejects_tamper(self):
        import rubric_policy

        rubric = json.loads((ROOT / 'data/rubric.json').read_text())
        policy = rubric_policy.build(rubric)
        # Hook is reusable from both modules and passes for a matching pair.
        contracts.validate_rubric_conformance(policy, rubric)
        rubric_policy.validate_conformance(policy, rubric)
        self.assertTrue(rubric_policy.matches_rubric(policy, rubric))
        tampered = copy.deepcopy(rubric)
        tampered['axes'][0]['anchors'][0] = 'Tampered anchor.'
        self.assertFalse(rubric_policy.matches_rubric(policy, tampered))
        with self.assertRaisesRegex(ValueError, 'does not encode'):
            contracts.validate_rubric_conformance(policy, tampered)
        with self.assertRaisesRegex(ValueError, 'does not encode'):
            rubric_policy.validate_conformance(policy, tampered)
        # The review projection, which every admission uses, refuses a policy that no longer encodes the rubric.
        import atlas

        package = ROOT / 'data/evaluations/sol-original-muse-v5-1'
        evaluation = contracts.load_json(package / 'evaluation.json')
        policy_doc = contracts.load_json(package / 'scoring-policy.json')
        data = atlas.validate(ROOT)
        source_texts = {
            f['path']: f['text'] for f in data['corpora'][evaluation['run_id']]['files'] if f['text'] is not None
        }
        contracts.review_projection(evaluation, policy_doc, rubric, source_texts)
        with self.assertRaisesRegex(ValueError, 'does not encode'):
            contracts.review_projection(evaluation, policy_doc, tampered, source_texts)

    def test_seed_element_bounds_are_shared_six_to_twenty(self):
        self.assertEqual((contracts.SEED_ELEMENT_MIN, contracts.SEED_ELEMENT_MAX), (6, 20))

        def reading(n):
            return {
                'elements': [
                    {'id': 'e-%02d' % i, 'kind': 'role', 'statement': 's', 'seed_span': [1, 1], 'ambiguity': None}
                    for i in range(n)
                ],
                'alternatives': [],
            }

        contracts.validate_seed_reading(reading(6))
        contracts.validate_seed_reading(reading(20))
        with self.assertRaisesRegex(ValueError, '6 to 20'):
            contracts.validate_seed_reading(reading(5))
        with self.assertRaisesRegex(ValueError, '6 to 20'):
            contracts.validate_seed_reading(reading(21))
        # Tamper: the old 3-element shape is now rejected in both contract and pipeline ranges.
        with self.assertRaisesRegex(ValueError, '6 to 20'):
            contracts.validate_seed_reading(reading(3))

    def test_projection_reproduces_committed_package_reviews(self):
        import atlas as _atlas

        data = _atlas.validate(ROOT)
        rubric = json.loads((ROOT / 'data/rubric.json').read_text())
        pkg = ROOT / 'data/evaluations/sol-original-muse-v5-1'
        evaluation = contracts.load_json(pkg / 'evaluation.json')
        policy = contracts.load_json(pkg / 'scoring-policy.json')
        review = json.loads((pkg / 'review.json').read_text())
        source_texts = {
            f['path']: f['text'] for f in data['corpora'][evaluation['run_id']]['files'] if f['text'] is not None
        }
        scores, axes = contracts.review_projection(evaluation, policy, rubric, source_texts)
        self.assertEqual(review['scores'], scores)
        self.assertEqual(review['provenance'].get('axis_outcomes'), axes)
        # Split sessions keep the host's agreement note on the shortest verdict, as every committed review does.
        self.assertTrue(any('Agreement:' in entry['rationale'] for entry in scores.values()))

    def test_candidate_identity_must_match_the_extraction(self):
        extraction = contracts.load_json(FIXTURE / 'extraction.json')
        tampered = copy.deepcopy(contracts.load_json(FIXTURE / 'evaluation.json'))
        tampered['identities']['candidate_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'Evaluation and extraction identities differ'):
            contracts.validate_evaluation(
                tampered,
                extraction=extraction,
                extraction_sha256=contracts.sha((FIXTURE / 'extraction.json').read_bytes()),
                policy=contracts.load_json(FIXTURE / 'scoring-policy.json'),
                scoring_policy_sha256=contracts.sha((FIXTURE / 'scoring-policy.json').read_bytes()),
            )

    def test_collect_alternatives_dedups_and_preserves_order(self):
        readings = [
            {'seed_reading': {'alternatives': ['B reading.', 'A reading.']}},
            {'seed_reading': {'alternatives': ['A reading.', 'C reading.']}},
        ]
        self.assertEqual(contracts.collect_alternatives(readings), ['B reading.', 'A reading.', 'C reading.'])
        self.assertEqual(contracts.collect_alternatives([]), [])


if __name__ == '__main__':
    unittest.main()
