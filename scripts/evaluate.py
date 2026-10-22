#!/usr/bin/env python3
"""Read worlds against the rubric, dream new ones and admit both, through Codex
and the local model router.

Live work calls models, which cost money, and refuses without --accept-run.
-n/--dry-run rehearses with synthetic answers and calls no model. Each command
prints its result as JSON on stdout and progress on stderr.
"""

import argparse
import contextlib
import json
import signal
import subprocess
import sys
import time
import tempfile
from pathlib import Path

import atlas
import artefact_capture
import bundles
import codex_worker as worker
import dream_run
import evaluation_contract
import evaluation_run
import extraction_pipeline
import page_prepare
import pipeline
import reconcile_generation
import rubric_policy
import scoring_pipeline


ROOT = Path(__file__).resolve().parents[1]


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def campaign_status(root):
    root = Path(root).resolve()
    plan = pipeline.load_plan(root)
    state_path = root / 'state.json'
    state = bundles.load_json(state_path) if state_path.is_file() else {'jobs': [], 'complete': False}
    return {
        'campaign': plan['config']['id'],
        'gateway': plan['gateway'],
        'dry_run': plan['runtime']['dry_run'],
        'queue': plan['queue'],
        'complete': state.get('complete', False),
        'jobs': state.get('jobs', []),
    }


def validate_campaign(root):
    root = Path(root).resolve()
    plan = pipeline.load_plan(root)
    require_report = plan['config'].get('existing_runs') or plan['config'].get('generators')
    if require_report:
        report_root = root / 'report'
        atlas.validate(report_root)
    bundles_checked = 0
    for path in sorted((root / 'jobs').glob('generation-*/bundle')):
        bundles.validate_bundle(path)
        bundles_checked += 1
    reviews_checked = sum(1 for path in (root / 'jobs').glob('review-*/review.json') if path.is_file())
    return {
        'campaign': plan['config']['id'],
        'plan_sha256': bundles.sha(bundles.canonical(plan)),
        'gateway': plan['gateway'],
        'bundles_checked': bundles_checked,
        'reviews_checked': reviews_checked,
    }


def doctor(config_file, *, project, router_url, catalog, router_profile, capabilities, dry_run):
    config = pipeline.validate_config(bundles.load_json(Path(config_file)))
    project = Path(project).resolve()
    atlas.validate(project)
    prompt = Path(config['prompt'])
    if not prompt.is_absolute():
        prompt = project / prompt
    prompt_bytes = prompt.read_bytes()
    prompt_bytes.decode('utf-8')
    jobs = config['generators'] + config['reviewers']
    with tempfile.TemporaryDirectory(prefix='after-poem-doctor-') as temporary:
        runtime = worker.freeze_runtime(
            Path(temporary), jobs, dry_run, router_url, catalog, router_profile, capability_path=capabilities
        )
    result = {
        'status': 'valid',
        'campaign': config['id'],
        'gateway': runtime['gateway'],
        'dry_run': runtime['dry_run'],
        'models': sorted({job['model'] for job in jobs}),
        'prompt_bytes': len(prompt_bytes),
        'prompt_sha256': bundles.sha(prompt_bytes),
        'model_capabilities': runtime['model_capabilities'],
        'capability_registry_sha256': runtime['capability_registry_sha256'],
    }
    if not dry_run:
        result.update(
            codex_version=runtime['codex_version'],
            router=runtime['router'],
            openai_auth_available=runtime['openai_auth_available'],
        )
    return result


ROUTER_URL = 'http://127.0.0.1:18766/v1'
REASONING_ORDER = ['none', 'low', 'medium', 'high', 'xhigh', 'max']
REASONING = sorted(
    evaluation_contract.REASONING,
    key=lambda level: REASONING_ORDER.index(level) if level in REASONING_ORDER else len(REASONING_ORDER),
)
PROJECT_HELP = 'Study directory (default: this repository)'
ACCEPT_HELP = 'Allow model calls, which cost money; live work refuses without it'
DRY_RUN_HELP = 'Rehearse with synthetic answers; calls no model'
CANCELLED = 'Evaluation cancelled; preserved work can be resumed with the same command.'
LIVE_BUDGET = 'budget (live runs need --max-turns, --max-input-tokens and --max-output-tokens)'
BUDGET_NOTE = (
    'Work stops before the next turn once any limit is reached, so the turn in progress can pass it. '
    'Cost is estimated from reported tokens and the --price-* flags.'
)
EPILOG = '''examples:
  # Read a world as a profile describes (calls a model)
  python3 scripts/evaluate.py run-profile examples/evaluation-profile-deepseek.json --accept-run
  # Rehearse the same reading with synthetic answers
  python3 scripts/evaluate.py run-profile examples/evaluation-profile-deepseek.json -n
  # Check the profile, Codex and the router first
  python3 scripts/evaluate.py doctor-profile examples/evaluation-profile-deepseek.json
  # Dream a new world, read it and admit both (calls models)
  python3 scripts/evaluate.py run-dream-profile examples/dream-profile-deepseek.json --accept-run

Rerun an interrupted command to continue it. Campaign commands come last,
from doctor on.

Reference: docs/commands.md
'''


