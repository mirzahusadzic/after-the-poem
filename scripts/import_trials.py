#!/usr/bin/env python3
"""Project retained October trials into inert, hash-verified source records."""

import argparse
import base64
import hashlib
import json
import re
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def safe_path(name):
    path = PurePosixPath(name)
    if (
        not name
        or path.is_absolute()
        or '..' in path.parts
        or '\\' in name
        or re.search(r'[\x00-\x1f\x7f]', name)
        or str(path) != name
        or not unicodedata.is_normalized('NFC', name)
    ):
        raise ValueError('Unsafe archived path: ' + name)
    return name


def projection(entries):
    result = []
    seen = set()
    for name, data, expected in sorted(entries):
        safe_path(name)
        folded = unicodedata.normalize('NFC', name).casefold()
        if folded in seen:
            raise ValueError('Duplicate path: ' + name)
        seen.add(folded)
        actual = digest(data)
        if expected and actual != expected:
            raise ValueError('Source hash mismatch: ' + name)
        text = None
        reason = None
        if PurePosixPath(name).name == '.DS_Store':
            reason = 'Finder metadata: hash only'
        else:
            try:
                text = data.decode('utf-8')
                if '\x00' in text:
                    text, reason = None, 'Binary material: hash only'
            except UnicodeDecodeError:
                reason = 'Binary material: hash only'
        result.append(dict(path=name, bytes=len(data), sha256=actual, text=text, omission=reason))
    return result


def import_trials(source, dest):
    comparison = json.loads((source / 'dream-max-20261016/comparison.json').read_text())
    by_name = {r['name']: r for r in comparison['runs']}
    specs = [
        (
            'sol-original',
            'the-hand-remains-20260821-original',
            'Dreamy Sol',
            'The Hand Remains',
            'gpt-5.6-sol',
            'xhigh',
            '2026-08-21',
            '0.147.0',
        ),
        (
            'astra-garden',
            'dream-repeat-astra-20261016-aycn38wg',
            'Astra',
            'A garden that can be resumed',
            'gpt-6-astra',
            'xhigh',
            '2026-10-16',
            '0.162.1',
        ),
        (
            'sol56-max',
            'dream-max-20261016/sol56',
            'Sol 5.6',
            'Twenty-four connected places',
            'gpt-5.6-sol',
            'max',
            '2026-10-16',
            '0.162.1',
        ),
        (
            'sol61-max',
            'dream-max-20261016/sol61',
            'Sol 6.1',
            'The Clearing',
            'gpt-6.1-sol',
            'max',
            '2026-10-16',
            '0.162.1',
        ),
        (
            'astra-max',
            'dream-max-20261016/astra',
            'Astra',
            'A place for questions',
            'gpt-6-astra',
            'max',
            '2026-10-16',
            '0.162.1',
        ),
    ]
    runs = []
    for run_id, rel, label, title, model, effort, date, cli in specs:
        folder = source / rel
        prompt = (folder / 'prompt.txt').read_bytes()
        prompt_hash = digest(prompt)
        pp = dest / 'corpus/prompts' / (prompt_hash + '.txt')
        pp.parent.mkdir(parents=True, exist_ok=True)
        pp.write_bytes(prompt)
        original = run_id == 'sol-original'
        entries = []
        if original:
            copy = json.loads((folder / 'copy-record.json').read_text())
            archive = folder / 'candidate.zip'
            if digest(archive.read_bytes()) != copy['archive_sha256']:
                raise ValueError('Original archive differs from its copy record')
            with zipfile.ZipFile(archive) as z:
                for info in copy['files']:
                    member = 'tmp/tendora-dream-20260821-001-review/' + info['path']
                    entries.append((info['path'], z.read(member), info['sha256']))
            archive_hash = copy['archive_sha256']
            snapshot_hash = None
            elapsed, usage = None, None
        else:
            snap = json.loads((folder / 'candidate-snapshot.json').read_text())
            for f in snap['files']:
                data = base64.b64decode(f['content_base64'], validate=True)
                if len(data) != f['bytes']:
                    raise ValueError('Snapshot size mismatch')
                entries.append((f['path'], data, f['sha256']))
            if sum(len(e[1]) for e in entries) != snap['total_bytes'] or len(entries) != snap['file_count']:
                raise ValueError('Snapshot totals mismatch')
            archive = folder / ('candidate.zip' if run_id == 'astra-garden' else 'returned-candidate.zip')
            with zipfile.ZipFile(archive) as z:
                for name, data, _ in entries:
                    if z.read(name) != data:
                        raise ValueError('Return archive and snapshot disagree: ' + name)
            archive_hash = digest(archive.read_bytes())
            snapshot_hash = snap['sha256']
            if run_id == 'astra-garden':
                elapsed = json.loads((folder / 'receipt.json').read_text())['elapsed_seconds']
                usage = None
            else:
                record = by_name[folder.name]
                if archive_hash != record['returned_zip_sha256']:
                    raise ValueError('Archive differs from historical comparison')
                elapsed, usage = record['elapsed_seconds'], record['usage'][0]
        corpus = dict(
            schema_version=1,
            run_id=run_id,
            archive_sha256=archive_hash,
            recorded_snapshot_sha256=snapshot_hash,
            prompt_sha256=prompt_hash,
            files=projection(entries),
        )
        corpus_path = dest / 'corpus/runs' / (run_id + '.json')
        write_json(corpus_path, corpus)
        record = dict(
            id=run_id,
            cohort='dream-4521-v1',
            label=label,
            title=title,
            model=model,
            effort=effort,
            date=date,
            cli_version=cli,
            elapsed_seconds=elapsed,
            usage=usage,
            prompt_sha256=prompt_hash,
            corpus_sha256=digest(corpus_path.read_bytes()),
            corpus='corpus/runs/' + run_id + '.json',
            protocol='dream-4521-v1',
            condition='max-30min' if effort == 'max' else 'xhigh',
            evidence_scope='Frozen source + retained historical observations; no new candidate execution',
        )
        runs.append(record)
    write_json(
        dest / 'data/runs.json',
        dict(
            schema_version=2,
            cohorts=[
                {
                    'id': 'dream-4521-v1',
                    'title': 'The Programmer and the Glory Portal',
                    'prompt_sha256': runs[0]['prompt_sha256'],
                    'baseline': 'sol-original',
                    'default_run': 'sol56-max',
                }
            ],
            runs=runs,
        ),
    )
    print('Imported %d frozen candidates; original source trees unchanged.' % len(runs))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trials', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT)
    args = parser.parse_args()
    if (args.output / 'data/runs.json').exists():
        parser.error('Corpus already exists. Import into a new directory and review the diff.')
    import_trials(args.trials, args.output)
