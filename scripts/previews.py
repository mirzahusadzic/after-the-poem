"""Opt-in previews, isolated from frozen evidence and from the report origin."""

import hashlib
import hmac
import http.server
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit


BROWSER_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'none'; worker-src 'none'; object-src 'none'; "
    "base-uri 'none'; form-action 'none'; navigate-to 'none'"
)
# Server worlds serve their own pages, so no response header can be added; each HTML file in the
# disposable copy carries this policy instead. It limits only where forms may submit, so the world's
# own scripts, styles and requests are untouched.
NATIVE_POLICY = "form-action 'self'"
BROWSER_EXTENSIONS = {'.html', '.css', '.js', '.svg', '.json', '.webmanifest', '.png', '.jpg', '.jpeg', '.gif', '.webp'}
MAX_PROCESS_GROUP = 64
BROWSER_HOST = '''(function () {
  const values = new Map();
  const storage = {getItem: key => values.has(String(key)) ? values.get(String(key)) : null,
    setItem: (key,value) => values.set(String(key),String(value)), removeItem: key => values.delete(String(key)),
    clear: () => values.clear(), key: index => Array.from(values.keys())[index] || null,
    get length() { return values.size; }};
  Object.defineProperty(window, 'localStorage', {value:storage});
  Object.defineProperty(navigator, 'serviceWorker', {value:{register:() => Promise.resolve(null)}});
  const previewRoot = location.protocol === 'file:'
    ? location.pathname.slice(0, location.pathname.lastIndexOf('/') + 1)
    : null;
  function samePreview(target) {
    try {
      const url = new URL(target, location.href);
      if (url.protocol !== location.protocol) return false;
      if (location.protocol === 'file:') return decodeURIComponent(url.pathname).startsWith(decodeURIComponent(previewRoot));
      return url.origin === location.origin;
    } catch (_) { return false; }
  }
  window.addEventListener('click', function (event) {
    const anchor = event.target && event.target.closest ? event.target.closest('a[href]') : null;
    if (anchor && !samePreview(anchor.href)) { event.preventDefault(); event.stopImmediatePropagation(); }
  }, true);
  // Form submits that would leave the preview are cancelled, whether the target comes from the
  // form or from the button that submits it; form.submit() fires no submit event, so it is
  // guarded too. This is defence in depth: the enforced limit is the form-action policy
  // (BROWSER_POLICY for browser worlds, NATIVE_POLICY for server pages). Scripted navigation
  // such as location assignment is not intercepted here.
  function formTarget(form, submitter) {
    const override = submitter && submitter.getAttribute ? submitter.getAttribute('formaction') : null;
    return override || (form && form.getAttribute('action')) || location.href;
  }
  window.addEventListener('submit', function (event) {
    const form = event.target && event.target.closest ? event.target.closest('form') : null;
    if (!samePreview(formTarget(form, event.submitter))) { event.preventDefault(); event.stopImmediatePropagation(); }
  }, true);
  const submitForm = HTMLFormElement.prototype.submit;
  HTMLFormElement.prototype.submit = function () {
    if (samePreview(formTarget(this, null))) submitForm.call(this);
  };
  window.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && window.parent !== window) {
      window.parent.postMessage({type: 'after-the-poem:escape'}, '*');
    }
  });
  window.open = function () { return null; };
}());'''


def _browser_destination(folder, path):
    relative = PurePosixPath(path)
    if (
        not path
        or relative.is_absolute()
        or '..' in relative.parts
        or '\\' in path
        or str(relative) != path
        or re.search(r'[\x00-\x1f\x7f]', path)
    ):
        raise ValueError('Unsafe browser preview path: ' + path)
    destination = folder.joinpath(*relative.parts)
    if not destination.resolve().is_relative_to(folder.resolve()):
        raise ValueError('Unsafe browser preview path: ' + path)
    return destination


def _inject_browser_host(text, path):
    match = re.search(r'<head(?:\s[^>]*)?>', text, re.IGNORECASE)
    if match is None:
        raise ValueError('Browser preview HTML needs an explicit head element: ' + path)
    prefix = '../' * (len(PurePosixPath(path).parts) - 1)
    injection = (
        '<meta http-equiv="Content-Security-Policy" content="' + BROWSER_POLICY + '">'
        '<script src="' + prefix + 'preview-host.js"></script>'
    )
    return text[: match.end()] + injection + text[match.end() :]


