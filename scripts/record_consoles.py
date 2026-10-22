#!/usr/bin/env python3
"""Record the console worlds' answers for the static site, where nothing can run.

The static site (GitHub Pages) has no runner, so its console worlds replay a recording instead.
Recording starts each console world shown on the release page in the isolated local runner on macOS
(a disposable copy, as `npm run dev` would), plays `help`, its opening command and its shortcuts once
in that order, and writes the exact answers to data/recordings/RUN.json with the date and the world's
corpus identity. A recording is presentation, never assessment evidence, and the build only reads it:
import, analysis and build never execute a candidate.
"""

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import atlas
import bundles
import previews
from bundles import load_json, require


ROOT = Path(__file__).resolve().parents[1]
COPY = '[the disposable copy]'


def shown_consoles(project):
    presentation = load_json(project / 'data/presentation.json')
    artefacts = load_json(project / 'data/artefacts.json')
    return [
        run_id
        for run_id in presentation['shown_runs']
        if artefacts.get(run_id, {}).get('kind') == 'console' and run_id in previews.COMMANDS
    ]


def commands_for(entry):
    ordered = ['help', entry['initial_command']] + entry['shortcuts']
    return list(dict.fromkeys(command for command in ordered if command))


def record(project, run_id, today):
    entry = load_json(project / 'data/artefacts.json')[run_id]
    run = next(item for item in load_json(project / 'data/runs.json')['runs'] if item['id'] == run_id)
    manager = previews.PreviewManager(project, True)
    entries = []
    try:
        session = manager.create(run_id)['session']
        # A world may print where its copy lives; that is the runner's temporary folder, not the world's answer.
        world = manager.sessions[session]['world']
        places = sorted({str(world), str(Path(world).resolve())}, key=len, reverse=True)
        for command in commands_for(entry):
            result = manager.execute(session, command)
            output = result['output'].rstrip('\n')
            for place in places:
                output = output.replace(place, COPY)
            require(
                not bundles.HOST_PATH.search(output),
                'A host path appears in the answer to ' + command + ' in ' + run_id,
            )
            entries.append({'command': command, 'output': output, 'exit_code': result['exit_code']})
    finally:
        manager.close()
    return {
        'schema_version': 1,
        'run_id': run_id,
        'corpus_sha256': run['corpus_sha256'],
        'recorded_on': today,
        'runner': 'isolated local runner (macOS sandbox, disposable copy)',
        'entries': entries,
    }


EPILOG = '''examples:
  # See which console worlds would be recorded; starts nothing
  python3 scripts/record_consoles.py --list
  # Record every console world shown on the release page (macOS)
  python3 scripts/record_consoles.py
  # Record one world again
  python3 scripts/record_consoles.py RUN_ID

Reference: docs/commands.md
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__, epilog=EPILOG, formatter_class=atlas.HelpFormatter)
    parser.add_argument(
        'run_ids',
        nargs='*',
        metavar='RUN_ID',
        help='Console world to record (default: every one shown on the release page)',
    )
    parser.add_argument(
        '--project', type=Path, default=ROOT, metavar='DIR', help='Study directory (default: this repository)'
    )
    parser.add_argument(
        '--list', action='store_true', help='Print the console worlds that would be recorded and exit; starts nothing'
    )
    args = parser.parse_args()
    project = args.project.resolve()
    run_ids = args.run_ids or shown_consoles(project)
    if args.list:
        for run_id in run_ids:
            print(run_id)
        return
    today = dt.date.today().isoformat()
    (project / 'data/recordings').mkdir(exist_ok=True)
    for run_id in run_ids:
        value = record(project, run_id, today)
        (project / 'data/recordings' / (run_id + '.json')).write_text(
            json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8'
        )
        print('Recorded %s: %d commands' % (run_id, len(value['entries'])))


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Interrupted. Recordings already written are kept.', file=sys.stderr)
        sys.exit(130)
    except (ValueError, OSError, KeyError) as error:
        print('Error: ' + str(error), file=sys.stderr)
        sys.exit(1)
