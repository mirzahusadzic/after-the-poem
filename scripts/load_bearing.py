#!/usr/bin/env python3
"""Load-bearing trials for readings: does a reading's level rest on the evidence it cites?

A plan is declared before any trial. It pins the reader, the reading prompt, the rubric, the
number of independent sessions and the masked vocabulary, and lists the worlds to read as they
are. Each case names one world and one question and takes, from a reference reading, the exact
line ranges that reading cited for its level there. Every case is then read four ways, each
trial in its own independent sessions:

  baseline      the evidence as preserved (shared with the plain reading of that world)
  repeat        the same evidence in fresh sessions: no intervention, the level should hold
  presentation  the seed's distinctive words masked: an irrelevant change, the level should hold
  ablation      the reference's cited ranges blanked: the relation removed, the level should fall
  sham          as many uncited lines blanked: removal alone, the level should hold

Only the evidence the reader sees changes. Frozen worlds are never touched; trials run against a
scratch copy of the study and are never admitted. Findings use five categories: carried, coupled
but misaligned, cooled, unresolved and outside scope. A later plan may reuse retained trials whose
evidence its own change does not touch (baseline, repeat, masked), pinned by hash, and may declare
stricter rules. The method is described in docs/study.md and the steps in docs/pipeline.md.
"""

import argparse
import concurrent.futures
import json
import random
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import atlas
import evaluation_contract as contracts
import extraction_pipeline
import rubric_policy
import session_flow
from bundles import canonical, load_json, require, sha


ROOT = Path(__file__).resolve().parents[1]
TRIALS = ['baseline', 'repeat', 'presentation', 'ablation', 'sham']
INTERVENTIONS = TRIALS[1:]
EXPECTATIONS = {'repeat': 'same level', 'presentation': 'same level', 'ablation': 'lower level', 'sham': 'same level'}
CATEGORIES = ['carried', 'coupled but misaligned', 'cooled', 'unresolved', 'outside scope']
RULES = (1, 2)
# One turn may overrun a cap before the budget check stops the next; reserve it too.
TURN_MARGIN_USD = 0.03


def overlaps(a, b):
    return a['path'] == b['path'] and a['start'] <= b['end'] and b['start'] <= a['end']


def merged(ranges):
    """Sorted, non-overlapping ranges; adjacent or overlapping citations become one block."""
    result = []
    for item in sorted(ranges, key=lambda r: (r['path'], r['start'], r['end'])):
        if result and result[-1]['path'] == item['path'] and item['start'] <= result[-1]['end'] + 1:
            result[-1]['end'] = max(result[-1]['end'], item['end'])
        else:
            result.append(dict(item))
    return result


def as_range(citation):
    return {'path': citation['source_path'], 'start': citation['start'], 'end': citation['end']}


def reference_ranges(evaluation, axis):
    """The ranges a reference reading cited for its level on one question, and every range it cited at all."""
    judgments = [j for j in evaluation['judgments'] if j['node_id'] == axis]
    require(judgments, 'The reference reading has no judgment for ' + axis)
    cited = merged([as_range(c) for j in judgments for c in j['citations']])
    every = merged(
        [as_range(c) for j in evaluation['judgments'] for c in j['citations'] + j.get('counter_citations', [])]
    )
    return judgments, cited, every


def sham_ranges(prepared, ablate, exclude, seed):
    """Blocks of the same lengths as the ablation, in the same file where one fits, from lines no reference
    citation touches and that are not all blank; drawn with a declared seed so the plan fixes them in advance."""
    rng = random.Random(seed)
    lines = {source['path']: source['lines'] for source in prepared['sources']}
    chosen = []
    for item in ablate:
        length = item['end'] - item['start'] + 1
        placed = None
        for path in dict.fromkeys([item['path']] + sorted(lines)):
            starts = [
                start
                for start in range(1, len(lines[path]) - length + 2)
                if not any(
                    overlaps({'path': path, 'start': start, 'end': start + length - 1}, other)
                    for other in exclude + chosen
                )
                and any(line.strip() for line in lines[path][start - 1 : start - 1 + length])
            ]
            if starts:
                start = rng.choice(starts)
                placed = {'path': path, 'start': start, 'end': start + length - 1}
                break
        require(placed is not None, 'No uncited block of ' + str(length) + ' lines is available for the sham')
        chosen.append(placed)
    return chosen


