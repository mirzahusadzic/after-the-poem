#!/usr/bin/env python3
"""Validate and import immutable Run Bundle v1 directories without execution."""

import argparse
import datetime as dt
import fsutil
import hashlib
import json
import os
import re
import sys
import unicodedata
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
SAFE_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,99}')
DIGEST = re.compile(r'[a-f0-9]{64}')
CONTROL = re.compile(r'[\x00-\x1f\x7f]')
FORBIDDEN_KEYS = {
    'access_token',
    'api_key',
    'api-key',
    'apikey',
    'authorization',
    'bearer_token',
    'cookie',
    'credentials',
    'password',
    'refresh_token',
    'secret',
    'session_token',
}
HOST_PATH = re.compile(r'(^|["\s])(/(?:Users|home|private|var/folders|tmp)/|[A-Za-z]:\\)')


class BundleError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise BundleError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def load_json(path):
    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'Duplicate JSON key in ' + path.name + ': ' + key)
            result[key] = value
        return result

    try:
        return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BundleError('Invalid JSON in ' + path.name + ': ' + str(error)) from error


def safe_path(value, label='path'):
    require(isinstance(value, str) and value, 'Missing ' + label)
    require(
        len(value) <= 1024 and '\\' not in value and not CONTROL.search(value), 'Unsafe ' + label + ': ' + repr(value)
    )
    path = PurePosixPath(value)
    require(not path.is_absolute() and '..' not in path.parts, 'Unsafe ' + label + ': ' + value)
    require(str(path) == value and path.parts and path.parts[0] != '.', 'Non-canonical ' + label + ': ' + value)
    require(unicodedata.is_normalized('NFC', value), 'Non-NFC ' + label + ': ' + value)
    return path


