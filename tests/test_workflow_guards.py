"""Guards for CI and Pages workflows: no paid runs, deterministic installs, gated deploys."""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github/workflows/ci.yml"
PAGES = ROOT / ".github/workflows/pages.yml"
PACKAGE = ROOT / "package.json"


def read(path):
    return Path(path).read_text(encoding="utf-8")


class WorkflowGuardTests(unittest.TestCase):
    def test_workflows_contain_no_accept_run(self):
        for path in (CI, PAGES):
            text = read(path)
            self.assertNotIn(
                "--accept-run",
                text,
                "%s must never start a paid model run; CI stays on --dry-run" % path.name,
            )

    def test_ci_browser_uses_npm_ci_not_npm_install(self):
        text = read(CI)
        self.assertIn("npm ci", text)
        bare_install = [
            line
            for line in text.splitlines()
            if re.search(r"run:\s*npm install\s*$", line.strip())
        ]
        self.assertEqual(bare_install, [], "CI browser must use `npm ci`, not `npm install`")

    def test_ci_checks_formatting(self):
        text = read(CI)
        self.assertIn("format:check", text)

    def test_pages_manual_trigger_remains(self):
        text = read(PAGES)
        self.assertIn("workflow_dispatch", text)

    def test_pages_gates_deploy_on_same_sha(self):
        text = read(PAGES)
        # A single build job runs every gate on the same checkout before the
        # artifact upload, and deploy only runs after that job.
        for gate in ("npm ci", "npm test", "npm run check", "format:check", "test:release"):
            self.assertIn(gate, text, "Pages must gate deployment on %s" % gate)
        build = text.split("deploy:")[0]
        for gate in ("npm ci", "npm test", "npm run check", "format:check", "test:release"):
            self.assertIn(gate, build, "%s must run in the build job (same SHA)" % gate)
        self.assertIn("needs: build", text)
        upload_at = text.find("upload-pages-artifact")
        for gate in ("npm ci", "npm test", "npm run check", "format:check", "test:release"):
            self.assertLess(text.find(gate), upload_at, "%s must run before the artifact upload" % gate)

    def test_pages_publishing_rights_stay_with_the_deploy_job(self):
        text = read(PAGES)
        build, deploy = text.split("\n  deploy:\n", 1)
        head, build = build.split("\njobs:\n", 1)
        for section in (head, build):
            self.assertNotIn("pages: write", section)
            self.assertNotIn("id-token: write", section)
        self.assertIn("pages: write", deploy)
        self.assertIn("id-token: write", deploy)

    def test_package_pins_node_22(self):
        package = json.loads(read(PACKAGE))
        engines = package.get("engines", {})
        self.assertIn("node", engines)
        self.assertIn("22", str(engines["node"]))
        self.assertEqual(read(ROOT / ".nvmrc").strip(), "22")

    def test_smoke_scripts_restore_build_without_hiding_rebuild_failure(self):
        package = json.loads(read(PACKAGE))
        scripts = package.get("scripts", {})
        for name in ("test:ui", "test:release", "test:engines"):
            script = scripts.get(name, "")
            self.assertIn(
                "test_ci_build_restore",
                script,
                "%s must restore the default build via tests/test_ci_build_restore.py" % name,
            )
            self.assertIn("scripts/atlas.py build", script)
        # The old pattern exited with the smoke status even when the rebuild
        # failed, hiding the rebuild failure.
        for name in ("test:ui", "test:release", "test:engines"):
            self.assertNotIn(
                "status=$?",
                scripts.get(name, ""),
                "%s must not use the status=$? pattern that hides rebuild failure" % name,
            )


if __name__ == "__main__":
    unittest.main()