def line_count(ranges):
    return sum(item['end'] - item['start'] + 1 for item in ranges)


def prepare_plan(draft_path, project):
    """Complete a draft with pinned identities, reference-derived ablations and sham ranges: the declared plan.

    A case names either one `reference` reading (a path in the study) or `anchors`: retained trials whose
    sessions' citations for the case's question together form the ablation. Trials listed under `reuse` (and a
    reading's `reuse`) are retained evaluations from an earlier plan, below `reuse_root`, pinned by hash."""
    draft, project = load_json(draft_path), Path(project)
    require(draft.get('schema_version') == 1, 'Unsupported load-bearing draft')
    data = atlas.validate(project)
    terms_path = ROOT / draft['masked_terms']
    root = ROOT / draft['reuse_root'] if 'reuse_root' in draft else None

    def retained(path):
        require(root is not None, 'Reuse and anchors need a reuse_root')
        record = load_json(root / path / 'evaluation.json')
        return record, {'from': path, 'evaluation_sha256': sha((root / path / 'evaluation.json').read_bytes())}

    plan = {
        'schema_version': 1,
        'id': draft['id'],
        'question': draft['question'],
        'frame': dict(
            draft['frame'],
            rubric_sha256=sha(canonical(load_json(project / 'data/rubric.json'))),
            prompt_version=extraction_pipeline.PROMPT_VERSION,
        ),
        'masked_terms': {'path': draft['masked_terms'], 'sha256': sha(terms_path.read_bytes())},
        'sham_seed': draft['sham_seed'],
        'expectations': EXPECTATIONS,
        'readings': [],
        'cases': [],
    }
    for key in ('rules', 'reuse_root', 'note'):
        if key in draft:
            plan[key] = draft[key]
    reused_readings = {}
    for item in draft['readings']:
        reading = item if isinstance(item, dict) else {'run_id': item}
        entry = {'run_id': reading['run_id'], 'corpus_sha256': corpus_sha256(data, reading['run_id'])}
        if 'reuse' in reading:
            record, entry['reuse'] = retained(reading['reuse'])
            require(record['run_id'] == reading['run_id'], 'A reused reading reads another world: ' + reading['run_id'])
            reused_readings[reading['run_id']] = record
        plan['readings'].append(entry)
    for index, case in enumerate(draft['cases']):
        reuse, seen = {}, []
        for trial, path in case.get('reuse', {}).items():
            require(
                trial in ('repeat', 'presentation'), 'Only trials the ablation does not touch can be reused: ' + trial
            )
            record, reuse[trial] = retained(path)
            require(record['run_id'] == case['run_id'], 'A reused trial reads another world: ' + case['id'])
            seen.append(record)
        if 'reference' in case:
            reference = load_json(project / case['reference'])
            require(reference['run_id'] == case['run_id'], 'The reference reads another world: ' + case['id'])
            judgments, cited, every = reference_ranges(reference, case['axis'])
            references = {
                'evaluation_id': reference['evaluation_id'],
                'evaluation_sha256': sha((project / case['reference']).read_bytes()),
                'reading_prompt': reference['gateway'].get('reading_prompt'),
                'outcomes': [j['outcome'] for j in judgments],
                'verdicts': [j['verdict'] for j in judgments],
            }
        else:
            anchors = [retained(path) for path in case['anchors']]
            require(all(record['run_id'] == case['run_id'] for record, _ in anchors), 'An anchor reads another world')
            cited = merged([r for record, _ in anchors for r in reference_ranges(record, case['axis'])[1]])
            seen += [record for record, _ in anchors]
            references = [
                dict(
                    identity,
                    evaluation_id=record['evaluation_id'],
                    outcomes=[j['outcome'] for j in reference_ranges(record, case['axis'])[0]],
                )
                for record, identity in anchors
            ]
            every = []
        # The sham avoids every line any retained reading of this world cited, on any question.
        seen += [reused_readings[case['run_id']]] if case['run_id'] in reused_readings else []
        every = merged(every + cited + [r for record in seen for r in reference_ranges(record, case['axis'])[2]])
        prepared = extraction_pipeline.prepare_sources(project, case['run_id'], data=data)
        entry = {
            'id': case['id'],
            'run_id': case['run_id'],
            'axis': case['axis'],
            'reference': references,
            'ablate': cited,
            'cited_anywhere': every,
            'sham': sham_ranges(prepared, cited, every, draft['sham_seed'] + index),
        }
        if reuse:
            entry['reuse'] = reuse
        plan['cases'].append(entry)
    validate_plan(plan, project)
    return plan


