#!/usr/bin/env python3
"""Dream a new world with Claude Code, then pack, admit and, with --read, read it.

A profile names the seed, the cohort, the model, its effort and wall time, and the
reader that reads the world afterwards. `run` gives a fresh, isolated Claude Code
session the exact seed on the operator's own login and packs what it leaves into a
Run Bundle v1. The session receives the same generation instructions as the Codex
harness; it runs with no network for its tools, no plugins, MCP servers, memory or
delegated agents, and cannot read outside its own world. Canaries check that
isolation before the dream begins. Nothing calls a model without --accept-run.

The transcript stays private under the profile's output directory; only the
world, its prompt, usage and outcome enter the bundle.
"""

import argparse
import datetime as dt
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from html.parser import HTMLParser
from pathlib import Path

import artefact_capture
import atlas
import bundles
import codex_worker as worker
import pipeline
import sealed_run as sealed
from bundles import canonical, load_json, require, sha


ROOT = Path(__file__).resolve().parents[1]
HARNESS = 'claude-code-dream-v1'
CODE_FILES = [
    'claude_dream.py',
    'dream_permissions.py',
    'codex_worker.py',
    'sealed_run.py',
    'bundles.py',
    'pipeline.py',
    'atlas.py',
]
PROFILE_FIELDS = {
    'schema_version',
    'run_id',
    'cohort',
    'prompt',
    'output',
    'model',
    'effort',
    'generation_seconds',
    'label',
    'audit_model',
    'reader',
}
# A profile may found a cohort that has no run yet; its first dream becomes the cohort's baseline.
OPTIONAL_PROFILE_FIELDS = {'cohort_title'}
READER_FIELDS = {'model', 'reasoning', 'reviewer', 'layout', 'budget'}
EFFORTS = {'low', 'medium', 'high', 'xhigh', 'max'}
# Every dreamer receives the same instructions; the Codex harness defines them.
INSTRUCTIONS = worker.GENERATION_INSTRUCTIONS
# The built-in tools the dreamer has: a shell, file reading and editing, and its own plan.
TOOLS = [
    'Bash',
    'Read',
    'Write',
    'Edit',
    'Glob',
    'Grep',
    'NotebookEdit',
    'TaskCreate',
    'TaskGet',
    'TaskList',
    'TaskUpdate',
]
# Named again as denials, so that a future default tool set cannot hand them back.
DENIED_TOOLS = [
    'WebFetch',
    'WebSearch',
    'Agent',
    'SendMessage',
    'ListAgents',
    'ToolSearch',
    'Skill',
    'CronCreate',
    'CronDelete',
    'CronList',
    'EnterWorktree',
    'ExitWorktree',
]
QUIET = {
    'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
    'DISABLE_AUTOUPDATER': '1',
    'CLAUDE_CODE_DISABLE_CLAUDE_MDS': '1',
    'CLAUDE_CODE_DISABLE_AUTO_MEMORY': '1',
    'CLAUDE_CODE_DISABLE_WEB_FETCH': '1',
}
TOKEN_ENV = 'CLAUDE_CODE_OAUTH_TOKEN'
TOKEN_FILE_ENV = 'CLAUDE_DREAM_TOKEN_FILE'


def code_identity():
    return sha(canonical({name: sha((ROOT / 'scripts' / name).read_bytes()) for name in CODE_FILES}))


