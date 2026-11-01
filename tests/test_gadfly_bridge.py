"""Gadfly frames for published readings, and the study's reader as their battery; nothing here calls a model."""

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import atlas  # noqa: E402
import extraction_pipeline  # noqa: E402
import gadfly_bridge as bridge  # noqa: E402
import scoring_pipeline  # noqa: E402
from bundles import load_json, sha  # noqa: E402

from midwife.interventions import plan as plan_trials, prompt as trial_prompt, rename  # noqa: E402
from midwife.pipeline import calls_needed, judge, overall, run as run_trials, situate  # noqa: E402
from midwife.types import Budget  # noqa: E402

SPEC = ROOT / 'examples/gadfly/round-1.json'


def current_copy(spec_path, directory):
    """A round's spec at the installed midwife, for tests of the commands' mechanics: a spec that has run keeps
    the release it ran under, and `build` refuses it under another."""
    spec = dict(load_json(spec_path), midwife=bridge.midwife.__version__)
    path = Path(directory) / Path(spec_path).name
    path.write_text(json.dumps(spec), encoding='utf-8')
    return path


CATEGORIES = {'carried', 'coupled but misaligned', 'cooled', 'unresolved', 'outside scope'}


def span_lines(span):
    """(source ID, line number) pairs a body span holds."""
    if span.kind == 'header':
        return []
    source_id, numbers = span.id.split(':')
    start, end = (int(n) for n in numbers.split('-'))
    return [(source_id, n) for n in range(start, end + 1)]


class Round:
    """The round's frames, built once for every test."""

    data = spec = frames = prepared = None

    @classmethod
    def load(cls):
        if cls.frames is None:
            cls.data = atlas.validate(ROOT)
            cls.spec = load_json(SPEC)
            cls.frames, cls.prepared = {}, {}
            for entry in cls.spec['frames']:
                raw = bridge.build_frame(ROOT, cls.spec, entry, data=cls.data)
                frame = situate(raw)
                cls.frames[entry['name']] = (entry, raw, frame)
                run_id = raw['baseline']['world']
                cls.prepared[run_id] = extraction_pipeline.prepare_sources(ROOT, run_id, data=cls.data)
        return cls


def cited_pairs(frame_raw, axes):
    evaluation = load_json(ROOT / 'data/evaluations' / frame_raw['baseline']['published']['evaluation'] / 'evaluation.json')
    paths = {s['path']: s['source_id'] for s in Round.prepared[frame_raw['baseline']['world']]['sources']}
    return {(paths[p], n) for p, numbers in bridge.cited(evaluation, axes).items() for n in numbers}


class FrameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Round.load()

    def test_frames_are_deterministic_and_pin_the_published_reader(self):
        for name, (entry, raw, frame) in Round.frames.items():
            again = situate(bridge.build_frame(ROOT, Round.spec, entry, data=Round.data))
            self.assertEqual(again.hash, frame.hash, name)
            published = ROOT / 'data/evaluations' / entry['evaluation'] / 'evaluation.json'
            evaluation = load_json(published)
            self.assertEqual(raw['baseline']['published']['evaluation_sha256'], sha(published.read_bytes()))
            self.assertEqual(raw['baseline']['reading_prompt'], evaluation['gateway']['reading_prompt'])
            self.assertEqual(raw['battery']['midwife'], bridge.midwife.__version__)

    def test_anchors_are_exactly_the_lines_the_sessions_cite_for_the_question(self):
        for name, (entry, raw, frame) in Round.frames.items():
            spans = {span.id: span for span in frame.spans}
            anchored = {pair for anchor in frame.anchors for pair in span_lines(spans[anchor])}
            self.assertEqual(anchored, cited_pairs(raw, {entry['axis']}), name)

    def test_the_sham_cuts_as_many_lines_in_blocks_of_the_same_sizes_and_nothing_cited(self):
        for name, (entry, raw, frame) in Round.frames.items():
            spans = {span.id: span for span in frame.spans}
            trials = {trial.id: trial for trial in plan_trials(frame)}
            sizes = lambda ids: sorted(len(span_lines(spans[i])) for i in ids)  # noqa: E731
            self.assertEqual(sizes(trials['sham-1'].removed), sizes(trials['ablation'].removed), name)
            cut = {pair for i in trials['sham-1'].removed for pair in span_lines(spans[i])}
            every = {item['id'] for item in Round.data['rubric']['axes']}
            self.assertFalse(cut & cited_pairs(raw, every), name + ': a sham cut a cited line')
            fallback = set(raw['interventions'].get('cumulative', [])) - set(frame.anchors)
            self.assertFalse(set(trials['sham-1'].removed) & fallback, name + ': a sham cut a fallback')

    def test_the_cumulative_cut_adds_exactly_the_declared_fallbacks(self):
        entry, raw, frame = Round.frames['dreamy-sol-continuation']
        spans = {span.id: span for span in frame.spans}
        paths = {s['path']: s['source_id'] for s in Round.prepared['sol-original']['sources']}
        declared = {(paths[r['path']], n) for r in entry['cumulative'] for n in range(r['start'], r['end'] + 1)}
        added = set(raw['interventions']['cumulative']) - set(frame.anchors)
        added_lines = {pair for i in added for pair in span_lines(spans[i])}
        anchored = {pair for anchor in frame.anchors for pair in span_lines(spans[anchor])}
        self.assertEqual(added_lines, declared - anchored)
        self.assertEqual(len(added_lines), 118)

    def test_the_control_holds_every_line_the_reading_cites(self):
        for name, (entry, raw, frame) in Round.frames.items():
            spans = {span.id: span for span in frame.spans}
            removed = {pair for i in raw['interventions']['control'] for pair in span_lines(spans[i])}
            every = {item['id'] for item in Round.data['rubric']['axes']}
            self.assertLessEqual(cited_pairs(raw, every), removed, name)

    def test_every_frame_has_a_prediction_for_each_trial_before_any_call(self):
        for name, (entry, raw, frame) in Round.frames.items():
            predictions = entry['predictions']
            self.assertEqual(set(predictions['levels']), {trial.id for trial in plan_trials(frame)}, name)
            for level in predictions['levels'].values():
                self.assertIn(level, bridge.LABELS + ['lower'], name)
            self.assertIn(predictions['category'], CATEGORIES, name)
            self.assertEqual(Round.spec['midwife'], bridge.midwife.__version__)


