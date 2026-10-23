#!/usr/bin/env python3
"""Run a smoke test, then always restore the default build.

``npm run test:ui`` and ``npm run test:release`` rebuild ``dist/`` into a
special view before running a Playwright smoke test, then must put the
default build back -- even when the smoke test fails -- without letting a
failed rebuild hide behind (or hide) the smoke result.

This helper is the single place that ordering lives so the npm scripts stay
short and every behaviour is unit-tested without browsers or the network:

* the restore command always runs, even if the test command fails or raises;
* a failed restore is never hidden: a failing restore exits non-zero even
  when the smoke test passed, and wins when both fail.

Usage (called from package.json, default restore rebuilds ``dist/``)::

    python3 tests/test_ci_build_restore.py -- node tests/ui-smoke.mjs
    python3 tests/test_ci_build_restore.py --restore python3 scripts/atlas.py build -- node tests/ui-smoke.mjs
"""

import subprocess
import sys
import unittest


DEFAULT_RESTORE = ["python3", "scripts/atlas.py", "build"]


def split_args(argv):
    """Split ``argv`` into ``(restore_cmd, test_cmd)`` around ``--``.

    Everything after the first ``--`` is the test command. An optional
    ``--restore`` flag before ``--`` overrides the restore command.
    """
    args = list(argv)
    if "--" in args:
        cut = args.index("--")
        head, test_cmd = args[:cut], args[cut + 1:]
    else:
        head, test_cmd = args, []
    restore_cmd = list(DEFAULT_RESTORE)
    if "--restore" in head:
        flag = head.index("--restore")
        restore_cmd = head[flag + 1:] or list(DEFAULT_RESTORE)
    return restore_cmd, test_cmd


def run_with_restore(test_cmd, restore_cmd, runner=None):
    """Run ``test_cmd``, always run ``restore_cmd`` afterwards, return exit code.

    ``runner`` is an injectable ``subprocess.run``-compatible callable used by
    unit tests; when omitted the real ``subprocess.run`` is used. A failing
    restore is never hidden: its exit code wins over the test result.
    """
    run = runner or (lambda cmd: subprocess.run(cmd))
    test_status = 1
    test_error = None
    try:
        test_status = run(list(test_cmd)).returncode
    except Exception as error:  # noqa: BLE001 -- any launch failure must still restore
        test_error = error
        test_status = 1
    restore_status = 1
    restore_error = None
    try:
        restore_status = run(list(restore_cmd)).returncode
    except Exception as error:  # noqa: BLE001 -- report, never swallow
        restore_error = error
        restore_status = 1
    if test_error is not None:
        print("test command failed to start: %r" % (test_error,), file=sys.stderr)
    if restore_error is not None:
        print("restore command failed to start: %r" % (restore_error,), file=sys.stderr)
    if restore_status != 0:
        print(
            "restore failed (exit %d); test exit was %d" % (restore_status, test_status),
            file=sys.stderr,
        )
        return restore_status
    return test_status


def main(argv=None):
    restore_cmd, test_cmd = split_args(sys.argv[1:] if argv is None else argv)
    if not test_cmd:
        print("usage: test_ci_build_restore.py [--restore CMD...] -- TEST...", file=sys.stderr)
        return 2
    return run_with_restore(test_cmd, restore_cmd)


class SplitArgsTests(unittest.TestCase):
    def test_default_restore(self):
        self.assertEqual(
            split_args(["--", "node", "tests/ui-smoke.mjs"]),
            (["python3", "scripts/atlas.py", "build"], ["node", "tests/ui-smoke.mjs"]),
        )

    def test_custom_restore(self):
        self.assertEqual(
            split_args(["--restore", "python3", "scripts/atlas.py", "build", "--", "node", "t.mjs"]),
            (["python3", "scripts/atlas.py", "build"], ["node", "t.mjs"]),
        )


class RunWithRestoreTests(unittest.TestCase):
    class Result:
        def __init__(self, code):
            self.returncode = code

    def test_restore_runs_after_pass(self):
        calls = []

        def fake(cmd):
            calls.append(list(cmd))
            return self.Result(0)

        self.assertEqual(run_with_restore(["t"], ["r"], runner=fake), 0)
        self.assertEqual(calls, [["t"], ["r"]])

    def test_restore_runs_after_failure_and_reports_test_code(self):
        calls = []

        def fake(cmd):
            calls.append(list(cmd))
            return self.Result(3 if cmd == ["t"] else 0)

        self.assertEqual(run_with_restore(["t"], ["r"], runner=fake), 3)
        self.assertEqual(calls, [["t"], ["r"]])

    def test_failing_restore_is_never_hidden_when_test_passes(self):
        def fake(cmd):
            return self.Result(0 if cmd == ["t"] else 7)

        self.assertEqual(run_with_restore(["t"], ["r"], runner=fake), 7)

    def test_failing_restore_wins_when_both_fail(self):
        def fake(cmd):
            return self.Result(3 if cmd == ["t"] else 9)

        self.assertEqual(run_with_restore(["t"], ["r"], runner=fake), 9)

    def test_restore_runs_when_test_raises(self):
        calls = []

        def fake(cmd):
            if cmd == ["t"]:
                raise OSError("cannot start test")
            calls.append(list(cmd))
            return self.Result(0)

        self.assertEqual(run_with_restore(["t"], ["r"], runner=fake), 1)
        self.assertEqual(calls, [["r"]])


if __name__ == "__main__":
    sys.exit(main())
