#!/usr/bin/env python3
"""Prepare deterministic public presentation records and static HTML pages."""

import argparse
import html
import json
import math
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

import evaluation_contract as contracts
import fsutil
from bundles import canonical, inspect_metadata, require, sha
from evidence_text import normalize_alternatives


ROOT = Path(__file__).resolve().parents[1]
PRESENTATION_FILES = {'evaluation.json', 'extraction.json', 'scoring-policy.json', 'presentation.json', 'index.html'}


def _presentation_id(evaluation):
    value = evaluation['evaluation_id']
    return (value[:80] if len(value) > 80 else value) + '-presentation-v1'


# A world may write the path it was dreamed in into its own files. A public record's quotes withhold that
# path; the preserved source keeps every byte and shows it.
HOST_PATH_TEXT = re.compile(
    r'(?<![\w./-])/(?:Users|home|private|var/folders|tmp)/[^\s"\'<>`]*?(?=[.,;:)]*(?:[\s"\'<>`]|$))'
)
WITHHELD_PATH = '[a path on the dreaming machine]'


def public_quote(citation, source_texts):
    quoted = contracts.quote_citation(citation, source_texts)
    return dict(quoted, quote=HOST_PATH_TEXT.sub(WITHHELD_PATH, quoted['quote']))


# Additive optional presentation fields: old records without them still validate.
OPTIONAL_PRESENTATION_FIELDS = {'alternatives'}


def build_presentation(
    evaluation,
    extraction,
    policy,
    *,
    evaluation_sha256,
    extraction_sha256,
    scoring_policy_sha256,
    source_texts=None,
    presentation_id=None,
    carriage=None,
    alternatives=None,
):
    """Project an evaluation into PresentationRecord v1; schema-2 citations get their quotes resolved.

    Supported seed alternative readings, given directly or collected from a carriage record, are kept in
    the additive optional ``alternatives`` field. Every sample is shown, whichever one a review chose.
    """
    nodes, outgoing, incoming = contracts.validate_policy(policy)
    if evaluation['schema_version'] == 2:
        require(source_texts is not None, 'Schema-2 evaluations need source texts to resolve quotes')
        quote = lambda items: [public_quote(item, source_texts) for item in items]
    else:
        quote = list
    criteria = []
    judgments = [node for node in nodes.values() if node['kind'] == 'judgment']
    require(judgments, 'Presentation needs at least one judgment criterion')
    outcome_by_node = {item['node_id']: item for item in evaluation['outcomes']}
    aggregate_by_node = {item['node_id']: item for item in evaluation['aggregations']}
    for judgment in judgments:
        samples = sorted(
            (sample for sample in evaluation['judgments'] if sample['node_id'] == judgment['id']),
            key=lambda sample: sample['sample'],
        )
        aggregate_targets = {
            edge['to'] for edge in outgoing.get(judgment['id'], []) if nodes[edge['to']]['kind'] == 'aggregate'
        }
        require(len(aggregate_targets) == 1, 'Judgment must feed one aggregate: ' + judgment['id'])
        aggregation = aggregate_by_node.get(next(iter(aggregate_targets)))
        require(aggregation is not None, 'Evaluation is missing an aggregate result: ' + judgment['id'])
        terminals = [
            outcome_by_node[edge['to']]
            for edge in outgoing.get(aggregation['node_id'], [])
            if edge['to'] in outcome_by_node
        ]
        require(len(terminals) == 1, 'Presentation branch needs one selected terminal: ' + judgment['id'])
        outcome = terminals[0]
        branch_terminals = {edge['to'] for edge in outgoing.get(aggregation['node_id'], [])}
        scale = sorted(
            {
                nodes[node_id]['result']['score']
                for node_id in branch_terminals
                if nodes[node_id]['result']['status'] == 'scored'
            }
        )
        criteria.append(
            {
                'node_id': judgment['id'],
                'label': judgment['criterion'],
                'question': judgment['question'],
                'scale': scale,
                'samples': [
                    {
                        'sample': sample['sample'],
                        'outcome': sample['outcome'],
                        'verdict': sample['verdict'],
                        'seed_span': sample['seed_span'],
                        'rationale': sample['rationale'],
                        'citations': quote(sample['citations']),
                        'counter_citations': quote(sample['counter_citations']),
                    }
                    for sample in samples
                ],
                'aggregation': {
                    'strategy': aggregation['strategy'],
                    'sample_count': aggregation['sample_count'],
                    'outcome': aggregation['outcome'],
                    'agreement': aggregation['agreement'],
                    'distinct_outcomes': aggregation['distinct_outcomes'],
                },
                'outcome': {
                    'terminal_id': outcome['node_id'],
                    'status': outcome['status'],
                    'score': outcome['score'],
                    'rationale': outcome['rationale'],
                },
            }
        )
    scored = [criterion['outcome'] for criterion in criteria if criterion['outcome']['status'] == 'scored']
    presentation_status = 'scored' if len(scored) == len(criteria) else 'unresolved'
    presentation_score = None
    if alternatives is None:
        alternatives = contracts.collect_alternatives(carriage.get('readings')) if carriage else []
    # Withhold host-specific paths from public alternatives without touching sources.
    alternatives = normalize_alternatives(alternatives)
    require(
        isinstance(alternatives, list) and len(alternatives) <= 32,
        'Presentation alternatives must be a bounded list',
    )
    for alternative in alternatives:
        contracts.bounded_text(alternative, 'presentation alternative', 2000)
    presentation = {
        'schema_version': 1,
        'presentation_id': presentation_id or _presentation_id(evaluation),
        'evaluation_id': evaluation['evaluation_id'],
        'run_id': evaluation['run_id'],
        'identities': {
            'evaluation_sha256': evaluation_sha256,
            'extraction_sha256': extraction_sha256,
            'scoring_policy_sha256': scoring_policy_sha256,
        },
        'label': evaluation['presentation']['label'],
        'title': evaluation['presentation']['title'],
        'summary': evaluation['presentation']['summary'],
        'status': presentation_status,
        'score': presentation_score,
        'criteria': criteria,
        'findings': list(evaluation['presentation']['findings']),
        'usage': dict(evaluation['usage']),
    }
    if alternatives:
        # Additive optional field: old records without it still validate.
        presentation['alternatives'] = list(alternatives)
    validate_presentation(presentation, evaluation=evaluation)
    return presentation


