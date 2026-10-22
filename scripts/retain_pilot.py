#!/usr/bin/env python3
"""Freeze a missing-handoff legacy pilot as a retrospective, unreviewed run bundle."""

import argparse
import base64
import sys
from pathlib import Path

import sealed_run
from bundles import canonical, inspect_metadata, load_json, require, sha, validate_bundle, validate_run_record


def retain(source, output, *, run_id, cohort_id, cohort_title, label, title, provider):
    source, output = Path(source).resolve(), Path(output).resolve()
    require(source.is_dir(), 'Pilot directory is missing')
    require(not output.exists() and not output.is_relative_to(source), 'Use a new output outside the preserved pilot')
    inputs = {}
    for name in ['prepared.json', 'receipt.json', 'seed-prompt.txt', 'delivery-suffix.txt', 'prompt.txt']:
        path = source / name
        require(
            path.is_file() and not path.is_symlink() and path.stat().st_size <= 1024 * 1024,
            'Missing or unsafe pilot input: ' + name,
        )
        inputs[name] = path.read_bytes()
    prepared, receipt = load_json(source / 'prepared.json'), load_json(source / 'receipt.json')
    require(
        prepared.get('schema_version') == 1 and prepared.get('mode') == receipt.get('mode') == 'workspace-result',
        'Expected a legacy workspace-result pilot',
    )
    require(
        prepared.get('dry_run') is False and receipt.get('dry_run') is False, 'Synthetic runs are not historical pilots'
    )
    require(
        receipt.get('status') == 'incomplete'
        and receipt.get('error') == 'Workspace result file is missing'
        and receipt.get('exit_code') == 0
        and receipt.get('stopped_reason') is None,
        'This adapter only handles successful processes with a missing completion handoff',
    )
    for name in ['candidate-snapshot.json', 'result.json', 'scratch/DREAM_RESULT.json']:
        require(not (source / name).exists(), 'Existing original snapshot or handoff needs a different retention path')
    for key in ['model', 'reasoning_effort']:
        require(
            isinstance(prepared.get(key), str) and prepared[key] == receipt.get(key), 'Pilot identity differs: ' + key
        )
    require(provider in ['openrouter', 'openai'], 'Specify the original requested provider')
    require(
        prepared.get('output_schema_argument') is False and receipt.get('output_schema_argument') is False,
        'Unexpected structured-output condition',
    )
    command = prepared.get('command')
    require(isinstance(command, list) and all(isinstance(part, str) for part in command), 'Missing retained command')
    require('--output-schema' not in command, 'Unexpected structured-output command')
    seed, suffix, delivered = [inputs[name] for name in ['seed-prompt.txt', 'delivery-suffix.txt', 'prompt.txt']]
    require(delivered == seed + suffix, 'Delivered prompt is not the retained seed plus suffix')
    for key, payload in [
        ('seed_prompt_sha256', seed),
        ('delivery_suffix_sha256', suffix),
        ('delivered_prompt_sha256', delivered),
    ]:
        require(prepared.get(key) == receipt.get(key) == sha(payload), 'Pilot prompt identity differs: ' + key)
    delivered.decode('utf-8')
    world = source / 'world'
    require(world.is_dir() and not world.is_symlink(), 'Missing or unsafe candidate workspace')
    snapshot = sealed_run.inspect_candidate(world)
    captured_at = sealed_run.utc_now()
    network_denied = any('network={enabled=false}' in part for part in command)
    run = {
        'schema_version': 1,
        'run_id': run_id,
        'seed_id': 'seed-' + sha(seed)[:24],
        'prompt_sha256': sha(delivered),
        'cohort': {'id': cohort_id, 'title': cohort_title, 'baseline': True},
        'presentation': {'label': label, 'title': title},
        'outcome': {
            'status': 'partial',
            'legacy_status': 'incomplete',
            'started_at': receipt['started_at'],
            'ended_at': receipt['finished_at'],
            'stop_reason': 'legacy-handoff-missing',
            'process_exit_code': 0,
            'errors': ['Completion handoff missing; artefact completeness has not been assessed.'],
            'partial_output': True,
            'truncation': {'truncated': False, 'reason': None},
        },
        'prompt_delivery': {
            'seed_sha256': sha(seed),
            'seed_bytes': len(seed),
            'suffix_sha256': sha(suffix),
            'suffix_bytes': len(suffix),
            'note': 'The original seed was followed by a completion-handoff suffix, not the new common protocol.',
        },
        'condition': {
            'requested': {
                'model': prepared['model'],
                'provider': provider,
                'router_revision': None,
                'routing_policy': None,
            },
            'reported': {'model': None, 'provider': None},
            'harness': {
                'name': 'legacy-workspace-result',
                'version': 'unrecorded',
                'reasoning_effort': prepared['reasoning_effort'],
                'tools': [],
                'runtime': {
                    'tool_inventory': 'not recorded in the retained receipt',
                    'codex_version': None,
                    'codex_binary_sha256': None,
                    'catalog_sha256': None,
                    'ephemeral_requested': '--ephemeral' in command,
                    'ignore_user_config_requested': '--ignore-user-config' in command,
                    'mcp_disabled_requested': 'mcp_servers={}' in command,
                    'web_search_disabled_requested': 'web_search="disabled"' in command,
                    'output_schema_argument': False,
                },
            },
            'isolation': {
                'mode': 'declared Codex permissions; not independently verified at retention',
                'network': 'tool network denial requested' if network_denied else 'unrecorded',
                'audit': {'status': 'not-retained'},
            },
            'budgets': {'wall_time_seconds': None, 'input_tokens': None, 'output_tokens': None, 'cost': None},
        },
        'usage': {},
        'cost': {'amount': None, 'currency': None, 'provenance': 'Not retained in the pilot receipt'},
        'retention': {
            'kind': 'retrospective-workspace-capture',
            'captured_at': captured_at,
            'original_snapshot_sha256': None,
            'captured_snapshot_sha256': snapshot['sha256'],
            'prepared_record_sha256': sha(inputs['prepared.json']),
            'receipt_sha256': sha(inputs['receipt.json']),
            'note': 'These hashes preserve the workspace at retention time, not its unrecorded original return state.',
        },
    }
    summary = (
        'Retrospective capture of the retained pilot workspace, without executing or repairing it. '
        'The recorded process exited with code 0, but the runner marked it incomplete because the completion handoff was missing. '
        'No original return snapshot was captured; the retained files cannot be verified against that original state. '
        'The delivered prompt includes the original seed plus a handoff suffix, so this is a separate cohort. '
        'Requested model identity, declared isolation and current source bytes do not establish upstream identity, '
        'equivalent generation conditions or a semantic score. CLI version, runtime pins, usage and budgets were not retained. '
        'Raw events, private final messages, command paths and credentials are not imported.'
    )
    observations = {
        'schema_version': 1,
        'attribution': 'After the Poem host · retrospective retention',
        'summary': summary,
        'captured_at': captured_at,
        'checks': [
            {'name': 'seed and delivered prompt', 'outcome': 'matched both retained metadata records'},
            {'name': 'workspace bytes', 'outcome': 'hashed at retrospective capture'},
            {'name': 'original return snapshot', 'outcome': 'not retained'},
            {'name': 'candidate execution and semantic review', 'outcome': 'not performed'},
        ],
    }
    validate_run_record(run, sha(delivered))
    inspect_metadata(observations)
    for name, payload in inputs.items():
        require((source / name).read_bytes() == payload, 'Pilot input changed while capturing')
    require(
        sealed_run.inspect_candidate(world)['sha256'] == snapshot['sha256'], 'Pilot workspace changed while capturing'
    )
    output.mkdir(mode=0o700, parents=True)
    (output / 'candidate').mkdir()
    for entry in snapshot['files']:
        target = output / 'candidate' / entry['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(entry['content_base64'], validate=True))
        target.chmod(entry['mode'])
    (output / 'prompt.txt').write_bytes(delivered)
    manifest = {
        'schema_version': 1,
        'files': [{key: entry[key] for key in ['path', 'bytes', 'sha256']} for entry in snapshot['files']],
    }
    for name, value in [('run.json', run), ('manifest.json', manifest), ('observations.json', observations)]:
        (output / name).write_bytes(canonical(value) + b'\n')
    return validate_bundle(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--cohort-id', required=True)
    parser.add_argument('--cohort-title', required=True)
    parser.add_argument('--label', required=True)
    parser.add_argument('--title', required=True)
    parser.add_argument('--provider', choices=['openai', 'openrouter'], required=True)
    args = parser.parse_args()
    result = retain(**vars(args))
    print(
        'Retained %s: %d files; bundle %s. Not scored or imported.'
        % (result['run']['run_id'], len(result['files']), result['bundle_sha256'])
    )


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError) as error:
        print('Error: ' + str(error), file=sys.stderr)
        sys.exit(1)
