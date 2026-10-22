#!/usr/bin/env python3
"""Run evidence-session scoring, admission and the UI build as one pipeline."""

import json
import os
import shutil
import tempfile
from pathlib import Path

import atlas
import evaluation_contract as contracts
import codex_worker as worker
import rubric_policy
import scoring_pipeline
import session_flow
from bundles import canonical, load_json, require, sha


ROOT = Path(__file__).resolve().parents[1]

PROFILE_FIELDS = {
    'schema_version',
    'run_id',
    'model',
    'reasoning',
    'repetitions',
    'minimum_agreement',
    'evaluation_id',
    'evaluation_run_id',
    'review_id',
    'reviewer',
    'run_label',
    'run_title',
    'output',
    'policy',
    'repairs',
    'budget',
    'layout',
}
OPTIONAL_PROFILE_FIELDS = {
    'schema_version',
    'policy',
    'evaluation_run_id',
    'run_label',
    'run_title',
    'repetitions',
    'minimum_agreement',
    'repairs',
    'budget',
    'layout',
}
METHOD = 'session-rubric-dag-v3'


def load_profile(project, path):
    project = Path(project).resolve()
    profile = contracts.load_json(Path(path).resolve())
    require(
        isinstance(profile, dict) and set(profile) <= PROFILE_FIELDS,
        'Evaluation profile has unknown fields: ' + ', '.join(sorted(set(profile) - PROFILE_FIELDS)),
    )
    require(profile.get('schema_version') == 2, 'Unsupported evaluation profile schema; expected 2')
    missing = sorted(PROFILE_FIELDS - OPTIONAL_PROFILE_FIELDS - set(profile))
    require(not missing, 'Evaluation profile is missing: ' + ', '.join(missing))
    for field in ['run_id', 'evaluation_id', 'review_id'] + (
        ['evaluation_run_id'] if 'evaluation_run_id' in profile else []
    ):
        require(contracts.SAFE_ID.fullmatch(profile[field] or ''), 'Invalid evaluation profile ' + field)
    for field in ['run_label', 'run_title', 'model', 'reviewer', 'output']:
        if field in profile:
            require(
                isinstance(profile[field], str) and profile[field].strip(),
                'Evaluation profile ' + field + ' must be non-empty',
            )
    require(profile['reasoning'] in contracts.REASONING, 'Invalid evaluation profile reasoning')
    result = dict(profile)
    result.setdefault('repetitions', 1)
    result.setdefault('minimum_agreement', 0.67)
    result.setdefault('repairs', 2)
    result.setdefault('layout', 'compact')
    result['policy'] = str((project / result['policy']).resolve()) if 'policy' in result else None
    result['evaluation_run_id'] = result.get('evaluation_run_id') or result['evaluation_id']
    result['run_label'] = result.get('run_label')
    result['run_title'] = result.get('run_title')
    result['budget'] = result.get('budget')
    result['output'] = str((project / result['output']).resolve())
    return result


def preflight_profile(
    project, path, *, router_url='http://127.0.0.1:18766/v1', catalog=None, router_profile=None, capabilities=None
):
    profile = load_profile(project, path)
    session_flow.validate_budget(profile['budget'], required=True)
    job = {'model': profile['model'], 'reasoning': profile['reasoning'], 'seconds': 900}
    with tempfile.TemporaryDirectory(prefix='after-poem-profile-doctor-') as temporary:
        runtime = worker.freeze_runtime(
            Path(temporary),
            [job],
            False,
            router_url,
            catalog,
            router_profile,
            extra_instructions={'session': session_flow.SESSION_INSTRUCTIONS},
            capability_path=capabilities,
        )
    return {
        'status': 'valid',
        'profile': str(Path(path).resolve()),
        'run_id': profile['run_id'],
        'model': profile['model'],
        'reasoning': profile['reasoning'],
        'budget': profile['budget'],
        'gateway': worker.GATEWAY,
        'codex_version': runtime['codex_version'],
        'router': runtime['router'],
        'model_capabilities': runtime['model_capabilities'],
        'capability_registry_sha256': runtime['capability_registry_sha256'],
    }


def dry_run_output(output):
    """Synthetic runs live beside, never inside, the live output they rehearse."""
    return str(Path(output).with_name(Path(output).name + '.dry-run'))


def run_profile(project, path, **options):
    profile = load_profile(project, path)
    return run(
        project,
        profile['run_id'],
        profile['policy'],
        dry_run_output(profile['output']) if options.get('dry_run') else profile['output'],
        model=profile['model'],
        reasoning=profile['reasoning'],
        evaluation_id=profile['evaluation_id'],
        evaluation_run_id=profile['evaluation_run_id'],
        review_id=profile['review_id'],
        run_label=profile['run_label'],
        run_title=profile['run_title'],
        reviewer=profile['reviewer'],
        repetitions=profile['repetitions'],
        minimum_agreement=profile['minimum_agreement'],
        repairs=profile['repairs'],
        layout=profile['layout'],
        budget=options.pop('budget', None) or profile['budget'],
        **options,
    )


