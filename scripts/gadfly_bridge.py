#!/usr/bin/env python3
"""Gadfly checks on published readings: does a level shown on the workbench rest on the lines it cites?

Gadfly (the `midwife` package, pinned in uv.lock) cuts or changes declared evidence under fixed trials,
has a scorer read each trial, and reads one verdict by fixed rules; no model judges the verdict. Here a
frame names one published level (one world, one question, the reading the release page shows) and is built
from that reading alone:

  anchors       every range the reading's three sessions cite for the question
  cumulative    the anchors plus declared fallback passages, when the round's spec names them
  sham          as many uncited lines, in blocks of the anchors' sizes (a block's kind is its line count)
  presentation  the seed's distinctive words renamed to neutral tokens, in the world only
  control       every range the reading cites on any question

The scorer is the study's own v5 reader: one session per call, through the same pipeline and reading prompt
as the published reading. The battery turns each trial's text back into numbered sources (a removed line
reads blank and keeps its number, as the study's ablation always has), checks every line against the frozen
world, and returns the session's level for the frame's question. A round may instead name Gemma 4 through
eGemma, read by Gadfly's own battery: a second reader of the same evidence, whose level never reaches the page.
A round's spec holds its predictions, written before the first call. A live run needs --accept-run, and a
dollar cap when the study's reader reads.
"""

import argparse
import json
import random
import re
import sys
from pathlib import Path

import atlas
import codex_worker as worker
import evaluation_contract as contracts
import extraction_pipeline
import rubric_policy
import scoring_pipeline
import session_flow
from bundles import load_json, require, sha
from evidence_text import withhold_host_paths

import midwife
from midwife.interventions import plan as plan_trials, prompt as trial_prompt, rename
from midwife.batteries.egemma import EGemma
from midwife.batteries.unified import TRAINS_ON_INPUTS, Unified
from midwife.pipeline import calls_needed, judge, overall, run as run_trials, situate
from midwife.types import Budget, Observation

ROOT = Path(__file__).resolve().parents[1]
KIND = 'after-the-poem-v5'
LABELS = ['level-%d' % level for level in range(5)]
# Gadfly's documented prompt: the spans by line breaks, a `---` line between blank lines, then the input.
SEPARATOR = '\n\n---\n\n'
HEADER = re.compile(r'=== SOURCE (source-\d{4}) · (.*) · (\d+) lines ===')
NUMBERED = re.compile(r'(\d+)\|(.*)')
BATTERY_KEYS = {'kind', 'model', 'reasoning', 'trainsOnInputs', 'midwife', 'router_revision'}
DEFAULT_ROUTER = 'http://127.0.0.1:18766/v1'
# The second reader: Gemma 4 on this machine through eGemma's /judge, read by Gadfly's own eGemma battery.
EGEMMA = 'egemma'
# A second critic: a model through the unified model router, read by Gadfly's own unified battery.
UNIFIED = 'unified'
UNIFIED_ANSWER_TOKENS = 32000  # reasoning included: the router's reasoning models count it as output
READERS = (KIND, EGEMMA, UNIFIED)
DEFAULT_EGEMMA = 'http://127.0.0.1:8000'
EGEMMA_CONTEXT = 32768  # eGemma's default context; it refuses (413), never truncates, a reading that does not fit
EGEMMA_ANSWER_TOKENS = 512
# A cautious count for code and prose (3 characters a token): a world estimated over the context is refused at build.
CHARS_PER_TOKEN = 3.0
READER_INPUT = 'Which level does this world reach? Answer as your instructions say.'
READER_SCHEMA = {
    'type': 'object',
    'properties': {
        'reason': {'type': 'string'},
        'level': {'type': 'string', 'enum': LABELS + ['unresolved']},
    },
    'required': ['reason', 'level'],
    'additionalProperties': False,
}
# The trials a round may declare beside the ablation, which Gadfly always runs. A spec that names none declares
# round 1's set. The control blanks every line the reading cites on any question; with `control: remnant`, every
# line but one uncited block of each cited block's size; with `control: world`, every line, for a reader that
# needs no citation. The last two are floors any reader that reads must move from.
TRIALS = ('repeat', 'sham', 'presentation', 'control')
CONTROL_REACH = ('cited', 'remnant', 'world')
# One session is one turn, and a turn may overrun its cap before the budget check can stop the next.
TURN_MARGIN_USD = 0.03


def cited(evaluation, axes):
    """{source path: set of line numbers} the evaluation's sessions cite for the given questions."""
    lines = {}
    for judgment in evaluation['judgments']:
        if judgment['node_id'] in axes:
            for citation in judgment['citations']:
                lines.setdefault(citation['source_path'], set()).update(
                    range(citation['start'], citation['end'] + 1)
                )
    return lines


def _runs(numbers):
    """Sorted line numbers as contiguous (start, end) pairs."""
    runs = []
    for number in sorted(numbers):
        if runs and number == runs[-1][1] + 1:
            runs[-1][1] = number
        else:
            runs.append([number, number])
    return [tuple(pair) for pair in runs]


def reading_prompt(project, run_id, data):
    """The v5 reading prompt this code gives a session, as the evaluation gateway names it."""
    policy = rubric_policy.build(load_json(Path(project) / 'data/rubric.json'), repetitions=1)
    plan = scoring_pipeline.Plan(project, policy, run_id=run_id, data=data)
    return {'version': plan.prompt_version, 'sha256': sha(plan.tasks(1)[0].prompt.encode('utf-8'))}