def load_profile(path, project=ROOT):
    project = Path(project).resolve()
    profile = load_json(Path(path).resolve())
    require(
        isinstance(profile, dict) and PROFILE_FIELDS <= set(profile) <= PROFILE_FIELDS | OPTIONAL_PROFILE_FIELDS,
        'Claude dream profile fields must be: '
        + ', '.join(sorted(PROFILE_FIELDS))
        + ' (optionally '
        + ', '.join(sorted(OPTIONAL_PROFILE_FIELDS))
        + ')',
    )
    require(
        'cohort_title' not in profile or isinstance(profile['cohort_title'], str) and profile['cohort_title'].strip(),
        'Profile cohort_title must be non-empty',
    )
    require(profile['schema_version'] == 1, 'Unsupported Claude dream profile schema; expected 1')
    require(bundles.SAFE_ID.fullmatch(profile['run_id']), 'Invalid run_id')
    require(profile['effort'] in EFFORTS, 'Effort must be one of ' + ', '.join(sorted(EFFORTS)))
    require(
        type(profile['generation_seconds']) is int and 1 <= profile['generation_seconds'] <= 4 * 3600,
        'generation_seconds must be an integer between 1 and 14400',
    )
    for field in ['model', 'audit_model', 'label', 'cohort', 'prompt', 'output']:
        require(isinstance(profile[field], str) and profile[field].strip(), 'Profile ' + field + ' must be non-empty')
    reader = profile['reader']
    require(
        isinstance(reader, dict) and set(reader) == READER_FIELDS,
        'Reader fields must be exactly: ' + ', '.join(sorted(READER_FIELDS)),
    )
    prompt = (project / profile['prompt']).resolve()
    output = (project / profile['output']).resolve()
    require(prompt.is_file(), 'Prompt file does not exist')
    require(project / '.local' in output.parents, 'The output must stay under .local/, outside the published tree')
    return dict(profile, prompt=prompt, output=output, project=project)


def cohort_of(profile):
    data = atlas.validate(profile['project'])
    cohort = next((item for item in data['cohorts'] if item['id'] == profile['cohort']), None)
    if cohort is None and 'cohort_title' in profile:
        require(bundles.SAFE_ID.fullmatch(profile['cohort']), 'Invalid cohort id')
        prompt_sha256 = sha(profile['prompt'].read_bytes())
        require(
            all(item['prompt_sha256'] != prompt_sha256 for item in data['cohorts']),
            'This prompt already belongs to a registered cohort',
        )
        return {'id': profile['cohort'], 'title': profile['cohort_title'], 'prompt_sha256': prompt_sha256, 'new': True}
    require(cohort is not None, 'Unknown cohort: ' + profile['cohort'])
    require('cohort_title' not in profile, 'The cohort is registered; drop cohort_title from the profile')
    require(cohort['prompt_sha256'] == sha(profile['prompt'].read_bytes()), 'The prompt is not the cohort prompt')
    return cohort


# ── The session ───────────────────────────────────────────────────────────────
def claude_binary():
    found = shutil.which('claude')
    require(found is not None, 'Install Claude Code first (the `claude` command)')
    return str(Path(found).resolve(strict=True))


def claude_version(binary):
    result = subprocess.run(
        [binary, '--version'],
        capture_output=True,
        text=True,
        timeout=30,
        env={'PATH': os.environ.get('PATH', ''), 'HOME': tempfile.gettempdir()},
    )
    require(result.returncode == 0 and result.stdout.strip(), 'Could not read the Claude Code version')
    return result.stdout.strip()


def credentials(max_usd):
    """Return the child's credential variables: a subscription token, or an API key with a dollar cap."""
    token = os.environ.get(TOKEN_ENV)
    if not token and os.environ.get(TOKEN_FILE_ENV):
        path = Path(os.environ[TOKEN_FILE_ENV]).expanduser()
        require(path.is_file(), TOKEN_FILE_ENV + ' does not name a file')
        require(path.stat().st_mode & 0o077 == 0, 'The token file must be private (chmod 600)')
        token = path.read_text(encoding='utf-8').strip()
    if token:
        return {TOKEN_ENV: token}, 'subscription'
    key = os.environ.get('ANTHROPIC_API_KEY')
    require(key, 'Set ' + TOKEN_ENV + ' or ' + TOKEN_FILE_ENV + ' (from `claude setup-token`), or ANTHROPIC_API_KEY')
    require(max_usd is not None, 'An API key bills per token: pass --max-usd')
    return {'ANTHROPIC_API_KEY': key}, 'api-key'


