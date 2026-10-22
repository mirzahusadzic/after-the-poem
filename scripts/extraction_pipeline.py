#!/usr/bin/env python3
"""Prepare evidence and run source-only semantic extraction through Codex and the router."""

import argparse
import json
import sys
from pathlib import Path

import atlas
import codex_worker as worker
import evaluation_contract as contracts
import session_flow
from bundles import canonical, inspect_metadata, load_json, require, sha


ROOT = Path(__file__).resolve().parents[1]
PIPELINE_FILES = [
    'evaluation_contract.py',
    'extraction_pipeline.py',
    'session_flow.py',
    'codex_worker.py',
    'sealed_run.py',
    'bundles.py',
    'atlas.py',
]
RECORD_SCHEMA = 2
EXTRACTION_TASK = (
    '''TASK extraction
Inventory the evidence that matters for reading this artefact: claims, actors,
relationships, conditions, boundaries, questions, continuation affordances and
genuine ambiguities. Keep the inventory focused; 20 to 60 items usually suffice.
Do not assign scores, infer hidden motive or repair the artefact.

Answer with one JSON object and nothing else:
{"items": [{"id": "kebab-case-id", "kind": KIND, "statement": "...",
            "citations": [{"source_id": "source-0001", "start": 1, "end": 3}],
            "ambiguity": null}],
 "unknowns": ["..."]}
KIND is one of: '''
    + ', '.join(sorted(contracts.ITEM_KINDS))
    + '''.
Rules: every item cites 1 to 32 inclusive line ranges inside the numbered source
lines; do not copy quotation text, because the host resolves it; `ambiguity` is
null or a short explanation; item IDs are unique; `unknowns` lists what the
evidence cannot settle (it may be empty).
'''
)


# Version 2 states the citation rule that live readings broke under version 1.
CITATION_RULE = (
    'Citations name only the SOURCE IDs listed in the evidence (source-0001, ...); '
    'the seed is never a citation source.'
)
# Version 3 asks judgments for a few decisive citations instead of exhaustive lists.
# Version 4 adds the seed reading and carriage map to the compact reading.
# Version 5 gives the reader the reading the study measures, a stance, a standing map and a lens per criterion.
PROMPT_VERSION = 5


def extraction_prompt(version=PROMPT_VERSION):
    return EXTRACTION_TASK + (CITATION_RULE + '\n' if version >= 2 else '')


class InvalidExtractionAnswer(ValueError):
    """The worker completed, but its answer could not form a valid extraction."""


def code_identity():
    return sha(canonical({name: sha((ROOT / 'scripts' / name).read_bytes()) for name in PIPELINE_FILES}))


def prepare_sources(project, run_id, *, data=None):
    """Return the run, seed lines and numbered text sources with stable source IDs."""
    require(atlas.safe_id(run_id), 'Invalid run ID')
    data = data or atlas.validate(project)
    run = next((item for item in data['runs'] if item['id'] == run_id), None)
    require(run is not None, 'Unknown run: ' + run_id)
    corpus = data['corpora'][run_id]
    prompt = (Path(project) / 'corpus/prompts' / (run['prompt_sha256'] + '.txt')).read_text(encoding='utf-8')
    sources, mapping, source_texts, manifest = [], {}, {}, []
    for index, source in enumerate(corpus['files'], 1):
        if source['text'] is None:
            continue
        source_id = 'source-' + str(index).zfill(4)
        mapping[source_id] = source['path']
        source_texts[source['path']] = source['text']
        sources.append(
            {
                'source_id': source_id,
                'path': source['path'],
                'format': Path(source['path']).suffix,
                'lines': source['text'].splitlines(),
            }
        )
        manifest.append({'path': source['path'], 'sha256': source['sha256'], 'lines': len(source['text'].splitlines())})
    require(sources, 'Run has no retained text sources to read')
    require(
        any(any(line.strip() for line in source['lines']) for source in sources),
        'Run has no non-empty retained source line to read',
    )
    return {
        'run': run,
        'seed_lines': prompt.splitlines(),
        'sources': sources,
        'source_map': mapping,
        'source_texts': source_texts,
        'manifest': manifest,
        'data': data,
    }