def inspect_metadata(value, where='run.json'):
    if isinstance(value, dict):
        for key, item in value.items():
            require(key.lower() not in FORBIDDEN_KEYS, 'Credential-like metadata key in ' + where + ': ' + key)
            inspect_metadata(item, where + '.' + key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            inspect_metadata(item, where + '[' + str(index) + ']')
    elif isinstance(value, str):
        require(not HOST_PATH.search(value), 'Host-specific path in ' + where)


def timestamp(value, field):
    require(isinstance(value, str) and value, 'Missing timestamp: ' + field)
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as error:
        raise BundleError('Invalid timestamp: ' + field) from error
    require(parsed.tzinfo is not None, 'Timestamp needs a timezone: ' + field)
    return parsed


def validate_run_record(record, prompt_hash):
    require(record.get('schema_version') == 1, 'Unsupported run schema')
    for field in ['run_id', 'seed_id']:
        require(SAFE_ID.fullmatch(record.get(field, '')), 'Invalid ' + field)
    require(record.get('prompt_sha256') == prompt_hash, 'Prompt identity differs between run.json and prompt.txt')
    cohort = record.get('cohort')
    require(isinstance(cohort, dict), 'Missing cohort identity')
    require(SAFE_ID.fullmatch(cohort.get('id', '')), 'Invalid cohort ID')
    require(isinstance(cohort.get('title'), str) and cohort['title'].strip(), 'Missing cohort title')
    require(type(cohort.get('baseline')) is bool, 'Cohort baseline must be explicit')
    presentation = record.get('presentation')
    require(isinstance(presentation, dict), 'Missing presentation metadata')
    require(isinstance(presentation.get('label'), str) and presentation['label'].strip(), 'Missing run label')
    require(isinstance(presentation.get('title'), str) and presentation['title'].strip(), 'Missing run title')
    outcome = record.get('outcome')
    require(isinstance(outcome, dict), 'Missing outcome record')
    require(
        outcome.get('status') in ['completed', 'failed', 'timed_out', 'cancelled', 'partial'], 'Invalid outcome status'
    )
    started = timestamp(outcome.get('started_at'), 'outcome.started_at')
    ended = timestamp(outcome.get('ended_at'), 'outcome.ended_at')
    require(ended >= started, 'Run end precedes start')
    require(isinstance(outcome.get('stop_reason'), str) and outcome['stop_reason'].strip(), 'Missing stop reason')
    require(isinstance(outcome.get('errors'), list), 'Outcome errors must be a list')
    require(type(outcome.get('partial_output')) is bool, 'partial_output must be explicit')
    truncation = outcome.get('truncation')
    require(
        isinstance(truncation, dict) and type(truncation.get('truncated')) is bool, 'Truncation state must be explicit'
    )
    require(not truncation['truncated'] or truncation.get('reason'), 'Truncation needs a reason')
    condition = record.get('condition')
    require(isinstance(condition, dict), 'Missing condition record')
    for section in ['requested', 'reported', 'harness', 'isolation', 'budgets']:
        require(isinstance(condition.get(section), dict), 'Missing condition.' + section)
    for section in ['requested']:
        require(
            isinstance(condition[section].get('model'), str) and condition[section]['model'].strip(),
            'Missing model in ' + section,
        )
        require(
            isinstance(condition[section].get('provider'), str) and condition[section]['provider'].strip(),
            'Missing provider in ' + section,
        )
    for field in ['model', 'provider']:
        require(field in condition['reported'], 'Missing reported.' + field)
        value = condition['reported'][field]
        require(value is None or isinstance(value, str) and value.strip(), 'Invalid reported.' + field)
    require(
        condition['requested'].get('router_revision') is None
        or isinstance(condition['requested'].get('router_revision'), str),
        'router_revision must be a string or null',
    )
    require(
        condition['requested'].get('routing_policy') is None
        or isinstance(condition['requested'].get('routing_policy'), (str, dict)),
        'routing_policy must be a string, object or null',
    )
    harness = condition['harness']
    require(isinstance(harness.get('name'), str) and harness['name'].strip(), 'Harness name is required')
    require(
        isinstance(harness.get('version'), (str, int, float)) and str(harness['version']).strip(),
        'Harness version is required',
    )
    require(
        isinstance(harness.get('reasoning_effort'), str) and harness['reasoning_effort'].strip(),
        'Harness effort is required',
    )
    require(
        isinstance(harness.get('tools'), list) and isinstance(harness.get('runtime'), dict),
        'Harness tools and runtime must be explicit',
    )
    isolation = condition['isolation']
    require(isolation.get('mode') and isolation.get('network'), 'Isolation policy is incomplete')
    budgets = condition['budgets']
    require(
        'wall_time_seconds' in budgets and 'input_tokens' in budgets and 'output_tokens' in budgets,
        'Budget fields are incomplete',
    )
    require('cost' in budgets, 'Cost budget must be explicit, including null')
    require(
        all(
            value is None or type(value) is int and value >= 0
            for value in [budgets['wall_time_seconds'], budgets['input_tokens'], budgets['output_tokens']]
        ),
        'Budget values must be non-negative integers or null',
    )
    require(budgets['cost'] is None or isinstance(budgets['cost'], dict), 'Cost budget must be an object or null')
    require('usage' in record and isinstance(record['usage'], dict), 'Usage counters must be explicit')
    require('cost' in record and isinstance(record['cost'], dict), 'Cost provenance must be explicit')
    require(
        record['cost'].get('amount') is None or isinstance(record['cost'].get('amount'), (int, float)),
        'Cost amount must be numeric or null',
    )
    require(
        record['cost'].get('currency') is None or isinstance(record['cost'].get('currency'), str),
        'Cost currency must be a string or null',
    )
    require(
        isinstance(record['cost'].get('provenance'), str) and record['cost']['provenance'].strip(),
        'Cost provenance is required',
    )
    inspect_metadata(record)
    return started, ended


def validate_manifest(manifest, candidate):
    require(manifest.get('schema_version') == 1, 'Unsupported manifest schema')
    files = manifest.get('files')
    require(isinstance(files, list), 'Manifest files must be a list')
    expected = {}
    normalized = set()
    for entry in files:
        relative = safe_path(entry.get('path'), 'manifest path')
        key = unicodedata.normalize('NFC', str(relative)).casefold()
        require(key not in normalized, 'Case-folded duplicate manifest path: ' + str(relative))
        normalized.add(key)
        require(relative not in expected, 'Duplicate manifest path: ' + str(relative))
        require(DIGEST.fullmatch(entry.get('sha256', '')), 'Invalid manifest digest: ' + str(relative))
        require(type(entry.get('bytes')) is int and entry['bytes'] >= 0, 'Invalid manifest size: ' + str(relative))
        expected[relative] = entry
    actual = {}
    for path in candidate.rglob('*'):
        require(not path.is_symlink(), 'Candidate contains a symlink: ' + str(path.relative_to(candidate)))
        if path.is_dir():
            continue
        require(path.is_file(), 'Candidate contains a non-regular file')
        relative = PurePosixPath(path.relative_to(candidate).as_posix())
        actual[relative] = path
    require(set(actual) == set(expected), 'Manifest and candidate file sets differ')
    for relative, path in actual.items():
        data = path.read_bytes()
        entry = expected[relative]
        require(len(data) == entry['bytes'], 'Candidate byte count differs: ' + str(relative))
        require(sha(data) == entry['sha256'], 'Candidate hash differs: ' + str(relative))
    return expected, actual


def validate_observations(observations):
    require(observations.get('schema_version') == 1, 'Unsupported observations schema')
    require(
        isinstance(observations.get('attribution'), str) and observations['attribution'].strip(),
        'Observation attribution required',
    )
    require(
        isinstance(observations.get('summary'), str) and observations['summary'].strip(), 'Observation summary required'
    )
    require(isinstance(observations.get('checks'), list), 'Observation checks must be a list')
    inspect_metadata(observations, 'observations.json')


def validate_bundle(bundle):
    bundle = Path(bundle).resolve()
    require(bundle.is_dir(), 'Bundle directory does not exist')
    for path in bundle.rglob('*'):
        require(not path.is_symlink(), 'Bundle contains a symlink: ' + str(path.relative_to(bundle)))
    required = {'run.json', 'prompt.txt', 'candidate', 'manifest.json', 'observations.json'}
    require({p.name for p in bundle.iterdir()} == required, 'Bundle must contain exactly the v1 entry points')
    prompt = (bundle / 'prompt.txt').read_bytes()
    require(bool(prompt.strip()), 'Prompt cannot be empty')
    prompt_hash = sha(prompt)
    run = load_json(bundle / 'run.json')
    started, ended = validate_run_record(run, prompt_hash)
    manifest_bytes = (bundle / 'manifest.json').read_bytes()
    observation_bytes = (bundle / 'observations.json').read_bytes()
    manifest = load_json(bundle / 'manifest.json')
    observations = load_json(bundle / 'observations.json')
    inspect_metadata(manifest, 'manifest.json')
    validate_observations(observations)
    expected, actual = validate_manifest(manifest, bundle / 'candidate')
    digest = sha(
        canonical(
            {
                'prompt_sha256': prompt_hash,
                'run_sha256': sha((bundle / 'run.json').read_bytes()),
                'manifest_sha256': sha(manifest_bytes),
                'observations_sha256': sha(observation_bytes),
            }
        )
    )
    duration = (ended - started).total_seconds()
    return dict(
        path=bundle,
        run=run,
        prompt=prompt,
        prompt_sha256=prompt_hash,
        manifest=manifest,
        manifest_sha256=sha(manifest_bytes),
        files=expected,
        paths=actual,
        observations=observations,
        observations_sha256=sha(observation_bytes),
        bundle_sha256=digest,
        elapsed_seconds=duration,
    )


def projection(relative, data):
    text = None
    omission = None
    if PurePosixPath(relative).name == '.DS_Store':
        omission = 'Finder metadata: hash only'
    else:
        try:
            text = data.decode('utf-8')
            if '\x00' in text:
                text, omission = None, 'Binary material: hash only'
        except UnicodeDecodeError:
            omission = 'Binary material: hash only'
    return dict(path=str(relative), bytes=len(data), sha256=sha(data), text=text, omission=omission)


def atomic_json(path, value):
    fsutil.write_json_atomic(path, value)


def _write_new_bytes(path, data, label):
    """Stage a new file atomically; a retry resumes identical content or raises BundleError.

    The existence check comes first so a retry after an interrupted import can
    never surface a raw FileExistsError, and the write itself goes through the
    atomic temp-file writer so readers never see a half-written record.
    """
    if path.exists():
        try:
            existing = path.read_bytes()
        except OSError as read_error:
            raise BundleError(label + ' already exists and cannot be re-read: ' + str(read_error))
        if existing == data:
            return False
        raise BundleError(label + ' already exists with different content; clear the partial import before retrying')
    fsutil.write_bytes_atomic(path, data)
    return True


def unreviewed_record(data, rubric, rubric_hash, date):
    run_id = data['run']['run_id']
    return {
        'schema_version': 1,
        'id': run_id + '-unreviewed-v1',
        'run_id': run_id,
        'date': date,
        'reviewer': 'Bundle admission pipeline',
        'status': 'unreviewed',
        'basis': 'static-artifact',
        'rubric_version': rubric['version'],
        'rubric_sha256': rubric_hash,
        'corpus_sha256': data['corpus_sha256'],
        'supersedes': None,
        'summary': 'No interpretive assessment has been recorded for this admitted run.',
        'live_cooperation': 'unassessed',
        'scores': {
            axis['id']: {
                'value': None,
                'confidence': 'unassessed',
                'rationale': 'Not assessed at admission.',
                'citations': [],
                'seed_span': [1, 1],
            }
            for axis in rubric['axes']
        },
    }


def import_bundle(bundle, project=ROOT):
    data = validate_bundle(bundle)
    project = Path(project).resolve()
    from atlas import validate as validate_project

    validate_project(project)
    registry_path = project / 'data/runs.json'
    require(registry_path.is_file(), 'Project has no data/runs.json')
    registry = load_json(registry_path)
    require(registry.get('schema_version') == 2, 'Unsupported project registry schema')
    run_record = data['run']
    run_id = run_record['run_id']
    require(run_id not in {run['id'] for run in registry['runs']}, 'Run ID already exists')
    cohort_record = run_record['cohort']
    cohort = next((item for item in registry['cohorts'] if item['id'] == cohort_record['id']), None)
    if cohort:
        require(cohort['prompt_sha256'] == data['prompt_sha256'], 'Existing cohort prompt differs')
        require(cohort['baseline'] != run_id, 'Existing cohort baseline cannot be replaced')
        require(not cohort_record['baseline'], 'Only a new cohort may declare its first baseline')
    else:
        require(cohort_record['baseline'], 'A new cohort must declare a baseline')
        cohort = {
            'id': cohort_record['id'],
            'title': cohort_record['title'],
            'prompt_sha256': data['prompt_sha256'],
            'baseline': run_id,
            'default_run': run_id,
        }
    rubric = load_json(project / 'data/rubric.json')
    rubric_hash = sha(canonical(rubric))
    files = [projection(relative, actual.read_bytes()) for relative, actual in sorted(data['paths'].items())]
    corpus = dict(
        schema_version=1,
        run_id=run_id,
        archive_sha256=None,
        recorded_snapshot_sha256=data['bundle_sha256'],
        prompt_sha256=data['prompt_sha256'],
        files=files,
    )
    corpus_bytes = (json.dumps(corpus, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    corpus_path = project / 'corpus/runs' / (run_id + '.json')
    prompt_path = project / 'corpus/prompts' / (data['prompt_sha256'] + '.txt')
    date = data['run']['outcome']['started_at'][:10]
    requested = data['run']['condition']['requested']
    reported = data['run']['condition']['reported']
    harness = data['run']['condition']['harness']
    registry_run = {
        'id': run_id,
        'cohort': cohort['id'],
        'label': data['run']['presentation']['label'],
        'title': data['run']['presentation']['title'],
        'model': reported['model'] or requested['model'],
        'model_identity_basis': 'reported' if reported['model'] else 'requested; upstream identity unverified',
        'effort': harness['reasoning_effort'],
        'date': date,
        'cli_version': str(harness['version']),
        'elapsed_seconds': data['elapsed_seconds'],
        'usage': data['run']['usage'],
        'prompt_sha256': data['prompt_sha256'],
        'corpus_sha256': sha(corpus_bytes),
        'corpus': 'corpus/runs/' + run_id + '.json',
        'protocol': harness['name'],
        'condition': data['run']['outcome']['status'],
        'evidence_scope': 'Frozen bundle source and separate observations; no execution during import',
    }
    observations_path = project / 'data/observations.json'
    observations = load_json(observations_path)
    evidence_path = project / 'data/evidence-sources.json'
    evidence_registry = load_json(evidence_path)
    require(evidence_registry.get('schema_version') == 1, 'Unsupported evidence-source registry')
    observation_text = data['observations']['summary']
    observations[run_id] = {
        'kind': 'imported bundle observations',
        'date': date,
        'summary': observation_text,
        'checks': len(data['observations']['checks']) if data['observations']['checks'] else None,
        'unit': 'checks' if data['observations']['checks'] else None,
        'browser': 'see bundle observations',
        'live_cooperation': 'unassessed',
        'evidence': {
            'source': 'observations.json',
            'source_sha256': data['observations_sha256'],
            'locator': 'bundle observations',
            'projection_note': 'Separate bundle observations, imported without execution. Parent bundle record retained by the producer.',
            'text': observation_text,
            'browser_observations': None,
            'parent_retained': True,
            'verification_status': 'bundle-record-retained',
        },
    }
    retained_bundle = project / 'data/bundles' / run_id
    retained_run = retained_bundle / 'run.json'
    retained_manifest = retained_bundle / 'manifest.json'
    retained_observations = retained_bundle / 'observations.json'
    require(not retained_bundle.exists(), 'Bundle retention directory already exists')
    evidence_registry['sources'][data['observations_sha256']] = {
        'label': 'Imported bundle observations · ' + run_id,
        'retained_in_repo': True,
        'path': retained_observations.relative_to(project).as_posix(),
        'scope': data['observations']['attribution'],
        'note': 'The complete validated observations record is retained separately from candidate source and assessment evidence.',
    }
    artefacts_path = project / 'data/artefacts.json'
    artefacts = load_json(artefacts_path)
    documents = [
        item['path'] for item in corpus['files'] if item['text'] is not None and item['path'].lower().endswith('.md')
    ][:4]
    artefacts[run_id] = {
        'kind': 'none',
        'description': 'No executable preview is configured for this imported bundle.',
        'documents': documents,
        'preview': None,
        'preview_note': 'Source records only. Import and build never execute retained candidates.',
        'images': [],
    }
    review = unreviewed_record(
        dict(run=data['run'], corpus_sha256=registry_run['corpus_sha256']), rubric, rubric_hash, date
    )
    review_path = project / 'data/reviews' / (review['id'] + '.json')
    selection_path = project / 'data/review-selection.json'
    selection = load_json(selection_path)
    require(selection.get('schema_version') == 1, 'Unsupported review-selection schema')

    modified = [registry_path, observations_path, artefacts_path, selection_path, evidence_path]
    snapshots = {path: path.read_bytes() for path in modified}
    created = []
    try:
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        if not prompt_path.exists():
            fsutil.write_bytes_atomic(prompt_path, data['prompt'])
            created.append(prompt_path)
        else:
            require(prompt_path.read_bytes() == data['prompt'], 'Prompt hash collision')
        corpus_path.parent.mkdir(parents=True, exist_ok=True)
        if _write_new_bytes(corpus_path, corpus_bytes, 'Corpus record'):
            created.append(corpus_path)
        review_bytes = (json.dumps(review, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        if _write_new_bytes(review_path, review_bytes, 'Review record'):
            created.append(review_path)
        retained_bundle.mkdir(parents=True, exist_ok=True)
        fsutil.write_bytes_atomic(retained_run, (Path(bundle) / 'run.json').read_bytes())
        fsutil.write_bytes_atomic(retained_manifest, (Path(bundle) / 'manifest.json').read_bytes())
        fsutil.write_bytes_atomic(retained_observations, (Path(bundle) / 'observations.json').read_bytes())
        created.extend([retained_run, retained_manifest, retained_observations])
        if cohort_record['baseline']:
            registry['cohorts'].append(cohort)
        registry['runs'].append(registry_run)
        selection['selections'][run_id] = review['id']
        atomic_json(registry_path, registry)
        atomic_json(observations_path, observations)
        atomic_json(artefacts_path, artefacts)
        atomic_json(selection_path, selection)
        atomic_json(evidence_path, evidence_registry)
        validate_project(project)
    except Exception as original:
        for path in created:
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass
        if retained_bundle.exists():
            try:
                if not any(retained_bundle.iterdir()):
                    retained_bundle.rmdir()
            except OSError:
                pass
        restore_errors = []
        for path, payload in snapshots.items():
            try:
                fsutil.write_bytes_atomic(path, payload)
            except Exception as restore_error:
                restore_errors.append(str(path) + ': ' + str(restore_error))
        if restore_errors:
            try:
                original.add_note('Snapshot restore failed: ' + '; '.join(restore_errors))
            except Exception:
                pass
        raise
    return dict(
        run_id=run_id,
        review_id=review['id'],
        bundle_sha256=data['bundle_sha256'],
        corpus_sha256=registry_run['corpus_sha256'],
        corpus=corpus,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['validate', 'import'])
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--project', type=Path, default=ROOT)
    args = parser.parse_args()
    if args.command == 'validate':
        data = validate_bundle(args.bundle)
        print(
            'Valid bundle %s: %d files, SHA-256 %s.'
            % (data['run']['run_id'], len(data['files']), data['bundle_sha256'])
        )
    else:
        data = import_bundle(args.bundle, args.project)
        print('Imported %s as unreviewed evidence. Corpus SHA-256 %s.' % (data['run_id'], data['corpus_sha256']))
        print('Review the candidate before replacing ' + data['review_id'] + ' with an attributed reading.')


if __name__ == '__main__':
    try:
        main()
    except (BundleError, OSError, KeyError, TypeError) as error:
        print('Error: ' + str(error), file=sys.stderr)
        sys.exit(1)