def corpus_sha256(data, run_id):
    run = next((item for item in data['runs'] if item['id'] == run_id), None)
    require(run is not None, 'Unknown run: ' + run_id)
    return run['corpus_sha256']


def validate_plan(plan, project):
    project = Path(project)
    fields(
        plan,
        ['schema_version', 'id', 'question', 'frame', 'masked_terms', 'sham_seed', 'expectations', 'readings', 'cases'],
        ['rules', 'reuse_root', 'note'],
        'load-bearing plan',
    )
    require(plan.get('rules', 1) in RULES, 'Unknown finding rules')
    require(
        plan.get('reuse_root') is None or contracts.safe_path(plan['reuse_root'], 'reuse root'), 'Invalid reuse root'
    )
    contracts.safe_id(plan['id'], 'plan id')
    contracts.exact_fields(
        plan['frame'],
        ['model', 'reasoning', 'sessions', 'minimum_agreement', 'rubric_sha256', 'prompt_version'],
        'plan frame',
    )
    require(
        type(plan['frame']['sessions']) is int and plan['frame']['sessions'] >= 3,
        'A trial needs at least three independent sessions',
    )
    require(plan['expectations'] == EXPECTATIONS, 'Expectations differ from the declared method')
    require(
        plan['frame']['rubric_sha256'] == sha(canonical(load_json(project / 'data/rubric.json'))),
        'The plan pins a different rubric',
    )
    require(
        plan['masked_terms']['sha256'] == sha((ROOT / plan['masked_terms']['path']).read_bytes()),
        'The plan pins a different masked vocabulary',
    )
    data = atlas.validate(project)
    for reading in plan['readings']:
        fields(reading, ['run_id', 'corpus_sha256'], ['reuse'], 'plan reading')
        require(
            corpus_sha256(data, reading['run_id']) == reading['corpus_sha256'],
            'The plan pins a different world: ' + reading['run_id'],
        )
        require('reuse' not in reading or 'reuse_root' in plan, 'Reuse needs a reuse_root')
    for source in reused(plan).values():
        fields(source, ['from', 'evaluation_sha256'], [], 'reused trial')
        contracts.safe_path(source['from'], 'reused trial')
        contracts.digest(source['evaluation_sha256'], 'reused evaluation')
    axes = {axis['id'] for axis in load_json(project / 'data/rubric.json')['axes']}
    read = {reading['run_id'] for reading in plan['readings']}
    for case in plan['cases']:
        fields(case, ['id', 'run_id', 'axis', 'reference', 'ablate', 'cited_anywhere', 'sham'], ['reuse'], 'plan case')
        require(
            set(case.get('reuse', {})) <= {'repeat', 'presentation'} and ('reuse' not in case or 'reuse_root' in plan),
            'Only the repeat and masked trials can be reused, from a reuse_root',
        )
        contracts.safe_id(case['id'], 'case id')
        require(case['axis'] in axes, 'Unknown axis: ' + case['axis'])
        require(case['run_id'] in read, 'A case needs its world among the plain readings (its baseline): ' + case['id'])
        require(
            case['ablate']
            and all(any(overlaps(removed, cited) for cited in case['cited_anywhere']) for removed in case['ablate']),
            'Ablated ranges must be cited by the reference',
        )
        require(
            not any(overlaps(a, b) for a in case['sham'] for b in case['cited_anywhere']),
            'A sham range touches cited evidence',
        )
        require(
            sorted(r['end'] - r['start'] for r in case['sham'])
            == sorted(r['end'] - r['start'] for r in case['ablate']),
            'The sham must remove blocks of the same lengths as the ablation',
        )