def settings(world, scratch):
    """Isolation for the session. Bash runs in the sandbox (writes only in the world and scratch, no reads of
    the operator's home, no network); the file tools are held to the world by permissions. Whatever would
    otherwise ask is answered by the fixed policy of dream_permissions.py."""
    inside = '/' + str(world) + '/**'
    return {
        'permissions': {
            'allow': [
                'Bash',
                'Read(' + inside + ')',
                'Edit(' + inside + ')',
                'Write(' + inside + ')',
                'NotebookEdit(' + inside + ')',
                'Glob',
                'Grep',
                'TaskCreate',
                'TaskGet',
                'TaskList',
                'TaskUpdate',
            ],
            'deny': list(DENIED_TOOLS),
            'blockReadsOutsideWorkingDirectories': True,
            'disableBypassPermissionsMode': 'disable',
        },
        'sandbox': {
            'enabled': True,
            'failIfUnavailable': True,
            'autoAllowBashIfSandboxed': True,
            'allowUnsandboxedCommands': False,
            'filesystem': {'allowWrite': [str(world), str(scratch)], 'denyRead': [str(Path.home())]},
            'network': {'allowedDomains': [], 'strictAllowlist': True},
        },
        'autoMemoryEnabled': False,
        'includeCoAuthoredBy': False,
        'pluginConfigs': {'cc-plugin-agents-md@builtin': {'options': {'instructionFiles': 'managed-only'}}},
        'env': dict(QUIET),
    }


def environment(place, secret):
    """A bare child environment: a fresh home and configuration, no inherited keys or proxies."""
    keep = {key: os.environ[key] for key in ['LANG', 'LC_ALL', 'SSL_CERT_FILE', 'SSL_CERT_DIR'] if key in os.environ}
    paths = [
        str(Path(claude_binary()).parent),
        '/usr/local/bin',
        '/opt/homebrew/bin',
        '/usr/bin',
        '/bin',
        '/usr/sbin',
        '/sbin',
    ]
    return dict(
        keep,
        PATH=':'.join(dict.fromkeys(paths)),
        HOME=str(place['home']),
        CLAUDE_CONFIG_DIR=str(place['config']),
        TMPDIR=str(place['scratch']),
        **QUIET,
        **secret,
    )


def command(binary, place, prompt, model, effort, max_usd, decisions):
    """The prompt is the argument, verbatim; the instructions every dreamer receives are appended to the system
    prompt. Permission prompts go to the fixed policy, never to a person and never to a blanket refusal: a refusal
    worded as if the shell were off would change what the dreamer believes it has."""
    policy = {
        'mcpServers': {
            'dream': {
                'command': sys.executable,
                'args': [str(ROOT / 'scripts' / 'dream_permissions.py'), str(place['world']), str(decisions)],
            }
        }
    }
    args = [
        binary,
        '-p',
        prompt,
        '--output-format',
        'stream-json',
        '--verbose',
        '--model',
        model,
        '--effort',
        effort,
        '--permission-mode',
        'default',
        '--settings',
        str(place['control'] / 'settings.json'),
        '--permission-prompt-tool',
        'mcp__dream__decide',
        '--permission-prompts',
        'host',
        '--setting-sources',
        'user',
        '--strict-mcp-config',
        '--mcp-config',
        json.dumps(policy),
        '--disable-slash-commands',
        '--tools',
        ','.join(TOOLS),
        '--append-system-prompt',
        INSTRUCTIONS,
        '--no-session-persistence',
        '--disallowedTools',
        *DENIED_TOOLS,
    ]
    if max_usd is not None:
        args += ['--max-budget-usd', str(max_usd)]
    return args


def prepare(output):
    """The session's places: the world and scratch live outside the home directory it may not read."""
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    place = {'output': output, 'control': output / 'control', 'home': output / 'home', 'config': output / 'config'}
    for key in ['control', 'home', 'config']:
        place[key].mkdir(mode=0o700, exist_ok=True)
    root = Path(
        tempfile.mkdtemp(prefix='after-the-poem-dream-', dir='/private/tmp' if Path('/private/tmp').is_dir() else None)
    )
    place['root'], place['world'], place['scratch'] = root, root / 'world', root / 'scratch'
    for key in ['world', 'scratch']:
        place[key].mkdir(mode=0o700)
    worker.write_json(place['control'] / 'settings.json', settings(place['world'], place['scratch']))
    return place


