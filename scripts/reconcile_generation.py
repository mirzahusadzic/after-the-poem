#!/usr/bin/env python3
"""Offline reconciliation of a finished generation rejected only by the old transport check.

Early dream runs finished cleanly, but Codex then added its exact project-trust
stanza to the private worker config and the harness of that time rejected any
config change. This module re-verifies such a job without inference or
execution and writes a separate, reconciled bundle. The original receipt and
bundle are never modified; the reconciled run record says what happened.
"""

import json
import shutil
from pathlib import Path

import bundles
import codex_worker as worker
import sealed_run as sealed
from bundles import canonical, load_json, require, sha


RULE = 'worker-transport-trust-stanza-v1'
EXPECTED_PHASES = [('before-audit', 'passed'), ('before-launch', 'passed'), ('after-process', 'failed')]


def verify(job):
    """Return the verified facts for one preserved generation job, or raise."""
    job = Path(job).resolve()
    campaign = job.parents[1]
    receipt_path = job / 'receipt.json'
    receipt = load_json(receipt_path)
    require(
        receipt.get('status') == 'failed' and receipt.get('error') == 'runtime-integrity-failed',
        'Only jobs rejected by the runtime integrity check can be reconciled',
    )
    require(receipt.get('exit_code') == 0 and receipt.get('stopped_reason') is None, 'The worker did not exit cleanly')
    require(receipt.get('isolation', {}).get('status') == 'passed', 'Isolation canaries did not pass')
    phases = [(phase['phase'], phase['status']) for phase in receipt.get('runtime_checks', {}).get('phases', [])]
    require(phases == EXPECTED_PHASES, 'Only the after-process check may have failed')
    require(
        (job / 'control/integrity-failure.txt').read_text(encoding='utf-8').strip() == 'Worker transport changed',
        'The after-process failure was not the transport check',
    )
    transport = (campaign / 'transport.config.toml').read_bytes()
    require(sha(transport) == receipt['runtime']['transport_config_sha256'], 'Frozen campaign transport changed')
    config = (job / 'control/codex-home/config.toml').read_bytes()
    require(config != transport, 'The worker config is unchanged; nothing to reconcile')
    # The current rule accepts exactly the frozen bytes plus one exact trust stanza.
    worker.validate_worker_transport(config, transport, job / 'world')
    events = job / 'events.jsonl'
    require(sha(events.read_bytes()) == receipt.get('events_sha256'), 'Event stream changed since the receipt')
    outcome = worker.event_outcome(events)
    require(outcome['thread_id'] == receipt.get('thread_id'), 'Event thread differs from the receipt')
    final = job / 'control/final-message.txt'
    require(
        final.is_file() and sha(final.read_bytes()) == receipt.get('final_message_sha256'),
        'Final message is missing or changed',
    )
    plan = load_json(campaign / 'campaign.json')
    prompt = (job / 'prompt.txt').read_bytes()
    require(sha(prompt) == receipt.get('prompt_sha256') == plan['prompt_sha256'], 'Generation prompt changed')
    snapshot = load_json(job / 'candidate-snapshot.json')
    content = {key: value for key, value in snapshot.items() if key != 'sha256'}
    require(
        sha(canonical(content)) == snapshot['sha256'] == receipt.get('candidate_sha256'), 'Candidate snapshot changed'
    )
    require(snapshot['file_count'] > 0, 'The candidate is empty')
    # Hash the preserved world again (read only; nothing is executed) to show it still matches.
    require(
        sealed.inspect_candidate(job / 'world')['sha256'] == snapshot['sha256'],
        'Preserved world differs from its snapshot',
    )
    original = bundles.validate_bundle(job / 'bundle')
    require(
        [item['path'] for item in original['manifest']['files']] == [item['path'] for item in snapshot['files']],
        'Original bundle differs from the snapshot',
    )
    return {
        'rule': RULE,
        'original_receipt': {
            'status': receipt['status'],
            'error': receipt['error'],
            'failed_phase': 'after-process',
            'receipt_sha256': sha(receipt_path.read_bytes()),
        },
        'explanation': (
            'The generation finished with one completed turn and exit code 0. Afterwards Codex had '
            'added its exact project-trust stanza to the private worker config; the harness of that '
            'time rejected any config change. The current rule accepts only that exact addition.'
        ),
        'verified': [
            'isolation canaries passed',
            'before-audit and before-launch runtime checks passed',
            'worker config equals frozen transport plus one exact trust stanza',
            'one completed turn with no error events; event and final-message hashes match',
            'prompt, candidate snapshot and re-hashed preserved world match the receipt',
            'original bundle validates and lists the same files',
        ],
        'original_bundle_sha256': original['bundle_sha256'],
        'candidate_sha256': snapshot['sha256'],
        'usage': receipt.get('usage', {}),
    }


def reconciled_bundle(job, *, cohort, label, title, output=None):
    """Write a separate reconciled bundle beside the original; returns its path."""
    job = Path(job).resolve()
    facts = verify(job)
    target = Path(output) if output else job / 'reconciled-bundle'
    require(not target.exists(), 'Reconciled bundle already exists: ' + str(target))
    staging = target.with_name('.' + target.name + '.tmp')
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(job / 'bundle', staging)
    run_path = staging / 'run.json'
    run = load_json(run_path)
    run['cohort'] = {'id': cohort['id'], 'title': cohort['title'], 'baseline': False}
    run['presentation'] = {'label': label, 'title': title}
    run['outcome'].update(
        status='completed',
        stop_reason='completed',
        partial_output=False,
        errors=['runtime-integrity-failed (reconciled offline; see reconciliation)'],
    )
    run['reconciliation'] = facts
    run_path.write_text(json.dumps(run, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    bundles.validate_bundle(staging)
    staging.replace(target)
    (job / 'reconciliation.json').write_text(
        json.dumps(dict(facts, reconciled_bundle=target.name), ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
    )
    return target


def admit(project, job, *, cohort_id, label, title):
    """Reconcile one job (if not yet done) and import its reconciled bundle into the study."""
    import atlas

    project = Path(project).resolve()
    data = atlas.validate(project)
    cohort = next((item for item in data['cohorts'] if item['id'] == cohort_id), None)
    require(cohort is not None, 'Unknown cohort: ' + cohort_id)
    target = Path(job).resolve() / 'reconciled-bundle'
    bundle = target if target.exists() else reconciled_bundle(job, cohort=cohort, label=label, title=title)
    validated = bundles.validate_bundle(bundle)
    require(
        validated['run']['reconciliation']['candidate_sha256'] == verify(job)['candidate_sha256'],
        'Reconciled bundle no longer matches its job',
    )
    run_id = validated['run']['run_id']
    if run_id in data['corpora']:
        require(
            data['corpora'][run_id]['recorded_snapshot_sha256'] == validated['bundle_sha256'],
            'An existing run has a different bundle identity',
        )
        status = 'already-admitted'
    else:
        bundles.import_bundle(bundle, project)
        status = 'admitted'
    atlas.build(project)
    return {'status': status, 'run_id': run_id, 'cohort': cohort_id, 'bundle_sha256': validated['bundle_sha256']}