def reused(plan):
    """Trial directory (relative to the output) -> the retained trial it copies."""
    sources = {'readings/' + reading['run_id']: reading['reuse'] for reading in plan['readings'] if 'reuse' in reading}
    for case in plan['cases']:
        for trial, source in case.get('reuse', {}).items():
            sources['cases/' + case['id'] + '/' + trial] = source
    return sources


def copy_reused(plan, out):
    """Copy each reused trial into the output after checking its hash, so the output holds every record it reads."""
    for target, source in reused(plan).items():
        if (out / target / 'evaluation.json').exists():
            continue
        origin = ROOT / plan['reuse_root'] / source['from']
        require(
            sha((origin / 'evaluation.json').read_bytes()) == source['evaluation_sha256'],
            'A reused trial changed since the plan was declared: ' + source['from'],
        )
        require(not (out / target).exists(), 'An unfinished trial stands where a reused one belongs: ' + target)
        shutil.copytree(origin, out / target)


def fields(value, required, optional, label):
    require(
        isinstance(value, dict) and set(required) <= set(value) <= set(required) | set(optional),
        'Unexpected or missing fields in ' + label,
    )


def finding(levels, samples=None, rules=1):
    """The category for one case from its five aggregate levels; None where the sessions reached no quorum.
    Rules 2 also need every baseline and repeat session to agree: a split level cannot show a one-level change."""
    missing = [trial for trial in TRIALS if levels[trial] is None]
    if missing:
        return 'unresolved', 'no agreed level in: ' + ', '.join(missing)
    if rules >= 2:
        pooled = samples['baseline'] + samples['repeat']
        if len(set(pooled)) > 1:
            return 'unresolved', (
                'the baseline and repeat sessions split (%s); a one-level change cannot be told '
                'from the spread' % ','.join(level.replace('level-', '') for level in pooled)
            )
    b, r, p, a, s = (levels[trial] for trial in TRIALS)
    if r != b:
        return 'unresolved', 'the level moved %d -> %d with no intervention; this grain cannot tell' % (b, r)
    if p != b:
        return 'coupled but misaligned', "masking the seed's words moved the level %d -> %d" % (b, p)
    if a > b:
        return 'coupled but misaligned', 'removing the cited evidence raised the level %d -> %d' % (b, a)
    if a == b:
        return 'cooled', 'removing the cited evidence left the level at %d' % b
    if s != b:
        return 'unresolved', 'removing uncited lines also moved the level %d -> %d; the fall is not specific' % (b, s)
    return 'carried', 'the level fell %d -> %d without the cited evidence and held under masking and the sham' % (b, a)


def trial_directory(out, case_or_run, trial):
    if trial == 'baseline':
        return out / 'readings' / case_or_run['run_id']
    return out / 'cases' / case_or_run['id'] / trial


def trial_command(plan, out, project, target, trial, budget):
    output = trial_directory(out, target, trial)
    name = target['run_id'] if trial == 'baseline' else target['id'] + '-' + trial
    args = [
        sys.executable,
        str(ROOT / 'scripts/evaluate.py'),
        'score',
        '--project',
        str(project),
        '--run-id',
        target['run_id'],
        '--policy',
        str(out / 'policy.json'),
        '--model',
        plan['frame']['model'],
        '--reasoning',
        plan['frame']['reasoning'],
        '--evaluation-id',
        plan['id'] + '-' + name,
        '--output',
        str(output),
        '--accept-run',
    ]
    if trial == 'presentation':
        args += ['--mask-terms', str(ROOT / plan['masked_terms']['path'])]
    if trial in ('ablation', 'sham'):
        ranges = out / 'cases' / target['id'] / (trial + '-ranges.json')
        args += ['--ablate', str(ranges)]
    return output, args + budget


def spent_usd(output, pricing):
    return session_flow.usage_totals(session_flow.ledger(output), pricing)['estimated_usd'] if output.exists() else 0.0


