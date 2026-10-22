#!/usr/bin/env python3
"""Validate the study, build its site and serve it locally.

The site is built into dist/: the home target for local viewing, with opt-in
native previews, and the pages target for the static site. Validation and the
build read the preserved worlds and never run them.
"""

import argparse
import copy
import csv
import hashlib
import html
import http.server
import io
import json
import os
import re
import shutil
import sys
import textwrap
import unicodedata
from pathlib import Path, PurePosixPath
from urllib.parse import quote
from evidence_text import extract_readable
from markdown_view import render_markdown, source_link

ROOT = Path(__file__).resolve().parents[1]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def safe_id(value):
    return isinstance(value, str) and re.fullmatch(r'[a-z0-9][a-z0-9-]{0,99}', value) is not None


def safe_path(value):
    return (
        isinstance(value, str)
        and value
        and not PurePosixPath(value).is_absolute()
        and '..' not in PurePosixPath(value).parts
        and '\\' not in value
        and not re.search(r'[\x00-\x1f\x7f]', value)
        and str(PurePosixPath(value)) == value
        and unicodedata.is_normalized('NFC', value)
    )


def validate_evaluation_run(run, source, *, package_id, rubric):
    """Validate one evaluation-run projection that reuses frozen artefact bytes."""
    require(run.get('schema_version') == 1, 'Unsupported evaluation run schema')
    fields = {
        'schema_version',
        'id',
        'source_run_id',
        'evaluation_id',
        'cohort',
        'label',
        'title',
        'model',
        'model_identity_basis',
        'effort',
        'date',
        'cli_version',
        'elapsed_seconds',
        'usage',
        'prompt_sha256',
        'corpus_sha256',
        'protocol',
        'condition',
        'evidence_scope',
    }
    require(set(run) == fields, 'Invalid evaluation run fields')
    require(safe_id(run['id']), 'Invalid evaluation run ID')
    require(run['source_run_id'] == source['id'], 'Evaluation run names the wrong source')
    require(run['evaluation_id'] == package_id, 'Evaluation run identity differs from its package')
    require(run['cohort'] == source['cohort'], 'Evaluation run changed its source cohort')
    require(run['prompt_sha256'] == source['prompt_sha256'], 'Evaluation run prompt differs from its source')
    require(run['corpus_sha256'] == source['corpus_sha256'], 'Evaluation run corpus differs from its source')
    for field in [
        'label',
        'title',
        'model',
        'model_identity_basis',
        'effort',
        'cli_version',
        'protocol',
        'evidence_scope',
    ]:
        require(isinstance(run[field], str) and run[field].strip(), 'Evaluation run needs ' + field)
    require(re.fullmatch(r'\d{4}-\d{2}-\d{2}', run['date']), 'Evaluation run needs a date')
    require(
        run['elapsed_seconds'] is None or type(run['elapsed_seconds']) in [int, float] and run['elapsed_seconds'] >= 0,
        'Invalid evaluation run duration',
    )
    require(isinstance(run['usage'], dict), 'Evaluation run usage must be explicit')
    require(run['condition'] == 'evaluation', 'Evaluation run condition must be evaluation')
    require(run['effort'] in ['none', 'low', 'medium', 'high', 'xhigh', 'max'], 'Invalid evaluation run effort')
    return run


def source_url(run_id, path, start=1):
    return 'sources/' + run_id + '/' + quote(path, safe='/') + '.html#L' + str(start)


def validate_review(review, runs, corpora, rubric, rubric_hash, prompt):
    require(review.get('schema_version') == 1, 'Unsupported review schema')
    require(safe_id(review.get('id')), 'Invalid review ID')
    run_id = review.get('run_id')
    require(run_id in runs, 'Unknown reviewed run')
    require(
        review.get('rubric_version') == rubric['version'] and review.get('rubric_sha256') == rubric_hash,
        'Review rubric identity mismatch',
    )
    require(review.get('corpus_sha256') == runs[run_id]['corpus_sha256'], 'Review corpus identity mismatch')
    require(review.get('status') in ['unreviewed', 'provisional', 'reviewed'], 'Invalid review status')
    require(review.get('basis') == 'static-artifact', 'This rubric evaluates retained artefacts')
    require(isinstance(review.get('reviewer'), str) and review['reviewer'].strip(), 'A reviewer is required')
    require(re.fullmatch(r'\d{4}-\d{2}-\d{2}', review.get('date', '')), 'A review date is required')
    require(isinstance(review.get('summary'), str) and review['summary'].strip(), 'Review summary required')
    require(review.get('supersedes') is None or safe_id(review.get('supersedes')), 'Invalid predecessor identity')
    require(review.get('live_cooperation') == 'unassessed', 'Live cooperation requires a separate protocol')
    sources = {f['path']: f for f in corpora[run_id]['files']}
    scores = review.get('scores', {})
    require(set(scores) == {a['id'] for a in rubric['axes']}, 'Review must name every rubric axis')
    for axis, score in scores.items():
        value = score.get('value')
        require(
            value is None or type(value) in [int, float] and value in rubric['scale'],
            'Score must use the declared rubric scale or be null',
        )
        require(score.get('confidence') in ['low', 'medium', 'high', 'unassessed'], 'Invalid confidence')
        require(isinstance(score.get('rationale'), str) and score['rationale'].strip(), 'Every axis needs a rationale')
        citations = score.get('citations', [])
        require(isinstance(citations, list) and (value is None or len(citations) > 0), 'Scored axes need evidence')
        require(
            value is not None or score['confidence'] == 'unassessed', 'Unassessed scores need unassessed confidence'
        )
        require(value is None or score['confidence'] != 'unassessed', 'Scored axes need confidence')
        for cite in citations:
            path = cite.get('path')
            require(path in sources and sources[path]['text'] is not None, 'Unknown or omitted evidence source')
            lines = sources[path]['text'].splitlines()
            start, end = cite.get('start'), cite.get('end')
            require(
                type(start) is int and type(end) is int and 1 <= start <= end <= len(lines),
                'Invalid citation line range',
            )
            excerpt = '\n'.join(lines[start - 1 : end])
            require(cite.get('quote') == excerpt, 'Citation quote differs from retained source')
        span = score.get('seed_span')
        require(
            isinstance(span, list)
            and len(span) == 2
            and all(type(x) is int for x in span)
            and 1 <= span[0] <= span[1] <= len(prompt.splitlines()),
            'Every axis needs a valid seed span',
        )
        if review['status'] == 'unreviewed':
            require(
                value is None and score['confidence'] == 'unassessed' and not citations,
                'Unreviewed records cannot award levels or cite interpreted evidence',
            )


