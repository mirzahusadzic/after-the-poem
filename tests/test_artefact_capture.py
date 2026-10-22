import json
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from evaluation_fixtures import EDITORIAL

sys.path.insert(0, str(ROOT / 'scripts'))
import artefact_capture
import atlas


class ArtefactCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='after-poem-capture-')
        self.root = Path(self.temporary.name)
        for name in ['corpus', 'data', 'src', 'assets']:
            shutil.copytree(ROOT / name, self.root / name, ignore=EDITORIAL)
        shutil.rmtree(self.root / 'data/evaluations', ignore_errors=True)
        for name in ['LICENSE', 'LICENSE-CONTENT', 'LICENSING.md', 'THIRD_PARTY_NOTICES.md']:
            shutil.copyfile(ROOT / name, self.root / name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_generated_browser_is_registered_captured_hashed_and_idempotent(self):
        run_id = 'deepseek-flash-max-pilot'
        path = self.root / 'data/artefacts.json'
        artefacts = json.loads(path.read_text(encoding='utf-8'))
        artefacts[run_id].update(kind='none', preview=None, images=[], preview_note='Source records only.')
        path.write_text(json.dumps(artefacts, indent=2) + '\n', encoding='utf-8')

        def capture(project, captured_run):
            self.assertEqual(captured_run, run_id)
            output = project / 'assets/artefacts' / (run_id + '.png')
            png = b'\x89PNG\r\n\x1a\n' + struct.pack('>I', 13) + b'IHDR' + struct.pack('>II', 1440, 1050)
            output.write_bytes(png)
            return output

        first = artefact_capture.ensure(self.root, run_id, description='Generated world fixture.', capture_page=capture)
        self.assertEqual(first['status'], 'captured')
        data = atlas.validate(self.root)
        entry = data['artefacts'][run_id]
        self.assertEqual(entry['kind'], 'browser')
        self.assertEqual(entry['preview'], 'previews/' + run_id + '/index.html')
        self.assertEqual(entry['description'], 'Generated world fixture.')
        self.assertEqual(len(entry['images']), 1)
        image = self.root / entry['images'][0]['path']
        self.assertEqual(atlas.sha(image.read_bytes()), entry['images'][0]['sha256'])
        self.assertTrue((self.root / 'dist/previews' / run_id / 'index.html').is_file())
        second = artefact_capture.ensure(self.root, run_id, capture_page=lambda *_: self.fail('capture repeated'))
        self.assertEqual(second['status'], 'already-captured')


if __name__ == '__main__':
    unittest.main()