def run(
    project,
    run_id,
    policy,
    output,
    *,
    model,
    reasoning='high',
    evaluation_id=None,
    evaluation_run_id=None,
    review_id=None,
    reviewer=None,
    run_label=None,
    run_title=None,
    repetitions=1,
    minimum_agreement=0.67,
    repairs=2,
    budget=None,
    layout='compact',
    dry_run=False,
    accept_run=False,
    router_url='http://127.0.0.1:18766/v1',
    catalog=None,
    router_profile=None,
    capabilities=None,
):
    """Read, extract and score a frozen run in evidence sessions, then admit it as its own run package."""
    project = Path(project).resolve()
    output = Path(output).resolve()
    policy = Path(policy).resolve() if policy else None
    require(contracts.SAFE_ID.fullmatch(run_id or ''), 'Invalid run ID')
    require(contracts.SAFE_ID.fullmatch(evaluation_id or ''), 'A stable evaluation ID is required')
    evaluation_run_id = evaluation_run_id or evaluation_id
    require(contracts.SAFE_ID.fullmatch(evaluation_run_id or ''), 'A stable evaluation run ID is required')
    require(contracts.SAFE_ID.fullmatch(review_id or ''), 'A stable review ID is required')
    require(
        not output.exists() or (output.is_dir() and not output.is_symlink()),
        'Evaluation output must be a real directory',
    )
    require(dry_run or accept_run, 'Live evaluation requires --accept-run')
    output.mkdir(parents=True, mode=0o700, exist_ok=True)
    if policy is None:
        policy = output / 'rubric-policy.json'
        generated = rubric_policy.build(
            load_json(project / 'data/rubric.json'), repetitions=repetitions, minimum_agreement=minimum_agreement
        )
        if policy.exists():
            require(
                policy.is_file() and not policy.is_symlink() and load_json(policy) == generated,
                'Existing generated rubric policy differs from the frozen rubric',
            )
        else:
            temporary = policy.with_suffix('.json.tmp')
            temporary.write_bytes(canonical(generated) + b'\n')
            os.replace(temporary, policy)
    evaluation_dir = output / 'evaluation'
    if (evaluation_dir / 'evaluation.json').is_file():
        evaluation = scoring_pipeline.validate_output(project, evaluation_dir)
        require(
            evaluation['dry_run'] == dry_run,
            'This output already holds a '
            + ('synthetic' if evaluation['dry_run'] else 'live')
            + ' evaluation; use a separate output for the other mode',
        )
        evaluation_reused = True
    else:
        evaluation = scoring_pipeline.run(
            project,
            policy,
            evaluation_dir,
            run_id=run_id,
            model=model,
            reasoning=reasoning,
            evaluation_id=evaluation_id,
            dry_run=dry_run,
            accept_run=accept_run,
            router_url=router_url,
            catalog=catalog,
            router_profile=router_profile,
            capabilities=capabilities,
            budget=budget,
            repairs=repairs,
            layout=layout,
        )
        evaluation_reused = False
    result = {
        'status': 'completed',
        'run_id': run_id,
        'source_run_id': run_id,
        'evaluation_run_id': evaluation_run_id,
        'model': model,
        'reasoning': reasoning,
        'dry_run': bool(evaluation.get('dry_run', dry_run)),
        'output': str(output),
        'evaluation': evaluation,
        'evaluation_reused': evaluation_reused,
    }
    if result['dry_run']:
        result.update(
            integration_status='skipped-synthetic',
            explanation='Synthetic evaluations remain local and are never inserted into the observatory.',
        )
        return result
    integrated = integrate_evaluation(
        project,
        evaluation_dir,
        review_id=review_id,
        reviewer=reviewer,
        evaluation_run_id=evaluation_run_id,
        run_label=run_label,
        run_title=run_title,
    )
    result.update(
        integration_status=integrated['status'],
        integration=integrated,
        ui_url=integrated['ui_url'],
        next_command=integrated['next_command'],
    )
    return result


def _evaluation_receipts(evaluation_directory):
    receipts = session_flow.ledger(evaluation_directory)
    require(receipts, 'Evaluation has no session receipts')
    return receipts