def validate(root=ROOT, extra_review=None, selection_override=None):
    registry = load(root / 'data/runs.json')
    require(registry.get('schema_version') == 2, 'Unsupported run registry schema')
    rubric = load(root / 'data/rubric.json')
    require(rubric.get('version') == '0.1.0', 'Unsupported rubric version')
    scale = rubric.get('scale')
    require(
        isinstance(scale, list)
        and 2 <= len(scale) <= 21
        and all(type(value) in [int, float] for value in scale)
        and len(set(scale)) == len(scale)
        and scale == sorted(scale),
        'Rubric scale must be an ascending list of 2–21 unique numbers',
    )
    axes = [a['id'] for a in rubric['axes']]
    require(1 <= len(axes) <= 32 and len(set(axes)) == len(axes), 'Rubric axes must be unique and bounded')
    for axis in rubric['axes']:
        require(safe_id(axis['id']) and len(axis['anchors']) == len(scale), 'Invalid rubric axis')
    rubric_hash = sha(canonical(rubric))
    cohorts = {}
    cohort_prompts = {}
    for cohort in registry['cohorts']:
        cid = cohort['id']
        require(safe_id(cid) and cid not in cohorts, 'Cohort IDs must be safe and unique')
        require(isinstance(cohort.get('title'), str) and cohort['title'].strip(), 'Cohort title required')
        digest = cohort.get('prompt_sha256', '')
        require(re.fullmatch(r'[a-f0-9]{64}', digest), 'Invalid cohort prompt identity')
        prompt_bytes = (root / 'corpus/prompts' / (digest + '.txt')).read_bytes()
        require(sha(prompt_bytes) == digest, 'Cohort prompt hash mismatch')
        cohort_prompts[cid] = prompt_bytes.decode('utf-8')
        cohorts[cid] = dict(cohort)
    for cid, cohort in cohorts.items():
        if 'seed_of' in cohort:
            validate_shared_seed(cid, cohort, cohorts, cohort_prompts)
    runs = {}
    corpora = {}
    for run in registry['runs']:
        rid = run['id']
        require(safe_id(rid) and rid not in runs, 'Run IDs must be safe and unique')
        cid = run.get('cohort')
        require(cid in cohorts, 'Unknown run cohort')
        require(run['corpus'] == 'corpus/runs/' + rid + '.json', 'Unexpected corpus path')
        cp = root / run['corpus']
        require(sha(cp.read_bytes()) == run['corpus_sha256'], 'Corpus hash mismatch: ' + rid)
        corpus = load(cp)
        require(corpus['run_id'] == rid, 'Corpus run mismatch')
        require(corpus['prompt_sha256'] == run['prompt_sha256'], 'Corpus prompt mismatch')
        require(run['prompt_sha256'] == cohorts[cid]['prompt_sha256'], 'Run prompt differs from its cohort')
        seen = set()
        for f in corpus['files']:
            folded = unicodedata.normalize('NFC', f.get('path', '')).casefold()
            require(safe_path(f['path']) and folded not in seen, 'Unsafe or duplicate source path')
            seen.add(folded)
            require(re.fullmatch('[a-f0-9]{64}', f['sha256']), 'Invalid source digest')
            require(type(f['bytes']) is int and f['bytes'] >= 0, 'Invalid source size')
            if f['text'] is not None:
                payload = f['text'].encode('utf-8')
                require(
                    len(payload) == f['bytes'] and sha(payload) == f['sha256'], 'Source hash mismatch: ' + f['path']
                )
            else:
                require(bool(f['omission']), 'Missing projection reason')
        runs[rid], corpora[rid] = run, corpus
    for cid, cohort in cohorts.items():
        members = [rid for rid, run in runs.items() if run['cohort'] == cid]
        require(bool(members), 'Every cohort needs at least one run')
        require(cohort.get('baseline') in members, 'Each cohort needs a baseline in that cohort')
        require(cohort.get('default_run') in members, 'Each cohort needs a default run in that cohort')
        cohort['runs'] = members
    baseline = cohorts[registry['cohorts'][0]['id']]['baseline']
    reviews = [load(p) for p in sorted((root / 'data/reviews').glob('*.json'))]
    if extra_review is not None:
        reviews.append(extra_review)
    selection_record = load(root / 'data/review-selection.json')
    require(selection_record.get('schema_version') == 1, 'Unsupported review-selection schema')
    base_selections = dict(selection_record.get('selections', {}))
    evaluation_reviews = []
    evaluation_selections = {}
    evaluation_packages = []
    evaluation_orders = set()
    evaluation_root = root / 'data/evaluations'
    if evaluation_root.exists():
        require(
            evaluation_root.is_dir() and not evaluation_root.is_symlink(), 'Evaluation store must be a real directory'
        )
        for directory in sorted(evaluation_root.iterdir(), key=lambda item: item.name):
            if directory.name.startswith('.'):
                continue
            require(
                safe_id(directory.name) and directory.is_dir() and not directory.is_symlink(),
                'Invalid evaluation package directory',
            )
            review_path = directory / 'review.json'
            selection_path = directory / 'selection.json'
            run_path = directory / 'run.json'
            require(
                review_path.is_file() and not review_path.is_symlink(),
                'Evaluation package needs review.json: ' + directory.name,
            )
            require(
                selection_path.is_file() and not selection_path.is_symlink(),
                'Evaluation package needs selection.json: ' + directory.name,
            )
            require(
                run_path.is_file() and not run_path.is_symlink(), 'Evaluation package needs run.json: ' + directory.name
            )
            review = load(review_path)
            selection = load(selection_path)
            run_record = load(run_path)
            require(
                selection.get('schema_version') == 3
                and set(selection)
                == {'schema_version', 'evaluation_id', 'source_run_id', 'run_id', 'review_id', 'order'},
                'Invalid evaluation package selection: ' + directory.name,
            )
            require(
                selection['evaluation_id'] == directory.name
                and selection['run_id'] == run_record.get('id')
                and selection['source_run_id'] == run_record.get('source_run_id')
                and selection['review_id'] == review.get('id'),
                'Evaluation package selection differs from its records: ' + directory.name,
            )
            require(
                type(selection['order']) is int
                and selection['order'] >= 1
                and selection['order'] not in evaluation_orders,
                'Evaluation package order must be unique and positive: ' + directory.name,
            )
            evaluation_orders.add(selection['order'])
            source = runs.get(run_record.get('source_run_id'))
            require(source is not None, 'Evaluation run names an unknown source run')
            validate_evaluation_run(run_record, source, package_id=directory.name, rubric=rubric)
            evaluation_reviews.append(review)
            evaluation_packages.append((selection['order'], directory, run_record, source, review))
    evaluation_packages.sort(key=lambda item: item[0])
    for _, directory, run_record, source, review in evaluation_packages:
        display_run = dict(run_record, corpus=source['corpus'])
        require(display_run['id'] not in runs, 'Evaluation run ID duplicates an existing run')
        runs[display_run['id']] = display_run
        corpora[display_run['id']] = corpora[source['id']]
        evaluation_selections[display_run['id']] = review['id']
        cohorts[display_run['cohort']]['runs'].append(display_run['id'])
    reviews.extend(evaluation_reviews)
    ids = {}
    for review in reviews:
        run = runs.get(review.get('run_id'))
        prompt = cohort_prompts.get(run.get('cohort'), '') if run else ''
        validate_review(review, runs, corpora, rubric, rubric_hash, prompt)
        require(review['id'] not in ids, 'Duplicate review ID')
        ids[review['id']] = review
    for _, directory, display_run, source_run, review in evaluation_packages:
        source_texts = {
            source['path']: source['text']
            for source in corpora[source_run['id']]['files']
            if source['text'] is not None
        }
        validate_evaluation_package(
            directory, display_run, source_run, corpora[source_run['id']], rubric, source_texts, review
        )
    successors = {}
    for review in reviews:
        parent = review.get('supersedes')
        if parent:
            require(parent in ids and ids[parent]['run_id'] == review['run_id'], 'Unknown or wrong-run predecessor')
            require(parent not in successors, 'A review may have only one correction successor')
            successors[parent] = review['id']
    for review in reviews:
        cursor, chain = review, set()
        while cursor.get('supersedes'):
            require(cursor['id'] not in chain, 'Review supersession cycle')
            chain.add(cursor['id'])
            cursor = ids[cursor['supersedes']]
    # The global file selects readings for registered runs only. Every package
    # run selects its own review, so deleting the package folder removes it.
    require(
        not set(base_selections) & set(evaluation_selections),
        'Evaluation package runs select their own review; remove them from data/review-selection.json',
    )
    if selection_override is not None:
        # Callers may pass a full effective selection; package entries must name the package's own review.
        require(
            all(
                selection_override[rid] == review_id
                for rid, review_id in evaluation_selections.items()
                if rid in selection_override
            ),
            'Evaluation package runs select their own review',
        )
        base_selections = {
            rid: review_id for rid, review_id in selection_override.items() if rid not in evaluation_selections
        }
    selections = dict(base_selections)
    selections.update(evaluation_selections)
    require(set(selections) == set(runs), 'Every run needs an explicit selected reading')
    for rid, review_id in selections.items():
        require(
            review_id in ids and ids[review_id]['run_id'] == rid,
            'Selected reading is missing or belongs to another run',
        )
        require(review_id not in successors, 'Selected reading must be the current leaf of its correction lineage')
    active = [ids[review_id] for review_id in selections.values()]
    baseline_review_id = registry.get('baseline_review')
    require(
        baseline_review_id in ids and ids[baseline_review_id]['run_id'] == baseline,
        'The historical baseline review is missing or belongs to another run',
    )
    observations = load(root / 'data/observations.json')
    for _, _, display_run, source_run, _ in evaluation_packages:
        source_observation = copy.deepcopy(observations[source_run['id']])
        source_observation.update(
            kind='evaluation run over the retained artefact',
            summary=(
                'This independent evaluation run reuses the frozen artefact from '
                + source_run['id']
                + '. '
                + source_observation['summary']
            ),
        )
        observations[display_run['id']] = source_observation
    require(set(observations) == set(runs), 'Each run needs an observation status')
    evidence_registry = load(root / 'data/evidence-sources.json')
    require(evidence_registry.get('schema_version') == 1, 'Unsupported evidence-source registry')
    evidence_sources = evidence_registry.get('sources', {})
    for rid, observation in observations.items():
        require(observation.get('live_cooperation') == 'unassessed', 'Live observations need a separate protocol')
        evidence = observation.get('evidence', {})
        require(
            re.fullmatch('[a-f0-9]{64}', evidence.get('source_sha256', '')),
            'Observation needs a parent record identity',
        )
        require(bool(evidence.get('text')) and bool(evidence.get('locator')), 'Observation needs a retained passage')
        source = evidence_sources.get(evidence['source_sha256'])
        require(isinstance(source, dict), 'Observation parent is missing from the evidence-source registry')
        require(
            type(source.get('retained_in_repo')) is bool and source.get('scope') and source.get('note'),
            'Observation provenance is incomplete',
        )
        if source['retained_in_repo']:
            matches = [
                f
                for f in corpora[rid]['files']
                if f['path'] == source.get('path') and f['sha256'] == evidence['source_sha256']
            ]
            repository_path = source.get('path', '')
            retained_file = root / repository_path if safe_path(repository_path) else None
            repository_match = (
                retained_file is not None
                and retained_file.is_file()
                and sha(retained_file.read_bytes()) == evidence['source_sha256']
            )
            require(len(matches) == 1 or repository_match, 'Retained observation source is missing or changed')
        else:
            require(source.get('path') is None, 'Unretained observation source cannot claim a repository path')
        evidence['parent_retained'] = source['retained_in_repo']
        evidence['scope'] = source['scope']
        evidence['provenance_note'] = source['note']
    artefacts = load(root / 'data/artefacts.json')
    for _, _, display_run, source_run, _ in evaluation_packages:
        source_artefact = copy.deepcopy(artefacts[source_run['id']])
        if source_artefact.get('kind') == 'browser':
            source_artefact['preview'] = 'previews/' + display_run['id'] + '/index.html'
        artefacts[display_run['id']] = source_artefact
    require(set(artefacts) == set(runs), 'Each run needs an artefact entry')
    for rid, entry in artefacts.items():
        sources = {f['path'] for f in corpora[rid]['files'] if f['text'] is not None}
        require(entry['kind'] in ['browser', 'server', 'console', 'none'], 'Invalid preview kind')
        require(all(p in sources for p in entry['documents']), 'Unknown artefact document')
        require(
            entry.get('medium') is None or isinstance(entry['medium'], str) and entry['medium'].strip(),
            'An artefact medium must be a name',
        )
        # A server that forbids framing (frame-ancestors 'none') opens in its own tab, unmodified.
        require(
            entry.get('window', 'frame') == 'frame' or (entry['window'] == 'tab' and entry['kind'] == 'server'),
            'Only a server artefact can open in its own tab',
        )
        if entry['kind'] == 'browser':
            from previews import browser_preview_sources

            browser_preview_sources(rid, entry, corpora[rid])
        for picture in entry['images']:
            require(safe_path(picture['path']) and picture['path'].startswith('assets/artefacts/'), 'Unsafe image path')
            require(sha((root / picture['path']).read_bytes()) == picture['sha256'], 'Artefact image hash mismatch')
    withdrawals = validate_withdrawals(root, runs)
    recordings = validate_recordings(root, runs, artefacts)
    result = dict(
        rubric=rubric,
        rubric_sha256=rubric_hash,
        baseline=baseline,
        baseline_review_id=baseline_review_id,
        cohorts=list(cohorts.values()),
        cohort_prompts=cohort_prompts,
        runs=list(runs.values()),
        corpora=corpora,
        reviews=reviews,
        selections=selections,
        base_selections=base_selections,
        evaluation_reviews=evaluation_reviews,
        evaluation_packages={display_run['id']: directory for _, directory, display_run, _, _ in evaluation_packages},
        active_reviews={r['run_id']: r for r in active},
        evidence_sources=evidence_sources,
        observations=observations,
        artefacts=artefacts,
        withdrawals=withdrawals,
        recordings=recordings,
    )
    return result