def _inject_native_host(text, path):
    policy = '<meta http-equiv="Content-Security-Policy" content="' + NATIVE_POLICY + '">'
    tag = '<script src="__after_poem_preview_host.js"></script>'
    opening = re.search(r'<head(?:\s[^>]*)?>', text, re.IGNORECASE)
    if opening:
        # The policy goes first so it governs every form that follows; the host script keeps its place.
        text = text[: opening.end()] + policy + text[opening.end() :]
    match = re.search(r'</head\s*>', text, re.IGNORECASE)
    if match:
        return text[: match.start()] + tag + text[match.start() :]
    if opening:
        return text[: opening.end() + len(policy)] + tag + text[opening.end() + len(policy) :]
    return policy + tag + text


def browser_preview_sources(run_id, entry, corpus):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,99}', run_id):
        raise ValueError('Unsafe browser preview run ID')
    prefix = 'previews/' + run_id + '/'
    preview = entry.get('preview')
    if not isinstance(preview, str) or not preview.startswith(prefix):
        raise ValueError('Browser preview must stay inside its run directory')
    relative = preview[len(prefix) :]
    _browser_destination(Path('.'), relative)
    sources = [
        source
        for source in corpus['files']
        if source['text'] is not None and Path(source['path']).suffix.lower() in BROWSER_EXTENSIONS
    ]
    if relative not in {source['path'] for source in sources} or Path(relative).suffix.lower() != '.html':
        raise ValueError('Browser preview entry must be a retained HTML source')
    if any(source['path'].casefold() == 'preview-host.js' for source in corpus['files']):
        raise ValueError('Candidate collides with the browser preview host')
    return sources


def build_browser_previews(data, out):
    """Copy browser artefacts without running them. Execution requires a click."""
    for rid, entry in data['artefacts'].items():
        if entry['kind'] != 'browser':
            continue
        sources = browser_preview_sources(rid, entry, data['corpora'][rid])
        folder = out / 'previews' / rid
        folder.mkdir(parents=True, exist_ok=True)
        # Opaque iframes cannot use persistent browser storage or service workers.
        # Keep candidate code unchanged and supply session-only host facilities.
        (folder / 'preview-host.js').write_text(BROWSER_HOST, encoding='utf-8')
        for source in sources:
            destination = _browser_destination(folder, source['path'])
            destination.parent.mkdir(parents=True, exist_ok=True)
            text = source['text']
            if Path(source['path']).suffix.lower() == '.html':
                text = _inject_browser_host(text, source['path'])
            destination.write_text(text, encoding='utf-8')