class Purse:
    """A total dollar cap across trials: each trial reserves its own cap and a turn's margin before it starts.
    A trial that does not fit waits for running trials to settle; it is skipped only when none is running."""

    def __init__(self, total, per_trial, pricing, outputs):
        self.total, self.per_trial, self.pricing = total, per_trial + TURN_MARGIN_USD, pricing
        self.spent = sum(spent_usd(output, pricing) for output in outputs)
        self.running, self.condition = 0, threading.Condition()

    def reserve(self):
        with self.condition:
            while self.spent + (self.running + 1) * self.per_trial > self.total:
                if not self.running:
                    return False
                self.condition.wait()
            self.running += 1
            return True

    def settle(self, cost):
        with self.condition:
            self.running -= 1
            self.spent += cost
            self.condition.notify_all()


def trials(plan):
    """The cases' worlds and trials first, so a total cap that runs short leaves plain readings, not cases, unread."""
    cased = {case['run_id'] for case in plan['cases']}
    for reading in plan['readings']:
        if reading['run_id'] in cased:
            yield reading, 'baseline'
    for case in plan['cases']:
        for trial in INTERVENTIONS:
            yield case, trial
    for reading in plan['readings']:
        if reading['run_id'] not in cased:
            yield reading, 'baseline'


def run(plan_path, project, out, budget, *, total_usd, parallel=6, retry=False):
    plan, project, out = load_json(plan_path), Path(project).resolve(), Path(out).resolve()
    validate_plan(plan, project)
    out.mkdir(parents=True, exist_ok=True)
    policy = rubric_policy.build(
        load_json(project / 'data/rubric.json'),
        repetitions=plan['frame']['sessions'],
        minimum_agreement=plan['frame']['minimum_agreement'],
    )
    for name, content in [('policy.json', canonical(policy) + b'\n'), ('plan.json', Path(plan_path).read_bytes())]:
        require(not (out / name).exists() or (out / name).read_bytes() == content, 'The output holds another ' + name)
        (out / name).write_bytes(content)
    for case in plan['cases']:
        (out / 'cases' / case['id']).mkdir(parents=True, exist_ok=True)
        for trial in ('ablation', 'sham'):
            (out / 'cases' / case['id'] / (trial + '-ranges.json')).write_text(
                json.dumps(case['ablate' if trial == 'ablation' else 'sham'], indent=2) + '\n'
            )
    copy_reused(plan, out)
    pricing = {
        'input_per_million': value(budget, '--price-input'),
        'cached_input_per_million': value(budget, '--price-cached-input'),
        'output_per_million': value(budget, '--price-output'),
    }
    # Reused trials were paid for by the plan that ran them.
    copies = {out / target for target in reused(plan)}
    jobs = [
        job
        for job in (trial_command(plan, out, project, target, trial, budget) for target, trial in trials(plan))
        if job[0] not in copies
    ]
    pending = [(output, command) for output, command in jobs if not (output / 'evaluation.json').exists()]
    if retry:
        for output, _ in pending:
            set_aside(output)
    # Attempts set aside still count against the total.
    attempts = [path for output, _ in jobs for path in output.parent.glob(output.name + '.attempt-*') if path.is_dir()]
    purse = Purse(total_usd, value(budget, '--max-usd'), pricing, [output for output, _ in jobs] + attempts)

    def launch(job):
        output, command = job
        if not purse.reserve():
            return output, 'skipped: the total cap leaves no room for another capped trial'
        try:
            before = spent_usd(output, pricing)
            output.parent.mkdir(parents=True, exist_ok=True)
            with (output.parent / (output.name + '.log')).open('a') as log:
                code = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT).returncode
            return output, 'exit ' + str(code)
        finally:
            purse.settle(spent_usd(output, pricing) - before)

    with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as pool:
        for output, result in pool.map(launch, pending):
            print(output.relative_to(out), result, flush=True)
    print('Estimated spend in this output: $%.4f' % sum(spent_usd(output, pricing) for output, _ in jobs))
    return findings(plan_path, out)


