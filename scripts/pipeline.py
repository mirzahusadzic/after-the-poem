#!/usr/bin/env python3
"""Prepare and run local Codex generation/review campaigns, then admit their records."""

import argparse
import base64
import contextlib
import json
import os
import secrets
import shutil
import sys
from pathlib import Path

import atlas
import bundles
import codex_worker as worker
import review_pipeline as reviews
from bundles import canonical, load_json, require, sha


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = 'dream-common-harness-v1'
CODE_FILES = [
    'evaluate.py',
    'pipeline.py',
    'codex_worker.py',
    'review_pipeline.py',
    'sealed_run.py',
    'bundles.py',
    'atlas.py',
]


def code_identity():
    return sha(canonical({name: sha((ROOT / 'scripts' / name).read_bytes()) for name in CODE_FILES}))


def validate_config(config):
    required = {
        'id',
        'title',
        'prompt',
        'samples',
        'generation_seconds',
        'review_seconds',
        'generators',
        'reviewers',
        'display_reviewer',
    }
    require(
        isinstance(config, dict) and required <= set(config) and set(config) <= required | {'existing_runs'},
        'Invalid campaign fields',
    )
    require(atlas.safe_id(config['id']) and len(config['id']) <= 30, 'Campaign ID must be at most 30 safe characters')
    require(isinstance(config['title'], str) and config['title'].strip(), 'Campaign title required')
    require(isinstance(config['prompt'], str) and config['prompt'], 'Prompt path required')
    require(type(config['samples']) is int and 1 <= config['samples'] <= 20, 'Samples must be 1–20')
    for key in ['generation_seconds', 'review_seconds']:
        require(type(config[key]) is int and 1 <= config[key] <= 7200, key + ' must be 1–7200')
    for kind in ['generators', 'reviewers']:
        require(isinstance(config[kind], list) and len(config[kind]) <= 12, 'Invalid ' + kind)
        ids = set()
        for job in config[kind]:
            require(isinstance(job, dict) and set(job) == {'id', 'model', 'reasoning'}, 'Invalid model entry')
            require(
                atlas.safe_id(job['id']) and len(job['id']) <= 16 and job['id'] not in ids,
                'Invalid or duplicate model ID',
            )
            ids.add(job['id'])
            require(
                isinstance(job['model'], str) and job['model'].strip() and not any(c.isspace() for c in job['model']),
                'Invalid model',
            )
            require(job['reasoning'] in ['none', 'low', 'medium', 'high', 'xhigh', 'max'], 'Invalid reasoning effort')
    require(isinstance(config['reviewers'], list), 'Reviewers must be a list')
    existing = config.get('existing_runs', [])
    require(
        isinstance(existing, list)
        and all(atlas.safe_id(item) for item in existing)
        and len(set(existing)) == len(existing),
        'Invalid existing runs',
    )
    require(config['generators'] or config['reviewers'] or existing, 'Choose generation, existing runs or reviewers')
    require(
        config['display_reviewer'] is None
        or config['display_reviewer'] in {item['id'] for item in config['reviewers']},
        'Display reviewer must be declared explicitly or null',
    )
    require(config['generators'] or existing, 'Choose generators or existing runs to re-review')
    bundles.inspect_metadata({key: value for key, value in config.items() if key != 'prompt'}, 'campaign')
    return config


def copy_report(project, destination):
    destination.mkdir()
    for name in ['data', 'corpus', 'src', 'assets']:
        shutil.copytree(project / name, destination / name)
    for name in ['LICENSE', 'LICENSE-CONTENT', 'LICENSING.md', 'THIRD_PARTY_NOTICES.md']:
        shutil.copyfile(project / name, destination / name)