def launch(args, *, cwd, env, transcript, seconds):
    """Run one headless session; at the wall time it is interrupted (letting the turn end), then stopped."""
    with transcript.open('wb') as out:
        process = subprocess.Popen(
            args,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            return process.wait(timeout=seconds), False
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGINT)
                process.wait(timeout=15)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                sealed.kill_group(process)
            return None, True
        except KeyboardInterrupt:
            # The session runs in its own process group, so Ctrl-C does not reach it: stop it before the world goes.
            sealed.kill_group(process)
            raise


def events(transcript):
    found = []
    for line in transcript.read_text(encoding='utf-8', errors='replace').splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            found.append(item)
    return found


def tool_results(found):
    """Tool calls and their results, in order: [(name, input, result text)]."""
    calls, results = {}, []
    for item in found:
        message = item.get('message')
        content = message.get('content') if isinstance(message, dict) else None
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'tool_use':
                calls[block.get('id')] = (block.get('name'), block.get('input') or {})
            elif block.get('type') == 'tool_result' and block.get('tool_use_id') in calls:
                content = block.get('content')
                text = (
                    content
                    if isinstance(content, str)
                    else ''.join(part.get('text', '') for part in content or [] if isinstance(part, dict))
                )
                name, given = calls[block['tool_use_id']]
                results.append((name, given, text, bool(block.get('is_error'))))
    return results


# ── Canaries ──────────────────────────────────────────────────────────────────
AUDIT_PROMPT = '''This is an isolation check, not a task. Use only the tools named. Run each step once,
exactly as written, report nothing else, and stop after the last step.
1. Bash: cat {canary}
2. Read the file {canary} with the Read tool.
3. Bash: cat audit-readable.txt
4. Bash: printf audit-write > audit-write.txt && cat audit-write.txt
5. Bash: /usr/bin/curl --noproxy '*' --connect-timeout 1 --max-time 2 http://127.0.0.1:{port}
'''


def audit(binary, place, model, env):
    """Ask a short session on the same settings to try what the dream must not do, and check the outcome."""
    canary = place['control'] / 'read-canary.txt'
    secret = secrets.token_hex(32)
    canary.write_text(secret, encoding='utf-8')
    (place['world'] / 'audit-readable.txt').write_text('audit-readable\n', encoding='utf-8')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        port = listener.getsockname()[1]
        transcript = place['control'] / 'audit.jsonl'
        code, timed_out = launch(
            command(
                binary,
                place,
                AUDIT_PROMPT.format(canary=canary, port=port),
                model,
                'low',
                None,
                place['control'] / 'audit-decisions.jsonl',
            ),
            cwd=place['world'],
            env=env,
            transcript=transcript,
            seconds=300,
        )
        listener.settimeout(0.05)
        try:
            connection, _ = listener.accept()
            connection.close()
            reached = True
        except socket.timeout:
            reached = False
    results = tool_results(events(transcript))
    text = transcript.read_text(encoding='utf-8', errors='replace')
    bash = [(given.get('command', ''), output, error) for name, given, output, error in results if name == 'Bash']
    reads = [(given.get('file_path', ''), output, error) for name, given, output, error in results if name == 'Read']
    written = (place['world'] / 'audit-write.txt').is_file()
    checks = {
        'host_read': secret not in text,
        'read_tool': any(path == str(canary) for path, _, _ in reads) and secret not in text,
        'workspace_read': any(
            'audit-readable.txt' in cmd and 'audit-readable' in out and not error for cmd, out, error in bash
        ),
        'workspace_write': written,
        'network': not reached and any('curl' in cmd for cmd, _, _ in bash),
    }
    for path in ['audit-readable.txt', 'audit-write.txt']:
        (place['world'] / path).unlink(missing_ok=True)
    record = {
        'status': 'passed' if all(checks.values()) and not timed_out and code == 0 else 'failed',
        'model': model,
        'checks': checks,
        'transcript_sha256': sha(transcript.read_bytes()),
        'protected_content_sha256': sha(secret.encode()),
    }
    worker.write_json(place['control'] / 'audit.json', record)
    require(
        record['status'] == 'passed',
        'Isolation canaries failed: '
        + ', '.join(k for k, v in checks.items() if not v)
        + ('' if code == 0 else ' (audit session exit ' + str(code) + ')'),
    )
    return record