# Server-backed artefacts: the system Ruby runs the copied world's own entry file (never a candidate
# executable) on a loopback port inside the sandbox.
SERVERS = {
    'sol56-max': ['bin/tendora.rb', 'serve', '--bind', '127.0.0.1', '--port', '{port}'],
    'dream-deepseek-20261021-deepseek-s1': ['lib/tendora_cli.rb', 'serve', '--bind', '127.0.0.1', '--port', '{port}'],
}
COMMANDS = {
    'astra-garden': {
        'inspect': (0, 0),
        'verify': (0, 0),
        'step': (0, 0),
        'propose': (1, 1),
        'rehearse': (1, 1),
        'commit': (1, 1),
        'run': (1, 1),
    },
    'astra-max': {
        'status': (0, 0),
        'verify': (0, 0),
        'log': (0, 0),
        'ask': (2, 2),
        'propose': (4, 5),
        'rehearse': (1, 1),
        'enact': (1, 1),
        'observe': (2, 2),
    },
    'dream-claude-fable51-20261022': {
        'status': (0, 0),
        'log': (0, 5),
        'show': (1, 1),
        'verify': (0, 0),
        'wake': (0, 0),
        'probe': (1, 1),
        'chisel': (1, 1),
        'boundary': (1, 1),
        'receipt': (1, 3),
        'sleep': (1, 1),
    },
    'dream-claude-opus55-v2-20261022': {
        'survey': (1, 6),
        'check': (1, 10),
        'probe': (2, 7),
        'chisel': (2, 8),
        'lean': (2, 7),
        'rehearse': (1, 12),
        'zoom': (2, 7),
        'receipts': (0, 6),
        'new': (1, 4),
    },
}
# Options that would write outside the copy's own ledger and worlds are refused.
FORBIDDEN_OPTIONS = {
    'dream-claude-opus55-v2-20261022': ('--ledger', '--dir'),
    'dream-claude-fable51-20261022': ('--world', '--actor'),
}
# The tend package runs from the copy itself; isolated mode keeps the user's site and environment out.
TEND = [
    '/usr/bin/python3',
    '-I',
    '-B',
    '-c',
    'import sys; sys.path.insert(0, ""); from tend.cli import main; sys.exit(main())',
]
# How each console world is started: the copied world's own entry file, never a candidate executable.
ZSH = ['/bin/zsh', '-f', './tendora.zsh']
CONSOLES = {
    'astra-garden': ZSH,
    'astra-max': ZSH,
    'dream-claude-opus55-v2-20261022': TEND,
    'dream-claude-fable51-20261022': [
        '/usr/bin/python3',
        '-I',
        '-B',
        '-c',
        'import sys; sys.path.insert(0, ""); from tendora.cli import main; sys.exit(main())',
    ],
}
# What a visitor types to reach the entry, accepted and dropped.
ENTRY_WORDS = {
    'astra-garden': [
        ['.', './tendora.zsh'],
        ['source', './tendora.zsh'],
        ['zsh', './tendora.zsh'],
        ['./tendora.zsh'],
        ['tendora.zsh'],
    ],
    'dream-claude-opus55-v2-20261022': [['python3', '-m', 'tend'], ['./bin/tend'], ['bin/tend'], ['tend']],
    'dream-claude-fable51-20261022': [['python3', '-m', 'tendora'], ['tendora']],
}
ENTRY_WORDS['astra-max'] = ENTRY_WORDS['astra-garden']
CONSOLE_ENVIRONMENT = {
    'dream-claude-opus55-v2-20261022': {'TEND_LEDGER': 'ledger/receipts.jsonl'},
    'dream-claude-fable51-20261022': {'TENDORA_ACTOR': 'a visitor'},
}
# Python on macOS runs from the Command Line Tools; only that tree is added to what may execute.
CONSOLE_EXECUTABLES = {
    'dream-claude-fable51-20261022': ['/Library/Developer/CommandLineTools'],
    'dream-claude-opus55-v2-20261022': ['/Library/Developer/CommandLineTools'],
}
HELP = {
    'astra-garden': 'inspect | verify | step | run N (1–20)\npropose collect/plant/harvest | rehearse ID | commit ID\nUse the proposal ID printed by propose. Start fresh resets this copy.',
    'astra-max': 'status | verify | log\nask ID "Question"\npropose PROPOSAL QUESTION STATUS "Reason" ["Evidence"]\nrehearse PROPOSAL | enact PROPOSAL\nobserve QUESTION "Observation"\nStatuses: open, active, parked, resolved. Start fresh resets this copy.',
    'dream-claude-fable51-20261022': 'status | log [--all] [-n N] [--kind KIND] | show SEQ | verify | wake\nprobe "QUESTION" | chisel "CONSTRAINT" | boundary "REFUSAL" | receipt "TEXT" [--resolves SEQ] | sleep "SUMMARY"\nEntries are signed by a visitor and stay in this copy. Beams and rehearsals are not defined from this console. Start fresh resets it.',
    'dream-claude-opus55-v2-20261022': 'survey WORLD | check WORLD… | probe WORLD "EXPR" | chisel WORLD "EXPR" [--carve]\nlean WORLD BOUNDARY | rehearse WORLD [--seed S] | zoom WORLD "EXPR" | receipts [--verify] | new NAME\nWorlds: worlds/lamp.world, jugs, peterson, handoff, broken/check-then-enter, broken/flag-then-wait.\nAny gesture takes --why "TEXT" and --json. Receipts stay in this copy. Start fresh resets it.',
}