def historical_presentation(run, review, rubric, rubric_sha256, corpus_sha256):
    """Project one authored historical review into PresentationRecord v1."""
    criteria = []
    findings = []
    for axis in rubric['axes']:
        score = review['scores'][axis['id']]
        outcome = 'unassessed' if score['value'] is None else 'level-' + str(score['value']).replace('.', '-')
        citations = [
            {
                'source_path': citation['path'],
                'start': citation['start'],
                'end': citation['end'],
                'quote': citation['quote'],
            }
            for citation in score['citations']
        ]
        terminal = {
            'terminal_id': axis['id'] + '-historical-terminal',
            'status': 'scored' if score['value'] is not None else 'review',
            'score': score['value'],
            'rationale': axis['anchors'][rubric['scale'].index(score['value'])]
            if score['value'] is not None
            else score['rationale'],
        }
        criteria.append(
            {
                'node_id': axis['id'],
                'label': axis['label'],
                'question': axis['question'],
                'scale': list(rubric['scale']),
                'samples': [
                    {
                        'sample': 1,
                        'outcome': outcome,
                        'verdict': (
                            axis['anchors'][rubric['scale'].index(score['value'])]
                            if score['value'] is not None
                            else 'Unassessed'
                        ),
                        'seed_span': score['seed_span'],
                        'rationale': score['rationale'],
                        'citations': citations,
                        'counter_citations': [],
                    }
                ],
                'aggregation': {
                    'strategy': 'unanimous',
                    'sample_count': 1,
                    'outcome': outcome,
                    'agreement': 1,
                    'distinct_outcomes': [outcome],
                },
                'outcome': terminal,
            }
        )
        findings.append(axis['label'] + ': ' + score['rationale'])
    record = {
        'schema_version': 1,
        'presentation_id': run['id'] + '-historical-presentation-v1',
        'evaluation_id': review['id'],
        'run_id': run['id'],
        'identities': {
            'evaluation_sha256': sha(canonical(review)),
            'extraction_sha256': corpus_sha256,
            'scoring_policy_sha256': rubric_sha256,
        },
        'label': run['label'] + ' · ' + run['effort'],
        'title': run['title'],
        'summary': review['summary'],
        'status': 'profile',
        'score': None,
        'criteria': criteria,
        'findings': findings,
        'usage': {'calls': 0, 'input_tokens': None, 'output_tokens': None, 'cost': None},
    }
    # Historical records preserve the alternatives shape explicitly, even when empty,
    # so normalized presentation structures always carry the field forward.
    if 'alternatives' not in record:
        record['alternatives'] = []
    validate_presentation(record)
    return record