def evaluation_run_from_evaluation(project, evaluation_directory, *, evaluation_run_id, run_label=None, run_title=None):
    project, evaluation_directory = Path(project).resolve(), Path(evaluation_directory).resolve()
    require(contracts.SAFE_ID.fullmatch(evaluation_run_id or ''), 'Invalid evaluation run ID')
    evaluation = contracts.load_json(evaluation_directory / 'evaluation.json')
    source_run = next((item for item in atlas.validate(project)['runs'] if item['id'] == evaluation['run_id']), None)
    require(source_run is not None, 'Evaluation source run is missing')
    require(
        not evaluation_run_id.startswith(source_run['id'] + '-unreviewed'),
        'Evaluation run ID collides with an unreviewed source record',
    )
    receipts = _evaluation_receipts(evaluation_directory)
    return {
        'schema_version': 1,
        'id': evaluation_run_id,
        'source_run_id': source_run['id'],
        'evaluation_id': evaluation['evaluation_id'],
        'cohort': source_run['cohort'],
        'label': run_label or evaluation['presentation']['label'],
        'title': run_title or (source_run['title'] + ' · rubric reading'),
        'model': evaluation['gateway']['model'],
        'model_identity_basis': 'requested; upstream identity unverified',
        'effort': evaluation['gateway']['reasoning'],
        'date': max(receipt['ended_at'] for receipt in receipts)[:10],
        'cli_version': evaluation['gateway']['codex_version'],
        'elapsed_seconds': round(sum(receipt.get('elapsed_seconds') or 0 for receipt in receipts), 3),
        'usage': dict(evaluation['usage']),
        'prompt_sha256': source_run['prompt_sha256'],
        'corpus_sha256': source_run['corpus_sha256'],
        'protocol': METHOD,
        'condition': 'evaluation',
        'evidence_scope': 'New evaluation run reusing frozen artefact bytes from ' + source_run['id'],
    }


def review_from_evaluation(project, evaluation_directory, *, review_id, evaluation_run_id, reviewer=None, data=None):
    project, evaluation_directory = Path(project).resolve(), Path(evaluation_directory).resolve()
    data = data or atlas.validate(project)
    validated = scoring_pipeline.validate_output(project, evaluation_directory, data=data)
    evaluation = contracts.load_json(evaluation_directory / 'evaluation.json')
    policy = contracts.load_json(evaluation_directory / 'scoring-policy.json')
    rubric = load_json(project / 'data/rubric.json')
    source_texts = {
        source['path']: source['text']
        for source in data['corpora'][evaluation['run_id']]['files']
        if source['text'] is not None
    }
    scores, axis_outcomes = contracts.review_projection(evaluation, policy, rubric, source_texts)
    receipts = _evaluation_receipts(evaluation_directory)
    sessions = validated['sessions']
    return {
        'schema_version': 1,
        'id': review_id,
        'run_id': evaluation_run_id,
        'date': max(receipt['ended_at'] for receipt in receipts)[:10],
        'reviewer': reviewer or (evaluation['gateway']['model'] + ' · rubric session'),
        'status': 'provisional',
        'basis': 'static-artifact',
        'rubric_version': rubric['version'],
        'rubric_sha256': sha(canonical(rubric)),
        'corpus_sha256': evaluation['identities']['corpus_sha256'],
        'supersedes': None,
        'summary': evaluation['presentation']['summary'],
        'live_cooperation': 'unassessed',
        'scores': scores,
        'provenance': {
            'method': METHOD,
            'evaluation_id': evaluation['evaluation_id'],
            'evaluation_run_id': evaluation_run_id,
            'source_run_id': evaluation['run_id'],
            'evaluation_sha256': validated['evaluation_sha256'],
            'extraction_sha256': evaluation['identities']['extraction_sha256'],
            'scoring_policy_sha256': evaluation['identities']['scoring_policy_sha256'],
            'sample_count': validated['judgment_samples'],
            'criteria': validated['criteria'],
            'axis_outcomes': axis_outcomes,
            'router_revision': evaluation['gateway']['router_revision'],
            'raw_samples_retained_in_local_run': True,
            'preview_evidence_used': False,
            'scope': (
                'Provisional semantic reading from ' + str(sessions) + ' evidence session(s); each axis was '
                'answered as its own task and aggregated before selection; unresolved axes remain null.'
            ),
        },
    }


def _evaluation_package_orders(project):
    root = Path(project).resolve() / 'data/evaluations'
    orders = []
    if not root.exists():
        return orders
    for directory in sorted(root.iterdir(), key=lambda item: item.name):
        if directory.name.startswith('.') or not directory.is_dir() or directory.is_symlink():
            continue
        selection_path = directory / 'selection.json'
        if not selection_path.is_file():
            continue
        selection = load_json(selection_path)
        require(
            selection.get('schema_version') == 3 and type(selection.get('order')) is int and selection['order'] >= 1,
            'Invalid existing evaluation package selection: ' + directory.name,
        )
        orders.append(selection['order'])
    require(len(orders) == len(set(orders)), 'Evaluation packages have duplicate order values')
    return orders