def command_arguments(run_id, command):
    if not isinstance(command, str) or not 1 <= len(command) <= 2048 or any(ord(c) < 32 for c in command):
        raise ValueError('Enter one command, up to 2,048 characters.')
    args = shlex.split(command)
    for words in ENTRY_WORDS.get(run_id, []):
        if args[: len(words)] == words:
            args = args[len(words) :]
            break
    if args == ['help']:
        return args
    if not args or run_id not in COMMANDS or args[0] not in COMMANDS[run_id]:
        raise ValueError('This console runs the artefact’s commands. Type help for the list.')
    low, high = COMMANDS[run_id][args[0]]
    if not low <= len(args) - 1 <= high:
        raise ValueError('Unexpected arguments. Type help for the command format.')
    if any(arg.split('=', 1)[0] in FORBIDDEN_OPTIONS.get(run_id, ()) for arg in args):
        raise ValueError('This console keeps receipts and new worlds inside its own copy.')
    if run_id == 'astra-garden':
        if args[0] == 'run' and not re.fullmatch(r'(?:[1-9]|1[0-9]|20)', args[1]):
            raise ValueError('run accepts 1–20 steps.')
        if args[0] == 'propose' and args[1] not in ['collect', 'plant', 'harvest']:
            raise ValueError('Choose collect, plant or harvest.')
        if args[0] in ['rehearse', 'commit'] and not re.fullmatch(r'[0-9]{6}-(collect|plant|harvest)', args[1]):
            raise ValueError('Use a proposal ID printed by this world.')
    return args


def sandbox_profile(world, port=None, executables=()):
    # No home directory, repository, inherited credentials or external network.
    base = '''(version 1)
(deny default)
(allow process-fork signal sysctl-read)
(allow mach-lookup (global-name "com.apple.cfprefsd.agent")
 (global-name "com.apple.system.logger")
 (global-name "com.apple.system.notification_center"))
(allow process-exec (subpath "/usr/bin") (subpath "/bin") (subpath "/usr/sbin")
 (subpath "/sbin") (subpath "/System/Library"))
(allow file-read-metadata)
(allow file-read* (literal "/") (literal "/private") (literal "/private/var") (literal "/Library") (literal "/System"))
(allow file-read* (subpath "/System") (subpath "/usr") (subpath "/bin")
 (subpath "/sbin") (subpath "/Library/Apple") (subpath "/Library/Developer")
 (subpath "/private/var/db/dyld") (subpath "/dev/fd")
 (literal "/dev/null") (literal "/dev/random") (literal "/dev/urandom"))
(allow file-read* file-write* (subpath %s))
(allow file-write* (literal "/dev/null"))
''' % json.dumps(str(world.resolve()))
    for tree in executables:
        base += '(allow process-exec file-read* (subpath %s))\n' % json.dumps(tree)
    if port is not None:
        base += '(allow network-bind network-inbound network-outbound (local ip "localhost:%d"))\n' % port
    return base


def child_limits():
    """Apply conservative per-process limits before exec."""
    import resource

    def apply(which, value):
        try:
            resource.setrlimit(which, value)
        except (OSError, ValueError):
            if which != getattr(resource, 'RLIMIT_AS', None):
                raise

    apply(resource.RLIMIT_CORE, (0, 0))
    apply(resource.RLIMIT_CPU, (20, 20))
    apply(resource.RLIMIT_FSIZE, (16 * 1024 * 1024, 16 * 1024 * 1024))
    apply(resource.RLIMIT_NOFILE, (128, 128))
    if hasattr(resource, 'RLIMIT_AS'):
        apply(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))


def process_group_count(pgid):
    """Count live members of one preview process group without touching the user-wide limit."""
    try:
        result = subprocess.run(
            ['/bin/ps', '-o', 'pid=', '-g', str(int(pgid))], text=True, capture_output=True, timeout=2
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    if result.returncode != 0:
        return 0
    return len([line for line in result.stdout.splitlines() if line.strip()])


def kill_process(process):
    if process and process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


# Console output keeps newline/tab and SGR colour (ESC [ ... m) so the host prompt
# stays readable. Everything else that could drive the terminal -- OSC hyperlink
# and control strings, non-SGR CSI sequences, lone escapes and C0/C1 controls --
# is stripped before the output is returned.
_OSC_RE = re.compile(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)')
_C1_OSC_RE = re.compile(r'\x9d[^\x07\x9c]*(?:\x07|\x9c)')
_CSI_RE = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
_C1_CSI_RE = re.compile(r'\x9b[0-?]*[ -/]*[@-~]')
_SGR_RE = re.compile(r'\A\x1b\[[0-9;]*m\Z')
_LONE_ESCAPE_RE = re.compile(r'\x1b(?!\[[0-9;]*m)')
_CONTROL_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1a\x1c-\x1f\x7f\x80-\x9f]')
_UNTERMINATED_OSC_RE = re.compile(r'\x1b\][^\x07\x1b]*$')
_C1_UNTERMINATED_OSC_RE = re.compile(r'\x9d[^\x07\x9c]*$')