def add_project(parser):
    parser.add_argument('--project', type=Path, default=ROOT, metavar='DIR', help=PROJECT_HELP)


def add_reasoning(parser, flag='--reasoning', text='Reasoning effort', default='high'):
    parser.add_argument(flag, choices=REASONING, default=default, help=text)


def add_router(parser):
    group = parser.add_argument_group('Codex and router')
    group.add_argument(
        '--router-url', default=ROUTER_URL, metavar='URL', help='Local model router; only http://127.0.0.1 is accepted'
    )
    group.add_argument(
        '--catalog',
        type=Path,
        metavar='FILE',
        help='Codex model catalog (default: models.json in CODEX_HOME, ~/.codex)',
    )
    group.add_argument(
        '--router-profile',
        type=Path,
        metavar='FILE',
        help='Codex config for the router (default: unified-openrouter.config.toml in CODEX_HOME)',
    )
    group.add_argument(
        '--capabilities',
        type=Path,
        metavar='FILE',
        help='Model capability registry (default: data/model-capabilities.json)',
    )


def add_safety(parser, dry_run=DRY_RUN_HELP, accept=ACCEPT_HELP):
    parser.add_argument('-n', '--dry-run', action='store_true', help=dry_run)
    if accept:
        parser.add_argument('--accept-run', action='store_true', help=accept)


def add_budget(parser, title=LIVE_BUDGET):
    group = parser.add_argument_group(title, BUDGET_NOTE)
    group.add_argument('--max-turns', type=int, metavar='N', help='Turns to allow')
    group.add_argument('--max-input-tokens', type=int, metavar='N', help='Input tokens to allow, cached ones included')
    group.add_argument('--max-output-tokens', type=int, metavar='N', help='Output tokens to allow')
    group.add_argument('--max-seconds', type=int, metavar='N', help='Seconds of turn time to allow')
    group.add_argument(
        '--max-usd', type=float, metavar='USD', help='Estimated US dollars to allow; needs all three --price-* flags'
    )
    group.add_argument(
        '--price-input',
        type=float,
        metavar='USD',
        help='US dollars per million uncached input tokens, for the estimate',
    )
    group.add_argument(
        '--price-cached-input',
        type=float,
        metavar='USD',
        help='US dollars per million cached input tokens, for the estimate',
    )
    group.add_argument(
        '--price-output', type=float, metavar='USD', help='US dollars per million output tokens, for the estimate'
    )


def budget_from(args):
    """Return the command-line budget, or None when no budget flag was given."""
    prices = [args.price_input, args.price_cached_input, args.price_output]
    values = {
        'max_turns': args.max_turns,
        'max_input_tokens': args.max_input_tokens,
        'max_output_tokens': args.max_output_tokens,
        'max_seconds': args.max_seconds,
        'max_usd': args.max_usd,
    }
    if all(value is None for value in list(values.values()) + prices):
        return None
    budget = {key: value for key, value in values.items() if value is not None}
    if any(price is not None for price in prices):
        budget['pricing'] = {
            'input_per_million': args.price_input,
            'cached_input_per_million': args.price_cached_input,
            'output_per_million': args.price_output,
        }
    return budget