def set_aside(output):
    """Keep an unfinished trial as OUTPUT.attempt-N, so the next try starts fresh sessions instead of repairing
    in a thread that already holds the failed answer."""
    if output.exists():
        number = 1 + sum(path.is_dir() for path in output.parent.glob(output.name + '.attempt-*'))
        output.rename(output.parent / (output.name + '.attempt-' + str(number)))
        log = output.parent / (output.name + '.log')
        if log.exists():
            log.rename(output.parent / (output.name + '.attempt-' + str(number) + '.log'))


def value(arguments, flag):
    require(flag in arguments, 'Each trial needs ' + flag)
    return float(arguments[arguments.index(flag) + 1])


def level_of(output, axis):
    if not (output / 'evaluation.json').exists():
        return None
    record = load_json(output / 'evaluation.json')
    node = next(item for item in record['aggregations'] if item['node_id'] == axis + '-consensus')
    samples = sorted((j for j in record['judgments'] if j['node_id'] == axis), key=lambda j: j.get('sample', 1))
    level = node['outcome'].replace('level-', '')
    return {
        'level': int(level) if level.isdigit() else None,
        'outcome': node['outcome'],
        'agreement': node['agreement'],
        'samples': [j['outcome'] for j in samples],
        'evaluation_sha256': sha((output / 'evaluation.json').read_bytes()),
    }


def findings(plan_path, out):
    plan = load_json(plan_path)
    cases = []
    for case in plan['cases']:
        observed = {trial: level_of(trial_directory(out, case, trial), case['axis']) for trial in TRIALS}
        sources = reused(plan)
        for trial, value in observed.items():
            source = sources.get(str(trial_directory(out, case, trial).relative_to(out)))
            if value is not None and source is not None:
                value['reused_from'] = source['from']
        category, reason = finding(
            {trial: observed[trial]['level'] if observed[trial] else None for trial in TRIALS},
            {trial: observed[trial]['samples'] if observed[trial] else [] for trial in TRIALS},
            plan.get('rules', 1),
        )
        cases.append(
            {
                'id': case['id'],
                'run_id': case['run_id'],
                'axis': case['axis'],
                'category': category,
                'reason': reason,
                'ablated_lines': line_count(case['ablate']),
                'trials': observed,
            }
        )
    readings = []
    for reading in plan['readings']:
        output = trial_directory(out, reading, 'baseline')
        record = load_json(output / 'evaluation.json') if (output / 'evaluation.json').exists() else None
        readings.append(
            {
                'run_id': reading['run_id'],
                'levels': None
                if record is None
                else {
                    item['node_id'].replace('-consensus', ''): {
                        'outcome': item['outcome'],
                        'agreement': item['agreement'],
                        'distinct_outcomes': item['distinct_outcomes'],
                    }
                    for item in record['aggregations']
                },
            }
        )
    record = {
        'schema_version': 1,
        'plan_id': plan['id'],
        'plan_sha256': sha(Path(plan_path).read_bytes()),
        'rules': plan.get('rules', 1),
        'categories': CATEGORIES,
        'readings': readings,
        'cases': cases,
    }
    (out / 'findings.json').write_text(json.dumps(record, indent=2, ensure_ascii=False) + '\n')
    return record