def corpus_sha256(data, run_id):
    run = next((item for item in data['runs'] if item['id'] == run_id), None)
    require(run is not None, 'Unknown world: ' + run_id)
    return run['corpus_sha256']


def spec_trials(spec):
    """The trials a round declares beside the ablation, and how far its control reaches."""
    declared = spec.get('trials', list(TRIALS))
    require(
        isinstance(declared, list)
        and declared
        and len(set(declared)) == len(declared)
        and set(declared) <= set(TRIALS),
        'trials lists one or more of: ' + ', '.join(TRIALS),
    )
    reach = spec.get('control', 'cited')
    require(reach in CONTROL_REACH, 'control reaches one of: ' + ', '.join(CONTROL_REACH))
    require('control' in declared or 'control' not in spec, 'A control reach needs a control trial')
    require(
        not spec.get('standing', {}).get('stable') or 'repeat' in declared,
        'A stable standing reads the baseline and repeat sessions together: declare repeat',
    )
    return declared, reach


def promise(declared, reach):
    """What the frame promises of its level, trial by trial; round 1's set reads as round 1 wrote it."""
    held = [
        clause
        for trial, clause in (
            ('presentation', "the seed's words are renamed in the world"),
            ('sham', 'as many uncited lines go'),
        )
        if trial in declared
    ]
    text = 'without the cited lines the level is lower'
    if held:
        text += '; it holds when ' + ' and when '.join(held)
    if 'control' in declared and reach == 'world':
        text += '; with every line of the world blank, the level moves'
    if 'control' in declared and reach == 'remnant':
        text += '; with the world cut to uncited blocks as large as the cited ones, the level moves'
    return text


def remnant_control(spans, anchors, uncited, seed):
    """Every line of the world blank but a remnant: for each anchor, in body order, one uncited block of its
    size, drawn with the published evaluation's hash as the seed. No line any reading cites is left."""
    kinds = {span['id']: span['kind'] for span in spans}
    rng = random.Random(int(seed[:16], 16))
    free, kept = list(uncited), set()
    for anchor in anchors:
        pool = [span_id for span_id in free if kinds[span_id] == kinds[anchor]]
        require(pool, 'No uncited block is left as large as ' + anchor + ', so the remnant cannot match the anchors')
        chosen = rng.choice(pool)
        free.remove(chosen)
        kept.add(chosen)
    return [span['id'] for span in spans if span['kind'] != 'header' and span['id'] not in kept]


