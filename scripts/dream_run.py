#!/usr/bin/env python3
"""Generate a new artefact and evaluate it through repeatable profiles."""

import json
import tempfile
from pathlib import Path

import atlas
import artefact_capture
import codex_worker as worker
import evaluation_contract as contracts
import evaluation_run
import pipeline
import session_flow
from bundles import load_json, require


ROOT = Path(__file__).resolve().parents[1]
FIELDS = {
    'schema_version',
    'campaign_id',
    'title',
    'prompt',
    'output',
    'generator_id',
    'generator_model',
    'generator_reasoning',
    'generation_seconds',
    'evaluator_model',
    'evaluator_reasoning',
    'evaluation_budget',
    'evaluation_id',
    'review_id',
    'reviewer',
}
OPTIONAL_FIELDS = {'repetitions', 'minimum_agreement', 'repairs', 'layout', 'run_label', 'run_title'}


def load_profile(project, path, *, runner_model=None, runner_reasoning=None, scorer_model=None, scorer_reasoning=None):
    """Load a dream profile; the runner and scorer may be different router models."""
    project = Path(project).resolve()
    profile = contracts.load_json(Path(path).resolve())
    require(
        isinstance(profile, dict) and FIELDS <= set(profile) <= FIELDS | OPTIONAL_FIELDS,
        'Dream profile fields must be: '
        + ', '.join(sorted(FIELDS))
        + ' (optional: '
        + ', '.join(sorted(OPTIONAL_FIELDS))
        + ')',
    )
    require(profile['schema_version'] == 2, 'Unsupported dream profile schema; expected 2')
    for field in ['campaign_id', 'generator_id', 'evaluation_id', 'review_id']:
        require(contracts.SAFE_ID.fullmatch(profile[field]), 'Invalid dream profile ' + field)
    require(len(profile['campaign_id']) <= 30, 'Dream campaign ID must be at most 30 characters')
    require(len(profile['generator_id']) <= 16, 'Dream generator ID must be at most 16 characters')
    for field in ['title', 'prompt', 'output', 'reviewer'] + [
        field for field in ['run_label', 'run_title'] if field in profile
    ]:
        require(isinstance(profile[field], str) and profile[field].strip(), 'Dream profile needs ' + field)
    require(
        type(profile['generation_seconds']) is int and 1 <= profile['generation_seconds'] <= 7200,
        'Dream generation_seconds must be 1-7200',
    )
    output = Path(profile['output'])
    require(
        not output.is_absolute() and '..' not in output.parts and output.parts,
        'Dream output must be a safe project-relative path',
    )
    result = dict(profile)
    result.setdefault('repetitions', 1)
    result.setdefault('minimum_agreement', 0.67)
    result.setdefault('repairs', 2)
    result.setdefault('layout', 'compact')
    overrides = {
        'generator_model': runner_model,
        'generator_reasoning': runner_reasoning,
        'evaluator_model': scorer_model,
        'evaluator_reasoning': scorer_reasoning,
    }
    result.update({field: value for field, value in overrides.items() if value is not None})
    for field in ['generator_model', 'evaluator_model']:
        require(isinstance(result[field], str) and result[field].strip(), 'Dream profile needs ' + field)
    for field in ['generator_reasoning', 'evaluator_reasoning']:
        require(result[field] in contracts.REASONING, 'Invalid dream profile ' + field)
    session_flow.validate_budget(result['evaluation_budget'], required=True)
    result['output'] = str((project / output).resolve())
    return result


def campaign_config(profile):
    return {
        'id': profile['campaign_id'],
        'title': profile['title'],
        'prompt': profile['prompt'],
        'samples': 1,
        'generation_seconds': profile['generation_seconds'],
        'review_seconds': 600,
        'generators': [
            {
                'id': profile['generator_id'],
                'model': profile['generator_model'],
                'reasoning': profile['generator_reasoning'],
            }
        ],
        'reviewers': [],
        'display_reviewer': None,
    }


def _retryable_generation_failure(campaign_dir, state):
    jobs = state.get('jobs', [])
    if not jobs or any(job.get('status') != 'failed' for job in jobs):
        return False
    for job in jobs:
        receipt_path = campaign_dir / 'jobs' / job['job'] / 'receipt.json'
        if not receipt_path.is_file() or receipt_path.is_symlink():
            return False
        receipt = load_json(receipt_path)
        if (
            receipt.get('status') != 'failed'
            or receipt.get('error') not in {'BundleError', 'worker-process-failed'}
            or receipt.get('runtime_checks', {}).get('status') != 'passed'
            or receipt.get('isolation', {}).get('status') != 'passed'
        ):
            return False
    return True