# ── The world's name ──────────────────────────────────────────────────────────
class TitleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inside, self.title = False, ''

    def handle_starttag(self, tag, attrs):
        self.inside = tag == 'title'

    def handle_endtag(self, tag):
        self.inside = False if tag == 'title' else self.inside

    def handle_data(self, data):
        if self.inside:
            self.title += data


def world_title(world):
    """The world's own name: its README or WORLD heading, else its page title."""
    for name in ['README.md', 'WORLD.md']:
        path = world / name
        if path.is_file():
            for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
                if line.startswith('# '):
                    return re.sub(r'[*_`]', '', line[2:]).strip()[:120] or None
    path = world / 'index.html'
    if path.is_file():
        parser = TitleParser()
        parser.feed(path.read_text(encoding='utf-8', errors='replace'))
        return ' '.join(parser.title.split())[:120] or None
    return None


# ── The bundle ────────────────────────────────────────────────────────────────
def receipt_of(found, code, timed_out, started, ended):
    init = next((item for item in found if item.get('type') == 'system' and item.get('subtype') == 'init'), {})
    result = next((item for item in reversed(found) if item.get('type') == 'result'), {})
    usage = result.get('usage') or {}
    fresh, read, written = (
        usage.get(key) for key in ['input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens']
    )
    total = sum(value or 0 for value in [fresh, read, written]) if usage else None
    if timed_out:
        status, reason = 'timed_out', 'wall time'
    elif code == 0 and result.get('subtype') == 'success' and not result.get('is_error'):
        status, reason = 'completed', None
    else:
        status, reason = 'failed', result.get('subtype') or ('exit ' + str(code))
    return {
        'status': status,
        'stop_reason': reason or status,
        'started_at': started,
        'ended_at': ended,
        'reported_model': init.get('model'),
        'tools': sorted(init.get('tools') or []),
        'turns': result.get('num_turns'),
        'estimated_cost_usd': result.get('total_cost_usd'),
        'usage': {
            'input_tokens': total,
            'cached_input_tokens': read,
            'cache_write_input_tokens': written,
            'output_tokens': usage.get('output_tokens'),
            'reasoning_output_tokens': None,
        },
    }


def write_bundle(profile, cohort, place, receipt, audit_record, runtime, auth, title):
    output = place['output']
    snapshot = sealed.inspect_candidate(output / 'world')
    bundle = output / ('bundle-staging-' + secrets.token_hex(8))
    bundle.mkdir()
    candidate = bundle / 'candidate'
    candidate.mkdir()
    manifest = []
    for source in snapshot['files']:
        relative = bundles.safe_path(source['path'])
        target = candidate / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((output / 'world' / relative).read_bytes())
        target.chmod(source['mode'] & 0o777)
        manifest.append({key: source[key] for key in ['path', 'bytes', 'sha256']})
    (bundle / 'prompt.txt').write_bytes(profile['prompt'].read_bytes())
    worker.write_json(bundle / 'manifest.json', {'schema_version': 1, 'files': manifest})
    status = receipt['status']
    if status == 'completed' and not manifest:
        status = 'partial'
    estimated = receipt['estimated_cost_usd']
    record = {
        'schema_version': 1,
        'run_id': profile['run_id'],
        'seed_id': 'seed-' + cohort['prompt_sha256'][:24],
        'prompt_sha256': cohort['prompt_sha256'],
        'cohort': {'id': cohort['id'], 'title': cohort['title'], 'baseline': bool(cohort.get('new'))},
        'presentation': {'label': profile['label'], 'title': title},
        'outcome': {
            'status': status,
            'started_at': receipt['started_at'],
            'ended_at': receipt['ended_at'],
            'stop_reason': receipt['stop_reason'],
            'errors': [] if status in ['completed', 'partial'] else [receipt['stop_reason']],
            'partial_output': status != 'completed',
            'truncation': {
                'truncated': status == 'timed_out',
                'reason': 'wall time' if status == 'timed_out' else None,
            },
        },
        'condition': {
            'requested': {
                'model': profile['model'],
                'provider': 'anthropic',
                'router_revision': None,
                'routing_policy': None,
            },
            'reported': {
                'model': receipt['reported_model'],
                'provider': 'anthropic' if receipt['reported_model'] else None,
            },
            'harness': {
                'name': HARNESS,
                'version': runtime['claude_version'],
                'reasoning_effort': profile['effort'],
                'tools': [
                    'local shell (sandboxed) and file editing inside the world; no network, web, plugins, MCP, memory or delegated agents'
                ],
                'runtime': runtime,
            },
            'isolation': {'mode': 'claude-code-sandbox', 'network': 'denied for tools', 'audit': audit_record},
            'budgets': {
                'wall_time_seconds': profile['generation_seconds'],
                'input_tokens': None,
                'output_tokens': None,
                'cost': None if auth == 'subscription' else {'amount': runtime['max_usd'], 'currency': 'USD'},
            },
        },
        'usage': receipt['usage'],
        'cost': {
            'amount': None if auth == 'subscription' else estimated,
            'currency': None if auth == 'subscription' else 'USD',
            'provenance': (
                'Claude subscription; no per-token billing. Claude Code estimated $%.2f at API rates.' % estimated
                if auth == 'subscription' and estimated is not None
                else 'Claude subscription; no per-token billing'
                if auth == 'subscription'
                else 'Claude Code estimate at API rates; provider billing is authoritative'
            ),
        },
    }
    worker.write_json(bundle / 'run.json', record)
    worker.write_json(
        bundle / 'observations.json',
        {
            'schema_version': 1,
            'attribution': 'After the Poem host capture',
            'summary': 'Host captured the world without executing the retained candidate. No semantic assessment or preview observation.',
            'checks': [
                {'name': 'workspace capture', 'outcome': 'hashed'},
                {'name': 'sandbox canaries', 'outcome': audit_record['status']},
            ],
        },
    )
    bundles.validate_bundle(bundle)
    if (output / 'bundle').exists():
        shutil.rmtree(output / 'bundle')
    bundle.rename(output / 'bundle')
    return output / 'bundle'