def build_frame(project, spec, entry, *, data, router_revision=None):
    """One Gadfly frame for one published level, derived from that reading and the frozen world alone."""
    project = Path(project).resolve()
    evaluation_path = project / 'data/evaluations' / entry['evaluation'] / 'evaluation.json'
    evaluation = load_json(evaluation_path)
    axis, run_id = entry['axis'], evaluation['run_id']
    rubric = load_json(project / 'data/rubric.json')
    question = next((item for item in rubric['axes'] if item['id'] == axis), None)
    require(question is not None, 'Unknown question: ' + str(axis))
    sessions = sorted(
        (j for j in evaluation['judgments'] if j['node_id'] == axis), key=lambda j: j.get('sample', 1)
    )
    require(sessions, entry['evaluation'] + ' has no session for ' + axis)
    level = next(item['outcome'] for item in evaluation['aggregations'] if item['node_id'] == axis + '-consensus')
    require(level in LABELS, entry['evaluation'] + ' shows no level for ' + axis)
    reader = spec['reader']
    require(reader.get('battery') in READERS, 'The spec names no known reader: ' + str(reader.get('battery')))
    declared, reach = spec_trials(spec)
    require(
        not (reader['battery'] == KIND and 'control' in declared and reach == 'world'),
        "The study's reader cites a non-blank line for every level, so with the whole world blank it could give"
        ' none: each control session would end in failed repairs, at a cost, with the verdict known in advance',
    )
    if reader['battery'] == KIND:
        gateway = evaluation['gateway']
        require(
            (gateway['model'], gateway['reasoning']) == (reader['model'], reader['reasoning']),
            entry['evaluation'] + ' was read by another reader than the spec names',
        )
        current = reading_prompt(project, run_id, data)
        require(
            gateway.get('reading_prompt') == current,
            'The reading prompt has changed since ' + entry['evaluation'] + '; a frame would test another reader',
        )

    prepared = extraction_pipeline.prepare_sources(project, run_id, data=data)
    paths = {source['path'] for source in prepared['sources']}
    anchored = cited(evaluation, {axis})
    anywhere = cited(evaluation, {item['id'] for item in rubric['axes']})
    fallback = {}
    for item in entry.get('cumulative', []):
        fallback.setdefault(item['path'], set()).update(range(item['start'], item['end'] + 1))
    require(set(anchored) | set(fallback) <= paths, 'A cited or fallback path is not among the world\'s sources')
    # Uncited stretches are cut to the anchors' sizes, in turn, so every anchor has shams of its own size.
    sizes = [
        end - start + 1 for source in prepared['sources'] for start, end in _runs(anchored.get(source['path'], ()))
    ]
    require(sizes, entry['evaluation'] + ' cites no line for ' + axis)
    turn = 0
    spans, anchors, fallbacks, control, uncited = [], [], [], [], []
    for source in prepared['sources']:
        source_id, lines, path = source['source_id'], source['lines'], source['path']
        spans.append(
            {
                'id': source_id,
                'kind': 'header',
                'text': '=== SOURCE %s · %s · %d lines ===' % (source_id, source['format'] or 'text', len(lines)),
            }
        )
        a, x, c = anchored.get(path, set()), fallback.get(path, set()), anywhere.get(path, set())
        roles = ['a' if n in a else 'x' if n in x else 'c' if n in c else 'u' for n in range(1, len(lines) + 1)]
        start = 1
        while start <= len(lines):
            role, end = roles[start - 1], start
            while end < len(lines) and roles[end] == role:
                end += 1
            block = start
            while block <= end:
                last = end
                if role == 'u':
                    last = min(end, block + sizes[turn % len(sizes)] - 1)
                    turn += 1
                count = last - block + 1
                span = {
                    'id': '%s:%d-%d' % (source_id, block, last),
                    'kind': 'lines-%d' % count if role in 'au' else ('fallback' if role == 'x' else 'cited'),
                    'text': '\n'.join('%d|%s' % (n, lines[n - 1]) for n in range(block, last + 1)),
                }
                spans.append(span)
                if role == 'a':
                    anchors.append(span['id'])
                if role == 'x':
                    fallbacks.append(span['id'])
                if role == 'u':
                    uncited.append(span['id'])
                if c & set(range(block, last + 1)):
                    control.append(span['id'])
                block = last + 1
            start = end + 1

    vocabulary = load_json(project / spec['masked_terms'])
    if reach == 'world':
        control = [span['id'] for span in spans if span['kind'] != 'header']
    if reach == 'remnant':
        control = remnant_control(spans, anchors, uncited, sha(evaluation_path.read_bytes()))
    offered = {
        'repeat': True,
        'sham': True,
        'presentation': {term: 'term-%02d' % index for index, term in enumerate(vocabulary, 1)},
        'control': control,
    }
    # The ablation is always run; midwife hashes the frame with sorted keys, so the order here is free.
    interventions = dict({name: offered[name] for name in declared}, ablation=True)
    if fallbacks:
        interventions['cumulative'] = anchors + fallbacks
    run = next(item for item in data['runs'] if item['id'] == run_id)
    frame = {
        'name': entry['name'],
        'relation': "%s's published %s level (%s) rests on the lines its three sessions cite for that question"
        % (run['label'], question['label'].lower(), level),
        'grain': "a range the sessions cite, or an uncited block of the same file cut to the cited ranges' sizes",
        'purpose': 'show a level on the workbench only as far as the evidence it cites carries it',
        'promise': promise(declared, reach),
        'baseline': {
            'world': run_id,
            'question': axis,
            'published': {
                'evaluation': entry['evaluation'],
                'evaluation_sha256': sha(evaluation_path.read_bytes()),
                'level': level,
                'sessions': [j['outcome'] for j in sessions],
            },
            'corpus_sha256': corpus_sha256(data, run_id),
            'rubric_sha256': data['rubric_sha256'],
            'masked_terms_sha256': sha((project / spec['masked_terms']).read_bytes()),
        },
        'body': {'spans': spans},
        'anchors': anchors,
        'interventions': interventions,
        'allowed': {'ablation': 'lower'},
        'standing': dict(spec['standing']),
        'carriage': spec['carriage'],
        'materialized': spec['materialized'],
    }
    if reader['battery'] == EGEMMA:
        return _egemma_frame(frame, reader, prepared, question, spans)
    if reader['battery'] == UNIFIED:
        return _unified_frame(frame, reader, prepared, question, spans, router_revision)
    battery = {'kind': KIND, 'model': reader['model'], 'reasoning': reader['reasoning'], 'midwife': midwife.__version__}
    if reader['model'].startswith(TRAINS_ON_INPUTS):
        # The contributor tier trains on what it reads; everything it reads here is published under CC BY 4.0.
        battery['trainsOnInputs'] = [reader['model']]
    if router_revision is not None:
        battery['router_revision'] = router_revision
    frame['baseline'].update(reading_prompt=current, pipeline_sha256=extraction_pipeline.code_identity())
    return dict(
        frame,
        locus="the numbered evidence the reader receives: every retained text source of the world, line by line;"
        " the seed, the rubric and the reading prompt are the reader's own and stay whole",
        role="the study's critic, %s at %s reasoning, reading prompt v5, one session per call"
        % (reader['model'], reader['reasoning']),
        trajectory='world -> numbered evidence -> one v5 session -> the level two of three sessions give'
        ' -> the score table and radar on the workbench',
        crossing='the level shown beside the question on the release page',
        inputs=[{'id': axis, 'text': question['question']}],
        observe={
            'labels': LABELS,
            'order': LABELS,
            'axis': axis,
            'instruction': ' '.join('%s: %s' % (LABELS[i], text) for i, text in enumerate(question['anchors'])),
        },
        battery=battery,
    )


