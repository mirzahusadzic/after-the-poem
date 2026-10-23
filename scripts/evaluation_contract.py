#!/usr/bin/env python3
"""Validate normalized extraction, scoring-policy and evaluation records."""

import argparse
import hashlib
import json
import math
import re
import sys
import unicodedata
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path, PurePosixPath

from bundles import inspect_metadata, require


ROOT = Path(__file__).resolve().parents[1]
DIGEST = re.compile(r'[a-f0-9]{64}')
SAFE_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,99}')
GATEWAY = 'codex-unified-model-router'
REASONING = {'none', 'low', 'medium', 'high', 'xhigh', 'max'}
ITEM_KINDS = {'claim', 'actor', 'relationship', 'condition', 'boundary', 'question', 'continuation', 'ambiguity'}
NODE_KINDS = {'task', 'judgment', 'aggregate', 'gate', 'terminal'}
BRANCH_KINDS = {'judgment', 'aggregate', 'gate'}
AGGREGATION_STRATEGIES = {'unanimous', 'majority', 'quorum'}
TERMINAL_STATUSES = {'scored', 'unresolved', 'review'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def load_json(path):
    def no_duplicates(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, 'Duplicate JSON key in ' + Path(path).name + ': ' + key)
            value[key] = item
        return value

    try:
        return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError('Invalid JSON in ' + Path(path).name + ': ' + str(error)) from error


def exact_fields(value, fields, label):
    require(
        isinstance(value, dict) and set(value) == set(fields), label + ' fields must be exactly: ' + ', '.join(fields)
    )


def safe_path(value, label='path'):
    require(isinstance(value, str) and value, 'Missing ' + label)
    path = PurePosixPath(value)
    require(
        not path.is_absolute() and '..' not in path.parts and str(path) == value and '\\' not in value,
        'Unsafe ' + label + ': ' + repr(value),
    )
    require(
        unicodedata.is_normalized('NFC', value) and not re.search(r'[\x00-\x1f\x7f]', value),
        'Non-canonical ' + label + ': ' + repr(value),
    )
    return path


def safe_id(value, label='ID'):
    require(isinstance(value, str) and SAFE_ID.fullmatch(value), 'Invalid ' + label + ': ' + repr(value))
    return value


def digest(value, label='digest'):
    require(isinstance(value, str) and DIGEST.fullmatch(value), 'Invalid ' + label)
    return value


def bounded_text(value, label, maximum=16000):
    require(isinstance(value, str) and 1 <= len(value) <= maximum, 'Invalid ' + label)
    return value


def gateway_record(value, label, *, name_field='gateway'):
    fields = [name_field, 'model', 'reasoning', 'codex_version', 'router_revision', 'pipeline_sha256']
    if 'answer_sha256' in value or 'receipt_sha256' in value:
        fields += ['answer_sha256', 'receipt_sha256']
    # Readings from prompt version 5 name the reading they were given.
    if 'reading_prompt' in value:
        fields += ['reading_prompt']
    exact_fields(value, fields, label)
    if 'reading_prompt' in value:
        exact_fields(value['reading_prompt'], ['version', 'sha256'], label + '.reading_prompt')
        require(
            type(value['reading_prompt']['version']) is int and value['reading_prompt']['version'] >= 5,
            'Invalid ' + label + '.reading_prompt version',
        )
        digest(value['reading_prompt']['sha256'], label + '.reading_prompt.sha256')
    require(value[name_field] == GATEWAY, label + ' must use ' + GATEWAY)
    bounded_text(value['model'], label + '.model')
    require(value['reasoning'] in REASONING, 'Invalid ' + label + '.reasoning')
    bounded_text(value['codex_version'], label + '.codex_version', 4000)
    digest(value['router_revision'], label + '.router_revision')
    digest(value['pipeline_sha256'], label + '.pipeline_sha256')
    if 'answer_sha256' in value:
        digest(value['answer_sha256'], label + '.answer_sha256')
        digest(value['receipt_sha256'], label + '.receipt_sha256')


def citation_lines(citation, sources=None, *, quoted=True):
    """Check one citation. Schema 2 records keep exact ranges and resolve quotes from pinned sources."""
    exact_fields(citation, ['source_path', 'start', 'end'] + (['quote'] if quoted else []), 'citation')
    safe_path(citation['source_path'], 'citation source_path')
    start, end = citation['start'], citation['end']
    require(
        type(start) is int and type(end) is int and 1 <= start <= end,
        'Citation line range must be positive and inclusive',
    )
    if quoted:
        bounded_text(citation['quote'], 'citation quote', 65536)
    if sources is not None:
        require(citation['source_path'] in sources, 'Citation names an unknown source: ' + citation['source_path'])
        lines = sources[citation['source_path']].splitlines()
        require(end <= len(lines), 'Citation exceeds source lines: ' + citation['source_path'])
        text = '\n'.join(lines[start - 1 : end])
        if quoted:
            require(
                citation['quote'] == text, 'Citation quote differs from retained source: ' + citation['source_path']
            )
        else:
            require(text.strip(), 'Citation resolves to blank lines: ' + citation['source_path'])


def citations(value, label, *, sources=None, minimum=0, maximum=32, quoted=True):
    require(isinstance(value, list) and minimum <= len(value) <= maximum, 'Invalid ' + label)
    for item in value:
        citation_lines(item, sources, quoted=quoted)


def quote_citation(citation, sources):
    """Return a schema-1 style citation with its exact quote resolved from the pinned source."""
    lines = sources[citation['source_path']].splitlines()
    return {
        'source_path': citation['source_path'],
        'start': citation['start'],
        'end': citation['end'],
        'quote': '\n'.join(lines[citation['start'] - 1 : citation['end']]),
    }


def meets_agreement(highest, total, threshold):
    """Compare agreement at the two-decimal precision used to author thresholds, rounding half up."""
    share = (Decimal(highest) / Decimal(total)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return share >= Decimal(str(threshold))


def aggregate_samples(outcomes, aggregate_node):
    """Deterministically combine sample outcomes for one aggregate node."""
    require(outcomes, 'Aggregation needs at least one sample')
    counts = Counter(outcomes)
    highest = max(counts.values())
    winners = [outcome for outcome, count in counts.items() if count == highest]
    strategy = aggregate_node['strategy']
    if strategy == 'unanimous':
        passed = highest == len(outcomes)
    elif strategy == 'majority':
        passed = highest * 2 > len(outcomes)
    else:
        passed = meets_agreement(highest, len(outcomes), aggregate_node['minimum_agreement'])
    outcome = winners[0] if len(winners) == 1 and passed else 'split'
    require(outcome in aggregate_node['outcomes'], 'Aggregate outcome is not declared by the policy')
    return {
        'node_id': aggregate_node['id'],
        'strategy': strategy,
        'sample_count': len(outcomes),
        'outcome': outcome,
        'agreement': round(highest / len(outcomes), 6),
        'distinct_outcomes': sorted(counts),
    }


def terminal_for(outgoing, nodes, aggregate_id, outcome):
    """Follow the selected aggregate edge through pass-through tasks to its terminal node."""
    edges = [edge for edge in outgoing.get(aggregate_id, []) if edge['when'] == outcome]
    require(len(edges) == 1, 'Aggregate outcome does not select exactly one edge')
    current = nodes[edges[0]['to']]
    visited = {aggregate_id}
    while current['kind'] != 'terminal':
        require(current['id'] not in visited, 'Scoreflow revisits a node')
        visited.add(current['id'])
        require(current['kind'] == 'task', 'Scoreflow supports only task pass-through before a terminal')
        edges = outgoing.get(current['id'], [])
        require(len(edges) == 1, 'Pass-through task must have one outgoing edge')
        current = nodes[edges[0]['to']]
    return current


def resolve_line_range(text, start, end):
    """Resolve inclusive lines, accepting only a final-newline off-by-one."""
    lines = text.splitlines()
    require(
        type(start) is int and type(end) is int and 1 <= start <= end,
        'Citation line range must be positive and inclusive',
    )
    repaired = False
    if end == len(lines) + 1 and 1 <= start <= len(lines):
        end -= 1
        repaired = True
    require(
        1 <= start <= end <= len(lines), 'Citation line range is invalid: source has ' + str(len(lines)) + ' line(s)'
    )
    return '\n'.join(lines[start - 1 : end]), repaired


def validate_extraction(record, *, source_texts=None):
    exact_fields(
        record,
        ['schema_version', 'extraction_id', 'run_id', 'identities', 'extractor', 'sources', 'items', 'unknowns'],
        'extraction record',
    )
    require(record['schema_version'] in (1, 2), 'Unsupported extraction schema')
    quoted = record['schema_version'] == 1
    safe_id(record['extraction_id'], 'extraction_id')
    safe_id(record['run_id'], 'run_id')
    identities = record['identities']
    exact_fields(identities, ['prompt_sha256', 'corpus_sha256', 'candidate_sha256'], 'extraction identities')
    for key, value in identities.items():
        digest(value, 'extraction.' + key)
    gateway_record(record['extractor'], 'extractor')
    require(
        isinstance(record['sources'], list) and 1 <= len(record['sources']) <= 1024,
        'Sources must be a bounded non-empty list',
    )
    source_paths = set()
    local_sources = {}
    for source in record['sources']:
        exact_fields(source, ['path', 'sha256', 'lines'], 'source')
        path = safe_path(source['path'], 'source path')
        require(str(path) not in source_paths, 'Duplicate source path: ' + source['path'])
        source_paths.add(str(path))
        digest(source['sha256'], 'source.sha256')
        require(type(source['lines']) is int and source['lines'] >= 0, 'Source line count must be non-negative')
        if source_texts is not None:
            require(source['path'] in source_texts, 'Retained source is missing: ' + source['path'])
            text = source_texts[source['path']]
            require(sha(text.encode('utf-8')) == source['sha256'], 'Source hash differs: ' + source['path'])
            require(len(text.splitlines()) == source['lines'], 'Source line count differs: ' + source['path'])
            local_sources[source['path']] = text
    items = record['items']
    require(isinstance(items, list) and 1 <= len(items) <= 4096, 'Extraction items must be a bounded non-empty list')
    item_ids = set()
    for item in items:
        exact_fields(item, ['id', 'kind', 'statement', 'citations', 'ambiguity'], 'extraction item')
        safe_id(item['id'], 'item.id')
        require(item['id'] not in item_ids, 'Duplicate extraction item ID: ' + item['id'])
        item_ids.add(item['id'])
        require(item['kind'] in ITEM_KINDS, 'Invalid extraction item kind: ' + str(item['kind']))
        bounded_text(item['statement'], 'item.statement')
        citations(
            item['citations'],
            'item.citations',
            sources=local_sources if source_texts is not None else None,
            minimum=1,
            quoted=quoted,
        )
        require(
            item['ambiguity'] is None or isinstance(item['ambiguity'], str) and len(item['ambiguity']) <= 8000,
            'Invalid item ambiguity',
        )
    require(isinstance(record['unknowns'], list) and len(record['unknowns']) <= 128, 'Invalid extraction unknowns')
    for unknown in record['unknowns']:
        bounded_text(unknown, 'extraction unknown', 4000)
    inspect_metadata(record, 'extraction record')
    return {item['id'] for item in record['items']}


def _validate_node(node):
    require(isinstance(node, dict) and node.get('kind') in NODE_KINDS, 'Unknown scoring-policy node')
    kind = node['kind']
    if kind == 'task':
        exact_fields(node, ['id', 'kind', 'instruction'], 'task node')
        bounded_text(node['instruction'], 'task instruction')
    elif kind == 'judgment':
        allowed = {'id', 'kind', 'criterion', 'question', 'outcomes', 'repetitions', 'anchors'}
        require(
            set(node) == allowed - ({'anchors'} if 'anchors' not in node else set()),
            'judgment node fields must be exactly: id, kind, criterion, question, outcomes, repetitions'
            + (', anchors' if 'anchors' in node else ''),
        )
        if 'anchors' in node:
            require(
                isinstance(node['anchors'], list)
                and 2 <= len(node['anchors']) <= 8
                and all(isinstance(value, str) and value for value in node['anchors']),
                'Judgment anchors must be a bounded non-empty string list',
            )
        bounded_text(node['criterion'], 'judgment criterion', 4000)
        bounded_text(node['question'], 'judgment question')
        require(
            isinstance(node['outcomes'], list)
            and 2 <= len(node['outcomes']) <= 8
            and len(set(node['outcomes'])) == len(node['outcomes']),
            'Judgment outcomes must be unique',
        )
        for outcome in node['outcomes']:
            safe_id(outcome, 'judgment outcome')
        require(
            type(node['repetitions']) is int and 1 <= node['repetitions'] <= 20, 'Judgment repetitions must be 1-20'
        )
    elif kind == 'aggregate':
        exact_fields(node, ['id', 'kind', 'strategy', 'minimum_agreement', 'outcomes'], 'aggregate node')
        require(node['strategy'] in AGGREGATION_STRATEGIES, 'Invalid aggregate strategy')
        require(
            type(node['minimum_agreement']) in [int, float] and 0 < node['minimum_agreement'] <= 1,
            'Aggregate minimum agreement must be in (0, 1]',
        )
        require(
            isinstance(node['outcomes'], list)
            and 2 <= len(node['outcomes']) <= 8
            and len(set(node['outcomes'])) == len(node['outcomes']),
            'Aggregate outcomes must be unique',
        )
        for outcome in node['outcomes']:
            safe_id(outcome, 'aggregate outcome')
    elif kind == 'gate':
        exact_fields(node, ['id', 'kind', 'condition', 'outcomes'], 'gate node')
        bounded_text(node['condition'], 'gate condition')
        require(
            isinstance(node['outcomes'], list)
            and 2 <= len(node['outcomes']) <= 8
            and len(set(node['outcomes'])) == len(node['outcomes']),
            'Gate outcomes must be unique',
        )
        for outcome in node['outcomes']:
            safe_id(outcome, 'gate outcome')
    else:
        exact_fields(node, ['id', 'kind', 'result'], 'terminal node')
        result = node['result']
        exact_fields(result, ['status', 'score', 'label'], 'terminal result')
        require(result['status'] in TERMINAL_STATUSES, 'Invalid terminal status')
        bounded_text(result['label'], 'terminal label', 4000)
        if result['status'] == 'scored':
            require(
                type(result['score']) in [int, float] and math.isfinite(result['score']),
                'Scored terminals require a finite numeric score',
            )
        else:
            require(result['score'] is None, 'Unresolved and review terminals must use null score')
    safe_id(node['id'], 'node.id')


def validate_policy(policy):
    exact_fields(
        policy, ['schema_version', 'policy_id', 'version', 'description', 'roots', 'nodes', 'edges'], 'scoring policy'
    )
    require(policy['schema_version'] == 1, 'Unsupported scoring-policy schema')
    safe_id(policy['policy_id'], 'policy_id')
    bounded_text(policy['version'], 'policy version', 200)
    bounded_text(policy['description'], 'policy description')
    nodes = policy['nodes']
    require(isinstance(nodes, list) and 2 <= len(nodes) <= 256, 'Scoring policy nodes must be a bounded list')
    by_id = {}
    for node in nodes:
        _validate_node(node)
        require(node['id'] not in by_id, 'Duplicate scoring-policy node: ' + node['id'])
        by_id[node['id']] = node
    require(
        any(node['kind'] == 'terminal' for node in by_id.values()), 'Scoring policy needs at least one terminal node'
    )
    roots = policy['roots']
    require(
        isinstance(roots, list) and 1 <= len(roots) <= 32 and len(set(roots)) == len(roots),
        'Scoring-policy roots must be unique',
    )
    require(all(root in by_id for root in roots), 'Unknown scoring-policy root')
    edges = policy['edges']
    require(
        isinstance(edges, list) and 1 <= len(edges) <= 1024, 'Scoring-policy edges must be a bounded non-empty list'
    )
    outgoing, incoming = {}, {}
    edge_keys = set()
    for edge in edges:
        exact_fields(edge, ['from', 'to', 'when'], 'edge')
        require(edge['from'] in by_id and edge['to'] in by_id, 'Edge names an unknown node')
        require(edge['when'] is None or isinstance(edge['when'], str), 'Edge condition must be a string or null')
        key = (edge['from'], edge['to'], edge['when'])
        require(key not in edge_keys, 'Duplicate scoring-policy edge')
        edge_keys.add(key)
        outgoing.setdefault(edge['from'], []).append(edge)
        incoming.setdefault(edge['to'], []).append(edge)
    require(not (set(roots) & set(incoming)), 'A root cannot have an incoming edge')
    for node_id, node in by_id.items():
        children = outgoing.get(node_id, [])
        if node['kind'] == 'terminal':
            require(not children, 'Terminal node cannot have outgoing edges: ' + node_id)
        else:
            require(bool(children), 'Non-terminal node has no outgoing edge: ' + node_id)
        if node['kind'] in BRANCH_KINDS:
            whens = [edge['when'] for edge in children]
            require(
                None not in whens and set(whens) == set(node['outcomes']) and len(whens) == len(node['outcomes']),
                'Branching node must wire every configured outcome exactly once: ' + node_id,
            )
        elif node['kind'] == 'task':
            require(all(edge['when'] is None for edge in children), 'Task edges must use a null condition')
    visited, stack = set(), set()

    def visit(node_id):
        require(node_id not in stack, 'Cycle detected in scoring policy at ' + node_id)
        if node_id in visited:
            return
        visited.add(node_id)
        stack.add(node_id)
        for edge in outgoing.get(node_id, []):
            visit(edge['to'])
        stack.remove(node_id)

    for root in roots:
        visit(root)
    require(visited == set(by_id), 'Scoring policy contains nodes unreachable from its roots')
    inspect_metadata(policy, 'scoring policy')
    return by_id, outgoing, incoming


JUDGMENT_FIELDS = [
    'node_id',
    'sample',
    'outcome',
    'verdict',
    'seed_span',
    'citations',
    'counter_citations',
    'rationale',
    'answer_sha256',
    'receipt_sha256',
]
USAGE_FIELDS = {
    1: ['calls', 'input_tokens', 'output_tokens', 'cost'],
    2: ['calls', 'input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens', 'cost'],
}


def _judgment_groups(record, nodes):
    groups = {}
    seen = set()
    version = record['schema_version']
    for judgment in record['judgments']:
        exact_fields(judgment, JUDGMENT_FIELDS + (['attempt', 'confidence'] if version == 2 else []), 'judgment')
        if version == 2:
            require(type(judgment['attempt']) is int and 1 <= judgment['attempt'] <= 8, 'Invalid judgment attempt')
            require(judgment['confidence'] in ('low', 'medium', 'high'), 'Invalid judgment confidence')
        node_id = judgment['node_id']
        require(node_id in nodes and nodes[node_id]['kind'] == 'judgment', 'Judgment names a non-judgment node')
        node = nodes[node_id]
        require(
            type(judgment['sample']) is int and 1 <= judgment['sample'] <= node['repetitions'],
            'Judgment sample is outside the declared repetition range',
        )
        require(
            (node_id, judgment['sample']) not in seen,
            'Duplicate judgment sample: ' + node_id + '/' + str(judgment['sample']),
        )
        seen.add((node_id, judgment['sample']))
        require(judgment['outcome'] in node['outcomes'], 'Judgment uses an undeclared outcome')
        bounded_text(judgment['verdict'], 'judgment verdict', 400)
        span = judgment['seed_span']
        require(
            isinstance(span, list)
            and len(span) == 2
            and all(type(value) is int for value in span)
            and 1 <= span[0] <= span[1],
            'Judgment needs a positive seed span',
        )
        citations(judgment['citations'], 'judgment.citations', minimum=1, quoted=version == 1)
        citations(judgment['counter_citations'], 'judgment.counter_citations', quoted=version == 1)
        bounded_text(judgment['rationale'], 'judgment rationale')
        digest(judgment['answer_sha256'], 'judgment.answer_sha256')
        digest(judgment['receipt_sha256'], 'judgment.receipt_sha256')
        groups.setdefault(node_id, []).append(judgment)
    for node_id, node in nodes.items():
        if node['kind'] == 'judgment':
            samples = groups.get(node_id, [])
            require(len(samples) == node['repetitions'], 'Judgment repetitions are incomplete: ' + node_id)
            require(
                {item['sample'] for item in samples} == set(range(1, node['repetitions'] + 1)),
                'Judgment sample numbering is incomplete: ' + node_id,
            )
    return groups


def validate_evaluation(record, *, extraction, extraction_sha256, policy, scoring_policy_sha256, source_texts=None):
    exact_fields(
        record,
        [
            'schema_version',
            'evaluation_id',
            'run_id',
            'identities',
            'gateway',
            'judgments',
            'aggregations',
            'outcomes',
            'presentation',
            'usage',
        ],
        'evaluation record',
    )
    require(record['schema_version'] in (1, 2), 'Unsupported evaluation schema')
    quoted = record['schema_version'] == 1
    safe_id(record['evaluation_id'], 'evaluation_id')
    safe_id(record['run_id'], 'run_id')
    validate_extraction(extraction, source_texts=source_texts)
    nodes, outgoing, incoming = validate_policy(policy)
    require(record['run_id'] == extraction['run_id'], 'Evaluation and extraction run IDs differ')
    identities = record['identities']
    exact_fields(
        identities,
        [
            'prompt_sha256',
            'corpus_sha256',
            'candidate_sha256',
            'rubric_sha256',
            'extraction_sha256',
            'scoring_policy_sha256',
        ],
        'evaluation identities',
    )
    for key, value in identities.items():
        digest(value, 'evaluation.' + key)
    for key in ['prompt_sha256', 'corpus_sha256', 'candidate_sha256']:
        require(identities[key] == extraction['identities'][key], 'Evaluation and extraction identities differ: ' + key)
    require(
        identities['extraction_sha256'] == digest(extraction_sha256, 'extraction_sha256'),
        'Evaluation names a different extraction record',
    )
    require(
        identities['scoring_policy_sha256'] == digest(scoring_policy_sha256, 'scoring_policy_sha256'),
        'Evaluation names a different scoring policy',
    )
    gateway_record(record['gateway'], 'gateway', name_field='name')
    groups = _judgment_groups(record, nodes)
    if source_texts is not None:
        for judgment in record['judgments']:
            citations(judgment['citations'], 'judgment.citations', sources=source_texts, minimum=1, quoted=quoted)
            citations(judgment['counter_citations'], 'judgment.counter_citations', sources=source_texts, quoted=quoted)
    require(
        isinstance(record['aggregations'], list) and 1 <= len(record['aggregations']) <= 256,
        'Evaluation aggregations must be a bounded non-empty list',
    )
    aggregate_results = {}
    for aggregate in record['aggregations']:
        exact_fields(
            aggregate,
            ['node_id', 'strategy', 'sample_count', 'outcome', 'agreement', 'distinct_outcomes'],
            'aggregation',
        )
        node_id = aggregate['node_id']
        require(node_id in nodes and nodes[node_id]['kind'] == 'aggregate', 'Aggregation names a non-aggregate node')
        node = nodes[node_id]
        require(node_id not in aggregate_results, 'Duplicate aggregation result: ' + node_id)
        aggregate_results[node_id] = aggregate
        require(aggregate['strategy'] == node['strategy'], 'Aggregation strategy differs from policy')
        require(aggregate['outcome'] in node['outcomes'], 'Aggregation uses an undeclared outcome')
        require(
            type(aggregate['sample_count']) is int and aggregate['sample_count'] >= 1,
            'Invalid aggregation sample count',
        )
        require(
            type(aggregate['agreement']) in [int, float] and 0 <= aggregate['agreement'] <= 1,
            'Aggregation agreement must be between 0 and 1',
        )
        distinct = aggregate['distinct_outcomes']
        require(
            isinstance(distinct, list) and 1 <= len(distinct) <= 8 and len(set(distinct)) == len(distinct),
            'Aggregation distinct outcomes must be unique',
        )
        parents = {edge['from'] for edge in incoming.get(node_id, [])}
        judgment_parents = [parent for parent in parents if nodes[parent]['kind'] == 'judgment']
        require(len(judgment_parents) == 1, 'Aggregation must follow exactly one judgment: ' + node_id)
        samples = sorted(groups[judgment_parents[0]], key=lambda item: item['sample'])
        # Recompute the aggregate rather than trusting the stored result.
        expected = aggregate_samples([sample['outcome'] for sample in samples], node)
        require(aggregate == expected, 'Aggregation differs from its judgments: ' + node_id)
    aggregate_nodes = {node_id for node_id, node in nodes.items() if node['kind'] == 'aggregate'}
    require(
        set(aggregate_results) == aggregate_nodes,
        'Evaluation must contain exactly one aggregation per policy aggregate',
    )
    require(
        isinstance(record['outcomes'], list) and 1 <= len(record['outcomes']) <= 256,
        'Evaluation outcomes must be a bounded non-empty list',
    )
    outcome_ids = set()
    for outcome in record['outcomes']:
        exact_fields(outcome, ['node_id', 'status', 'score', 'rationale'], 'evaluation outcome')
        node_id = outcome['node_id']
        require(node_id in nodes and nodes[node_id]['kind'] == 'terminal', 'Outcome names a non-terminal node')
        require(node_id not in outcome_ids, 'Duplicate terminal outcome: ' + node_id)
        outcome_ids.add(node_id)
        result = nodes[node_id]['result']
        require(outcome['status'] == result['status'], 'Outcome status differs from policy terminal')
        require(outcome['score'] == result['score'], 'Outcome score differs from policy terminal')
        bounded_text(outcome['rationale'], 'outcome rationale')
    selected_terminals = {
        terminal_for(outgoing, nodes, aggregate_id, aggregate_results[aggregate_id]['outcome'])['id']
        for aggregate_id in aggregate_nodes
    }
    require(
        outcome_ids == selected_terminals, 'Evaluation outcomes must match the terminal selected by every aggregate'
    )
    presentation = record['presentation']
    exact_fields(presentation, ['label', 'title', 'summary', 'findings'], 'presentation')
    for key in ['label', 'title', 'summary']:
        bounded_text(presentation[key], 'presentation.' + key)
    require(
        isinstance(presentation['findings'], list) and len(presentation['findings']) <= 256,
        'Presentation findings must be a bounded list',
    )
    for finding in presentation['findings']:
        bounded_text(finding, 'presentation finding', 8000)
    usage = record['usage']
    exact_fields(usage, USAGE_FIELDS[record['schema_version']], 'usage')
    require(type(usage['calls']) is int and usage['calls'] >= 0, 'Usage calls must be non-negative')
    for key in USAGE_FIELDS[record['schema_version']][1:-1]:
        require(usage[key] is None or type(usage[key]) is int and usage[key] >= 0, 'Invalid usage.' + key)
    require(usage['cost'] is None or isinstance(usage['cost'], dict), 'Usage cost must be an object or null')
    inspect_metadata(record, 'evaluation record')
    return {
        'judgment_samples': sum(len(items) for items in groups.values()),
        'aggregations': len(aggregate_results),
        'outcomes': len(outcome_ids),
    }


# A stance is how the seed asks to be read: its frame, turn, and what it leaves open (prompt version 5).
SEED_KINDS = {'role', 'relation', 'condition', 'conjecture', 'stance'}
# Seed-reading bounds are shared by contract validation and the scoring pipeline:
# every reading needs 6 to 20 elements so a stance can be recorded without
# turning the seed into an inventory. The pipeline enforces the same interval.
SEED_ELEMENT_MIN = 6
SEED_ELEMENT_MAX = 20
SEED_ALTERNATIVES_MAX = 10
CARRIAGE_STATUSES = {'carried', 'transformed', 'lost', 'introduced'}
STANDING_KINDS = {'person', 'intelligence', 'successor', 'material', 'other'}
GESTURES = {'question', 'constraint', 'invariant', 'boundary', 'rehearsal', 'receipt', 'accept', 'withdraw'}


def validate_seed_reading(reading, *, seed_line_count=None):
    """A reader's own decomposition of the seed: roles, relations, conditions and conjectures."""
    exact_fields(reading, ['elements', 'alternatives'], 'seed reading')
    elements = reading['elements']
    require(
        isinstance(elements, list) and SEED_ELEMENT_MIN <= len(elements) <= SEED_ELEMENT_MAX,
        'Seed reading needs ' + str(SEED_ELEMENT_MIN) + ' to ' + str(SEED_ELEMENT_MAX) + ' elements',
    )
    ids = set()
    for element in elements:
        exact_fields(element, ['id', 'kind', 'statement', 'seed_span', 'ambiguity'], 'seed element')
        safe_id(element['id'], 'seed element id')
        require(element['id'] not in ids, 'Duplicate seed element: ' + element['id'])
        ids.add(element['id'])
        require(element['kind'] in SEED_KINDS, 'Seed element kind must be one of: ' + ', '.join(sorted(SEED_KINDS)))
        bounded_text(element['statement'], 'seed element statement', 2000)
        span = element['seed_span']
        require(
            isinstance(span, list)
            and len(span) == 2
            and all(type(value) is int for value in span)
            and 1 <= span[0] <= span[1]
            and (seed_line_count is None or span[1] <= seed_line_count),
            'Seed element needs a seed line range inside the seed: ' + element['id'],
        )
        require(
            element['ambiguity'] is None or isinstance(element['ambiguity'], str) and len(element['ambiguity']) <= 2000,
            'Invalid seed element ambiguity',
        )
    require(
        isinstance(reading['alternatives'], list) and len(reading['alternatives']) <= SEED_ALTERNATIVES_MAX,
        'Seed alternatives must be a list of at most ' + str(SEED_ALTERNATIVES_MAX) + ' readings',
    )
    for alternative in reading['alternatives']:
        bounded_text(alternative, 'seed alternative', 2000)
    return ids


def validate_carriage(entries, element_ids, *, sources=None):
    """Every seed element is carried, transformed or lost exactly once; introductions name no element."""
    require(isinstance(entries, list) and 1 <= len(entries) <= 60, 'Carriage must be a bounded non-empty list')
    seen = set()
    for entry in entries:
        exact_fields(entry, ['element', 'status', 'note', 'citations'], 'carriage entry')
        require(
            entry['status'] in CARRIAGE_STATUSES,
            'Carriage status must be one of: ' + ', '.join(sorted(CARRIAGE_STATUSES)),
        )
        if entry['status'] == 'introduced':
            require(entry['element'] is None, 'Introduced carriage entries name no seed element')
        else:
            require(entry['element'] in element_ids, 'Carriage names an unknown seed element: ' + str(entry['element']))
            require(entry['element'] not in seen, 'Seed element appears twice in the carriage: ' + entry['element'])
            seen.add(entry['element'])
        bounded_text(entry['note'], 'carriage note', 2000)
        citations(
            entry['citations'],
            'carriage citations',
            sources=sources,
            maximum=6,
            quoted=False,
            minimum=0 if entry['status'] == 'lost' else 1,
        )
    require(
        seen == set(element_ids),
        'Carriage must account for every seed element: missing ' + ', '.join(sorted(set(element_ids) - seen)),
    )


def validate_standing(entries, *, sources=None):
    """Who can do what in the artefact: each represented participant and the gestures it can actually perform."""
    require(isinstance(entries, list) and 1 <= len(entries) <= 8, 'Standing needs 1 to 8 participants')
    ids = set()
    for entry in entries:
        exact_fields(entry, ['participant', 'kind', 'gestures', 'note', 'citations'], 'standing entry')
        safe_id(entry['participant'], 'standing participant')
        require(entry['participant'] not in ids, 'Duplicate standing participant: ' + entry['participant'])
        ids.add(entry['participant'])
        require(entry['kind'] in STANDING_KINDS, 'Standing kind must be one of: ' + ', '.join(sorted(STANDING_KINDS)))
        require(
            isinstance(entry['gestures'], list)
            and len(set(entry['gestures'])) == len(entry['gestures'])
            and set(entry['gestures']) <= GESTURES,
            'Standing gestures must be distinct names from: ' + ', '.join(sorted(GESTURES)),
        )
        bounded_text(entry['note'], 'standing note', 2000)
        citations(entry['citations'], 'standing citations', sources=sources, maximum=6, quoted=False, minimum=1)


def validate_carriage_record(record, *, source_texts=None, seed_line_count=None):
    exact_fields(record, ['schema_version', 'evaluation_id', 'run_id', 'readings'], 'carriage record')
    # Schema 2 (prompt version 5) adds each reading's stance elements and its standing map.
    require(record['schema_version'] in (1, 2), 'Unsupported carriage schema')
    safe_id(record['evaluation_id'], 'carriage evaluation_id')
    safe_id(record['run_id'], 'carriage run_id')
    require(isinstance(record['readings'], list) and record['readings'], 'Carriage record needs readings')
    for reading in record['readings']:
        exact_fields(
            reading,
            ['sample', 'answer_sha256', 'seed_reading', 'carriage']
            + (['standing'] if record['schema_version'] == 2 else []),
            'carriage reading',
        )
        require(type(reading['sample']) is int and reading['sample'] >= 1, 'Invalid carriage sample')
        digest(reading['answer_sha256'], 'carriage answer_sha256')
        ids = validate_seed_reading(reading['seed_reading'], seed_line_count=seed_line_count)
        validate_carriage(reading['carriage'], ids, sources=source_texts)
        if record['schema_version'] == 2:
            require(
                any(element['kind'] == 'stance' for element in reading['seed_reading']['elements']),
                'A schema-2 seed reading names at least one stance',
            )
            validate_standing(reading['standing'], sources=source_texts)
    inspect_metadata(record, 'carriage record')
    return record


def validate_rubric_conformance(policy, rubric):
    """Raise unless the scoring policy encodes the current rubric's axes, anchors and scale.

    Review projection and evaluation admission call this, so a stale policy cannot produce a review.
    The rule itself is :func:`rubric_policy.validate_conformance`, shared with atlas.
    """
    import rubric_policy

    rubric_policy.validate_conformance(policy, rubric)


def collect_alternatives(readings):
    """Collect distinct seed alternative readings across carriage readings.

    Order is preserved and duplicates are removed so normalized presentation and
    report structures can preserve every supported alternative without repetition.
    """
    seen, ordered = set(), []
    for reading in readings or []:
        seed_reading = reading.get('seed_reading', {}) if isinstance(reading, dict) else {}
        for alternative in seed_reading.get('alternatives', []) or []:
            if alternative not in seen:
                seen.add(alternative)
                ordered.append(alternative)
    return ordered


CONFIDENCE_ORDER = ['low', 'medium', 'high']


def agreement_confidence(agreement):
    return 'high' if agreement >= 0.99 else 'medium' if agreement >= 0.8 else 'low'


def review_projection(evaluation, policy, rubric, source_texts):
    """Project an evaluation onto per-axis review scores; admission and package validation share this rule.

    The representative is the shortest verdict among samples that chose the
    aggregate outcome. Schema 2 keeps the reader's own confidence, lowered when
    independent sessions disagree, and unresolved axes carry no citations.
    """
    nodes, outgoing, _ = validate_policy(policy)
    validate_rubric_conformance(policy, rubric)
    groups = {}
    for judgment in evaluation['judgments']:
        groups.setdefault(judgment['node_id'], []).append(judgment)
    aggregates = {item['node_id']: item for item in evaluation['aggregations']}
    outcomes = {item['node_id']: item for item in evaluation['outcomes']}
    require(set(groups) == {axis['id'] for axis in rubric['axes']}, 'Evaluation must contain every rubric axis exactly')
    version = evaluation['schema_version']
    scores, axis_outcomes = {}, {}
    for axis in rubric['axes']:
        axis_id = axis['id']
        targets = {edge['to'] for edge in outgoing.get(axis_id, []) if nodes[edge['to']]['kind'] == 'aggregate'}
        require(len(targets) == 1, 'Evaluation axis does not feed exactly one aggregate: ' + axis_id)
        aggregate = aggregates.get(next(iter(targets)))
        require(aggregate is not None, 'Evaluation is missing the axis aggregate: ' + axis_id)
        terminal = outcomes.get(terminal_for(outgoing, nodes, aggregate['node_id'], aggregate['outcome'])['id'])
        require(terminal is not None, 'Evaluation is missing the selected axis outcome: ' + axis_id)
        samples = groups[axis_id]
        matching = [item for item in samples if item['outcome'] == aggregate['outcome']] or samples
        representative = min(matching, key=lambda item: (len(item['verdict']), item['sample']))
        value = terminal['score']
        citations = [
            citation if version == 1 else quote_citation(citation, source_texts)
            for citation in representative['citations']
        ]
        if value is None:
            rationale, confidence = terminal['rationale'], 'unassessed'
            if version > 1:
                citations = []
        else:
            rationale = representative['verdict'].strip()
            if aggregate['agreement'] < 1:
                rationale += (
                    ' Agreement: '
                    + str(aggregate['sample_count'])
                    + ' samples, '
                    + str(round(aggregate['agreement'], 3))
                    + '.'
                )
            confidence = agreement_confidence(aggregate['agreement'])
            if version > 1:
                confidence = min(confidence, representative['confidence'], key=CONFIDENCE_ORDER.index)
        scores[axis_id] = {
            'value': value,
            'confidence': confidence,
            'rationale': rationale[:16000],
            'citations': [
                {
                    'path': citation['source_path'],
                    'start': citation['start'],
                    'end': citation['end'],
                    'quote': citation['quote'],
                }
                for citation in citations
            ],
            'seed_span': representative['seed_span'],
        }
        axis_outcomes[axis_id] = {
            'aggregation': aggregate['node_id'],
            'outcome': aggregate['outcome'],
            'terminal': terminal['node_id'],
        }
    return scores, axis_outcomes


def validate_directory(
    root, *, evaluation_name='evaluation.json', extraction_name='extraction.json', policy_name='scoring-policy.json'
):
    root = Path(root).resolve()
    extraction_bytes = (root / extraction_name).read_bytes()
    policy_bytes = (root / policy_name).read_bytes()
    evaluation_bytes = (root / evaluation_name).read_bytes()
    extraction = load_json(root / extraction_name)
    policy = load_json(root / policy_name)
    evaluation = load_json(root / evaluation_name)
    source_texts = {}
    for source in extraction.get('sources', []):
        path = safe_path(source.get('path'), 'source path')
        resolved = (root / str(path)).resolve()
        require(
            resolved.is_relative_to(root) and resolved.is_file(),
            'Source record is missing or escapes the contract directory',
        )
        source_texts[str(path)] = resolved.read_text(encoding='utf-8')
    result = validate_evaluation(
        evaluation,
        extraction=extraction,
        extraction_sha256=sha(extraction_bytes),
        policy=policy,
        scoring_policy_sha256=sha(policy_bytes),
        source_texts=source_texts,
    )
    result.update(
        extraction_sha256=sha(extraction_bytes),
        scoring_policy_sha256=sha(policy_bytes),
        evaluation_sha256=sha(evaluation_bytes),
        record_sha256=sha(canonical(evaluation)),
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    try:
        result = validate_directory(args.directory)
    except (ValueError, OSError, KeyError, TypeError) as error:
        print('Contract validation stopped: ' + str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