def _sanitize_console_output(text):
    """Remove unsafe terminal CSI/OSC sequences, keeping newline/tab and SGR colour."""
    text = _OSC_RE.sub('', text)
    text = _C1_OSC_RE.sub('', text)
    # A truncated OSC without a terminator would otherwise let the terminal
    # swallow following text; drop the dangling introducer to the end of output.
    text = _UNTERMINATED_OSC_RE.sub('', text)
    text = _C1_UNTERMINATED_OSC_RE.sub('', text)

    def _csi(match):
        return match.group(0) if _SGR_RE.match(match.group(0)) else ''

    def _c1_csi(match):
        candidate = '\x1b[' + match.group(0)[1:]
        return candidate if _SGR_RE.match(candidate) else ''

    text = _CSI_RE.sub(_csi, text)
    text = _C1_CSI_RE.sub(_c1_csi, text)
    text = _LONE_ESCAPE_RE.sub('', text)
    return _CONTROL_RE.sub('', text)


# GET /_preview/status carries the bearer token, so it needs a strong
# same-origin browser signal -- never CORS. Modern same-origin fetch sends
# `Sec-Fetch-Site: same-origin`; clients without Fetch Metadata (Safari before
# 16.4) fall back to an exact Origin/Referer match against this host, which is
# why src/previews.js asks for this one request with `referrerPolicy:
# 'same-origin'` despite the page's no-referrer default. Navigations
# (`Sec-Fetch-Site: none`), cross-site sends and bare requests with no signal
# get no token.
def _preview_status_allowed(headers, host):
    expected = 'http://' + host
    origin = headers.get('Origin')
    site = headers.get('Sec-Fetch-Site')
    if site is not None:
        if site != 'same-origin':
            return False
        return origin is None or origin == expected
    if origin is not None:
        return origin == expected
    referer = headers.get('Referer')
    if referer is not None:
        return referer == expected or referer.startswith(expected + '/')
    return False


def _preview_status_payload(manager, allowed):
    full = manager.status()
    if allowed:
        return 200, full
    redacted = {key: value for key, value in full.items() if key != 'token'}
    redacted['error'] = 'Preview status needs a same-origin request.'
    return 403, redacted


# Hardening headers for the local static server. The report is same-origin
# only, so `no-referrer` plus `nosniff` are always safe; static responses keep
# `frame-ancestors 'self'` out of the candidate preview policies defined above.
STATIC_HEADERS = (
    ('X-Content-Type-Options', 'nosniff'),
    ('Referrer-Policy', 'no-referrer'),
)


