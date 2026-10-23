"""Focused tests for scripts/record_consoles.py that never start a world.

Only the pure helpers and the ``--list`` flag (documented as "starts
nothing") are exercised here. ``record()`` starts the isolated local runner
and is never called. Path scrubbing is tested through ``scrub_output``, the
function ``record()`` applies to every answer.
"""

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

spec = importlib.util.spec_from_file_location("record_consoles", ROOT / "scripts/record_consoles.py")
record_consoles = importlib.util.module_from_spec(spec)
spec.loader.exec_module(record_consoles)

bundles_spec = importlib.util.spec_from_file_location("bundles", ROOT / "scripts/bundles.py")
bundles = importlib.util.module_from_spec(bundles_spec)
bundles_spec.loader.exec_module(bundles)


class CommandsForTests(unittest.TestCase):
    def test_help_runs_first_and_duplicates_drop_out(self):
        entry = {"initial_command": "open", "shortcuts": ["look", "open", "look"]}
        self.assertEqual(record_consoles.commands_for(entry), ["help", "open", "look"])

    def test_blank_commands_are_skipped(self):
        entry = {"initial_command": "", "shortcuts": ["", "look", ""]}
        self.assertEqual(record_consoles.commands_for(entry), ["help", "look"])

    def test_copy_placeholder_value(self):
        self.assertEqual(record_consoles.COPY, "[the disposable copy]")


class ShownConsolesTests(unittest.TestCase):
    def test_shown_consoles_are_console_worlds_with_commands(self):
        run_ids = record_consoles.shown_consoles(ROOT)
        self.assertIsInstance(run_ids, list)
        self.assertGreater(len(run_ids), 0)
        presentation = bundles.load_json(ROOT / "data/presentation.json")
        artefacts = bundles.load_json(ROOT / "data/artefacts.json")
        for run_id in run_ids:
            self.assertIn(run_id, presentation["shown_runs"])
            self.assertEqual(artefacts[run_id]["kind"], "console")

    def test_list_flag_starts_nothing_and_matches_shown_consoles(self):
        recordings = ROOT / "data/recordings"
        before = sorted(p.name for p in recordings.glob("*.json")) if recordings.exists() else []
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts/record_consoles.py"), "--list"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        listed = [line for line in proc.stdout.splitlines() if line.strip()]
        self.assertEqual(listed, record_consoles.shown_consoles(ROOT))
        after = sorted(p.name for p in recordings.glob("*.json")) if recordings.exists() else []
        self.assertEqual(before, after, "--list must not write recordings")

    def test_every_shown_console_has_a_freshly_pinned_recording(self):
        runs = bundles.load_json(ROOT / "data/runs.json")["runs"]
        by_id = {run["id"]: run for run in runs}
        for run_id in record_consoles.shown_consoles(ROOT):
            path = ROOT / "data/recordings" / (run_id + ".json")
            self.assertTrue(path.is_file(), "Console world has no static-site recording: " + run_id)
            recording = bundles.load_json(path)
            self.assertEqual(recording["run_id"], run_id)
            self.assertEqual(recording["corpus_sha256"], by_id[run_id]["corpus_sha256"])


class PathScrubbingTests(unittest.TestCase):
    def test_copy_placeholder_is_not_a_host_path(self):
        self.assertIsNone(bundles.HOST_PATH.search(record_consoles.COPY))

    def test_runner_copy_path_is_scrubbed(self):
        world = "/tmp/after-the-poem-disposable-xyz/world"
        output = "you are in %s\nsee %s/notes.txt" % (world, world)
        scrubbed = record_consoles.scrub_output(output, world, "where", "fixture")
        self.assertNotIn(world, scrubbed)
        self.assertIn(record_consoles.COPY, scrubbed)
        self.assertIsNone(
            bundles.HOST_PATH.search(scrubbed),
            "scrubbed output must not leak the runner copy path",
        )

    def test_any_other_host_path_stops_the_recording(self):
        world = "/tmp/after-the-poem-disposable-xyz/world"
        with self.assertRaisesRegex(ValueError, "host path appears in the answer to notes in fixture"):
            record_consoles.scrub_output("wrote /Users/someone/notes.txt", world, "notes", "fixture")


if __name__ == "__main__":
    unittest.main()
