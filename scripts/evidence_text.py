"""Extract readable text from validated source citations without exposing code."""

import html
import json
import re
from html.parser import HTMLParser
from pathlib import PurePosixPath


MAX_EXCERPT = 1600
CODE_SUFFIXES = {
    '.js',
    '.mjs',
    '.cjs',
    '.ts',
    '.tsx',
    '.jsx',
    '.py',
    '.rb',
    '.zsh',
    '.sh',
    '.bash',
    '.fish',
    '.css',
    '.scss',
    '.sql',
    '.go',
    '.rs',
    '.c',
    '.h',
    '.java',
    '.php',
    '.pl',
    '.lua',
    '.swift',
    '.kt',
    '.toml',
    '.yaml',
    '.yml',
    '.ini',
    '.cfg',
}
JSON_SUFFIXES = {'.json', '.webmanifest', '.geojson'}
CODE_CHARACTERS = set('$(){}[];=<>|\\`&')
# Shell, SQL and template syntax that marks a fragment as code rather than prose.
CODE_MARKERS = re.compile(
    r'\$[({.\w]|%[-\d]*[sd]\b|\\[nt"\\]|[;{}]\s*$|^\s*[)}\]]|\b(?:SELECT|INSERT|UPDATE|DELETE|'
    r'FROM|WHERE|CREATE|JOIN|VALUES)\b|\w+\(\S*\)|=>|==|&&|\|\||::|[<>]=?\s*\d'
)
MARKDOWN_SUFFIXES = {'.md', '.markdown', '.rst'}
TEXT_SUFFIXES = {'.txt'}


class _VisibleHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items = []
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style'}:
            self.ignored += 1
        for key, value in attrs:
            if key in {'alt', 'aria-label', 'title', 'placeholder'} and value:
                self._add(value)

    def handle_endtag(self, tag):
        if tag in {'script', 'style'} and self.ignored:
            self.ignored -= 1

    def handle_data(self, data):
        if not self.ignored:
            self._add(data)

    def _add(self, value):
        text = _clean(value)
        if _readable(text) and text not in self.items:
            self.items.append(text)


def _clean(value):
    value = html.unescape(str(value))
    return re.sub(r'\s+', ' ', value).strip()


def _readable(value):
    if len(value) < 8 or not re.search(r'[A-Za-z]', value):
        return False
    words = re.findall(r'[A-Za-z][A-Za-z’\'’-]*', value)
    return len(words) >= 2 or (len(value) >= 12 and value[-1:] in '.!?')


def _finish(items, *, single_word=False):
    selected, seen = [], set()
    for item in items:
        item = _clean(item)
        key = item.casefold()
        if (_readable(item) or single_word and len(item) >= 3 and re.search(r'[A-Za-z]', item)) and key not in seen:
            seen.add(key)
            selected.append(item)
    if not selected:
        return None
    text = '\n'.join(selected)
    return text if len(text) <= MAX_EXCERPT else text[:MAX_EXCERPT].rsplit(' ', 1)[0] + '…'


def _html_text(value):
    parser = _VisibleHTML()
    parser.feed(value)
    parser.close()
    return _finish(parser.items)


def _markdown_text(value):
    lines, items, fenced = value.splitlines(), [], None
    for line in lines:
        fence = re.match(r'^\s*(```+|~~~+)', line)
        if fence:
            marker = fence.group(1)[0]
            if fenced is None:
                fenced = marker
            elif fenced == marker:
                fenced = None
            continue
        if fenced is not None:
            continue
        if re.fullmatch(r'\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*', line):
            continue
        if line.count('|') >= 2:
            cells = [_clean(cell) for cell in line.strip().strip('|').split('|')]
            items.append(' — '.join(cell for cell in cells if cell))
            continue
        line = re.sub(r'^\s{0,3}#{1,6}\s*', '', line)
        line = re.sub(r'^\s*>\s?', '', line)
        line = re.sub(r'^\s*(?:[-*+]|\d+[.)])\s+', '', line)
        line = re.sub(r'!?\[([^\]]+)\]\([^)]+\)', r'\1', line)
        line = re.sub(r'(`+)(.*?)\1', r'\2', line)
        line = re.sub(r'(\*\*|__|\*|_)(.*?)\1', r'\2', line)
        line = re.sub(r'<[^>]+>', '', line)
        line = _clean(line)
        if line:
            items.append(line)
    return _finish(items, single_word=True)