def _archive_failed_generation(output, campaign_dir, state, number):
    attempts = output / 'generation-attempts'
    attempts.mkdir(mode=0o700, exist_ok=True)
    target = attempts / ('attempt-' + str(number).zfill(3))
    require(not target.exists(), 'Generation attempt destination already exists')
    campaign_dir.replace(target)
    record = {
        'status': 'preserved-failed-generation',
        'attempt': number,
        'campaign_state': state,
        'receipt_errors': {},
    }
    for job in state.get('jobs', []):
        receipt = load_json(target / 'jobs' / job['job'] / 'receipt.json')
        record['receipt_errors'][job['job']] = {
            'status': receipt.get('status'),
            'error': receipt.get('error'),
            'runtime_checks': receipt.get('runtime_checks', {}).get('status'),
            'isolation': receipt.get('isolation', {}).get('status'),
        }
    (target / 'generation-archive.json').write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
    )
    return target


def preflight(
    project,
    path,
    *,
    router_url='http://127.0.0.1:18766/v1',
    catalog=None,
    router_profile=None,
    capabilities=None,
    runner_model=None,
    runner_reasoning=None,
    scorer_model=None,
    scorer_reasoning=None,
):
    profile = load_profile(
        project,
        path,
        runner_model=runner_model,
        runner_reasoning=runner_reasoning,
        scorer_model=scorer_model,
        scorer_reasoning=scorer_reasoning,
    )
    jobs = [
        {'model': profile['generator_model'], 'reasoning': profile['generator_reasoning'], 'seconds': 600},
        {'model': profile['evaluator_model'], 'reasoning': profile['evaluator_reasoning'], 'seconds': 600},
    ]
    with tempfile.TemporaryDirectory(prefix='after-poem-dream-doctor-') as temporary:
        runtime = worker.freeze_runtime(
            Path(temporary),
            jobs,
            False,
            router_url,
            catalog,
            router_profile,
            extra_instructions={'session': session_flow.SESSION_INSTRUCTIONS},
            capability_path=capabilities,
        )
    return {
        'status': 'valid',
        'campaign_id': profile['campaign_id'],
        'runner_model': profile['generator_model'],
        'runner_reasoning': profile['generator_reasoning'],
        'scorer_model': profile['evaluator_model'],
        'scorer_reasoning': profile['evaluator_reasoning'],
        # Retain the profile field names for callers written before the roles
        # were presented as runner and scorer.
        'generator_model': profile['generator_model'],
        'generator_reasoning': profile['generator_reasoning'],
        'evaluator_model': profile['evaluator_model'],
        'evaluator_reasoning': profile['evaluator_reasoning'],
        'gateway': worker.GATEWAY,
        'codex_version': runtime['codex_version'],
        'router': runtime['router'],
        'model_capabilities': runtime['model_capabilities'],
        'capability_registry_sha256': runtime['capability_registry_sha256'],
    }