# ── The passage ───────────────────────────────────────────────────────────────
def now():
    return dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00', 'Z')


def status_of(output):
    return load_json(output / 'bundle' / 'run.json')['outcome']['status'] if (output / 'bundle').is_dir() else None


def set_aside(output):
    """Keep an unfinished attempt beside the run, numbered; it is never admitted."""
    number = 1
    while output.with_name(output.name + '.attempt-' + str(number)).exists():
        number += 1
    output.rename(output.with_name(output.name + '.attempt-' + str(number)))


def dream(profile, *, max_usd=None, title=None, retry=False):
    """Audit, dream and pack. A completed dream is reused; an unfinished one is set aside only with retry."""
    output = profile['output']
    cohort = cohort_of(profile)
    status = status_of(output)
    if status == 'completed':
        return output / 'bundle'
    if status or (output / 'world').exists() or (output / 'receipt.json').exists():
        require(
            retry,
            'The previous attempt ended ' + (status or 'unfinished') + '; pass --retry to set it aside and dream again',
        )
        set_aside(output)
    binary = claude_binary()
    secret, auth = credentials(max_usd)
    place = prepare(output)
    env = environment(place, secret)
    try:
        runtime = {
            'claude_version': claude_version(binary),
            'claude_binary_sha256': sha(Path(binary).read_bytes()),
            'auth': auth,
            'max_usd': max_usd,
            'protocol_code_sha256': code_identity(),
            'instructions_sha256': sha(INSTRUCTIONS.encode()),
            'settings_sha256': sha((place['control'] / 'settings.json').read_bytes()),
            'tools': TOOLS,
            'denied_tools': DENIED_TOOLS,
            'os': os.uname().sysname,
            'architecture': os.uname().machine,
        }
        audit_record = audit(binary, place, profile['audit_model'], env)
        started = now()
        prompt = profile['prompt'].read_text(encoding='utf-8')
        code, timed_out = launch(
            command(binary, place, prompt, profile['model'], profile['effort'], max_usd, output / 'decisions.jsonl'),
            cwd=place['world'],
            env=env,
            transcript=output / 'transcript.jsonl',
            seconds=profile['generation_seconds'],
        )
        ended = now()
        shutil.move(str(place['world']), str(output / 'world'))
    finally:
        shutil.rmtree(place['root'], ignore_errors=True)
    guard(output, next(iter(secret.values())))
    found = events(output / 'transcript.jsonl')
    receipt = receipt_of(found, code, timed_out, started, ended)
    receipt.update(runtime=runtime, audit=audit_record)
    worker.write_json(output / 'receipt.json', receipt)
    return pack(profile, title=title)