def validate_presentation(record, *, evaluation=None):
    allowed_presentation = {
        'schema_version',
        'presentation_id',
        'evaluation_id',
        'run_id',
        'identities',
        'label',
        'title',
        'summary',
        'status',
        'score',
        'criteria',
        'findings',
        'usage',
    }
    extra_presentation = set(record) - allowed_presentation
    require(
        extra_presentation <= OPTIONAL_PRESENTATION_FIELDS,
        'Presentation has undeclared fields: ' + ', '.join(sorted(extra_presentation)),
    )
    require(
        allowed_presentation <= set(record),
        'Presentation is missing fields: ' + ', '.join(sorted(allowed_presentation - set(record))),
    )
    if 'alternatives' in record:
        require(
            isinstance(record['alternatives'], list) and len(record['alternatives']) <= 32,
            'Presentation alternatives must be a bounded list',
        )
        for alternative in record['alternatives']:
            contracts.bounded_text(alternative, 'presentation alternative', 2000)
    require(record['schema_version'] == 1, 'Unsupported presentation schema')
    contracts.safe_id(record['presentation_id'], 'presentation_id')
    contracts.safe_id(record['evaluation_id'], 'evaluation_id')
    contracts.safe_id(record['run_id'], 'run_id')
    contracts.exact_fields(
        record['identities'],
        ['evaluation_sha256', 'extraction_sha256', 'scoring_policy_sha256'],
        'presentation identities',
    )
    for value in record['identities'].values():
        contracts.digest(value)
    for key in ['label', 'title', 'summary']:
        contracts.bounded_text(record[key], 'presentation.' + key)
    require(record['status'] in contracts.TERMINAL_STATUSES | {'profile'}, 'Invalid presentation status')
    if record['status'] in ['profile', 'unresolved', 'review']:
        require(record['score'] is None, 'Profile, unresolved and review presentations need null score')
    require(
        isinstance(record['criteria'], list) and 1 <= len(record['criteria']) <= 256,
        'Presentation criteria must be a bounded non-empty list',
    )
    criterion_ids = set()
    for criterion in record['criteria']:
        contracts.exact_fields(
            criterion, ['node_id', 'label', 'question', 'scale', 'samples', 'aggregation', 'outcome'], 'criterion'
        )
        contracts.safe_id(criterion['node_id'], 'criterion.node_id')
        require(criterion['node_id'] not in criterion_ids, 'Duplicate presentation criterion')
        criterion_ids.add(criterion['node_id'])
        contracts.bounded_text(criterion['label'], 'criterion.label', 4000)
        contracts.bounded_text(criterion['question'], 'criterion.question')
        require(
            isinstance(criterion['scale'], list) and len(criterion['scale']) <= 256,
            'Criterion scale must be a bounded list',
        )
        require(
            all(
                type(value) in [int, float] and not isinstance(value, bool) and math.isfinite(value)
                for value in criterion['scale']
            ),
            'Criterion scale values must be finite numbers',
        )
        require(
            criterion['scale'] == sorted(set(criterion['scale'])), 'Criterion scale values must be unique and ascending'
        )
        require(
            isinstance(criterion['samples'], list) and 1 <= len(criterion['samples']) <= 20,
            'Criterion samples must be a bounded non-empty list',
        )
        sample_numbers = set()
        for sample in criterion['samples']:
            contracts.exact_fields(
                sample,
                ['sample', 'outcome', 'verdict', 'seed_span', 'rationale', 'citations', 'counter_citations'],
                'criterion sample',
            )
            require(
                type(sample['sample']) is int and sample['sample'] >= 1 and sample['sample'] not in sample_numbers,
                'Criterion sample numbers must be unique and positive',
            )
            sample_numbers.add(sample['sample'])
            contracts.safe_id(sample['outcome'], 'criterion sample outcome')
            contracts.bounded_text(sample['verdict'], 'criterion sample verdict', 400)
            span = sample['seed_span']
            require(
                isinstance(span, list)
                and len(span) == 2
                and all(type(value) is int for value in span)
                and 1 <= span[0] <= span[1],
                'Criterion sample needs a positive seed span',
            )
            contracts.bounded_text(sample['rationale'], 'criterion sample rationale')
            contracts.citations(sample['citations'], 'criterion sample citations')
            require(
                sample['citations'] or sample['outcome'] == 'unassessed', 'Assessed presentation samples need evidence'
            )
            contracts.citations(sample['counter_citations'], 'criterion sample counter citations')
        aggregation = criterion['aggregation']
        contracts.exact_fields(
            aggregation,
            ['strategy', 'sample_count', 'outcome', 'agreement', 'distinct_outcomes'],
            'criterion aggregation',
        )
        require(aggregation['strategy'] in contracts.AGGREGATION_STRATEGIES, 'Invalid criterion aggregation strategy')
        require(aggregation['sample_count'] == len(criterion['samples']), 'Criterion sample count differs')
        contracts.safe_id(aggregation['outcome'], 'criterion aggregation outcome')
        require(
            type(aggregation['agreement']) in [int, float] and 0 <= aggregation['agreement'] <= 1,
            'Invalid criterion agreement',
        )
        require(
            isinstance(aggregation['distinct_outcomes'], list) and aggregation['distinct_outcomes'],
            'Criterion distinct outcomes must be non-empty',
        )
        for value in aggregation['distinct_outcomes']:
            contracts.safe_id(value, 'criterion distinct outcome')
        outcome = criterion['outcome']
        contracts.exact_fields(outcome, ['terminal_id', 'status', 'score', 'rationale'], 'criterion outcome')
        contracts.safe_id(outcome['terminal_id'], 'criterion terminal id')
        require(outcome['status'] in contracts.TERMINAL_STATUSES, 'Invalid criterion terminal status')
        contracts.bounded_text(outcome['rationale'], 'criterion outcome rationale')
        if outcome['status'] == 'scored':
            require(
                type(outcome['score']) in [int, float] and outcome['score'] in criterion['scale'],
                'Scored criterion needs a declared scale value',
            )
        else:
            require(outcome['score'] is None, 'Unresolved and review criteria need null score')
    if record['status'] != 'profile':
        outcome_statuses = {criterion['outcome']['status'] for criterion in record['criteria']}
        expected = 'scored' if outcome_statuses == {'scored'} else 'unresolved'
        require(record['status'] == expected, 'Presentation status differs from criterion outcomes')
    require(isinstance(record['findings'], list) and len(record['findings']) <= 256, 'Invalid presentation findings')
    for finding in record['findings']:
        contracts.bounded_text(finding, 'presentation finding', 8000)
    require(
        isinstance(record['usage'], dict)
        and list(sorted(record['usage'])) in [sorted(fields) for fields in contracts.USAGE_FIELDS.values()],
        'Invalid presentation usage fields',
    )
    require(type(record['usage']['calls']) is int and record['usage']['calls'] >= 0, 'Invalid presentation usage calls')
    inspect_metadata(record, 'presentation record')
    if evaluation is not None:
        require(record['evaluation_id'] == evaluation['evaluation_id'], 'Presentation evaluation identity differs')
        require(record['run_id'] == evaluation['run_id'], 'Presentation run identity differs')
        require(record['label'] == evaluation['presentation']['label'], 'Presentation label differs')
        require(record['title'] == evaluation['presentation']['title'], 'Presentation title differs')
        require(record['summary'] == evaluation['presentation']['summary'], 'Presentation summary differs')
        require(record['findings'] == evaluation['presentation']['findings'], 'Presentation findings differ')
        require(record['usage'] == evaluation['usage'], 'Presentation usage differs')
    return {
        'criteria': len(record['criteria']),
        'samples': sum(len(item['samples']) for item in record['criteria']),
        'status': record['status'],
        'score': record['score'],
    }