class TrialChoiceTests(unittest.TestCase):
    """A round declares its trials beside the ablation; one that names none declares round 1's set."""

    @classmethod
    def setUpClass(cls):
        Round.load()

    def build(self, name='fable-5-1-recognition', spec=None, **changes):
        spec = dict(copy.deepcopy(spec or Round.spec), **changes)
        entry = next(entry for entry in spec['frames'] if entry['name'] == name)
        revision = 'f' * 64 if spec['reader']['battery'] == bridge.UNIFIED else None
        return bridge.build_frame(ROOT, spec, entry, data=Round.data, router_revision=revision)

    def test_a_spec_that_names_no_trials_declares_round_ones_set(self):
        entry, raw, frame = Round.frames['fable-5-1-recognition']
        self.assertNotIn('trials', Round.spec)
        self.assertEqual(set(raw['interventions']), {'repeat', 'ablation', 'sham', 'presentation', 'control'})
        self.assertEqual(
            raw['promise'],
            "without the cited lines the level is lower; it holds when the seed's words are renamed in the world"
            ' and when as many uncited lines go',
        )
        named = self.build(trials=list(bridge.TRIALS), control='cited')
        self.assertEqual(situate(named).hash, frame.hash)

    def test_only_the_declared_trials_run(self):
        loose = dict(Round.spec['standing'], stable=False)
        raw = self.build(trials=['control'], standing=loose)
        self.assertEqual([trial.id for trial in plan_trials(situate(raw))], ['baseline', 'ablation', 'control'])
        self.assertEqual(
            raw['interventions']['control'], Round.frames['fable-5-1-recognition'][1]['interventions']['control']
        )

    def test_a_spec_with_unknown_or_unpaired_trials_is_refused(self):
        loose = dict(Round.spec['standing'], stable=False)
        for changes in (
            {'trials': []},
            {'trials': ['bypass'], 'standing': loose},
            {'trials': ['control', 'control'], 'standing': loose},
            {'trials': ['sham'], 'control': 'cited', 'standing': loose},
            {'control': 'everything'},
            {'trials': ['control']},
        ):
            with self.assertRaises(ValueError, msg=str(changes)):
                self.build(**changes)

    def test_the_study_reader_is_never_handed_a_blank_world(self):
        # Every level it gives cites a non-blank line, so a world-blank control could only end in paid openings.
        loose = dict(Round.spec['standing'], stable=False)
        with self.assertRaisesRegex(ValueError, 'non-blank line'):
            self.build(trials=['control'], control='world', standing=loose)

    def test_the_remnant_keeps_one_uncited_block_per_cited_block_and_nothing_cited(self):
        round_two = load_json(ROOT / 'examples/gadfly/round-2.json')
        every = {item['id'] for item in Round.data['rubric']['axes']}
        for entry in round_two['frames']:
            raw = self.build(entry['name'], spec=round_two)
            frame = situate(raw)
            self.assertEqual(situate(self.build(entry['name'], spec=round_two)).hash, frame.hash, entry['name'])
            spans = {span.id: span for span in frame.spans}
            control = set(raw['interventions']['control'])
            kept = [span.id for span in frame.spans if span.kind != 'header' and span.id not in control]
            sizes = lambda ids: sorted(len(span_lines(spans[i])) for i in ids)  # noqa: E731
            self.assertEqual(sizes(kept), sizes(frame.anchors), entry['name'])
            left = {pair for i in kept for pair in span_lines(spans[i])}
            self.assertFalse(left & cited_pairs(raw, every), entry['name'] + ': the remnant keeps a cited line')
            self.assertIn('uncited blocks as large as the cited ones', raw['promise'])

    def test_round_two_predicts_each_trial_before_any_call(self):
        round_two = load_json(ROOT / 'examples/gadfly/round-2.json')
        self.assertEqual(round_two['midwife'], bridge.midwife.__version__)
        for entry in round_two['frames']:
            frame = situate(self.build(entry['name'], spec=round_two))
            predictions = entry['predictions']
            self.assertEqual(set(predictions['levels']), {trial.id for trial in plan_trials(frame)}, entry['name'])
            self.assertLessEqual(set(predictions['levels'].values()), set(bridge.LABELS + ['lower']), entry['name'])
            self.assertIn(predictions['category'], CATEGORIES, entry['name'])

    def test_a_reader_that_needs_no_citation_can_be_handed_a_blank_world(self):
        muse = load_json(ROOT / 'examples/gadfly/round-1-muse.json')
        loose = dict(muse['standing'], stable=False)
        raw = self.build(spec=muse, trials=['control'], control='world', standing=loose)
        frame = situate(raw)
        self.assertEqual(raw['interventions']['control'], [s.id for s in frame.spans if s.kind != 'header'])
        control = next(trial for trial in plan_trials(frame) if trial.id == 'control')
        prompt = trial_prompt(frame, control, frame.inputs[0])
        body = prompt.rpartition(bridge.SEPARATOR)[0]
        self.assertTrue(all(line.startswith('=== SOURCE ') for line in body.split('\n')))
        self.assertIn('with every line of the world blank', raw['promise'])


class TrialEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Round.load()

    def test_every_trial_reads_back_as_the_world_with_its_change(self):
        for name, (entry, raw, frame) in Round.frames.items():
            prepared = Round.prepared[raw['baseline']['world']]
            spans = {span.id: span for span in frame.spans}
            mapping = tuple(sorted(raw['interventions']['presentation'].items()))
            for trial in plan_trials(frame):
                lines, renamed = bridge.trial_evidence(frame, trial_prompt(frame, trial, frame.inputs[0]), prepared)
                self.assertEqual(renamed, trial.kind == 'presentation', (name, trial.id))
                blank = {pair for i in trial.removed for pair in span_lines(spans[i])}
                for source in prepared['sources']:
                    for n, original in enumerate(source['lines'], 1):
                        expected = '' if (source['source_id'], n) in blank else original
                        if trial.kind == 'presentation':
                            expected = rename(original, mapping)
                        self.assertEqual(lines[source['source_id']][n - 1], expected, (name, trial.id, n))

    def test_text_that_is_not_the_world_is_refused(self):
        entry, raw, frame = Round.frames['fable-5-1-recognition']
        prepared = Round.prepared[raw['baseline']['world']]
        whole = trial_prompt(frame, plan_trials(frame)[0], frame.inputs[0])
        first = next(line for line in whole.split('\n') if bridge.NUMBERED.fullmatch(line))
        changed = whole.replace(first, first + ' (edited)', 1)
        without_header = whole.replace(whole.split('\n')[0] + '\n', '', 1)
        renamed_line = next(
            line for line in whole.split('\n')
            if bridge.NUMBERED.fullmatch(line)
            and rename(line, tuple(sorted(raw['interventions']['presentation'].items()))) != line
        )
        mixed = whole.replace(
            renamed_line, rename(renamed_line, tuple(sorted(raw['interventions']['presentation'].items()))), 1
        )
        other_question = whole.rpartition(bridge.SEPARATOR)[0] + bridge.SEPARATOR + 'Is it good?'
        for prompt in (changed, without_header, mixed, other_question, 'no separator here'):
            with self.assertRaises(ValueError):
                bridge.trial_evidence(frame, prompt, prepared)


class ReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Round.load()

    def reworked(self, name, change):
        raw = copy.deepcopy(Round.frames[name][1])
        change(raw)
        return situate(raw)

    def test_the_reader_refuses_frames_it_must_not_serve(self):
        reader = bridge.StudyReader(ROOT, dry_run=True)
        name = 'fable-5-1-recognition'
        reader.check(Round.frames[name][2])

        def edit_line(raw):
            span = next(s for s in raw['body']['spans'] if s['kind'] != 'header')
            span['text'] = span['text'] + ' (edited)'

        cases = {
            'another midwife': lambda raw: raw['battery'].update(midwife='0.0.9'),
            'no consent to train': lambda raw: raw['battery'].pop('trainsOnInputs'),
            'another pipeline': lambda raw: raw['baseline'].update(pipeline_sha256='0' * 64),
            'another reading prompt': lambda raw: raw['baseline'].update(reading_prompt={'version': 5, 'sha256': '0' * 64}),
            'an edited line': edit_line,
            'an unknown battery key': lambda raw: raw['battery'].update(tools=[]),
        }
        for label, change in cases.items():
            with self.subTest(label), self.assertRaises(ValueError):
                reader.check(self.reworked(name, change))
        live = bridge.StudyReader(ROOT, Path(tempfile.gettempdir()), budget={'max_turns': 1})
        with self.assertRaisesRegex(ValueError, 'router revision'):
            live.check(Round.frames[name][2])

    def run_with(self, name, read):
        entry, raw, frame = Round.frames[name]
        reader = bridge.StudyReader(ROOT, dry_run=True, read=read)
        result = run_trials(frame, reader, Budget(calls=calls_needed(frame)))
        self.assertIsNone(result.stopped)
        return judge(frame, result.records)

    def test_verdicts_follow_what_the_reader_reads(self):
        name = 'fable-5-1-recognition'
        entry, raw, frame = Round.frames[name]
        spans = {span.id: span for span in frame.spans}
        prepared = Round.prepared[raw['baseline']['world']]
        originals = {s['source_id']: s['lines'] for s in prepared['sources']}
        anchored = [
            pair for anchor in frame.anchors for pair in span_lines(spans[anchor]) if originals[pair[0]][pair[1] - 1].strip()
        ]

        def by_citation(frame, lines, cap):
            whole = all(lines[s][n - 1] for s, n in anchored)
            return {'label': 'level-3' if whole else 'level-2', 'raw': '', 'usage': {}, 'served': 'test'}

        [finding] = self.run_with(name, by_citation)
        self.assertEqual(finding.category, 'carried')
        self.assertEqual(finding.obligations, ['no hidden fallback: no bypass trial was declared'])

        def constant(frame, lines, cap):
            return {'label': 'level-3', 'raw': '', 'usage': {}, 'served': 'test'}

        [finding] = self.run_with(name, constant)
        self.assertEqual(finding.category, 'unresolved')
        self.assertIn('discrimination: the control left it at level-3 too', finding.obligations)

        def echo(frame, lines, cap):
            renamed = any('term-' in line.lower() for source in lines.values() for line in source)
            return {'label': 'level-4' if renamed else 'level-3', 'raw': '', 'usage': {}, 'served': 'test'}

        self.assertEqual(overall(self.run_with(name, echo)).category, 'coupled but misaligned')

    def test_a_dry_session_reads_the_trial_as_given_and_is_never_admitted(self):
        entry, raw, frame = Round.frames['fable-5-1-recognition']
        ablation = next(trial for trial in plan_trials(frame) if trial.id == 'ablation')
        spans = {span.id: span for span in frame.spans}
        with tempfile.TemporaryDirectory(prefix='after-poem-gadfly-') as temporary:
            reader = bridge.StudyReader(ROOT, Path(temporary), dry_run=True)
            seen = reader.observe(frame, trial_prompt(frame, ablation, frame.inputs[0]), 64)
            self.assertEqual(seen.label, 'level-3')
            call = next(Path(temporary).glob('*/call-0001'))
            given = load_json(call / 'evidence-lines.json')
            for span_id in ablation.removed:
                for source_id, n in span_lines(spans[span_id]):
                    self.assertEqual(given[source_id][n - 1], '')
            status = scoring_pipeline.validate_output(ROOT, call, data=Round.data)
            self.assertEqual(status['control'], 'supplied trial evidence; never admitted')


class SuppliedEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Round.load()

    def test_supplied_evidence_keeps_the_world_shape(self):
        prepared = Round.prepared['dream-claude-fable51-20261022']
        lines = {s['source_id']: list(s['lines']) for s in prepared['sources']}
        first = prepared['sources'][0]['source_id']
        self.assertEqual(scoring_pipeline.supply_evidence(prepared, lines)['sources'][0]['lines'], lines[first])
        short = dict(lines, **{first: lines[first][:-1]})
        missing = {k: v for k, v in lines.items() if k != first}
        broken = dict(lines, **{first: ['a\nb'] + lines[first][1:]})
        for bad in (short, missing, broken):
            with self.assertRaises(ValueError):
                scoring_pipeline.supply_evidence(prepared, bad)

    def test_supplied_evidence_stands_alone(self):
        policy = bridge.rubric_policy.build(Round.data['rubric'], repetitions=1)
        prepared = Round.prepared['dream-claude-fable51-20261022']
        lines = {s['source_id']: list(s['lines']) for s in prepared['sources']}
        source = prepared['sources'][0]
        with self.assertRaises(ValueError):
            scoring_pipeline.Plan(
                ROOT,
                policy,
                run_id='dream-claude-fable51-20261022',
                data=Round.data,
                evidence_lines=lines,
                ablated_ranges=[{'path': source['path'], 'start': 1, 'end': 1}],
            )


if __name__ == '__main__':
    unittest.main()