def identity(prepared, extraction_id):
    run = prepared['run']
    value = {
        'extraction_id': extraction_id,
        'run_id': run['id'],
        'prompt_sha256': run['prompt_sha256'],
        'corpus_sha256': run['corpus_sha256'],
        'candidate_sha256': run['corpus_sha256'],
        'source_map': prepared['source_map'],
        'sources': prepared['manifest'],
    }
    inspect_metadata(value, 'extraction identity')
    return value


def synthetic_answer(prepared):
    source = next(source for source in prepared['sources'] if any(line.strip() for line in source['lines']))
    line_number = next(index for index, line in enumerate(source['lines'], 1) if line.strip())
    return json.dumps(
        {
            'items': [
                {
                    'id': 'synthetic-source-reading',
                    'kind': 'claim',
                    'statement': 'Synthetic extraction fixture; no semantic assessment was performed.',
                    'citations': [{'source_id': source['source_id'], 'start': line_number, 'end': line_number}],
                    'ambiguity': None,
                }
            ],
            'unknowns': ['No model inference occurred.'],
        },
        ensure_ascii=False,
    )


def resolve_citations(citations, source_map, source_texts, label, repairs, *, minimum):
    """Resolve answer citations to exact ranges; blank-line and final-newline slips are noted, never hidden."""
    require(isinstance(citations, list) and len(citations) <= 32, 'Invalid ' + label)
    resolved = []
    for citation in citations:
        contracts.exact_fields(citation, ['source_id', 'start', 'end'], label + ' citation')
        require(citation['source_id'] in source_map, label + ' names an unknown source ID')
        path = source_map[citation['source_id']]
        quote, repaired = contracts.resolve_line_range(source_texts[path], citation['start'], citation['end'])
        end = citation['end'] - 1 if repaired else citation['end']
        if repaired:
            repairs.append(label + ': normalized trailing-newline line range')
        if not quote.strip():
            repairs.append(label + ': ignored blank-line citation ' + path + ':' + str(citation['start']))
            continue
        resolved.append({'source_path': path, 'start': citation['start'], 'end': end})
    require(len(resolved) >= minimum, label + ' has no non-blank evidence after normalization')
    return resolved


def check_receipt(receipt):
    require(receipt.get('status') == 'completed', 'Worker did not complete')
    require(
        receipt.get('dry_run') or receipt.get('runtime_checks', {}).get('status') == 'passed',
        'Worker runtime integrity was not verified',
    )


def record_from_answer(answer, ident, source_texts, receipt, *, answer_sha256, receipt_sha256):
    """Normalize a parsed extraction answer into a schema-2 extraction record."""
    contracts.exact_fields(answer, ['items', 'unknowns'], 'extraction answer')
    require(
        isinstance(answer['items'], list) and 1 <= len(answer['items']) <= 4096,
        'Extraction answer items must be a bounded non-empty list',
    )
    require(
        isinstance(answer['unknowns'], list) and len(answer['unknowns']) <= 128,
        'Extraction unknowns must be a bounded list',
    )
    items, repairs = [], []
    for item in answer['items']:
        contracts.exact_fields(item, ['id', 'kind', 'statement', 'citations', 'ambiguity'], 'extraction answer item')
        require(isinstance(item['citations'], list) and item['citations'], 'Extraction item needs evidence')
        citations = resolve_citations(
            item['citations'], ident['source_map'], source_texts, 'item ' + str(item['id']), repairs, minimum=1
        )
        items.append(dict(item, citations=citations))
    requested, runtime = receipt['requested'], receipt['runtime']
    record = {
        'schema_version': RECORD_SCHEMA,
        'extraction_id': ident['extraction_id'],
        'run_id': ident['run_id'],
        'identities': {key: ident[key] for key in ['prompt_sha256', 'corpus_sha256', 'candidate_sha256']},
        'extractor': {
            'gateway': worker.GATEWAY,
            'model': requested['model'],
            'reasoning': requested['reasoning'],
            'codex_version': runtime['codex_version'],
            'router_revision': runtime['router']['revision'] if runtime.get('router') else '0' * 64,
            'pipeline_sha256': code_identity(),
            'answer_sha256': answer_sha256,
            'receipt_sha256': receipt_sha256,
        },
        'sources': ident['sources'],
        'items': items,
        'unknowns': answer['unknowns'] + repairs,
    }
    contracts.validate_extraction(record, source_texts=source_texts)
    return record


