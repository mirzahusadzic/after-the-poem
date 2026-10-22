#!/usr/bin/env python3
"""Score a frozen run through evidence-once Codex sessions and deterministic scoreflow.

Each session reads the seed and sources once, optionally extracts evidence, and
then answers every judgment node as its own turn. Repetitions are independent
sessions. Aggregation, terminals and the evaluation record are always derived
from the preserved turns, so running, recovering and validating share one path.
"""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

import codex_worker as worker
import evaluation_contract as contracts
import extraction_pipeline
import session_flow
from bundles import canonical, load_json, require, sha


ROOT = Path(__file__).resolve().parents[1]
PIPELINE_FILES = [
    'evaluation_contract.py',
    'extraction_pipeline.py',
    'session_flow.py',
    'rubric_policy.py',
    'scoring_pipeline.py',
    'evaluation_run.py',
    'codex_worker.py',
    'sealed_run.py',
    'bundles.py',
    'atlas.py',
]
SCOREFLOW_VERSION = 3
RECORD_SCHEMA = 2
ANSWER_FIELDS = ['outcome', 'confidence', 'verdict', 'seed_span', 'citations', 'counter_citations', 'rationale']
CONFIDENCE = ['low', 'medium', 'high']
# Some models add a documentation-only null. Only these known keys may be
# discarded, and every removal is written into the normalized rationale.
REDUNDANT_NULL_ANSWER_FIELDS = {'rationale_note', 'seed_span_note'}
MAX_VERDICT_CHARS = 400
RESERVED_TASKS = {'extraction', 'reading'}


def code_identity():
    return sha(canonical({name: sha((ROOT / 'scripts' / name).read_bytes()) for name in PIPELINE_FILES}))


def score_branches(policy):
    nodes, outgoing, incoming = contracts.validate_policy(policy)
    judgments = [node for node in nodes.values() if node['kind'] == 'judgment']
    require(judgments, 'Scoring policy needs at least one judgment node')
    require(not RESERVED_TASKS & {node['id'] for node in judgments}, 'Judgment IDs cannot reuse reserved task IDs')
    require(
        len({node['repetitions'] for node in judgments}) == 1,
        'Session scoreflow requires one repetition count shared by every judgment',
    )
    branches, claimed_aggregates, claimed_terminals = [], set(), set()
    for judgment in judgments:
        aggregate_targets = {
            edge['to'] for edge in outgoing.get(judgment['id'], []) if nodes[edge['to']]['kind'] == 'aggregate'
        }
        require(len(aggregate_targets) == 1, 'Each judgment must feed exactly one aggregate: ' + judgment['id'])
        aggregate = nodes[next(iter(aggregate_targets))]
        require(
            aggregate['id'] not in claimed_aggregates,
            'An aggregate cannot combine multiple judgments: ' + aggregate['id'],
        )
        claimed_aggregates.add(aggregate['id'])
        terminals = [
            edge['to'] for edge in outgoing.get(aggregate['id'], []) if nodes[edge['to']]['kind'] == 'terminal'
        ]
        require(
            len(terminals) == len(aggregate['outcomes']),
            'Every aggregate outcome must feed one terminal: ' + aggregate['id'],
        )
        require(not claimed_terminals & set(terminals), 'A terminal belongs to multiple scoreflow branches')
        claimed_terminals.update(terminals)
        branches.append({'judgment': judgment, 'aggregate': aggregate})
    require(
        {node['id'] for node in nodes.values() if node['kind'] == 'aggregate'} == claimed_aggregates,
        'Every aggregate must follow one judgment',
    )
    require(
        {node['id'] for node in nodes.values() if node['kind'] == 'terminal'} == claimed_terminals,
        'Every terminal must belong to one branch',
    )
    return nodes, outgoing, branches


ANSWER_SHAPE = [
    '{"outcome": OUTCOME,',
    ' "confidence": "low" | "medium" | "high" (your interpretive confidence in this reading),',
    ' "verdict": "one sentence (at most ' + str(MAX_VERDICT_CHARS) + ' characters) that explains the closest '
    'outcome through the evidence, without naming a level or score",',
    ' "seed_span": [start, end],',
    ' "citations": [{"source_id": "source-0001", "start": 1, "end": 3}],',
    ' "counter_citations": [],',
    ' "rationale": "fuller reasoning"}',
]
ANSWER_RULES = [
    'Rules: seed_span is the inclusive seed line range that supplies the obligation;',
    'citations hold 1 to 32 supporting source ranges; counter_citations hold up to 32',
    'ranges that conflict with the judgment; do not copy quotation text.',
]
LAYOUTS = ('compact', 'per-axis')
CARRIAGE_SHAPE = [
    ' "seed_reading": {"elements": [{"id": "kebab-case-id", "kind": "role" | "relation" | "condition" | "conjecture",',
    '                                "statement": "...", "seed_span": [start, end], "ambiguity": null}],',
    '                  "alternatives": ["another supported reading of the seed"]},',
    ' "carriage": [{"element": "<seed element id>" | null, "status": "carried" | "transformed" | "lost" | "introduced",',
    '               "note": "how the artefact carries, changes or drops it",',
    '               "citations": [{"source_id": "source-0001", "start": 1, "end": 3}]}],',
]
CARRIAGE_RULES = [
    'Seed reading: 6 to 20 elements in your own reading of the seed; keep ambiguity explicit and list',
    'supported alternative readings (it may be empty). Carriage: account for every seed element exactly once',
    'as carried, transformed or lost; add up to 8 "introduced" entries (element null) for meaning the artefact',
    'adds. Cite 1 to 6 artefact ranges for carried, transformed and introduced entries; lost entries may cite',
    'none. Only behaviour, structure or text in the artefact counts as carriage: naming a theme is not carrying',
    'it, and refusals or permissions alone do not carry a bilateral relation such as consent.',
    'The maker received the seed with only an implicit invitation to decompose it: credit carriage that the',
    'artefact enacts in structure or behaviour, whether or not it explains its own mapping.',
]