def run_profiles(profiles, *, project, parallel, dry_run, accept_run):
    """Run several evaluation profiles as separate processes, at most `parallel` at once.

    Each profile is an independent session and admits its own package (admission
    is serialized by a lock). Stopping the batch sends SIGTERM to every running
    child, which cancels its Codex process group and keeps its turns resumable.
    """
    require = bundles.require
    require(1 <= parallel <= 16, '--parallel must be 1-16')
    require(dry_run or accept_run, 'Live batches require --accept-run')
    pending = [Path(path).resolve() for path in profiles]
    require(len(set(pending)) == len(pending), 'Each profile may appear only once')
    running, results = {}, {}
    label = lambda path: path.parent.name + '/' + path.name
    try:
        while pending or running:
            while pending and len(running) < parallel:
                path = pending.pop(0)
                command = [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    'run-profile',
                    str(path),
                    '--project',
                    str(project),
                ] + (['--dry-run'] if dry_run else ['--accept-run'])
                running[path] = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                print('Started ' + label(path), file=sys.stderr, flush=True)
            for path, process in list(running.items()):
                if process.poll() is None:
                    continue
                stdout, stderr = process.communicate()
                del running[path]
                try:
                    value = json.loads(stdout) if process.returncode == 0 else None
                except json.JSONDecodeError:
                    value = None
                results[label(path)] = (
                    {
                        'status': 'completed',
                        'run_id': value['evaluation_run_id'],
                        'integration_status': value.get('integration_status'),
                        'usage': value['evaluation'].get('usage'),
                    }
                    if value
                    else {'status': 'failed', 'error': (stderr or stdout).strip()[-1500:]}
                )
                print('Finished ' + label(path) + ' · ' + results[label(path)]['status'], file=sys.stderr, flush=True)
            time.sleep(0.2)
    except KeyboardInterrupt:
        for process in running.values():
            process.terminate()
        for path, process in running.items():
            try:
                process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                process.kill()
            results[label(path)] = {'status': 'cancelled'}
        raise
    return {
        'status': 'completed' if all(item['status'] == 'completed' for item in results.values()) else 'partial',
        'runs': results,
    }


def stop_on_terminate(signum, frame):
    # Turn SIGTERM into the same path as Ctrl+C so the running Codex process group
    # is stopped and a cancelled receipt is written before exit.
    raise KeyboardInterrupt


