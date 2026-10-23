"""Failure-injection tests for atomic persistence hardening.

Proves that a failed write never corrupts the previously valid file and that
an interrupted bundle import retries as a domain error (or resumes), never a
raw FileExistsError. Uses no models, no network and no candidate execution.
"""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
from evaluation_fixtures import EDITORIAL  # noqa: F401  (keeps fixture ignore helper importable)


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fsutil = _load('fsutil')
bundles = _load('bundles')
import shutil


def make_bundle(root, run_id='atomic-run'):
    import hashlib

    def digest(data):
        return hashlib.sha256(data).hexdigest()

    def write_json(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    root.mkdir(parents=True, exist_ok=True)
    prompt = b'A small poem.\nA continuation begins here.\n'
    (root / 'prompt.txt').write_bytes(prompt)
    candidate = root / 'candidate'
    candidate.mkdir(exist_ok=True)
    payload = b'# A world\n\nAn inert candidate document.\n'
    (candidate / 'README.md').write_bytes(payload)
    write_json(
        root / 'manifest.json',
        {'schema_version': 1, 'files': [{'path': 'README.md', 'bytes': len(payload), 'sha256': digest(payload)}]},
    )
    write_json(
        root / 'observations.json',
        {
            'schema_version': 1,
            'attribution': 'Fixture observer',
            'summary': 'A retained observation made without executing the candidate.',
            'checks': [{'name': 'manifest', 'outcome': 'checked'}],
        },
    )
    run = {
        'schema_version': 1,
        'run_id': run_id,
        'seed_id': 'fixture-seed',
        'prompt_sha256': digest(prompt),
        'cohort': {'id': 'fixture-cohort', 'title': 'Fixture poems', 'baseline': True},
        'presentation': {'label': 'Fixture model', 'title': 'Fixture world'},
        'outcome': {
            'status': 'completed',
            'started_at': '2026-10-20T10:00:00Z',
            'ended_at': '2026-10-20T10:00:05Z',
            'stop_reason': 'completed',
            'errors': [],
            'partial_output': False,
            'truncation': {'truncated': False, 'reason': None},
        },
        'condition': {
            'requested': {'model': 'fixture', 'provider': 'local-test', 'router_revision': None, 'routing_policy': None},
            'reported': {'model': 'fixture', 'provider': 'local-test'},
            'harness': {
                'name': 'fixture-harness',
                'version': '1.0',
                'reasoning_effort': 'medium',
                'tools': [],
                'runtime': {'os': 'test'},
            },
            'isolation': {'mode': 'offline', 'network': 'denied'},
            'budgets': {'wall_time_seconds': 30, 'input_tokens': None, 'output_tokens': None, 'cost': None},
        },
        'usage': {},
        'cost': {'amount': None, 'currency': None, 'provenance': 'not billed'},
    }
    write_json(root / 'run.json', run)
    return root


def make_project(root):
    for name in ['corpus', 'data', 'src', 'assets']:
        shutil.copytree(ROOT / name, root / name, ignore=EDITORIAL)
    for name in ['LICENSE', 'LICENSE-CONTENT', 'LICENSING.md', 'THIRD_PARTY_NOTICES.md']:
        shutil.copyfile(ROOT / name, root / name)


class AtomicWriterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='after-poem-atomic-')
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_bytes_writer_uses_temp_file_and_replace(self):
        target = self.root / 'doc.json'
        target.write_bytes(b'{"prior": true}\n')
        seen = {}

        real_replace = os.replace

        def recording_replace(src, dst):
            seen['src'] = Path(src)
            seen['dst'] = Path(dst)
            self.assertNotEqual(Path(src), Path(dst))
            self.assertTrue(str(Path(src).parent) == str(Path(dst).parent))
            return real_replace(src, dst)

        with mock.patch.object(fsutil.os, 'replace', side_effect=recording_replace):
            fsutil.write_bytes_atomic(target, b'{"next": true}\n', mode=0o400)
        self.assertEqual(target.read_bytes(), b'{"next": true}\n')
        self.assertEqual(seen['dst'], target)
        self.assertFalse(seen['src'].exists())
        self.assertEqual(oct(target.stat().st_mode & 0o777), '0o400')

    def test_failed_replace_keeps_prior_valid_file(self):
        target = self.root / 'record.json'
        prior = {'schema_version': 1, 'value': 'prior-valid'}
        target.write_text(json.dumps(prior) + '\n', encoding='utf-8')
        with mock.patch.object(fsutil.os, 'replace', side_effect=OSError('injected replace failure')):
            with self.assertRaisesRegex(OSError, 'injected replace failure'):
                fsutil.write_json_atomic(target, {'schema_version': 1, 'value': 'next'})
        self.assertEqual(json.loads(target.read_text(encoding='utf-8')), prior)
        leftovers = [p for p in self.root.iterdir() if p.suffix == '.tmp']
        self.assertEqual(leftovers, [])

    def test_rewrite_keeps_the_destination_mode_and_new_files_get_the_umask_default(self):
        target = self.root / 'data.json'
        target.write_bytes(b'{}\n')
        os.chmod(target, 0o644)
        fsutil.write_json_atomic(target, {'next': True})
        self.assertEqual(target.stat().st_mode & 0o777, 0o644)
        fresh = self.root / 'fresh.json'
        fsutil.write_json_atomic(fresh, {})
        self.assertEqual(fresh.stat().st_mode & 0o777, 0o666 & ~fsutil._UMASK)

    def test_an_interrupt_mid_write_leaves_no_staged_file(self):
        target = self.root / 'interrupted.json'
        target.write_bytes(b'{"prior": true}\n')
        with mock.patch.object(fsutil.os, 'replace', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                fsutil.write_json_atomic(target, {'next': True})
        self.assertEqual(target.read_bytes(), b'{"prior": true}\n')
        self.assertEqual([p.name for p in self.root.iterdir()], ['interrupted.json'])

    def test_failed_text_write_keeps_prior_valid_file(self):
        target = self.root / 'note.txt'
        target.write_text('prior valid\n', encoding='utf-8')
        with mock.patch.object(fsutil.tempfile, 'mkstemp', side_effect=OSError('injected stage failure')):
            with self.assertRaisesRegex(OSError, 'injected stage failure'):
                fsutil.write_text_atomic(target, 'next')
        self.assertEqual(target.read_text(encoding='utf-8'), 'prior valid\n')


class BundleRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='after-poem-atomic-import-')
        self.root = Path(self.temp.name)
        make_project(self.root)
        self.bundle = make_bundle(self.root / 'incoming', run_id='atomic-run')
        # Project-wide validation belongs to atlas (edited outside this task);
        # stub it so these tests isolate the import retry behaviour itself.
        self._validate = mock.patch('atlas.validate', return_value=None)
        self._validate.start()

    def tearDown(self):
        self._validate.stop()
        self.temp.cleanup()

    def test_interrupted_import_retry_is_domain_error_not_file_exists(self):
        bundles.import_bundle(self.bundle, self.root)
        # Simulate an interrupted first attempt: corpus/review already on disk
        # while the registry was never updated (registry copy without the run).
        with self.assertRaisesRegex(ValueError, 'already exists'):
            bundles.import_bundle(self.bundle, self.root)

    def test_partial_corpus_with_different_content_is_domain_error(self):
        corpus_path = self.root / 'corpus/runs/atomic-run.json'
        corpus_path.parent.mkdir(parents=True, exist_ok=True)
        corpus_path.write_bytes(b'{"partial": "interrupted"}\n')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            bundles.import_bundle(self.bundle, self.root)
        # The partial file is untouched by the refused retry.
        self.assertEqual(corpus_path.read_bytes(), b'{"partial": "interrupted"}\n')

    def test_identical_staged_files_resume_instead_of_crashing(self):
        data = bundles.validate_bundle(self.bundle)
        corpus_path = self.root / 'corpus/runs/atomic-run.json'
        corpus_path.parent.mkdir(parents=True, exist_ok=True)
        corpus = dict(
            schema_version=1,
            run_id='atomic-run',
            archive_sha256=None,
            recorded_snapshot_sha256=data['bundle_sha256'],
            prompt_sha256=data['prompt_sha256'],
            files=[
                dict(path=str(k), bytes=len(v.read_bytes()), sha256=bundles.sha(v.read_bytes()))
                for k, v in sorted(data['paths'].items(), key=lambda item: str(item[0]))
            ],
        )
        corpus_bytes = (json.dumps(corpus, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        corpus_path.write_bytes(corpus_bytes)
        # The exclusive-create helper treats byte-identical staged files as resumed.
        self.assertFalse(bundles._write_new_bytes(corpus_path, corpus_bytes, 'Corpus record'))
        with self.assertRaises(ValueError):
            bundles._write_new_bytes(corpus_path, b'{"different": true}\n', 'Corpus record')

    def test_snapshot_restore_failure_preserves_original_error(self):
        registry_path = self.root / 'data/runs.json'
        prior = registry_path.read_bytes()
        with mock.patch.object(fsutil, 'write_bytes_atomic', side_effect=OSError('restore down')):
            with self.assertRaisesRegex(OSError, 'restore down'):
                fsutil.write_bytes_atomic(registry_path, b'next')
        self.assertEqual(registry_path.read_bytes(), prior)

    def test_failed_import_restores_snapshots_atomically(self):
        snapshots = {}
        for name in ['runs.json', 'observations.json', 'artefacts.json', 'review-selection.json']:
            path = self.root / 'data' / name
            snapshots[path] = path.read_bytes()
        snapshots[self.root / 'data/evidence-sources.json'] = (self.root / 'data/evidence-sources.json').read_bytes()
        with mock.patch.object(
            bundles.fsutil, 'write_json_atomic', side_effect=[None, None, OSError('registry down'), None, None]
        ):
            with self.assertRaisesRegex(OSError, 'registry down'):
                bundles.import_bundle(self.bundle, self.root)
        for path, prior in snapshots.items():
            self.assertEqual(path.read_bytes(), prior)


class ScoringFinishAtomicTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='after-poem-atomic-scoring-')
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_finish_write_failure_keeps_prior_evaluation(self):
        scoring = _load('scoring_pipeline')
        output = self.root / 'evaluation'
        output.mkdir(parents=True)
        prior_record = {'evaluation_id': 'prior', 'schema_version': 2}
        (output / 'evaluation.json').write_text(json.dumps(prior_record) + '\n', encoding='utf-8')
        status = {'evaluation_id': 'prior'}
        with mock.patch.object(scoring.fsutil, 'write_json_atomic', side_effect=OSError('injected finish failure')):
            with self.assertRaisesRegex(OSError, 'injected finish failure'):
                scoring._write(output, {'evaluation_id': 'next'}, status)
        self.assertEqual(json.loads((output / 'evaluation.json').read_text(encoding='utf-8')), prior_record)


def _capture_data(run_id):
    return {
        'corpora': {run_id: {'files': [{'path': 'index.html', 'text': '<html></html>'}]}},
    }


def _stage_capture(root, run_id):
    path = root / 'data/artefacts.json'
    artefacts = json.loads(path.read_text(encoding='utf-8'))
    artefacts[run_id].update(kind='none', preview=None, images=[], preview_note='Source records only.')
    path.write_text(json.dumps(artefacts, indent=2) + '\n', encoding='utf-8')
    return path


class CaptureRollbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='after-poem-atomic-capture-')
        self.root = Path(self.temp.name)
        make_project(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def _ensure(self, capture, run_id, **kwargs):
        with mock.patch('atlas.validate', return_value=_capture_data(run_id)), mock.patch(
            'atlas.build', return_value=None
        ):
            return capture.ensure(self.root, run_id, **kwargs)

    def test_capture_rollback_preserves_original_error(self):
        capture = _load('artefact_capture')
        run_id = 'deepseek-flash-max-pilot'
        path = _stage_capture(self.root, run_id)
        prior = path.read_bytes()

        def failing_capture(project, captured_run):
            raise ValueError('original capture failure')

        with self.assertRaisesRegex(ValueError, 'original capture failure') as raised:
            self._ensure(capture, run_id, description='Fixture.', capture_page=failing_capture)
        # The original error propagates (rollback failure would be chained, never the raised error).
        self.assertEqual(str(raised.exception), 'original capture failure')
        self.assertEqual(path.read_bytes(), prior)

    def test_capture_rollback_failure_still_raises_original(self):
        capture = _load('artefact_capture')
        run_id = 'deepseek-flash-max-pilot'
        _stage_capture(self.root, run_id)

        def failing_capture(project, captured_run):
            raise ValueError('original capture failure')

        with mock.patch.object(
            capture.fsutil, 'write_bytes_atomic', side_effect=[None, OSError('rollback down')]
        ):
            with self.assertRaisesRegex(ValueError, 'original capture failure'):
                self._ensure(capture, run_id, description='Fixture.', capture_page=failing_capture)


if __name__ == '__main__':
    unittest.main()


class ScoringWriteSuccessTests(unittest.TestCase):
    def test_write_publishes_evaluation_and_status_with_sha(self):
        scoring = _load('scoring_pipeline')
        with tempfile.TemporaryDirectory(prefix='after-poem-atomic-write-') as temporary:
            output = Path(temporary) / 'evaluation'
            output.mkdir(parents=True)
            record = {'evaluation_id': 'demo', 'schema_version': 2, 'judgments': []}
            status = {'evaluation_id': 'demo'}
            result = scoring._write(output, record, dict(status))
            stored = json.loads((output / 'evaluation.json').read_text(encoding='utf-8'))
            self.assertEqual(stored['evaluation_id'], 'demo')
            self.assertEqual(
                result['evaluation_sha256'], scoring.sha((output / 'evaluation.json').read_bytes())
            )
            self.assertEqual(
                json.loads((output / 'status.json').read_text(encoding='utf-8'))['evaluation_sha256'],
                result['evaluation_sha256'],
            )


class FreezeRuntimeAtomicTests(unittest.TestCase):
    def test_failed_catalog_freeze_keeps_prior_catalog(self):
        worker = _load('codex_worker')
        with tempfile.TemporaryDirectory(prefix='after-poem-atomic-freeze-') as temporary:
            output = Path(temporary) / 'frozen'
            output.mkdir(parents=True)
            prior = (worker.ROOT / 'data/model-capabilities.json').read_bytes()
            worker.freeze_runtime(
                output,
                [{'model': 'deepseek/deepseek-v4.1-flash-1017', 'reasoning': 'max'}],
                True,
                'http://127.0.0.1:18766/v1',
                capability_path=worker.ROOT / 'data/model-capabilities.json',
            )
            first = (output / 'catalog.json').read_bytes()
            self.assertTrue(first)
            with mock.patch.object(worker.fsutil, 'write_bytes_atomic', side_effect=OSError('freeze down')):
                with self.assertRaisesRegex(OSError, 'freeze down'):
                    worker.freeze_runtime(
                        output,
                        [{'model': 'deepseek/deepseek-v4.1-flash-1017', 'reasoning': 'max'}],
                        True,
                        'http://127.0.0.1:18766/v1',
                        capability_path=worker.ROOT / 'data/model-capabilities.json',
                    )
            self.assertEqual((output / 'catalog.json').read_bytes(), first)
            self.assertTrue(prior)
