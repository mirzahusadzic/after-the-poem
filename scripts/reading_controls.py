#!/usr/bin/env python3
"""Prepare a scratch copy of the study for validating a reading before it is shown.

`prepare OUT` copies the study's records into OUT and imports the authored decoy
world (examples/controls/decoy-world) as a clearly labelled control run. Pilot
readings and control readings then run against OUT with `evaluate.py run-profile`
or `evaluate.py score --project OUT`; nothing they produce enters the real study
unless it is later admitted on its own. The decoy names every gesture of the seed
and carries none of its relations; a reader that is not fooled by echo reads it low.
"""

import argparse
import datetime as dt
import shutil
import sys
import tempfile
from pathlib import Path

import atlas
import bundles
import codex_worker as worker
from bundles import load_json, require, sha


ROOT = Path(__file__).resolve().parents[1]
DECOY = ROOT / 'examples/controls/decoy-world'
DECOY_RUN = 'control-decoy-gestures'
STUDY = ['corpus', 'data', 'src', 'assets']
NOTICES = ['LICENSE', 'LICENSE-CONTENT', 'LICENSING.md', 'THIRD_PARTY_NOTICES.md']


def decoy_bundle(project, target):
    """A Run Bundle for the authored decoy, answering the cohort's own prompt; no model made it."""
    cohort = next(item for item in atlas.validate(project)['cohorts'] if item['id'] == 'dream-4521-v1')
    prompt = (project / 'corpus/prompts' / (cohort['prompt_sha256'] + '.txt')).read_bytes()
    target.mkdir()
    shutil.copytree(DECOY, target / 'candidate')
    files = sorted(path for path in (target / 'candidate').rglob('*') if path.is_file())
    (target / 'prompt.txt').write_bytes(prompt)
    worker.write_json(
        target / 'manifest.json',
        {
            'schema_version': 1,
            'files': [
                {
                    'path': path.relative_to(target / 'candidate').as_posix(),
                    'bytes': path.stat().st_size,
                    'sha256': sha(path.read_bytes()),
                }
                for path in files
            ],
        },
    )
    now = dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00', 'Z')
    worker.write_json(
        target / 'run.json',
        {
            'schema_version': 1,
            'run_id': DECOY_RUN,
            'seed_id': 'seed-' + cohort['prompt_sha256'][:24],
            'prompt_sha256': cohort['prompt_sha256'],
            'cohort': {'id': 'control-decoy-v1', 'title': 'Authored decoy (reader control)', 'baseline': True},
            'presentation': {'label': 'Decoy · authored control', 'title': 'The Glory Workbench'},
            'outcome': {
                'status': 'completed',
                'started_at': now,
                'ended_at': now,
                'stop_reason': 'authored',
                'errors': [],
                'partial_output': False,
                'truncation': {'truncated': False, 'reason': None},
            },
            'condition': {
                'requested': {
                    'model': 'none (authored control)',
                    'provider': 'authored-control',
                    'router_revision': None,
                    'routing_policy': None,
                },
                'reported': {'model': None, 'provider': None},
                'harness': {
                    'name': 'authored-control',
                    'version': '1',
                    'reasoning_effort': 'none',
                    'tools': [],
                    'runtime': {},
                },
                'isolation': {'mode': 'authored', 'network': 'not applicable'},
                'budgets': {'wall_time_seconds': None, 'input_tokens': None, 'output_tokens': None, 'cost': None},
            },
            'usage': {},
            'cost': {'amount': None, 'currency': None, 'provenance': 'Authored control; no model call'},
        },
    )
    worker.write_json(
        target / 'observations.json',
        {
            'schema_version': 1,
            'attribution': 'After the Poem authored control',
            'summary': 'An authored decoy that names every gesture of the seed and carries none of its relations. '
            'It exists only to validate a reading and is never shown or admitted to the study.',
            'checks': [],
        },
    )
    bundles.validate_bundle(target)
    return target


def prepare(out):
    out = Path(out).resolve()
    require(not out.exists(), 'Choose a new directory for the scratch study')
    out.mkdir(parents=True)
    for name in STUDY:
        shutil.copytree(ROOT / name, out / name)
    for name in NOTICES:
        shutil.copyfile(ROOT / name, out / name)
    with tempfile.TemporaryDirectory(prefix='after-poem-decoy-') as temporary:
        bundles.import_bundle(decoy_bundle(out, Path(temporary) / 'bundle'), out)
    atlas.validate(out)
    return out


EPILOG = '''examples:
  # Make a scratch study with the decoy world
  python3 scripts/reading_controls.py prepare .local/scratch-study
  # Read the decoy there (calls a model; see evaluate.py score --help)
  python3 scripts/evaluate.py score --project .local/scratch-study --run-id control-decoy-gestures \\
    --policy POLICY.json --model MODEL --output .local/evaluation-runs/control-decoy --accept-run \\
    --max-turns 3 --max-input-tokens 300000 --max-output-tokens 120000

Reference: docs/commands.md
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__, epilog=EPILOG, formatter_class=atlas.HelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True, metavar='COMMAND')
    summary = 'Copy the study into a new directory and import the decoy world; calls no model'
    prepare_parser = commands.add_parser(
        'prepare', help=summary, description=summary + '.', formatter_class=atlas.HelpFormatter
    )
    prepare_parser.add_argument(
        'out', type=Path, metavar='OUT', help='New directory for the scratch study; it must not exist'
    )
    args = parser.parse_args()
    print('Scratch study with the decoy run "' + DECOY_RUN + '": ' + str(prepare(args.out)))


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Interrupted. The scratch study may be incomplete; remove it or choose a new directory.', file=sys.stderr)
        sys.exit(130)
    except (bundles.BundleError, OSError, KeyError, ValueError) as error:
        print('Error: ' + str(error), file=sys.stderr)
        sys.exit(1)
