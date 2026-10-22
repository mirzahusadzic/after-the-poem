"""Small, inert Markdown views for retained documents and evidence excerpts.

Supports the headings, paragraphs, lists, quotes, fences, tables and inline
markup in this corpus. Raw HTML is escaped; images and active links stay text.
It deliberately does not execute or embed anything from an archived candidate.
"""

import html
import posixpath
import re
from urllib.parse import quote, unquote, urlsplit


def source_link(target, source_path, available, prefix=''):
    """Resolve only known archived documents, always to their inert views."""
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or parts.query or '\\' in target:
        return None
    path = posixpath.normpath(posixpath.join(posixpath.dirname(source_path), unquote(parts.path)))
    if not parts.path:
        path = source_path
    if path not in available:
        return None
    fragment = parts.fragment
    if fragment and not re.fullmatch(r'[A-Za-z0-9_-]+', fragment):
        return None
    return prefix + quote(path, safe='/') + '.html' + ('#' + fragment if fragment else '')


def inline(text, resolve_link=None):
    # Tokenize before escaping. No raw HTML or URL becomes markup by accident.
    pattern = re.compile(r'(`+)(.+?)\1|(!?\[([^\]\n]+)\]\(([^\s)]+)\))|\*\*(.+?)\*\*|(?<!\*)\*([^*\n]+)\*(?!\*)')
    output, end = [], 0
    for match in pattern.finditer(text):
        output.append(html.escape(text[end : match.start()]))
        if match.group(1):
            output.append('<code>' + html.escape(match.group(2)) + '</code>')
        elif match.group(3):
            label, target = match.group(4), match.group(5)
            href = resolve_link(target) if resolve_link and not match.group(3).startswith('!') else None
            if href:
                output.append('<a href="' + html.escape(href, quote=True) + '">' + html.escape(label) + '</a>')
            else:
                output.append(html.escape(match.group(3)))
        elif match.group(6):
            output.append('<strong>' + inline(match.group(6), resolve_link) + '</strong>')
        else:
            output.append('<em>' + inline(match.group(7), resolve_link) + '</em>')
        end = match.end()
    output.append(html.escape(text[end:]))
    return ''.join(output)


def render_markdown(text, *, start=1, line_ids=False, resolve_link=None):
    lines, out, index = text.splitlines(), [], 0

    def markers(first, last):
        if not line_ids:
            return ''
        return ''.join('<span class="md-line" id="L%d"></span>' % (start + n) for n in range(first, last))

    def render(value):
        return inline(value, resolve_link)

    def table_separator(value):
        cells = value.strip().strip('|').split('|')
        return len(cells) > 1 and all(re.fullmatch(r'\s*:?-{3,}:?\s*', c) for c in cells)

    def cells(value):
        return value.strip().strip('|').split('|')

    def special(n):
        value = lines[n]
        return (
            not value.strip()
            or re.match(r'^\s{0,3}(#{1,6}\s|`{3,}|~{3,}|>|[-*+]\s|\d+[.)]\s)', value)
            or re.fullmatch(r'\s*(?:-{3,}|\*{3,}|_{3,})\s*', value)
            or n + 1 < len(lines)
            and '|' in value
            and table_separator(lines[n + 1])
        )

    while index < len(lines):
        line, first = lines[index], index
        if not line.strip():
            out.append(markers(index, index + 1))
            index += 1
            continue
        fence = re.match(r'^\s{0,3}(`{3,}|~{3,})[^\n]*$', line)
        if fence:
            index += 1
            code = []
            while index < len(lines) and not re.fullmatch(
                r'\s*' + re.escape(fence[1][0]) + '{' + str(len(fence[1])) + r',}\s*', lines[index]
            ):
                code.append(markers(index, index + 1) + html.escape(lines[index]))
                index += 1
            closing = markers(index, index + 1) if index < len(lines) else ''
            out.append('<pre><code>' + markers(first, first + 1) + '\n'.join(code) + closing + '</code></pre>')
            index += index < len(lines)
            continue
        heading = re.match(r'^\s{0,3}(#{1,6})\s+(.+?)\s*#*$', line)
        if heading:
            level = len(heading[1])
            slug = re.sub(r'[^\w\s-]', '', heading[2].lower()).strip().replace(' ', '-')
            out.append(
                '<h%d id="%s">%s%s</h%d>'
                % (level, html.escape(slug, quote=True), markers(index, index + 1), render(heading[2]), level)
            )
            index += 1
            continue
        if index + 1 < len(lines) and '|' in line and table_separator(lines[index + 1]):
            headers = cells(line)
            out.append(
                '<div class="md-table"><table><thead><tr>'
                + ''.join(
                    '<th>' + (markers(index, index + 2) if n == 0 else '') + render(c.strip()) + '</th>'
                    for n, c in enumerate(headers)
                )
                + '</tr></thead><tbody>'
            )
            index += 2
            while index < len(lines) and '|' in lines[index] and lines[index].strip():
                row = cells(lines[index])
                out.append(
                    '<tr>'
                    + ''.join(
                        '<td>' + (markers(index, index + 1) if n == 0 else '') + render(c.strip()) + '</td>'
                        for n, c in enumerate(row)
                    )
                    + '</tr>'
                )
                index += 1
            out.append('</tbody></table></div>')
            continue
        if re.fullmatch(r'\s*(?:-{3,}|\*{3,}|_{3,})\s*', line):
            out.append(markers(index, index + 1) + '<hr>')
            index += 1
            continue
        item = re.match(r'^\s{0,3}([-*+]|\d+[.)])\s+(.+)$', line)
        if item:
            ordered = item[1][0].isdigit()
            tag = 'ol' if ordered else 'ul'
            attrs = ' start="%s"' % re.match(r'\d+', item[1])[0] if ordered else ''
            out.append('<' + tag + attrs + '>')
            while index < len(lines):
                item = re.match(r'^\s{0,3}([-*+]|\d+[.)])\s+(.+)$', lines[index])
                if not item or item[1][0].isdigit() != ordered:
                    break
                content = [markers(index, index + 1) + render(item[2])]
                index += 1
                while index < len(lines) and lines[index].startswith('  ') and not special(index):
                    content.append(markers(index, index + 1) + render(lines[index].strip()))
                    index += 1
                out.append('<li>' + '\n'.join(content) + '</li>')
            out.append('</' + tag + '>')
            continue
        if re.match(r'^\s{0,3}>', line):
            quoted = []
            while index < len(lines) and re.match(r'^\s{0,3}>', lines[index]):
                quoted.append(re.sub(r'^\s{0,3}> ?', '', lines[index]))
                index += 1
            out.append(
                '<blockquote>'
                + render_markdown('\n'.join(quoted), start=start + first, line_ids=line_ids, resolve_link=resolve_link)
                + '</blockquote>'
            )
            continue
        paragraph = [markers(index, index + 1) + render(line)]
        index += 1
        while index < len(lines) and not special(index):
            paragraph.append(markers(index, index + 1) + render(lines[index]))
            index += 1
        out.append('<p>' + '\n'.join(paragraph) + '</p>')
    return '\n'.join(out)