def _code_density(value):
    return sum(character in CODE_CHARACTERS for character in value) / max(len(value), 1)


def _prose(value, *, minimum_words=3):
    """True for natural-language fragments; shell, SQL, paths and identifiers are not prose."""
    value = _clean(value)
    words = re.findall(r'[A-Za-z][A-Za-z’\'-]*', value)
    letters = sum(character.isalpha() for character in value)
    tokens = value.split()
    identifiers = [token for token in tokens if re.search(r'[_/]|\.\w', token.strip('.,;:!?'))]
    return (
        len(words) >= minimum_words
        and len(value) >= 12
        and letters / len(value) >= 0.6
        and not CODE_MARKERS.search(value)
        and _code_density(re.sub(r'\{\w+\}', '', value)) < 0.04
        and len(identifiers) <= len(tokens) * 0.25
    )


def _code_text(value):
    items = []
    # Comments are usually prose; string literals are kept only when they read as sentences or labels.
    for pattern in (r'/\*([\s\S]*?)\*/', r'(?<![:\w])//([^\n]*)', r'(?m)(?:^|\s)(?:#(?!!)|--)\s+(.+)$'):
        for match in re.finditer(pattern, value):
            comment = re.sub(r'(?m)^\s*\*\s?', '', match.group(1))
            if _prose(comment, minimum_words=2):
                items.append(comment)
    string = r'(["\'`])((?:\\.|(?!\1)[^\\\n])*?)\1'
    for match in re.finditer(string, value):
        candidate = match.group(2).replace('\\"', '"').replace("\\'", "'")
        if '://' not in candidate and _prose(candidate):
            items.append(candidate)
    return _finish(items)


def _looks_like_code(value):
    lines = [line for line in value.splitlines() if line.strip()]
    if not lines:
        return False
    if lines[0].startswith('#!'):
        return True
    coded = sum(_code_density(line) >= 0.05 or bool(CODE_MARKERS.search(line)) for line in lines)
    return coded / len(lines) >= 0.3


def _json_text(value):
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return _code_text(value)
    items = []

    def visit(item):
        if isinstance(item, str):
            items.append(item)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, dict):
            for child in item.values():
                visit(child)

    visit(data)
    return _finish(items)


def extract_readable(path, value):
    """Return only prose-like text from a citation, or None when there is none."""
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in {'.html', '.htm'}:
        return _html_text(value)
    if suffix in MARKDOWN_SUFFIXES:
        return _markdown_text(value)
    if suffix in JSON_SUFFIXES:
        return _json_text(value)
    if suffix in CODE_SUFFIXES or _looks_like_code(value):
        return _code_text(value)
    if suffix in TEXT_SUFFIXES or not suffix:
        return _finish(value.splitlines())
    cleaned = _clean(value)
    return cleaned if _readable(cleaned) else None


# A world may write the path it was dreamed in into its own files. Normalized
# presentation quotes withhold that path; the preserved source keeps every byte.
HOST_PATH_TEXT = re.compile(
    r'(?<![\w./-])/(?:Users|home|private|var/folders|tmp)/[^\s"\'<>`]*?(?=[.,;:)]*(?:[\s"\'<>`]|$))'
)
WITHHELD_PATH = '[a path on the dreaming machine]'


def withhold_host_paths(value):
    """Replace host-specific filesystem paths with a stable placeholder.

    Page preparation applies this to public quotes and alternative readings so
    private machine paths never leak into normalized presentation structures
    while the frozen sources retain every byte.
    """
    return HOST_PATH_TEXT.sub(WITHHELD_PATH, str(value))


def normalize_alternatives(alternatives):
    """Preserve supported alternative readings for normalized presentation.

    Order is preserved, exact duplicates are removed, host paths are withheld,
    and entries are capped so alternative readings stay displayable without
    collapsing ambiguity to a single verdict.
    """
    seen, ordered = set(), []
    for alternative in alternatives or []:
        cleaned = withhold_host_paths(_clean(alternative))
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        ordered.append(cleaned)
        if len(ordered) >= 32:
            break
    return ordered