def run(
    project,
    path,
    *,
    dry_run=False,
    accept_run=False,
    router_url='http://127.0.0.1:18766/v1',
    catalog=None,
    router_profile=None,
    capabilities=None,
    runner_model=None,
    runner_reasoning=None,
    scorer_model=None,
    scorer_reasoning=None,
    generation_attempts=1,
    budget=None,
    repairs=None,
):
    project = Path(project).resolve()
    profile = load_profile(
        project,
        path,
        runner_model=runner_model,
        runner_reasoning=runner_reasoning,
        scorer_model=scorer_model,
        scorer_reasoning=scorer_reasoning,
    )
    require(dry_run or accept_run, 'Live generation requires --accept-run')
    require(type(generation_attempts) is int and 1 <= generation_attempts <= 5, 'generation_attempts must be 1-5')
    output = Path(evaluation_run.dry_run_output(profile['output']) if dry_run else profile['output'])
    output.mkdir(parents=True, mode=0o700, exist_ok=True)
    config = campaign_config(profile)
    config_path = output / 'campaign.config.json'
    if config_path.exists():
        require(load_json(config_path) == config, 'Preserved dream campaign config differs from the profile')
    else:
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        config_path.chmod(0o400)
    campaign_dir = output / 'campaign'
    if campaign_dir.exists():
        plan = pipeline.load_plan(campaign_dir)
        frozen_config = {key: value for key, value in config.items() if key != 'prompt'}
        require(plan['config'] == frozen_config, 'Preserved dream campaign plan differs from the profile')
        require(plan['runtime']['dry_run'] == dry_run, 'Dream campaign mode differs on resume')
    else:
        plan = pipeline.prepare(
            config_path,
            campaign_dir,
            project,
            dry_run=dry_run,
            router_url=router_url,
            catalog=catalog,
            router_profile=router_profile,
            capabilities=capabilities,
        )
    if dry_run:
        campaign_state_path = campaign_dir / 'state.json'
        if campaign_state_path.exists() and load_json(campaign_state_path).get('complete'):
            campaign_state = load_json(campaign_state_path)
        else:
            campaign_state = pipeline.execute(campaign_dir, accept_run=False)
        require(campaign_state.get('all_succeeded') is True, 'Dream generation did not complete')
        run_id = profile['campaign_id'] + '-' + profile['generator_id'] + '-s1'
        return {
            'status': 'completed',
            'dry_run': True,
            'campaign_id': profile['campaign_id'],
            'run_id': run_id,
            'campaign': str(campaign_dir),
            'integration_status': 'skipped-synthetic',
            'explanation': 'Synthetic generation is retained only in the campaign workspace.',
        }
    archived_attempts = []
    campaign_state = None
    for attempt in range(1, generation_attempts + 1):
        if campaign_dir.exists() and (campaign_dir / 'state.json').is_file():
            prior_state = load_json(campaign_dir / 'state.json')
            if prior_state.get('all_succeeded') is False and _retryable_generation_failure(campaign_dir, prior_state):
                if attempt == generation_attempts:
                    require(
                        False,
                        'Dream generation failed '
                        + str(attempt)
                        + ' time(s); the latest campaign remains preserved in '
                        + str(campaign_dir),
                    )
                archived_attempts.append(_archive_failed_generation(output, campaign_dir, prior_state, attempt))
        if not campaign_dir.exists():
            pipeline.prepare(
                config_path,
                campaign_dir,
                project,
                dry_run=False,
                router_url=router_url,
                catalog=catalog,
                router_profile=router_profile,
                capabilities=capabilities,
            )
        campaign_state = pipeline.execute(campaign_dir, accept_run=True)
        require(
            not campaign_state.get('cancelled'),
            'Dream generation was cancelled; the partial campaign is preserved and was not admitted',
        )
        if campaign_state.get('all_succeeded') is True:
            break
        retryable = _retryable_generation_failure(campaign_dir, campaign_state)
        if not retryable:
            require(False, 'Dream generation stopped with a non-retryable failure in ' + str(campaign_dir))
        if attempt == generation_attempts:
            require(
                False,
                'Dream generation failed '
                + str(attempt)
                + ' time(s); the latest campaign remains preserved in '
                + str(campaign_dir),
            )
        archived_attempts.append(_archive_failed_generation(output, campaign_dir, campaign_state, attempt))
    require(
        campaign_state is not None and campaign_state.get('all_succeeded') is True, 'Dream generation did not complete'
    )
    pipeline.admit(campaign_dir, project)
    run_id = profile['campaign_id'] + '-' + profile['generator_id'] + '-s1'
    require(
        run_id in {run['id'] for run in atlas.validate(project)['runs']},
        'Generated run was not imported into the project',
    )
    capture = artefact_capture.ensure(project, run_id)
    evaluation = evaluation_run.run(
        project,
        run_id,
        None,
        output / 'evaluation',
        model=profile['evaluator_model'],
        reasoning=profile['evaluator_reasoning'],
        evaluation_id=profile['evaluation_id'],
        review_id=profile['review_id'],
        reviewer=profile['reviewer'],
        run_label=profile.get('run_label'),
        run_title=profile.get('run_title'),
        repetitions=profile['repetitions'],
        minimum_agreement=profile['minimum_agreement'],
        repairs=repairs if repairs is not None else profile['repairs'],
        layout=profile['layout'],
        budget=budget or profile['evaluation_budget'],
        dry_run=False,
        accept_run=True,
        router_url=router_url,
        catalog=catalog,
        router_profile=router_profile,
        capabilities=capabilities,
    )
    return {
        'status': 'completed',
        'dry_run': False,
        'campaign_id': profile['campaign_id'],
        'run_id': run_id,
        'runner': {'model': profile['generator_model'], 'reasoning': profile['generator_reasoning']},
        'scorer': {'model': profile['evaluator_model'], 'reasoning': profile['evaluator_reasoning']},
        'campaign': str(campaign_dir),
        'generation_attempts_used': len(archived_attempts) + 1,
        'archived_generation_attempts': [str(path) for path in archived_attempts],
        'artefact_capture': capture,
        'evaluation': evaluation,
        'integration_status': evaluation['integration_status'],
        'ui_url': evaluation['ui_url'],
        'next_command': 'npm run dev:full',
    }