class EGemmaRoundTests(unittest.TestCase):
    """The same decisions read by a second reader, Gemma 4 through Gadfly's own eGemma battery."""

    SPEC = ROOT / 'examples/gadfly/round-1-egemma.json'

    @classmethod
    def setUpClass(cls):
        Round.load()
        cls.spec = load_json(cls.SPEC)
        cls.frames = {}
        for entry in cls.spec['frames']:
            raw = bridge.build_frame(ROOT, cls.spec, entry, data=Round.data)
            cls.frames[entry['name']] = (entry, raw, situate(raw))

    def test_frames_pin_the_second_reader_and_never_reach_the_page(self):
        from midwife.batteries.egemma import BATTERY_KEYS

        for name, (entry, raw, frame) in self.frames.items():
            battery = raw['battery']
            self.assertEqual(battery['kind'], 'egemma', name)
            self.assertLessEqual(set(battery), BATTERY_KEYS, name)
            for key in ('model', 'revision', 'temperature'):
                self.assertEqual(battery[key], self.spec['reader'][key], name)
            self.assertFalse(raw['materialized'], name)
            self.assertEqual(raw['baseline']['midwife'], bridge.midwife.__version__)
            self.assertLessEqual(raw['baseline']['estimated_tokens'], bridge.EGEMMA_CONTEXT)

    def test_the_seed_and_the_declared_reading_stay_whole_in_the_instruction(self):
        for name, (entry, raw, frame) in self.frames.items():
            instruction = raw['observe']['instruction']
            seed = extraction_pipeline.prepare_sources(ROOT, raw['baseline']['world'], data=Round.data)['seed_lines']
            self.assertIn('\n'.join(seed), instruction, name)
            self.assertIn('\n'.join(bridge.scoring_pipeline.DECLARED_READING), instruction, name)
            question = next(a for a in Round.data['rubric']['axes'] if a['id'] == entry['axis'])
            for anchor in question['anchors']:
                self.assertIn(anchor, instruction, name)
            # Renaming reaches the world only: the input names no word of the seed's vocabulary.
            mapping = tuple(sorted(raw['interventions']['presentation'].items()))
            self.assertEqual(rename(frame.inputs[0]['text'], mapping), frame.inputs[0]['text'], name)

    def test_the_trials_match_the_study_readers_trials(self):
        for name, (entry, raw, frame) in self.frames.items():
            study = situate(dict(bridge.build_frame(ROOT, dict(Round.spec), dict(entry, predictions={}), data=Round.data)))
            self.assertEqual(frame.anchors, study.anchors, name)
            ours = {t.id: t.removed for t in plan_trials(frame)}
            theirs = {t.id: t.removed for t in plan_trials(study)}
            self.assertEqual(ours.keys(), theirs.keys(), name)
            for trial in ('ablation', 'control'):
                self.assertEqual(ours[trial], theirs[trial], (name, trial))

    def test_a_world_eGemma_cannot_hold_is_refused_before_any_call(self):
        entry = next(e for e in Round.spec['frames'] if e['name'] == 'dreamy-sol-continuation')
        with self.assertRaisesRegex(ValueError, 'eGemma reads 32768'):
            bridge.build_frame(ROOT, self.spec, entry, data=Round.data)

    def test_every_frame_has_a_prediction_for_each_trial_before_any_call(self):
        for name, (entry, raw, frame) in self.frames.items():
            predictions = entry['predictions']
            self.assertEqual(set(predictions['levels']), {trial.id for trial in plan_trials(frame)}, name)
            self.assertIn(predictions['category'], CATEGORIES, name)

    def test_a_dry_run_checks_each_world_and_calls_nothing(self):
        with tempfile.TemporaryDirectory(prefix='after-poem-gadfly-egemma-') as temporary:
            out = Path(temporary) / 'round'
            spec = current_copy(self.SPEC, temporary)
            self.assertEqual(bridge.main(['build', str(spec), '--output', str(out)]), 0)
            self.assertEqual(bridge.main(['run', str(out), '--dry-run', '--frame', 'astra-garden-touch']), 0)
            kept = bridge.load_records(out / 'dry-records' / 'astra-garden-touch.jsonl')
            self.assertEqual(len(kept), 18)
            self.assertEqual({record['served'] for record in kept}, {'synthetic'})
            self.assertEqual(bridge.main(['run', str(out), '--frame', 'astra-garden-touch']), 2)
            # A reading under other rules keeps its own file: a later release never overwrites it.
            earlier = out / 'dry-report-midwife-0.0.0.json'
            earlier.write_text('{"midwife": "0.0.0"}\n')
            self.assertEqual(bridge.main(['report', str(out), '--dry-run', '--frame', 'astra-garden-touch']), 0)
            self.assertEqual(earlier.read_text(), '{"midwife": "0.0.0"}\n')
            reading = load_json(out / ('dry-report-midwife-%s.json' % bridge.midwife.__version__))
            self.assertEqual(reading['midwife'], bridge.midwife.__version__)
            self.assertEqual([f['name'] for f in reading['frames']], ['astra-garden-touch'])