def validate_answer(answer_path, ident, source_texts, receipt, *, receipt_sha256):
    """Normalize one completed extraction answer file into a schema-2 extraction record."""
    try:
        check_receipt(receipt)
        path = Path(answer_path)
        require(path.is_file() and not path.is_symlink(), 'Extraction answer is missing')
        return record_from_answer(
            contracts.load_json(path),
            ident,
            source_texts,
            receipt,
            answer_sha256=sha(path.read_bytes()),
            receipt_sha256=receipt_sha256,
        )
    except ValueError as error:
        raise InvalidExtractionAnswer(str(error)) from error


def extraction_task(prepared, ident, *, dry_run, version=PROMPT_VERSION):
    return session_flow.Task(
        id='extraction',
        prompt=extraction_prompt(version),
        validate=lambda answer, receipt, receipt_sha256: {
            'extraction': validate_answer(
                answer, ident, prepared['source_texts'], receipt, receipt_sha256=receipt_sha256
            )
        },
        fake=synthetic_answer(prepared) if dry_run else None,
        answer_tokens=12000,
    )


def is_synthetic(record):
    return record['extractor']['codex_version'] == 'synthetic-no-inference'


def _write_record(output, record):
    path = output / 'extraction.json'
    temporary = output / '.extraction.json.tmp'
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)
    return path