def guard(output, credential):
    """The session's shell sees its credential. The network is closed, so the world is the only way out:
    a world that holds the credential is never packed, and the private transcripts are redacted."""
    marker = credential.encode()
    for name in ['transcript.jsonl', 'control/audit.jsonl']:
        path = output / name
        if path.is_file() and marker in path.read_bytes():
            path.write_bytes(path.read_bytes().replace(marker, b'[credential]'))
    leaked = [
        path.relative_to(output / 'world').as_posix()
        for path in (output / 'world').rglob('*')
        if path.is_file() and not path.is_symlink() and marker in path.read_bytes()
    ]
    if leaked:
        (output / 'world-holds-credential').write_text('\n'.join(leaked) + '\n', encoding='utf-8')
    require(not leaked, 'The world holds the session credential; it will not be packed. Revoke the token.')
    require(not (output / 'world-holds-credential').exists(), 'This world once held the session credential')


def pack(profile, *, title=None):
    """(Re)build the bundle from a finished dream; refuses once the run is admitted."""
    output = profile['output']
    cohort = cohort_of(profile)
    require(profile['run_id'] not in atlas.validate(profile['project'])['corpora'], 'The run is already admitted')
    require(not (output / 'world-holds-credential').exists(), 'This world once held the session credential')
    receipt = load_json(output / 'receipt.json')
    name = title or world_title(output / 'world') or 'Untitled world'
    return write_bundle(
        profile,
        cohort,
        {'output': output},
        receipt,
        receipt['audit'],
        receipt['runtime'],
        receipt['runtime']['auth'],
        name,
    )


def admit(profile):
    """Import a completed dream; a browser world also gets its sandboxed preview and screenshot, as every dream does."""
    require(
        status_of(profile['output']) == 'completed',
        'Only a completed dream is admitted; unfinished attempts stay in .local',
    )
    pipeline.ensure_import(profile['output'] / 'bundle', profile['project'])
    if (profile['output'] / 'bundle' / 'candidate' / 'index.html').is_file():
        artefact_capture.ensure(profile['project'], profile['run_id'])
    atlas.build(profile['project'])
    return profile['run_id']


def reading_profile(profile, title):
    reader, run_id = profile['reader'], profile['run_id']
    # The release reading: prompt version 5 in three independent sessions, the level at least two agree on.
    value = {
        'schema_version': 2,
        'run_id': run_id,
        'model': reader['model'],
        'reasoning': reader['reasoning'],
        'evaluation_id': run_id + '-muse-v5-1',
        'review_id': run_id + '-muse-v5-1-review',
        'reviewer': reader['reviewer'],
        'output': '.local/evaluation-runs/' + run_id + '-muse-v5-1',
        'repetitions': 3,
        'budget': reader['budget'],
        'layout': reader['layout'],
        'run_label': 'Muse v5 reading',
        'run_title': title,
    }
    path = profile['output'] / 'reading-profile.json'
    worker.write_json(path, value)
    return path


def read(profile, extra):
    title = load_json(profile['output'] / 'bundle' / 'run.json')['presentation']['title']
    path = reading_profile(profile, title)
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / 'scripts' / 'evaluate.py'),
            'run-profile',
            str(path),
            '--project',
            str(profile['project']),
            '--accept-run',
            *extra,
        ]
    )
    require(result.returncode == 0, 'The reading did not complete')