def _citations_html(items):
    return ''.join(
        '<li><code>'
        + html.escape(item['source_path'] + ':' + str(item['start']) + '–' + str(item['end']))
        + '</code><pre>'
        + html.escape(item['quote'])
        + '</pre></li>'
        for item in items
    )


def render_html(presentation):
    criteria = presentation['criteria']
    rows = []
    details = []
    alternatives = list(presentation.get('alternatives', []) or [])
    for criterion in criteria:
        for sample in criterion['samples']:
            rows.append(
                '<tr><td>'
                + html.escape(criterion['label'])
                + '</td><td>'
                + str(sample['sample'])
                + '</td><td><strong>'
                + html.escape(sample['outcome'])
                + '</strong></td><td>'
                + html.escape(sample['rationale'])
                + '</td><td>'
                + str(len(sample['citations']))
                + '</td></tr>'
            )
            details.append(
                '<section class="judgment"><p class="eyebrow">'
                + html.escape(criterion['label'])
                + ' · sample '
                + str(sample['sample'])
                + '</p><h3>'
                + html.escape(sample['outcome'])
                + '</h3><p>'
                + html.escape(sample['rationale'])
                + '</p><h4>Supporting evidence</h4><ul>'
                + _citations_html(sample['citations'])
                + '</ul>'
            )
            if sample['counter_citations']:
                details.append('<h4>Counter-evidence</h4><ul>' + _citations_html(sample['counter_citations']) + '</ul>')
            details.append('</section>')
    if alternatives:
        alternatives_html = (
            '<p>Supported alternative readings preserved from the seed reading; '
            'neither is an answer key.</p><ul>'
            + ''.join('<li>' + html.escape(item) + '</li>' for item in alternatives)
            + '</ul>'
        )
    else:
        alternatives_html = '<p>No supported alternative readings were recorded.</p>'
    score = 'No score awarded' if presentation['score'] is None else str(presentation['score'])
    return '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'">