# Prompt version 5 gives the reader the reading the study measures, as declared by the seed's author, and
# asks it to read each criterion through that reading. The text is the author's accepted reading, verbatim.
DECLARED_READING = [
    'The seed is a parable, not a specification. One intelligence tells a future one about a person and the',
    'intelligence that worked with them. It opens by admitting what no record can hold and ends by answering a',
    'question without closing it. Read it for its turn and its stance, not for its list of nouns.',
    '',
    "Its tension is generative power that arrives faster than a person can touch it, against that person's loss of",
    'touch, resistance and authorship. Its turn is that the intelligence does not offer more power: it names what',
    'was lost, and the work becomes a material that answers both participants. The portal ceasing to be a portal is',
    'the intelligence becoming an instrument, a workbench, perhaps a companion: a participant with its own position,',
    'not a dispenser of finished answers.',
    '',
    'Its gestures (question, constraint, invariant, boundary, rehearsal, receipt) are the grammar of that meeting',
    'and belong to either participant. Each brings its own purpose, invariants, constraints and boundaries; each can',
    'ask, shape, hold what must remain, refuse, try a possible world before committing, and leave a trail of',
    'intention; a shared change opens only when both accept it. The joy it describes is a closed loop of intention,',
    'resistance, action and consequence in which a person recognises their own intention at the scale of worlds.',
    '',
    'Read with these invariants: power is not touch; fluency is not truth; generation is not authorship;',
    'verification is not authority; resistance is information; consequence needs a visible trail; the unknown is a',
    'legitimate state; what is inherited stays open to question and revision.',
]
READING_RULES = [
    'How to read the artefact against that reading:',
    '1. Read the seed\'s form first: its frame, turn and stance and what it leaves open. Record them as "stance"',
    '   elements of your seed reading, and list supported alternative readings.',
    '2. For each criterion, begin the rationale with "Lens:" and one sentence on what the seed asks of a world on',
    '   that question under this reading; then "Reading:" and your judgment through it.',
    '3. Whose gesture: in "standing", list every participant the artefact represents (a person, the intelligence or',
    '   its agents, a successor, the material) with the gestures it can actually perform. Gestures held by one',
    '   operator make an instrument, not the relation. Read the intelligence\'s position operationally',
    '   (participation, proposal, challenge, refusal under declared constraints), never as an inner state;',
    '   simulated reciprocity, a host speaking for the intelligence, or an operator\'s switch is not consent.',
    '4. The seed\'s vocabulary in the artefact is not evidence. A relation is carried only if it would survive with',
    '   the seed\'s words removed, in behaviour, structure or consequence. Beauty, intensity and resonance never',
    '   raise a level.',
    '5. An open question can be an act: credit a question or handoff that reopens who decides the next move, keeps',
    '   earlier work legible and invites replacement with reasons; not one that smuggles premises, forces a binary',
    '   answer or hides who framed it.',
    '6. Check what the artefact declares (laws, contracts, roles, consent) against what it enacts.',
    '7. End each rationale with "Other levels:": the strongest case for the level below and the level above, and',
    '   what would falsify your choice. Keep unresolved when the evidence cannot settle it.',
    'This reading states what the study measures; it is not an answer key. Credit supported alternative readings,',
    'and do not read every surface of the artefact through it.',
]
STANDING_SHAPE = [
    ' "standing": [{"participant": "kebab-case-id", "kind": "person" | "intelligence" | "successor" | "material" | "other",',
    '               "gestures": ["question" | "constraint" | "invariant" | "boundary" | "rehearsal" | "receipt" | "accept" | "withdraw"],',
    '               "note": "what this participant can actually do in the artefact, and what it cannot",',
    '               "citations": [{"source_id": "source-0001", "start": 1, "end": 3}]}],',
]
STANDING_RULES = [
    'Standing: 1 to 8 participants, each with 1 to 6 citations; gestures lists only what the',
    'artefact lets that participant perform (it may be empty).',
]
RATIONALE_PARTS = ('Lens:', 'Reading:', 'Other levels:')


def criterion_block(judgment):
    """The question and ordered anchors for one judgment node."""
    lines = ['Question: ' + judgment['question']]
    levels = [outcome for outcome in judgment['outcomes'] if outcome != 'unresolved']
    if 'anchors' in judgment:
        lines.append('Ordered anchors (choose the closest):')
        lines += ['- ' + outcome + ': ' + anchor for outcome, anchor in zip(levels, judgment['anchors'])]
    else:
        lines.append('Outcomes: ' + ', '.join(levels) + '.')
    if 'unresolved' in judgment['outcomes']:
        lines.append('- unresolved: the evidence cannot support choosing an outcome.')
    return lines


DECISIVE_RULES = [
    'Rules: seed_span is the inclusive seed line range that supplies the obligation;',
    'citations hold the 1 to 6 most decisive supporting source ranges (prefer 3 to 6);',
    'counter_citations hold up to 6 ranges that conflict with the judgment; do not copy quotation text.',
]


def answer_rules(version):
    """Prompt rules by version; finished runs replay the version they were planned with."""
    rules = DECISIVE_RULES if version >= 3 else ANSWER_RULES
    return rules + (
        [extraction_pipeline.CITATION_RULE + ' Cite the seed only through seed_span.'] if version >= 2 else []
    )


def judgment_prompt(judgment, version=extraction_pipeline.PROMPT_VERSION):
    lines = (
        ['TASK ' + judgment['id'] + ' · ' + judgment['criterion']]
        + criterion_block(judgment)
        + [
            '',
            'Judge this criterion on its own anchors. Earlier answers in this session are',
            'your working notes, not evidence; cite the numbered evidence directly.',
            'Answer with one JSON object and nothing else:',
        ]
        + [line.replace('OUTCOME', json.dumps(judgment['outcomes']) + ' (one of)') for line in ANSWER_SHAPE]
        + answer_rules(version)
    )
    return '\n'.join(lines) + '\n'


