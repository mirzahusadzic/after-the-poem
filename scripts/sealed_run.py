#!/usr/bin/env python3
"""Run one isolated continuation with a post-validated workspace result file."""

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import time
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
PROMPT_HASH = '7e785c3e1b7f795a82477f449ada368399deac8b3f2232e036073d986a5a4e8d'
PROMPT_PATH = ROOT / 'corpus/prompts' / (PROMPT_HASH + '.txt')
MAX_CANDIDATE_FILES = 256
MAX_CANDIDATE_BYTES = 4 * 1024 * 1024
MAX_RESULT_BYTES = 1024 * 1024
MAX_EVENT_BYTES = 128 * 1024 * 1024
DISABLED_FEATURES = [
    'apps',
    'browser_use',
    'browser_use_external',
    'browser_use_full_cdp_access',
    'code_mode',
    'computer_use',
    'external_agent_memory_import',
    'goals',
    'hooks',
    'image_generation',
    'in_app_browser',
    'memories',
    'multi_agent',
    'multi_agent_v2',
    'plugins',
    'recommended_plugins',
    'remote_plugin',
    'skill_search',
    'standalone_web_search',
    'tool_suggest',
    'view_image',
    'workspace_dependencies',
]
RESULT_SCHEMA = {
    '$schema': 'https://json-schema.org/draft/2020-12/schema',
    'title': 'After the Poem workspace result',
    'type': 'object',
    'additionalProperties': False,
    'required': ['status', 'summary', 'unknowns'],
    'properties': {
        'status': {'type': 'string', 'enum': ['candidate', 'unresolved']},
        'summary': {'type': 'string', 'minLength': 1, 'maxLength': 8000},
        'unknowns': {
            'type': 'array',
            'maxItems': 64,
            'items': {'type': 'string', 'minLength': 1, 'maxLength': 2000},
        },
    },
}


class SealedRunError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise SealedRunError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def load_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SealedRunError('Invalid JSON in ' + path.name + ': ' + str(error)) from error


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def result_suffix():
    return (
        '\n\n---\nCompletion handoff (runner contract)\n\n'
        'After finishing the material world, write one JSON object to '
        '$TMPDIR/DREAM_RESULT.json, replacing any file already there. '
        'This path is outside the candidate world and is not part of the artefact. '
        'The object must satisfy this JSON Schema:\n\n'
        + json.dumps(RESULT_SCHEMA, ensure_ascii=False, indent=2)
        + '\n\nReturn a short human-readable completion note as your final message. '
        'Do not include the JSON object in the final message.\n'
    )


def validate_result(value):
    require(isinstance(value, dict), 'Result must be a JSON object')
    require(set(value) == {'status', 'summary', 'unknowns'}, 'Result fields do not match the contract')
    require(value['status'] in ['candidate', 'unresolved'], 'Result status is invalid')
    require(isinstance(value['summary'], str) and 1 <= len(value['summary']) <= 8000, 'Result summary is invalid')
    require(
        isinstance(value['unknowns'], list) and len(value['unknowns']) <= 64, 'Result unknowns must be a bounded list'
    )
    require(
        all(isinstance(item, str) and 1 <= len(item) <= 2000 for item in value['unknowns']),
        'Every unknown must be a non-empty bounded string',
    )
    return value