<title>{title} · After the Poem evaluation</title>
<style>
:root{{color-scheme:light dark;--bg:#f2eee5;--ink:#17221d;--muted:#53605a;--line:#c8c1b4;--accent:#8b4939;--panel:#fffaf0}}
@media(prefers-color-scheme:dark){{:root{{--bg:#171c1a;--ink:#f0eadf;--muted:#b6beb9;--line:#3f4944;--accent:#d68b71;--panel:#222a27}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.65 system-ui,sans-serif}}main{{max-width:1100px;margin:auto;padding:clamp(24px,5vw,72px)}}h1{{font:clamp(35px,7vw,72px)/1.05 Georgia,serif;font-weight:400;margin:.25em 0}}h2{{font:30px Georgia,serif;font-weight:400}}h3{{font:22px Georgia,serif;font-weight:400;margin:.25em 0}}.eyebrow{{font:12px ui-monospace,monospace;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}}.status{{display:inline-block;border:1px solid var(--accent);padding:.3em .6em;color:var(--accent)}}.summary{{font-size:20px;max-width:75ch}}.meta{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:1px;background:var(--line);border:1px solid var(--line);margin:32px 0}}.meta div{{background:var(--panel);padding:16px}}table{{width:100%;border-collapse:collapse;margin:16px 0 34px;background:var(--panel)}}caption{{text-align:left;color:var(--muted);padding:10px 0}}th,td{{text-align:left;vertical-align:top;padding:11px;border:1px solid var(--line)}}th{{font-size:13px}}.judgment{{border-top:1px solid var(--line);padding:24px 0}}ul{{padding-left:22px}}li{{margin:12px 0}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--panel);border-left:3px solid var(--accent);padding:10px;font:13px/1.6 ui-monospace,monospace}}footer{{border-top:1px solid var(--line);margin-top:30px;padding-top:16px;color:var(--muted);font-size:13px}}.table-scroll{{overflow-x:auto;overscroll-behavior-inline:contain;margin:16px 0 34px;border:1px solid var(--line)}}@media(max-width:600px){{main{{padding:18px}}table{{display:table;min-width:720px;margin:0}}thead{{display:table-header-group}}tbody{{display:table-row-group}}tr{{display:table-row}}th,td{{display:table-cell}}}}
</style></head><body><main>
<p class="eyebrow">After the Poem · normalized evaluation</p>
<h1>{title}</h1><p class="status">{status} · {score}</p><p class="summary">{summary}</p>
<div class="meta"><div><span class="eyebrow">Model label</span><br>{label}</div><div><span class="eyebrow">Run</span><br>{run_id}</div><div><span class="eyebrow">Evaluation</span><br><code>{evaluation_id}</code></div><div><span class="eyebrow">Calls</span><br>{calls}</div></div>
<section><h2>Repeated judgments</h2><p>The table is the primary comparison. Every sample remains visible; agreement is not correctness.</p>
<div class="table-scroll" tabindex="0" aria-label="Repeated judgment samples, horizontally scrollable on narrow screens"><table><caption>Judgment samples and outcomes</caption><thead><tr><th>Criterion</th><th>Sample</th><th>Outcome</th><th>Rationale</th><th>Citations</th></tr></thead><tbody>{rows}</tbody></table></div></section>
<section><h2>Evidence</h2>{details}</section>
<section><h2>Alternative readings</h2>{alternatives}</section>
<section><h2>Terminal result</h2><p>Declared scale: <strong>{scale}</strong></p><p>Aggregation: <strong>{aggregation}</strong> · agreement {agreement} · distinct outcomes {distinct}</p><p>{outcome}</p></section>
<footer>No candidate code was executed to prepare this page. Missing or conflicting evidence remains unresolved. Presentation identity <code>{presentation_id}</code>.</footer>
</main></body></html>
'''.format(
        title=html.escape(presentation['title']),
        status=html.escape(presentation['status']),
        score=html.escape(score),
        summary=html.escape(presentation['summary']),
        label=html.escape(presentation['label']),
        run_id=html.escape(presentation['run_id']),
        evaluation_id=html.escape(presentation['evaluation_id']),
        calls=presentation['usage']['calls'],
        rows=''.join(rows),
        details=''.join(details),
        scale=html.escape(', '.join(str(value) for value in criteria[0]['scale'])),
        aggregation=html.escape(criteria[0]['aggregation']['outcome']),
        agreement=criteria[0]['aggregation']['agreement'],
        distinct=html.escape(', '.join(criteria[0]['aggregation']['distinct_outcomes'])),
        outcome=html.escape(criteria[0]['outcome']['rationale']),
        presentation_id=html.escape(presentation['presentation_id']),
        alternatives=alternatives_html,
    )


def prepare_page(project, evaluation_directory, output, *, presentation_id=None):
    import scoring_pipeline

    project, evaluation_directory, output = (
        Path(project).resolve(),
        Path(evaluation_directory).resolve(),
        Path(output).resolve(),
    )
    require(not output.exists(), 'Presentation output already exists; use a new directory')
    validated = scoring_pipeline.validate_output(project, evaluation_directory)
    source_texts = _source_texts(project, validated['run_id'])
    evaluation_bytes = (evaluation_directory / 'evaluation.json').read_bytes()
    extraction_bytes = (evaluation_directory / 'extraction.json').read_bytes()
    policy_bytes = (evaluation_directory / 'scoring-policy.json').read_bytes()
    evaluation = contracts.load_json(evaluation_directory / 'evaluation.json')
    extraction = contracts.load_json(evaluation_directory / 'extraction.json')
    policy = contracts.load_json(evaluation_directory / 'scoring-policy.json')
    # Example and material policies need not encode the current rubric, so page preparation checks none.
    carriage = None
    carriage_path = evaluation_directory / 'carriage.json'
    if carriage_path.is_file():
        try:
            carriage = contracts.load_json(carriage_path)
        except (OSError, ValueError):
            carriage = None
    presentation = build_presentation(
        evaluation,
        extraction,
        policy,
        evaluation_sha256=sha(evaluation_bytes),
        extraction_sha256=sha(extraction_bytes),
        scoring_policy_sha256=sha(policy_bytes),
        source_texts=source_texts,
        carriage=carriage,
    )
    if presentation_id:
        require(contracts.SAFE_ID.fullmatch(presentation_id), 'Invalid presentation ID')
        presentation['presentation_id'] = presentation_id
        validate_presentation(presentation, evaluation=evaluation)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.' + output.name + '.', dir=str(output.parent)))
    staging.chmod(0o700)
    try:
        fsutil.write_bytes_atomic(staging / 'evaluation.json', (evaluation_directory / 'evaluation.json').read_bytes())
        fsutil.write_bytes_atomic(staging / 'extraction.json', (evaluation_directory / 'extraction.json').read_bytes())
        fsutil.write_bytes_atomic(staging / 'scoring-policy.json', (evaluation_directory / 'scoring-policy.json').read_bytes())
        fsutil.write_json_atomic(staging / 'presentation.json', presentation)
        fsutil.write_text_atomic(staging / 'index.html', render_html(presentation))
        manifest = {
            'schema_version': 1,
            'files': {name: sha((staging / name).read_bytes()) for name in sorted(PRESENTATION_FILES)},
        }
        fsutil.write_json_atomic(staging / 'manifest.json', manifest)
        result = validate_output(project, staging)
        require(not output.exists(), 'Presentation output already exists; use a new directory')
        os.replace(staging, output)
        return result
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _source_texts(project, run_id, data=None):
    import atlas

    data = data or atlas.validate(project)
    return {source['path']: source['text'] for source in data['corpora'][run_id]['files'] if source['text'] is not None}


def validate_output(project, directory, *, data=None):
    """Validate a prepared page against its normalized records; it holds no session evidence."""
    import atlas
    import extraction_pipeline

    project, directory = Path(project).resolve(), Path(directory).resolve()
    require(
        {path.name for path in directory.iterdir() if path.is_file()} == PRESENTATION_FILES | {'manifest.json'},
        'Presentation directory has unexpected files',
    )
    data = data or atlas.validate(project)
    extraction_pipeline.validate_output(project, directory / 'extraction.json', data=data)
    record = contracts.load_json(directory / 'evaluation.json')
    contracts.validate_evaluation(
        record,
        extraction=contracts.load_json(directory / 'extraction.json'),
        extraction_sha256=sha((directory / 'extraction.json').read_bytes()),
        policy=contracts.load_json(directory / 'scoring-policy.json'),
        scoring_policy_sha256=sha((directory / 'scoring-policy.json').read_bytes()),
        source_texts=_source_texts(project, record['run_id'], data),
    )
    presentation = contracts.load_json(directory / 'presentation.json')
    evaluation = contracts.load_json(directory / 'evaluation.json')
    result = validate_presentation(presentation, evaluation=evaluation)
    identities = {
        'evaluation_sha256': sha((directory / 'evaluation.json').read_bytes()),
        'extraction_sha256': sha((directory / 'extraction.json').read_bytes()),
        'scoring_policy_sha256': sha((directory / 'scoring-policy.json').read_bytes()),
    }
    require(presentation['identities'] == identities, 'Presentation identity differs from its inputs')
    manifest = contracts.load_json(directory / 'manifest.json')
    contracts.exact_fields(manifest, ['schema_version', 'files'], 'presentation manifest')
    require(manifest['schema_version'] == 1, 'Unsupported presentation manifest')
    require(set(manifest['files']) == PRESENTATION_FILES, 'Presentation manifest file set differs')
    for name, expected in manifest['files'].items():
        contracts.digest(expected)
        require(sha((directory / name).read_bytes()) == expected, 'Presentation file changed: ' + name)
    # The input hashes are echoed so a caller can recheck them without rehashing.
    result.update(
        presentation_id=presentation['presentation_id'],
        presentation_sha256=sha((directory / 'presentation.json').read_bytes()),
        html_sha256=sha((directory / 'index.html').read_bytes()),
        record_sha256=sha(contracts.canonical(presentation)),
        **identities,
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=ROOT)
    parser.add_argument('--evaluation', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--presentation-id')
    args = parser.parse_args()
    try:
        result = prepare_page(args.project, args.evaluation, args.output, presentation_id=args.presentation_id)
    except (ValueError, OSError, KeyError, TypeError) as error:
        print('Page preparation stopped: ' + str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