def main():
    signal.signal(signal.SIGTERM, stop_on_terminate)
    parser = argparse.ArgumentParser(description=__doc__, epilog=EPILOG, formatter_class=atlas.HelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True, metavar='COMMAND')

    def command(name, summary):
        return commands.add_parser(name, help=summary, description=summary + '.', formatter_class=atlas.HelpFormatter)

    # Profiles and dreams: the common way in.
    profile_parser = command(
        'run-profile', 'Read a world as a JSON profile describes, then admit it; rerunning resumes'
    )
    profile_parser.add_argument('profile', type=Path, help='Evaluation profile JSON')
    add_project(profile_parser)
    add_safety(profile_parser, 'Rehearse with synthetic answers in a separate OUTPUT.dry-run; calls no model')
    add_router(profile_parser)
    add_budget(profile_parser, "budget (any flag here replaces the profile's whole budget)")

    batch_parser = command(
        'run-profiles', 'Run several evaluation profiles at once, each as its own session and package'
    )
    batch_parser.add_argument(
        'profiles', type=Path, nargs='+', metavar='profile', help='Evaluation profile JSON; each may appear once'
    )
    add_project(batch_parser)
    batch_parser.add_argument('--parallel', type=int, default=4, metavar='N', help='Profiles to run at once, 1-16')
    add_safety(batch_parser)

    profile_doctor_parser = command(
        'doctor-profile', 'Check a profile, Codex, the router, the catalog and reasoning ' 'support; calls no model'
    )
    profile_doctor_parser.add_argument('profile', type=Path, help='Evaluation profile JSON')
    add_project(profile_doctor_parser)
    add_router(profile_doctor_parser)

    dream_parser = command(
        'run-dream-profile', 'Generate a new world from a dream profile, read it and admit both; ' 'rerunning resumes'
    )
    dream_parser.add_argument('profile', type=Path, help='Dream profile JSON')
    add_project(dream_parser)
    dream_parser.add_argument(
        '--runner-model', metavar='MODEL', help="Model that creates the world (default: the profile's)"
    )
    add_reasoning(dream_parser, '--runner-reasoning', "Runner's reasoning effort (default: the profile's)", None)
    dream_parser.add_argument(
        '--scorer-model', metavar='MODEL', help="Model that reads the world (default: the profile's)"
    )
    add_reasoning(dream_parser, '--scorer-reasoning', "Scorer's reasoning effort (default: the profile's)", None)
    dream_parser.add_argument(
        '--generation-attempts',
        type=int,
        default=1,
        metavar='N',
        help='Generation attempts, 1-5; after a retryable failure the next is a fresh, ' 'full generation',
    )
    dream_parser.add_argument(
        '--repairs',
        type=int,
        metavar='N',
        help="Repair turns per reading task after an invalid answer " "(default: the profile's, else 2)",
    )
    add_safety(dream_parser, 'Rehearse the generation alone in OUTPUT.dry-run; calls no model')
    add_router(dream_parser)
    add_budget(dream_parser, "reading budget (any flag here replaces the profile's evaluation_budget)")

    dream_profile_parser = command(
        'doctor-dream-profile', "Check a dream profile's runner and scorer, Codex and the router; calls no model"
    )
    dream_profile_parser.add_argument('profile', type=Path, help='Dream profile JSON')
    add_project(dream_profile_parser)
    dream_profile_parser.add_argument(
        '--runner-model', metavar='MODEL', help="Check this runner model instead of the profile's"
    )
    add_reasoning(dream_profile_parser, '--runner-reasoning', "Check this runner reasoning effort instead", None)
    dream_profile_parser.add_argument(
        '--scorer-model', metavar='MODEL', help="Check this scorer model instead of the profile's"
    )
    add_reasoning(dream_profile_parser, '--scorer-reasoning', "Check this scorer reasoning effort instead", None)
    add_router(dream_profile_parser)

    # A reading in single steps.
    policy_parser = command(
        'generate-policy', 'Write the full-rubric scoring policy from data/rubric.json; calls no model'
    )
    add_project(policy_parser)
    policy_parser.add_argument('--output', type=Path, required=True, metavar='FILE', help='Policy JSON to write')
    policy_parser.add_argument(
        '--repetitions', type=int, default=1, metavar='N', help='Independent sessions per judgment'
    )
    policy_parser.add_argument(
        '--minimum-agreement',
        type=float,
        default=0.67,
        metavar='SHARE',
        help='Share of sessions that must agree on a level',
    )

    score_parser = command(
        'score', 'Read the evidence once per session, answer every judgment and aggregate them; ' 'rerunning resumes'
    )
    add_project(score_parser)
    target = score_parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--run-id', metavar='RUN_ID', help='World to read; the session extracts first, then judges')
    target.add_argument(
        '--extraction', type=Path, metavar='FILE', help='Judge with this validated extraction.json instead'
    )
    score_parser.add_argument('--policy', type=Path, required=True, metavar='FILE', help='Scoring policy JSON')
    score_parser.add_argument(
        '--output', type=Path, required=True, metavar='DIR', help='Directory for the scoreflow; an existing one resumes'
    )
    score_parser.add_argument('--evaluation-id', metavar='ID', help='Stable evaluation ID (default: evaluation-RUN_ID)')
    score_parser.add_argument(
        '--model', required=True, metavar='MODEL', help='Model to read with, as the catalog names it'
    )
    add_reasoning(score_parser)
    score_parser.add_argument(
        '--layout',
        choices=scoring_pipeline.LAYOUTS,
        default='compact',
        help='compact asks every axis in one turn; per-axis asks one turn each',
    )
    score_parser.add_argument(
        '--repairs',
        type=int,
        default=2,
        metavar='N',
        help='Repair turns per task after an invalid answer; invalid answers are kept',
    )
    score_parser.add_argument(
        '--synthetic-outcomes', metavar='LIST', help='Dry run only: comma-separated outcomes, one per repetition'
    )
    controls = score_parser.add_argument_group('controls and trials (never admitted)')
    controls.add_argument(
        '--control-seed', type=Path, metavar='FILE', help='Show the world with this seed text instead of its own'
    )
    controls.add_argument(
        '--mask-terms', type=Path, metavar='FILE', help="JSON list of the seed's words to mask in the evidence"
    )
    controls.add_argument(
        '--ablate',
        type=Path,
        metavar='FILE',
        help='JSON list of {path, start, end} line ranges to blank in the evidence',
    )
    add_safety(score_parser)
    add_router(score_parser)
    add_budget(score_parser)

    score_validate_parser = command(
        'validate-evaluation', 'Validate a scoreflow output against its world; calls no model'
    )
    score_validate_parser.add_argument('directory', type=Path, help='Scoreflow output directory')
    add_project(score_validate_parser)

    score_recover_parser = command(
        'recover-score',
        'Finish a scoreflow whose sessions completed but whose record ' 'was not written; calls no model',
    )
    score_recover_parser.add_argument('directory', type=Path, help='Scoreflow output directory without evaluation.json')
    score_recover_parser.add_argument(
        '--evaluation-id', metavar='ID', help='Stable evaluation ID (default: recovered-evaluation-RUN_ID)'
    )
    add_project(score_recover_parser)

    integrate_parser = command(
        'integrate-evaluation', 'Admit a validated evaluation as its own run package and rebuild the site'
    )
    add_project(integrate_parser)
    integrate_parser.add_argument(
        '--evaluation', type=Path, required=True, metavar='DIR', help='Scoreflow output directory'
    )
    integrate_parser.add_argument('--review-id', required=True, metavar='ID', help='Stable ID for the review record')
    integrate_parser.add_argument(
        '--evaluation-run-id', metavar='ID', help='ID of the run package (default: the evaluation ID)'
    )
    integrate_parser.add_argument('--run-label', metavar='TEXT', help="Run label (default: the evaluation's)")
    integrate_parser.add_argument(
        '--run-title', metavar='TEXT', help="Run title (default: the world's title · rubric reading)"
    )
    integrate_parser.add_argument('--reviewer', metavar='TEXT', help='Reviewer name (default: MODEL · rubric session)')

    end_to_end_parser = command(
        'run-evaluation',
        'Extract, score, admit and rebuild for one world, ' 'every option on the command line; rerunning resumes',
    )
    end_to_end_parser.add_argument('run_id', help='World to read')
    add_project(end_to_end_parser)
    end_to_end_parser.add_argument(
        '--policy',
        type=Path,
        metavar='FILE',
        help='Custom full-rubric policy (default: generated from data/rubric.json)',
    )
    end_to_end_parser.add_argument(
        '--output',
        type=Path,
        required=True,
        metavar='DIR',
        help='Directory for the evaluation; an existing one resumes',
    )
    end_to_end_parser.add_argument(
        '--model', required=True, metavar='MODEL', help='Model to read with, as the catalog names it'
    )
    add_reasoning(end_to_end_parser)
    end_to_end_parser.add_argument('--evaluation-id', required=True, metavar='ID', help='Stable evaluation ID')
    end_to_end_parser.add_argument(
        '--evaluation-run-id', metavar='ID', help='ID of the run package (default: the evaluation ID)'
    )
    end_to_end_parser.add_argument('--review-id', required=True, metavar='ID', help='Stable ID for the review record')
    end_to_end_parser.add_argument('--run-label', metavar='TEXT', help="Run label (default: the evaluation's)")
    end_to_end_parser.add_argument(
        '--run-title', metavar='TEXT', help="Run title (default: the world's title · rubric reading)"
    )
    end_to_end_parser.add_argument('--reviewer', metavar='TEXT', help='Reviewer name (default: MODEL · rubric session)')
    end_to_end_parser.add_argument(
        '--repetitions',
        type=int,
        default=1,
        metavar='N',
        help='Independent evidence sessions; more than one adds a per-axis quorum',
    )
    end_to_end_parser.add_argument(
        '--minimum-agreement',
        type=float,
        default=0.67,
        metavar='SHARE',
        help='Share of sessions that must agree on a level',
    )
    end_to_end_parser.add_argument(
        '--repairs',
        type=int,
        default=2,
        metavar='N',
        help='Repair turns per task after an invalid answer; invalid answers are kept',
    )
    end_to_end_parser.add_argument(
        '--layout',
        choices=scoring_pipeline.LAYOUTS,
        default='compact',
        help='compact asks every axis in one turn; per-axis asks one turn each',
    )
    add_safety(end_to_end_parser)
    add_router(end_to_end_parser)
    add_budget(end_to_end_parser)

    page_parser = command('prepare-page', 'Prepare presentation JSON and static HTML for an evaluation; calls no model')
    add_project(page_parser)
    page_parser.add_argument('--evaluation', type=Path, required=True, metavar='DIR', help='Scoreflow output directory')
    page_parser.add_argument(
        '--output', type=Path, required=True, metavar='DIR', help='New directory for the presentation'
    )
    page_parser.add_argument(
        '--presentation-id', metavar='ID', help='Stable presentation ID (default: EVALUATION_ID-presentation-v1)'
    )

    page_validate_parser = command(
        'validate-presentation', 'Validate a prepared presentation and its manifest; ' 'calls no model'
    )
    page_validate_parser.add_argument('directory', type=Path, help='Presentation directory')
    add_project(page_validate_parser)

    capture_parser = command(
        'capture-artefact', 'Register a browser world and capture its screenshot in a sandboxed browser'
    )
    capture_parser.add_argument('run_id', help='World with a top-level index.html')
    add_project(capture_parser)
    capture_parser.add_argument(
        '--description', metavar='TEXT', help="Screenshot description (default: taken from the world's README)"
    )

    # Campaigns: frozen multi-model generation and review, and extraction on its own.
    doctor_parser = command('doctor', 'Check a campaign config, the study, Codex and the router; calls no model')
    doctor_parser.add_argument('config', type=Path, help='Campaign config JSON')
    add_project(doctor_parser)
    add_safety(doctor_parser, 'Check the config and study only, not Codex or the router', accept=None)
    add_router(doctor_parser)

    plan_parser = command('plan', 'Freeze a campaign config into a new campaign directory; calls no model')
    plan_parser.add_argument('config', type=Path, help='Campaign config JSON')
    plan_parser.add_argument('--output', type=Path, required=True, metavar='DIR', help='New campaign directory')
    add_project(plan_parser)
    add_safety(plan_parser, 'Freeze a synthetic campaign; running it calls no model', accept=None)
    add_router(plan_parser)

    for name, summary in [
        ('run', 'Start a prepared campaign: generate and review every queued job'),
        ('resume', 'Continue an interrupted campaign; jobs that have a receipt are not repeated'),
    ]:
        run_parser = command(name, summary)
        run_parser.add_argument('campaign', type=Path, help='Campaign directory made by plan')
        run_parser.add_argument(
            '--accept-run',
            action='store_true',
            help='Allow model calls, which cost money; a live campaign refuses without it',
        )

    status_parser = command('status', "Show a campaign's queue and job states; calls no model")
    status_parser.add_argument('campaign', type=Path, help='Campaign directory')
    validate_parser = command(
        'validate', "Validate a campaign's report and bundles and count its reviews; calls no model"
    )
    validate_parser.add_argument('campaign', type=Path, help='Campaign directory')
    contract_parser = command(
        'validate-contract', 'Validate a normalized evaluation contract directory; calls no model'
    )
    contract_parser.add_argument('directory', type=Path, help='Contract directory')

    admit_parser = command('admit', "Admit a completed live campaign's bundles and reviews into the study")
    admit_parser.add_argument('campaign', type=Path, help='Campaign directory')
    add_project(admit_parser)

    reconcile_parser = command(
        'reconcile-generation',
        'Admit a finished generation that failed only the after-process '
        'transport check, as a separate reconciled bundle',
    )
    reconcile_parser.add_argument('job', type=Path, help='Campaign job directory (jobs/generation-NNNN)')
    add_project(reconcile_parser)
    reconcile_parser.add_argument(
        '--cohort', required=True, metavar='ID', help='Cohort whose prompt the generation received'
    )
    reconcile_parser.add_argument('--label', required=True, metavar='TEXT', help='Label for the reconciled run')
    reconcile_parser.add_argument('--title', required=True, metavar='TEXT', help='Title for the reconciled run')
    reconcile_parser.add_argument('--verify-only', action='store_true', help='Check the job and write nothing')

    extract_parser = command(
        'extract', 'Extract source-only evidence for one world in its own session ' '(score extracts by itself)'
    )
    extract_parser.add_argument('run_id', help='World to read')
    add_project(extract_parser)
    extract_parser.add_argument(
        '--output',
        type=Path,
        required=True,
        metavar='DIR',
        help='Directory for the extraction; an existing one resumes',
    )
    extract_parser.add_argument(
        '--extraction-id', metavar='ID', help='Stable extraction ID (default: extraction-RUN_ID)'
    )
    extract_parser.add_argument(
        '--model', required=True, metavar='MODEL', help='Model to read with, as the catalog names it'
    )
    add_reasoning(extract_parser)
    extract_parser.add_argument(
        '--repairs',
        type=int,
        default=2,
        metavar='N',
        help='Repair turns after an invalid answer; invalid answers are kept',
    )
    add_safety(extract_parser)
    add_router(extract_parser)
    add_budget(extract_parser)

    extraction_validate_parser = command(
        'validate-extraction', 'Validate an extraction record against its world; ' 'calls no model'
    )
    extraction_validate_parser.add_argument('extraction', type=Path, help='extraction.json to validate')
    add_project(extraction_validate_parser)

    extraction_recover_parser = command(
        'recover-extraction',
        'Finish an extraction whose session completed but whose ' 'record was not written; calls no model',
    )
    extraction_recover_parser.add_argument('directory', type=Path, help='Extraction output directory')
    add_project(extraction_recover_parser)

    args = parser.parse_args()
    try:
        if args.command == 'doctor':
            result = doctor(
                args.config,
                project=args.project,
                router_url=args.router_url,
                catalog=args.catalog,
                router_profile=args.router_profile,
                capabilities=args.capabilities,
                dry_run=args.dry_run,
            )
        elif args.command == 'plan':
            result = pipeline.prepare(
                args.config,
                args.output,
                args.project,
                dry_run=args.dry_run,
                router_url=args.router_url,
                catalog=args.catalog,
                router_profile=args.router_profile,
                capabilities=args.capabilities,
            )
        elif args.command in ['run', 'resume']:
            with contextlib.redirect_stdout(sys.stderr):
                result = pipeline.execute(args.campaign, accept_run=args.accept_run)
        elif args.command == 'status':
            result = campaign_status(args.campaign)
        elif args.command == 'validate':
            result = validate_campaign(args.campaign)
        elif args.command == 'validate-contract':
            result = evaluation_contract.validate_directory(args.directory)
        elif args.command == 'generate-policy':
            policy = rubric_policy.build(
                atlas.load(args.project / 'data/rubric.json'),
                repetitions=args.repetitions,
                minimum_agreement=args.minimum_agreement,
            )
            evaluation_contract.validate_policy(policy)
            path = rubric_policy.write(policy, args.output)
            result = {
                'status': 'generated',
                'policy': str(path),
                'policy_id': policy['policy_id'],
                'nodes': len(policy['nodes']),
                'edges': len(policy['edges']),
                'judgments': sum(node['kind'] == 'judgment' for node in policy['nodes']),
                'policy_sha256': bundles.sha(path.read_bytes()),
            }
        elif args.command == 'extract':
            with contextlib.redirect_stdout(sys.stderr):
                result = extraction_pipeline.run(
                    args.project,
                    args.run_id,
                    args.output,
                    model=args.model,
                    reasoning=args.reasoning,
                    extraction_id=args.extraction_id,
                    dry_run=args.dry_run,
                    accept_run=args.accept_run,
                    router_url=args.router_url,
                    catalog=args.catalog,
                    router_profile=args.router_profile,
                    capabilities=args.capabilities,
                    budget=budget_from(args),
                    repairs=args.repairs,
                )
        elif args.command == 'validate-extraction':
            result = extraction_pipeline.validate_output(args.project, args.extraction)
        elif args.command == 'recover-extraction':
            result = extraction_pipeline.recover(args.project, args.directory)
        elif args.command == 'capture-artefact':
            result = artefact_capture.ensure(args.project, args.run_id, description=args.description)
        elif args.command == 'score':
            outcomes = args.synthetic_outcomes.split(',') if args.synthetic_outcomes else None
            with contextlib.redirect_stdout(sys.stderr):
                result = scoring_pipeline.run(
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
                    router_url=args.router_url,
                    catalog=args.catalog,
                    router_profile=args.router_profile,
                    capabilities=args.capabilities,
                    budget=budget_from(args),
                    repairs=args.repairs,
                    layout=args.layout,
                    control_seed_path=args.control_seed,
                    masked_terms_path=args.mask_terms,
                    ablated_ranges_path=args.ablate,
                )
        elif args.command == 'validate-evaluation':
            result = scoring_pipeline.validate_output(args.project, args.directory)
        elif args.command == 'recover-score':
            result = scoring_pipeline.recover(args.project, args.directory, evaluation_id=args.evaluation_id)
        elif args.command == 'prepare-page':
            result = page_prepare.prepare_page(
                args.project, args.evaluation, args.output, presentation_id=args.presentation_id
            )
        elif args.command == 'validate-presentation':
            result = page_prepare.validate_output(args.project, args.directory)
        elif args.command == 'integrate-evaluation':
            with contextlib.redirect_stdout(sys.stderr):
                result = evaluation_run.integrate_evaluation(
                    args.project,
                    args.evaluation,
                    review_id=args.review_id,
                    evaluation_run_id=args.evaluation_run_id,
                    run_label=args.run_label,
                    run_title=args.run_title,
                    reviewer=args.reviewer,
                )
        elif args.command == 'run-evaluation':
            with contextlib.redirect_stdout(sys.stderr):
                result = evaluation_run.run(
                    args.project,
                    args.run_id,
                    args.policy,
                    args.output,
                    model=args.model,
                    reasoning=args.reasoning,
                    evaluation_id=args.evaluation_id,
                    evaluation_run_id=args.evaluation_run_id,
                    review_id=args.review_id,
                    run_label=args.run_label,
                    run_title=args.run_title,
                    reviewer=args.reviewer,
                    repetitions=args.repetitions,
                    minimum_agreement=args.minimum_agreement,
                    repairs=args.repairs,
                    budget=budget_from(args),
                    layout=args.layout,
                    dry_run=args.dry_run,
                    accept_run=args.accept_run,
                    router_url=args.router_url,
                    catalog=args.catalog,
                    router_profile=args.router_profile,
                    capabilities=args.capabilities,
                )
        elif args.command == 'run-profiles':
            result = run_profiles(
                args.profiles,
                project=args.project,
                parallel=args.parallel,
                dry_run=args.dry_run,
                accept_run=args.accept_run,
            )
        elif args.command == 'doctor-profile':
            result = evaluation_run.preflight_profile(
                args.project,
                args.profile,
                router_url=args.router_url,
                catalog=args.catalog,
                router_profile=args.router_profile,
                capabilities=args.capabilities,
            )
        elif args.command == 'doctor-dream-profile':
            result = dream_run.preflight(
                args.project,
                args.profile,
                router_url=args.router_url,
                catalog=args.catalog,
                router_profile=args.router_profile,
                capabilities=args.capabilities,
                runner_model=args.runner_model,
                runner_reasoning=args.runner_reasoning,
                scorer_model=args.scorer_model,
                scorer_reasoning=args.scorer_reasoning,
            )
        elif args.command == 'run-dream-profile':
            with contextlib.redirect_stdout(sys.stderr):
                result = dream_run.run(
                    args.project,
                    args.profile,
                    dry_run=args.dry_run,
                    accept_run=args.accept_run,
                    router_url=args.router_url,
                    catalog=args.catalog,
                    router_profile=args.router_profile,
                    capabilities=args.capabilities,
                    runner_model=args.runner_model,
                    runner_reasoning=args.runner_reasoning,
                    scorer_model=args.scorer_model,
                    scorer_reasoning=args.scorer_reasoning,
                    generation_attempts=args.generation_attempts,
                    budget=budget_from(args),
                    repairs=args.repairs,
                )
        elif args.command == 'run-profile':
            with contextlib.redirect_stdout(sys.stderr):
                result = evaluation_run.run_profile(
                    args.project,
                    args.profile,
                    dry_run=args.dry_run,
                    accept_run=args.accept_run,
                    router_url=args.router_url,
                    catalog=args.catalog,
                    router_profile=args.router_profile,
                    capabilities=args.capabilities,
                    budget=budget_from(args),
                )
        elif args.command == 'reconcile-generation':
            if args.verify_only:
                result = dict(reconcile_generation.verify(args.job), status='verified')
            else:
                with contextlib.redirect_stdout(sys.stderr):
                    result = reconcile_generation.admit(
                        args.project, args.job, cohort_id=args.cohort, label=args.label, title=args.title
                    )
        else:
            with contextlib.redirect_stdout(sys.stderr):
                pipeline.admit(args.campaign, args.project)
            result = {'status': 'admitted', 'campaign': str(Path(args.campaign).resolve())}
        emit(result)
        if args.command in ['run', 'resume'] and not result.get('all_succeeded'):
            return 1
        if args.command == 'extract' and result.get('status') != 'completed':
            return 1
        if args.command in ['score', 'recover-score'] and result.get('status') != 'completed':
            return 1
        if (
            args.command in ['run-evaluation', 'run-profile', 'run-dream-profile', 'run-profiles']
            and result.get('status') != 'completed'
        ):
            return 1
    except KeyboardInterrupt:
        print(CANCELLED, file=sys.stderr)
        return 130
    except (ValueError, OSError, KeyError, TypeError) as error:
        print('Evaluation stopped: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    try:
        status = main()
    except KeyboardInterrupt:
        print(CANCELLED, file=sys.stderr)
        status = 130
    raise SystemExit(status)