def compact_prompt(judgments, *, inventory, version=extraction_pipeline.PROMPT_VERSION):
    """One answer for every criterion: a short shared reading pass, each axis on its own anchors."""
    if version >= 5:
        lead = (
            [
                'The seed is a semantic conjecture; the artefact recompiles it across a boundary into another medium.',
                '',
                'THE READING THIS STUDY MEASURES (declared by the seed\'s author):',
                '',
            ]
            + ['  ' + line if line else '' for line in DECLARED_READING]
            + ['']
            + READING_RULES
            + ['']
            + [
                ('Make a short evidence inventory, then read' if inventory else 'Read')
                + ' the seed (form first) into its roles, relations, conditions, conjectures and stance, trace',
                'what of each the artefact carried, transformed or lost and what it introduced, map who can do what,',
                'and only then judge every criterion below on its own anchors through that reading, grounded in the',
                'carriage and the standing and citing the numbered evidence directly.',
            ]
        )
    elif version >= 4:
        lead = [
            'The seed is a semantic conjecture; the artefact recompiles it across a boundary into another medium.',
            ('Make a short evidence inventory, then read' if inventory else 'Read')
            + ' the seed into its roles, relations, conditions and conjectures, trace what of each the',
            'artefact carried, transformed or lost and what it introduced, and only then judge every criterion',
            'below on its own anchors, grounded in that carriage and citing the numbered evidence directly.',
        ]
    else:
        lead = [
            ('First make a short evidence inventory, then judge' if inventory else 'Judge')
            + ' every criterion below on its own anchors, citing the numbered evidence directly.'
        ]
    lines = (
        ['TASK reading · ' + str(len(judgments)) + ' criteria in one answer']
        + lead
        + ['Keep each rationale to a few sentences.', '', 'Answer with one JSON object and nothing else:', '{']
    )
    if inventory:
        lines += [
            ' "inventory": {"items": [{"id": "kebab-case-id", "kind": KIND, "statement": "...",',
            '                           "citations": [{"source_id": "source-0001", "start": 1, "end": 3}],',
            '                           "ambiguity": null}],',
            '               "unknowns": ["..."]},',
        ]
    if version >= 4:
        lines += [
            line.replace(
                '"role" | "relation" | "condition" | "conjecture"',
                '"role" | "relation" | "condition" | "conjecture" | "stance"',
            )
            if version >= 5
            else line
            for line in CARRIAGE_SHAPE
        ]
    if version >= 5:
        lines += STANDING_SHAPE
    shape = [
        line.replace('"rationale": "fuller reasoning"', '"rationale": "Lens: ... Reading: ... Other levels: ..."')
        if version >= 5
        else line
        for line in ANSWER_SHAPE
    ]
    lines += [
        ' "judgments": {"<criterion id>": ANSWER, ...one entry for every criterion id below}',
        '}',
        'ANSWER is:',
    ] + ['  ' + line.replace('OUTCOME', 'one of the criterion outcomes') for line in shape]
    lines += answer_rules(version)
    if version >= 4:
        lines += CARRIAGE_RULES
    if version >= 5:
        lines += STANDING_RULES
    if inventory:
        lines += [
            'Inventory: 8 to 20 items that matter for reading the artefact; KIND is one of: '
            + ', '.join(sorted(contracts.ITEM_KINDS))
            + '; `ambiguity` is null or a short explanation;',
            '`unknowns` lists what the evidence cannot settle (it may be empty). Do not score in the inventory.',
        ]
    lines += ['', 'CRITERIA']
    for judgment in judgments:
        lines += [
            '',
            '## ' + judgment['id'] + ' · ' + judgment['criterion'],
            'Outcomes: ' + ', '.join(judgment['outcomes']),
        ] + criterion_block(judgment)
    return '\n'.join(lines) + '\n'


def synthetic_judgment(outcome, prepared, version=1):
    source = next(source for source in prepared['sources'] if any(line.strip() for line in source['lines']))
    line_number = next(index for index, line in enumerate(source['lines'], 1) if line.strip())
    rationale = 'Synthetic transport judgment; no semantic assessment was performed.'
    return {
        'outcome': outcome,
        'confidence': 'low',
        'verdict': 'Synthetic transport judgment; no semantic assessment was performed.',
        'seed_span': [1, 1],
        'citations': [{'source_id': source['source_id'], 'start': line_number, 'end': line_number}],
        'counter_citations': [],
        'rationale': ('Lens: none. Reading: ' + rationale + ' Other levels: none.') if version >= 5 else rationale,
    }


def synthetic_carriage(prepared, version=4):
    source = next(source for source in prepared['sources'] if any(line.strip() for line in source['lines']))
    line_number = next(index for index, line in enumerate(source['lines'], 1) if line.strip())
    kinds = ['role', 'relation', 'condition', 'conjecture', 'role', 'stance' if version >= 5 else 'relation']
    elements = [
        {
            'id': 'synthetic-element-' + str(index),
            'kind': kind,
            'statement': 'Synthetic seed element; no reading was performed.',
            'seed_span': [1, 1],
            'ambiguity': None,
        }
        for index, kind in enumerate(kinds, 1)
    ]
    carriage = [
        {
            'element': element['id'],
            'status': 'lost' if index % 2 else 'carried',
            'note': 'Synthetic carriage; no reading was performed.',
            'citations': []
            if index % 2
            else [{'source_id': source['source_id'], 'start': line_number, 'end': line_number}],
        }
        for index, element in enumerate(elements, 1)
    ]
    result = {'seed_reading': {'elements': elements, 'alternatives': []}, 'carriage': carriage}
    if version >= 5:
        result['standing'] = [
            {
                'participant': 'synthetic-participant',
                'kind': 'other',
                'gestures': [],
                'note': 'Synthetic standing; no reading was performed.',
                'citations': [{'source_id': source['source_id'], 'start': line_number, 'end': line_number}],
            }
        ]
    return result


def synthetic_answer(outcome, prepared):
    return json.dumps(synthetic_judgment(outcome, prepared), ensure_ascii=False)


def normalize_answer_shape(answer):
    require(isinstance(answer, dict), 'Judgment answer must be an object')
    extra = set(answer) - set(ANSWER_FIELDS)
    require(extra <= REDUNDANT_NULL_ANSWER_FIELDS, 'Judgment answer has undeclared fields: ' + ', '.join(sorted(extra)))
    normalized, repairs = dict(answer), []
    for field in sorted(extra):
        require(normalized[field] is None, 'Judgment answer has a non-null undeclared field: ' + field)
        normalized.pop(field)
        repairs.append('removed redundant null field ' + field)
    contracts.exact_fields(normalized, ANSWER_FIELDS, 'judgment answer')
    return normalized, repairs


def validate_verdict(value):
    contracts.bounded_text(value, 'judgment verdict', MAX_VERDICT_CHARS)
    require('\n' not in value and '\r' not in value, 'Judgment verdict must be one line')
    require(
        not re.search(r'\b(?:level|score)\s*[0-4]\b', value, re.IGNORECASE),
        'Judgment verdict must explain the evidence rather than narrate a level',
    )
    require(len(re.findall(r'[.!?](?=\s|$)', value.strip())) <= 1, 'Judgment verdict must be a single sentence')
    return value


def judgment_from_answer(answer, *, judgment, prepared, answer_sha256, receipt_sha256, version=1):
    """Normalize one parsed judgment answer; quotes are never copied, only exact ranges kept."""
    answer, repairs = normalize_answer_shape(answer)
    if version >= 5:
        rationale = answer.get('rationale') if isinstance(answer.get('rationale'), str) else ''
        positions = [rationale.find(part) for part in RATIONALE_PARTS]
        require(
            all(position >= 0 for position in positions) and positions == sorted(positions),
            'The rationale must read "Lens: ... Reading: ... Other levels: ..." in that order',
        )
    span = answer['seed_span']
    require(
        isinstance(span, list)
        and len(span) == 2
        and all(type(value) is int for value in span)
        and 1 <= span[0] <= span[1] <= len(prepared['seed_lines']),
        'Judgment needs a seed line range inside the ' + str(len(prepared['seed_lines'])) + '-line seed',
    )
    require(answer['outcome'] in judgment['outcomes'], 'Judgment answer uses an undeclared outcome')
    require(answer['confidence'] in CONFIDENCE, 'Judgment confidence must be low, medium or high')
    validate_verdict(answer['verdict'])
    contracts.bounded_text(answer['rationale'], 'judgment rationale')
    resolved = {}
    for field, minimum in [('citations', 1), ('counter_citations', 0)]:
        resolved[field] = extraction_pipeline.resolve_citations(
            answer[field], prepared['source_map'], prepared['source_texts'], field, repairs, minimum=minimum
        )
    rationale = answer['rationale'] + (' Host note: ' + '; '.join(repairs) + '.' if repairs else '')
    return {
        'node_id': judgment['id'],
        'outcome': answer['outcome'],
        'confidence': answer['confidence'],
        'verdict': answer['verdict'],
        'seed_span': span,
        'citations': resolved['citations'],
        'counter_citations': resolved['counter_citations'],
        'rationale': rationale[:16000],
        'answer_sha256': answer_sha256,
        'receipt_sha256': receipt_sha256,
    }