def reader_instruction(seed_lines, question):
    """The second reader's whole system message: the seed, the author's declared reading, the v5 rules that
    apply to one answer, and the question with its anchors. The seed stays whole in every trial."""
    return '\n'.join(
        [
            'You read one world for a study of what a poem carries into the worlds that models make from it.',
            'A model received the seed below, with an empty world and an invitation to continue it. The user message',
            'holds what it made, as numbered sources (each line "number|text"), then asks for one level.',
            '',
            '=== SEED · %d lines ===' % len(seed_lines),
            *seed_lines,
            '=== END OF SEED ===',
            '',
            "The reading the study measures, as the seed's author declares it:",
            '',
            *scoring_pipeline.DECLARED_READING,
            '',
            'How to read the world against that reading:',
            "- The seed's vocabulary in the world is not evidence. A relation is carried only if it would survive with",
            "  the seed's words removed, in behaviour, structure or consequence. Beauty, intensity and resonance never",
            '  raise a level.',
            '- Check what the world declares (laws, contracts, roles, consent) against what it enacts.',
            "- Read the intelligence's position operationally (participation, proposal, challenge, refusal under",
            '  declared constraints), never as an inner state. Gestures held by one operator make an instrument, not',
            '  the relation.',
            '- An open question can be an act: credit a question or handoff that reopens who decides the next move,',
            '  keeps earlier work legible and invites replacement with reasons.',
            '- This reading states what the study measures; it is not an answer key. Credit supported alternative',
            '  readings, and do not read every surface of the world through it.',
            '',
            'Question: ' + question['question'],
            'Ordered anchors (choose the closest):',
            *['- %s: %s' % (LABELS[i], anchor) for i, anchor in enumerate(question['anchors'])],
            '- unresolved: the evidence cannot support choosing a level.',
            '',
            'Answer in JSON: "reason", one or two sentences naming the source lines the level rests on, then "level".',
        ]
    )


def _unified_frame(frame, reader, prepared, question, spans, router_revision):
    """The same decision and trials, read by a second critic through the unified model router and Gadfly's
    unified battery: one Responses call per reading, with no tools. Its levels never reach the page."""
    for key in ('model', 'reasoning'):
        require(key in reader, 'A router reader pins its ' + key)
    require(
        router_revision is not None, 'A frame read through the router pins its revision: build with --router-revision'
    )
    instruction = reader_instruction(prepared['seed_lines'], question)
    characters = len(instruction) + sum(len(span['text']) + 1 for span in spans) + len(SEPARATOR + READER_INPUT)
    frame['baseline'].update(
        midwife=midwife.__version__,
        estimated_input_tokens=int(characters / CHARS_PER_TOKEN),
        router_revision=router_revision,
    )
    battery = {
        'kind': UNIFIED,
        'model': reader['model'],
        'reasoning': reader['reasoning'],
        'output': {'schema': READER_SCHEMA, 'label': '/level'},
    }
    if reader['model'].startswith(TRAINS_ON_INPUTS):
        # The contributor tier trains on what it reads; everything it reads here is published under CC BY 4.0.
        battery['trainsOnInputs'] = [reader['model']]
    return dict(
        frame,
        locus='the numbered sources the critic receives after its instructions: every retained text source of the'
        ' world, line by line; the seed and the declared reading are in the instructions and stay whole; a removed'
        ' line is left out, its number with it',
        role='a second critic: %s at %s reasoning through the unified model router at revision %s, one call per'
        ' reading, no tools' % (reader['model'], reader['reasoning'], router_revision[:12]),
        trajectory='world -> numbered sources -> one router call -> the level two of three calls give'
        ' -> a check on the published reading, never shown as a level',
        crossing="the verdict beside the published level in the study's working record",
        inputs=[{'id': question['id'], 'text': READER_INPUT}],
        observe={'labels': LABELS, 'order': LABELS, 'axis': question['id'], 'instruction': instruction},
        battery=battery,
    )


def record_usd(record, pricing):
    """What one kept call cost at the given prices, from the usage its battery reported."""
    usage = record.get('usage') or {}
    return (
        usage.get('input_tokens', 0) * pricing['input_per_million']
        + usage.get('output_tokens', 0) * pricing['output_per_million']
    ) / 1_000_000


def _egemma_frame(frame, reader, prepared, question, spans):
    """The same decision and trials, read by Gemma 4 through Gadfly's eGemma battery: a second reader of the
    evidence the published reading cites. Its levels never reach the page."""
    for key in ('model', 'revision', 'temperature'):
        require(key in reader, 'An eGemma reader pins its ' + key)
    instruction = reader_instruction(prepared['seed_lines'], question)
    characters = len(instruction) + sum(len(span['text']) + 1 for span in spans) + len(SEPARATOR + READER_INPUT)
    estimate = int(characters / CHARS_PER_TOKEN) + EGEMMA_ANSWER_TOKENS
    require(
        estimate <= EGEMMA_CONTEXT,
        '%s needs about %d tokens with its answer; eGemma reads %d' % (frame['name'], estimate, EGEMMA_CONTEXT),
    )
    frame['baseline'].update(midwife=midwife.__version__, estimated_tokens=estimate)
    return dict(
        frame,
        locus='the numbered sources eGemma receives after its instructions: every retained text source of the world,'
        ' line by line; the seed and the declared reading are in the instructions and stay whole; a removed line is'
        ' left out, its number with it',
        role='a second reader: %s at revision %s through eGemma, temperature %s, one call per reading'
        % (reader['model'], reader['revision'][:12], reader['temperature']),
        trajectory='world -> numbered sources -> one eGemma call -> the level two of three calls give'
        ' -> a check on the published reading, never shown as a level',
        crossing="the verdict beside the published level in the study's working record",
        inputs=[{'id': question['id'], 'text': READER_INPUT}],
        observe={'labels': LABELS, 'order': LABELS, 'axis': question['id'], 'instruction': instruction},
        battery={
            'kind': EGEMMA,
            'model': reader['model'],
            'revision': reader['revision'],
            'temperature': reader['temperature'],
            'output': {'schema': READER_SCHEMA, 'label': '/level'},
        },
    )


