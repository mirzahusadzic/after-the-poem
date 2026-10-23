#!/usr/bin/env python3
"""Generate an executable scoring policy directly from a rubric."""

import json
from pathlib import Path

from bundles import require


def build(rubric, *, repetitions=1, minimum_agreement=0.67):
    """One judgment per axis per session; more repetitions add independent sessions and a quorum."""
    require(isinstance(rubric, dict), 'A rubric object is required')
    require(isinstance(rubric.get('version'), str) and rubric['version'], 'Rubric version is required')
    require(isinstance(rubric.get('title'), str) and rubric['title'], 'Rubric title is required')
    scale = rubric.get('scale')
    axes = rubric.get('axes')
    require(isinstance(scale, list) and len(scale) >= 2, 'Rubric scale must contain at least two values')
    require(scale == sorted(set(scale)), 'Rubric scale must be unique and ascending')
    require(
        all(type(value) in [int, float] and not isinstance(value, bool) for value in scale),
        'Rubric scale values must be numeric',
    )
    require(isinstance(axes, list) and axes, 'Rubric axes are required')
    require(type(repetitions) is int and 1 <= repetitions <= 20, 'Repetitions must be 1-20')
    require(
        type(minimum_agreement) in [int, float] and 0 < minimum_agreement <= 1, 'Minimum agreement must be in (0, 1]'
    )
    roots = ['collect-evidence']
    nodes = [
        {
            'id': 'collect-evidence',
            'kind': 'task',
            'instruction': 'Read the seed and retained sources once. Judge each rubric axis against its own anchors, citing exact spans and the prescribed seed span.',
        }
    ]
    edges = []
    seen = {root for root in roots}
    for axis in axes:
        axis_id = axis.get('id')
        require(isinstance(axis_id, str) and axis_id, 'Every rubric axis needs an ID')
        require(axis_id not in seen, 'Rubric axis IDs must be unique and cannot collide with reserved nodes')
        seen.add(axis_id)
        anchors = axis.get('anchors')
        require(isinstance(anchors, list) and len(anchors) == len(scale), 'Every axis needs one anchor per scale value')
        outcomes = []
        terminals = []
        for value, anchor in zip(scale, anchors):
            label = ('negative-' if value < 0 else '') + str(abs(value)).replace('.', '-')
            outcome = 'level-' + label
            terminal = axis_id + '-' + outcome
            aggregate_outcome = outcome
            require(terminal not in seen, 'Generated terminal ID collision: ' + terminal)
            seen.add(terminal)
            outcomes.append(outcome)
            terminals.append(
                {
                    'id': terminal,
                    'kind': 'terminal',
                    'result': {'status': 'scored', 'score': value, 'label': anchor},
                }
            )
        unresolved = axis_id + '-unresolved'
        split = axis_id + '-split'
        consensus = axis_id + '-consensus'
        for suffix in [unresolved, split, consensus]:
            require(suffix not in seen, 'Generated node ID collision: ' + suffix)
            seen.add(suffix)
        judgment_outcomes = outcomes + ['unresolved']
        aggregate_outcomes = outcomes + ['unresolved', 'split']
        nodes.append(
            {
                'id': axis_id,
                'kind': 'judgment',
                'criterion': axis['label'],
                'question': axis['question'],
                'anchors': anchors,
                'outcomes': judgment_outcomes,
                'repetitions': repetitions,
            }
        )
        nodes.append(
            {
                'id': consensus,
                'kind': 'aggregate',
                'strategy': 'quorum',
                'minimum_agreement': minimum_agreement,
                'outcomes': aggregate_outcomes,
            }
        )
        nodes.extend(terminals)
        nodes.extend(
            [
                {
                    'id': unresolved,
                    'kind': 'terminal',
                    'result': {
                        'status': 'review',
                        'score': None,
                        'label': axis['label'] + ': the judgment returned unresolved evidence.',
                    },
                },
                {
                    'id': split,
                    'kind': 'terminal',
                    'result': {
                        'status': 'unresolved',
                        'score': None,
                        'label': axis['label'] + ': independent sessions disagree; no score is awarded.',
                    },
                },
            ]
        )
        edges.append({'from': 'collect-evidence', 'to': axis_id, 'when': None})
        for outcome in judgment_outcomes:
            edges.append({'from': axis_id, 'to': consensus, 'when': outcome})
        for outcome in outcomes:
            edges.append({'from': consensus, 'to': axis_id + '-' + outcome, 'when': outcome})
        edges.append({'from': consensus, 'to': unresolved, 'when': 'unresolved'})
        edges.append({'from': consensus, 'to': split, 'when': 'split'})
    return {
        'schema_version': 1,
        'policy_id': 'rubric-' + rubric['version'].replace('.', '-') + '-session-profile',
        'version': '2.0.0',
        'description': (
            'Rubric judgments generated from '
            + rubric['title']
            + ' v'
            + rubric['version']
            + ' · '
            + str(repetitions)
            + ' session(s).'
        ),
        'roots': roots,
        'nodes': nodes,
        'edges': edges,
    }


def write(policy, path):
    path = Path(path)
    require(not path.exists(), 'Policy output already exists; use a new path')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(policy, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return path


def matches_rubric(policy, rubric):
    """True when every judgment node encodes the current rubric axis, anchors and scale."""
    judgments = {node['id']: node for node in policy['nodes'] if node['kind'] == 'judgment'}
    terminals = {node['id']: node for node in policy['nodes'] if node['kind'] == 'terminal'}
    if set(judgments) != {axis['id'] for axis in rubric['axes']}:
        return False
    for axis in rubric['axes']:
        node = judgments[axis['id']]
        if (node['criterion'], node['question'], node.get('anchors')) != (
            axis['label'],
            axis['question'],
            axis['anchors'],
        ):
            return False
        for value, anchor in zip(rubric['scale'], axis['anchors']):
            label = ('negative-' if value < 0 else '') + str(abs(value)).replace('.', '-')
            terminal = terminals.get(axis['id'] + '-level-' + label)
            if terminal is None or terminal['result'] != {'status': 'scored', 'score': value, 'label': anchor}:
                return False
        # No extra scored terminals beyond the rubric scale: the policy must not invent levels.
        scored = [
            node_id
            for node_id, terminal in terminals.items()
            if node_id.startswith(axis['id'] + '-level-') and terminal['result']['status'] == 'scored'
        ]
        if len(scored) != len(rubric['scale']):
            return False
        if len(axis['anchors']) != len(rubric['scale']):
            return False
    return True


def validate_conformance(policy, rubric):
    """Raise unless the policy encodes the rubric: the raising form of :func:`matches_rubric`."""
    require(matches_rubric(policy, rubric), 'Scoring policy does not encode the current rubric')