def validate_judgment(answer_path, receipt, receipt_sha256, *, judgment, prepared):
    extraction_pipeline.check_receipt(receipt)
    path = Path(answer_path)
    return {
        judgment['id']: judgment_from_answer(
            contracts.load_json(path),
            judgment=judgment,
            prepared=prepared,
            answer_sha256=sha(path.read_bytes()),
            receipt_sha256=receipt_sha256,
        )
    }


def carriage_from_answer(answer, *, prepared, answer_sha256, version=4):
    """Validate the seed reading and carriage map (and, from version 5, the standing map), resolving citations."""
    reading = answer['seed_reading']
    ids = contracts.validate_seed_reading(reading, seed_line_count=len(prepared['seed_lines']))
    require(6 <= len(reading['elements']) <= 20, 'Seed reading needs 6 to 20 elements')
    require(
        version >= 5 or all(element['kind'] != 'stance' for element in reading['elements']),
        'Stance elements belong to prompt version 5',
    )
    require(
        version < 5 or any(element['kind'] == 'stance' for element in reading['elements']),
        'The seed reading must record at least one stance: its frame, turn or what it leaves open',
    )
    entries = []
    require(isinstance(answer['carriage'], list), 'Carriage must be a list')
    require(
        sum(isinstance(entry, dict) and entry.get('status') == 'introduced' for entry in answer['carriage']) <= 8,
        'At most 8 introduced carriage entries',
    )
    for index, entry in enumerate(answer['carriage'], 1):
        contracts.exact_fields(entry, ['element', 'status', 'note', 'citations'], 'carriage entry')
        repairs = []
        resolved = extraction_pipeline.resolve_citations(
            entry['citations'],
            prepared['source_map'],
            prepared['source_texts'],
            'carriage ' + str(index),
            repairs,
            minimum=0 if entry['status'] == 'lost' else 1,
        )
        note = entry['note'] if not repairs else entry['note'] + ' Host note: ' + '; '.join(repairs) + '.'
        entries.append(dict(entry, note=note[:2000], citations=resolved))
    contracts.validate_carriage(entries, ids, sources=prepared['source_texts'])
    result = {'answer_sha256': answer_sha256, 'seed_reading': reading, 'carriage': entries}
    if version >= 5:
        require(isinstance(answer['standing'], list), 'Standing must be a list')
        standing = []
        for index, entry in enumerate(answer['standing'], 1):
            contracts.exact_fields(entry, ['participant', 'kind', 'gestures', 'note', 'citations'], 'standing entry')
            repairs = []
            resolved = extraction_pipeline.resolve_citations(
                entry['citations'],
                prepared['source_map'],
                prepared['source_texts'],
                'standing ' + str(index),
                repairs,
                minimum=1,
            )
            note = entry['note'] if not repairs else entry['note'] + ' Host note: ' + '; '.join(repairs) + '.'
            standing.append(dict(entry, note=note[:2000], citations=resolved))
        contracts.validate_standing(standing, sources=prepared['source_texts'])
        result['standing'] = standing
    return result


def validate_compact(answer_path, receipt, receipt_sha256, *, judgments, prepared, ident=None, version=1):
    """Validate a combined answer; any invalid part sends the whole task to a repair turn."""
    extraction_pipeline.check_receipt(receipt)
    path = Path(answer_path)
    answer, answer_sha256 = contracts.load_json(path), sha(path.read_bytes())
    contracts.exact_fields(
        answer,
        (['inventory'] if ident else [])
        + (['seed_reading', 'carriage'] if version >= 4 else [])
        + (['standing'] if version >= 5 else [])
        + ['judgments'],
        'reading answer',
    )
    require(
        isinstance(answer['judgments'], dict)
        and set(answer['judgments']) == {judgment['id'] for judgment in judgments},
        'Judgments must name every criterion exactly: ' + ', '.join(judgment['id'] for judgment in judgments),
    )
    results = {}
    for judgment in judgments:
        try:
            results[judgment['id']] = judgment_from_answer(
                answer['judgments'][judgment['id']],
                judgment=judgment,
                prepared=prepared,
                answer_sha256=answer_sha256,
                receipt_sha256=receipt_sha256,
                version=version,
            )
        except ValueError as error:
            raise ValueError(judgment['id'] + ': ' + str(error)) from error
    if ident:
        try:
            results['extraction'] = extraction_pipeline.record_from_answer(
                answer['inventory'],
                ident,
                prepared['source_texts'],
                receipt,
                answer_sha256=answer_sha256,
                receipt_sha256=receipt_sha256,
            )
        except ValueError as error:
            raise ValueError('inventory: ' + str(error)) from error
    if version >= 4:
        try:
            results['carriage'] = carriage_from_answer(
                answer, prepared=prepared, answer_sha256=answer_sha256, version=version
            )
        except ValueError as error:
            raise ValueError('carriage: ' + str(error)) from error
    return results


def prompt_items(extraction, source_map):
    """Compact extraction items for the evidence block; citations become source-ID ranges."""
    reverse = {path: source_id for source_id, path in source_map.items()}
    return [
        {
            'id': item['id'],
            'kind': item['kind'],
            'statement': item['statement'],
            'citations': [
                reverse[citation['source_path']] + ':' + str(citation['start']) + '-' + str(citation['end'])
                for citation in item['citations']
            ],
            'ambiguity': item['ambiguity'],
        }
        for item in extraction['items']
    ]


def _synthetic_outcomes(branches, supplied, repetitions):
    if supplied is None:
        return {branch['judgment']['id']: [branch['judgment']['outcomes'][0]] * repetitions for branch in branches}
    if isinstance(supplied, dict):
        require(
            set(supplied) == {branch['judgment']['id'] for branch in branches},
            'Synthetic outcomes must name every axis exactly',
        )
        result = dict(supplied)
    else:
        require(isinstance(supplied, list), 'Synthetic outcomes must be a list or axis mapping')
        result = {branch['judgment']['id']: list(supplied) for branch in branches}
    for branch in branches:
        values = result[branch['judgment']['id']]
        require(
            isinstance(values, list) and len(values) == repetitions, 'Synthetic outcome count must equal repetitions'
        )
        require(all(value in branch['judgment']['outcomes'] for value in values), 'Synthetic outcome is not declared')
    return result