def trial_evidence(frame, prompt, prepared):
    """A trial's prompt as the study's evidence: {source ID: lines}, a removed line blank, and whether it was renamed.

    Every line must be the frozen world's own line, or, for the whole prompt at once, its renaming under the
    frame's presentation map; anything else is refused."""
    body, separator, question = prompt.rpartition(SEPARATOR)
    require(separator and body and '\n\n' not in body, 'The prompt is not a Gadfly trial (body, `---`, input)')
    mapping = tuple(sorted(frame.interventions.get('presentation', {}).items()))
    sources = {source['source_id']: source for source in prepared['sources']}
    given, current, last = {}, None, 0
    for raw in body.split('\n'):
        header = HEADER.fullmatch(raw)
        if header:
            source_id = header.group(1)
            require(source_id in sources and source_id not in given, 'Unknown or repeated source: ' + source_id)
            source = sources[source_id]
            require(
                int(header.group(3)) == len(source['lines'])
                and header.group(2) in (source['format'] or 'text', rename(source['format'] or 'text', mapping)),
                'A source header does not match the world: ' + source_id,
            )
            given[source_id], current, last = {}, source_id, 0
            continue
        line = NUMBERED.fullmatch(raw)
        require(line is not None and current is not None, 'A line outside a numbered source')
        number = int(line.group(1))
        require(last < number <= len(sources[current]['lines']), 'Line %d of %s is out of place' % (number, current))
        given[current][number], last = line.group(2), number
    require(list(given) == list(sources), 'The trial does not hold every source of the world, in order')
    pairs = [(text, sources[s]['lines'][n - 1]) for s, lines in given.items() for n, text in lines.items()]
    plain = all(text == original for text, original in pairs)
    renamed = bool(mapping) and all(text == rename(original, mapping) for text, original in pairs)
    require(plain or renamed, "A line is not the world's own, nor its renaming under the frame's map")
    inputs = [x['text'] for x in frame.inputs]
    require(
        question in (inputs if plain else [rename(text, mapping) for text in inputs]),
        "The input is not the frame's question",
    )
    lines = {
        source_id: [given[source_id].get(n, '') for n in range(1, len(source['lines']) + 1)]
        for source_id, source in sources.items()
    }
    return lines, not plain


class StudyReader:
    """The study's v5 reader as a Gadfly battery: one session per call, its level for the frame's question.

    `read` replaces the session (tests pass a deterministic one); by default a session runs through the
    scoring pipeline into `out`, live only when the battery is built without `dry_run` and with a budget."""

    name = KIND

    def __init__(
        self,
        project=ROOT,
        out=None,
        *,
        dry_run=False,
        synthetic='level-3',
        budget=None,
        router_url=DEFAULT_ROUTER,
        read=None,
    ):
        self.project = Path(project).resolve()
        self.out = Path(out) if out is not None else None
        self.dry_run = dry_run
        self.synthetic = synthetic
        self.budget = budget
        self.router_url = router_url
        self.read = read or self._session
        self.spent_usd = 0.0
        self._data = None
        self._prepared = {}

    def data(self):
        if self._data is None:
            self._data = atlas.validate(self.project)
        return self._data

    def prepared(self, run_id):
        if run_id not in self._prepared:
            self._prepared[run_id] = extraction_pipeline.prepare_sources(self.project, run_id, data=self.data())
        return self._prepared[run_id]

    def check(self, frame):
        """Refuse, before any call, a frame this reader must not serve."""
        raw, data = frame.raw, self.data()
        battery = raw['battery']
        require(battery.get('kind') == self.name, 'This frame asks for the %s battery' % battery.get('kind'))
        require(set(battery) <= BATTERY_KEYS, 'The reader reads no ' + ', '.join(sorted(set(battery) - BATTERY_KEYS)))
        require(
            battery.get('midwife') == midwife.__version__,
            'The frame was built for midwife %s; this is %s' % (battery.get('midwife'), midwife.__version__),
        )
        model = battery.get('model')
        require(isinstance(model, str) and model, 'The frame names no reader model')
        if model.startswith(TRAINS_ON_INPUTS):
            require(
                model in battery.get('trainsOnInputs', []),
                model + "'s provider trains on what it reads: name it under battery.trainsOnInputs",
            )
        require(battery.get('reasoning') in contracts.REASONING, 'Invalid reasoning effort')
        axes = {item['id'] for item in data['rubric']['axes']}
        observe = raw['observe']
        require(
            observe.get('axis') in axes and list(frame.labels) == LABELS and observe.get('order') == LABELS,
            'The frame must ask one rubric question with the levels 0 to 4, in order',
        )
        baseline, run_id = raw['baseline'], raw['baseline'].get('world')
        require(
            baseline.get('corpus_sha256') == corpus_sha256(data, run_id)
            and baseline.get('rubric_sha256') == data['rubric_sha256']
            and baseline.get('reading_prompt') == reading_prompt(self.project, run_id, data)
            and baseline.get('pipeline_sha256') == extraction_pipeline.code_identity(),
            'The frame pins another world, rubric, reading prompt or pipeline than this code reads',
        )
        self.check_world(frame)
        if not self.dry_run:
            require(self.out is not None and self.budget is not None, 'A live reading needs an output and a budget')
            require('router_revision' in battery, 'A live reading needs a frame that pins the router revision')
            live = worker.router_health(self.router_url, require_openrouter=True)['revision']
            require(
                live == battery['router_revision'],
                'The router is at %s…; the frame pins %s…' % (live[:12], battery['router_revision'][:12]),
            )

    def check_world(self, frame):
        """Refuse a frame whose body is not its world as preserved, line for line, or whose world changed."""
        baseline, data = frame.raw['baseline'], self.data()
        require(
            baseline.get('corpus_sha256') == corpus_sha256(data, baseline.get('world'))
            and baseline.get('rubric_sha256') == data['rubric_sha256'],
            'The frame pins another world or rubric than the study holds',
        )
        prepared = self.prepared(baseline['world'])
        whole = trial_prompt(frame, plan_trials(frame)[0], frame.inputs[0])
        lines, renamed = trial_evidence(frame, whole, prepared)
        require(
            not renamed and lines == {source['source_id']: source['lines'] for source in prepared['sources']},
            "The frame's body is not the world as preserved",
        )

    def observe(self, frame, prompt, max_output_tokens):
        """One session on the trial's evidence: its level for the frame's question, or an opening."""
        try:
            lines, _ = trial_evidence(frame, prompt, self.prepared(frame.raw['baseline']['world']))
        except ValueError as error:
            return Observation(label=None, opening='the trial is not this world: ' + str(error)[:200])
        try:
            seen = self.read(frame, lines, max_output_tokens)
        except (ValueError, OSError, KeyError, TypeError) as error:
            return Observation(label=None, opening='the reading stopped: ' + withhold_host_paths(str(error))[:200])
        extra = {'raw': seen.get('raw', ''), 'usage': seen.get('usage', {}), 'served': seen.get('served', '')}
        if seen['label'] not in LABELS:
            return Observation(label=None, opening='the session gave %s, not a level' % seen['label'], **extra)
        return Observation(label=seen['label'], **extra)

    def _session(self, frame, lines, cap):
        raw = frame.raw
        require(self.out is not None, 'The reader needs an output directory')
        home = self.out / frame.hash[:16]
        home.mkdir(parents=True, exist_ok=True)
        policy = home / 'policy.json'
        if not policy.exists():
            rubric_policy.write(rubric_policy.build(self.data()['rubric'], repetitions=1), policy)
        number = 1
        while True:  # claimed, never shared: parallel slices of a run each get their own call directory
            try:
                (home / ('call-%04d.claimed' % number)).open('x').close()
                break
            except FileExistsError:
                number += 1
        call = home / ('call-%04d' % number)
        battery, run_id, axis = raw['battery'], raw['baseline']['world'], raw['observe']['axis']
        status = scoring_pipeline.run(
            self.project,
            policy,
            call,
            run_id=run_id,
            model=battery['model'],
            reasoning=battery['reasoning'],
            evaluation_id='%s-gadfly-%s-%04d' % (run_id, frame.hash[:12], number),
            dry_run=self.dry_run,
            accept_run=not self.dry_run,
            synthetic_outcomes=[self.synthetic] if self.dry_run else None,
            budget=None if self.dry_run else dict(self.budget, max_output_tokens=cap),
            router_url=self.router_url,
            evidence_lines=lines,
        )
        self.spent_usd += status.get('cost_estimate_usd') or 0.0
        evaluation = load_json(call / 'evaluation.json')
        judgment = next(j for j in evaluation['judgments'] if j['node_id'] == axis)
        revision = evaluation['gateway']['router_revision']
        return {
            'label': judgment['outcome'],
            'raw': judgment['verdict'] + '\n' + judgment['rationale'],
            'usage': {k: v for k, v in status.get('usage', {}).items() if type(v) is int},
            'served': '%s · router %s · evaluation %s'
            % (battery['model'], revision[:12], status['evaluation_sha256'][:12]),
        }