TRIAL_BUDGET = 'TRIAL_BUDGET...'
EPILOG = """examples:
  # Run every trial of a declared plan under a total cap (calls a model)
  python3 scripts/load_bearing.py run PLAN.json --project .local/scratch-study \\
    --output .local/load-bearing/OUT --total-usd 1.00 --accept-run -- \\
    --max-turns 9 --max-input-tokens 900000 --max-output-tokens 300000 \\
    --max-seconds 2400 --max-usd 0.07 --price-input 0.10 \\
    --price-cached-input 0.002 --price-output 0.20
  # Recompute the findings from retained trials
  python3 scripts/load_bearing.py findings PLAN.json --output .local/load-bearing/OUT
  # Declare a plan from a draft, once, before any trial
  python3 scripts/load_bearing.py prepare DRAFT.json --project .local/scratch-study --output PLAN.json

Everything after a bare -- is each trial's budget, passed to evaluate.py score
as FLAG VALUE pairs (not FLAG=VALUE): --max-turns, --max-input-tokens,
--max-output-tokens, and --max-usd with --price-input, --price-cached-input and
--price-output; --max-seconds is optional. --max-usd caps each trial and
--total-usd caps them all. Rerunning run resumes.

Reference: docs/commands.md
"""


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=EPILOG,
        formatter_class=atlas.HelpFormatter,
        usage='%(prog)s [-h] COMMAND ...\n       %(prog)s run PLAN [OPTIONS] -- ' + TRIAL_BUDGET,
    )
    commands = parser.add_subparsers(dest='command', required=True, metavar='COMMAND', prog=parser.prog)

    def command(name, summary, **options):
        return commands.add_parser(
            name, help=summary, description=summary + '.', formatter_class=atlas.HelpFormatter, **options
        )

    prepare = command('prepare', 'Declare a plan: pin identities, derive ablations and draw shams; calls no model')
    prepare.add_argument('draft', type=Path, help='Draft plan JSON')
    prepare.add_argument(
        '--project', type=Path, required=True, metavar='DIR', help='Scratch study holding the reference readings'
    )
    prepare.add_argument(
        '--output',
        type=Path,
        required=True,
        metavar='FILE',
        help='New file for the declared plan; an existing one is never overwritten',
    )
    execute = command(
        'run',
        'Run every trial of a declared plan against a scratch study; ' 'each trial\'s budget follows a bare --',
        usage='%(prog)s [-h] --project DIR --output DIR --total-usd USD [--parallel N]\n'
        + ' ' * 27
        + '[--accept-run] [--retry] plan -- '
        + TRIAL_BUDGET,
    )
    execute.add_argument('plan', type=Path, help='Declared plan JSON')
    execute.add_argument('--project', type=Path, required=True, metavar='DIR', help='Scratch study the trials read')
    execute.add_argument(
        '--output',
        type=Path,
        required=True,
        metavar='DIR',
        help='Directory for the trials and findings.json; rerunning resumes',
    )
    execute.add_argument('--parallel', type=int, default=6, metavar='N', help='Trials to run at once')
    execute.add_argument(
        '--total-usd',
        type=float,
        required=True,
        metavar='USD',
        help='Estimated US dollars to allow across every trial in the output, ' 'earlier attempts included',
    )
    execute.add_argument(
        '--accept-run', action='store_true', help='Allow model calls, which cost money; run refuses without it'
    )
    execute.add_argument(
        '--retry',
        action='store_true',
        help='Set unfinished trials aside as .attempt-N and run them again in fresh sessions',
    )
    summary = command('findings', 'Recompute findings from retained trials; calls no model')
    summary.add_argument('plan', type=Path, help='Declared plan JSON')
    summary.add_argument(
        '--output',
        type=Path,
        required=True,
        metavar='DIR',
        help='Directory of the trials; findings.json is written there',
    )
    argv = sys.argv[1:]
    # Everything after -- is passed to each trial as its budget.
    budget = argv[argv.index('--') + 1 :] if '--' in argv else []
    args = parser.parse_args(argv[: argv.index('--')] if '--' in argv else argv)
    if args.command == 'prepare':
        require(not args.output.exists(), 'A declared plan is never overwritten; choose a new file')
        plan = prepare_plan(args.draft, args.project.resolve())
        args.output.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + '\n')
        print('Declared plan ' + plan['id'] + ': ' + str(args.output))
        return
    if args.command == 'run':
        require(args.accept_run, 'Trials call a model: pass --accept-run')
        record = run(
            args.plan,
            args.project,
            args.output,
            budget,
            total_usd=args.total_usd,
            parallel=args.parallel,
            retry=args.retry,
        )
    else:
        record = findings(args.plan, args.output.resolve())
    for case in record['cases']:
        print('%-30s %-24s %s' % (case['id'], case['category'], case['reason']))


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Interrupted. Finished trials are kept; run the same command again to continue.', file=sys.stderr)
        sys.exit(130)
    except (ValueError, OSError, KeyError) as error:
        print('Error: ' + str(error), file=sys.stderr)
        sys.exit(1)