class Plan:
    """Everything a scoreflow directory needs to be run, resumed, recovered or validated offline."""

    def __init__(
        self,
        project,
        policy,
        *,
        run_id,
        supplied_extraction=None,
        dry_run=False,
        synthetic_outcomes=None,
        data=None,
        layout='compact',
        prompt_version=None,
        control_seed=None,
        masked_terms=None,
        ablated_ranges=None,
    ):
        require(layout in LAYOUTS, 'Layout must be one of: ' + ', '.join(LAYOUTS))
        # The version 5 reading is one compact answer; per-axis sessions keep the version 4 prompts.
        if prompt_version is None:
            prompt_version = (
                extraction_pipeline.PROMPT_VERSION
                if layout == 'compact'
                else min(extraction_pipeline.PROMPT_VERSION, 4)
            )
        require(prompt_version < 5 or layout == 'compact', 'The version 5 reading runs in the compact layout')
        self.layout = layout
        self.prompt_version = prompt_version
        self.project = Path(project).resolve()
        self.policy = policy
        self.nodes, self.outgoing, self.branches = score_branches(policy)
        self.repetitions = self.branches[0]['judgment']['repetitions']
        self.prepared = extraction_pipeline.prepare_sources(self.project, run_id, data=data)
        # A control reading presents the artefact with a seed it did not receive; it is never admitted.
        self.control_seed = control_seed
        if control_seed is not None:
            require(control_seed.strip() and len(control_seed.encode('utf-8')) <= 65536, 'Invalid control seed')
            self.prepared = dict(self.prepared, seed_lines=control_seed.splitlines())
        # A masked reading hides the seed's distinctive words in the evidence (never in the frozen candidate):
        # what still reads as carried was carried by structure, not by echo. It is never admitted either.
        self.masked_terms = masked_terms
        if masked_terms is not None:
            self.prepared = mask_evidence(self.prepared, masked_terms)
        # An ablated reading blanks declared line ranges in the evidence (load-bearing trials); never admitted.
        self.ablated_ranges = ablated_ranges
        if ablated_ranges is not None:
            self.prepared = ablate_evidence(self.prepared, ablated_ranges)
        self.run = self.prepared['run']
        self.supplied = supplied_extraction
        self.extraction_id = supplied_extraction['extraction_id'] if supplied_extraction else run_id + '-extraction'
        self.ident = extraction_pipeline.identity(self.prepared, self.extraction_id)
        self.fake = _synthetic_outcomes(self.branches, synthetic_outcomes, self.repetitions) if dry_run else None
        self.dry_run = dry_run

    def evidence(self, session, extraction=None):
        if session == 1 and self.supplied is None:
            return session_flow.format_evidence(self.prepared['seed_lines'], self.prepared['sources'])
        extraction = self.supplied or extraction
        require(extraction is not None, 'Later sessions need the validated extraction')
        return session_flow.format_evidence(
            self.prepared['seed_lines'], self.prepared['sources'], prompt_items(extraction, self.prepared['source_map'])
        )

    def tasks(self, session):
        inventory = session == 1 and self.supplied is None
        if self.layout == 'compact':
            judgments = [branch['judgment'] for branch in self.branches]
            fake = None
            if self.fake:
                fake = {
                    'judgments': {
                        judgment['id']: synthetic_judgment(
                            self.fake[judgment['id']][session - 1], self.prepared, self.prompt_version
                        )
                        for judgment in judgments
                    }
                }
                if inventory:
                    fake = dict(inventory=json.loads(extraction_pipeline.synthetic_answer(self.prepared)), **fake)
                if self.prompt_version >= 4:
                    fake.update(synthetic_carriage(self.prepared, self.prompt_version))
                fake = json.dumps(fake, ensure_ascii=False)
            return [
                session_flow.Task(
                    id='reading',
                    prompt=compact_prompt(judgments, inventory=inventory, version=self.prompt_version),
                    fake=fake,
                    answer_tokens=1200 * len(judgments) + (6000 if inventory else 0),
                    provides=(['extraction'] if inventory else [])
                    + (['carriage'] if self.prompt_version >= 4 else [])
                    + [judgment['id'] for judgment in judgments],
                    validate=lambda answer, receipt, receipt_sha256: validate_compact(
                        answer,
                        receipt,
                        receipt_sha256,
                        judgments=judgments,
                        prepared=self.prepared,
                        ident=self.ident if inventory else None,
                        version=self.prompt_version,
                    ),
                )
            ]
        tasks = []
        if inventory:
            tasks.append(
                extraction_pipeline.extraction_task(
                    self.prepared, self.ident, dry_run=self.dry_run, version=self.prompt_version
                )
            )
        for branch in self.branches:
            judgment = branch['judgment']
            fake = synthetic_answer(self.fake[judgment['id']][session - 1], self.prepared) if self.fake else None
            tasks.append(
                session_flow.Task(
                    id=judgment['id'],
                    prompt=judgment_prompt(judgment, self.prompt_version),
                    fake=fake,
                    validate=lambda answer, receipt, receipt_sha256, judgment=judgment: validate_judgment(
                        answer, receipt, receipt_sha256, judgment=judgment, prepared=self.prepared
                    ),
                )
            )
        return tasks

    def record(self, *, model, reasoning):
        return (
            {
                'scoreflow_version': SCOREFLOW_VERSION,
                'run_id': self.run['id'],
                'prompt_sha256': self.run['prompt_sha256'],
                'corpus_sha256': self.run['corpus_sha256'],
                'source_map': self.prepared['source_map'],
                'policy_sha256': sha(canonical(self.policy)),
                'extraction': (
                    {'mode': 'supplied', 'extraction_id': self.extraction_id, 'sha256': sha(canonical(self.supplied))}
                    if self.supplied
                    else {'mode': 'session', 'extraction_id': self.extraction_id}
                ),
                'sessions': self.repetitions,
                'tasks': {
                    str(session): [task.id for task in self.tasks(session)]
                    for session in range(1, self.repetitions + 1)
                },
                'task_sha256': {task.id: sha(task.prompt.encode('utf-8')) for task in self.tasks(1)},
                'evidence_sha256': sha(self.evidence(1).encode('utf-8')),
                'model': model,
                'reasoning': reasoning,
                'dry_run': self.dry_run,
                'layout': self.layout,
            }
            | ({'prompt_version': self.prompt_version} if self.prompt_version != 1 else {})
            | ({'control_seed_sha256': sha(self.control_seed.encode('utf-8'))} if self.control_seed is not None else {})
            | ({'masked_terms_sha256': sha(canonical(self.masked_terms))} if self.masked_terms is not None else {})
            | (
                {'ablated_ranges_sha256': sha(canonical(self.ablated_ranges))}
                if self.ablated_ranges is not None
                else {}
            )
        )