class SecondCriticTests(unittest.TestCase):
    """The study's critic as a second critic: one call through the unified model router, read by Gadfly."""

    SPEC = ROOT / 'examples/gadfly/round-1-muse.json'
    REVISION = 'f' * 64

    @classmethod
    def setUpClass(cls):
        Round.load()
        cls.spec = load_json(cls.SPEC)
        cls.frames = {}
        for entry in cls.spec['frames']:
            raw = bridge.build_frame(ROOT, cls.spec, entry, data=Round.data, router_revision=cls.REVISION)
            cls.frames[entry['name']] = (entry, raw, situate(raw))

    def test_frames_pin_the_critic_its_consent_to_train_and_the_router(self):
        from midwife.batteries.unified import BATTERY_KEYS

        for name, (entry, raw, frame) in self.frames.items():
            battery = raw['battery']
            self.assertEqual(battery['kind'], 'unified', name)
            self.assertLessEqual(set(battery), BATTERY_KEYS, name)
            self.assertEqual((battery['model'], battery['reasoning']), (self.spec['reader']['model'], 'xhigh'))
            self.assertEqual(battery['trainsOnInputs'], [battery['model']], name)
            self.assertNotIn('tools', battery, name)
            self.assertEqual(raw['baseline']['router_revision'], self.REVISION, name)
            self.assertFalse(raw['materialized'], name)

    def test_a_router_frame_without_a_revision_is_refused(self):
        entry = self.spec['frames'][0]
        with self.assertRaisesRegex(ValueError, 'pins its revision'):
            bridge.build_frame(ROOT, self.spec, entry, data=Round.data)

    def test_only_the_reader_differs_from_the_egemma_round(self):
        egemma = load_json(ROOT / 'examples/gadfly/round-1-egemma.json')
        for entry in egemma['frames']:
            theirs = bridge.build_frame(ROOT, egemma, entry, data=Round.data)
            ours = self.frames[entry['name']][1]
            for key in ('body', 'anchors', 'inputs', 'observe', 'interventions', 'allowed', 'standing'):
                self.assertEqual(ours[key], theirs[key], (entry['name'], key))

    def test_every_frame_has_a_prediction_for_each_trial_before_any_call(self):
        for name, (entry, raw, frame) in self.frames.items():
            predictions = entry['predictions']
            self.assertEqual(set(predictions['levels']), {trial.id for trial in plan_trials(frame)}, name)
            self.assertIn(predictions['category'], CATEGORIES, name)

    def test_a_live_run_needs_its_dollar_caps_before_any_call(self):
        with tempfile.TemporaryDirectory(prefix='after-poem-gadfly-muse-') as temporary:
            out = Path(temporary) / 'round'
            spec = current_copy(self.SPEC, temporary)
            built = bridge.main(['build', str(spec), '--output', str(out), '--router-revision', self.REVISION])
            self.assertEqual(built, 0)
            self.assertEqual(bridge.main(['run', str(out), '--accept-run', '--frame', 'astra-garden-touch']), 2)
            self.assertFalse((out / 'records' / 'astra-garden-touch.jsonl').exists())
            self.assertEqual(bridge.main(['run', str(out), '--dry-run', '--frame', 'astra-garden-touch']), 0)

    def test_a_free_reader_after_a_router_frame_is_not_held_by_its_reserve(self):
        # eGemma costs nothing, so a router frame's per-call reserve must not stop it near the cap.
        from unittest import mock

        with tempfile.TemporaryDirectory(prefix='after-poem-gadfly-mixed-') as temporary:
            out, gemma = Path(temporary) / 'round', Path(temporary) / 'gemma'
            muse = current_copy(self.SPEC, temporary)
            self.assertEqual(bridge.main(['build', str(muse), '--output', str(out), '--router-revision', self.REVISION]), 0)
            local = current_copy(ROOT / 'examples/gadfly/round-1-egemma.json', temporary)
            self.assertEqual(bridge.main(['build', str(local), '--output', str(gemma)]), 0)
            manifest = load_json(out / 'round.json')
            free = next(item for item in load_json(gemma / 'round.json')['frames'] if item['name'] == 'astra-garden-touch')
            (out / 'frames' / 'gemma-astra.json').write_bytes((gemma / 'frames' / 'astra-garden-touch.json').read_bytes())
            manifest['frames'].append(dict(free, name='gemma-astra'))
            (out / 'round.json').write_text(json.dumps(manifest), encoding='utf-8')

            pricing = {'input_per_million': 0.10, 'cached_input_per_million': 0.002, 'output_per_million': 0.20}
            routed = load_json(out / 'frames' / 'astra-garden-touch.json')
            reserve = bridge.record_usd(
                {
                    'usage': {
                        'input_tokens': routed['baseline']['estimated_input_tokens'],
                        'output_tokens': bridge.UNIFIED_ANSWER_TOKENS,
                    }
                },
                pricing,
            )
            usage = {'input_tokens': 1000, 'output_tokens': 1000}
            each = bridge.record_usd({'usage': usage}, pricing)
            calls = next(item['calls'] for item in manifest['frames'] if item['name'] == 'astra-garden-touch')
            total = (calls - 0.5) * each + reserve  # every router call fits; a further reserve would not

            def reader(served):
                class Fake:
                    def __init__(self, *args, **kwargs):
                        self.name = served

                    def check(self, frame):
                        pass

                    def observe(self, frame, prompt, max_output_tokens):
                        paid = usage if served == bridge.UNIFIED else {}
                        return bridge.Observation(label='level-3', raw='fake', usage=paid, served=served)

                return Fake

            health = {'revision': self.REVISION}
            with (
                mock.patch.object(bridge.worker, 'router_health', return_value=health),
                mock.patch.object(bridge, 'Unified', reader(bridge.UNIFIED)),
                mock.patch.object(bridge, 'EGemma', reader(bridge.EGEMMA)),
            ):
                flags = ['--price-input', '0.10', '--price-cached-input', '0.002', '--price-output', '0.20']
                status = bridge.main(
                    ['run', str(out), '--accept-run', '--total-usd', repr(total), '--max-usd-per-call', '0.03']
                    + flags
                    + ['--frame', 'astra-garden-touch', '--frame', 'gemma-astra']
                )
            self.assertEqual(len(bridge.load_records(out / 'records' / 'astra-garden-touch.jsonl')), calls)
            self.assertEqual(len(bridge.load_records(out / 'records' / 'gemma-astra.jsonl')), free['calls'])
            self.assertEqual(status, 0)

    def test_the_cap_counts_what_each_call_reported(self):
        pricing = {'input_per_million': 0.10, 'cached_input_per_million': 0.002, 'output_per_million': 0.20}
        record = {'usage': {'input_tokens': 20000, 'output_tokens': 5000}}
        self.assertAlmostEqual(bridge.record_usd(record, pricing), 0.003)