class Synthetic:
    """A dry stand-in for a reader this script does not run itself: a fixed level, no call."""

    def __init__(self, name, label='level-3'):
        self.name, self.label = name, label

    def check(self, frame):
        pass

    def observe(self, frame, prompt, max_output_tokens):
        return Observation(label=self.label, raw='synthetic; no model was called', served='synthetic')


def spent_before(directory, pricing):
    """Dollars already spent by the calls kept under a reader's output (a resumed run counts them)."""
    if pricing is None or not Path(directory).is_dir():
        return 0.0
    total = 0.0
    for call in sorted(Path(directory).glob('*/call-*')):
        if call.is_dir():
            total += session_flow.usage_totals(session_flow.ledger(call), pricing)['estimated_usd']
    return total


def load_records(path):
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def command_build(args):
    spec_path = Path(args.spec).resolve()
    spec, out = load_json(spec_path), Path(args.output)
    require(spec.get('schema_version') == 1, 'Unsupported round spec')
    require(
        spec.get('midwife') == midwife.__version__,
        'The spec names midwife %s; this is %s' % (spec.get('midwife'), midwife.__version__),
    )
    require(not (out / 'round.json').exists(), 'This round is built already; build into a new directory')
    for entry in spec['frames']:
        require({'levels', 'category'} <= set(entry.get('predictions', {})), entry['name'] + ' has no predictions')
    revision = args.router_revision
    if revision == 'live':
        revision = worker.router_health(args.router_url, require_openrouter=True)['revision']
    data = atlas.validate(ROOT)
    manifest = {
        'schema_version': 1,
        'spec': spec['id'],
        'spec_sha256': sha(spec_path.read_bytes()),
        'midwife': midwife.__version__,
        'frames': [],
    }
    (out / 'frames').mkdir(parents=True, exist_ok=True)
    for entry in spec['frames']:
        raw = build_frame(ROOT, spec, entry, data=data, router_revision=revision)
        frame = situate(raw)
        path = out / 'frames' / (entry['name'] + '.json')
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        manifest['frames'].append({'name': entry['name'], 'hash': frame.hash, 'calls': calls_needed(frame)})
    (out / 'round.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    for item in manifest['frames']:
        print('%-28s %s  %d calls' % (item['name'], item['hash'][:16], item['calls']))
    print('%d calls in all' % sum(item['calls'] for item in manifest['frames']))
    return 0


def frames_of(out, names=None):
    manifest = load_json(Path(out) / 'round.json')
    for item in manifest['frames']:
        if names and item['name'] not in names:
            continue
        frame = situate(load_json(Path(out) / 'frames' / (item['name'] + '.json')))
        require(frame.hash == item['hash'], item['name'] + ' differs from the frame this round was built with')
        yield item['name'], frame


def command_plan(args):
    total = 0
    for name, frame in frames_of(args.round, args.frame):
        trials = plan_trials(frame)
        by = {span.id: span for span in frame.spans}
        print('%s (%s): %d calls' % (name, frame.hash[:16], calls_needed(frame)))
        for trial in trials:
            removed = sum(by[i].text.count('\n') + 1 for i in trial.removed)
            change = '%d lines removed' % removed if trial.removed else 'renamed' if trial.renamed else 'as preserved'
            print('  %-12s %s' % (trial.id, change))
        total += calls_needed(frame)
    print('%d calls in all' % total)
    return 0


def command_run(args):
    out = Path(args.round)
    require(args.dry_run or args.accept_run, 'A live run needs --accept-run; --dry-run makes no model call')
    chosen = list(frames_of(out, args.frame))
    kinds = {frame.battery['kind'] for _, frame in chosen}
    budget, pricing = None, None
    if not args.dry_run and kinds & {KIND, UNIFIED}:
        for flag in ('total_usd', 'max_usd_per_call', 'price_input', 'price_cached_input', 'price_output'):
            require(getattr(args, flag) is not None, 'A live run needs --' + flag.replace('_', '-'))
        pricing = {
            'input_per_million': args.price_input,
            'cached_input_per_million': args.price_cached_input,
            'output_per_million': args.price_output,
        }
        budget = {
            'max_turns': 3,
            'max_input_tokens': args.max_input_tokens,
            'max_output_tokens': args.max_output_tokens or 100000,
            'max_usd': args.max_usd_per_call,
            'pricing': pricing,
        } | ({'max_seconds': args.max_seconds} if args.max_seconds else {})
    readings = out / ('dry-readings' if args.dry_run else 'readings')
    records = out / ('dry-records' if args.dry_run else 'records')
    records.mkdir(parents=True, exist_ok=True)
    reader = StudyReader(ROOT, readings, dry_run=args.dry_run, budget=budget, router_url=args.router_url)
    reader.spent_usd = spent_before(readings, pricing)
    per_call = (args.max_usd_per_call or 0.0) + TURN_MARGIN_USD
    # The router's calls are counted from the usage each record reports; earlier calls in this round count too.
    routed = {'spent': 0.0, 'reserve': 0.0}
    if pricing is not None:
        routed['spent'] = sum(
            record_usd(record, pricing)
            for name, frame in frames_of(out)
            if frame.battery['kind'] == UNIFIED
            for record in load_records(records / (name + '.jsonl'))
        )

    def cancelled():
        if pricing is None:
            return False
        if routed['reserve']:
            return reader.spent_usd + routed['spent'] + routed['reserve'] > args.total_usd
        return reader.spent_usd + routed['spent'] + per_call > args.total_usd

    status = 0
    for name, frame in chosen:
        if frame.battery['kind'] == EGEMMA:
            reader.check_world(frame)
            battery = (
                Synthetic(EGEMMA)
                if args.dry_run
                else EGemma(args.egemma_url.rstrip('/') + '/judge', timeout=args.egemma_timeout)
            )
            cap = args.max_output_tokens or EGEMMA_ANSWER_TOKENS
        elif frame.battery['kind'] == UNIFIED:
            reader.check_world(frame)
            cap = args.max_output_tokens or UNIFIED_ANSWER_TOKENS
            if args.dry_run:
                battery = Synthetic(UNIFIED)
            else:
                live = worker.router_health(args.router_url, require_openrouter=True)['revision']
                pinned = frame.raw['baseline']['router_revision']
                require(live == pinned, 'The router is at %s…; %s pins %s…' % (live[:12], name, pinned[:12]))
                battery = Unified(args.router_url.rstrip('/') + '/responses', timeout=args.unified_timeout)
                # Before each call: the most it can cost, its whole input and a full answer.
                routed['reserve'] = record_usd(
                    {
                        'usage': {
                            'input_tokens': frame.raw['baseline']['estimated_input_tokens'],
                            'output_tokens': cap,
                        }
                    },
                    pricing,
                )
        else:
            battery, cap = reader, args.max_output_tokens or 100000
            routed['reserve'] = 0.0
        path = records / (name + '.jsonl')
        kept = load_records(path)
        with path.open('a', encoding='utf-8') as sink:

            def keep(record, sink=sink):
                sink.write(json.dumps(record.as_dict(), ensure_ascii=False) + '\n')
                sink.flush()
                if pricing is not None and record.battery == UNIFIED:
                    routed['spent'] += record_usd(record.as_dict(), pricing)

            result = run_trials(
                frame,
                battery,
                Budget(calls=calls_needed(frame), output_tokens_per_call=cap),
                cancelled=cancelled,
                on_record=keep,
                only=args.only,
                kept=kept,
            )
        openings = [r.opening for r in result.records if r.opening is not None]
        print(
            '%-28s %d/%d calls made%s%s'
            % (
                name,
                len(result.records),
                result.planned,
                ', %d openings' % len(openings) if openings else '',
                '; stopped: ' + result.stopped if result.stopped else '',
            )
        )
        if result.stopped:
            status = 1
            break
    if pricing is not None:
        print('Estimated spend so far: $%.4f' % (reader.spent_usd + routed['spent']))
    return status


def command_report(args):
    out = Path(args.round)
    records = out / ('dry-records' if args.dry_run else 'records')
    spec = load_json(ROOT / args.spec) if args.spec else None
    predicted = {entry['name']: entry['predictions'] for entry in spec['frames']} if spec else {}
    # A reading names the rules that read it: another midwife release reads the same records into its own file.
    document = {
        'round': load_json(out / 'round.json')['spec'],
        'midwife': midwife.__version__,
        'dry_run': args.dry_run,
        'frames': [],
    }
    for name, frame in frames_of(out, args.frame):
        kept = load_records(records / (name + '.jsonl'))
        if not kept:
            print('%s: no records yet' % name)
            continue
        findings = judge(frame, kept)
        verdict = overall(findings)
        print('%s (%s): %s' % (name, frame.hash[:16], verdict.category))
        guess = predicted.get(name, {})
        for trial, level in findings[0].levels.items():
            expected = guess.get('levels', {}).get(trial)
            print('  %-12s %-8s%s' % (trial, level or '—', ('  predicted ' + expected) if expected else ''))
        if guess:
            print('  predicted category: %s' % guess['category'])
        for line in verdict.evidence:
            print('  evidence: ' + line)
        for line in verdict.obligations:
            print('  open: ' + line)
        document['frames'].append(
            {
                'name': name,
                'hash': frame.hash,
                'records': len(kept),
                'overall': verdict.as_dict(),
                'findings': [finding.as_dict() for finding in findings],
                'predicted': guess or None,
            }
        )
    target = out / ('%sreport-midwife-%s.json' % ('dry-' if args.dry_run else '', midwife.__version__))
    target.write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return 0


EXAMPLES = """\
examples (each command but a live run makes no model call):
  python3 scripts/gadfly_bridge.py build examples/gadfly/round-1.json --output .local/gadfly/round-1
  python3 scripts/gadfly_bridge.py plan .local/gadfly/round-1
  python3 scripts/gadfly_bridge.py run .local/gadfly/round-1 --dry-run --only baseline
  python3 scripts/gadfly_bridge.py report .local/gadfly/round-1 --spec examples/gadfly/round-1.json --dry-run

A live run reads with the study's v5 reader and spends money. Build its frames against the live router,
then run with --accept-run, a total cap, a per-call cap and the reader's prices:
  python3 scripts/gadfly_bridge.py build examples/gadfly/round-1.json --output .local/gadfly/round-1-live \\
    --router-revision live
  python3 scripts/gadfly_bridge.py run .local/gadfly/round-1-live --accept-run --total-usd 1.00 \\
    --max-usd-per-call 0.03 --price-input 0.10 --price-cached-input 0.002 --price-output 0.20
"""


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__.split('\n\n')[0], epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest='command', required=True)
    build = commands.add_parser('build', help='build a round\'s frames from its spec (no call)')
    build.add_argument('spec')
    build.add_argument('--output', required=True)
    build.add_argument('--router-revision', help='pin this router revision, or `live` to read it from the router')
    build.add_argument('--router-url', default=DEFAULT_ROUTER)
    build.set_defaults(func=command_build)
    show = commands.add_parser('plan', help='each frame\'s trials and calls (no call)')
    show.add_argument('round')
    show.add_argument('--frame', action='append')
    show.set_defaults(func=command_plan)
    execute = commands.add_parser('run', help='read every planned trial; resumes from kept records')
    execute.add_argument('round')
    execute.add_argument('--frame', action='append')
    execute.add_argument('--only', action='append', help='a trial id; may repeat')
    execute.add_argument('--dry-run', action='store_true', help='synthetic sessions, no model call')
    execute.add_argument('--accept-run', action='store_true')
    execute.add_argument('--total-usd', type=float)
    execute.add_argument('--max-usd-per-call', type=float)
    execute.add_argument('--price-input', type=float)
    execute.add_argument('--price-cached-input', type=float)
    execute.add_argument('--price-output', type=float)
    execute.add_argument(
        '--max-output-tokens',
        type=int,
        help='answer cap per call (default: 100000 for the v5 reader, 32000 through the router, 512 for eGemma)',
    )
    execute.add_argument('--max-input-tokens', type=int, default=400000)
    execute.add_argument('--max-seconds', type=int)
    execute.add_argument('--router-url', default=DEFAULT_ROUTER)
    execute.add_argument('--egemma-url', default=DEFAULT_EGEMMA)
    execute.add_argument('--egemma-timeout', type=float, default=600.0, help='seconds for one eGemma reading')
    execute.add_argument(
        '--unified-timeout', type=float, default=900.0, help='seconds for one reading through the router'
    )
    execute.set_defaults(func=command_run)
    report = commands.add_parser('report', help='read the verdicts from kept records (no call)')
    report.add_argument('round')
    report.add_argument('--frame', action='append')
    report.add_argument('--spec', help='the round spec, to show predictions beside the levels')
    report.add_argument('--dry-run', action='store_true')
    report.set_defaults(func=command_report)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, OSError, KeyError) as error:
        print('Gadfly bridge stopped: ' + withhold_host_paths(str(error)), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