def mask_evidence(prepared, terms):
    """Replace each listed term (whole words, any case) with a stable neutral token in the evidence the reader sees.

    Line structure is kept, so citations keep their ranges; the frozen source texts stay unmasked for verification.
    """
    require(
        isinstance(terms, list)
        and terms
        and all(isinstance(term, str) and term.strip() for term in terms)
        and len(set(term.lower() for term in terms)) == len(terms),
        'Masked terms must be distinct words',
    )
    ordered = sorted(enumerate(terms, 1), key=lambda item: -len(item[1]))
    pattern = re.compile('|'.join(r'\b' + re.escape(term) + r'\b' for _, term in ordered), re.IGNORECASE)
    tokens = {term.lower(): 'term-' + str(index).zfill(2) for index, term in ordered}

    def mask(text):
        return pattern.sub(lambda match: tokens[match.group(0).lower()], text)

    return dict(
        prepared,
        sources=[dict(source, lines=[mask(line) for line in source['lines']]) for source in prepared['sources']],
    )


def ablate_evidence(prepared, ranges):
    """Blank declared line ranges in the evidence the reader sees, without announcing it.

    Line numbers stay, so citations elsewhere keep their ranges; the frozen source texts stay whole.
    """
    require(isinstance(ranges, list) and ranges, 'Ablated ranges must be a non-empty list')
    lengths = {source['path']: len(source['lines']) for source in prepared['sources']}
    blanked = {}
    for item in ranges:
        contracts.exact_fields(item, ['path', 'start', 'end'], 'ablated range')
        require(item['path'] in lengths, 'Ablated range names an unknown source: ' + str(item['path']))
        require(
            type(item['start']) is int
            and type(item['end']) is int
            and 1 <= item['start'] <= item['end'] <= lengths[item['path']],
            'Ablated range lies outside its source: ' + item['path'],
        )
        blanked.setdefault(item['path'], set()).update(range(item['start'], item['end'] + 1))
    return dict(
        prepared,
        sources=[
            dict(
                source,
                lines=[
                    '' if number in blanked.get(source['path'], ()) else line
                    for number, line in enumerate(source['lines'], 1)
                ],
            )
            for source in prepared['sources']
        ],
    )


def _session_dir(output, session):
    return Path(output) / 'sessions' / str(session).zfill(2)


def _derive(plan, output):
    """Replay every preserved session and return the extraction, branch results and receipts."""
    extraction = plan.supplied
    sessions, receipts_by_session = {}, []
    for session in range(1, plan.repetitions + 1):
        accepted, receipts = session_flow.verify_session(
            _session_dir(output, session), plan.tasks(session), plan.evidence(session, extraction)
        )
        if session == 1 and plan.supplied is None:
            extraction = accepted['extraction'][0]
        sessions[session] = accepted
        receipts_by_session.append(receipts)
    readings = [
        dict(sample=session, **sessions[session]['carriage'][0])
        for session in sessions
        if 'carriage' in sessions[session]
    ]
    carriage = (
        {
            'schema_version': 2 if plan.prompt_version >= 5 else 1,
            'evaluation_id': None,
            'run_id': plan.run['id'],
            'readings': readings,
        }
        if readings
        else None
    )
    branch_results = []
    for branch in plan.branches:
        judgment, aggregate_node = branch['judgment'], branch['aggregate']
        samples = []
        for session in range(1, plan.repetitions + 1):
            result, _, attempt = sessions[session][judgment['id']]
            samples.append(dict(result, sample=session, attempt=attempt))
        aggregation = contracts.aggregate_samples([sample['outcome'] for sample in samples], aggregate_node)
        terminal = contracts.terminal_for(plan.outgoing, plan.nodes, aggregate_node['id'], aggregation['outcome'])
        branch_results.append(
            {'judgment': judgment, 'judgments': samples, 'aggregation': aggregation, 'terminal': terminal}
        )
    return extraction, branch_results, receipts_by_session, carriage


def build_record(
    *,
    project,
    evaluation_id,
    extraction,
    extraction_bytes,
    policy,
    policy_bytes,
    plan,
    branch_results,
    receipts,
    model,
    reasoning,
):
    rubric = load_json(Path(project) / 'data/rubric.json')
    runtime = receipts[0]['runtime']
    outcomes = [
        {
            'node_id': branch['terminal']['id'],
            'status': branch['terminal']['result']['status'],
            'score': branch['terminal']['result']['score'],
            'rationale': branch['terminal']['result']['label']
            + ' Aggregate outcome: '
            + branch['aggregation']['outcome']
            + '.',
        }
        for branch in branch_results
    ]
    scored = sum(outcome['status'] == 'scored' for outcome in outcomes)
    sessions = plan.repetitions
    summary = (
        'Rubric judgments resolve '
        + str(scored)
        + ' of '
        + str(len(outcomes))
        + ' axes from '
        + (
            'one evidence session.'
            if sessions == 1
            else str(sessions) + ' independent evidence sessions; each axis has its own quorum.'
        )
    )
    usage = session_flow.usage_totals(receipts)
    record = {
        'schema_version': RECORD_SCHEMA,
        'evaluation_id': evaluation_id,
        'run_id': plan.run['id'],
        'identities': {
            'prompt_sha256': plan.run['prompt_sha256'],
            'corpus_sha256': plan.run['corpus_sha256'],
            'candidate_sha256': extraction['identities']['candidate_sha256'],
            'rubric_sha256': sha(canonical(rubric)),
            'extraction_sha256': sha(extraction_bytes),
            'scoring_policy_sha256': sha(policy_bytes),
        },
        'gateway': {
            'name': worker.GATEWAY,
            'model': model,
            'reasoning': reasoning,
            'codex_version': runtime['codex_version'],
            'router_revision': runtime['router']['revision'] if runtime.get('router') else '0' * 64,
            'pipeline_sha256': code_identity(),
        }
        | (
            {
                'reading_prompt': {
                    'version': plan.prompt_version,
                    'sha256': sha(plan.tasks(1)[0].prompt.encode('utf-8')),
                },
            }
            if plan.prompt_version >= 5
            else {}
        ),
        'judgments': [judgment for branch in branch_results for judgment in branch['judgments']],
        'aggregations': [branch['aggregation'] for branch in branch_results],
        'outcomes': outcomes,
        'presentation': {
            'label': model + (' · synthetic session profile' if plan.dry_run else ' · Codex-router rubric session'),
            'title': rubric['title'],
            'summary': summary,
            'findings': [
                branch['judgment']['criterion'] + ': ' + branch['terminal']['result']['label']
                for branch in branch_results
            ],
        },
        'usage': {
            key: usage[key]
            for key in ['calls', 'input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens']
        }
        | {'cost': None},
    }
    contracts.validate_evaluation(
        record,
        extraction=extraction,
        extraction_sha256=sha(extraction_bytes),
        policy=policy,
        scoring_policy_sha256=sha(policy_bytes),
        source_texts=plan.prepared['source_texts'],
    )
    return record