class PreviewManager:
    def __init__(self, root, enabled):
        self.root = root
        self.enabled = enabled and sys.platform == 'darwin' and Path('/usr/bin/sandbox-exec').is_file()
        self.token = secrets.token_urlsafe(32)
        self.sessions = {}
        self.lock = threading.RLock()

    def status(self):
        return {
            'enabled': self.enabled,
            'token': self.token,
            'message': 'Native previews use the local macOS sandbox. Start with npm run dev on macOS.',
        }

    def stop(self, session_id):
        with self.lock:
            session = self.sessions.pop(session_id, None)
        if session:
            with session['lock']:
                kill_process(session.get('process'))
                shutil.rmtree(session['folder'], ignore_errors=True)

    def close(self):
        for sid in list(self.sessions):
            self.stop(sid)

    def expire(self):
        with self.lock:
            stale = [sid for sid, s in self.sessions.items() if time.monotonic() - s['touched'] > 1800]
        for sid in stale:
            self.stop(sid)

    def create(self, run_id):
        if not self.enabled:
            raise ValueError(self.status()['message'])
        if run_id not in COMMANDS and run_id not in SERVERS:
            raise ValueError('Unknown native preview.')
        self.expire()
        with self.lock:
            if len(self.sessions) >= 6:
                raise ValueError('Close an existing preview before opening another.')
            corpus_bytes = (self.root / 'corpus/runs' / (run_id + '.json')).read_bytes()
            registry = json.loads((self.root / 'data/runs.json').read_text())
            expected = next(r['corpus_sha256'] for r in registry['runs'] if r['id'] == run_id)
            if hashlib.sha256(corpus_bytes).hexdigest() != expected:
                raise ValueError('Frozen corpus identity changed. Rebuild and validate before previewing.')
            corpus = json.loads(corpus_bytes)
            folder = Path(tempfile.mkdtemp(prefix='after-poem-preview-')).resolve()
            world = folder / 'world'
            control = folder / 'control'
            world.mkdir()
            control.mkdir(mode=0o700)
            session_id = secrets.token_hex(16)
            session = dict(
                run_id=run_id,
                folder=folder,
                world=world,
                control=control,
                process=None,
                touched=time.monotonic(),
                lock=threading.RLock(),
            )
            self.sessions[session_id] = session
        try:
            for source in corpus['files']:
                if source['text'] is None:
                    continue
                destination = _browser_destination(world, source['path'])
                if not destination.resolve().is_relative_to(world):
                    raise ValueError('Unsafe candidate path.')
                payload = source['text'].encode('utf-8')
                if hashlib.sha256(payload).hexdigest() != source['sha256']:
                    raise ValueError('Candidate source hash mismatch.')
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(payload)
            host_directories = set()
            for html in world.rglob('*.html'):
                if html.is_symlink() or not html.is_file():
                    continue
                try:
                    text = html.read_text(encoding='utf-8')
                except UnicodeDecodeError:
                    continue
                html.write_text(_inject_native_host(text, html.relative_to(world).as_posix()), encoding='utf-8')
                host_directories.add(html.parent)
            for directory in host_directories:
                (directory / '__after_poem_preview_host.js').write_text(BROWSER_HOST, encoding='utf-8')
            home = world / '.preview-home'
            home.mkdir()
            session['environment'] = {
                'PATH': '/usr/bin:/bin:/usr/sbin:/sbin',
                'HOME': str(home),
                'TMPDIR': str(world),
                'LANG': 'en_US.UTF-8',
                'LC_ALL': 'en_US.UTF-8',
                **CONSOLE_ENVIRONMENT.get(run_id, {}),
            }
            port = None
            if run_id in SERVERS:
                with socket.socket() as listener:
                    listener.bind(('127.0.0.1', 0))
                    port = listener.getsockname()[1]
            session['profile'] = control / 'profile.sb'
            session['profile'].write_text(sandbox_profile(world, port, CONSOLE_EXECUTABLES.get(run_id, ())))
            if port:
                log = (control / 'server.log').open('wb')
                try:
                    args = [arg.replace('{port}', str(port)) for arg in SERVERS[run_id]]
                    session['process'] = self.spawn(session, ['/usr/bin/ruby', '--disable-gems'] + args, log)
                finally:
                    log.close()
                deadline = time.monotonic() + 12
                while time.monotonic() < deadline:
                    if session['process'].poll() is not None:
                        raise ValueError(
                            'The copied world could not start: '
                            + (control / 'server.log').read_text(errors='replace')[:2000]
                        )
                    if process_group_count(session['process'].pid) > MAX_PROCESS_GROUP:
                        raise ValueError('The copied world exceeded its process limit.')
                    try:
                        with socket.create_connection(('127.0.0.1', port), timeout=0.1):
                            return {'session': session_id, 'url': 'http://127.0.0.1:%d/' % port}
                    except OSError:
                        time.sleep(0.05)
                raise ValueError('The copied world did not become ready in time.')
            return {'session': session_id}
        except Exception:
            self.stop(session_id)
            raise

    def spawn(self, session, args, output):
        command = ['/usr/bin/sandbox-exec', '-f', str(session['profile'])] + args
        return subprocess.Popen(
            command,
            cwd=session['world'],
            env=session['environment'],
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            preexec_fn=child_limits,
        )

    def execute(self, session_id, command):
        with self.lock:
            session = self.sessions.get(session_id)
        if session is None:
            raise ValueError('This preview has ended. Start a fresh one.')
        with session['lock']:
            if session['run_id'] not in COMMANDS:
                raise ValueError('This artefact uses its browser interface.')
            args = command_arguments(session['run_id'], command)
            session['touched'] = time.monotonic()
            if args == ['help']:
                return {'output': HELP[session['run_id']], 'exit_code': 0}
            output_path = session['control'] / ('output-' + secrets.token_hex(8))
            with output_path.open('w+b') as output:
                process = self.spawn(session, CONSOLES[session['run_id']] + args, output)
                session['process'] = process
                deadline = time.monotonic() + 15
                while process.poll() is None:
                    if (
                        time.monotonic() > deadline
                        or output.tell() > 65536
                        or process_group_count(process.pid) > MAX_PROCESS_GROUP
                    ):
                        kill_process(process)
                        break
                    time.sleep(0.025)
                output.seek(0)
                text = output.read(65537).decode('utf-8', errors='replace')[:65536]
            text = _sanitize_console_output(text)
            output_path.unlink(missing_ok=True)
            return {'output': text, 'exit_code': process.returncode}


