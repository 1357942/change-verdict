"""End-to-end checks against temporary Git repositories."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SKILL = Path(__file__).resolve().parents[1] / "skills" / "change-verdict"
SCRIPT = SKILL / "scripts" / "baseline_compare.py"
INSPECT_SCRIPT = SKILL / "scripts" / "inspect_evidence.py"


def run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", check=False)


class BaselineCompareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="cv-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        for command in (["git", "init", "-q"],
                        ["git", "config", "user.email", "test@example.invalid"],
                        ["git", "config", "user.name", "Test"]):
            result = run(command, self.repo)
            self.assertEqual(result.returncode, 0, result.stderr)
        (self.repo / "app.py").write_text("def value():\n    return 0\n", encoding="utf-8")
        self.git("add", "app.py")
        self.git("commit", "-qm", "buggy baseline")
        (self.repo / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
        (self.repo / "tests").mkdir()

    def git(self, *args: str) -> None:
        result = run(["git", *args], self.repo)
        self.assertEqual(result.returncode, 0, result.stderr)

    def write_test(self, body: str) -> None:
        (self.repo / "tests" / "test_regression.py").write_text(
            "from app import value\n\n"
            f"def test_value():\n    {body}\n", encoding="utf-8")

    def compare(self, expected: str | None = None, timeout: int = 20,
                command: list[str] | None = None, *,
                case: str | None = "tests/test_regression.py::test_value",
                exception: str | None = None, repeat: int = 1,
                base: str = "HEAD", require_candidate: bool = False,
                explore_config_diff: bool = False) -> tuple[subprocess.CompletedProcess[str], dict]:
        out = self.root / "evidence"
        argv = [sys.executable, str(SCRIPT), "--repo", str(self.repo),
                "--base", base, "--test-file", "tests/test_regression.py",
                "--output", str(out), "--timeout", str(timeout), "--repeat", str(repeat)]
        if expected is not None:
            argv.extend(["--expect-baseline", expected])
        if case is not None:
            argv.extend(["--expect-case", case])
        if exception is not None:
            argv.extend(["--expect-exception", exception])
        if require_candidate:
            argv.append("--require-candidate")
        if explore_config_diff:
            argv.append("--explore-config-diff")
        argv.extend(["--", *(command or [sys.executable, "-m", "pytest", "-q",
                                       "tests/test_regression.py"])])
        result = run(argv, self.root)
        payload = json.loads((out / "result.json").read_text(encoding="utf-8")) if (out / "result.json").exists() else {}
        return result, payload

    def test_matching_baseline_assertion_is_candidate_evidence(self) -> None:
        self.write_test("assert value() == 1")
        result, payload = self.compare("assert 0 == 1", require_candidate=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["classification"], "candidate_regression_evidence",
                         (self.root / "evidence" / "baseline.log").read_text())
        self.assertEqual(payload["baseline"]["state"], "failed")
        self.assertEqual(payload["current"]["state"], "passed")
        self.assertTrue((self.root / "evidence" / "evidence.md").is_file())
        self.assertIn("assert 0 == 1", (self.root / "evidence" / "baseline.log").read_text())
        self.assertEqual(payload["current"]["tests"]["passed"], 1)
        self.assertEqual(payload["baseline"]["tests"]["failed"], 1)
        self.assertFalse(payload["workspace_changed_during_run"])
        self.assertEqual(payload["schema_version"], 4)
        self.assertEqual(payload["classification_reason"],
                         "expected case failed with the requested signature on every baseline run")

    def test_green_on_both_versions_does_not_discriminate(self) -> None:
        self.write_test("assert True")
        result, payload = self.compare("AssertionError")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["classification"], "no_baseline_discrimination")

    def test_ci_gate_returns_nonzero_but_keeps_evidence(self) -> None:
        self.write_test("assert True")
        result, payload = self.compare("AssertionError", require_candidate=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(payload["classification"], "no_baseline_discrimination")
        self.assertTrue((self.root / "evidence" / "evidence.md").is_file())

    def test_baseline_import_error_is_unavailable(self) -> None:
        (self.repo / "new_api.py").write_text("def check():\n    return True\n", encoding="utf-8")
        (self.repo / "tests" / "test_regression.py").write_text(
            "from new_api import check\n\n"
            "def test_check():\n    assert check()\n", encoding="utf-8")
        result, payload = self.compare("No module named")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["classification"], "comparison_unavailable")
        self.assertEqual(payload["baseline"]["state"], "collection_or_setup_error")

    def test_current_failure_is_not_accepted(self) -> None:
        self.write_test("assert value() == 2")
        result, payload = self.compare("AssertionError")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["classification"], "current_failed")

    def test_baseline_failure_without_expected_pattern_is_unclassified(self) -> None:
        self.write_test("assert value() == 1")
        result, payload = self.compare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["classification"], "baseline_failure_unclassified")

    def test_modified_tracked_test_is_applied_to_baseline(self) -> None:
        self.git("restore", "app.py")
        self.write_test("assert value() == 0")
        self.git("add", "tests/test_regression.py")
        self.git("commit", "-qm", "add original test")
        (self.repo / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
        self.write_test("assert value() == 1")
        result, payload = self.compare("assert 0 == 1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["classification"], "candidate_regression_evidence")
        self.assertNotEqual(payload["test_patch_sha256"], "")

    def test_pytest_regression_check(self) -> None:
        (self.repo / "tests" / "test_regression.py").write_text(
            "from app import value\n\ndef test_value():\n    assert value() == 1\n",
            encoding="utf-8")
        result, payload = self.compare(
            "assert 0 == 1", command=[sys.executable, "-m", "pytest", "-q",
                                      "tests/test_regression.py"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["classification"], "candidate_regression_evidence",
                         (self.root / "evidence" / "baseline.log").read_text())

    def test_skipped_current_test_is_incomplete(self) -> None:
        self.write_test("import pytest; pytest.skip('not ready')")
        result, payload = self.compare("not ready")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["current"]["state"], "incomplete")
        self.assertEqual(payload["classification"], "comparison_unavailable")

    def test_expected_pattern_only_in_output_does_not_qualify(self) -> None:
        self.write_test("print('original ' + 'bug'); assert value() == 1")
        result, payload = self.compare("original bug")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["classification"], "baseline_failure_unclassified")

    def test_expected_pattern_only_in_test_source_does_not_qualify(self) -> None:
        self.write_test("assert value() == 1  # source-only-marker")
        result, payload = self.compare("source-only-marker")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["classification"], "baseline_failure_unclassified")

    def test_workspace_mutation_invalidates_comparison(self) -> None:
        self.write_test("from pathlib import Path; Path('mutation.txt').write_text('changed'); assert value() == 1")
        result, payload = self.compare("assert 0 == 1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(payload["workspace_changed_during_run"])
        self.assertEqual(payload["classification"], "comparison_unavailable")

    def test_command_must_target_selected_file(self) -> None:
        self.write_test("assert value() == 1")
        result, payload = self.compare(command=[sys.executable, "-m", "pytest", "-q"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(payload, {})
        self.assertIn("must target", result.stderr)

    def test_same_failure_pattern_in_other_case_is_not_accepted(self) -> None:
        (self.repo / "tests" / "test_regression.py").write_text(
            "from app import value\n\ndef test_value():\n    assert True\n"
            "\ndef test_other():\n    assert value() == 1\n", encoding="utf-8")
        result, payload = self.compare("assert 0 == 1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["classification"], "baseline_failure_unclassified")

    def test_exception_class_can_identify_expected_failure(self) -> None:
        self.write_test("if value() == 0: raise ValueError('old behavior')")
        result, payload = self.compare(exception="ValueError")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["classification"], "candidate_regression_evidence")
        self.assertEqual(payload["baseline"]["failures"][0]["exception"], "ValueError")

    def test_dependency_config_difference_is_unavailable(self) -> None:
        self.write_test("assert value() == 1")
        (self.repo / "pyproject.toml").write_text("[project]\nname='changed'\n", encoding="utf-8")
        result, payload = self.compare("assert 0 == 1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["classification"], "comparison_unavailable")
        self.assertIn("pyproject.toml", payload["environment_differences"])
        self.assertEqual(payload["environment_details"][0]["status"], "added_in_current")
        self.assertIsNone(payload["environment_details"][0]["baseline_sha256"])
        self.assertIsNone(payload["provisional_observation"])

    def test_config_exploration_does_not_pass_ci_gate(self) -> None:
        self.write_test("assert value() == 1")
        (self.repo / "pyproject.toml").write_text("[project]\nname='changed'\n", encoding="utf-8")
        result, payload = self.compare("assert 0 == 1", require_candidate=True,
                                       explore_config_diff=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(payload["classification"], "comparison_unavailable")
        self.assertEqual(payload["provisional_observation"]["classification"],
                         "candidate_regression_evidence")
        self.assertTrue(payload["provisional_observation"]["environment_unverified"])
        self.assertIn("Exploratory observation (not accepted)",
                      (self.root / "evidence" / "evidence.md").read_text(encoding="utf-8"))

    def test_inspector_shows_exact_case_and_failure_message(self) -> None:
        self.write_test("assert value() == 1, 'distinctive old result'")
        result, payload = self.compare("distinctive old result")
        self.assertEqual(result.returncode, 0, result.stderr)
        inspected = run([sys.executable, str(INSPECT_SCRIPT),
                         str(self.root / "evidence" / "result.json")], self.root)
        self.assertEqual(inspected.returncode, 0, inspected.stderr)
        self.assertIn(payload["baseline"]["failures"][0]["case_id"], inspected.stdout)
        self.assertIn("distinctive old result", inspected.stdout)

    def test_log_is_readable_utf8_even_with_local_console_encoding(self) -> None:
        self.write_test("assert value() == 1, '中文失败'")
        result, payload = self.compare("中文失败")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["classification"], "candidate_regression_evidence")
        log = (self.root / "evidence" / "baseline.log").read_text(encoding="utf-8")
        # Pytest may escape non-ASCII assertion text on Windows CI. Both forms
        # are valid UTF-8 log text and preserve the failure without loss.
        self.assertTrue("中文失败" in log or "\\u4e2d\\u6587\\u5931\\u8d25" in log, log)
        self.assertFalse(payload["baseline"]["log_decode_lossy"])
        if payload["baseline"]["log_source_encoding"] != "utf-8":
            self.assertTrue(Path(payload["baseline"]["raw_log"]).is_file())

    def test_nonancestor_base_is_rejected(self) -> None:
        self.write_test("assert value() == 1")
        tree = run(["git", "rev-parse", "HEAD^{tree}"], self.repo).stdout.strip()
        unrelated = run(["git", "commit-tree", tree, "-m", "unrelated"], self.repo)
        self.assertEqual(unrelated.returncode, 0, unrelated.stderr)
        result, payload = self.compare("assert 0 == 1", base=unrelated.stdout.strip())
        self.assertEqual(result.returncode, 2)
        self.assertEqual(payload, {})
        self.assertIn("ancestor", result.stderr)

    def test_repeat_detects_unstable_current_result(self) -> None:
        marker = self.root / "run-marker.txt"
        self.write_test(
            f"from pathlib import Path; p=Path({str(marker)!r}); seen=p.exists(); "
            "p.write_text('x'); assert not seen")
        result, payload = self.compare(repeat=2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["classification"], "flaky_or_unstable")
        self.assertEqual(len(payload["current_runs"]), 2)

    def test_pattern_without_case_stays_unclassified(self) -> None:
        self.write_test("assert value() == 1")
        result, payload = self.compare("assert 0 == 1", case=None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["classification"], "baseline_failure_unclassified")

    def test_invalid_base_is_reported_without_evidence(self) -> None:
        self.write_test("assert value() == 1")
        argv = [sys.executable, str(SCRIPT), "--repo", str(self.repo),
                "--base", "missing-ref", "--test-file", "tests/test_regression.py",
                "--output", str(self.root / "evidence"), "--", sys.executable,
                "-m", "pytest", "-q", "tests/test_regression.py"]
        result = run(argv, self.root)
        self.assertEqual(result.returncode, 2)
        self.assertIn("change-verdict:", result.stderr)
        self.assertFalse((self.root / "evidence" / "result.json").exists())


if __name__ == "__main__":
    unittest.main()