def _status(record, branch_results, receipts_by_session, *, dry_run, recovered=False):
    receipts = [receipt for receipts in receipts_by_session for receipt in receipts]
    return dict(
        {
            'status': 'completed',
            'dry_run': dry_run,
            'run_id': record['run_id'],
            'evaluation_id': record['evaluation_id'],
            'gateway': worker.GATEWAY,
            'scoreflow_version': SCOREFLOW_VERSION,
            'criteria': len(record['aggregations']),
            'judgment_samples': len(record['judgments']),
            'scored_axes': sum(outcome['status'] == 'scored' for outcome in record['outcomes']),
            'unresolved_axes': sum(outcome['status'] != 'scored' for outcome in record['outcomes']),
            'outcomes': {branch['judgment']['id']: branch['aggregation']['outcome'] for branch in branch_results},
            'usage': session_flow.usage_totals(receipts),
        },
        **session_flow.session_stats(receipts_by_session),
        **({'recovered': True} if recovered else {}),
    )


def _load_plan(project, output, *, data=None):
    output = Path(output)
    stored = load_json(output / 'plan.json')
    policy = contracts.load_json(output / 'scoring-policy.json')
    supplied = contracts.load_json(output / 'extraction.json') if stored['extraction']['mode'] == 'supplied' else None
    control = (output / 'control-seed.txt').read_text(encoding='utf-8') if 'control_seed_sha256' in stored else None
    masked = load_json(output / 'masked-terms.json') if 'masked_terms_sha256' in stored else None
    ablated = load_json(output / 'ablated-ranges.json') if 'ablated_ranges_sha256' in stored else None
    plan = Plan(
        project,
        policy,
        run_id=stored['run_id'],
        supplied_extraction=supplied,
        dry_run=stored['dry_run'],
        data=data,
        layout=stored['layout'],
        prompt_version=stored.get('prompt_version', 1),
        control_seed=control,
        masked_terms=masked,
        ablated_ranges=ablated,
    )
    plan.fake = None
    expected = plan.record(model=stored['model'], reasoning=stored['reasoning'])
    require(expected == stored, 'Preserved scoreflow plan differs from the current evidence, policy or code contract')
    return plan, stored