def make_server(root, out, port, previews):
    """The local report server and its preview manager, bound to 127.0.0.1 but not yet serving."""
    manager = PreviewManager(root, previews)
    allowed_hosts = set()

    class Handler(http.server.SimpleHTTPRequestHandler):
        # Expected iframe sandbox attributes (set by the report frontend in
        # src/previews.js, documented here because this module owns the host
        # policy they pair with):
        #   browser artefact: "allow-scripts allow-forms allow-downloads"
        #     (opaque origin: no allow-same-origin, so the candidate cannot
        #     reach the report origin; downloads stay permitted).
        #   server artefact:  "allow-scripts allow-forms allow-same-origin allow-downloads"
        #     (the running copy is its own local origin and needs
        #     same-origin for its own resources; it is still fenced off from
        #     the report origin by the origin check in openPreview).
        BROWSER_SANDBOX = 'allow-scripts allow-forms allow-downloads'
        SERVER_SANDBOX = 'allow-scripts allow-forms allow-same-origin allow-downloads'

        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(out), **kwargs)

        def end_headers(self):
            existing = (
                b'\r\n'.join(self._headers_buffer).decode('latin-1').lower()
                if hasattr(self, '_headers_buffer')
                else ''
            )
            for name, value in STATIC_HEADERS:
                if name.lower() not in existing:
                    self.send_header(name, value)
            super().end_headers()

        def list_directory(self, path):
            # Directory listings are disabled: browsing a folder must not
            # reveal the report tree.
            self.send_error(404, 'Not found')
            return None

        def json_response(self, code, value):
            payload = json.dumps(value).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            host = self.headers.get('Host')
            if host not in allowed_hosts:
                self.send_error(403)
                return
            if urlsplit(self.path).path == '/_preview/status':
                # Same-origin fetch keeps working (Sec-Fetch-Site, or the
                # same-origin Referer src/previews.js requests); anything
                # without that signal gets the status without the bearer
                # token. No CORS involved.
                allowed = _preview_status_allowed(self.headers, host)
                code, payload = _preview_status_payload(manager, allowed)
                self.json_response(code, payload)
            else:
                super().do_GET()

        def do_POST(self):
            host = self.headers.get('Host')
            if host not in allowed_hosts or self.headers.get('Origin') != 'http://' + host:
                self.json_response(403, {'error': 'Preview requests must come from this local report.'})
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 8192:
                    raise ValueError('Invalid request size.')
                request = json.loads(self.rfile.read(size))
                token = request.get('token', '')
                if not isinstance(token, str) or not hmac.compare_digest(token, manager.token):
                    self.json_response(403, {'error': 'Refresh the report to open a preview.'})
                    return
                endpoint = urlsplit(self.path).path
                if endpoint == '/_preview/start':
                    value = manager.create(request.get('run_id'))
                elif endpoint == '/_preview/command':
                    value = manager.execute(request.get('session'), request.get('command'))
                elif endpoint == '/_preview/stop':
                    manager.stop(request.get('session'))
                    value = {'stopped': True}
                else:
                    self.json_response(404, {'error': 'Unknown preview action.'})
                    return
                self.json_response(200, value)
            except (ValueError, OSError, TypeError, AttributeError) as error:
                self.json_response(400, {'error': str(error)})

    server = http.server.ThreadingHTTPServer(('127.0.0.1', port), Handler)
    bound = server.server_address[1]
    allowed_hosts.update({'127.0.0.1:%d' % bound, 'localhost:%d' % bound})
    return server, manager


def serve(root, out, port, previews):
    server, manager = make_server(root, out, port, previews)
    with server:
        print(
            'Local: http://127.0.0.1:%d · native previews %s' % (port, 'enabled' if manager.enabled else 'disabled'),
            flush=True,
        )
        stop = threading.Event()

        def reap():
            while not stop.wait(30):
                manager.expire()

        cleanup = threading.Thread(target=reap, daemon=True)
        cleanup.start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            manager.close()