def inspect_candidate(world):
    root = world.resolve()
    files = []
    total = 0
    for path in sorted(root.rglob('*'), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        info = path.lstat()
        require(not path.is_symlink(), 'Candidate contains a symlink: ' + relative)
        if path.is_dir():
            continue
        require(path.is_file(), 'Candidate contains a non-regular file: ' + relative)
        require(not re.search(r'[\x00-\x1f\x7f]', relative), 'Candidate path contains control characters')
        require(PurePosixPath(relative).as_posix() == relative, 'Candidate path is not canonical')
        require(len(files) < MAX_CANDIDATE_FILES, 'Candidate exceeds its file limit')
        require(unicodedata.is_normalized('NFC', relative), 'Candidate path is not NFC')
        require(relative.casefold() not in {item['path'].casefold() for item in files}, 'Candidate path collision')
        require(total + info.st_size <= MAX_CANDIDATE_BYTES, 'Candidate exceeds its byte limit')
        payload = path.read_bytes()
        total += len(payload)
        require(total <= MAX_CANDIDATE_BYTES, 'Candidate exceeds its byte limit')
        files.append(
            {
                'path': relative,
                'mode': info.st_mode & 0o777,
                'bytes': len(payload),
                'sha256': sha(payload),
                'content_base64': base64.b64encode(payload).decode('ascii'),
            }
        )
    content = {
        'schema_version': 1,
        'retention': 'full-private-snapshot',
        'file_count': len(files),
        'total_bytes': total,
        'files': files,
    }
    return dict(content, sha256=sha(canonical(content)))


def child_environment(scratch):
    allowed = ['PATH', 'HOME', 'CODEX_HOME', 'LANG', 'LC_ALL', 'SSL_CERT_FILE', 'SSL_CERT_DIR']
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    environment.update({'TMPDIR': str(scratch), 'TMP': str(scratch), 'TEMP': str(scratch)})
    for key in ['HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'http_proxy', 'https_proxy', 'no_proxy']:
        if key in os.environ:
            environment[key] = os.environ[key]
    return environment


def kill_group(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=2)


def capture_process(command, cwd, environment, prompt, events_path, stderr_path, timeout, *, before_launch=None):
    started = time.monotonic()
    with events_path.open('wb') as events, stderr_path.open('wb') as stderr:
        if before_launch:
            before_launch()
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=events,
            stderr=stderr,
            start_new_session=True,
        )
        reason = None

        def send_input():
            try:
                process.stdin.write(prompt)
            except (BrokenPipeError, OSError):
                pass
            finally:
                try:
                    process.stdin.close()
                except (BrokenPipeError, OSError):
                    pass

        writer = threading.Thread(target=send_input, daemon=True)
        writer.start()
        try:
            deadline = started + timeout
            while process.poll() is None:
                if time.monotonic() > deadline:
                    reason = 'wall_time_limit'
                    kill_group(process)
                    break
                if events_path.stat().st_size + stderr_path.stat().st_size > MAX_EVENT_BYTES:
                    reason = 'output_limit'
                    kill_group(process)
                    break
                time.sleep(0.05)
        finally:
            kill_group(process)
            writer.join(timeout=2)
    return {
        'exit_code': process.returncode,
        'signal': None,
        'stopped_reason': reason,
        'elapsed_seconds': round(time.monotonic() - started, 3),
        'events_sha256': sha(events_path.read_bytes()),
        'stderr_sha256': sha(stderr_path.read_bytes()),
    }


def parse_events(path):
    events = []
    for number, line in enumerate(path.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise SealedRunError('Invalid JSONL event at line ' + str(number) + ': ' + str(error)) from error
    threads = [event['thread_id'] for event in events if event.get('type') == 'thread.started']
    require(len(threads) == 1, 'One fresh Codex thread is required')
    return events, threads[0]


def fake_worker(output_path):
    scratch = Path(os.environ['TMPDIR'])
    world = Path.cwd()
    prompt = sys.stdin.buffer.read().decode('utf-8')
    require(result_suffix() in prompt, 'Fake worker did not receive the workspace-result handoff')
    (world / 'README.md').write_text(
        '# Dry-run continuation\n\nNo model was called. This world verifies the sealed runner.\n',
        encoding='utf-8',
    )
    mode = os.environ.get('SEALED_FAKE_RESULT', 'valid')
    if mode != 'missing':
        result = {
            'status': 'candidate',
            'summary': 'Dry-run result validated after capture.',
            'unknowns': ['No model inference occurred.'],
        }
        if mode == 'invalid':
            result['status'] = 'invalid'
        (scratch / 'DREAM_RESULT.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    output_path.write_text('Dry-run completion note.\n', encoding='utf-8')
    events = [
        {'type': 'thread.started', 'thread_id': 'dry-run-' + secrets.token_hex(8)},
        {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Dry-run completion note.'}},
        {'type': 'turn.completed', 'usage': {'input_tokens': 0, 'output_tokens': 0}},
    ]
    for event in events:
        print(json.dumps(event), flush=True)


def prepare_output(output):
    output = Path(output).expanduser().resolve()
    require(not output.exists(), 'Output already exists: ' + str(output))
    output.mkdir(parents=True)
    world = output / 'world'
    scratch = output / 'scratch'
    control = output / 'control'
    world.mkdir()
    scratch.mkdir()
    control.mkdir()
    return output, world, scratch, control


def run_once(output, model, reasoning, timeout, dry_run):
    require(dry_run, 'Live sealed runs are disabled; use scripts/evaluate.py for Codex-router inference')
    output, world, scratch, control = prepare_output(output)
    started_at = utc_now()
    seed = PROMPT_PATH.read_bytes()
    require(sha(seed) == PROMPT_HASH, 'The frozen seed prompt changed')
    suffix = result_suffix().encode('utf-8')
    delivered = seed + suffix
    (output / 'seed-prompt.txt').write_bytes(seed)
    (output / 'delivery-suffix.txt').write_bytes(suffix)
    (output / 'prompt.txt').write_bytes(delivered)
    final_message = control / 'final-message.txt'
    command = [sys.executable, str(Path(__file__).resolve()), '_fake-worker', '--output', str(final_message)]
    environment = child_environment(scratch)
    if dry_run:
        environment['SEALED_FAKE_RESULT'] = os.environ.get('SEALED_FAKE_RESULT', 'valid')
    prepared = {
        'schema_version': 1,
        'mode': 'workspace-result',
        'dry_run': dry_run,
        'model': model,
        'reasoning_effort': reasoning,
        'seed_prompt_sha256': sha(seed),
        'delivery_suffix_sha256': sha(suffix),
        'delivered_prompt_sha256': sha(delivered),
        'result_schema_sha256': sha(canonical(RESULT_SCHEMA)),
        'output_schema_argument': False,
        'command': [command[0], command[1], command[2]],
    }
    write_json(output / 'prepared.json', prepared)
    process = capture_process(
        command, world, environment, delivered, output / 'events.jsonl', output / 'stderr.log', timeout
    )
    receipt = dict(
        process,
        status='incomplete',
        mode='workspace-result',
        dry_run=dry_run,
        model=model,
        reasoning_effort=reasoning,
        seed_prompt_sha256=sha(seed),
        delivery_suffix_sha256=sha(suffix),
        delivered_prompt_sha256=sha(delivered),
        output_schema_argument=False,
        started_at=started_at,
    )
    try:
        candidate = inspect_candidate(world)
        write_json(output / 'candidate-snapshot.json', candidate)
        receipt['candidate'] = {key: value for key, value in candidate.items() if key != 'files'}
    except (SealedRunError, OSError) as error:
        receipt['capture_error'] = str(error)
    try:
        require('capture_error' not in receipt, receipt.get('capture_error', 'Candidate capture failed'))
        require(process['exit_code'] == 0, 'Worker exited with code ' + str(process['exit_code']))
        require(process['stopped_reason'] is None, 'Worker stopped: ' + str(process['stopped_reason']))
        events, thread_id = parse_events(output / 'events.jsonl')
        result_path = scratch / 'DREAM_RESULT.json'
        require(result_path.is_file() and not result_path.is_symlink(), 'Workspace result file is missing')
        require(result_path.stat().st_size <= MAX_RESULT_BYTES, 'Workspace result exceeds its size limit')
        result_bytes = result_path.read_bytes()
        result = validate_result(json.loads(result_bytes.decode('utf-8')))
        (output / 'result.json').write_bytes(result_bytes)
        receipt.update(
            status='completed',
            thread_id=thread_id,
            result=result,
            result_sha256=sha(result_bytes),
            final_message_sha256=sha(final_message.read_bytes()) if final_message.is_file() else None,
        )
    except (SealedRunError, OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        receipt['error'] = str(error)
    receipt['finished_at'] = utc_now()
    write_json(output / 'receipt.json', receipt)
    require(receipt['status'] == 'completed', receipt.get('error', 'Sealed run did not complete'))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest='command', required=True)
    dry = subparsers.add_parser('dry-run', help='Exercise capture with an inert fake worker')
    dry.add_argument('--output', type=Path, required=True)
    dry.add_argument('--model', default='deepseek/deepseek-v4.1-flash-1017')
    dry.add_argument('--reasoning', default='max')
    dry.add_argument('--timeout', type=int, default=30)
    live = subparsers.add_parser('run', help='Disabled legacy live path; use scripts/evaluate.py')
    live.add_argument('--output', type=Path, required=True)
    live.add_argument('--model', default='deepseek/deepseek-v4.1-flash-1017')
    live.add_argument('--reasoning', default='max')
    live.add_argument('--timeout', type=int, default=1800)
    live.add_argument('--accept-run', action='store_true')
    fake = subparsers.add_parser('_fake-worker')
    fake.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == '_fake-worker':
        fake_worker(args.output)
        return
    if args.command == 'run':
        require(args.accept_run, 'Live inference requires --accept-run')
        raise SealedRunError('Live sealed runs are disabled; use scripts/evaluate.py for Codex-router inference')
    else:
        receipt = run_once(args.output, args.model, args.reasoning, args.timeout, dry_run=True)
    print(
        json.dumps(
            {
                'status': receipt['status'],
                'mode': receipt['mode'],
                'dry_run': receipt['dry_run'],
                'thread_id': receipt.get('thread_id'),
                'result_sha256': receipt.get('result_sha256'),
                'candidate_sha256': receipt.get('candidate', {}).get('sha256'),
                'output_schema_argument': receipt['output_schema_argument'],
            },
            indent=2,
        )
    )


if __name__ == '__main__':
    try:
        main()
    except (SealedRunError, OSError, KeyError) as error:
        print('Error: ' + str(error), file=sys.stderr)
        sys.exit(1)