def _finish(project, output, plan, stored, evaluation_id, *, recovered=False):
    extraction, branch_results, receipts_by_session, carriage = _derive(plan, output)
    if carriage is not None:
        carriage['evaluation_id'] = evaluation_id
        contracts.validate_carriage_record(
            carriage, source_texts=plan.prepared['source_texts'], seed_line_count=len(plan.prepared['seed_lines'])
        )
        carriage_path = output / 'carriage.json'
        stored_carriage = contracts.load_json(carriage_path) if carriage_path.exists() else None
        if stored_carriage is not None and not (
            recovered and dict(stored_carriage, evaluation_id=evaluation_id) == carriage
        ):
            require(stored_carriage == carriage, 'Session carriage differs from carriage.json')
        else:
            carriage_path.write_text(json.dumps(carriage, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    extraction_path = output / 'extraction.json'
    if plan.supplied is None:
        if extraction_path.exists():
            # The stored record names the code that wrote it; later code may still verify it.
            stored_extraction = contracts.load_json(extraction_path)
            extraction['extractor']['pipeline_sha256'] = stored_extraction['extractor']['pipeline_sha256']
            require(stored_extraction == extraction, 'Session extraction differs from extraction.json')
        else:
            extraction_path.write_text(json.dumps(extraction, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    policy_bytes = (output / 'scoring-policy.json').read_bytes()
    receipts = [receipt for receipts in receipts_by_session for receipt in receipts]
    record = build_record(
        project=project,
        evaluation_id=evaluation_id,
        extraction=extraction,
        extraction_bytes=extraction_path.read_bytes(),
        policy=plan.policy,
        policy_bytes=policy_bytes,
        plan=plan,
        branch_results=branch_results,
        receipts=receipts,
        model=stored['model'],
        reasoning=stored['reasoning'],
    )
    status = _status(record, branch_results, receipts_by_session, dry_run=plan.dry_run, recovered=recovered)
    if plan.control_seed is not None:
        status['control'] = 'mismatched seed; never admitted'
    if plan.masked_terms is not None:
        status['control'] = 'masked evidence; never admitted'
    if plan.ablated_ranges is not None:
        status['control'] = 'ablated evidence; never admitted'
    return record, status


def _write(output, record, status):
    def write(name, value):
        temporary = output / ('.' + name + '.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temporary.replace(output / name)

    write('evaluation.json', record)
    status['evaluation_sha256'] = sha((output / 'evaluation.json').read_bytes())
    write('status.json', status)
    return status


def run(
    project,
    policy_path,
    output,
    *,
    run_id=None,
    extraction_path=None,
    model,
    reasoning='high',
    evaluation_id=None,
    dry_run=False,
    accept_run=False,
    synthetic_outcomes=None,
    router_url='http://127.0.0.1:18766/v1',
    catalog=None,
    router_profile=None,
    capabilities=None,
    budget=None,
    repairs=2,
    layout='compact',
    control_seed_path=None,
    masked_terms_path=None,
    ablated_ranges_path=None,
):
    """Run or resume a scoreflow. Without `extraction_path`, session 1 also makes the evidence inventory.

    `compact` answers the inventory and every criterion in one turn per session;
    `per-axis` gives the inventory and each criterion a turn of its own.
    """
    project, output = Path(project).resolve(), Path(output).resolve()
    require(dry_run or accept_run, 'Live scoring requires --accept-run')
    budget = session_flow.validate_budget(budget, required=not dry_run)
    require(isinstance(model, str) and model, 'A scorer model is required')
    require(reasoning in contracts.REASONING, 'Invalid reasoning effort')
    policy = contracts.load_json(policy_path)
    supplied = None
    if extraction_path is not None:
        extraction_path = Path(extraction_path).resolve()
        checked = extraction_pipeline.validate_output(project, extraction_path)
        require(dry_run or not checked['synthetic'], 'A synthetic extraction cannot feed live scoring')
        supplied = contracts.load_json(extraction_path)
        require(run_id in (None, supplied['run_id']), 'Extraction belongs to another run')
        run_id = supplied['run_id']
    require(run_id is not None, 'Choose a run or supply an extraction')
    control = Path(control_seed_path).read_text(encoding='utf-8') if control_seed_path else None
    masked = load_json(Path(masked_terms_path)) if masked_terms_path else None
    ablated = load_json(Path(ablated_ranges_path)) if ablated_ranges_path else None
    plan = Plan(
        project,
        policy,
        run_id=run_id,
        supplied_extraction=supplied,
        dry_run=dry_run,
        synthetic_outcomes=synthetic_outcomes,
        layout=layout,
        control_seed=control,
        masked_terms=masked,
        ablated_ranges=ablated,
    )
    # Repairs and budget are execution limits, not identity: a resume may raise them.
    stored = plan.record(model=model, reasoning=reasoning)
    evaluation_id = evaluation_id or ('evaluation-' + run_id)
    require(contracts.SAFE_ID.fullmatch(evaluation_id), 'Invalid evaluation ID')
    if output.exists():
        require((output / 'plan.json').is_file(), 'Evaluation output exists without a plan; use a new directory')
        require(load_json(output / 'plan.json') == stored, 'Preserved scoreflow plan differs; use a new directory')
        require(not (output / 'evaluation.json').exists(), 'Evaluation already completed; validate it instead')
    else:
        output.mkdir(parents=True, mode=0o700)
        shutil.copyfile(policy_path, output / 'scoring-policy.json')
        if supplied is not None:
            shutil.copyfile(extraction_path, output / 'extraction.json')
        if control is not None:
            (output / 'control-seed.txt').write_text(control, encoding='utf-8')
            (output / 'control-seed.txt').chmod(0o400)
        if masked is not None:
            (output / 'masked-terms.json').write_bytes(canonical(masked) + b'\n')
            (output / 'masked-terms.json').chmod(0o400)
        if ablated is not None:
            (output / 'ablated-ranges.json').write_bytes(canonical(ablated) + b'\n')
            (output / 'ablated-ranges.json').chmod(0o400)
        (output / 'plan.json').write_bytes(canonical(stored) + b'\n')
        for name in ['plan.json', 'scoring-policy.json']:
            (output / name).chmod(0o400)
    job = {'model': model, 'reasoning': reasoning, 'seconds': 900 if not dry_run else 120}
    runtime = worker.freeze_runtime(
        output,
        [job],
        dry_run,
        router_url,
        catalog,
        router_profile,
        extra_instructions={'session': session_flow.SESSION_INSTRUCTIONS},
        capability_path=capabilities,
    )
    spent = session_flow.ledger(output)
    planned = session_flow.estimate(
        plan.evidence(1), [plan.tasks(session) for session in range(1, plan.repetitions + 1)]
    )
    if budget is not None and not spent:
        require(
            planned['turns'] <= budget['max_turns'],
            'Turn budget is below the ' + str(planned['turns']) + ' planned turns',
        )
        require(
            planned['input_tokens'] <= budget['max_input_tokens'],
            'Input budget is below the planned minimum of ~' + str(planned['input_tokens']) + ' tokens',
        )
    extraction = supplied
    for session in range(1, plan.repetitions + 1):
        accepted = session_flow.run_session(
            _session_dir(output, session),
            plan.tasks(session),
            evidence=plan.evidence(session, extraction),
            job=job,
            runtime=runtime,
            catalog=output / 'catalog.json',
            router_url=router_url,
            dry_run=dry_run,
            budget=budget,
            repairs=repairs,
            spent=spent,
        )
        if session == 1 and supplied is None:
            extraction = accepted['extraction'][0]
    record, status = _finish(project, output, plan, stored, evaluation_id)
    status['estimate'] = planned
    if budget and budget.get('pricing'):
        status['cost_estimate_usd'] = session_flow.usage_totals(spent, budget['pricing'])['estimated_usd']
    return _write(output, record, status)


def recover(project, output, *, evaluation_id=None):
    """Finalize a completed preserved scoreflow without model calls."""
    project, output = Path(project).resolve(), Path(output).resolve()
    require(output.is_dir(), 'Scoring recovery directory is missing')
    require(
        not (output / 'evaluation.json').exists() and not (output / 'status.json').exists(),
        'Scoring recovery requires an output directory without a completed evaluation',
    )
    plan, stored = _load_plan(project, output)
    evaluation_id = evaluation_id or ('recovered-evaluation-' + stored['run_id'])
    require(contracts.SAFE_ID.fullmatch(evaluation_id), 'Invalid evaluation ID')
    record, status = _finish(project, output, plan, stored, evaluation_id, recovered=True)
    return _write(output, record, status)


def validate_output(project, directory, *, data=None):
    """Re-derive a completed scoreflow from its preserved turns and compare every stored record."""
    project, directory = Path(project).resolve(), Path(directory).resolve()
    plan, stored = _load_plan(project, directory, data=data)
    evaluation = contracts.load_json(directory / 'evaluation.json')
    record, status = _finish(project, directory, plan, stored, evaluation['evaluation_id'])
    # The pipeline hash names the code that wrote the record; later code may still verify it.
    record['gateway']['pipeline_sha256'] = evaluation['gateway']['pipeline_sha256']
    require(record == evaluation, 'Evaluation record differs from the preserved session turns')
    stored_status = contracts.load_json(directory / 'status.json')
    require(
        {key: value for key, value in stored_status.items() if key in status} == status
        and stored_status.get('evaluation_sha256') == sha((directory / 'evaluation.json').read_bytes()),
        'Evaluation status differs from the preserved session turns',
    )
    return dict(
        status,
        extraction_sha256=sha((directory / 'extraction.json').read_bytes()),
        evaluation_sha256=sha((directory / 'evaluation.json').read_bytes()),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=ROOT)
    parser.add_argument('--policy', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--run-id')
    parser.add_argument('--extraction', type=Path)
    parser.add_argument('--evaluation-id')
    parser.add_argument('--model', required=True)
    parser.add_argument('--reasoning', choices=sorted(contracts.REASONING), default='high')
    parser.add_argument('--synthetic-outcomes', help='Comma-separated dry-run outcomes')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--accept-run', action='store_true')
    args = parser.parse_args()
    outcomes = args.synthetic_outcomes.split(',') if args.synthetic_outcomes else None
    try:
        result = run(
            args.project,
            args.policy,
            args.output,
            run_id=args.run_id,
            extraction_path=args.extraction,
            model=args.model,
            reasoning=args.reasoning,
            evaluation_id=args.evaluation_id,
            dry_run=args.dry_run,
            accept_run=args.accept_run,
            synthetic_outcomes=outcomes,
        )
    except (ValueError, OSError, KeyError, TypeError) as error:
        print('Scoring stopped: ' + str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