def _admit_bundle(project, evaluation_directory, run_record, review, selection):
    project, evaluation_directory = Path(project).resolve(), Path(evaluation_directory).resolve()
    evaluation = load_json(evaluation_directory / 'evaluation.json')
    root = project / 'data/evaluations'
    root.mkdir(parents=True, exist_ok=True)
    target = root / evaluation['evaluation_id']
    files = ['run.json', 'evaluation.json', 'extraction.json', 'scoring-policy.json', 'status.json'] + (
        ['carriage.json'] if (evaluation_directory / 'carriage.json').is_file() else []
    )
    if target.exists():
        require(target.is_dir() and not target.is_symlink(), 'Evaluation bundle target is unsafe')
        for name in files:
            if name == 'run.json':
                require(load_json(target / name) == run_record, 'Existing evaluation run record differs')
            else:
                require(
                    (target / name).is_file()
                    and sha((target / name).read_bytes()) == sha((evaluation_directory / name).read_bytes()),
                    'Existing evaluation bundle differs: ' + name,
                )
        require(load_json(target / 'review.json') == review, 'Existing evaluation package review differs')
        require(load_json(target / 'selection.json') == selection, 'Existing evaluation package selection differs')
        return target, 'already-integrated'
    staging = Path(tempfile.mkdtemp(prefix='.evaluation-', dir=root))
    manifest = {'schema_version': 1, 'evaluation_id': evaluation['evaluation_id'], 'files': {}}
    try:
        for name in files:
            if name == 'run.json':
                (staging / name).write_text(
                    json.dumps(run_record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
                )
            else:
                shutil.copyfile(evaluation_directory / name, staging / name)
            manifest['files'][name] = sha((staging / name).read_bytes())
        (staging / 'review.json').write_text(json.dumps(review, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        (staging / 'selection.json').write_text(
            json.dumps(selection, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
        )
        manifest['files']['review.json'] = sha((staging / 'review.json').read_bytes())
        manifest['files']['selection.json'] = sha((staging / 'selection.json').read_bytes())
        (staging / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        staging.replace(target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return target, 'integrated'


def _integrate_evaluation(
    project, evaluation_directory, *, review_id, evaluation_run_id=None, run_label=None, run_title=None, reviewer=None
):
    project, evaluation_directory = Path(project).resolve(), Path(evaluation_directory).resolve()
    data = atlas.validate(project)
    validation = scoring_pipeline.validate_output(project, evaluation_directory, data=data)
    require(not validation['dry_run'], 'Synthetic evaluations cannot be admitted into the observatory')
    require('control' not in validation, 'Control readings (mismatched seed) are never admitted')
    evaluation = contracts.load_json(evaluation_directory / 'evaluation.json')
    require(evaluation['run_id'] in data['active_reviews'], 'Evaluation names an unknown source run')
    evaluation_run_id = evaluation_run_id or evaluation['evaluation_id']
    require(contracts.SAFE_ID.fullmatch(evaluation_run_id), 'Invalid evaluation run ID')
    run_record = evaluation_run_from_evaluation(
        project, evaluation_directory, evaluation_run_id=evaluation_run_id, run_label=run_label, run_title=run_title
    )
    review = review_from_evaluation(
        project,
        evaluation_directory,
        review_id=review_id,
        evaluation_run_id=evaluation_run_id,
        reviewer=reviewer,
        data=data,
    )
    package = project / 'data/evaluations' / evaluation['evaluation_id']
    if (package / 'selection.json').is_file():
        selection = contracts.load_json(package / 'selection.json')
    else:
        selection = {
            'schema_version': 3,
            'evaluation_id': evaluation['evaluation_id'],
            'source_run_id': evaluation['run_id'],
            'run_id': evaluation_run_id,
            'review_id': review_id,
            'order': max(_evaluation_package_orders(project), default=0) + 1,
        }
    bundle, bundle_status = _admit_bundle(project, evaluation_directory, run_record, review, selection)
    try:
        atlas.build(project)
    except Exception:
        if bundle_status == 'integrated':
            shutil.rmtree(bundle, ignore_errors=True)
        raise
    return {
        'status': bundle_status,
        'review_id': review_id,
        'evaluation_id': evaluation['evaluation_id'],
        'source_run_id': evaluation['run_id'],
        'run_id': evaluation_run_id,
        'order': selection['order'],
        'bundle': str(bundle.relative_to(project)),
        'bundle_status': bundle_status,
        'ui_url': 'index.html#run=' + evaluation_run_id,
        'next_command': 'npm run dev:full',
    }


def integrate_evaluation(project, evaluation_directory, **options):
    """Admit one evaluation as its own removable run package; serialized across local runs."""
    project = Path(project).resolve()
    if os.name != 'posix':
        return _integrate_evaluation(project, evaluation_directory, **options)
    import fcntl

    with (project / '.evaluation-admission.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _integrate_evaluation(project, evaluation_directory, **options)
