"""Exercise the generic JUnit adapter against Node's built-in test runner."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "change-verdict" / "scripts" / "baseline_compare.py"
NODE = shutil.which("node")


def run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", check=False)


@unittest.skipUnless(NODE, "Node.js is required for the JUnit integration test")
class NodeJUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="cv-node-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        for command in (["git", "init", "-q"],
                        ["git", "config", "user.email", "test@example.invalid"],
                        ["git", "config", "user.name", "Test"]):
            result = run(command, self.repo)
            self.assertEqual(result.returncode, 0, result.stderr)
        (self.repo / "package.json").write_text(
            '{"name":"change-verdict-node-case","private":true}\n', encoding="utf-8")
        (self.repo / "booking.js").write_text(
            "function reserve(capacity, booked, requested) {\n"
            "  const remaining = capacity - booked;\n"
            "  if (requested >= remaining) throw new Error(`Only ${remaining} seats remain`);\n"
            "  return booked + requested;\n"
            "}\nmodule.exports = { reserve };\n", encoding="utf-8")
        (self.repo / "runner.js").write_text(
            "const { spawnSync } = require('node:child_process');\n"
            "const xml = process.env.CV_JUNIT_XML;\n"
            "if (!xml) throw new Error('missing CV_JUNIT_XML');\n"
            "const result = spawnSync(process.execPath, [\n"
            "  '--test', '--test-reporter=junit', `--test-reporter-destination=${xml}`,\n"
            "  process.argv[2]\n"
            "], { encoding: 'utf8' });\n"
            "process.stdout.write(result.stdout || '');\n"
            "process.stderr.write(result.stderr || '');\n"
            "process.exit(result.status ?? 1);\n", encoding="utf-8")
        self.git("add", "package.json", "booking.js", "runner.js")
        self.git("commit", "-qm", "buggy baseline")
        (self.repo / "booking.js").write_text(
            "function reserve(capacity, booked, requested) {\n"
            "  const remaining = capacity - booked;\n"
            "  if (requested > remaining) throw new Error(`Only ${remaining} seats remain`);\n"
            "  return booked + requested;\n"
            "}\nmodule.exports = { reserve };\n", encoding="utf-8")
        (self.repo / "tests").mkdir()
        (self.repo / "tests" / "booking.test.js").write_text(
            "const test = require('node:test');\n"
            "const assert = require('node:assert/strict');\n"
            "const { reserve } = require('../booking');\n"
            "test('reserves exact remaining capacity', () => {\n"
            "  assert.equal(reserve(5, 3, 2), 5);\n"
            "});\n", encoding="utf-8")

    def git(self, *args: str) -> None:
        result = run(["git", *args], self.repo)
        self.assertEqual(result.returncode, 0, result.stderr)

    def compare(self, *, extra: list[str] | None = None,
                command: list[str] | None = None) -> tuple[subprocess.CompletedProcess[str], dict]:
        out = self.root / "evidence"
        argv = [sys.executable, str(SCRIPT), "--repo", str(self.repo),
                "--base", "HEAD", "--runner", "junit",
                "--test-file", "tests/booking.test.js", "--output", str(out),
                "--expect-case", "test::reserves exact remaining capacity",
                "--expect-baseline", "Only 2 seats remain", "--repeat", "2"]
        argv.extend(extra or [])
        argv.extend(["--", *(command or [NODE, "--test", "--test-reporter=junit",
                                        "--test-reporter-destination={junit_xml}",
                                        "tests/booking.test.js"])])
        result = run(argv, self.root)
        payload = json.loads((out / "result.json").read_text(encoding="utf-8")) if (out / "result.json").exists() else {}
        return result, payload

    def test_node_junit_finds_regression_in_two_runs(self) -> None:
        result, payload = self.compare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["classification"], "candidate_regression_evidence",
                         (self.root / "evidence" / "baseline.log").read_text())
        self.assertEqual(payload["schema_version"], 4)
        self.assertEqual(payload["runner"], "junit")
        self.assertEqual([run["state"] for run in payload["current_runs"]], ["passed", "passed"])
        self.assertEqual([run["state"] for run in payload["baseline_runs"]], ["failed", "failed"])
        self.assertEqual(payload["environment_differences"], [])

    def test_junit_command_needs_report_path(self) -> None:
        result, payload = self.compare(command=[NODE, "--test", "tests/booking.test.js"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(payload, {})
        self.assertIn("{junit_xml}", result.stderr)

    def test_report_env_provides_output_path(self) -> None:
        result, payload = self.compare(
            extra=["--report-env", "CV_JUNIT_XML"],
            command=[NODE, "runner.js", "tests/booking.test.js"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["classification"], "candidate_regression_evidence")
        self.assertEqual(payload["report_env"], "CV_JUNIT_XML")

    def test_duplicate_junit_case_ids_cannot_qualify(self) -> None:
        test_file = self.repo / "tests" / "booking.test.js"
        test_file.write_text(test_file.read_text(encoding="utf-8") +
                             "test('reserves exact remaining capacity', () => {\n"
                             "  assert.equal(reserve(5, 3, 2), 5);\n});\n",
                             encoding="utf-8")
        result, payload = self.compare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["classification"], "comparison_unavailable")
        self.assertEqual(payload["current"]["state"], "ambiguous_case_ids")


if __name__ == "__main__":
    unittest.main()