def run(
    project,
    run_id,
    output,
    *,
    model,
    reasoning='high',
    extraction_id=None,
    dry_run=False,
    accept_run=False,
    router_url='http://127.0.0.1:18766/v1',
    catalog=None,
    router_profile=None,
    capabilities=None,
    budget=None,
    repairs=2,
):
    """Run a one-task evidence session. Prefer the evaluation pipeline, which extracts in the scoring session."""
    project, output = Path(project).resolve(), Path(output).resolve()
    require(dry_run or accept_run, 'Live extraction requires --accept-run')
    budget = session_flow.validate_budget(budget, required=not dry_run)
    extraction_id = extraction_id or ('extraction-' + run_id)
    require(contracts.SAFE_ID.fullmatch(extraction_id), 'Invalid extraction ID')
    prepared = prepare_sources(project, run_id)
    ident = identity(prepared, extraction_id)
    evidence = session_flow.format_evidence(prepared['seed_lines'], prepared['sources'])
    task = extraction_task(prepared, ident, dry_run=dry_run)
    plan = dict(
        ident,
        evidence_sha256=sha(evidence.encode('utf-8')),
        task_sha256=sha(task.prompt.encode('utf-8')),
        model=model,
        reasoning=reasoning,
        dry_run=dry_run,
        prompt_version=PROMPT_VERSION,
    )
    if output.exists():
        require(load_json(output / 'plan.json') == plan, 'Preserved extraction plan differs; use a new directory')
    else:
        output.mkdir(parents=True, mode=0o700)
        (output / 'plan.json').write_bytes(canonical(plan) + b'\n')
        (output / 'plan.json').chmod(0o400)
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
    if budget is not None:
        estimate = session_flow.estimate(evidence, [[task]])
        require(
            estimate['input_tokens'] <= budget['max_input_tokens'],
            'Input budget is below the planned minimum of ~' + str(estimate['input_tokens']) + ' tokens',
        )
    accepted = session_flow.run_session(
        output / 'sessions/01',
        [task],
        evidence=evidence,
        job=job,
        runtime=runtime,
        catalog=output / 'catalog.json',
        router_url=router_url,
        dry_run=dry_run,
        budget=budget,
        repairs=repairs,
        spent=spent,
    )
    record, _, attempt = accepted['extraction']
    path = _write_record(output, record)
    result = {
        'status': 'completed',
        'dry_run': dry_run,
        'run_id': run_id,
        'extraction_id': extraction_id,
        'gateway': worker.GATEWAY,
        'attempts': attempt,
        'item_count': len(record['items']),
        'usage': session_flow.usage_totals(spent, (budget or {}).get('pricing')),
        'extraction_sha256': sha(path.read_bytes()),
    }
    (output / 'status.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result


def validate_output(project, record_path, *, data=None):
    project, record_path = Path(project).resolve(), Path(record_path).resolve()
    data = data or atlas.validate(project)
    record = contracts.load_json(record_path)
    run_id = record.get('run_id')
    require(run_id in data['corpora'], 'Extraction names an unknown run')
    run = next(item for item in data['runs'] if item['id'] == run_id)
    corpus = data['corpora'][run_id]
    source_texts = {source['path']: source['text'] for source in corpus['files'] if source['text'] is not None}
    require(
        record.get('identities', {}).get('prompt_sha256') == run['prompt_sha256'],
        'Extraction prompt identity differs from the selected run',
    )
    require(
        record.get('identities', {}).get('corpus_sha256') == run['corpus_sha256'],
        'Extraction corpus identity differs from the selected run',
    )
    expected_sources = {
        source['path']: {'path': source['path'], 'sha256': source['sha256'], 'lines': len(source['text'].splitlines())}
        for source in corpus['files']
        if source['text'] is not None
    }
    require(
        {source['path']: source for source in record.get('sources', [])} == expected_sources,
        'Extraction source manifest differs from the selected run',
    )
    contracts.validate_extraction(record, source_texts=source_texts)
    return {
        'run_id': run_id,
        'extraction_id': record['extraction_id'],
        'synthetic': is_synthetic(record),
        'item_count': len(record['items']),
        'extraction_sha256': sha(record_path.read_bytes()),
    }


def recover(project, output):
    """Rebuild extraction.json from a preserved completed session without inference."""
    project, output = Path(project).resolve(), Path(output).resolve()
    plan = load_json(output / 'plan.json')
    prepared = prepare_sources(project, plan['run_id'])
    ident = identity(prepared, plan['extraction_id'])
    evidence = session_flow.format_evidence(prepared['seed_lines'], prepared['sources'])
    version = plan.get('prompt_version', 1)
    task = extraction_task(prepared, ident, dry_run=False, version=version)
    expected = dict(
        ident,
        evidence_sha256=sha(evidence.encode('utf-8')),
        task_sha256=sha(task.prompt.encode('utf-8')),
        model=plan['model'],
        reasoning=plan['reasoning'],
        dry_run=plan['dry_run'],
    )
    if version != 1:
        expected['prompt_version'] = version
    require(plan == expected, 'Preserved extraction plan differs from the current evidence')
    accepted, receipts = session_flow.verify_session(output / 'sessions/01', [task], evidence)
    record = accepted['extraction'][0]
    path = _write_record(output, record)
    result = {
        'status': 'completed',
        'recovered': True,
        'run_id': plan['run_id'],
        'extraction_id': plan['extraction_id'],
        'gateway': worker.GATEWAY,
        'item_count': len(record['items']),
        'usage': session_flow.usage_totals(receipts),
        'extraction_sha256': sha(path.read_bytes()),
    }
    (output / 'status.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    validate_output(project, path, data=prepared['data'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_id')
    parser.add_argument('--project', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--extraction-id')
    parser.add_argument('--model', required=True)
    parser.add_argument('--reasoning', choices=sorted(contracts.REASONING), default='high')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--accept-run', action='store_true')
    args = parser.parse_args()
    try:
        result = run(
            args.project,
            args.run_id,
            args.output,
            model=args.model,
            reasoning=args.reasoning,
            extraction_id=args.extraction_id,
            dry_run=args.dry_run,
            accept_run=args.accept_run,
        )
    except (ValueError, OSError, KeyError, TypeError) as error:
        print('Extraction stopped: ' + str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