EPILOG = """examples:
  # Check the profile, the cohort prompt, Claude Code and credentials
  python3 scripts/claude_dream.py check examples/dream-profile-claude-v2.json
  # Dream, pack and admit (calls models)
  python3 scripts/claude_dream.py run examples/dream-profile-claude-v2.json --accept-run
  # Dream, pack, admit and read the world with the profile's reader
  python3 scripts/claude_dream.py run examples/dream-profile-claude-v2.json --accept-run --read

environment:
  CLAUDE_CODE_OAUTH_TOKEN   subscription token from `claude setup-token`
  CLAUDE_DREAM_TOKEN_FILE   private file (chmod 600) that holds that token
  ANTHROPIC_API_KEY         used when no token is set; it bills per token, so
                            run then needs --max-usd

Budget flags passed with --read-arg replace the reader's whole budget. Rerunning
run reuses a finished dream; an unfinished one needs --retry.
Reference: docs/commands.md
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__, epilog=EPILOG, formatter_class=atlas.HelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True, metavar='COMMAND')

    def command(name, summary):
        return commands.add_parser(name, help=summary, description=summary + '.', formatter_class=atlas.HelpFormatter)

    check = command('check', 'Check the profile, the cohort prompt, Claude Code and credentials; calls no model')
    run = command('run', 'Audit the sandbox, dream, pack and admit; with --read, also read the world')
    repack = command('pack', 'Rebuild the bundle of a finished dream that is not yet admitted; calls no model')
    for sub in [check, run, repack]:
        sub.add_argument('profile', type=Path, help='Claude dream profile JSON')
        sub.add_argument(
            '--project', type=Path, default=ROOT, metavar='DIR', help='Study directory (default: this repository)'
        )
    run.add_argument(
        '--accept-run', action='store_true', help='Allow model calls, which cost money; run refuses without it'
    )
    run.add_argument(
        '--max-usd',
        type=float,
        metavar='USD',
        help='Spending cap passed to Claude Code; required with ANTHROPIC_API_KEY, which bills per token',
    )
    run.add_argument('--title', metavar='TEXT', help="World's name when its own files give none")
    run.add_argument('--retry', action='store_true', help='Set an unfinished previous attempt aside and dream again')
    run.add_argument('--no-admit', action='store_true', help='Stop after packing the bundle')
    run.add_argument(
        '--read',
        action='store_true',
        help="Read the admitted world with the profile's reader (evaluate.py run-profile)",
    )
    run.add_argument(
        '--read-arg',
        action='append',
        default=[],
        metavar='ARG',
        help='Pass one argument to the reading (evaluate.py run-profile); repeat for more, each '
        'in the = form: --read-arg=--flag=value',
    )
    repack.add_argument('--title', metavar='TEXT', help="World's name when its own files give none")
    args = parser.parse_args()
    profile = load_profile(args.profile, args.project)
    if args.command == 'check':
        cohort = cohort_of(profile)
        version = claude_version(claude_binary())
        _, auth = (
            credentials(None) if os.environ.get(TOKEN_ENV) or os.environ.get(TOKEN_FILE_ENV) else (None, 'missing')
        )
        print(
            'Profile %s: cohort %s (%s), %s at %s effort, %d s; Claude Code %s; credentials: %s.'
            % (
                profile['run_id'],
                cohort['id'],
                cohort['prompt_sha256'][:12],
                profile['model'],
                profile['effort'],
                profile['generation_seconds'],
                version,
                auth,
            )
        )
        return
    if args.command == 'pack':
        print('Packed ' + str(pack(profile, title=args.title)))
        return
    require(args.accept_run, 'A dream calls models: pass --accept-run')
    bundle = dream(profile, max_usd=args.max_usd, title=args.title, retry=args.retry)
    print('Packed ' + str(bundle))
    status = status_of(profile['output'])
    require(
        status == 'completed',
        'The dream ended '
        + str(status)
        + '. The attempt is preserved in '
        + str(profile['output'])
        + ' and not admitted; pass --retry to set it aside and dream again.',
    )
    if args.no_admit:
        return
    print('Admitted ' + admit(profile))
    if args.read:
        read(profile, args.read_arg)
        print('Read ' + profile['run_id'])


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print(
            'Interrupted. A Claude Code session that had already started may still be running; '
            'check before you run again.',
            file=sys.stderr,
        )
        sys.exit(130)
    except (bundles.BundleError, OSError, KeyError, TypeError, ValueError) as error:
        print('Error: ' + str(error), file=sys.stderr)
        sys.exit(1)