def prepare(
    config_file,
    output,
    project=ROOT,
    *,
    dry_run=False,
    router_url='http://127.0.0.1:18766/v1',
    catalog=None,
    router_profile=None,
    capabilities=None,
):
    config = validate_config(load_json(Path(config_file)))
    project, output = Path(project).resolve(), Path(output).resolve()
    data = atlas.validate(project)
    known = {run['id'] for run in data['runs']}
    require(set(config.get('existing_runs', [])) <= known, 'An existing run is unknown')
    if config['generators']:
        require(
            config['id'] not in {cohort['id'] for cohort in data['cohorts']},
            'Generation needs a new campaign/cohort ID',
        )
    prompt_path = Path(config['prompt'])
    if not prompt_path.is_absolute():
        prompt_path = project / prompt_path
    prompt = prompt_path.read_bytes()
    require(0 < len(prompt) <= 1024 * 1024, 'Prompt must be 1 byte–1 MiB')
    prompt.decode('utf-8')
    require(not output.exists(), 'Campaign output already exists; use run to resume it')
    output.mkdir(mode=0o700, parents=True)
    (output / 'prompt.txt').write_bytes(prompt)
    (output / 'jobs').mkdir()
    copy_report(project, output / 'report')
    jobs = config['generators'] + config['reviewers']
    runtime = worker.freeze_runtime(
        output, jobs, dry_run, router_url, catalog, router_profile, capability_path=capabilities
    )
    saved = {key: value for key, value in config.items() if key != 'prompt'}
    generation_jobs = len(config['generators']) * config['samples']
    review_jobs = (generation_jobs + len(config.get('existing_runs', []))) * len(config['reviewers'])
    plan = {
        'schema_version': 1,
        'protocol': PROTOCOL,
        'gateway': worker.GATEWAY,
        'config': saved,
        'prompt_sha256': sha(prompt),
        'rubric_sha256': data['rubric_sha256'],
        'runtime': runtime,
        'code_sha256': code_identity(),
        'router_url': router_url,
        'queue': {
            'generation_jobs': generation_jobs,
            'review_jobs': review_jobs,
            'worker_wall_time_seconds': generation_jobs * config['generation_seconds']
            + review_jobs * config['review_seconds'],
        },
    }
    worker.write_json(output / 'campaign.json', plan)
    (output / 'campaign.sha256').write_text(sha(canonical(plan)) + '\n', encoding='utf-8')
    return plan


def load_plan(root):
    plan = load_json(root / 'campaign.json')
    require((root / 'campaign.sha256').read_text().strip() == sha(canonical(plan)), 'Frozen campaign changed')
    return plan


def write_bundle(output, plan, job, run_id, receipt, baseline):
    snapshot = load_json(output / 'candidate-snapshot.json')
    content = {key: value for key, value in snapshot.items() if key != 'sha256'}
    require(sha(canonical(content)) == receipt.get('candidate_sha256') == snapshot['sha256'], 'Snapshot changed')
    bundle = output / ('bundle-staging-' + secrets.token_hex(8))
    bundle.mkdir()
    candidate = bundle / 'candidate'
    candidate.mkdir()
    manifest = []
    for source in snapshot['files']:
        relative = bundles.safe_path(source['path'])
        payload = base64.b64decode(source['content_base64'], validate=True)
        require(len(payload) == source['bytes'] and sha(payload) == source['sha256'], 'Snapshot file changed')
        target = candidate / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        target.chmod(source['mode'] & 0o777)
        manifest.append({key: source[key] for key in ['path', 'bytes', 'sha256']})
    prompt = (output / 'prompt.txt').read_bytes()
    require(sha(prompt) == plan['prompt_sha256'], 'Generation prompt changed')
    (bundle / 'prompt.txt').write_bytes(prompt)
    worker.write_json(bundle / 'manifest.json', {'schema_version': 1, 'files': manifest})
    status = receipt['status']
    if status == 'completed' and not manifest:
        status = 'partial'
    runtime = plan['runtime']
    record = {
        'schema_version': 1,
        'run_id': run_id,
        'seed_id': 'seed-' + plan['prompt_sha256'][:24],
        'prompt_sha256': plan['prompt_sha256'],
        'cohort': {'id': plan['config']['id'], 'title': plan['config']['title'], 'baseline': baseline},
        'presentation': {'label': ('Synthetic · ' if runtime['dry_run'] else '') + job['model'], 'title': run_id},
        'outcome': {
            'status': status,
            'started_at': receipt['started_at'],
            'ended_at': receipt['ended_at'],
            'stop_reason': receipt.get('stopped_reason') or receipt.get('error') or status,
            'errors': [receipt['error']] if receipt.get('error') else [],
            'partial_output': status != 'completed',
            'truncation': {'truncated': bool(receipt.get('stopped_reason')), 'reason': receipt.get('stopped_reason')},
        },
        'condition': {
            'requested': {
                'model': job['model'],
                'provider': worker.GATEWAY,
                'router_revision': runtime['router']['revision'] if runtime['router'] else None,
                'routing_policy': worker.POLICY,
            },
            'reported': receipt['reported'],
            'harness': {
                'name': PROTOCOL,
                'version': runtime['codex_version'],
                'reasoning_effort': job['reasoning'],
                'tools': ['local shell and file editing; no network, plugins, MCP or delegated agents'],
                'runtime_checks': receipt['runtime_checks'],
                'runtime': dict(
                    runtime, protocol_code_sha256=plan['code_sha256'], campaign_sha256=sha(canonical(plan))
                ),
            },
            'isolation': {
                'mode': 'synthetic' if runtime['dry_run'] else 'codex-named-profile',
                'network': 'not exercised' if runtime['dry_run'] else 'denied for tools',
                'audit': receipt['isolation'],
            },
            'budgets': {'wall_time_seconds': job['seconds'], 'input_tokens': None, 'output_tokens': None, 'cost': None},
        },
        'usage': receipt['usage'],
        'cost': {
            'amount': None,
            'currency': None,
            'provenance': 'No inference' if runtime['dry_run'] else 'Provider billing not measured',
        },
    }
    worker.write_json(bundle / 'run.json', record)
    worker.write_json(
        bundle / 'observations.json',
        {
            'schema_version': 1,
            'attribution': 'After the Poem host capture',
            'summary': 'Host captured the workspace without executing the retained candidate. No semantic assessment or preview observation.',
            'checks': [
                {'name': 'workspace capture', 'outcome': 'hashed'},
                {'name': 'sandbox canaries', 'outcome': receipt['isolation']['status']},
            ],
        },
    )
    bundles.validate_bundle(bundle)
    bundle.rename(output / 'bundle')
    return output / 'bundle'


