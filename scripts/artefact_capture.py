#!/usr/bin/env python3
"""Register a generated browser artefact and capture its representative preview."""

import json
import os
import re
import shutil
import struct
import subprocess
from datetime import date
from pathlib import Path

import atlas
import fsutil
from bundles import load_json, require, sha


ROOT = Path(__file__).resolve().parents[1]
CAPTURE_SCRIPT = ROOT / 'scripts/capture_page.mjs'
IMAGE_LIMIT = 10 * 1024 * 1024


def _write_atomic(path, value):
    fsutil.write_json_atomic(path, value)


def _description(source_texts, entry_path, override=None):
    if override:
        return override.strip()
    readme = source_texts.get('README.md', '')
    heading = next((line.strip() for line in readme.splitlines() if line.startswith('# ')), '')
    paragraphs, current = [], []
    for line in readme.splitlines() + ['']:
        line = line.strip()
        if line and not line.startswith(('#', '```', '|', '-', '>')):
            current.append(line)
        elif current:
            paragraphs.append(' '.join(current))
            current = []
    # Skip an italic epigraph; describe the work with its first plain paragraph.
    plain = [text for text in paragraphs if not re.fullmatch(r'([*_]).*\1', text)]
    value = (plain or paragraphs or [''])[0]
    value = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', value).replace('**', '').replace('`', '').strip()
    return value or (heading[2:].strip() if heading else '') or 'Newly generated browser artefact: ' + entry_path


def _png_dimensions(path):
    payload = path.read_bytes()
    require(len(payload) <= IMAGE_LIMIT, 'Representative screenshot exceeds 10 MiB')
    require(
        payload.startswith(b'\x89PNG\r\n\x1a\n') and payload[12:16] == b'IHDR', 'Representative screenshot is not a PNG'
    )
    width, height = struct.unpack('>II', payload[16:24])
    require((width, height) == (1440, 1050), 'Representative screenshot has the wrong dimensions')
    return payload


def _default_capture(project, run_id):
    node = shutil.which('node')
    require(node is not None, 'Node.js is required to capture the representative preview')
    preview_root = project / 'dist/previews' / run_id
    output = project / 'assets/artefacts' / (run_id + '.png')
    temporary = output.with_suffix('.png.tmp.png')
    temporary.parent.mkdir(parents=True, exist_ok=True)
    command = [node, str(CAPTURE_SCRIPT), str(preview_root), 'index.html', str(temporary)]
    result = subprocess.run(command, cwd=project, capture_output=True, text=True, timeout=90)
    if result.returncode != 0:
        temporary.unlink(missing_ok=True)
        details = (result.stderr or result.stdout).strip()
        raise ValueError(
            'Representative screenshot capture failed. Install Chromium with `npm install` and '
            '`npx playwright install chromium`, then retry. ' + details
        )
    _png_dimensions(temporary)
    os.replace(temporary, output)
    return output


def ensure(project, run_id, *, description=None, capture_page=None):
    """Register and capture one generated web artefact transactionally."""
    project = Path(project).resolve()
    require(atlas.safe_id(run_id), 'Invalid artefact run ID')
    data = atlas.validate(project)
    require(run_id in data['corpora'], 'Unknown artefact run: ' + run_id)
    corpus = data['corpora'][run_id]
    sources = {source['path'] for source in corpus['files'] if source['text'] is not None}
    require('index.html' in sources, 'Generated run has no top-level index.html; no screenshot registered')
    source_texts = {source['path']: source['text'] for source in corpus['files'] if source['text'] is not None}
    artefacts_path = project / 'data/artefacts.json'
    artefacts = load_json(artefacts_path)
    entry = artefacts.get(run_id)
    require(isinstance(entry, dict), 'Generated run has no artefact registry entry')
    image_record = next(
        (item for item in entry.get('images', []) if item.get('path') == 'assets/artefacts/' + run_id + '.png'), None
    )
    if entry.get('kind') == 'browser' and entry.get('preview') == 'previews/' + run_id + '/index.html' and image_record:
        image_path = project / image_record['path']
        require(
            image_path.is_file() and sha(image_path.read_bytes()) == image_record['sha256'],
            'Representative screenshot changed',
        )
        return {'status': 'already-captured', 'run_id': run_id, 'image': image_record['path']}
    require(
        entry.get('kind') == 'none' and not entry.get('images'),
        'Existing custom artefact registration will not be overwritten',
    )
    documents = [
        source['path']
        for source in corpus['files']
        if source['text'] is not None and source['path'].lower().endswith('.md')
    ][:4]
    registered = dict(entry)
    registered.update(
        kind='browser',
        description=_description(source_texts, 'index.html', description),
        documents=documents,
        preview='previews/' + run_id + '/index.html',
        preview_note='Sandboxed browser copy. The preserved generated files remain unchanged; '
        'external network and active navigation are blocked.',
        images=[],
    )
    prior_bytes = artefacts_path.read_bytes()
    artefacts[run_id] = registered
    _write_atomic(artefacts_path, artefacts)
    try:
        atlas.build(project)
        capture = capture_page or _default_capture
        image_path = Path(capture(project, run_id)).resolve()
        require(
            image_path == (project / 'assets/artefacts' / (run_id + '.png')).resolve(),
            'Capture returned an unexpected screenshot path',
        )
        payload = _png_dimensions(image_path)
    except Exception as original:
        try:
            fsutil.write_bytes_atomic(artefacts_path, prior_bytes)
            atlas.build(project)
        except Exception as rollback_error:
            try:
                original.add_note('Artefact rollback failed: ' + str(rollback_error))
            except Exception:
                pass
            raise original from rollback_error
        raise
    today = date.today()
    registered['images'] = [
        {
            'path': 'assets/artefacts/' + run_id + '.png',
            'sha256': sha(payload),
            'caption': 'Sandboxed preview inspection · ' + str(today.day) + ' ' + today.strftime('%B %Y'),
            'provenance': 'Generated automatically from the registered disposable preview at 1440 × 1050 '
            'after the live run. This is a current presentation check, not assessment evidence.',
        }
    ]
    artefacts[run_id] = registered
    _write_atomic(artefacts_path, artefacts)
    atlas.build(project)
    return {
        'status': 'captured',
        'run_id': run_id,
        'image': registered['images'][0]['path'],
        'sha256': registered['images'][0]['sha256'],
        'preview': registered['preview'],
    }