def validate_recordings(root, runs, artefacts):
    """Recorded console answers for the static site, made by scripts/record_consoles.py; presentation only."""
    folder = root / 'data/recordings'
    recordings = {}
    if not folder.is_dir():
        return recordings
    for path in sorted(folder.glob('*.json')):
        record = load(path)
        require(
            isinstance(record, dict)
            and set(record) == {'schema_version', 'run_id', 'corpus_sha256', 'recorded_on', 'runner', 'entries'}
            and record['schema_version'] == 1,
            'Unsupported console recording: ' + path.name,
        )
        rid = record['run_id']
        require(
            path.stem == rid and rid in runs and runs[rid]['corpus_sha256'] == record['corpus_sha256'],
            'A recording names another world or corpus: ' + path.name,
        )
        require(artefacts.get(rid, {}).get('kind') == 'console', 'Only console worlds are recorded: ' + rid)
        require(
            isinstance(record['recorded_on'], str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', record['recorded_on']),
            'A recording needs its date',
        )
        require(isinstance(record['runner'], str) and record['runner'].strip(), 'A recording names its runner')
        entries = record['entries']
        require(isinstance(entries, list) and 1 <= len(entries) <= 64, 'A recording holds 1–64 answers: ' + rid)
        for entry in entries:
            require(
                isinstance(entry, dict)
                and set(entry) == {'command', 'output', 'exit_code'}
                and isinstance(entry['command'], str)
                and 0 < len(entry['command']) <= 2048
                and isinstance(entry['output'], str)
                and len(entry['output']) <= 65536
                and type(entry['exit_code']) is int,
                'Invalid recorded answer in ' + rid,
            )
        from bundles import inspect_metadata  # no host paths or credential-like keys in a published recording

        inspect_metadata(record, 'recording ' + rid)
        recordings[rid] = record
    return recordings


def harness_line_difference(line, other):
    """The differing words of two first lines that share their wording around one short harness name, or None."""
    a, b = line.split(' '), other.split(' ')
    head = 0
    while head < min(len(a), len(b)) and a[head] == b[head]:
        head += 1
    tail = 0
    while tail < min(len(a), len(b)) - head and a[len(a) - 1 - tail] == b[len(b) - 1 - tail]:
        tail += 1
    names = [' '.join(a[head : len(a) - tail]), ' '.join(b[head : len(b) - tail])]
    if head + tail >= 3 and all(
        re.fullmatch(r'[A-Za-z][A-Za-z0-9.-]*(?: [A-Za-z0-9.-]+){0,2}', name) for name in names
    ):
        return names
    return None


def validate_shared_seed(cid, cohort, cohorts, prompts):
    """A cohort may share another's seed: the same poem and invitation, with only the harness named in line 1 changed."""
    parent = cohort['seed_of']
    require(
        parent in cohorts and parent != cid and 'seed_of' not in cohorts[parent],
        'A shared seed names an unknown or nested cohort: ' + cid,
    )
    first, rest = (prompts[cid].split('\n', 1) + [''])[:2]
    parent_first, parent_rest = (prompts[parent].split('\n', 1) + [''])[:2]
    require(rest == parent_rest, 'A shared seed may differ only in its first line: ' + cid)
    require(
        first != parent_first and harness_line_difference(first, parent_first),
        'A shared seed may change only the harness named in its first line: ' + cid,
    )


def reading_details(package, rubric):
    """Per question, what the page shows beside a level: how many independent sessions gave it, every session's
    level, the reader's own confidence, and its case for the levels on either side.

    The review record keeps one merged confidence and a one-sentence verdict; these come from the same sessions.
    The representative is chosen as the review projection chooses it: the shortest verdict among samples that
    gave the level."""
    evaluation = json.loads((package / 'evaluation.json').read_text(encoding='utf-8'))
    aggregates = {item['node_id']: item for item in evaluation['aggregations']}
    result = {}
    for axis in rubric['axes']:
        samples = [item for item in evaluation['judgments'] if item['node_id'] == axis['id']]
        aggregate = aggregates.get(axis['id'] + '-consensus')
        if not samples or aggregate is None or not aggregate['outcome'].startswith('level-'):
            continue
        matching = [item for item in samples if item['outcome'] == aggregate['outcome']]
        representative = min(matching, key=lambda item: (len(item['verdict']), item['sample']))
        other = re.split(r'\bOther levels:\s*', representative.get('rationale', ''), maxsplit=1)
        levels = sorted(
            int(item['outcome'].split('-')[1]) for item in samples if re.fullmatch(r'level-\d+', item['outcome'])
        )
        result[axis['id']] = {
            'agreeing': len(matching),
            'sessions': len(samples),
            'levels': levels,
            'reader_confidence': representative.get('confidence'),
            'other_levels': other[1].strip() if len(other) == 2 and other[1].strip() else None,
        }
    return result


GESTURE_ORDER = ['question', 'constraint', 'invariant', 'boundary', 'rehearsal', 'receipt', 'accept', 'withdraw']
STANDING_ORDER = ['person', 'intelligence', 'successor', 'material', 'other']


def standing_summary(package):
    """Who can do what in a world, as the reader saw it: the gestures each kind of participant holds in at least
    two of three sessions (a majority of the sessions in general). The intelligence is always listed."""
    path = package / 'carriage.json'
    if not path.is_file():
        return None
    readings = [
        reading for reading in json.loads(path.read_text(encoding='utf-8'))['readings'] if 'standing' in reading
    ]
    if not readings:
        return None
    quorum = len(readings) // 2 + 1
    counts = {}
    for reading in readings:
        held = {}
        for entry in reading['standing']:
            held.setdefault(entry['kind'], set()).update(entry['gestures'])
        for kind, gestures in held.items():
            for gesture in gestures:
                counts[(kind, gesture)] = counts.get((kind, gesture), 0) + 1
    present = {kind for kind, _ in counts} | {'intelligence'}
    return {
        'sessions': len(readings),
        'participants': [
            {'kind': kind, 'gestures': [g for g in GESTURE_ORDER if counts.get((kind, g), 0) >= quorum]}
            for kind in STANDING_ORDER
            if kind in present
        ],
    }


def validate_withdrawals(root, runs):
    """A withdrawn run keeps its frozen bundle and readings; the record says when and why it left the release."""
    path = root / 'data/withdrawals.json'
    if not path.exists():
        return {}
    record = load(path)
    require(
        record.get('schema_version') == 1
        and set(record) == {'schema_version', 'withdrawals'}
        and isinstance(record['withdrawals'], list),
        'Unsupported withdrawals record',
    )
    withdrawals = {}
    for item in record['withdrawals']:
        require(isinstance(item, dict) and set(item) == {'run_id', 'date', 'reason'}, 'Invalid withdrawal fields')
        rid = item['run_id']
        require(rid in runs and not runs[rid].get('source_run_id'), 'A withdrawal names an unknown run: ' + str(rid))
        require(rid not in withdrawals, 'A run is withdrawn once: ' + rid)
        require(
            isinstance(item['date'], str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', item['date']),
            'A withdrawal needs a date',
        )
        require(
            isinstance(item['reason'], str) and 0 < len(item['reason'].strip()) <= 600,
            'A withdrawal needs a short reason',
        )
        withdrawals[rid] = {'date': item['date'], 'reason': item['reason']}
    return withdrawals


def analyze(corpus):
    text_files = [f for f in corpus['files'] if f['text'] is not None]
    return dict(
        files=len(corpus['files']),
        bytes=sum(f['bytes'] for f in corpus['files']),
        text_files=len(text_files),
        omitted_files=len(corpus['files']) - len(text_files),
        lines=sum(len(f['text'].splitlines()) for f in text_files),
        extensions=sorted({PurePosixPath(f['path']).suffix or '(extensionless)' for f in text_files}),
        verification='Every included text byte checked against its retained SHA-256',
    )


def write_selection(root, selections):
    target = root / 'data/review-selection.json'
    temporary = target.with_suffix('.json.tmp')
    temporary.write_text(
        json.dumps({'schema_version': 1, 'selections': selections}, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )
    os.replace(temporary, target)


def add_review(root, review, select=False):
    data = validate(root)
    rid = review.get('run_id')
    require(rid in data['base_selections'], 'Review names an unknown run or an evaluation package run')
    selections = dict(data['base_selections'])
    if select or review.get('supersedes') == selections[rid]:
        selections[rid] = review.get('id')
    validate(root, extra_review=review, selection_override=selections)
    target = root / 'data/reviews' / (review['id'] + '.json')
    temporary = target.with_name('.' + target.name + '.tmp')
    temporary.write_text(json.dumps(review, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    selection_target = root / 'data/review-selection.json'
    previous_selection = selection_target.read_bytes()
    selection_temporary = selection_target.with_suffix('.json.tmp')
    selection_temporary.write_text(
        json.dumps({'schema_version': 1, 'selections': selections}, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )
    try:
        os.replace(temporary, target)
        os.replace(selection_temporary, selection_target)
    except Exception:
        temporary.unlink(missing_ok=True)
        selection_temporary.unlink(missing_ok=True)
        if (
            target.exists()
            and target.read_bytes() == json.dumps(review, ensure_ascii=False, indent=2).encode('utf-8') + b'\n'
        ):
            target.unlink()
        selection_target.write_bytes(previous_selection)
        raise
    return selections[rid]


def select_review(root, review_id):
    data = validate(root)
    review = next((item for item in data['reviews'] if item['id'] == review_id), None)
    require(review is not None, 'Unknown review')
    require(review['run_id'] in data['base_selections'], 'Evaluation package runs select their own review')
    selections = dict(data['base_selections'])
    selections[review['run_id']] = review_id
    validate(root, selection_override=selections)
    write_selection(root, selections)
    return review


def corpus_prompt_lines(project, run):
    return (Path(project) / 'corpus/prompts' / (run['prompt_sha256'] + '.txt')).read_text(encoding='utf-8').splitlines()


PACKAGE_FILES = {
    'run.json',
    'evaluation.json',
    'extraction.json',
    'scoring-policy.json',
    'status.json',
    'review.json',
    'selection.json',
    'manifest.json',
}
PACKAGE_METHODS = {'repeated-rubric-dag-v2', 'session-rubric-dag-v3'}


def validate_evaluation_package(directory, display_run, source_run, corpus, rubric, source_texts, review):
    """Validate one removable evaluation package by re-deriving everything it displays."""
    import evaluation_contract as contracts
    import rubric_policy

    files = {path.name for path in directory.iterdir() if not path.name.startswith('.')}
    require(
        files - {'carriage.json'} == PACKAGE_FILES, 'Evaluation package has an unexpected file set: ' + directory.name
    )
    require(
        all((directory / name).is_file() and not (directory / name).is_symlink() for name in files),
        'Evaluation package files must be regular files',
    )
    manifest = contracts.load_json(directory / 'manifest.json')
    require(
        set(manifest) == {'schema_version', 'evaluation_id', 'files'} and manifest['schema_version'] == 1,
        'Invalid evaluation package manifest',
    )
    require(manifest['evaluation_id'] == directory.name, 'Evaluation package identity differs from its directory')
    require(set(manifest['files']) == files - {'manifest.json'}, 'Evaluation package manifest file set differs')
    for name, expected_hash in manifest['files'].items():
        require(
            sha((directory / name).read_bytes()) == expected_hash,
            'Evaluation package file changed: ' + directory.name + '/' + name,
        )
    extraction, policy, evaluation, status, run_record, selection = [
        contracts.load_json(directory / name)
        for name in [
            'extraction.json',
            'scoring-policy.json',
            'evaluation.json',
            'status.json',
            'run.json',
            'selection.json',
        ]
    ]
    require(
        run_record == {key: value for key, value in display_run.items() if key != 'corpus'},
        'Evaluation run record changed after discovery',
    )
    require(evaluation['evaluation_id'] == directory.name, 'Evaluation identity differs from its package directory')
    require(evaluation['run_id'] == source_run['id'], 'Evaluation package names the wrong source run')
    identities = evaluation['identities']
    require(
        identities['prompt_sha256'] == source_run['prompt_sha256']
        and identities['corpus_sha256'] == source_run['corpus_sha256']
        and identities['candidate_sha256'] == source_run['corpus_sha256'],
        'Evaluation identities differ from the frozen source run: ' + directory.name,
    )
    require(identities['rubric_sha256'] == sha(canonical(rubric)), 'Evaluation names another rubric: ' + directory.name)
    require(rubric_policy.matches_rubric(policy, rubric), 'Scoring policy does not encode the current rubric')
    contracts.validate_evaluation(
        evaluation,
        extraction=extraction,
        extraction_sha256=sha((directory / 'extraction.json').read_bytes()),
        policy=policy,
        scoring_policy_sha256=sha((directory / 'scoring-policy.json').read_bytes()),
        source_texts=source_texts,
    )
    aggregate_of = {
        edge['from']: edge['to']
        for edge in policy['edges']
        if edge['from'] in {node['id'] for node in policy['nodes'] if node['kind'] == 'judgment'}
    }
    by_aggregate = {item['node_id']: item['outcome'] for item in evaluation['aggregations']}
    outcomes = {judgment: by_aggregate[aggregate] for judgment, aggregate in aggregate_of.items()}
    scored = sum(outcome['status'] == 'scored' for outcome in evaluation['outcomes'])
    require(
        status.get('status') == 'completed'
        and status.get('dry_run') is False
        and status.get('run_id') == source_run['id']
        and status.get('evaluation_id') == directory.name
        and status.get('outcomes') == outcomes
        and status.get('criteria') == len(outcomes)
        and status.get('judgment_samples') == len(evaluation['judgments'])
        and status.get('scored_axes') == scored
        and status.get('unresolved_axes') == len(outcomes) - scored
        and status.get('evaluation_sha256') == sha((directory / 'evaluation.json').read_bytes()),
        'Evaluation package status differs from its evaluation: ' + directory.name,
    )
    require(
        selection.get('schema_version') == 3
        and set(selection) == {'schema_version', 'evaluation_id', 'source_run_id', 'run_id', 'review_id', 'order'}
        and selection['evaluation_id'] == directory.name
        and selection['source_run_id'] == source_run['id']
        and selection['run_id'] == display_run['id']
        and selection['review_id'] == review['id']
        and type(selection['order']) is int
        and selection['order'] >= 1,
        'Evaluation package selection is invalid',
    )
    require(review['run_id'] == display_run['id'], 'Evaluation package review names the wrong run')
    provenance = review.get('provenance', {})
    require(provenance.get('method') in PACKAGE_METHODS, 'Evaluation package review has an unknown method')
    require(review.get('supersedes') is None, 'Package reviews are revised by admitting a new package')
    if 'carriage.json' in files:
        carriage = contracts.load_json(directory / 'carriage.json')
        require(
            carriage['evaluation_id'] == directory.name and carriage['run_id'] == source_run['id'],
            'Carriage record names another evaluation: ' + directory.name,
        )
        contracts.validate_carriage_record(
            carriage,
            source_texts=source_texts,
            seed_line_count=len(corpus_prompt_lines(directory.parents[2], source_run)),
        )
        answers = {judgment['answer_sha256'] for judgment in evaluation['judgments']}
        require(
            {reading['answer_sha256'] for reading in carriage['readings']} <= answers,
            'Carriage readings must come from the evaluation answers: ' + directory.name,
        )
    scores, axis_outcomes = contracts.review_projection(evaluation, policy, rubric, source_texts)
    require(provenance.get('axis_outcomes') == axis_outcomes, 'Axis outcome provenance differs: ' + directory.name)
    require(review['scores'] == scores, 'Review scores differ from the evaluation projection: ' + directory.name)
    return evaluation


PALETTES = ('dusk', 'daylight', 'play')


def themed_url(url, palette):
    return re.sub(r'(?:\.(?:daylight|play))?\.html(?=#|$)', ('' if palette == 'dusk' else '.' + palette) + '.html', url)


def document_head(title, palette, prefix):
    return (
        '<!doctype html><html lang="en" data-palette="' + palette + '"><head>'
        '<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="referrer" content="no-referrer"><title>' + html.escape(title) + ' · After the Poem</title>'
        '<link rel="icon" type="image/svg+xml" href="' + prefix + 'assets/favicon.svg">'
        '<link rel="stylesheet" href="' + prefix + 'palette.css">'
        '<link rel="stylesheet" href="' + prefix + 'source.css"></head>'
    )


def document_header(prefix, palette, navigation=''):
    return (
        '<header class="document-masthead"><a class="document-brand" href="'
        + prefix
        + 'index.html?palette='
        + palette
        + '"><span>AFTER THE POEM</span></a>'
        + ('<nav aria-label="Source navigation">' + navigation + '</nav>' if navigation else '')
        + '</header>'
    )


def links_open_in_new_tabs(document):
    """Open cross-document links in a new tab while preserving in-page anchors."""

    def replace(match):
        tag = match.group(0)
        href = re.search(r'\bhref="([^"]*)"', tag, re.IGNORECASE)
        if href is None or html.unescape(href.group(1)).startswith('#'):
            return tag
        closing = ' />' if tag.endswith(' />') else '>'
        opening = tag[: -len(closing)]
        if re.search(r'\btarget=', opening, re.IGNORECASE) is None:
            opening += ' target="_blank"'
        rel = re.search(r'\brel="([^"]*)"', opening, re.IGNORECASE)
        if rel is None:
            opening += ' rel="noopener"'
        elif 'noopener' not in rel.group(1).split():
            opening = opening[: rel.start(1)] + (rel.group(1) + ' noopener').strip() + opening[rel.end(1) :]
        return opening + closing

    return re.sub(r'<a\b[^>]*>', replace, document, flags=re.IGNORECASE)


def source_page(
    title,
    text,
    digest,
    *,
    markdown=False,
    alternate=None,
    resolve_link=None,
    icon_href=None,
    palette='dusk',
    poem=False,
):
    prefix = icon_href[: -len('assets/favicon.svg')] if icon_href else '../'
    resolve = (
        (lambda target: themed_url(resolve_link(target), palette) if resolve_link(target) else None)
        if resolve_link
        else None
    )
    lines = ''.join(
        '<span class="line" id="L%d"><a href="#L%d" aria-label="Line %d">%d</a>%s</span>'
        % (i, i, i, i, html.escape(line))
        for i, line in enumerate(text.splitlines(), 1)
    )
    navigation = ''
    if alternate:
        navigation = (
            '<a href="'
            + html.escape(themed_url(alternate, palette), quote=True)
            + '">'
            + ('Exact source lines ↗' if markdown else 'Read the document ↗')
            + '</a>'
        )
    if poem:
        original_lines = text.splitlines()
        start = next((i for i, line in enumerate(original_lines) if line.startswith('### ')), 0)
        verse = render_markdown('\n'.join(original_lines[start:]), start=start + 1, line_ids=True)
        verse = verse.replace('<h3 ', '<h1 ', 1).replace('</h3>', '</h1>', 1)
        context = render_markdown('\n'.join(original_lines[:start]) + '\n', line_ids=True)
        content = (
            '<article class="markdown poem">' + verse + '</article>'
            '<section class="poem-context" id="poem-context"><h2>Context supplied with the poem</h2>'
            '<div class="markdown">' + context + '</div></section>'
        )
        navigation = '<a href="#poem-context">The inherited present ↓</a>' + navigation
    elif markdown:
        content = (
            '<article class="markdown">' + render_markdown(text, line_ids=True, resolve_link=resolve) + '</article>'
        )
    else:
        content = '<pre>' + lines + '</pre>'
    record = (
        '<footer class="document-record"><p>Retained source · SHA-256: <code>' + digest + '</code></p>'
        '<p><a rel="license" href="'
        + prefix
        + themed_url('licenses.html#content', palette)
        + '">Content · CC BY 4.0</a></p></footer>'
    )
    page = (
        document_head(title, palette, prefix)
        + '<body class="source-page'
        + (' poem-page' if poem else '')
        + '">'
        + document_header(prefix, palette, navigation)
        + '<main class="document-main">'
        + ('' if poem else '<h1>' + html.escape(title) + '</h1>')
        + content
        + record
        + '</main></body></html>'
    )
    return links_open_in_new_tabs(page)


def project_reader(data, root):
    """Show one reader on the page: each artefact carries that reader's reading under its own ID.

    Data stays untouched: earlier reviews remain in data/reviews and every reading
    package remains its own run on disk. Only the published report is projected.
    """
    path = Path(root) / 'data/presentation.json'
    if not path.is_file():
        return data
    config = load(path)
    require(config.get('schema_version') == 1 and isinstance(config.get('reader'), dict), 'Invalid presentation config')
    require(
        isinstance(config['reader'].get('note'), str) and config['reader']['note'].strip(),
        'The presentation reader needs a short note explaining why it was chosen',
    )
    reader = config['reader']
    runs = {run['id']: run for run in data['runs']}
    preferred = {}
    for package_run in data['evaluation_packages']:
        run, review = runs[package_run], data['active_reviews'][package_run]
        if (
            run['model'] == reader['model']
            and review['provenance'].get('method') == reader['method']
            and review['provenance'].get('evaluation_id', '').endswith(reader.get('evaluation_suffix', ''))
        ):
            # Later packages supersede earlier ones of the same reader for display; a suffix pins one reading.
            preferred[run['source_run_id']] = package_run
    hidden = set(config.get('hidden_cohorts', []))
    require(hidden <= {cohort['id'] for cohort in data['cohorts']}, 'Presentation hides an unknown cohort')
    require(
        next(run for run in data['runs'] if run['id'] == data['baseline'])['cohort'] not in hidden,
        'The baseline cohort cannot be hidden',
    )
    require(data['baseline'] in preferred, 'The presentation reader has no reading for the baseline')
    # The release page is an explicit editorial list: nothing new goes public until it is added here.
    listed = config.get('shown_runs')
    require(
        isinstance(listed, list) and listed and len(set(listed)) == len(listed),
        'The presentation needs a list of shown runs',
    )
    known = {run['id']: run for run in data['runs'] if not run.get('source_run_id')}
    require(all(rid in known for rid in listed), 'Presentation lists an unknown run')
    require(not set(listed) & set(data.get('withdrawals', {})), 'Presentation lists a withdrawn run')
    require(all(known[rid]['cohort'] not in hidden for rid in listed), 'Presentation lists a run from a hidden cohort')
    require(data['baseline'] in listed, 'The baseline must be listed')
    missing = [rid for rid in listed if rid not in preferred]
    require(not missing, 'The presentation reader has no reading for: ' + ', '.join(missing))
    artefacts = [run for run in data['runs'] if run['id'] in listed]
    # Read but unlisted worlds stay off the page and are reported by the build for review.
    reader = dict(reader, awaiting=[rid for rid in known if rid not in listed and known[rid]['cohort'] not in hidden])
    active, packages = {}, {}
    for run in artefacts:
        reading = data['active_reviews'][preferred[run['id']]]
        active[run['id']] = dict(
            reading, run_id=run['id'], provenance=dict(reading['provenance'], reading_run_id=preferred[run['id']])
        )
        packages[run['id']] = data['evaluation_packages'][preferred[run['id']]]
    keep = set(active)
    projected = dict(
        data,
        runs=artefacts,
        active_reviews=active,
        reviews=list(active.values()),
        selections={rid: review['id'] for rid, review in active.items()},
        baseline_review_id=active[data['baseline']]['id'],
        evaluation_packages=packages,
        cohorts=[
            dict(cohort, runs=[rid for rid in cohort['runs'] if rid in keep])
            for cohort in data['cohorts']
            if cohort['id'] not in hidden and keep & set(cohort['runs'])
        ],
        artefacts={rid: value for rid, value in data['artefacts'].items() if rid in keep},
        observations={rid: value for rid, value in data['observations'].items() if rid in keep},
        reader=reader,
        comparison=config.get('comparison'),
    )
    # The crossing (the poem, then the first world in pieces, with its sound) can be paused while it is unfinished:
    # its settings stay validated but the page opens straight on the workbench.
    crossing = config.get('crossing', True)
    require(type(crossing) is bool, 'The presentation crossing switch must be true or false')
    threshold = config.get('threshold')
    if threshold is not None:
        require(
            isinstance(threshold, dict)
            and threshold.get('cohort') in {run['cohort'] for run in artefacts}
            and all(
                isinstance(threshold.get(key), str) and threshold[key].strip()
                for key in ('eyebrow', 'provenance', 'run')
            ),
            'The threshold must name a shown cohort and give its text',
        )
        if crossing:
            projected['threshold'] = threshold
    # The crossing: the first world is reread from its own files before the workbench opens.
    game = config.get('game')
    if game is not None:
        require(threshold is not None, 'The crossing needs the threshold')
        require(
            isinstance(game, dict)
            and game.get('first_world') in keep
            and all(
                isinstance(game.get(key), str) and game[key].strip()
                for key in ('journal_world', 'workbench_title', 'workbench_meta', 'journal_workbench')
            ),
            'The crossing must name a shown first world and give its text',
        )
        first = game['first_world']
        # Its files travel with the page as inert text, to be unfolded and read.
        files = [
            {'path': source['path'], 'text': source['text']}
            for source in data['corpora'][first]['files']
            if source['text'] is not None
        ]
        require(files, 'The first world has no text to read')
        if crossing:
            projected['game'] = dict(game, files=files)
    origin = config.get('earliest_run')
    if origin is not None:
        require(
            origin.get('run_id') in keep
            and isinstance(origin.get('heading'), str)
            and isinstance(origin.get('paragraphs'), list)
            and all(isinstance(text, str) and text.strip() for text in origin['paragraphs']),
            'The earliest-run note must name a shown run and give its paragraphs',
        )
        projected['earliest_run'] = origin
    comparison = config.get('comparison')
    if comparison is not None:
        require(
            comparison.get('mode') == 'pins'
            and isinstance(comparison.get('default_pins'), list)
            and set(comparison['default_pins']) <= keep,
            'Presentation default pins must name shown runs',
        )
    return projected


def _build(root=ROOT, view='release', target='home'):
    data = validate(root)
    if view == 'release':
        data = project_reader(data, root)
    out = root / 'dist'
    if out.exists():
        shutil.rmtree(out)
    out.mkdir()
    for name in ['index.html', 'palette.css', 'style.css', 'source.css', 'app.js']:
        shutil.copyfile(root / 'src' / name, out / name)
    if (root / 'src/vendor').exists():
        shutil.copytree(root / 'src/vendor', out / 'vendor')
    shutil.copyfile(root / 'src/previews.js', out / 'previews.js')
    # The crossing's music is optional: the page falls silent where the file is absent.
    if (root / 'src/sound.js').is_file():
        shutil.copyfile(root / 'src/sound.js', out / 'sound.js')
    shutil.copytree(root / 'assets', out / 'assets')
    (out / 'licenses').mkdir()
    shutil.copyfile(root / 'LICENSE', out / 'licenses/MIT.txt')
    shutil.copyfile(root / 'LICENSE-CONTENT', out / 'licenses/CC-BY-4.0.txt')

    def license_document(title, content, back, links, palette):
        prefix = back[: -len('index.html')]
        resolve = lambda target: themed_url(links[target], palette) if target in links else None
        page = (
            document_head(title, palette, prefix)
            + '<body class="source-page">'
            + document_header(prefix, palette)
            + '<main class="document-main markdown">'
            + render_markdown(content, resolve_link=resolve)
            + '</main></body></html>'
        )
        return links_open_in_new_tabs(page)

    for palette in PALETTES:
        (out / themed_url('licenses.html', palette)).write_text(
            license_document(
                'Licensing',
                (root / 'LICENSING.md').read_text(encoding='utf-8'),
                'index.html',
                {
                    'LICENSE': 'licenses/MIT.txt',
                    'LICENSE-CONTENT': 'licenses/CC-BY-4.0.txt',
                    'THIRD_PARTY_NOTICES.md': 'licenses/third-party.html',
                },
                palette,
            ),
            encoding='utf-8',
        )
        (out / themed_url('licenses/third-party.html', palette)).write_text(
            license_document(
                'Third-party notices',
                (root / 'THIRD_PARTY_NOTICES.md').read_text(encoding='utf-8'),
                '../index.html',
                {
                    'https://github.com/xtermjs/xterm.js': 'https://github.com/xtermjs/xterm.js',
                    'src/vendor/xterm-LICENSE': '../vendor/xterm-LICENSE',
                    'src/vendor/addon-fit-LICENSE': '../vendor/addon-fit-LICENSE',
                },
                palette,
            ),
            encoding='utf-8',
        )
    report = {
        k: data[k]
        for k in [
            'rubric',
            'rubric_sha256',
            'baseline',
            'baseline_review_id',
            'runs',
            'reviews',
            'active_reviews',
            'selections',
            'evidence_sources',
        ]
    }
    withdrawals = data.get('withdrawals', {})
    report['runs'] = [
        dict(run, withdrawn=withdrawals[run.get('source_run_id') or run['id']])
        if (run.get('source_run_id') or run['id']) in withdrawals
        else run
        for run in report['runs']
    ]
    report['reader'] = data.get('reader')
    report['comparison'] = data.get('comparison')
    report['earliest_run'] = data.get('earliest_run')
    report['threshold'] = data.get('threshold')
    report['game'] = data.get('game')
    # `home` can reach the local preview runner; `pages` is a static site where native artefacts stay local.
    report['site'] = {'target': target}
    # The static site cannot run native worlds; its console worlds replay a recording made by the local runner.
    shown = {run['id'] for run in report['runs']}
    report['recordings'] = (
        {rid: record for rid, record in data.get('recordings', {}).items() if rid in shown} if target == 'pages' else {}
    )
    report['schema_version'] = 1
    report['review_selections'] = report.pop('selections')
    # Carriage maps (seed reading → what the artefact carried) ship with v4 readings; no view renders them yet.
    report['reading_details'] = {
        rid: reading_details(package, data['rubric']) for rid, package in data['evaluation_packages'].items()
    }
    report['standing'] = {
        rid: summary for rid, package in data['evaluation_packages'].items() if (summary := standing_summary(package))
    }
    report['carriage'] = {
        rid: json.loads((package / 'carriage.json').read_text(encoding='utf-8'))
        for rid, package in data['evaluation_packages'].items()
        if (package / 'carriage.json').is_file()
    }
    report['cohorts'] = []
    report['prompts'] = {}
    prompt_cohorts = []
    for cohort in data['cohorts']:
        cid = cohort['id']
        prompt = data['cohort_prompts'][cid]
        public = dict(cohort)
        public.update(
            prompt_url='sources/prompts/' + cid + '.html',
            prompt_bytes=len(prompt.encode('utf-8')),
            baseline_label=next(r['label'] for r in data['runs'] if r['id'] == cohort['baseline']),
        )
        prompt_cohorts.append(public)
        report['prompts'][cid] = dict(
            text=prompt,
            sha256=cohort['prompt_sha256'],
            url=public['prompt_url'],
            bytes=len(prompt.encode('utf-8')),
            provenance=(
                'The meaning is worked out in dialogue with Sol; Sol compiled it into this form.'
                if cohort['prompt_sha256'] == data['cohorts'][0]['prompt_sha256']
                else 'Exact delivered prompt for this cohort. Consult the run records for seed and delivery conditions; a shared prompt alone does not establish equivalent conditions.'
            ),
        )
    # A cohort that shares another's seed joins its workbench; each run keeps its own exact prompt.
    shown = {cohort['id'] for cohort in prompt_cohorts}
    joined = {cohort['id']: cohort['seed_of'] for cohort in prompt_cohorts if cohort.get('seed_of') in shown}
    for cohort in prompt_cohorts:
        if cohort['id'] in joined:
            parent = next(item for item in prompt_cohorts if item['id'] == joined[cohort['id']])
            parent['runs'] = parent['runs'] + cohort['runs']
            parent.setdefault('variants', []).append(cohort['id'])
        else:
            report['cohorts'].append(cohort)
    report['runs'] = [
        dict(run, cohort=joined[run['cohort']], prompt_cohort=run['cohort']) if run['cohort'] in joined else run
        for run in report['runs']
    ]
    primary = report['cohorts'][0]
    report['prompt'] = report['prompts'][primary['id']]
    report['artefacts'] = data['artefacts']
    from previews import build_browser_previews

    build_browser_previews(data, out)
    report['observations'] = data['observations']
    for rid, observation in report['observations'].items():
        evidence = observation['evidence']
        content = (
            'Source: '
            + evidence['source']
            + '\nParent record SHA-256: '
            + evidence['source_sha256']
            + '\nParent retained in repository: '
            + ('yes' if evidence['parent_retained'] else 'no')
            + '\nProvenance scope: '
            + evidence['scope']
            + '\nSelection: '
            + evidence['locator']
            + '\n'
            + evidence['projection_note']
            + '\n'
            + evidence['provenance_note']
            + '\n\n'
            + evidence['text']
        )
        if evidence['browser_observations'] is not None:
            content += '\n\nRetained browser observations:\n' + json.dumps(
                evidence['browser_observations'], ensure_ascii=False, indent=2
            )
        relative = 'observations/' + rid + '.html'
        dest = out / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        for palette in PALETTES:
            (out / themed_url(relative, palette)).write_text(
                source_page(
                    'Retained observations · ' + rid,
                    content,
                    sha(content.encode('utf-8')),
                    icon_href='../assets/favicon.svg',
                    palette=palette,
                ),
                encoding='utf-8',
            )
        observation['url'] = relative
    report['sources'] = {}
    for run in report['runs']:
        rid = run['id']
        corpus = data['corpora'][rid]
        available = {s['path'] for s in corpus['files'] if s['text'] is not None}
        run['metrics'] = analyze(corpus)
        report['sources'][rid] = []
        for source in corpus['files']:
            public = {k: v for k, v in source.items() if k != 'text'}
            if source['text'] is not None:
                public['url'] = source_url(rid, source['path']).split('#')[0]
                dest = out / 'sources' / rid / (source['path'] + '.html')
                dest.parent.mkdir(parents=True, exist_ok=True)
                icon_href = '../' * len(dest.relative_to(out).parent.parts) + 'assets/favicon.svg'
                is_markdown = source['path'].lower().endswith('.md')
                relative_root = '../' * len(PurePosixPath(source['path']).parent.parts)
                resolve = lambda target: source_link(target, source['path'], available, relative_root)
                for palette in PALETTES:
                    dest.with_name(themed_url(dest.name, palette)).write_text(
                        source_page(
                            source['path'],
                            source['text'],
                            source['sha256'],
                            markdown=is_markdown,
                            alternate=dest.stem + '.raw.html' if is_markdown else None,
                            resolve_link=resolve,
                            icon_href=icon_href,
                            palette=palette,
                        ),
                        encoding='utf-8',
                    )
                    if is_markdown:
                        raw = dest.with_suffix('.raw.html')
                        raw.with_name(themed_url(raw.name, palette)).write_text(
                            source_page(
                                source['path'],
                                source['text'],
                                source['sha256'],
                                alternate=dest.name,
                                icon_href=icon_href,
                                palette=palette,
                            ),
                            encoding='utf-8',
                        )
            report['sources'][rid].append(public)
    for cohort in prompt_cohorts:
        cid = cohort['id']
        prompt = report['prompts'][cid]['text']
        prompt_hash = cohort['prompt_sha256']
        relative = 'sources/prompts/' + cid + '.html'
        dest = out / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        icon_href = '../' * len(dest.relative_to(out).parent.parts) + 'assets/favicon.svg'
        is_poem = any(line.startswith('### ') for line in prompt.splitlines())
        for palette in PALETTES:
            (out / themed_url(relative, palette)).write_text(
                source_page(
                    'The exact prompt · ' + cohort['title'],
                    prompt,
                    prompt_hash,
                    markdown=True,
                    alternate=cid + '.raw.html',
                    icon_href=icon_href,
                    palette=palette,
                    poem=is_poem,
                ),
                encoding='utf-8',
            )
            (out / themed_url(relative.replace('.html', '.raw.html'), palette)).write_text(
                source_page(
                    'The exact prompt · ' + cohort['title'],
                    prompt,
                    prompt_hash,
                    alternate=cid + '.html',
                    icon_href=icon_href,
                    palette=palette,
                ),
                encoding='utf-8',
            )
        (out / 'prompts').mkdir(exist_ok=True)
        (out / 'prompts' / (cid + '.txt')).write_text(prompt, encoding='utf-8')
    for palette in PALETTES:
        primary_prompt = report['prompts'][primary['id']]['text']
        (out / themed_url('sources/prompt.html', palette)).write_text(
            source_page(
                'The exact shared prompt',
                primary_prompt,
                primary['prompt_sha256'],
                markdown=True,
                alternate='prompt.raw.html',
                icon_href='../assets/favicon.svg',
                palette=palette,
                poem=any(line.startswith('### ') for line in primary_prompt.splitlines()),
            ),
            encoding='utf-8',
        )
        (out / themed_url('sources/prompt.raw.html', palette)).write_text(
            source_page(
                'The exact shared prompt',
                primary_prompt,
                primary['prompt_sha256'],
                alternate='prompt.html',
                icon_href='../assets/favicon.svg',
                palette=palette,
            ),
            encoding='utf-8',
        )
    report['evidence_text'] = {}
    for review in report['reviews']:
        for score in review['scores'].values():
            for cite in score['citations']:
                key = '%s/%s:%d-%d' % (review['run_id'], cite['path'], cite['start'], cite['end'])
                extracted = extract_readable(cite['path'], cite['quote'])
                if extracted:
                    report['evidence_text'][key] = extracted
    import evaluation_contract
    from page_prepare import build_presentation, historical_presentation

    report['presentations'] = {}
    (out / 'presentations').mkdir()
    for run in report['runs']:
        rid = run['id']
        package = data['evaluation_packages'].get(rid)
        if package is None:
            presentation = historical_presentation(
                run, data['active_reviews'][rid], data['rubric'], data['rubric_sha256'], run['corpus_sha256']
            )
        else:
            # Evaluation runs publish their real samples, agreement and usage.
            records = {
                name: evaluation_contract.load_json(package / name)
                for name in ['evaluation.json', 'extraction.json', 'scoring-policy.json']
            }
            texts = {f['path']: f['text'] for f in data['corpora'][rid]['files'] if f['text'] is not None}
            presentation = build_presentation(
                records['evaluation.json'],
                records['extraction.json'],
                records['scoring-policy.json'],
                evaluation_sha256=sha((package / 'evaluation.json').read_bytes()),
                extraction_sha256=sha((package / 'extraction.json').read_bytes()),
                scoring_policy_sha256=sha((package / 'scoring-policy.json').read_bytes()),
                source_texts=texts,
                presentation_id=rid + '-presentation-v1',
            )
        report['presentations'][rid] = presentation
        (out / 'presentations' / (rid + '.json')).write_text(
            json.dumps(presentation, ensure_ascii=False, sort_keys=True, indent=2) + '\n', encoding='utf-8'
        )
    (out / 'prompt.txt').write_text(primary_prompt, encoding='utf-8')
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    (out / 'report.json').write_text(payload + '\n', encoding='utf-8')
    (out / 'report-data.js').write_text(
        'window.POEM_REPORT = '
        + payload.replace('<', '\\u003c').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
        + ';\n',
        encoding='utf-8',
    )
    csv_text = io.StringIO()
    writer = csv.writer(csv_text)
    writer.writerow(['run', 'review', 'rubric', 'status'] + [a['id'] for a in data['rubric']['axes']])
    for run in report['runs']:
        review = data['active_reviews'][run['id']]
        writer.writerow(
            [run['id'], review['id'], data['rubric']['version'], review['status']]
            + [review['scores'][a['id']]['value'] for a in data['rubric']['axes']]
        )
    (out / 'scores.csv').write_text(csv_text.getvalue(), encoding='utf-8')
    (out / '.nojekyll').touch()
    # Stamp local assets with their content hash so a reload always picks up a new build.
    index = out / 'index.html'
    page = index.read_text(encoding='utf-8')
    for name in [
        'vendor/xterm.css',
        'palette.css',
        'style.css',
        'report-data.js',
        'vendor/xterm.js',
        'vendor/addon-fit.js',
        'previews.js',
        'app.js',
    ]:
        if (out / name).is_file():
            stamp = name + '?v=' + sha((out / name).read_bytes())[:12]
            page = page.replace('href="' + name + '"', 'href="' + stamp + '"').replace(
                'src="' + name + '"', 'src="' + stamp + '"'
            )
    index.write_text(page, encoding='utf-8')
    manifest = {str(p.relative_to(out)): sha(p.read_bytes()) for p in sorted(out.rglob('*')) if p.is_file()}
    (out / 'build-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(
        'Built %d runs, %d assessments, %d file records → %s'
        % (len(report['runs']), len(report['reviews']), sum(len(v) for v in report['sources'].values()), out)
    )
    if (report.get('reader') or {}).get('awaiting'):
        print('Not listed in data/presentation.json (kept off this view): %s' % ', '.join(report['reader']['awaiting']))
    return out


def build(root=ROOT, view='release', target='home'):
    """Build one deterministic snapshot at a time for concurrent local runs.

    `release` applies data/presentation.json (one reader, hidden cohorts); `full`
    shows every run and reading, which the full browser smoke test exercises.
    `home` is the local build that the native preview runner can serve; `pages`
    is the static site, which offers native artefacts only as screenshots.
    """
    require(view in ('release', 'full'), 'Unknown build view: ' + str(view))
    require(target in ('home', 'pages'), 'Unknown build target: ' + str(target))
    root = Path(root).resolve()
    if os.name != 'posix':
        return _build(root, view, target)
    import fcntl

    lock_path = root / '.build.lock'
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _build(root, view, target)


class HelpFormatter(argparse.HelpFormatter):
    """Command-line help for every script: prose paragraphs wrapped to the terminal, indented blocks such as
    examples kept as written, and a default shown wherever an option has one that its help does not name."""

    def __init__(self, prog):
        super().__init__(prog, max_help_position=30)

    def _fill_text(self, text, width, indent):
        paragraphs = []
        for block in re.split(r'\n[ \t]*\n', text.strip('\n')):
            lines = block.splitlines()
            if any(line[:1].isspace() for line in lines):
                paragraphs.append('\n'.join(indent + line for line in lines))
            else:
                paragraphs.append(
                    textwrap.fill(
                        ' '.join(line.strip() for line in lines),
                        width,
                        initial_indent=indent,
                        subsequent_indent=indent,
                        break_on_hyphens=False,
                    )
                )
        return '\n\n'.join(paragraphs)

    def _split_lines(self, text, width):
        return textwrap.wrap(' '.join(text.split()), width, break_on_hyphens=False)

    def _get_help_string(self, action):
        text = action.help or ''
        default = action.default
        if (
            '%(default)' in text
            or '(default' in text
            or default is None
            or default is False
            or default is argparse.SUPPRESS
            or default == []
        ):
            return text
        if action.option_strings or action.nargs in (argparse.OPTIONAL, argparse.ZERO_OR_MORE):
            return text + ' (default: %(default)s)'
        return text


EPILOG = '''examples:
  python3 scripts/atlas.py validate
  python3 scripts/atlas.py build
  python3 scripts/atlas.py serve --previews
  python3 scripts/atlas.py build --target pages

npm aliases: check, build, build:pages, dev, dev:full, serve:pages.

Reference: docs/commands.md
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__, epilog=EPILOG, formatter_class=HelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True, metavar='COMMAND')

    def command(name, summary):
        return commands.add_parser(name, help=summary, description=summary + '.', formatter_class=HelpFormatter)

    def site_options(sub):
        sub.add_argument(
            '--view',
            choices=['release', 'full'],
            default='release',
            help='release applies data/presentation.json; full shows every run and reading',
        )
        sub.add_argument(
            '--target',
            choices=['home', 'pages'],
            default='home',
            help='home is the local site with native previews; pages is the static site',
        )

    command('validate', 'Check source hashes, the rubric, citations and review lineage; writes nothing')
    command('analyze', 'Print descriptive metrics for every world as JSON')
    site_options(command('build', 'Build the site into dist/'))
    serve = command('serve', 'Build the site and serve it on 127.0.0.1 until Ctrl-C')
    site_options(serve)
    serve.add_argument('--port', type=int, default=4317, help='Port to listen on')
    serve.add_argument(
        '--previews',
        action='store_true',
        help='Run native worlds on request in the isolated local runner (macOS, home target)',
    )
    add = command('add-review', 'Add an exported review to the study; rebuild to show it')
    add.add_argument('review_file', type=Path, metavar='REVIEW_JSON', help='Exported review file')
    add.add_argument(
        '--select',
        action='store_true',
        help='Select the review for display (a review that supersedes the selected one always is)',
    )
    select = command('select-review', 'Select a review for display; rebuild to show it')
    select.add_argument('review_file', metavar='REVIEW_ID', help='ID of a review already in the study')
    args = parser.parse_args()
    if args.command == 'validate':
        data = validate()
        print(
            'Valid: %d frozen candidates; source hashes, rubric, citations and review lineage checked.'
            % len(data['runs'])
        )
    elif args.command == 'analyze':
        data = validate()
        print(json.dumps({rid: analyze(c) for rid, c in data['corpora'].items()}, indent=2))
    elif args.command == 'build':
        build(view=args.view, target=args.target)
    elif args.command == 'add-review':
        require(args.review_file is not None, 'Supply an exported review JSON')
        review = load(args.review_file)
        selected = add_review(ROOT, review, select=args.select)
        print('Added review ' + review['id'] + '. Rebuild to publish it locally.')
        print('Selected reading for ' + review['run_id'] + ': ' + selected)
    elif args.command == 'select-review':
        require(args.review_file is not None, 'Supply a review ID')
        review = select_review(ROOT, str(args.review_file))
        print('Selected ' + review['id'] + ' for ' + review['run_id'] + '. Rebuild to publish it locally.')
    else:
        # `serve --target pages` previews the static site as Pages would show it.
        require(not (args.previews and args.target == 'pages'), 'Native previews belong to the home build')
        out = build(view=args.view, target=args.target)
        from previews import serve

        serve(ROOT, out, args.port, args.previews)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Interrupted.', file=sys.stderr)
        sys.exit(130)
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as error:
        print('Error: ' + str(error), file=sys.stderr)
        sys.exit(1)