@contextlib.contextmanager
def campaign_lock(root):
    require(os.name == 'posix', 'Campaign execution currently requires macOS or Linux')
    import fcntl

    with (root / '.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('Campaign is already running') from error
        yield


def ensure_import(bundle, project):
    validated = bundles.validate_bundle(bundle)
    run_id = validated['run']['run_id']
    data = atlas.validate(project)
    if run_id in data['corpora']:
        require(
            data['corpora'][run_id]['recorded_snapshot_sha256'] == validated['bundle_sha256'],
            'An existing run has a different bundle identity',
        )
    else:
        bundles.import_bundle(bundle, project)


def execute(root, *, accept_run=False):
    root = Path(root).resolve()
    with campaign_lock(root):
        plan = load_plan(root)
        dry_run = plan['runtime']['dry_run']
        require(dry_run or accept_run, 'Live generation and review require --accept-run')
        require(plan['code_sha256'] == code_identity(), 'Pipeline code changed; prepare a new campaign')
        require(sha((root / 'prompt.txt').read_bytes()) == plan['prompt_sha256'], 'Frozen prompt changed')
        project = root / 'report'
        require(atlas.validate(project)['rubric_sha256'] == plan['rubric_sha256'], 'Frozen rubric changed')
        config, results = plan['config'], []
        runs = list(config.get('existing_runs', []))
        index = 0
        for sample in range(1, config['samples'] + 1):
            for generator in config['generators']:
                index += 1
                run_id = config['id'] + '-' + generator['id'] + '-s' + str(sample)
                output = root / 'jobs' / ('generation-' + str(index).zfill(4))
                job = dict(generator, seconds=config['generation_seconds'])
                if output.exists():
                    require(
                        (output / 'receipt.json').is_file(),
                        'Interrupted job without receipt; retain it and prepare a new campaign',
                    )
                    receipt = load_json(output / 'receipt.json')
                else:
                    print('Generate ' + run_id, flush=True)
                    receipt = worker.run_job(
                        output,
                        job,
                        (root / 'prompt.txt').read_bytes(),
                        plan['runtime'],
                        root / 'catalog.json',
                        plan['router_url'],
                        dry_run=dry_run,
                    )
                if 'candidate_sha256' in receipt:
                    bundle = output / 'bundle'
                    if not bundle.exists():
                        first = not any(cohort['id'] == config['id'] for cohort in atlas.validate(project)['cohorts'])
                        write_bundle(output, plan, job, run_id, receipt, first)
                    ensure_import(bundle, project)
                    runs.append(run_id)
                results.append({'job': output.name, 'run_id': run_id, 'status': receipt['status']})
                worker.write_json(root / 'state.json', {'jobs': results, 'complete': False})
                if receipt['status'] == 'cancelled':
                    atlas.build(project)
                    return {'jobs': results, 'complete': False, 'all_succeeded': False, 'cancelled': True}
        for run_index, run_id in enumerate(runs, 1):
            for judge_index, judge in enumerate(config['reviewers'], 1):
                output = root / 'jobs' / ('review-' + str(run_index).zfill(4) + '-' + str(judge_index).zfill(2))
                review_id = config['id'] + '-r' + str(run_index) + '-' + judge['id']
                prompt, identity = reviews.prepare_packet(project, run_id)
                job = dict(judge, seconds=config['review_seconds'])
                if output.exists():
                    require(
                        (output / 'receipt.json').is_file(),
                        'Interrupted review without receipt; retain it and prepare a new campaign',
                    )
                    receipt = load_json(output / 'receipt.json')
                else:
                    print('Review ' + run_id + ' with ' + judge['id'], flush=True)
                    fixture = reviews.synthetic_result(atlas.validate(project)['rubric']) if dry_run else None
                    receipt = worker.run_job(
                        output,
                        job,
                        prompt,
                        plan['runtime'],
                        root / 'catalog.json',
                        plan['router_url'],
                        review=True,
                        dry_run=dry_run,
                        fake_result=fixture,
                    )
                result = {'job': output.name, 'run_id': run_id, 'review_id': review_id, 'status': receipt['status']}
                if receipt['status'] == 'completed':
                    try:
                        require(receipt['prompt_sha256'] == sha(prompt), 'Review packet changed on resume')
                        final = output / 'control/final-message.txt'
                        require(sha(final.read_bytes()) == receipt['final_message_sha256'], 'Reviewer answer changed')
                        reviewer = (
                            'Synthetic transport check'
                            if dry_run
                            else judge['model'] + ' via ' + worker.GATEWAY + ' · independent static reading'
                        )
                        review = reviews.validate_answer(
                            project, final, identity, review_id, reviewer, receipt, dry_run=dry_run
                        )
                        existing = project / 'data/reviews' / (review_id + '.json')
                        if existing.exists():
                            require(load_json(existing) == review, 'Admitted review changed')
                        else:
                            atlas.add_review(project, review, select=judge['id'] == config['display_reviewer'])
                        worker.write_json(output / 'review.json', review)
                        result['status'] = 'admitted'
                    except (ValueError, OSError, TypeError, KeyError) as error:
                        result.update(status='rejected', error=type(error).__name__)
                        (output / 'control/rejection.txt').write_text(str(error), encoding='utf-8')
                results.append(result)
                worker.write_json(root / 'state.json', {'jobs': results, 'complete': False})
                if receipt['status'] == 'cancelled':
                    atlas.build(project)
                    return {'jobs': results, 'complete': False, 'all_succeeded': False, 'cancelled': True}
        atlas.build(project)
        state = {
            'jobs': results,
            'complete': True,
            'all_succeeded': all(item['status'] in ['completed', 'admitted'] for item in results),
        }
        worker.write_json(root / 'state.json', state)
        return state


def admit(root, project=ROOT):
    root, project = Path(root).resolve(), Path(project).resolve()
    plan = load_plan(root)
    require(not plan['runtime']['dry_run'], 'Synthetic campaigns cannot be admitted into the study')
    with campaign_lock(root):
        for bundle in sorted((root / 'jobs').glob('generation-*/bundle')):
            ensure_import(bundle, project)
        for path in sorted((root / 'jobs').glob('review-*/review.json')):
            review = load_json(path)
            receipt = load_json(path.parent / 'receipt.json')
            prompt, identity = reviews.prepare_packet(project, review['run_id'])
            require(
                receipt['status'] == 'completed' and receipt['prompt_sha256'] == sha(prompt), 'Review capture differs'
            )
            answer = path.parent / 'control/final-message.txt'
            require(
                sha(answer.read_bytes()) == receipt['final_message_sha256'], 'Review answer changed before admission'
            )
            validated_review = reviews.validate_answer(
                project, answer, identity, review['id'], review['reviewer'], receipt
            )
            require(validated_review == review, 'Exported review differs from the validated answer')
            existing = project / 'data/reviews' / (review['id'] + '.json')
            if existing.exists():
                require(load_json(existing) == review, 'Existing review differs')
            else:
                atlas.add_review(project, review)
        selections = atlas.validate(root / 'report')['selections']
        data = atlas.validate(project)
        for run_id, active in data['active_reviews'].items():
            if active['status'] == 'unreviewed' and run_id in selections:
                atlas.select_review(project, selections[run_id])
        atlas.build(project)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prepare_parser = commands.add_parser('prepare', help='Freeze inputs and queue; never calls a model')
    prepare_parser.add_argument('config', type=Path)
    prepare_parser.add_argument('--output', type=Path, required=True)
    prepare_parser.add_argument('--project', type=Path, default=ROOT)
    prepare_parser.add_argument('--catalog', type=Path)
    prepare_parser.add_argument('--router-url', default='http://127.0.0.1:18766/v1')
    prepare_parser.add_argument('--dry-run', action='store_true')
    run_parser = commands.add_parser('run', help='Execute or resume the queue without repeating finished jobs')
    run_parser.add_argument('campaign', type=Path)
    run_parser.add_argument('--accept-run', action='store_true')
    admit_parser = commands.add_parser('admit', help='Import retained bundles and reviews into the study')
    admit_parser.add_argument('campaign', type=Path)
    admit_parser.add_argument('--project', type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            plan = prepare(
                args.config,
                args.output,
                args.project,
                dry_run=args.dry_run,
                router_url=args.router_url,
                catalog=args.catalog,
            )
            print(
                'Prepared '
                + plan['config']['id']
                + (' (synthetic; no inference)' if args.dry_run else ' (live; not started)')
            )
            print(json.dumps(plan['queue']))
        elif args.command == 'run':
            result = execute(args.campaign, accept_run=args.accept_run)
            print(json.dumps(result, indent=2))
            if not result.get('all_succeeded'):
                return 1
        else:
            admit(args.campaign, args.project)
    except (ValueError, OSError, KeyError) as error:
        print('Pipeline stopped: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
