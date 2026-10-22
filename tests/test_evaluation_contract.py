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


if __name__ == '__main__':
    unittest.main()
